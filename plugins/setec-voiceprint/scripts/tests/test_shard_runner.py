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


# --------------- v1.44.1.B: pause-marker -----------------------


def test_pause_marker_write_and_clear(sharded_run):
    """Round-trip: write a pause marker via pause-all, observe
    `is_paused` returns True, clear it, observe False."""
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    assert sr.is_paused(base, run_id) is False
    rc = sr.main([
        "--base-dir", str(base), "pause-all",
        "--run-id", run_id,
        "--reason", "operator test pause",
    ])
    assert rc == 0
    assert sr.is_paused(base, run_id) is True
    # The marker file should be valid JSON with the recorded reason.
    marker = sr.pause_marker_path(base, run_id)
    payload = json.loads(marker.read_text())
    assert payload["reason"] == "operator test pause"
    assert "paused_at" in payload
    # Clear it.
    rc = sr.main([
        "--base-dir", str(base), "pause-all",
        "--run-id", run_id, "--clear",
    ])
    assert rc == 0
    assert sr.is_paused(base, run_id) is False


def test_pause_all_clear_when_no_marker_returns_1(
    sharded_run, capsys: pytest.CaptureFixture,
):
    """Clearing a non-existent marker returns rc=1 (informational —
    nothing to do — not an error)."""
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    rc = sr.main([
        "--base-dir", str(base), "pause-all",
        "--run-id", run_id, "--clear",
    ])
    assert rc == 1


def test_work_exits_cleanly_when_pause_marker_present(
    sharded_run, capsys: pytest.CaptureFixture,
):
    """A worker invoked while a pause marker is present exits
    cleanly with rc=0 and does NOT process any shards."""
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    sr.write_pause_marker(base, run_id, reason="test")
    rc = sr.main(["--base-dir", str(base), "work", "--run-id", run_id])
    assert rc == 0
    # No shards should have moved out of pending.
    state = ss.read_state(sr.state_path(base, run_id))
    for sid in ("000", "001", "002"):
        assert state["shards"][sid]["state"] == "pending"


# --------------- v1.44.1.B: --time-window ---------------------


def test_parse_time_window_roundtrip():
    """Direct unit test of the parser, including the cross-midnight
    case and invalid inputs."""
    import datetime as dt
    assert sr.parse_time_window("23:00-06:00") == (
        dt.time(23, 0), dt.time(6, 0),
    )
    assert sr.parse_time_window("09:00-17:00") == (
        dt.time(9, 0), dt.time(17, 0),
    )
    # Tolerant of whitespace.
    assert sr.parse_time_window("  09:00 - 17:00  ") == (
        dt.time(9, 0), dt.time(17, 0),
    )
    with pytest.raises(ValueError):
        sr.parse_time_window("not a window")
    with pytest.raises(ValueError):
        sr.parse_time_window("23:00")  # missing end


def test_is_within_time_window_handles_cross_midnight():
    """A 23:00-06:00 window crosses midnight; 03:00 must register
    as in-window, 12:00 must register as out."""
    import datetime as dt
    w = (dt.time(23, 0), dt.time(6, 0))
    # 23:30 — in-window
    assert sr.is_within_time_window(
        w, now=dt.datetime(2026, 1, 1, 23, 30),
    )
    # 03:00 — in-window
    assert sr.is_within_time_window(
        w, now=dt.datetime(2026, 1, 1, 3, 0),
    )
    # 06:00 — out (endpoint exclusive)
    assert not sr.is_within_time_window(
        w, now=dt.datetime(2026, 1, 1, 6, 0),
    )
    # 12:00 — out
    assert not sr.is_within_time_window(
        w, now=dt.datetime(2026, 1, 1, 12, 0),
    )
    # No window — always in
    assert sr.is_within_time_window(None)


def test_work_exits_when_outside_time_window(
    sharded_run, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
):
    """If the time window is configured and the current local time
    is outside it, the worker exits cleanly with rc=0 without
    processing any shards.

    We patch ``datetime.datetime.now`` (via _dt) inside shard_runner
    so the test can pin "current time" to a specific moment. A
    23:00-06:00 window combined with a fake "now" of 12:00 puts
    the worker outside the window.
    """
    import datetime as dt

    class _FakeDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            base = dt.datetime(2026, 1, 1, 12, 0)
            return base if tz is None else base.replace(tzinfo=tz)

    monkeypatch.setattr(sr._dt, "datetime", _FakeDatetime)
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    rc = sr.main([
        "--base-dir", str(base), "work",
        "--run-id", run_id,
        "--time-window", "23:00-06:00",
    ])
    assert rc == 0
    # No shards should have moved out of pending.
    state = ss.read_state(sr.state_path(base, run_id))
    for sid in ("000", "001", "002"):
        assert state["shards"][sid]["state"] == "pending"


def test_work_runs_when_inside_time_window(
    sharded_run, monkeypatch: pytest.MonkeyPatch,
):
    """Same setup, but fake 'now' falls inside the window, so the
    worker processes shards as normal."""
    import datetime as dt

    class _FakeDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            base = dt.datetime(2026, 1, 1, 2, 30)  # in [23:00, 06:00)
            return base if tz is None else base.replace(tzinfo=tz)

    monkeypatch.setattr(sr._dt, "datetime", _FakeDatetime)
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    rc = sr.main([
        "--base-dir", str(base), "work",
        "--run-id", run_id,
        "--time-window", "23:00-06:00",
    ])
    assert rc == 0
    state = ss.read_state(sr.state_path(base, run_id))
    for sid in ("000", "001", "002"):
        assert state["shards"][sid]["state"] == "done"


def test_work_rejects_malformed_time_window(
    sharded_run, capsys: pytest.CaptureFixture,
):
    """A bad --time-window should fail fast with rc=2 and an error
    message, not silently fall back to 'no window'."""
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    rc = sr.main([
        "--base-dir", str(base), "work",
        "--run-id", run_id,
        "--time-window", "garbage",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Invalid --time-window" in err


# --------------- v1.44.1.B: SigtermInterrupt contract --------


def test_sigterm_interrupt_marks_pending_resume(
    sharded_run, monkeypatch: pytest.MonkeyPatch,
):
    """A scorer that raises ``SigtermInterrupt`` mid-shard should
    cause the orchestrator to:
      * mark the shard claimed_pending_resume with the flushed/total
        entry counts
      * KEEP the .claim file in place (resume eligibility per §2.4)
      * return rc=0 (clean checkpoint exit, not a failure)
    """
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]

    def _interrupting_scorer(
        shard_manifest_path, *, cache_path, **kwargs,
    ):
        # Pretend we got partway through.
        raise sr.SigtermInterrupt(
            n_entries_flushed=7,
            n_entries_total=20,
            partial_cache_path=Path(cache_path),
        )

    monkeypatch.setattr(sr, "DEFAULT_SCORER", _interrupting_scorer)
    rc = sr.main(["--base-dir", str(base), "work", "--run-id", run_id])
    # Clean checkpoint exit.
    assert rc == 0
    # Exactly one shard should be in claimed_pending_resume; the
    # remaining shards are still pending (worker exited on first
    # checkpoint).
    state = ss.read_state(sr.state_path(base, run_id))
    pending_resume = [
        sid for sid, sh in state["shards"].items()
        if sh.get("state") == "claimed_pending_resume"
    ]
    assert len(pending_resume) == 1
    cpr = state["shards"][pending_resume[0]]
    assert cpr["n_entries_flushed"] == 7
    assert cpr["n_entries_total"] == 20
    # Claim file MUST still be in place — only the original host
    # may resume.
    claim_path = sr.shard_claim_path(base, run_id, pending_resume[0])
    assert claim_path.exists()


def test_sigterm_interrupt_resume_path(
    sharded_run, monkeypatch: pytest.MonkeyPatch,
):
    """After a SigtermInterrupt, a second `work` invocation on the
    same host should pick up the claimed_pending_resume shard via
    the resume path (expected_state='claimed_pending_resume') and
    re-score it. Here the second run's scorer succeeds normally.
    """
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]

    def _interrupting_scorer(shard_manifest_path, *, cache_path, **kwargs):
        raise sr.SigtermInterrupt(
            n_entries_flushed=5, n_entries_total=20,
        )

    monkeypatch.setattr(sr, "DEFAULT_SCORER", _interrupting_scorer)
    sr.main(["--base-dir", str(base), "work", "--run-id", run_id])
    state = ss.read_state(sr.state_path(base, run_id))
    assert any(
        sh.get("state") == "claimed_pending_resume"
        for sh in state["shards"].values()
    )
    # Second run: scorer succeeds.
    monkeypatch.setattr(sr, "DEFAULT_SCORER", _stub_scorer)
    rc = sr.main(["--base-dir", str(base), "work", "--run-id", run_id])
    assert rc == 0
    state = ss.read_state(sr.state_path(base, run_id))
    for sid in ("000", "001", "002"):
        assert state["shards"][sid]["state"] == "done"


def test_sigterm_interrupt_resume_refreshes_claim_file(
    sharded_run, monkeypatch: pytest.MonkeyPatch,
):
    """Reviewer P2 (2026-05-14 round 4): the resume path used to
    leave the original (now-dead) worker's pid + start_time_epoch
    in the .claim file. terminate-all / kill-all reading the
    .claim file would then see a dead pid and skip — leaving the
    live resumed worker effectively unsignalable.

    Fix: on resume, the worker calls refresh_claim_file() to
    overwrite the .claim file with its own pid + start_time_epoch
    before transitioning state. This test pins both:

      * The claim file's pid matches the current process after
        the resume path runs (i.e., not the stale original-worker
        pid).
      * The claim file's start_time_epoch matches the current
        process (i.e., a fresh ps reading, not the original
        worker's stored value).
    """
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    # Plant a claimed_pending_resume shard with stale claim
    # metadata — simulating the original worker having exited.
    state = ss.read_state(sr.state_path(base, run_id))
    state["shards"]["000"]["state"] = "claimed_pending_resume"
    state["shards"]["000"]["claimed_by_host"] = ss._host()
    state["shards"]["000"]["claimed_by_pid"] = 999_999_999  # dead
    state["shards"]["000"]["n_entries_flushed"] = 10
    state["shards"]["000"]["n_entries_total"] = 20
    ss.write_state(sr.state_path(base, run_id), state)
    # Stale .claim file with the dead-original-worker's metadata.
    claim_path = sr.shard_claim_path(base, run_id, "000")
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    stale_start = 0.0  # Epoch 0 — definitely not the test process.
    claim_path.write_text(json.dumps({
        "host": ss._host(),
        "pid": 999_999_999,
        "claimed_at": "2026-01-01T00:00:00+00:00",
        "start_time_epoch": stale_start,
        "tool": "shard_runner",
    }))
    # Run the worker. The resume path should pick up shard 000,
    # refresh the .claim file with our pid + start time, and
    # complete it.
    rc = sr.main(["--base-dir", str(base), "work", "--run-id", run_id])
    assert rc == 0
    # The state.json says the shard is done now.
    state = ss.read_state(sr.state_path(base, run_id))
    assert state["shards"]["000"]["state"] == "done"
    # The claim file was released on done (existing v1.44.1.A
    # behavior). To prove the refresh happened, we need to inspect
    # the mid-run state. Easier approach: stop the worker before
    # completion via a scorer-injected mid-shard checkpoint, then
    # check the file.
    #
    # We've proven the integration end-to-end (resume completed
    # successfully). For the file-content assertion, see the
    # focused test below that uses refresh_claim_file directly.


def test_refresh_claim_file_overwrites_with_current_pid_and_start_time(
    tmp_path: Path,
):
    """Reviewer P2 (2026-05-14 round 4) focused unit test:
    refresh_claim_file() must overwrite an existing claim file
    with this process's current pid + start_time_epoch, atomically.

    Atomicity check: no .claim-refresh-*.tmp files should remain in
    the directory after a successful refresh."""
    cp = tmp_path / ".claim"
    # Stale claim file with someone else's metadata.
    cp.write_text(json.dumps({
        "host": "old-host",
        "pid": 999_999_999,
        "claimed_at": "2026-01-01T00:00:00+00:00",
        "start_time_epoch": 0.0,
        "tool": "shard_runner",
    }))
    ss.refresh_claim_file(cp)
    refreshed = json.loads(cp.read_text(encoding="utf-8"))
    # PID overwritten with current.
    assert refreshed["pid"] == os.getpid()
    # start_time_epoch overwritten with current (a real float, not 0.0).
    if refreshed["start_time_epoch"] is not None:
        # When ps is available, the refreshed start time should be
        # different from the stale value.
        assert refreshed["start_time_epoch"] != 0.0
        # And reasonable (>= the Unix epoch by a wide margin).
        assert refreshed["start_time_epoch"] > 1_000_000_000
    # Host refreshed.
    assert refreshed["host"] == ss._host()
    # Tool fingerprint preserved.
    assert refreshed["tool"] == "shard_runner"
    # claimed_at is a fresh timestamp.
    assert refreshed["claimed_at"] != "2026-01-01T00:00:00+00:00"
    # Atomicity: no stray temp file from the temp+rename dance.
    leftover = list(tmp_path.glob(".claim-refresh-*.tmp"))
    assert leftover == []


def test_refresh_claim_file_makes_resumed_worker_signalable(
    sharded_run, monkeypatch: pytest.MonkeyPatch,
):
    """End-to-end reproducer for the reviewer's scenario: a
    resumed worker holding a state.json entry with the current pid
    AND a freshly-refreshed .claim file with the same current pid
    must be successfully signalable via terminate-all.

    Pre-fix: state.json had the new pid, .claim still had the dead
    original-worker pid. terminate-all read .claim, saw the dead
    pid, skipped — the live worker was unsignalable.

    Post-fix: refresh_claim_file() on resume aligns the .claim
    file with state.json. terminate-all reads the refreshed pid,
    sees the live process with matching start_time_epoch, signals
    successfully."""
    import signal as _sig
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    # Plant the claimed_pending_resume shard.
    state = ss.read_state(sr.state_path(base, run_id))
    state["shards"]["000"]["state"] = "claimed_pending_resume"
    state["shards"]["000"]["claimed_by_host"] = ss._host()
    state["shards"]["000"]["claimed_by_pid"] = 999_999_999
    ss.write_state(sr.state_path(base, run_id), state)
    claim_path = sr.shard_claim_path(base, run_id, "000")
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.write_text(json.dumps({
        "host": ss._host(),
        "pid": 999_999_999,
        "claimed_at": "2026-01-01T00:00:00+00:00",
        "start_time_epoch": 0.0,
        "tool": "shard_runner",
    }))
    # Directly invoke refresh_claim_file (the resume path's new
    # call) — simulating what happens just before the worker
    # transitions state to claimed.
    ss.refresh_claim_file(claim_path)
    refreshed = json.loads(claim_path.read_text(encoding="utf-8"))
    assert refreshed["pid"] == os.getpid()
    # Now stub os.kill so terminate-all observes the SIGTERM
    # without actually signalling.
    kill_calls: list[tuple[int, int]] = []
    real_kill = os.kill

    def _fake_kill(pid, sig):
        kill_calls.append((pid, sig))
        if sig == 0:
            return real_kill(pid, sig)

    monkeypatch.setattr(sr.os, "kill", _fake_kill)
    monkeypatch.setattr(ss.os, "kill", _fake_kill)
    rc = sr.main([
        "--base-dir", str(base), "terminate-all",
        "--run-id", run_id,
    ])
    # terminate-all found exactly one live worker to signal (us).
    assert rc == 0
    sigterm_calls = [
        (pid, s) for pid, s in kill_calls if s == _sig.SIGTERM
    ]
    assert len(sigterm_calls) == 1
    assert sigterm_calls[0][0] == os.getpid()


# --------------- v1.44.1.B: sweep-stale ----------------------


def test_sweep_stale_releases_dead_claim_past_threshold(sharded_run):
    """sweep-stale should release a claim whose pid is dead AND
    whose claimed_at timestamp is older than the threshold.
    """
    import datetime as dt
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    # Hand-create a stale claim file for shard 001: dead pid, old
    # timestamp. We use pid=1 only as a sentinel (live on the
    # test runner) — better to use a guaranteed-dead pid.
    # Strategy: write the claim file with the current host but a
    # very high pid that's almost certainly not in use.
    claim_path = sr.shard_claim_path(base, run_id, "001")
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    stale_pid = 999_999_999  # Linux PID max default is ~4M; this is dead.
    old_ts = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=12)
    ).isoformat(timespec="seconds")
    claim_path.write_text(json.dumps({
        "host": ss._host(),  # local host so sweep-stale picks it up
        "pid": stale_pid,
        "claimed_at": old_ts,
    }))
    # Also mark the shard as 'claimed' in state.json (consistent
    # with the claim file).
    state = ss.read_state(sr.state_path(base, run_id))
    state["shards"]["001"]["state"] = "claimed"
    state["shards"]["001"]["claimed_by_host"] = ss._host()
    state["shards"]["001"]["claimed_by_pid"] = stale_pid
    state["shards"]["001"]["claimed_at"] = old_ts
    ss.write_state(sr.state_path(base, run_id), state)
    # Run sweep-stale with a 6-hour threshold (default).
    rc = sr.main([
        "--base-dir", str(base), "sweep-stale",
        "--run-id", run_id,
    ])
    assert rc == 0
    # Claim file should be gone.
    assert not claim_path.exists()
    # Shard state.json entry should be back to pending, with
    # claim metadata cleared.
    state = ss.read_state(sr.state_path(base, run_id))
    assert state["shards"]["001"]["state"] == "pending"
    assert "claimed_by_host" not in state["shards"]["001"]
    assert "claimed_by_pid" not in state["shards"]["001"]


def test_sweep_stale_skips_live_pid(sharded_run):
    """Even with a very-old claim, sweep-stale must NOT release a
    claim whose pid is still alive — that's a long-running shard,
    not a stale one."""
    import datetime as dt
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    claim_path = sr.shard_claim_path(base, run_id, "001")
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    live_pid = os.getpid()  # this test process is by definition alive
    old_ts = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
    ).isoformat(timespec="seconds")
    claim_path.write_text(json.dumps({
        "host": ss._host(),
        "pid": live_pid,
        "claimed_at": old_ts,
    }))
    rc = sr.main([
        "--base-dir", str(base), "sweep-stale",
        "--run-id", run_id,
    ])
    assert rc == 0
    # Live pid → claim survives even with a 24-hour-old timestamp.
    assert claim_path.exists()


def test_sweep_stale_skips_young_dead_claim(sharded_run):
    """A dead pid + a young claim must be skipped. The two-condition
    rule (dead AND old) defeats the same-pid race where a worker
    crashed and immediately restarted with a new pid."""
    import datetime as dt
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    claim_path = sr.shard_claim_path(base, run_id, "001")
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    stale_pid = 999_999_999
    young_ts = (
        # 5 minutes ago — well under the 6-hour default.
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
    ).isoformat(timespec="seconds")
    claim_path.write_text(json.dumps({
        "host": ss._host(),
        "pid": stale_pid,
        "claimed_at": young_ts,
    }))
    rc = sr.main([
        "--base-dir", str(base), "sweep-stale",
        "--run-id", run_id,
    ])
    assert rc == 0
    # Young claim survives even though pid is dead.
    assert claim_path.exists()


def test_sweep_stale_dry_run_does_not_release(sharded_run):
    """--dry-run should report what would be released but leave
    everything in place."""
    import datetime as dt
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    claim_path = sr.shard_claim_path(base, run_id, "001")
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    stale_pid = 999_999_999
    old_ts = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=12)
    ).isoformat(timespec="seconds")
    claim_path.write_text(json.dumps({
        "host": ss._host(),
        "pid": stale_pid,
        "claimed_at": old_ts,
    }))
    state = ss.read_state(sr.state_path(base, run_id))
    state["shards"]["001"]["state"] = "claimed"
    ss.write_state(sr.state_path(base, run_id), state)
    rc = sr.main([
        "--base-dir", str(base), "sweep-stale",
        "--run-id", run_id, "--dry-run",
    ])
    assert rc == 0
    # Dry-run: claim is still in place.
    assert claim_path.exists()
    state = ss.read_state(sr.state_path(base, run_id))
    assert state["shards"]["001"]["state"] == "claimed"


def test_sweep_stale_preserves_pending_resume_by_default(sharded_run):
    """A claimed_pending_resume shard with a dead pid must NOT be
    released by default — per spec §2.4 only the original host may
    resume. Pass --include-resume to release it."""
    import datetime as dt
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    claim_path = sr.shard_claim_path(base, run_id, "001")
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    stale_pid = 999_999_999
    old_ts = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=12)
    ).isoformat(timespec="seconds")
    claim_path.write_text(json.dumps({
        "host": ss._host(),
        "pid": stale_pid,
        "claimed_at": old_ts,
    }))
    # Mark state.json as claimed_pending_resume.
    state = ss.read_state(sr.state_path(base, run_id))
    state["shards"]["001"]["state"] = "claimed_pending_resume"
    state["shards"]["001"]["claimed_by_host"] = ss._host()
    state["shards"]["001"]["claimed_by_pid"] = stale_pid
    state["shards"]["001"]["claimed_at"] = old_ts
    state["shards"]["001"]["n_entries_flushed"] = 10
    state["shards"]["001"]["n_entries_total"] = 20
    ss.write_state(sr.state_path(base, run_id), state)
    # Without --include-resume: claim survives.
    rc = sr.main([
        "--base-dir", str(base), "sweep-stale",
        "--run-id", run_id,
    ])
    assert rc == 0
    assert claim_path.exists()
    state = ss.read_state(sr.state_path(base, run_id))
    assert state["shards"]["001"]["state"] == "claimed_pending_resume"
    # With --include-resume: claim released; partial-progress fields
    # cleared.
    rc = sr.main([
        "--base-dir", str(base), "sweep-stale",
        "--run-id", run_id, "--include-resume",
    ])
    assert rc == 0
    assert not claim_path.exists()
    state = ss.read_state(sr.state_path(base, run_id))
    assert state["shards"]["001"]["state"] == "pending"
    assert "n_entries_flushed" not in state["shards"]["001"]


def test_sweep_stale_skips_remote_host_claims(sharded_run):
    """Cross-host claims must never be swept — we can't liveness-
    check pids on a different machine from POSIX user-space."""
    import datetime as dt
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    claim_path = sr.shard_claim_path(base, run_id, "001")
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    old_ts = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
    ).isoformat(timespec="seconds")
    claim_path.write_text(json.dumps({
        "host": "some-other-machine.local",  # NOT ss._host()
        "pid": 12345,
        "claimed_at": old_ts,
    }))
    rc = sr.main([
        "--base-dir", str(base), "sweep-stale",
        "--run-id", run_id,
    ])
    assert rc == 0
    # Remote claim is untouched even though it's stale.
    assert claim_path.exists()


# --------------- v1.44.1.B: terminate-all / kill-all ---------


def test_terminate_all_no_workers_returns_1(sharded_run):
    """terminate-all on a run with no active workers returns rc=1
    (informational; empty queue is not an error)."""
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    rc = sr.main([
        "--base-dir", str(base), "terminate-all",
        "--run-id", run_id,
    ])
    assert rc == 1


def test_terminate_all_signals_active_pid(
    sharded_run, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
):
    """terminate-all should send SIGTERM to every distinct local
    pid recorded in a .claim file on this run. We stub os.kill so
    the test can observe the signal without actually killing
    anything.

    Reviewer P2 (2026-05-14): claim files must carry the new
    ``start_time_epoch`` field for the PID-identity check to pass.
    We record the live pid's actual start time here so the
    identity check sees a match.
    """
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    # Plant two claim files: shard 001 with a live pid, shard 002
    # with the same live pid (de-dup check). Include start_time_epoch
    # so the new identity check (PR #25 review P2 fix) passes.
    live_pid = os.getpid()
    real_start = ss.process_start_time_epoch(live_pid)
    for sid in ("001", "002"):
        cp = sr.shard_claim_path(base, run_id, sid)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps({
            "host": ss._host(),
            "pid": live_pid,
            "claimed_at": "2026-01-01T00:00:00+00:00",
            "start_time_epoch": real_start,
            "tool": "shard_runner",
        }))
    # Capture os.kill calls.
    kill_calls: list[tuple[int, int]] = []
    real_kill = os.kill

    def _fake_kill(pid, sig):
        kill_calls.append((pid, sig))
        if sig == 0:
            return real_kill(pid, sig)  # liveness check
        # SIGTERM / SIGKILL: do nothing.

    monkeypatch.setattr(sr.os, "kill", _fake_kill)
    monkeypatch.setattr(ss.os, "kill", _fake_kill)
    rc = sr.main([
        "--base-dir", str(base), "terminate-all",
        "--run-id", run_id,
    ])
    assert rc == 0
    # SIGTERM is 15 on POSIX. Each distinct pid signaled exactly once.
    import signal as _sig
    sigterm_calls = [
        (pid, s) for pid, s in kill_calls if s == _sig.SIGTERM
    ]
    assert len(sigterm_calls) == 1
    assert sigterm_calls[0][0] == live_pid


def test_kill_all_uses_sigkill(
    sharded_run, monkeypatch: pytest.MonkeyPatch,
):
    """kill-all should send SIGKILL (not SIGTERM)."""
    import signal as _sig
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    live_pid = os.getpid()
    real_start = ss.process_start_time_epoch(live_pid)
    cp = sr.shard_claim_path(base, run_id, "001")
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps({
        "host": ss._host(),
        "pid": live_pid,
        "claimed_at": "2026-01-01T00:00:00+00:00",
        "start_time_epoch": real_start,
        "tool": "shard_runner",
    }))
    kill_calls: list[tuple[int, int]] = []
    real_kill = os.kill

    def _fake_kill(pid, sig):
        kill_calls.append((pid, sig))
        if sig == 0:
            return real_kill(pid, sig)

    monkeypatch.setattr(sr.os, "kill", _fake_kill)
    monkeypatch.setattr(ss.os, "kill", _fake_kill)
    rc = sr.main([
        "--base-dir", str(base), "kill-all",
        "--run-id", run_id,
    ])
    assert rc == 0
    sigkill_calls = [
        (pid, s) for pid, s in kill_calls if s == _sig.SIGKILL
    ]
    assert len(sigkill_calls) == 1
    assert sigkill_calls[0][0] == live_pid


def test_terminate_all_skips_remote_host(
    sharded_run, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
):
    """A remote-host claim must not be signaled — we can't reach
    pids on another machine from user-space. terminate-all reports
    it and moves on; the operator runs terminate-all on the
    remote host to handle it there."""
    import signal as _sig
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    cp = sr.shard_claim_path(base, run_id, "001")
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps({
        "host": "remote-machine.local",
        "pid": 12345,
        "claimed_at": "2026-01-01T00:00:00+00:00",
    }))
    kill_calls: list[tuple[int, int]] = []

    def _fake_kill(pid, sig):
        kill_calls.append((pid, sig))

    monkeypatch.setattr(sr.os, "kill", _fake_kill)
    monkeypatch.setattr(ss.os, "kill", _fake_kill)
    rc = sr.main([
        "--base-dir", str(base), "terminate-all",
        "--run-id", run_id,
    ])
    assert rc == 1  # nothing signaled
    # No SIGTERM was sent to the remote pid.
    sigterm_calls = [
        (pid, s) for pid, s in kill_calls if s == _sig.SIGTERM
    ]
    assert sigterm_calls == []
    err = capsys.readouterr().err
    assert "remote-machine.local" in err


# ---------- Reviewer P2: PID-reuse defense (2026-05-14) ---------


class TestPidReuseIdentityCheck:
    """Reviewer P2: ``terminate-all`` / ``kill-all`` used to trust
    the claim's recorded PID after only `pid_alive()`. A stale or
    edited claim whose PID had been reused by an unrelated process
    would make `terminate-all` SIGTERM that unrelated process —
    the reviewer reproduced this against a dummy `sleep 60`.

    Fix: at claim time, record the worker's
    ``start_time_epoch`` (via `ps -o lstart= -p PID`). Before
    sending the signal, re-read the live process's start time and
    skip the signal if it doesn't match within a small tolerance
    (the PID has been reused since the claim was written)."""

    def test_process_start_time_epoch_self_is_a_number(self):
        """`process_start_time_epoch(os.getpid())` should return a
        positive epoch number — the test process is alive."""
        start = ss.process_start_time_epoch(os.getpid())
        assert isinstance(start, float)
        assert start > 0

    def test_process_start_time_epoch_dead_pid_returns_none(self):
        """A pid that's almost certainly not allocated should
        produce None — ps -p will exit non-zero."""
        assert ss.process_start_time_epoch(999_999_999) is None

    def test_process_start_time_epoch_handles_permission_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        """Reviewer P2 (2026-05-14 round 4): sandboxed environments
        (Codex's review sandbox, locked-down containers) refuse
        subprocess spawn with PermissionError. The helper must
        return None — its documented contract — instead of letting
        the exception propagate into try_claim_shard_atomically()
        and break the `work` path before a claim can be created."""
        import subprocess as _sp

        def _raise_permission(*args, **kwargs):
            raise PermissionError(
                "[Errno 13] Permission denied: 'ps' "
                "(simulated sandbox refusal)"
            )

        monkeypatch.setattr(_sp, "run", _raise_permission)
        # Must return None, not raise.
        result = ss.process_start_time_epoch(os.getpid())
        assert result is None

    def test_process_start_time_epoch_handles_generic_oserror(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        """Same contract holds for any other OSError raised by
        subprocess.run (locked-down /proc, fork limit reached,
        etc.). The helper must return None across the whole
        OSError family."""
        import subprocess as _sp

        def _raise_oserror(*args, **kwargs):
            raise OSError(
                "[Errno 11] Resource temporarily unavailable "
                "(simulated fork-bomb guard)"
            )

        monkeypatch.setattr(_sp, "run", _raise_oserror)
        assert ss.process_start_time_epoch(os.getpid()) is None

    def test_try_claim_shard_atomically_works_in_sandbox(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """End-to-end: even when `ps` raises PermissionError (the
        sandbox reproducer), try_claim_shard_atomically must
        succeed. Pre-fix, the exception escaped and broke the
        whole `work` path. Post-fix, start_time_epoch lands as
        None in the claim payload and the identity check at signal
        time later treats None as 'unverifiable' (conservative
        refuse). The claim creation itself succeeds."""
        import subprocess as _sp

        def _raise_permission(*args, **kwargs):
            raise PermissionError("simulated sandbox refusal")

        monkeypatch.setattr(_sp, "run", _raise_permission)
        claim_path = tmp_path / ".claim"
        won = ss.try_claim_shard_atomically(claim_path)
        assert won is True
        assert claim_path.exists()
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        # The claim file was written — that's the critical
        # behavior. start_time_epoch is None (unreadable).
        assert claim["start_time_epoch"] is None
        # Host + pid + tool fingerprint still present.
        assert claim["pid"] == os.getpid()
        assert claim["tool"] == "shard_runner"

    def test_claim_matches_live_process_self(self):
        """Round-trip: build a claim with our own start time;
        the identity check should pass for our own pid."""
        pid = os.getpid()
        claim = {
            "host": ss._host(), "pid": pid,
            "start_time_epoch": ss.process_start_time_epoch(pid),
            "claimed_at": "2026-05-14T00:00:00+00:00",
        }
        matches, reason = sr._claim_matches_live_process(claim, pid)
        assert matches is True, reason

    def test_claim_matches_live_process_pid_reused(self):
        """If the recorded start time disagrees with the live pid's
        start time by more than the tolerance, the check refuses."""
        pid = os.getpid()
        claim = {
            "host": ss._host(), "pid": pid,
            # Pretend the claim was written for a worker that
            # started at the Unix epoch — clearly not the same
            # process as our live pid.
            "start_time_epoch": 0.0,
            "claimed_at": "2026-05-14T00:00:00+00:00",
        }
        matches, reason = sr._claim_matches_live_process(claim, pid)
        assert matches is False
        assert "reused" in reason or "match" in reason.lower()

    def test_claim_matches_live_process_legacy_no_start_time(self):
        """A pre-fix claim file with no recorded
        start_time_epoch should be REFUSED — the framework can't
        verify identity, so the conservative move is to not signal.
        Cost: operator has to hand-kill or restart the worker.
        Reward: never signal an unrelated process."""
        claim = {
            "host": ss._host(), "pid": os.getpid(),
            "claimed_at": "2026-05-14T00:00:00+00:00",
            # No start_time_epoch field.
        }
        matches, reason = sr._claim_matches_live_process(
            claim, os.getpid(),
        )
        assert matches is False
        assert "start_time_epoch" in reason or "legacy" in reason.lower()

    def test_terminate_all_refuses_pid_reused(
        self,
        sharded_run, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ):
        """End-to-end reproducer for the reviewer's scenario: claim
        file recorded for a PID whose start time has since changed
        (PID reuse). terminate-all must NOT signal that PID."""
        import signal as _sig
        base = sharded_run["base"]
        run_id = sharded_run["run_id"]
        # Live pid (the test process); but record a start_time_epoch
        # that doesn't match (simulate "the PID has been reused").
        live_pid = os.getpid()
        cp = sr.shard_claim_path(base, run_id, "001")
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps({
            "host": ss._host(),
            "pid": live_pid,
            "claimed_at": "2026-01-01T00:00:00+00:00",
            "start_time_epoch": 0.0,  # epoch 0 — definitely not our process
            "tool": "shard_runner",
        }))
        kill_calls: list[tuple[int, int]] = []
        real_kill = os.kill

        def _fake_kill(pid, sig):
            kill_calls.append((pid, sig))
            if sig == 0:
                return real_kill(pid, sig)

        monkeypatch.setattr(sr.os, "kill", _fake_kill)
        monkeypatch.setattr(ss.os, "kill", _fake_kill)
        rc = sr.main([
            "--base-dir", str(base), "terminate-all",
            "--run-id", run_id,
        ])
        # rc=1: nothing signaled.
        assert rc == 1
        # CRITICALLY: no SIGTERM was sent to our pid.
        sigterm_calls = [
            (pid, s) for pid, s in kill_calls if s == _sig.SIGTERM
        ]
        assert sigterm_calls == []
        err = capsys.readouterr().err
        # Operator-facing message names the PID-reuse condition.
        assert "PID-reuse" in err or "reused" in err.lower()

    def test_terminate_all_refuses_legacy_claim_without_start_time(
        self,
        sharded_run, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ):
        """A pre-2026-05-14 claim file (no start_time_epoch) is the
        canonical example of "unverifiable identity." terminate-all
        must refuse signaling rather than risk hitting an unrelated
        process."""
        base = sharded_run["base"]
        run_id = sharded_run["run_id"]
        live_pid = os.getpid()
        cp = sr.shard_claim_path(base, run_id, "001")
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps({
            "host": ss._host(),
            "pid": live_pid,
            "claimed_at": "2026-01-01T00:00:00+00:00",
            # No start_time_epoch — pre-fix claim file shape.
        }))
        kill_calls: list[tuple[int, int]] = []
        real_kill = os.kill

        def _fake_kill(pid, sig):
            kill_calls.append((pid, sig))
            if sig == 0:
                return real_kill(pid, sig)

        monkeypatch.setattr(sr.os, "kill", _fake_kill)
        monkeypatch.setattr(ss.os, "kill", _fake_kill)
        rc = sr.main([
            "--base-dir", str(base), "terminate-all",
            "--run-id", run_id,
        ])
        assert rc == 1
        import signal as _sig
        sigterm_calls = [
            (pid, s) for pid, s in kill_calls if s == _sig.SIGTERM
        ]
        assert sigterm_calls == []
        err = capsys.readouterr().err
        assert "start_time_epoch" in err or "legacy" in err.lower()

    def test_claim_file_records_start_time_epoch(self, tmp_path: Path):
        """The atomic-claim primitive records start_time_epoch in
        the JSON payload. This is the *fix* — without it,
        terminate-all can't verify the worker's identity later."""
        claim_path = tmp_path / ".claim"
        assert ss.try_claim_shard_atomically(claim_path) is True
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        assert "start_time_epoch" in claim
        # For the live test process, the recorded value should be
        # a real number (ps was able to read it).
        assert isinstance(claim["start_time_epoch"], (int, float))
        # And the tool fingerprint.
        assert claim.get("tool") == "shard_runner"


# ---------- Reviewer P2 (2026-05-14 round 5) ----------


class TestWorkerLoopRaceTolerance:
    """Reviewer P2 round 5: a ShardStateError from claim_shard
    inside _run_single_worker's state-update lock used to be a
    fatal rc=3. In practice that error is almost always benign —
    _select_next_shard read a stale state.json snapshot before
    another worker marked the shard done, this worker won the
    atomic .claim race against the now-empty file, and then the
    state-update found the shard already in 'done' state. Every
    multi-worker run exhibited this race intermittently and
    reported the whole run as failed.

    Fix: catch the specific "Cannot claim shard X in state Y;
    expected Z" shape, release the claim, and continue the loop.
    Reserve rc=3 for unrecognized errors (unknown shard id,
    etc.)."""

    def test_race_lost_releases_claim_and_continues(
        self, sharded_run, monkeypatch: pytest.MonkeyPatch,
    ):
        """Inject a claim_shard that raises the race-shape
        ShardStateError on the first call, succeeds on subsequent
        calls. Worker should release the first claim, continue,
        and succeed on the next shard."""
        base = sharded_run["base"]
        run_id = sharded_run["run_id"]
        # Capture the real claim_shard so we can call it after the
        # first injected failure.
        real_claim_shard = ss.claim_shard
        n_calls = {"count": 0}

        def _flaky_claim(state, sid, *, host=None, pid=None,
                         expected_state="pending"):
            n_calls["count"] += 1
            if n_calls["count"] == 1:
                # Reviewer's exact error shape from the race.
                raise ss.ShardStateError(
                    f"Cannot claim shard {sid} in state 'done'; "
                    f"expected {expected_state!r}"
                )
            return real_claim_shard(
                state, sid, host=host, pid=pid,
                expected_state=expected_state,
            )

        monkeypatch.setattr(sr, "claim_shard", _flaky_claim)
        rc = sr.main([
            "--base-dir", str(base), "work", "--run-id", run_id,
        ])
        # Race recovery: rc=0, not rc=3.
        assert rc == 0
        # All shards completed.
        state = ss.read_state(sr.state_path(base, run_id))
        for sid in ("000", "001", "002"):
            assert state["shards"][sid]["state"] == "done"

    def test_unknown_shard_id_still_returns_rc3(
        self, sharded_run, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ):
        """ShardStateError from an unknown shard id (or any other
        non-race shape) MUST still be fatal — the race-tolerant
        path is reserved for the specific 'state transitioned'
        shape."""
        base = sharded_run["base"]
        run_id = sharded_run["run_id"]

        def _unknown_shard(state, sid, *, host=None, pid=None,
                           expected_state="pending"):
            raise ss.ShardStateError(f"Unknown shard id: {sid!r}")

        monkeypatch.setattr(sr, "claim_shard", _unknown_shard)
        rc = sr.main([
            "--base-dir", str(base), "work", "--run-id", run_id,
        ])
        # Unknown shard is a real corruption — rc=3.
        assert rc == 3

class TestSignalWorkersDedupeOrdering:
    """Reviewer P2 round 5: ``seen_pids.add(claim_pid)`` happened
    BEFORE the identity check. If a stale claim file (lower shard
    id) had a reused PID with mismatched start_time_epoch, the
    PID was added to seen_pids and skipped. A LATER claim file
    (higher shard id) with the same PID but matching start_time
    was then deduped — and the live worker received no signal.

    Fix: only add to seen_pids AFTER a successful os.kill. A
    failed path (remote host, dead pid, identity mismatch) does
    NOT claim the de-dupe slot.
    """

    def test_stale_then_live_same_pid_signals_live(
        self,
        sharded_run, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ):
        """Reviewer's exact reproducer: shard 000 has a stale
        claim (PID matches live, but start_time mismatches);
        shard 001 has a live claim (same PID, matching
        start_time). Pre-fix: no signal sent. Post-fix: the live
        claim gets signaled."""
        import signal as _sig
        base = sharded_run["base"]
        run_id = sharded_run["run_id"]
        live_pid = os.getpid()
        live_start = ss.process_start_time_epoch(live_pid)
        # Skip the test cleanly if ps is unavailable in this
        # environment (sandbox). The identity check would refuse
        # both claims for the legitimate "can't verify" reason
        # and the dedupe-ordering bug wouldn't surface.
        if live_start is None:
            pytest.skip(
                "ps unavailable in this environment; identity check "
                "can't verify the live claim either way."
            )
        # Shard 000: stale claim — same pid but mismatched start
        # time (the OS reused this pid).
        cp_000 = sr.shard_claim_path(base, run_id, "000")
        cp_000.parent.mkdir(parents=True, exist_ok=True)
        cp_000.write_text(json.dumps({
            "host": ss._host(),
            "pid": live_pid,
            "claimed_at": "2026-01-01T00:00:00+00:00",
            "start_time_epoch": 0.0,  # mismatched (epoch 0)
            "tool": "shard_runner",
        }))
        # Shard 001: live claim — matching start_time_epoch.
        cp_001 = sr.shard_claim_path(base, run_id, "001")
        cp_001.parent.mkdir(parents=True, exist_ok=True)
        cp_001.write_text(json.dumps({
            "host": ss._host(),
            "pid": live_pid,
            "claimed_at": "2026-05-14T00:00:00+00:00",
            "start_time_epoch": live_start,
            "tool": "shard_runner",
        }))
        # Stub os.kill so we observe SIGTERM without actually
        # killing the test process.
        kill_calls: list[tuple[int, int]] = []
        real_kill = os.kill

        def _fake_kill(pid, sig_):
            kill_calls.append((pid, sig_))
            if sig_ == 0:
                return real_kill(pid, sig_)

        monkeypatch.setattr(sr.os, "kill", _fake_kill)
        monkeypatch.setattr(ss.os, "kill", _fake_kill)
        rc = sr.main([
            "--base-dir", str(base), "terminate-all",
            "--run-id", run_id,
        ])
        # The live claim WAS signaled.
        assert rc == 0, (
            "expected rc=0 (a signal was sent); pre-fix this "
            "would be rc=1 because the live claim got deduped"
        )
        sigterm_calls = [
            (pid, s) for pid, s in kill_calls if s == _sig.SIGTERM
        ]
        assert len(sigterm_calls) == 1
        assert sigterm_calls[0][0] == live_pid

    def test_same_pid_same_start_time_deduped_after_signal(
        self,
        sharded_run, monkeypatch: pytest.MonkeyPatch,
    ):
        """Sanity check: when a worker legitimately holds claims
        on multiple shards (same pid, same start_time across all
        claims), os.kill should still be called only ONCE — the
        dedupe still works correctly post-fix, just deferred until
        after the successful signal."""
        import signal as _sig
        base = sharded_run["base"]
        run_id = sharded_run["run_id"]
        live_pid = os.getpid()
        live_start = ss.process_start_time_epoch(live_pid)
        if live_start is None:
            pytest.skip("ps unavailable")
        # Three claim files, all with the SAME live identity.
        for sid in ("000", "001", "002"):
            cp = sr.shard_claim_path(base, run_id, sid)
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps({
                "host": ss._host(),
                "pid": live_pid,
                "claimed_at": "2026-05-14T00:00:00+00:00",
                "start_time_epoch": live_start,
                "tool": "shard_runner",
            }))
        kill_calls: list[tuple[int, int]] = []
        real_kill = os.kill

        def _fake_kill(pid, sig_):
            kill_calls.append((pid, sig_))
            if sig_ == 0:
                return real_kill(pid, sig_)

        monkeypatch.setattr(sr.os, "kill", _fake_kill)
        monkeypatch.setattr(ss.os, "kill", _fake_kill)
        rc = sr.main([
            "--base-dir", str(base), "terminate-all",
            "--run-id", run_id,
        ])
        assert rc == 0
        sigterm_calls = [
            (pid, s) for pid, s in kill_calls if s == _sig.SIGTERM
        ]
        # Only ONE SIGTERM even though three claims pointed at
        # the same pid — the post-success dedupe still works.
        assert len(sigterm_calls) == 1


# --------------- v1.44.2: --no-sync-state flag ---------------


def test_work_no_sync_state_flag_parses(sharded_run):
    """`--no-sync-state` is accepted by the work subparser and
    sets args.no_sync_state=True. Since the sharded_run fixture
    uses tmp_path (not in a git repo), sync is silently skipped
    regardless of the flag — this test just verifies the flag
    wiring doesn't break the normal run path."""
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    rc = sr.main([
        "--base-dir", str(base), "work",
        "--run-id", run_id, "--no-sync-state",
    ])
    assert rc == 0
    state = ss.read_state(sr.state_path(base, run_id))
    for sid in ("000", "001", "002"):
        assert state["shards"][sid]["state"] == "done"


def test_should_sync_returns_false_for_tmp_path(sharded_run):
    """Internal helper: tmp_path is never in a git repo, so
    _should_sync returns False even without --no-sync-state."""
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    sp = sr.state_path(base, run_id)
    import argparse as _ap
    args = _ap.Namespace(no_sync_state=False)
    assert sr._should_sync(args, sp) is False


def test_should_sync_respects_no_sync_state_flag(tmp_path: Path):
    """When state.json IS inside a git repo, _should_sync returns
    True by default and False when --no-sync-state was passed."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    nested = repo_root / "calibration_runs" / "x"
    nested.mkdir(parents=True)
    state_file = nested / "state.json"
    state_file.write_text("{}")
    import argparse as _ap
    args_default = _ap.Namespace(no_sync_state=False)
    args_opt_out = _ap.Namespace(no_sync_state=True)
    assert sr._should_sync(args_default, state_file) is True
    assert sr._should_sync(args_opt_out, state_file) is False


def test_synced_state_update_calls_pull_and_push_in_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """When state.json IS in a git repo, _synced_state_update
    should call pull_state before yielding and push_state after.
    Monkeypatch both so we can observe without touching real git."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    nested = repo_root / "calibration_runs" / "x"
    nested.mkdir(parents=True)
    state_file = nested / "state.json"
    state_file.write_text("{}")
    pull_calls = []
    push_calls = []

    def _fake_pull(path, **kwargs):
        pull_calls.append((path, kwargs))
        return True

    def _fake_push(path, *, message, **kwargs):
        push_calls.append((path, message))
        return True

    monkeypatch.setattr(sr, "pull_state", _fake_pull)
    monkeypatch.setattr(sr, "push_state", _fake_push)
    import argparse as _ap
    args = _ap.Namespace(no_sync_state=False)
    with sr._synced_state_update(args, state_file, message="test"):
        pass
    assert len(pull_calls) == 1
    assert len(push_calls) == 1
    assert push_calls[0][1] == "test"


def test_synced_state_update_skips_when_no_sync_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """With --no-sync-state, neither pull nor push should be
    called — _should_sync returns False and the helper takes
    the local-only fast path."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    state_file = repo_root / "state.json"
    state_file.write_text("{}")
    pull_calls = []
    push_calls = []

    def _fake_pull(path, **kwargs):
        pull_calls.append(path)
        return True

    def _fake_push(path, *, message, **kwargs):
        push_calls.append(path)
        return True

    monkeypatch.setattr(sr, "pull_state", _fake_pull)
    monkeypatch.setattr(sr, "push_state", _fake_push)
    import argparse as _ap
    args = _ap.Namespace(no_sync_state=True)
    with sr._synced_state_update(args, state_file, message="test"):
        pass
    assert pull_calls == []
    assert push_calls == []


def test_synced_state_update_tolerates_transient_pull_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
):
    """A non-conflict SyncError on pull is non-fatal: the helper
    logs and continues. The wrapped state-update still runs."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    nested = repo_root / "calibration_runs" / "x"
    nested.mkdir(parents=True)
    state_file = nested / "state.json"
    state_file.write_text("{}")

    def _flaky_pull(path, **kwargs):
        raise ss.SyncError("transient network error")

    def _fake_push(path, *, message, **kwargs):
        return True

    monkeypatch.setattr(sr, "pull_state", _flaky_pull)
    monkeypatch.setattr(sr, "push_state", _fake_push)
    import argparse as _ap
    args = _ap.Namespace(no_sync_state=False)
    body_ran = False
    with sr._synced_state_update(args, state_file, message="t"):
        body_ran = True
    assert body_ran is True  # the locked region still ran
    err = capsys.readouterr().err
    assert "transient" in err
    assert "continuing" in err.lower()


def test_synced_state_update_reraises_conflict_pull_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """A SyncError that mentions resolve-conflict (real merge
    conflict) IS re-raised — the worker should bail out so the
    operator can run resolve-conflict."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    nested = repo_root / "calibration_runs" / "x"
    nested.mkdir(parents=True)
    state_file = nested / "state.json"
    state_file.write_text("{}")

    def _conflict_pull(path, **kwargs):
        raise ss.SyncError("run `shard_runner resolve-conflict` to merge")

    monkeypatch.setattr(sr, "pull_state", _conflict_pull)
    import argparse as _ap
    args = _ap.Namespace(no_sync_state=False)
    with pytest.raises(ss.SyncError, match="resolve-conflict"):
        with sr._synced_state_update(args, state_file, message="t"):
            pass


# --------------- v1.44.2: resolve-conflict subcommand --------


def test_resolve_conflict_fails_outside_git_repo(
    sharded_run, capsys: pytest.CaptureFixture,
):
    """resolve-conflict only makes sense in a git-synced run.
    Outside a repo, it should exit rc=2 with a clear message."""
    base = sharded_run["base"]
    run_id = sharded_run["run_id"]
    rc = sr.main([
        "--base-dir", str(base), "resolve-conflict",
        "--run-id", run_id,
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not inside a git working tree" in err


def test_resolve_conflict_auto_merges_disjoint_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Set up a fake git index with three stages of state.json
    (base, ours, theirs) where the two sides changed different
    shards. resolve-conflict should auto-merge and write the
    result to disk."""
    # Build a fake repo
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    base_dir = repo_root / "baselines"
    run_id = "test-resolve"
    nested = base_dir / "calibration_runs" / run_id
    nested.mkdir(parents=True)
    state_file = nested / "state.json"
    # Write a placeholder; the real content comes from the fake
    # git-show stages.
    state_file.write_text(json.dumps({"shards": {}}))

    base_state = json.dumps({
        "run_id": run_id,
        "shards": {
            "000": {"state": "pending"},
            "001": {"state": "pending"},
        },
    })
    ours_state = json.dumps({
        "run_id": run_id,
        "shards": {
            "000": {"state": "done", "n_entries": 100,
                    "cache_path": "shards/000/cache.json",
                    "cache_sha256": "abc"},
            "001": {"state": "pending"},
        },
    })
    theirs_state = json.dumps({
        "run_id": run_id,
        "shards": {
            "000": {"state": "pending"},
            "001": {"state": "done", "n_entries": 50,
                    "cache_path": "shards/001/cache.json",
                    "cache_sha256": "def"},
        },
    })

    def _fake_show(repo, stage, rel):
        return {1: base_state, 2: ours_state, 3: theirs_state}[stage]

    monkeypatch.setattr(sr, "_git_show_stage", _fake_show)
    rc = sr.main([
        "--base-dir", str(base_dir), "resolve-conflict",
        "--run-id", run_id,
    ])
    assert rc == 0
    merged = json.loads(state_file.read_text())
    assert merged["shards"]["000"]["state"] == "done"
    assert merged["shards"]["001"]["state"] == "done"


def test_resolve_conflict_reports_unresolved_same_shard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
):
    """When both sides claimed the same shard from different
    hosts, resolve-conflict should report the unresolved shard
    and exit rc=7."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    base_dir = repo_root / "baselines"
    run_id = "test-conflict"
    nested = base_dir / "calibration_runs" / run_id
    nested.mkdir(parents=True)
    state_file = nested / "state.json"
    state_file.write_text(json.dumps({"shards": {}}))

    base_state = json.dumps({"run_id": run_id, "shards": {"000": {"state": "pending"}}})
    ours_state = json.dumps({
        "run_id": run_id,
        "shards": {"000": {
            "state": "claimed", "claimed_by_host": "hostA",
            "claimed_by_pid": 1,
            "claimed_at": "2026-05-13T01:00:00+00:00",
        }},
    })
    theirs_state = json.dumps({
        "run_id": run_id,
        "shards": {"000": {
            "state": "claimed", "claimed_by_host": "hostB",
            "claimed_by_pid": 2,
            "claimed_at": "2026-05-13T01:00:01+00:00",
        }},
    })

    def _fake_show(repo, stage, rel):
        return {1: base_state, 2: ours_state, 3: theirs_state}[stage]

    monkeypatch.setattr(sr, "_git_show_stage", _fake_show)
    rc = sr.main([
        "--base-dir", str(base_dir), "resolve-conflict",
        "--run-id", run_id,
    ])
    assert rc == 7
    err = capsys.readouterr().err
    assert "Unresolved" in err
    assert "hostA" in err and "hostB" in err
