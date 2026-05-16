#!/usr/bin/env python3
"""End-to-end smoke test for the sharded calibration pipeline.

The existing ``test_shard_runner.py`` tests exercise each subcommand
in isolation. This file complements that with the **canonical
operator pipeline** as a single fixture:

    shard → work → verify → aggregate → status

Every operator who runs sharded calibration walks this sequence;
this test pins that the sequence works end-to-end, that the
inter-subcommand contract (state.json + per-shard caches +
aggregate survey) is consistent, and that the failure modes
operators actually hit (corrupted cache, missing cache, mid-run
status check) get caught early.

What this test does NOT cover:

  * Real corpus data — uses a synthetic manifest with hashed text.
  * The actual scorer math — uses a stub that produces
    deterministic synthetic records (test_shard_runner.py's
    ``_stub_scorer`` pattern).
  * Concurrent workers — covered by
    ``test_work_subcommand_two_workers_complete_all_shards`` in
    ``test_shard_runner.py``.
  * Multi-machine sync — covered by the v1.44.2 tests once that
    PR merges.

If a regression breaks the operator pipeline (e.g., a refactor
silently drops a state.json field, or aggregate's cache-integrity
check loosens), this test should fail loudly. Adopt this file as
the regression guard whenever modifying any subcommand's
interface.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))

import shard_runner as sr  # type: ignore
import shard_state as ss  # type: ignore


# ---------- Synthetic corpus + stub scorer ----------


def _write_synthetic_manifest(path: Path, n_rows: int = 30) -> None:
    """Build a minimal labeled manifest. Not validator-clean
    (missing source, length, privacy) but sufficient for the
    sharded toolchain which only cares about ai_status, register,
    text_id, and text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    registers = ["literary_fiction", "blog_essay", "personal"]
    statuses = ["pre_ai_human", "ai_generated"]
    with path.open("w", encoding="utf-8") as fh:
        for i in range(n_rows):
            row = {
                "text_id": f"smoke_{i:03d}",
                "text": f"Synthetic smoke-test prose number {i}. " * 5,
                "register": registers[i % len(registers)],
                "ai_status": statuses[i % len(statuses)],
                "use": "validation",
                "privacy": "shareable",
            }
            fh.write(json.dumps(row) + "\n")


def _stub_scorer(
    shard_manifest_path: Path,
    *,
    fpr_target: float,
    tier1: bool,
    tier2: bool,
    tier3: bool,
    use: str,
    cache_path: Path,
    flush_every: int,
    sigterm_event: Any,
    # 1.80.0+: pipeline-wiring kwargs. Stub ignores them.
    **_extra: Any,
) -> dict[str, Any]:
    """Same stub pattern as test_shard_runner.py: read the shard's
    manifest, produce one fake record per row, write the cache.
    Returns the records + meta dict the orchestrator expects."""
    records = []
    with Path(shard_manifest_path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            # Deterministic per-row score so the same manifest
            # always produces the same cache SHA-256.
            score = float(hash(row["text_id"]) % 1000) / 1000.0
            records.append({
                "text_id": row["text_id"],
                "label": row.get("ai_status"),
                "scores": {"synthetic_signal": score},
            })
    meta = {
        "scorer_version": "smoke-pipeline-stub-1.0",
        "tier1": tier1, "tier2": tier2, "tier3": tier3,
        "fpr_target": fpr_target, "use": use,
    }
    cp = Path(cache_path)
    cp.parent.mkdir(parents=True, exist_ok=True)
    with cp.open("w", encoding="utf-8") as fh:
        json.dump({"records": records, "meta": meta}, fh, sort_keys=True)
    return {"records": records, "meta": meta, "cache_hit": False}


# ---------- Fixture: shard + work in one go ----------


@pytest.fixture
def smoke_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run the canonical operator pipeline through ``work``,
    returning the paths + run state for downstream tests to inspect.

    Steps:

      1. Write a 30-row synthetic source manifest.
      2. Run ``shard_runner shard`` to split into 3 shards.
      3. Run ``shard_runner work`` to score them (stub scorer).
    """
    monkeypatch.setattr(sr, "DEFAULT_SCORER", _stub_scorer)
    base = tmp_path / "baselines"
    base.mkdir()
    src = base / "synthetic" / "manifest.jsonl"
    _write_synthetic_manifest(src, n_rows=30)
    run_id = "smoke_canonical_run"
    # shard
    rc = sr.main([
        "--base-dir", str(base),
        "shard",
        "--source-manifest", str(src),
        "--run-id", run_id,
        "--shard-size", "10",
        "--shuffle-seed", "42",
        "--fpr-target", "0.01",
        "--no-tier2", "--no-tier3",
    ])
    assert rc == 0, "shard subcommand failed"
    # work
    rc = sr.main([
        "--base-dir", str(base), "work", "--run-id", run_id,
    ])
    assert rc == 0, "work subcommand failed"
    return {
        "base": base, "src": src, "run_id": run_id, "n_rows": 30,
    }


# ---------- Canonical pipeline ----------


class TestCanonicalPipeline:
    """The canonical sequence every operator runs:
    shard → work → verify → aggregate → status."""

    def test_shard_writes_state_json_and_manifests(self, smoke_pipeline):
        """After ``shard``, state.json exists with one entry per
        shard, and each shard's manifest.jsonl is on disk."""
        base = smoke_pipeline["base"]
        run_id = smoke_pipeline["run_id"]
        sp = sr.state_path(base, run_id)
        assert sp.exists()
        state = ss.read_state(sp)
        assert state["run_id"] == run_id
        # 30 rows / 10 per shard = 3 shards.
        assert state["shard_count"] == 3
        for sid in ("000", "001", "002"):
            mp = sr.shard_manifest_path(base, run_id, sid)
            assert mp.exists()
            rows = [
                json.loads(line) for line in mp.read_text().splitlines()
                if line
            ]
            assert rows  # non-empty

    def test_work_marks_every_shard_done(self, smoke_pipeline):
        """After ``work``, every shard's state is ``done`` and
        carries a cache_path + cache_sha256."""
        base = smoke_pipeline["base"]
        run_id = smoke_pipeline["run_id"]
        state = ss.read_state(sr.state_path(base, run_id))
        for sid in ("000", "001", "002"):
            shard = state["shards"][sid]
            assert shard["state"] == "done"
            assert shard["cache_path"]
            assert shard["cache_sha256"]
            assert shard["n_entries"] > 0

    def test_verify_passes_on_clean_caches(self, smoke_pipeline):
        """``verify`` should succeed (rc=0) on the canonical
        post-work state."""
        base = smoke_pipeline["base"]
        run_id = smoke_pipeline["run_id"]
        rc = sr.main([
            "--base-dir", str(base), "verify", "--run-id", run_id,
        ])
        assert rc == 0

    def test_aggregate_concatenates_all_records(
        self, smoke_pipeline, tmp_path: Path,
    ):
        """``aggregate`` should produce a survey JSON whose
        n_records equals the source manifest's row count.

        This catches the regression class "aggregate silently drops
        a shard." The source has 30 rows split across 3 shards;
        the aggregate must report all 30."""
        base = smoke_pipeline["base"]
        run_id = smoke_pipeline["run_id"]
        out_path = tmp_path / "smoke-aggregate.json"
        rc = sr.main([
            "--base-dir", str(base), "aggregate",
            "--run-id", run_id,
            "--out", str(out_path),
            "--no-derive",
        ])
        assert rc == 0
        payload = json.loads(out_path.read_text())
        assert payload["n_records"] == smoke_pipeline["n_rows"]
        assert payload["n_shards_contributed"] == 3
        assert payload["run_id"] == run_id
        # Contributing shard ids are sorted + complete.
        assert payload["contributing_shards"] == ["000", "001", "002"]

    def test_status_reports_100_percent_done(
        self, smoke_pipeline, capsys: pytest.CaptureFixture,
    ):
        """``status --json`` should report 3-of-3 done after the
        canonical pipeline completes."""
        base = smoke_pipeline["base"]
        run_id = smoke_pipeline["run_id"]
        rc = sr.main([
            "--base-dir", str(base), "status",
            "--run-id", run_id, "--json",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        summary = json.loads(out)
        assert summary["counts"]["done"] == 3
        assert summary["counts"]["pending"] == 0
        assert summary["fraction_done"] == pytest.approx(1.0)


# ---------- Failure-mode regression guards ----------


class TestPipelineFailureModes:
    """The integration-level failure modes operators actually hit.
    Each test exercises one cross-subcommand contract."""

    def test_verify_then_aggregate_catches_corruption(
        self, smoke_pipeline, tmp_path: Path,
        capsys: pytest.CaptureFixture,
    ):
        """If a cache file is tampered AFTER work but BEFORE
        aggregate, ``verify`` catches it via SHA-256 mismatch
        AND ``aggregate`` refuses without --allow-partial.

        This is the load-bearing integrity contract from PR #17:
        aggregate must not produce a survey from a tampered cache."""
        base = smoke_pipeline["base"]
        run_id = smoke_pipeline["run_id"]
        # Tamper one cache.
        cp = sr.shard_cache_path(base, run_id, "001")
        cache = json.loads(cp.read_text())
        cache["records"].append(
            {"text_id": "injected", "label": "ai_generated", "scores": {}}
        )
        cp.write_text(json.dumps(cache))
        # verify fails.
        rc = sr.main([
            "--base-dir", str(base), "verify", "--run-id", run_id,
        ])
        assert rc == 4  # hash mismatch
        # aggregate fails too, without --allow-partial.
        rc = sr.main([
            "--base-dir", str(base), "aggregate",
            "--run-id", run_id,
            "--out", str(tmp_path / "corrupted.json"),
            "--no-derive",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "integrity" in err.lower()

    def test_status_before_work_shows_all_pending(
        self, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ):
        """An operator who runs ``status`` after ``shard`` but
        before ``work`` should see all shards as pending. This
        catches a regression where status defaults to incorrect
        bucket reporting."""
        monkeypatch.setattr(sr, "DEFAULT_SCORER", _stub_scorer)
        base = tmp_path / "baselines"
        base.mkdir()
        src = base / "synthetic" / "manifest.jsonl"
        _write_synthetic_manifest(src, n_rows=20)
        run_id = "smoke_pre_work_status"
        rc = sr.main([
            "--base-dir", str(base), "shard",
            "--source-manifest", str(src),
            "--run-id", run_id,
            "--shard-size", "10",
            "--no-tier2", "--no-tier3",
        ])
        assert rc == 0
        rc = sr.main([
            "--base-dir", str(base), "status",
            "--run-id", run_id, "--json",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        summary = json.loads(out)
        assert summary["counts"]["pending"] == 2
        assert summary["counts"]["done"] == 0
        assert summary["fraction_done"] == 0.0

    def test_aggregate_without_work_fails_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """``aggregate`` before ``work`` should refuse (rc=2)
        rather than producing an empty survey."""
        monkeypatch.setattr(sr, "DEFAULT_SCORER", _stub_scorer)
        base = tmp_path / "baselines"
        base.mkdir()
        src = base / "synthetic" / "manifest.jsonl"
        _write_synthetic_manifest(src, n_rows=10)
        run_id = "smoke_aggregate_without_work"
        rc = sr.main([
            "--base-dir", str(base), "shard",
            "--source-manifest", str(src),
            "--run-id", run_id,
            "--shard-size", "5",
            "--no-tier2", "--no-tier3",
        ])
        assert rc == 0
        # Aggregate without running work — all shards pending.
        rc = sr.main([
            "--base-dir", str(base), "aggregate",
            "--run-id", run_id,
            "--out", str(tmp_path / "empty.json"),
            "--no-derive",
        ])
        assert rc == 2  # refuses

    def test_idempotent_shard_refuses_overwrite(
        self, smoke_pipeline, capsys: pytest.CaptureFixture,
    ):
        """``shard`` should refuse to overwrite an existing
        state.json without --force. Otherwise an operator
        accidentally re-running ``shard`` would clobber an
        in-progress run's state."""
        base = smoke_pipeline["base"]
        src = smoke_pipeline["src"]
        run_id = smoke_pipeline["run_id"]
        rc = sr.main([
            "--base-dir", str(base), "shard",
            "--source-manifest", str(src),
            "--run-id", run_id,
            "--shard-size", "10",
            "--no-tier2", "--no-tier3",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "--force" in err


# ---------- Cross-subcommand contract: cache + state ----------


class TestStateCacheContract:
    """state.json's per-shard ``cache_sha256`` must match the
    on-disk cache's actual SHA-256. The aggregator depends on this
    invariant for its integrity check; the verify subcommand
    depends on it for hash validation. Pin it explicitly."""

    def test_state_sha256_matches_disk(self, smoke_pipeline):
        base = smoke_pipeline["base"]
        run_id = smoke_pipeline["run_id"]
        state = ss.read_state(sr.state_path(base, run_id))
        for sid in ("000", "001", "002"):
            shard = state["shards"][sid]
            cp = base / shard["cache_path"]
            assert cp.exists(), f"cache file missing: {cp}"
            disk_sha = ss.sha256_file(cp)
            assert shard["cache_sha256"] == disk_sha, (
                f"shard {sid}: state.json sha != disk sha "
                f"({shard['cache_sha256']} vs {disk_sha})"
            )

    def test_aggregate_record_count_matches_sum_of_n_entries(
        self, smoke_pipeline, tmp_path: Path,
    ):
        """Sanity: aggregate's n_records should equal the sum of
        per-shard n_entries in state.json. Catches regressions
        where aggregate silently drops records mid-merge."""
        base = smoke_pipeline["base"]
        run_id = smoke_pipeline["run_id"]
        state = ss.read_state(sr.state_path(base, run_id))
        expected_total = sum(
            state["shards"][sid]["n_entries"]
            for sid in ("000", "001", "002")
        )
        out_path = tmp_path / "agg.json"
        rc = sr.main([
            "--base-dir", str(base), "aggregate",
            "--run-id", run_id,
            "--out", str(out_path),
            "--no-derive",
        ])
        assert rc == 0
        payload = json.loads(out_path.read_text())
        assert payload["n_records"] == expected_total


# ---------- Multi-task smoke (v1.45.0) ----------


class TestCorpusHygieneSmoke:
    """End-to-end smoke for the corpus_hygiene task surface
    landing in v1.45.0. Mirrors ``TestCanonicalPipeline`` but on
    the hygiene task: shard -> work -> aggregate produces a valid
    cross-shard hygiene summary.

    The single-process check_corpus path stays the operator-facing
    UX for small corpora; this test pins that the sharded path
    produces a parity-comparable artifact at scale, which is the
    contract that lets operators swap paths confidently."""

    @pytest.fixture
    def hygiene_smoke(self, tmp_path: Path):
        ROOT_LOCAL = Path(__file__).resolve().parents[1]
        clean = ROOT_LOCAL / "test_data" / "preprocessing" / (
            "css_contaminated_fixture_clean.md"
        )
        contaminated = ROOT_LOCAL / "test_data" / "preprocessing" / (
            "css_contaminated_fixture.md"
        )
        base = tmp_path / "baselines"
        base.mkdir()
        src = base / "hygiene" / "manifest.jsonl"
        src.parent.mkdir(parents=True, exist_ok=True)
        with src.open("w", encoding="utf-8") as fh:
            for i in range(6):
                fh.write(json.dumps({
                    "text_id": f"clean_{i:03d}",
                    "path": str(clean),
                    "ai_status": "pre_ai_human",
                    "register": "literary_fiction",
                }) + "\n")
            for i in range(3):
                fh.write(json.dumps({
                    "text_id": f"contaminated_{i:03d}",
                    "path": str(contaminated),
                    "ai_status": "pre_ai_human",
                    "register": "blog_essay",
                }) + "\n")
        run_id = "smoke_hygiene_run"
        rc = sr.main([
            "--base-dir", str(base),
            "shard",
            "--task", "corpus_hygiene",
            "--source-manifest", str(src),
            "--run-id", run_id,
            "--shard-size", "3",
            "--stratify", "ai_status",
            "--shuffle-seed", "42",
        ])
        assert rc == 0, "shard subcommand failed"
        rc = sr.main([
            "--base-dir", str(base), "work",
            "--run-id", run_id,
            "--task", "corpus_hygiene",
        ])
        assert rc == 0, "work subcommand failed"
        return {
            "base": base, "src": src, "run_id": run_id,
            "n_files": 9, "n_clean": 6, "n_fail": 3,
        }

    def test_hygiene_shard_writes_corpus_hygiene_state(self, hygiene_smoke):
        """After shard, state.json records task=corpus_hygiene and
        per-shard manifests carry the path field through."""
        base = hygiene_smoke["base"]
        run_id = hygiene_smoke["run_id"]
        state = ss.read_state(sr.state_path(base, run_id))
        assert state["task"] == "corpus_hygiene"
        # Source rows should be sharded out.
        for sid in state["shards"]:
            mp = sr.shard_manifest_path(base, run_id, sid)
            assert mp.exists()
            rows = [
                json.loads(line) for line in mp.read_text().splitlines()
                if line
            ]
            for row in rows:
                # Every row must carry through a path field for
                # the hygiene scorer.
                assert "path" in row

    def test_hygiene_work_dispatches_via_state(self, hygiene_smoke):
        """Even though --task corpus_hygiene was passed explicitly,
        the dispatcher must read state["task"] to decide which
        scorer to invoke. We verify by inspecting the produced
        cache shape — hygiene records have status / strip_ratio,
        calibration records have scores / label."""
        base = hygiene_smoke["base"]
        run_id = hygiene_smoke["run_id"]
        state = ss.read_state(sr.state_path(base, run_id))
        for sid in state["shards"]:
            cp = sr.shard_cache_path(base, run_id, sid)
            cache = json.loads(cp.read_text())
            for record in cache["records"]:
                assert "status" in record
                assert "strip_ratio" in record
                assert "scores" not in record

    def test_hygiene_aggregate_produces_status_fail(
        self, hygiene_smoke, tmp_path: Path,
    ):
        """The corpus has contaminated rows, so aggregate must
        report status=fail. Pin this so a future regression
        doesn't accidentally classify contaminated rows as
        warnings."""
        base = hygiene_smoke["base"]
        run_id = hygiene_smoke["run_id"]
        out_path = tmp_path / "hygiene-smoke-agg.json"
        rc = sr.main([
            "--base-dir", str(base), "aggregate",
            "--task", "corpus_hygiene",
            "--run-id", run_id,
            "--out", str(out_path),
            "--no-derive",
        ])
        assert rc == 0
        payload = json.loads(out_path.read_text())
        assert payload["task"] == "corpus_hygiene"
        assert payload["n_files"] == hygiene_smoke["n_files"]
        assert payload["n_clean"] == hygiene_smoke["n_clean"]
        assert payload["n_fail"] == hygiene_smoke["n_fail"]
        assert payload["status"] == "fail"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
