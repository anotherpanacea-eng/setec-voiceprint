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


# --------------- Atomic claim files (v1.44.1) -----------------


def test_try_claim_shard_atomically_first_caller_wins(tmp_path: Path):
    """The kernel's O_CREAT | O_EXCL guarantee: when two callers
    race to create the same claim file, exactly one wins."""
    claim_path = tmp_path / ".claim"
    assert ss.try_claim_shard_atomically(claim_path, host="hostA", pid=1) is True
    # Second attempt sees the file already exists.
    assert ss.try_claim_shard_atomically(claim_path, host="hostB", pid=2) is False
    # File content reflects the first winner.
    content = json.loads(claim_path.read_text(encoding="utf-8"))
    assert content["host"] == "hostA"
    assert content["pid"] == 1
    assert "claimed_at" in content


def test_try_claim_shard_atomically_after_release_succeeds(tmp_path: Path):
    """Once a claim is released, another caller can re-claim. This
    is the path the resume / re-run case relies on."""
    claim_path = tmp_path / ".claim"
    assert ss.try_claim_shard_atomically(claim_path, host="hostA", pid=1) is True
    ss.release_claim(claim_path)
    assert ss.try_claim_shard_atomically(claim_path, host="hostB", pid=2) is True
    content = json.loads(claim_path.read_text(encoding="utf-8"))
    assert content["host"] == "hostB"


def test_release_claim_is_idempotent(tmp_path: Path):
    """Releasing a claim that's already gone should not raise.
    Workers call release on every shard completion regardless of
    whether the claim file still exists (e.g., after sweep-stale
    intervened)."""
    claim_path = tmp_path / ".claim"
    # Release a non-existent claim — no error.
    ss.release_claim(claim_path)
    # Create then release twice in a row — second release no-ops.
    ss.try_claim_shard_atomically(claim_path)
    ss.release_claim(claim_path)
    ss.release_claim(claim_path)
    assert not claim_path.exists()


def test_read_claim_file_returns_metadata(tmp_path: Path):
    claim_path = tmp_path / ".claim"
    ss.try_claim_shard_atomically(claim_path, host="some-host", pid=12345)
    out = ss.read_claim_file(claim_path)
    assert out is not None
    assert out["host"] == "some-host"
    assert out["pid"] == 12345


def test_read_claim_file_returns_none_when_missing(tmp_path: Path):
    out = ss.read_claim_file(tmp_path / ".claim")
    assert out is None


def test_read_claim_file_returns_none_when_malformed(tmp_path: Path):
    """Truncated or corrupted claim files (e.g., from a worker crash
    mid-write) should be treated as absent rather than crashing the
    caller. ``sweep-stale`` will handle the cleanup in v1.44.1.B."""
    claim_path = tmp_path / ".claim"
    claim_path.write_text("{not valid json")
    assert ss.read_claim_file(claim_path) is None


def test_try_claim_shard_atomically_multiprocess_race(tmp_path: Path):
    """Spawn N subprocesses that race to claim the same shard.
    Exactly one should win; the rest see FileExistsError and
    return False. This is the load-bearing test for v1.44.1's
    multi-worker correctness — without it, two workers could end
    up scoring the same shard and producing redundant cache files.
    """
    import multiprocessing as mp

    claim_path = tmp_path / ".claim"

    def _race_worker(idx, q):  # pragma: no cover — runs in subprocess
        won = ss.try_claim_shard_atomically(
            claim_path, host=f"host-{idx}", pid=10000 + idx,
        )
        q.put((idx, won))

    ctx = mp.get_context("fork")
    q = ctx.Queue()
    n = 8
    procs = [ctx.Process(target=_race_worker, args=(i, q)) for i in range(n)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=10)
    results = []
    while not q.empty():
        results.append(q.get())
    won = [idx for idx, w in results if w]
    lost = [idx for idx, w in results if not w]
    assert len(won) == 1, (
        f"Expected exactly one winner; got {len(won)}: {won}"
    )
    assert len(lost) == n - 1


# --------------- state_update_lock (v1.44.1) ------------------


def test_state_update_lock_serializes_concurrent_writers(tmp_path: Path):
    """When multiple workers update state.json concurrently, the
    lock serializes them. Without the lock, two workers can read
    the same state, modify different shards, and overwrite each
    other's writes (the last writer wins, the other shard's update
    is lost).

    This test starts with both shards pending, runs two workers
    concurrently — each claiming its own shard — and asserts that
    both claims are visible in the final state.
    """
    import multiprocessing as mp

    # Set up a state with two pending shards.
    state = ss.build_initial_state(
        run_id="lock_test",
        source_manifest_path=Path("/tmp/src.jsonl"),
        source_manifest_sha256="abc",
        shard_count=2,
        shard_size_target=100,
        stratify_by=["register"],
        shuffle_seed=42,
        fpr_target=0.01,
        tier1=True, tier2=False, tier3=False,
        embedding_model=None, embedding_revision=None,
        shard_summaries=[
            {"n_entries": 100, "stratum_counts": {}},
            {"n_entries": 100, "stratum_counts": {}},
        ],
    )
    sp = tmp_path / "state.json"
    ss.write_state(sp, state)

    def _lock_worker(shard_id):  # pragma: no cover — subprocess
        # Hold the lock, read, modify, write. Sleep briefly inside
        # the lock to make the race-without-lock scenario reliably
        # surface — without serialization, both workers would race
        # in the read-modify-write window and one's update would
        # be lost.
        with ss.state_update_lock(sp):
            local_state = ss.read_state(sp)
            local_state = ss.claim_shard(
                local_state, shard_id, host=f"host-{shard_id}", pid=1,
            )
            import time
            time.sleep(0.05)
            ss.write_state(sp, local_state)

    ctx = mp.get_context("fork")
    p_a = ctx.Process(target=_lock_worker, args=("000",))
    p_b = ctx.Process(target=_lock_worker, args=("001",))
    p_a.start()
    p_b.start()
    p_a.join(timeout=10)
    p_b.join(timeout=10)
    # Both workers' claims must be visible in the final state.
    final = ss.read_state(sp)
    assert final["shards"]["000"]["state"] == "claimed"
    assert final["shards"]["001"]["state"] == "claimed"
    assert final["shards"]["000"]["claimed_by_host"] == "host-000"
    assert final["shards"]["001"]["claimed_by_host"] == "host-001"


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


# --------------- pid_alive (v1.44.1.B) --------------------------


def test_pid_alive_recognizes_running_process():
    """The test runner's own pid must register as alive."""
    assert ss.pid_alive(os.getpid()) is True


def test_pid_alive_recognizes_dead_pid():
    """A pid that's almost certainly not allocated should register
    as dead. Linux's default pid_max is ~4M; we pick 999_999_999
    to avoid colliding with any reasonable host's running pids.
    The same number is used in shard_runner's sweep-stale tests
    so behavior is consistent across the suite."""
    assert ss.pid_alive(999_999_999) is False


def test_pid_alive_handles_permission_error_conservatively(
    monkeypatch: pytest.MonkeyPatch,
):
    """If we hit ``PermissionError`` sending signal 0 (the process
    exists but is owned by another user), treat as alive — refusing
    to release a claim we can't conclusively prove is dead is the
    safer default. Otherwise an unprivileged ``sweep-stale`` run
    could release a perfectly-healthy worker's claim mid-shard."""

    def _raise_permission(pid, sig):
        raise PermissionError("simulated")

    monkeypatch.setattr(ss.os, "kill", _raise_permission)
    assert ss.pid_alive(1) is True


def test_pid_alive_treats_unknown_oserror_conservatively(
    monkeypatch: pytest.MonkeyPatch,
):
    """An unexpected OSError from os.kill (e.g., EINVAL because the
    kernel rejected our signal-0 call) should NOT cause sweep-stale
    to think the process is dead. Conservative path = treat as alive,
    same reasoning as PermissionError."""

    def _raise_oserror(pid, sig):
        raise OSError("simulated unexpected error")

    monkeypatch.setattr(ss.os, "kill", _raise_oserror)
    assert ss.pid_alive(1) is True
