#!/usr/bin/env python3
"""Tests for the checkpointed aggregator (PR
feat/checkpointed-aggregate, stacked on
feat/hardened-parallel-aggregator).

The hardened aggregator (1.65.0) made the parallel sweep memory-
safe and CPU-efficient. The checkpointed aggregator (1.66.0) adds
the third leg: turning the all-or-nothing sweep into a
checkpointed, resumable job. The motivation is psychological as
much as technical — yesterday's loop-engine MAGE run cost 8h26m
of single-core wall-clock and produced nothing because it died
before the final monolithic write. Sharding solved this for the
scoring phase years ago; this PR brings the same affordance to
the aggregate phase.

Pin:

  * The aggregator writes a partial JSON to ``--out`` after every
    signal completion, with status="in_progress".
  * The final return flips status to "complete".
  * Atomic writes (tmp + rename) prevent partial-file corruption.
  * On restart, when ``--out`` exists with parseable per_signal
    state, ``--resume`` (default ON) carries forward the prior
    entries and dispatches only the remaining signals.
  * ``--no-resume`` forces a fresh sweep regardless of prior
    state.
  * Resume from a 'complete' payload skips the sweep entirely
    (idempotent re-run).
  * A corrupted prior payload doesn't break the run; the
    aggregator logs the parse failure and starts fresh.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "calibration") not in sys.path:
    sys.path.insert(0, str(ROOT / "calibration"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import shard_runner as sr  # type: ignore  # noqa: E402
import task_surfaces as ts  # type: ignore  # noqa: E402
import calibrate_thresholds as ct  # type: ignore  # noqa: E402

# Re-use the stub scorer + manifest writer from the hardened-
# aggregator tests.
from test_hardened_aggregator import (  # noqa: E402
    _stub_scorer_with_signals,
    _write_synth_manifest,
    hardened_run,
)


# --------------- CLI surface ----------


def test_aggregate_parser_accepts_resume_flags():
    """``--resume`` is on by default; ``--no-resume`` flips it."""
    parser = sr.build_arg_parser()
    args = parser.parse_args([
        "--base-dir", "/tmp", "aggregate", "--run-id", "x",
    ])
    assert args.resume is True
    args = parser.parse_args([
        "--base-dir", "/tmp", "aggregate", "--run-id", "x",
        "--no-resume",
    ])
    assert args.resume is False
    args = parser.parse_args([
        "--base-dir", "/tmp", "aggregate", "--run-id", "x",
        "--resume",
    ])
    assert args.resume is True


# --------------- Checkpoint: partial save on each signal completion --------


def test_partial_json_saved_after_each_signal(
    hardened_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """The aggregator must write a partial JSON (status=
    'in_progress') after every signal completion. We spy on the
    save helper itself to observe the trajectory: each call should
    carry a strictly larger per_signal dict than the previous one,
    and all intermediate calls should be in_progress; only the
    final aggregator return flips to complete."""
    base = hardened_run["base"]
    run_id = hardened_run["run_id"]
    out = tmp_path / "agg.json"

    saved_snapshots: list[tuple[int, str]] = []
    real_save = ts._save_aggregator_partial

    def _spy(payload, out_path):
        saved_snapshots.append((
            len(payload.get("per_signal") or {}),
            payload.get("status", "unknown"),
        ))
        return real_save(payload, out_path)

    monkeypatch.setattr(ts, "_save_aggregator_partial", _spy)
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(out),
    ])
    assert rc == 0
    # Final payload on disk has status=complete + all signals.
    final = json.loads(out.read_text(encoding="utf-8"))
    assert final["status"] == "complete"
    assert len(final["per_signal"]) == len(ct.COMPRESSION_HEURISTICS)
    # The spy observed at least N_signals mid-flight saves (one
    # after each signal completion in the serial path).
    assert len(saved_snapshots) >= len(ct.COMPRESSION_HEURISTICS), (
        f"expected at least {len(ct.COMPRESSION_HEURISTICS)} mid-"
        f"flight saves, got {len(saved_snapshots)}"
    )
    # Per-signal count is monotonically non-decreasing.
    sizes = [s[0] for s in saved_snapshots]
    assert sorted(sizes) == sizes, (
        f"per_signal count must monotonically grow during the "
        f"sweep; observed sequence: {sizes}"
    )
    # Every mid-flight save has status=in_progress.
    for size, status in saved_snapshots:
        assert status == "in_progress", (
            f"all mid-flight saves should be in_progress; "
            f"got status={status} at size={size}"
        )


def test_partial_json_uses_atomic_write(
    hardened_run, tmp_path: Path,
):
    """The save uses tmp + rename so a crash mid-write doesn't
    leave a corrupted JSON. After a clean run, no .tmp file should
    remain on disk."""
    base = hardened_run["base"]
    run_id = hardened_run["run_id"]
    out = tmp_path / "agg.json"
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(out),
    ])
    assert rc == 0
    assert out.exists()
    # No leftover .tmp file.
    assert not out.with_suffix(out.suffix + ".tmp").exists(), (
        "atomic-write tmp file should have been renamed away"
    )


def test_final_payload_status_is_complete(
    hardened_run, tmp_path: Path,
):
    """The aggregator's return value (and final disk state) must
    have status='complete' so downstream consumers can distinguish
    a finished run from a crashed-mid-sweep partial."""
    base = hardened_run["base"]
    run_id = hardened_run["run_id"]
    out = tmp_path / "agg.json"
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(out),
    ])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "complete"


# --------------- Resume: skip already-completed signals ----------


def test_resume_skips_signals_already_in_partial(
    hardened_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Pre-populate ``--out`` with a 'in_progress' payload that
    already has 5 signals. The resumed run should dispatch only
    the remaining signals; derive_threshold_from_records should be
    called fewer times than there are signals in the registry."""
    base = hardened_run["base"]
    run_id = hardened_run["run_id"]
    out = tmp_path / "agg.json"

    all_sigs = sorted(ct.COMPRESSION_HEURISTICS.keys())
    half = all_sigs[:len(all_sigs) // 2]
    pre_existing_per_signal = {
        s: {"slug": f"pre_{s}", "signal": s, "derived_value": 0.0}
        for s in half
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "status": "in_progress",
        "per_signal": pre_existing_per_signal,
        "task_surface": "calibration",
        "tool": "shard_runner",
        "tool_version": "1.0",
        "run_id": run_id,
    }, indent=2))

    derive_calls: list[str] = []
    real_derive = ct.derive_threshold_from_records

    def _record(records, *, args, scoring_meta, **kwargs):
        derive_calls.append(args.signal)
        return real_derive(
            records, args=args, scoring_meta=scoring_meta, **kwargs,
        )

    monkeypatch.setattr(ct, "derive_threshold_from_records", _record)
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(out),
    ])
    assert rc == 0
    # Only the non-pre-existing signals should have been dispatched.
    expected_dispatched = set(all_sigs) - set(half)
    assert set(derive_calls) == expected_dispatched, (
        f"derive should have been called only for "
        f"{expected_dispatched}, got {set(derive_calls)}"
    )
    # Final payload: every signal present (carried-forward + freshly-
    # dispatched), status=complete.
    final = json.loads(out.read_text(encoding="utf-8"))
    assert final["status"] == "complete"
    assert set(final["per_signal"].keys()) == set(all_sigs)
    # Carried-forward entries should still have their pre-existing
    # slug (untouched by the resume run).
    for s in half:
        assert final["per_signal"][s]["slug"] == f"pre_{s}", (
            f"resumed entry {s} should carry forward unchanged"
        )


def test_no_resume_forces_fresh_sweep(
    hardened_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """``--no-resume`` ignores any prior partial and dispatches
    every signal regardless. Useful when the prior partial is from
    a stale registry / different task_params."""
    base = hardened_run["base"]
    run_id = hardened_run["run_id"]
    out = tmp_path / "agg.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "status": "in_progress",
        "per_signal": {
            "burstiness_B": {
                "slug": "stale", "derived_value": 9.99,
            },
        },
        "task_surface": "calibration",
        "tool": "shard_runner",
    }))

    derive_calls: list[str] = []
    real_derive = ct.derive_threshold_from_records

    def _record(records, *, args, scoring_meta, **kwargs):
        derive_calls.append(args.signal)
        return real_derive(
            records, args=args, scoring_meta=scoring_meta, **kwargs,
        )

    monkeypatch.setattr(ct, "derive_threshold_from_records", _record)
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(out),
        "--no-resume",
    ])
    assert rc == 0
    # Every signal dispatched, including the one in the prior partial.
    assert set(derive_calls) == set(ct.COMPRESSION_HEURISTICS.keys())
    # Final payload: the stale burstiness_B entry was overwritten.
    final = json.loads(out.read_text(encoding="utf-8"))
    assert final["per_signal"]["burstiness_B"].get("slug") != "stale"


def test_resume_from_complete_is_idempotent(
    hardened_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Re-running aggregate against a 'complete' --out (with
    --resume on, the default) should be a no-op: every signal is
    already in per_signal, so dispatch_signals is empty, and the
    final state stays 'complete'."""
    base = hardened_run["base"]
    run_id = hardened_run["run_id"]
    out = tmp_path / "agg.json"

    # First run: produce a complete payload.
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(out),
    ])
    assert rc == 0
    first = json.loads(out.read_text(encoding="utf-8"))
    assert first["status"] == "complete"

    # Second run: should re-use everything.
    derive_calls: list[str] = []
    real_derive = ct.derive_threshold_from_records

    def _record(records, *, args, scoring_meta, **kwargs):
        derive_calls.append(args.signal)
        return real_derive(
            records, args=args, scoring_meta=scoring_meta, **kwargs,
        )

    monkeypatch.setattr(ct, "derive_threshold_from_records", _record)
    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(out),
    ])
    assert rc == 0
    assert derive_calls == [], (
        "re-running against a complete --out should dispatch nothing"
    )
    second = json.loads(out.read_text(encoding="utf-8"))
    assert second["status"] == "complete"
    # per_signal entries should be the same set (slugs may differ if
    # the test fixture is non-deterministic, but signal names match).
    assert set(second["per_signal"].keys()) == set(first["per_signal"].keys())


def test_resume_handles_corrupted_prior_partial(
    hardened_run, tmp_path: Path,
):
    """A garbled prior partial shouldn't break the run — the
    aggregator should log the parse failure and start fresh."""
    base = hardened_run["base"]
    run_id = hardened_run["run_id"]
    out = tmp_path / "agg.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{ this is not valid JSON !!!")

    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(out),
    ])
    assert rc == 0
    final = json.loads(out.read_text(encoding="utf-8"))
    assert final["status"] == "complete"
    # All signals dispatched (no resume happened).
    assert set(final["per_signal"].keys()) == set(
        ct.COMPRESSION_HEURISTICS.keys()
    )


def test_aggregator_perf_records_resume(
    hardened_run, tmp_path: Path,
):
    """The aggregator_perf block should record resume metadata so
    operators can audit which run did what."""
    base = hardened_run["base"]
    run_id = hardened_run["run_id"]
    out = tmp_path / "agg.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "status": "in_progress",
        "per_signal": {
            "burstiness_B": {"slug": "carried", "derived_value": 0.0},
            "mattr": {"slug": "carried", "derived_value": 0.0},
        },
        "task_surface": "calibration",
        "tool": "shard_runner",
    }))

    rc = sr.main([
        "--base-dir", str(base), "aggregate",
        "--run-id", run_id,
        "--out", str(out),
    ])
    assert rc == 0
    final = json.loads(out.read_text(encoding="utf-8"))
    perf = final["aggregator_perf"]
    assert perf["resumed_from_partial"] is True
    assert perf["resumed_signal_count"] == 2
