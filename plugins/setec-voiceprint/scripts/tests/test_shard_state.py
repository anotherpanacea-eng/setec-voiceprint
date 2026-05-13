#!/usr/bin/env python3
"""Regression tests for shard_state.py.

Pins the contract `internal/SPEC_sharded_calibration.md` §2.2/§2.4
made load-bearing:

  * Atomic write: write_state(path, state) followed by an
    interrupting failure leaves either old state or new state on
    disk, never a partial file.
  * State transitions: pending -> claimed -> done is the happy
    path; pending -> claimed -> claimed_pending_resume (SIGTERM
    interrupt) -> claimed -> done is the interrupted path; both
    must be supported.
  * Invalid transitions raise ShardStateError (e.g., claiming an
    already-done shard).
  * SHA-256 helper produces stable per-file hashes.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "calibration") not in sys.path:
    sys.path.insert(0, str(ROOT / "calibration"))

import shard_state as ss  # type: ignore


# --------------- write_state / read_state -----------------------


def test_write_and_read_roundtrip(tmp_path: Path):
    state = {"foo": "bar", "shards": {"000": {"state": "pending"}}}
    sp = tmp_path / "state.json"
    ss.write_state(sp, state)
    assert sp.exists()
    out = ss.read_state(sp)
    assert out == state


def test_write_state_creates_parent_directories(tmp_path: Path):
    state = {"hello": "world"}
    sp = tmp_path / "nested" / "deep" / "state.json"
    ss.write_state(sp, state)
    assert sp.exists()


def test_write_state_atomic_no_temp_files_left_on_success(tmp_path: Path):
    """After a successful write, no .state-*.tmp files should
    remain in the target directory."""
    state = {"a": 1}
    sp = tmp_path / "state.json"
    ss.write_state(sp, state)
    leftover = list(tmp_path.glob(".state-*.tmp"))
    assert leftover == []


def test_read_state_missing_raises(tmp_path: Path):
    with pytest.raises(ss.ShardStateError):
        ss.read_state(tmp_path / "nope.json")


def test_read_state_malformed_raises(tmp_path: Path):
    sp = tmp_path / "state.json"
    sp.write_text("{not valid json")
    with pytest.raises(ss.ShardStateError):
        ss.read_state(sp)


# --------------- sha256_file ------------------------------------


def test_sha256_file_matches_hashlib(tmp_path: Path):
    p = tmp_path / "data.bin"
    p.write_bytes(b"the quick brown fox" * 100)
    expected = hashlib.sha256(b"the quick brown fox" * 100).hexdigest()
    assert ss.sha256_file(p) == expected


def test_sha256_file_streams_large_input(tmp_path: Path):
    p = tmp_path / "big.bin"
    # 5 MB of data — exercises the streaming chunk loop.
    p.write_bytes(b"x" * (5 * 1024 * 1024))
    expected = hashlib.sha256(b"x" * (5 * 1024 * 1024)).hexdigest()
    assert ss.sha256_file(p) == expected


# --------------- build_initial_state ----------------------------


def test_initial_state_has_pending_shards(tmp_path: Path):
    summaries = [
        {"n_entries": 1000, "stratum_counts": {"a|x": 500, "a|y": 500}},
        {"n_entries": 1000, "stratum_counts": {"a|x": 500, "a|y": 500}},
        {"n_entries": 1000, "stratum_counts": {"a|x": 500, "a|y": 500}},
    ]
    state = ss.build_initial_state(
        run_id="test_run",
        source_manifest_path=tmp_path / "src.jsonl",
        source_manifest_sha256="deadbeef" * 8,
        shard_count=3,
        shard_size_target=1000,
        stratify_by=["register"],
        shuffle_seed=42,
        fpr_target=0.01,
        tier1=True, tier2=False, tier3=False,
        embedding_model=None, embedding_revision=None,
        shard_summaries=summaries,
    )
    assert state["shard_count"] == 3
    assert state["schema_version"] == ss.SHARD_STATE_VERSION
    assert set(state["shards"]) == {"000", "001", "002"}
    for sid in ("000", "001", "002"):
        assert state["shards"][sid]["state"] == "pending"
        assert state["shards"][sid]["n_entries_planned"] == 1000


# --------------- State transitions ------------------------------


def _three_shard_state():
    summaries = [{"n_entries": 100, "stratum_counts": {}} for _ in range(3)]
    return ss.build_initial_state(
        run_id="run",
        source_manifest_path=Path("/tmp/src.jsonl"),
        source_manifest_sha256="abc",
        shard_count=3,
        shard_size_target=100,
        stratify_by=["register"],
        shuffle_seed=42,
        fpr_target=0.01,
        tier1=True, tier2=False, tier3=False,
        embedding_model=None, embedding_revision=None,
        shard_summaries=summaries,
    )


def test_claim_shard_transitions_pending_to_claimed():
    state = _three_shard_state()
    ss.claim_shard(state, "001", host="kestrel.local", pid=9999)
    assert state["shards"]["001"]["state"] == "claimed"
    assert state["shards"]["001"]["claimed_by_host"] == "kestrel.local"
    assert state["shards"]["001"]["claimed_by_pid"] == 9999
    assert "claimed_at" in state["shards"]["001"]


def test_claim_shard_rejects_unknown_id():
    state = _three_shard_state()
    with pytest.raises(ss.ShardStateError):
        ss.claim_shard(state, "999")


def test_claim_shard_rejects_already_claimed():
    state = _three_shard_state()
    ss.claim_shard(state, "001")
    with pytest.raises(ss.ShardStateError):
        ss.claim_shard(state, "001")  # already claimed


def test_mark_done_requires_claimed_state():
    state = _three_shard_state()
    with pytest.raises(ss.ShardStateError):
        ss.mark_done(
            state, "000",
            n_entries=100, cache_path="shards/000/cache.json",
            cache_sha256="x" * 64,
        )


def test_full_pending_claim_done_flow():
    state = _three_shard_state()
    ss.claim_shard(state, "000")
    ss.mark_done(
        state, "000",
        n_entries=100,
        cache_path="shards/000/cache.json",
        cache_sha256="x" * 64,
    )
    sh = state["shards"]["000"]
    assert sh["state"] == "done"
    assert sh["n_entries"] == 100
    assert sh["cache_path"] == "shards/000/cache.json"
    assert sh["cache_sha256"] == "x" * 64
    assert "completed_at" in sh


def test_mark_failed_records_reason():
    state = _three_shard_state()
    ss.claim_shard(state, "002")
    ss.mark_failed(state, "002", failure_reason="OOM kill")
    sh = state["shards"]["002"]
    assert sh["state"] == "failed"
    assert sh["failure_reason"] == "OOM kill"


def test_mark_pending_resume_records_progress():
    state = _three_shard_state()
    ss.claim_shard(state, "000")
    ss.mark_pending_resume(
        state, "000",
        n_entries_flushed=50, n_entries_total=100,
    )
    sh = state["shards"]["000"]
    assert sh["state"] == "claimed_pending_resume"
    assert sh["n_entries_flushed"] == 50
    assert sh["n_entries_total"] == 100
    assert "last_flush_at" in sh


def test_resumable_shard_can_be_reclaimed_via_expected_state():
    state = _three_shard_state()
    ss.claim_shard(state, "000")
    ss.mark_pending_resume(
        state, "000",
        n_entries_flushed=50, n_entries_total=100,
    )
    # Resume path passes expected_state explicitly.
    ss.claim_shard(state, "000", expected_state="claimed_pending_resume")
    assert state["shards"]["000"]["state"] == "claimed"


# --------------- Query helpers ----------------------------------


def test_pending_shard_ids_returns_only_pending():
    state = _three_shard_state()
    assert ss.pending_shard_ids(state) == ["000", "001", "002"]
    ss.claim_shard(state, "001")
    assert ss.pending_shard_ids(state) == ["000", "002"]


def test_resumable_shard_ids():
    state = _three_shard_state()
    ss.claim_shard(state, "000")
    ss.mark_pending_resume(state, "000", n_entries_flushed=10, n_entries_total=100)
    assert ss.resumable_shard_ids(state) == ["000"]


def test_status_summary():
    state = _three_shard_state()
    ss.claim_shard(state, "000")
    ss.mark_done(
        state, "000",
        n_entries=100, cache_path="shards/000/cache.json", cache_sha256="x" * 64,
    )
    summ = ss.status_summary(state)
    assert summ["counts"]["pending"] == 2
    assert summ["counts"]["done"] == 1
    assert summ["fraction_done"] == pytest.approx(1.0 / 3)


# --------------- Atomicity simulation ---------------------------


def test_write_state_does_not_corrupt_existing_on_partial_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """If os.replace raises after a temp file was written, the
    original state file (if any) must remain unchanged and the
    temp file should be cleaned up. Atomicity contract from the
    spec §2.3.
    """
    sp = tmp_path / "state.json"
    initial = {"version": 1}
    ss.write_state(sp, initial)
    # Patch os.replace to fail on the next write only.
    real_replace = os.replace
    calls = {"count": 0}

    def _flaky_replace(src, dst):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("simulated rename failure")
        return real_replace(src, dst)

    monkeypatch.setattr("os.replace", _flaky_replace)
    with pytest.raises(OSError):
        ss.write_state(sp, {"version": 2})
    # Original state preserved.
    out = ss.read_state(sp)
    assert out == initial
    # Temp file cleaned up.
    leftover = list(tmp_path.glob(".state-*.tmp"))
    assert leftover == []
