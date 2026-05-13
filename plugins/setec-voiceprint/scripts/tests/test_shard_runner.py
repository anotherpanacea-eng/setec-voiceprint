#!/usr/bin/env python3
"""Integration tests for shard_runner.py (v1.44.0 core).

Tests cover the end-to-end CLI flow:

  1. ``shard`` splits a synthetic manifest into deterministic
     stratified shards and writes a valid state.json.
  2. ``work`` claims a pending shard, invokes the scorer (stubbed),
     writes a cache file with the recorded SHA, and marks the
     shard done.
  3. ``aggregate`` concatenates shard caches and emits a unified
     survey JSON with per-signal threshold sweep results (or skips
     derive when ``--no-derive`` is passed).
  4. ``verify`` confirms shard cache hashes match state.json
     records, and fails loudly when they don't.
  5. ``status`` reports counts and progress.

The scorer is stubbed via ``DEFAULT_SCORER`` monkeypatching so the
tests run in seconds rather than minutes (the real scoring path
would invoke spaCy + variance_audit + optionally SBERT). The stub
returns deterministic synthetic records that downstream
``derive_threshold_from_records`` can either consume or skip via
``--no-derive``.

These tests do NOT exercise the real ``calibrate_thresholds`` import
chain — that's the responsibility of the smoke-test against MAGE
listed in the spec's §6.3, which runs on the calibration host where
the real corpus and the real scoring path are available.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "calibration") not in sys.path:
    sys.path.insert(0, str(ROOT / "calibration"))

import shard_runner as sr  # type: ignore
import shard_state as ss  # type: ignore


# --------------- Synthetic source manifest ----------------------


def _write_synthetic_manifest(
    path: Path, *, n_rows: int = 60, registers: list[str] | None = None,
) -> None:
    """Write a deterministic synthetic JSONL manifest. Each row
    carries a text_id, a register, an ai_status, and a synthetic
    text field. The framework's validator would balk at this
    minimal shape (no source, no length, no privacy field) — these
    tests do not exercise the validator.
    """
    registers = registers or ["literary_fiction", "blog_essay"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for i in range(n_rows):
            row = {
                "text_id": f"r{i:04d}",
                "text": f"Synthetic prose sample number {i}. " * 5,
                "register": registers[i % len(registers)],
                "ai_status": "pre_ai_human" if i % 2 == 0 else "ai_generated",
                "use": "validation",
                "privacy": "shareable",
            }
            fh.write(json.dumps(row) + "\n")


# --------------- Stub scorer ------------------------------------


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
) -> dict[str, Any]:
    """Stand-in for calibration_survey's real scorer. Reads the
    shard manifest, produces one fake record per row carrying a
    deterministic synthetic score, and writes the cache to
    ``cache_path``. Returns the records + meta dict.
    """
    records = []
    with Path(shard_manifest_path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            # One synthetic score field per row. Deterministic via
            # the text_id hash.
            score = float(hash(row["text_id"]) % 1000) / 1000.0
            records.append({
                "text_id": row["text_id"],
                "label": row.get("ai_status"),
                "scores": {"synthetic_signal": score},
            })
    meta = {
        "scorer_version": "stub-1.0",
        "tier1": tier1,
        "tier2": tier2,
        "tier3": tier3,
        "fpr_target": fpr_target,
        "use": use,
    }
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w", encoding="utf-8") as fh:
        json.dump({"records": records, "meta": meta}, fh, sort_keys=True)
    return {"records": records, "meta": meta, "cache_hit": False}


# --------------- Fixtures --------------------------------------


@pytest.fixture
def sharded_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up a run-directory base with a synthetic source manifest
    and a freshly-sharded state. Returns the args namespace that
    subsequent subcommands can reuse."""
    monkeypatch.setattr(sr, "DEFAULT_SCORER", _stub_scorer)
    base = tmp_path / "baselines"
    base.mkdir()
    src = base / "synthetic" / "manifest.jsonl"
    _write_synthetic_manifest(src, n_rows=60)
    run_id = "test_run_1"
    rc = sr.main([
        "--base-dir", str(base),
        "shard",
        "--source-manifest", str(src),
        "--run-id", run_id,
        "--shard-size", "20",
        "--shuffle-seed", "42",
        "--fpr-target", "0.01",
        "--no-tier2", "--no-tier3",
    ])
    assert rc == 0
    return {"base": base, "src": src, "run_id": run_id}


# --------------- shard ----------------------------------------


def test_shard_subcommand_writes_state_and_manifests(sharded_run):
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    sp = sr.state_path(base, run_id)
    assert sp.exists()
    state = ss.read_state(sp)
    assert state["run_id"] == run_id
    assert state["shard_count"] == 3  # 60 rows / 20 = 3
    for sid in ("000", "001", "002"):
        mp = sr.shard_manifest_path(base, run_id, sid)
        assert mp.exists(), f"shard manifest missing: {mp}"
        rows = [
            json.loads(line) for line in mp.read_text().splitlines() if line
        ]
        assert rows, f"shard {sid} is empty"


def test_shard_subcommand_refuses_to_overwrite_without_force(
    tmp_path: Path, sharded_run, capsys: pytest.CaptureFixture,
):
    """Re-running shard against an existing run_id without --force
    must refuse and exit non-zero. State file remains intact."""
    base = sharded_run["base"]
    src = sharded_run["src"]
    run_id = sharded_run["run_id"]
    rc = sr.main([
        "--base-dir", str(base),
        "shard",
        "--source-manifest", str(src),
        "--run-id", run_id,
        "--shard-size", "20",
        "--no-tier2", "--no-tier3",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--force" in err


def test_shard_subcommand_missing_source_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture,
):
    rc = sr.main([
        "--base-dir", str(tmp_path),
        "shard",
        "--source-manifest", str(tmp_path / "nope.jsonl"),
        "--run-id", "test",
        "--shard-size", "20",
        "--no-tier2", "--no-tier3",
    ])
    assert rc == 2


# --------------- work -----------------------------------------


def test_work_subcommand_completes_all_shards(sharded_run):
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    rc = sr.main([
        "--base-dir", str(base), "work",
        "--run-id", run_id,
    ])
    assert rc == 0
    state = ss.read_state(sr.state_path(base, run_id))
    for sid in ("000", "001", "002"):
        assert state["shards"][sid]["state"] == "done"
        assert state["shards"][sid]["n_entries"] > 0
        assert "cache_sha256" in state["shards"][sid]


def test_work_subcommand_writes_cache_files(sharded_run):
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    sr.main(["--base-dir", str(base), "work", "--run-id", run_id])
    for sid in ("000", "001", "002"):
        cp = sr.shard_cache_path(base, run_id, sid)
        assert cp.exists()
        cache = json.loads(cp.read_text())
        assert "records" in cache
        assert "meta" in cache


def test_work_subcommand_state_file_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture,
):
    rc = sr.main([
        "--base-dir", str(tmp_path), "work", "--run-id", "no-such-run",
    ])
    assert rc == 2


def test_work_subcommand_handles_scorer_failure(
    sharded_run, monkeypatch: pytest.MonkeyPatch,
):
    """A scorer that raises should mark the shard failed (with
    failure_reason recorded) and return non-zero, not leave the
    shard stuck in 'claimed' forever."""
    def _failing_scorer(*args, **kwargs):
        raise RuntimeError("simulated scoring failure")
    monkeypatch.setattr(sr, "DEFAULT_SCORER", _failing_scorer)
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    rc = sr.main(["--base-dir", str(base), "work", "--run-id", run_id])
    assert rc == 4
    state = ss.read_state(sr.state_path(base, run_id))
    # First shard should be marked failed; the rest stay pending.
    sh000 = state["shards"]["000"]
    assert sh000["state"] == "failed"
    assert "simulated scoring failure" in sh000["failure_reason"]


# --------------- Multi-worker mode (v1.44.1) ------------------


def test_work_subcommand_creates_and_releases_claim_file(sharded_run):
    """v1.44.1 invariant: single-worker mode now goes through the
    atomic-claim path. After a successful run, no .claim files
    should remain — each shard's claim is released on completion.
    """
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    rc = sr.main(["--base-dir", str(base), "work", "--run-id", run_id])
    assert rc == 0
    # After all shards complete, claim files should be cleaned up.
    for sid in ("000", "001", "002"):
        claim_path = sr.shard_claim_path(base, run_id, sid)
        assert not claim_path.exists(), (
            f"Stale claim file at {claim_path}; expected release on "
            f"shard completion."
        )


def test_work_subcommand_skips_pre_claimed_shards(sharded_run):
    """If a .claim file already exists for a shard, the worker
    should treat it as already-owned and not attempt to claim it
    again. Without this behavior, a second worker would race past
    the atomic-claim primitive and corrupt state.json updates.
    """
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    # Pre-create a claim file for shard 000 — simulating another
    # worker already owns it.
    claim_path = sr.shard_claim_path(base, run_id, "000")
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    won = ss.try_claim_shard_atomically(
        claim_path, host="other-host", pid=99999,
    )
    assert won is True
    # Now run a worker. It should claim shards 001 and 002 but
    # skip 000 because the claim file is already in place.
    rc = sr.main(["--base-dir", str(base), "work", "--run-id", run_id])
    assert rc == 0
    state = ss.read_state(sr.state_path(base, run_id))
    # 000 stays pending (we hold its claim from a "different
    # worker" but didn't actually score it).
    assert state["shards"]["000"]["state"] == "pending"
    # 001 and 002 should be done.
    assert state["shards"]["001"]["state"] == "done"
    assert state["shards"]["002"]["state"] == "done"
    # The pre-existing claim file is still in place.
    claim_content = ss.read_claim_file(claim_path)
    assert claim_content is not None
    assert claim_content["host"] == "other-host"


def test_work_subcommand_two_workers_complete_all_shards(
    sharded_run, monkeypatch: pytest.MonkeyPatch,
):
    """v1.44.1 multi-worker integration test: two concurrent
    workers process three shards. Each shard goes to exactly one
    worker; no shard gets scored twice; no shard is left pending.

    Uses ``fork`` so the stub-scorer monkeypatch from the
    ``sharded_run`` fixture propagates into the spawned worker
    subprocesses. On non-POSIX (Windows-native), this test would
    need to either use a picklable top-level stub or
    skip; SETEC's calibration host is WSL2 Linux so fork is the
    target deployment.
    """
    import multiprocessing as mp
    # Confirm fork is available (skip on platforms without it).
    try:
        mp.get_context("fork")
    except ValueError:
        pytest.skip("fork start method unavailable on this platform")
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    rc = sr.main([
        "--base-dir", str(base), "work",
        "--run-id", run_id,
        "--workers", "2",
    ])
    assert rc == 0, "All workers should exit cleanly"
    state = ss.read_state(sr.state_path(base, run_id))
    # Every shard should be done.
    for sid in ("000", "001", "002"):
        assert state["shards"][sid]["state"] == "done", (
            f"Shard {sid} state is {state['shards'][sid]['state']!r}; "
            f"expected 'done' after multi-worker completion."
        )
    # No leftover claim files.
    for sid in ("000", "001", "002"):
        claim_path = sr.shard_claim_path(base, run_id, sid)
        assert not claim_path.exists()
    # Per-shard record count is consistent (no duplicate scoring).
    for sid in ("000", "001", "002"):
        cp = sr.shard_cache_path(base, run_id, sid)
        assert cp.exists()
        cache = json.loads(cp.read_text())
        # The stub scorer produces one record per shard-manifest
        # row; with 60 rows / 3 shards = 20 records per shard.
        assert len(cache["records"]) == 20


def test_workers_arg_defaults_to_one(sharded_run):
    """Backwards compat: omitting --workers runs single-worker
    mode, exactly the v1.44.0 behavior."""
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    # No --workers flag — should run single-worker.
    rc = sr.main(["--base-dir", str(base), "work", "--run-id", run_id])
    assert rc == 0
    state = ss.read_state(sr.state_path(base, run_id))
    for sid in ("000", "001", "002"):
        assert state["shards"][sid]["state"] == "done"


# --------------- aggregate -----------------------------------


def test_aggregate_concats_records_and_skips_derive_when_requested(
    sharded_run, tmp_path: Path,
):
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    # Complete all shards.
    sr.main(["--base-dir", str(base), "work", "--run-id", run_id])
    out_path = tmp_path / "agg.json"
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(out_path),
        "--no-derive",
    ])
    assert rc == 0
    payload = json.loads(out_path.read_text())
    # 60 source rows / 3 shards × 3 shards = 60.
    assert payload["n_records"] == 60
    assert payload["n_shards_contributed"] == 3
    assert payload["run_id"] == run_id
    assert payload["per_signal"] == {}


def test_aggregate_refuses_when_shards_not_done(sharded_run, tmp_path: Path):
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    # No work; all shards still pending.
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(tmp_path / "agg.json"),
        "--no-derive",
    ])
    assert rc == 2


def test_aggregate_refuses_when_done_shard_cache_is_tampered(
    sharded_run, tmp_path: Path, capsys: pytest.CaptureFixture,
):
    """Reviewer P2 regression (PR #17 follow-up, 2026-05-12).

    `aggregate` is the artifact-producing command; it must not
    depend on a separate manual `verify` step to detect tampered
    or stale shard caches. State-integrity contract: every done
    shard's on-disk cache must match the SHA-256 recorded in
    `state.json` when the shard was marked done. A mismatched
    cache is a tampering / staleness failure that would produce
    a wrong aggregate.

    Without ``--allow-partial``, the aggregator refuses and exits
    non-zero with a hash-mismatch report. With ``--allow-partial``,
    it warns and continues with the surviving shards (same
    semantics as the missing-cache integrity failure).
    """
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    # Complete all shards normally; state.json records sha256 per
    # done shard.
    sr.main(["--base-dir", str(base), "work", "--run-id", run_id])
    # Tamper one shard's cache by appending a record (changes the
    # SHA-256 without disturbing JSON parseability — exactly the
    # quiet failure mode aggregate must catch).
    cp_to_tamper = sr.shard_cache_path(base, run_id, "001")
    cache = json.loads(cp_to_tamper.read_text())
    cache["records"].append({"injected": True, "label": "pre_ai_human"})
    cp_to_tamper.write_text(json.dumps(cache))
    # Aggregate without --allow-partial must refuse with a hash
    # mismatch report.
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(tmp_path / "agg-tampered.json"),
        "--no-derive",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "integrity failures" in err
    assert ("Cache hash mismatches" in err or "tampered" in err.lower())
    assert "--allow-partial" in err
    # Aggregate WITH --allow-partial should succeed but skip the
    # tampered shard.
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(tmp_path / "agg-tampered-partial.json"),
        "--allow-partial",
        "--no-derive",
    ])
    assert rc == 0
    payload = json.loads(
        (tmp_path / "agg-tampered-partial.json").read_text(),
    )
    # Two surviving shards (000 + 002); shard 001 was tampered and
    # skipped. The injected record never reaches the aggregate.
    assert payload["n_shards_contributed"] == 2
    assert "001" not in payload["contributing_shards"]


def test_aggregate_refuses_when_done_shard_cache_is_missing(
    sharded_run, tmp_path: Path, capsys: pytest.CaptureFixture,
):
    """Reviewer P2 regression (PR #17, 2026-05-12).

    State-integrity contract: if state.json marks a shard as done,
    its cache file must exist. A done shard whose cache file is
    gone is a state-integrity failure — the alternative (silently
    skipping the missing shard) produces a "complete" aggregate
    artifact whose n_records and per-signal sweeps don't match
    what state.json claims.

    Without ``--allow-partial``, the aggregator should refuse and
    exit non-zero rather than producing the silently-incomplete
    artifact.
    """
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    # Complete all shards normally.
    sr.main(["--base-dir", str(base), "work", "--run-id", run_id])
    # Now delete one shard's cache file while leaving state.json saying
    # the shard is done.
    cp_to_delete = sr.shard_cache_path(base, run_id, "001")
    assert cp_to_delete.exists()
    cp_to_delete.unlink()
    # Aggregate without --allow-partial must refuse.
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(tmp_path / "agg-incomplete.json"),
        "--no-derive",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    # The error message format unified after the integrity-check
    # follow-up: missing caches AND tampered caches share the
    # "integrity failures" gate.
    assert "integrity failures" in err
    assert "Missing cache files" in err
    assert "--allow-partial" in err
    # Aggregate WITH --allow-partial should succeed but only count
    # the surviving shards.
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(tmp_path / "agg-partial.json"),
        "--allow-partial",
        "--no-derive",
    ])
    assert rc == 0
    payload = json.loads((tmp_path / "agg-partial.json").read_text())
    # Two surviving shards, ~40 records.
    assert payload["n_shards_contributed"] == 2
    assert payload["n_records"] < 60


def test_aggregate_namespace_includes_required_fields_for_derive(
    sharded_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Reviewer P2 regression (PR #17, 2026-05-12).

    `derive_threshold_from_records` reads `args.slug`, `args.use`,
    and `args.notes` in addition to the signal/manifest/fpr fields.
    The earlier aggregate path built a minimal Namespace missing
    these three fields, causing `AttributeError` mid-derivation and
    storing the failure as a per-signal error rather than producing
    real entries.

    This test stubs `derive_threshold_from_records` to inspect the
    Namespace it receives and confirms all required fields are
    present.
    """
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    # Complete all shards.
    sr.main(["--base-dir", str(base), "work", "--run-id", run_id])
    # Stub calibrate_thresholds.derive_threshold_from_records so the
    # test doesn't need a real signal column in the synthetic
    # records.
    captured_namespaces: list = []

    class _FakeCT:
        COMPRESSION_HEURISTICS = {"stub_signal": object()}

        @staticmethod
        def derive_threshold_from_records(records, *, args, scoring_meta):
            captured_namespaces.append(args)
            return {"slug": args.slug, "signal": args.signal,
                    "derived_value": 0.0}

    monkeypatch.setitem(sys.modules, "calibrate_thresholds", _FakeCT)
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(tmp_path / "agg.json"),
    ])
    assert rc == 0
    assert len(captured_namespaces) == 1
    ns = captured_namespaces[0]
    # The contract that broke under the pre-fix aggregate path:
    assert hasattr(ns, "slug"), "Namespace must carry .slug"
    assert hasattr(ns, "use"), "Namespace must carry .use"
    assert hasattr(ns, "notes"), "Namespace must carry .notes"
    # And the previously-present fields, for full contract coverage:
    assert hasattr(ns, "signal")
    assert hasattr(ns, "manifest")
    assert hasattr(ns, "fpr_target")
    assert hasattr(ns, "bootstrap_seed")
    assert hasattr(ns, "bootstrap_resamples")
    assert hasattr(ns, "bootstrap_confidence")
    # Slug should be sharded-run-aware (not hard-coded "editlens"
    # like the calibrate_thresholds default).
    assert "sharded" in ns.slug.lower() or run_id in ns.slug
    # Notes should reference the sharded run so a maintainer reading
    # the resulting entry knows it came from sharded aggregation.
    assert "shard" in ns.notes.lower()


def test_aggregate_allow_partial_processes_done_shards_only(
    sharded_run, tmp_path: Path,
):
    """With --allow-partial, the aggregator processes shards that
    are done and skips others. Useful for inspecting progress
    mid-run, not for shipping calibration entries."""
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    # Hand-complete shard 000 only.
    state = ss.read_state(sr.state_path(base, run_id))
    cp = sr.shard_cache_path(base, run_id, "000")
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps({
        "records": [{"text_id": "r0", "label": "pre_ai_human", "scores": {}}],
        "meta": {},
    }))
    state = ss.claim_shard(state, "000")
    ss.mark_done(
        state, "000",
        n_entries=1,
        cache_path=str(cp.relative_to(base)),
        cache_sha256=ss.sha256_file(cp),
    )
    ss.write_state(sr.state_path(base, run_id), state)
    out_path = tmp_path / "agg-partial.json"
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(out_path),
        "--allow-partial",
        "--no-derive",
    ])
    assert rc == 0
    payload = json.loads(out_path.read_text())
    assert payload["n_records"] == 1
    assert payload["n_shards_contributed"] == 1


# --------------- verify --------------------------------------


def test_verify_passes_on_unmodified_caches(sharded_run):
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    sr.main(["--base-dir", str(base), "work", "--run-id", run_id])
    rc = sr.main(["--base-dir", str(base), "verify", "--run-id", run_id])
    assert rc == 0


def test_verify_fails_on_tampered_cache(sharded_run):
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    sr.main(["--base-dir", str(base), "work", "--run-id", run_id])
    # Corrupt one cache file.
    cp = sr.shard_cache_path(base, run_id, "000")
    cache = json.loads(cp.read_text())
    cache["records"].append({"injected": True})
    cp.write_text(json.dumps(cache))
    rc = sr.main(["--base-dir", str(base), "verify", "--run-id", run_id])
    assert rc == 4


def test_verify_state_file_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture,
):
    rc = sr.main([
        "--base-dir", str(tmp_path), "verify", "--run-id", "no-such-run",
    ])
    assert rc == 2


# --------------- status --------------------------------------


def test_status_reports_progress(sharded_run, capsys: pytest.CaptureFixture):
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    sr.main(["--base-dir", str(base), "work", "--run-id", run_id])
    rc = sr.main(["--base-dir", str(base), "status", "--run-id", run_id, "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    summary = json.loads(out)
    assert summary["counts"]["done"] == 3
    assert summary["fraction_done"] == 1.0


def test_status_human_readable(sharded_run, capsys: pytest.CaptureFixture):
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    rc = sr.main(["--base-dir", str(base), "status", "--run-id", run_id])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pending" in out
    assert "done" in out


# --------------- read_manifest / write_manifest ----------------


def test_read_manifest_skips_blank_lines(tmp_path: Path):
    p = tmp_path / "m.jsonl"
    p.write_text('{"a":1}\n\n{"b":2}\n  \n')
    rows = sr.read_manifest(p)
    assert rows == [{"a": 1}, {"b": 2}]


def test_read_manifest_raises_on_bad_json(tmp_path: Path):
    p = tmp_path / "m.jsonl"
    p.write_text('{"a":1}\n{not valid\n')
    with pytest.raises(ValueError):
        sr.read_manifest(p)


def test_write_manifest_roundtrip(tmp_path: Path):
    p = tmp_path / "m.jsonl"
    sr.write_manifest(p, [{"a": 1}, {"b": 2}])
    assert sr.read_manifest(p) == [{"a": 1}, {"b": 2}]
