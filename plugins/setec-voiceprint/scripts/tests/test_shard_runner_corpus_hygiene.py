#!/usr/bin/env python3
"""End-to-end tests for the corpus_hygiene task surface running
through shard_runner.

Builds a synthetic mini-corpus of .md fixtures, writes a manifest
that references them, runs ``shard_runner shard --task
corpus_hygiene`` to split into shards, runs ``work`` to score, and
finally runs ``aggregate`` to produce a cross-shard hygiene
summary. The synthetic fixtures pull from the same css-contamination
test data the single-process ``test_check_corpus.py`` exercises, so
the sharded artifact can be parity-tested against the single-process
output.

The tests pin:

  * ``shard --task corpus_hygiene`` records the task name + the
    threshold/strip params in state.json.
  * ``work`` (default --task) dispatches to the corpus_hygiene
    scorer surface because state.json is authoritative.
  * Per-shard cache files contain ``records`` and ``meta`` with the
    hygiene-summary shape.
  * ``aggregate`` produces a survey JSON whose ``status``,
    ``n_clean``, ``n_warning``, ``n_fail`` match the single-process
    ``check_corpus_paths`` output on the same input.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "calibration") not in sys.path:
    sys.path.insert(0, str(ROOT / "calibration"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import shard_runner as sr  # type: ignore
import shard_state as ss  # type: ignore
import check_corpus as cc  # type: ignore


FIXTURE_DIR = ROOT / "test_data" / "preprocessing"
CONTAMINATED = FIXTURE_DIR / "css_contaminated_fixture.md"
CLEAN = FIXTURE_DIR / "css_contaminated_fixture_clean.md"


# --------------- Fixtures ----------------------------------------


def _write_hygiene_manifest(
    path: Path, *, n_clean: int = 4, n_contaminated: int = 2,
) -> None:
    """Build a manifest of repeated CLEAN/CONTAMINATED rows. The
    rows reference the real CSS contamination fixtures so the
    scorer produces real (non-stubbed) hygiene records — this is
    the integration-test path, not a unit test.

    Repeats lets us hit a sharded run with more rows than shards so
    the test exercises actual cross-shard aggregation.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for i in range(n_clean):
            fh.write(json.dumps({
                "text_id": f"clean_{i:03d}",
                "path": str(CLEAN),
                "ai_status": "pre_ai_human",
                "use": ["validation"],
                "privacy": "shareable",
                "register": "literary_fiction",
            }) + "\n")
        for i in range(n_contaminated):
            fh.write(json.dumps({
                "text_id": f"contaminated_{i:03d}",
                "path": str(CONTAMINATED),
                "ai_status": "pre_ai_human",
                "use": ["validation"],
                "privacy": "shareable",
                "register": "blog_essay",
            }) + "\n")


@pytest.fixture
def hygiene_run(tmp_path: Path):
    """Set up a sharded run that uses the corpus_hygiene task
    surface. Returns the base directory, run id, and source
    manifest path."""
    base = tmp_path / "baselines"
    base.mkdir()
    src = base / "hygiene_src" / "manifest.jsonl"
    _write_hygiene_manifest(src, n_clean=4, n_contaminated=2)
    run_id = "hygiene_test_run"
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
    assert rc == 0
    return {"base": base, "src": src, "run_id": run_id}


# --------------- shard subcommand --------------------------------


class TestShardCorpusHygiene:
    """Pin that ``shard --task corpus_hygiene`` writes the task
    name + threshold/strip params into state.json so downstream
    workers / aggregator read them back without re-parsing CLI
    flags."""

    def test_state_records_corpus_hygiene_task(self, hygiene_run):
        base = hygiene_run["base"]
        run_id = hygiene_run["run_id"]
        state = ss.read_state(sr.state_path(base, run_id))
        assert state["task"] == "corpus_hygiene"

    def test_state_task_params_default_thresholds(self, hygiene_run):
        """When the operator passes no hygiene-specific flags, the
        registered task's default_task_params land in state.json."""
        base = hygiene_run["base"]
        run_id = hygiene_run["run_id"]
        state = ss.read_state(sr.state_path(base, run_id))
        params = state["task_params"]
        assert params["warn_threshold"] == 0.01
        assert params["fail_threshold"] == 0.05
        assert params["strip_aggressive"] is False
        assert params["strip_rules"] is None

    def test_explicit_thresholds_land_in_state(self, tmp_path: Path):
        """Operator-supplied --warn-threshold / --fail-threshold
        override the registered defaults."""
        base = tmp_path / "baselines"
        base.mkdir()
        src = base / "manifest.jsonl"
        _write_hygiene_manifest(src, n_clean=2, n_contaminated=1)
        rc = sr.main([
            "--base-dir", str(base),
            "shard",
            "--task", "corpus_hygiene",
            "--source-manifest", str(src),
            "--run-id", "explicit_thresholds",
            "--shard-size", "10",
            "--stratify", "ai_status",
            "--warn-threshold", "0.02",
            "--fail-threshold", "0.10",
            "--strip-aggressive",
        ])
        assert rc == 0
        state = ss.read_state(
            sr.state_path(base, "explicit_thresholds")
        )
        assert state["task_params"]["warn_threshold"] == 0.02
        assert state["task_params"]["fail_threshold"] == 0.10
        assert state["task_params"]["strip_aggressive"] is True

    def test_unknown_task_fails_with_helpful_message(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
    ):
        """``shard --task does_not_exist`` exits 2 with a clear
        list of registered tasks so the operator can correct the
        typo. The argparse choices path is intentionally not used
        (so tests can register/unregister ad-hoc surfaces), and
        validation lands at runtime instead."""
        base = tmp_path / "baselines"
        base.mkdir()
        src = base / "manifest.jsonl"
        _write_hygiene_manifest(src, n_clean=1, n_contaminated=1)
        rc = sr.main([
            "--base-dir", str(base),
            "shard",
            "--task", "does_not_exist",
            "--source-manifest", str(src),
            "--run-id", "doomed",
            "--shard-size", "10",
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "does_not_exist" in err
        assert "Registered" in err
        assert "corpus_hygiene" in err


# --------------- work subcommand ---------------------------------


class TestWorkCorpusHygiene:
    """Pin that ``work`` against a corpus_hygiene run actually
    runs check_corpus.score_manifest_rows for each shard and
    produces hygiene records (not calibration records)."""

    def test_work_produces_hygiene_records(self, hygiene_run):
        base = hygiene_run["base"]
        run_id = hygiene_run["run_id"]
        rc = sr.main([
            "--base-dir", str(base), "work",
            "--run-id", run_id,
            "--task", "corpus_hygiene",
        ])
        assert rc == 0
        state = ss.read_state(sr.state_path(base, run_id))
        # Every shard should be done.
        for sid in state["shards"]:
            assert state["shards"][sid]["state"] == "done"
        # Each cache should have records that look like hygiene
        # records, not calibration records.
        for sid in state["shards"]:
            cp = sr.shard_cache_path(base, run_id, sid)
            assert cp.exists()
            cache = json.loads(cp.read_text())
            assert "records" in cache
            assert "meta" in cache
            for record in cache["records"]:
                # Hygiene records have these fields; calibration
                # records have "scores" / "label". The shape
                # difference is the load-bearing signal that
                # task dispatch worked.
                assert "status" in record
                assert "path" in record
                assert "strip_ratio" in record
                assert record["status"] in (
                    "clean", "warning", "fail", "error",
                )
            # Meta should carry the threshold contract.
            assert cache["meta"]["scorer_version"].startswith("corpus_hygiene")
            assert "warn_threshold" in cache["meta"]

    def test_contaminated_rows_scored_as_fail(self, hygiene_run):
        """The 2 contaminated fixture rows should each score
        status='fail' (their strip_ratio exceeds the default 5%
        fail threshold). The 4 clean rows should score 'clean'."""
        base = hygiene_run["base"]
        run_id = hygiene_run["run_id"]
        sr.main([
            "--base-dir", str(base), "work", "--run-id", run_id,
            "--task", "corpus_hygiene",
        ])
        state = ss.read_state(sr.state_path(base, run_id))
        n_fail = 0
        n_clean = 0
        for sid in state["shards"]:
            cp = sr.shard_cache_path(base, run_id, sid)
            cache = json.loads(cp.read_text())
            for record in cache["records"]:
                if record["status"] == "fail":
                    n_fail += 1
                elif record["status"] == "clean":
                    n_clean += 1
        assert n_fail == 2
        assert n_clean == 4

    def test_work_omitting_task_dispatches_via_state(self, hygiene_run):
        """The default --task value is calibration_survey, but
        state.json says corpus_hygiene; the mismatch check kicks
        in. Operators who want to omit --task have to pass
        --task corpus_hygiene explicitly. (Alternatively, omit
        --task on `shard` to default the task to
        calibration_survey.)

        This is a backcompat tradeoff: defaulting --task to
        whatever state.json says would silently work for runs the
        operator forgot to label, but would also break the
        ``--task different_task`` mismatch detection. We chose the
        latter to keep operator intent explicit. Pin the behavior
        so a future contributor doesn't quietly invert it.
        """
        base = hygiene_run["base"]
        run_id = hygiene_run["run_id"]
        rc = sr.main([
            "--base-dir", str(base), "work", "--run-id", run_id,
        ])
        assert rc == 2  # task mismatch


# --------------- aggregate subcommand ----------------------------


class TestAggregateCorpusHygiene:
    """Pin that ``aggregate --task corpus_hygiene`` produces a
    cross-shard summary whose counts match the single-process
    check_corpus_paths output on the same input. This is the
    parity contract that lets the sharded path replace the
    single-process path on RAID-scale corpora."""

    def test_aggregate_produces_hygiene_summary(
        self, hygiene_run, tmp_path: Path,
    ):
        base = hygiene_run["base"]
        run_id = hygiene_run["run_id"]
        sr.main([
            "--base-dir", str(base), "work", "--run-id", run_id,
            "--task", "corpus_hygiene",
        ])
        out_path = tmp_path / "hygiene-aggregate.json"
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
        assert payload["n_files"] == 6  # 4 clean + 2 contaminated
        assert payload["n_clean"] == 4
        assert payload["n_fail"] == 2
        # An aggregate with any fail rows has status=fail.
        assert payload["status"] == "fail"
        # The dominant rule on the contaminated fixture is
        # css_rule_block per test_check_corpus.py.
        assert payload["dominant_rule"] == "css_rule_block"

    def test_aggregate_parity_with_single_process(
        self, hygiene_run, tmp_path: Path,
    ):
        """Cross-check: aggregate's n_clean / n_fail / dominant_rule
        should match what ``check_corpus.check_corpus_paths``
        produces when called on the same set of input files
        sequentially. This is the load-bearing parity test that
        lets an operator confidently swap the single-process
        path for the sharded path on RAID."""
        base = hygiene_run["base"]
        run_id = hygiene_run["run_id"]
        sr.main([
            "--base-dir", str(base), "work", "--run-id", run_id,
            "--task", "corpus_hygiene",
        ])
        out_path = tmp_path / "hygiene-aggregate.json"
        sr.main([
            "--base-dir", str(base), "aggregate",
            "--task", "corpus_hygiene",
            "--run-id", run_id,
            "--out", str(out_path),
            "--no-derive",
        ])
        payload = json.loads(out_path.read_text())
        # Now compute the single-process equivalent.
        single = cc.check_corpus_paths(
            [CLEAN] * 4 + [CONTAMINATED] * 2,
        )
        assert payload["n_clean"] == single["n_clean"]
        assert payload["n_warning"] == single["n_warning"]
        assert payload["n_fail"] == single["n_fail"]
        assert payload["status"] == single["status"]
        assert payload["dominant_rule"] == single["dominant_rule"]


# --------------- score_manifest_rows --------------------------


class TestScoreManifestRows:
    """Direct tests for the new ``check_corpus.score_manifest_rows``
    helper, the surface the corpus_hygiene scorer adapter calls."""

    def test_score_manifest_rows_resolves_paths(self, tmp_path: Path):
        """Manifest rows with relative paths should resolve against
        the manifest's parent directory (mirroring
        ``paths_from_manifest``'s contract)."""
        # Write fixture copies into tmp so we can use relative paths.
        clean = tmp_path / "clean.md"
        clean.write_text(CLEAN.read_text(), encoding="utf-8")
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            json.dumps({"text_id": "c", "path": "clean.md"}) + "\n",
            encoding="utf-8",
        )
        records, summary = cc.score_manifest_rows(manifest)
        assert len(records) == 1
        assert records[0]["status"] == "clean"
        assert summary["n_clean"] == 1

    def test_score_manifest_rows_carries_text_id(self, tmp_path: Path):
        """Records returned by score_manifest_rows must carry
        through the manifest's text_id so the aggregator can join
        records back to source-manifest rows."""
        clean = tmp_path / "clean.md"
        clean.write_text(CLEAN.read_text(), encoding="utf-8")
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            json.dumps({
                "text_id": "carryover_id_42",
                "path": "clean.md",
            }) + "\n",
            encoding="utf-8",
        )
        records, _summary = cc.score_manifest_rows(manifest)
        assert records[0]["text_id"] == "carryover_id_42"

    def test_score_manifest_rows_skips_missing_path_rows(
        self, tmp_path: Path,
    ):
        """A manifest row without a usable ``path`` is silently
        skipped (the sharded path is more permissive than the
        single-process path, which errors loudly — at RAID scale
        an operator probably doesn't want one malformed row to
        nuke a whole shard)."""
        clean = tmp_path / "clean.md"
        clean.write_text(CLEAN.read_text(), encoding="utf-8")
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            json.dumps({"text_id": "good", "path": "clean.md"}) + "\n"
            + json.dumps({"text_id": "bad_no_path"}) + "\n",
            encoding="utf-8",
        )
        records, _ = cc.score_manifest_rows(manifest)
        # Only the good row was scored.
        assert len(records) == 1
        assert records[0]["text_id"] == "good"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
