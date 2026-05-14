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


# --------------- find_git_repo / is_git_synced (v1.44.2) -------


def test_find_git_repo_returns_none_outside_repo(tmp_path: Path):
    """A directory under pytest's tmp_path (typically /tmp) is not
    in a git tree; find_git_repo should return None. This is what
    keeps the existing tests unaffected by v1.44.2's sync layer."""
    assert ss.find_git_repo(tmp_path / "state.json") is None
    assert ss.find_git_repo(tmp_path) is None


def test_find_git_repo_finds_dot_git_in_ancestor(tmp_path: Path):
    """A state.json inside a directory whose ancestor has .git/
    should be found. We simulate a git repo by just creating a
    .git directory — find_git_repo does a file-system walk, not
    a git command, so this is sufficient."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    nested = repo_root / "calibration_runs" / "test_run"
    nested.mkdir(parents=True)
    state_file = nested / "state.json"
    state_file.write_text("{}")
    found = ss.find_git_repo(state_file)
    assert found == repo_root


def test_is_git_synced_matches_find_git_repo(tmp_path: Path):
    """is_git_synced is just ``find_git_repo() is not None``."""
    assert ss.is_git_synced(tmp_path / "state.json") is False
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    nested_state = repo_root / "calibration_runs" / "x" / "state.json"
    nested_state.parent.mkdir(parents=True)
    nested_state.write_text("{}")
    assert ss.is_git_synced(nested_state) is True


# --------------- pull_state / push_state (v1.44.2) -------------


def test_pull_state_skips_when_not_in_repo(tmp_path: Path):
    """Outside a git repo, pull_state silently no-ops (returns
    False), never raises. This is what keeps the test suite
    running without touching real git."""
    sp = tmp_path / "state.json"
    sp.write_text("{}")
    assert ss.pull_state(sp) is False


def test_pull_state_skips_when_disabled(tmp_path: Path):
    """enabled=False short-circuits even inside a repo. Used by
    --no-sync-state in shard_runner."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    state_file = repo_root / "state.json"
    state_file.write_text("{}")
    assert ss.pull_state(state_file, enabled=False) is False


def test_pull_state_invokes_git_pull(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Inside a git repo with enabled=True, pull_state should call
    ``git pull --rebase``. We monkeypatch _git so the test
    observes the call shape without touching real git."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    state_file = repo_root / "state.json"
    state_file.write_text("{}")
    calls = []

    def _fake_git(repo, args, *, timeout=30.0, check=True):
        calls.append((repo, args))
        import subprocess as sp_module
        return sp_module.CompletedProcess(
            args=args, returncode=0, stdout="", stderr="",
        )

    monkeypatch.setattr(ss, "_git", _fake_git)
    result = ss.pull_state(state_file)
    assert result is True
    assert len(calls) == 1
    repo, args = calls[0]
    assert repo == repo_root
    assert args[:3] == ["pull", "--rebase", "--quiet"]


def test_pull_state_raises_sync_error_on_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """A ``git pull --rebase`` that hits a CONFLICT must raise
    SyncError with a message pointing the operator at
    resolve-conflict."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    state_file = repo_root / "state.json"
    state_file.write_text("{}")
    import subprocess as sp_module

    def _conflict_git(repo, args, *, timeout=30.0, check=True):
        raise sp_module.CalledProcessError(
            returncode=1, cmd=["git"] + args,
            stderr="CONFLICT (content): Merge conflict in state.json",
        )

    monkeypatch.setattr(ss, "_git", _conflict_git)
    with pytest.raises(ss.SyncError, match="resolve-conflict"):
        ss.pull_state(state_file)


def test_push_state_invokes_add_commit_push_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """push_state should run, in order: git add, git diff --cached
    (the v1.49.0+ staged-changes check that distinguishes
    nothing-to-commit from real commit failure), git commit,
    git push."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    state_file = repo_root / "state.json"
    state_file.write_text("{}")
    calls = []

    def _fake_git(repo, args, *, timeout=30.0, check=True):
        calls.append(args)
        import subprocess as sp_module
        # `git diff --cached --quiet`: rc=1 means "changes are
        # staged" (the case this test exercises). All other
        # commands succeed with rc=0.
        if args[:3] == ["diff", "--cached", "--quiet"]:
            rc = 1
        else:
            rc = 0
        return sp_module.CompletedProcess(
            args=args, returncode=rc, stdout="", stderr="",
        )

    monkeypatch.setattr(ss, "_git", _fake_git)
    result = ss.push_state(state_file, message="test commit")
    assert result is True
    # Expected git invocations: add, diff --cached, commit, push.
    op_sequence = [c[0] for c in calls]
    assert op_sequence == ["add", "diff", "commit", "push"]
    assert "test commit" in calls[2]  # commit message arg


def test_push_state_returns_false_on_nothing_to_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """When nothing is staged (``git diff --cached --quiet`` rc=0),
    push_state should return False without attempting commit or
    push. The v1.49.0+ implementation detects this case BEFORE
    calling commit so genuine commit failures (missing user
    config, hook rejection) can be distinguished from no-ops."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    state_file = repo_root / "state.json"
    state_file.write_text("{}")
    calls = []

    def _fake_git(repo, args, *, timeout=30.0, check=True):
        calls.append(args)
        import subprocess as sp_module
        # `git diff --cached --quiet` rc=0 → nothing staged.
        return sp_module.CompletedProcess(
            args=args, returncode=0, stdout="", stderr="",
        )

    monkeypatch.setattr(ss, "_git", _fake_git)
    result = ss.push_state(state_file, message="test")
    assert result is False
    # Neither commit nor push should be attempted.
    assert not any(c[0] == "commit" for c in calls)
    assert not any(c[0] == "push" for c in calls)


def test_push_state_raises_sync_error_on_commit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Reviewer P2 (2026-05-14): a genuine ``git commit`` failure
    (missing user.name/user.email, pre-commit hook rejection,
    index corruption, repo mid-rebase) must raise ``SyncError`` —
    NOT be silently collapsed into ``return False`` along with
    the nothing-to-commit case. Pre-fix, this kind of failure
    looked like a benign no-op and silently left state
    transitions local-only on a multi-machine run.

    We exercise this by:
      * making ``git diff --cached --quiet`` exit 1 (i.e.,
        something IS staged), so the commit will be attempted;
      * making ``git commit`` raise CalledProcessError with the
        canonical "missing user.email" stderr message.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    state_file = repo_root / "state.json"
    state_file.write_text("{}")
    calls = []
    import subprocess as sp_module

    def _fake_git(repo, args, *, timeout=30.0, check=True):
        calls.append(args)
        if args[:3] == ["diff", "--cached", "--quiet"]:
            # Changes ARE staged.
            return sp_module.CompletedProcess(
                args=args, returncode=1, stdout="", stderr="",
            )
        if args[0] == "commit":
            raise sp_module.CalledProcessError(
                returncode=128, cmd=["git"] + args,
                stderr=(
                    "Author identity unknown\n\n"
                    "*** Please tell me who you are.\n\n"
                    "Run\n\n"
                    "  git config --global user.email "
                    '"you@example.com"\n'
                ),
            )
        return sp_module.CompletedProcess(
            args=args, returncode=0, stdout="", stderr="",
        )

    monkeypatch.setattr(ss, "_git", _fake_git)
    with pytest.raises(ss.SyncError) as excinfo:
        ss.push_state(state_file, message="hostA shard 000 claim")
    # Error message mentions the user.name/user.email cause AND
    # tells the operator how to inspect.
    msg = str(excinfo.value)
    assert "git commit failed" in msg
    assert "user.name" in msg or "user.email" in msg
    assert "git -C" in msg and "status" in msg
    # Push must NOT have been attempted after a commit failure —
    # pushing a non-existent commit would be either a no-op or
    # an even more confusing failure mode.
    assert not any(c[0] == "push" for c in calls)


def test_push_state_raises_sync_error_on_pre_commit_hook_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """A pre-commit hook rejection looks like a commit failure
    too. Same path: distinguish from no-op, surface as SyncError."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    state_file = repo_root / "state.json"
    state_file.write_text("{}")
    import subprocess as sp_module

    def _fake_git(repo, args, *, timeout=30.0, check=True):
        if args[:3] == ["diff", "--cached", "--quiet"]:
            return sp_module.CompletedProcess(
                args=args, returncode=1, stdout="", stderr="",
            )
        if args[0] == "commit":
            raise sp_module.CalledProcessError(
                returncode=1, cmd=["git"] + args,
                stderr=(
                    ".git/hooks/pre-commit: line 3: forbidden\n"
                ),
            )
        return sp_module.CompletedProcess(
            args=args, returncode=0, stdout="", stderr="",
        )

    monkeypatch.setattr(ss, "_git", _fake_git)
    with pytest.raises(ss.SyncError, match="git commit failed"):
        ss.push_state(state_file, message="m")


def test_push_state_retries_on_push_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """If push fails because another machine pushed first
    (non-fast-forward), pull + retry. After the rebase, the
    second push should succeed."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    state_file = repo_root / "state.json"
    state_file.write_text("{}")
    calls = []
    push_attempts = {"n": 0}

    def _fake_git(repo, args, *, timeout=30.0, check=True):
        calls.append(args)
        import subprocess as sp_module
        # v1.49.0+: `git diff --cached --quiet` rc=1 means "staged
        # changes exist" → proceed to commit. rc=0 would mean
        # "nothing staged" and short-circuit before commit.
        if args[:3] == ["diff", "--cached", "--quiet"]:
            return sp_module.CompletedProcess(
                args=args, returncode=1, stdout="", stderr="",
            )
        if args[0] == "push":
            push_attempts["n"] += 1
            if push_attempts["n"] == 1:
                raise sp_module.CalledProcessError(
                    returncode=1, cmd=["git"] + args,
                    stderr="! [rejected] main -> main (non-fast-forward)",
                )
        return sp_module.CompletedProcess(
            args=args, returncode=0, stdout="", stderr="",
        )

    monkeypatch.setattr(ss, "_git", _fake_git)
    result = ss.push_state(state_file, message="m")
    assert result is True
    push_calls = [c for c in calls if c[0] == "push"]
    pull_calls = [c for c in calls if c[0] == "pull"]
    assert len(push_calls) == 2
    assert len(pull_calls) == 1


# --------------- merge_state_files (v1.44.2) -------------------


def _shard(state="pending", **extra):
    """Helper: build a shard dict in the canonical shape."""
    return {"state": state, **extra}


def test_merge_state_files_identical_no_op():
    """When ours == theirs == base, the merge is a no-op."""
    base = {"shards": {"000": _shard("pending")}, "run_id": "x"}
    merged, unresolved = ss.merge_state_files(base, base, base)
    assert merged["shards"] == base["shards"]
    assert unresolved == []


def test_merge_state_files_only_ours_changed():
    """If only our side advanced a shard, take ours."""
    base = {"shards": {"000": _shard("pending"), "001": _shard("pending")}}
    ours = {"shards": {"000": _shard("done", n_entries=10), "001": _shard("pending")}}
    theirs = base
    merged, unresolved = ss.merge_state_files(base, ours, theirs)
    assert merged["shards"]["000"]["state"] == "done"
    assert merged["shards"]["001"]["state"] == "pending"
    assert unresolved == []


def test_merge_state_files_only_theirs_changed():
    """If only their side advanced a shard, take theirs."""
    base = {"shards": {"000": _shard("pending"), "001": _shard("pending")}}
    ours = base
    theirs = {"shards": {"000": _shard("pending"), "001": _shard("done", n_entries=5)}}
    merged, unresolved = ss.merge_state_files(base, ours, theirs)
    assert merged["shards"]["001"]["state"] == "done"
    assert unresolved == []


def test_merge_state_files_disjoint_changes():
    """Different shards changed by different sides → both changes
    preserved (the trivial-merge case per spec §4.6: "different
    shards by different machines produce disjoint diffs and merge
    trivially")."""
    base = {"shards": {"000": _shard("pending"), "001": _shard("pending")}}
    ours = {"shards": {"000": _shard("done"), "001": _shard("pending")}}
    theirs = {"shards": {"000": _shard("pending"), "001": _shard("done")}}
    merged, unresolved = ss.merge_state_files(base, ours, theirs)
    assert merged["shards"]["000"]["state"] == "done"
    assert merged["shards"]["001"]["state"] == "done"
    assert unresolved == []


def test_merge_state_files_takes_more_advanced_state():
    """Both sides modified the same shard but to different states:
    take the more advanced one (done > claimed_pending_resume >
    claimed > pending)."""
    base = {"shards": {"000": _shard("pending")}}
    ours = {"shards": {"000": _shard("claimed", claimed_by_host="A")}}
    theirs = {"shards": {"000": _shard("done", n_entries=100)}}
    merged, unresolved = ss.merge_state_files(base, ours, theirs)
    assert merged["shards"]["000"]["state"] == "done"
    assert unresolved == []


def test_merge_state_files_same_shard_concurrent_claim_unresolved():
    """Both sides claimed the same shard from different hosts at
    the same rank → unresolved. This is the canonical "should not
    happen" case per spec §4.6, but the helper still has to
    handle it gracefully and signal to the operator."""
    base = {"shards": {"000": _shard("pending")}}
    ours = {"shards": {"000": _shard(
        "claimed", claimed_by_host="hostA", claimed_by_pid=1,
        claimed_at="2026-05-13T01:00:00+00:00",
    )}}
    theirs = {"shards": {"000": _shard(
        "claimed", claimed_by_host="hostB", claimed_by_pid=2,
        claimed_at="2026-05-13T01:00:01+00:00",
    )}}
    merged, unresolved = ss.merge_state_files(base, ours, theirs)
    assert "000" in unresolved
    # Placeholder for callers that want to write something out.
    assert merged["shards"]["000"]["claimed_by_host"] == "hostA"


def test_merge_state_files_same_host_different_pid_picks_newer():
    """Two pids on the same host racing the same shard is a sane
    case (e.g., worker crashed + restarted). Pick the more recent
    timestamp; no unresolved entry needed."""
    base = {"shards": {"000": _shard("pending")}}
    ours = {"shards": {"000": _shard(
        "claimed", claimed_by_host="hostA", claimed_by_pid=1,
        claimed_at="2026-05-13T01:00:00+00:00",
    )}}
    theirs = {"shards": {"000": _shard(
        "claimed", claimed_by_host="hostA", claimed_by_pid=2,
        claimed_at="2026-05-13T02:00:00+00:00",
    )}}
    merged, unresolved = ss.merge_state_files(base, ours, theirs)
    assert unresolved == []
    assert merged["shards"]["000"]["claimed_by_pid"] == 2


def test_merge_state_files_handles_new_shards_only_on_one_side():
    """If a shard exists only in ours OR only in theirs (rare —
    shard_count is fixed at shard time — but the helper should
    still handle it gracefully)."""
    base = {"shards": {"000": _shard("pending")}}
    ours = {"shards": {"000": _shard("pending"), "001": _shard("done")}}
    theirs = {"shards": {"000": _shard("pending")}}
    merged, unresolved = ss.merge_state_files(base, ours, theirs)
    assert "001" in merged["shards"]
    assert merged["shards"]["001"]["state"] == "done"
    assert unresolved == []


# ---------- Codex PR #27 review P0: failed-is-terminal ----------
#
# Codex flagged that the pre-fix state-rank table put ``failed``
# below ``pending``, so a remote-side ``pending`` / ``claimed`` /
# ``claimed_pending_resume`` could silently overwrite a local
# ``failed`` shard during the merge — resurrecting a failed shard
# without any operator action and without the failure ever being
# recorded in the merged state. The fix treats ``failed`` as
# terminal unless the other side recorded ``done`` (the only state
# that legitimately overrides ``failed`` because it means the other
# host genuinely re-ran the shard and it succeeded).


class TestFailedIsTerminalUnlessDone:
    """``failed`` must persist through merges unless the other side
    is ``done``. Anything else (pending, claimed,
    claimed_pending_resume) yields to ``failed``."""

    def test_failed_vs_pending_keeps_failed(self):
        """Reviewer reproducer: a remote ``pending`` (e.g., from
        sweep-stale that released a stale claim) must NOT overwrite
        our ``failed``."""
        base = {"shards": {"000": _shard("claimed")}}
        ours = {"shards": {"000": _shard(
            "failed", failed_at="2026-05-14T01:00:00+00:00",
            failure_reason="OOM kill",
        )}}
        theirs = {"shards": {"000": _shard("pending")}}
        merged, unresolved = ss.merge_state_files(
            base, ours, theirs,
        )
        assert merged["shards"]["000"]["state"] == "failed", (
            "failed must not be overwritten by pending — the "
            "failure was a real signal and must persist."
        )
        assert (
            merged["shards"]["000"].get("failure_reason")
            == "OOM kill"
        )
        assert unresolved == []

    def test_failed_vs_claimed_keeps_failed(self):
        """A remote ``claimed`` (another host took the shard before
        seeing our failure) must NOT overwrite ``failed``."""
        base = {"shards": {"000": _shard("pending")}}
        ours = {"shards": {"000": _shard(
            "failed", failure_reason="scorer crashed",
        )}}
        theirs = {"shards": {"000": _shard(
            "claimed", claimed_by_host="hostB", claimed_by_pid=42,
        )}}
        merged, unresolved = ss.merge_state_files(
            base, ours, theirs,
        )
        assert merged["shards"]["000"]["state"] == "failed"
        assert unresolved == []

    def test_failed_vs_claimed_pending_resume_keeps_failed(self):
        """A remote ``claimed_pending_resume`` (mid-shard SIGTERM
        on the other host) must NOT overwrite ``failed`` — the
        local failure record outranks an in-flight resume claim."""
        base = {"shards": {"000": _shard("pending")}}
        ours = {"shards": {"000": _shard("failed")}}
        theirs = {"shards": {"000": _shard(
            "claimed_pending_resume",
            claimed_by_host="hostB",
            n_entries_flushed=50,
        )}}
        merged, unresolved = ss.merge_state_files(
            base, ours, theirs,
        )
        assert merged["shards"]["000"]["state"] == "failed"
        assert unresolved == []

    def test_failed_vs_done_done_wins(self):
        """The one exception: a remote ``done`` legitimately
        overrides ``failed`` — another host re-ran the shard and it
        succeeded. The merged result records the success."""
        base = {"shards": {"000": _shard("pending")}}
        ours = {"shards": {"000": _shard("failed")}}
        theirs = {"shards": {"000": _shard(
            "done", n_entries=100, completed_at="…",
        )}}
        merged, unresolved = ss.merge_state_files(
            base, ours, theirs,
        )
        assert merged["shards"]["000"]["state"] == "done", (
            "done is the only state that overrides failed — "
            "another host genuinely re-ran the shard."
        )
        assert merged["shards"]["000"].get("n_entries") == 100
        assert unresolved == []

    def test_symmetric_failed_on_theirs_against_pending_ours(self):
        """Symmetric to the first test: ``failed`` on theirs must
        persist when ours is ``pending`` (e.g., we never observed
        the failure because the local claim file was swept)."""
        base = {"shards": {"000": _shard("claimed")}}
        ours = {"shards": {"000": _shard("pending")}}
        theirs = {"shards": {"000": _shard(
            "failed", failure_reason="kernel panic",
        )}}
        merged, unresolved = ss.merge_state_files(
            base, ours, theirs,
        )
        assert merged["shards"]["000"]["state"] == "failed"
        assert (
            merged["shards"]["000"].get("failure_reason")
            == "kernel panic"
        )
        assert unresolved == []

    def test_symmetric_ours_done_overrides_theirs_failed(self):
        """Symmetric to the done-wins test: ``done`` on ours must
        override ``failed`` on theirs."""
        base = {"shards": {"000": _shard("pending")}}
        ours = {"shards": {"000": _shard("done", n_entries=100)}}
        theirs = {"shards": {"000": _shard("failed")}}
        merged, unresolved = ss.merge_state_files(
            base, ours, theirs,
        )
        assert merged["shards"]["000"]["state"] == "done"
        assert unresolved == []

    def test_both_failed_picks_ours_no_unresolved(self):
        """Both sides failed (same shard failed independently). The
        merge picks ours arbitrarily (the failure records are likely
        identical or near-identical); no unresolved entry is needed
        because both sides agree the shard is terminally failed."""
        base = {"shards": {"000": _shard("claimed")}}
        ours = {"shards": {"000": _shard(
            "failed", failure_reason="ours: OOM",
        )}}
        theirs = {"shards": {"000": _shard(
            "failed", failure_reason="theirs: OOM",
        )}}
        merged, unresolved = ss.merge_state_files(
            base, ours, theirs,
        )
        assert merged["shards"]["000"]["state"] == "failed"
        assert unresolved == []


# ---------- Reviewer P2 fixes (2026-05-14) ----------


class TestPushStateRetryAfterRebase:
    """Reviewer P2: after a non-fast-forward rejection,
    ``push_state`` used to run ``add`` + ``commit`` again in the
    retry loop. The local transition commit was already created on
    attempt 1 and rebased on top of the pulled-in remote commit,
    so attempt 2's ``commit`` exited rc=1 ("nothing to commit")
    and the function returned False — leaving the local branch
    ahead by one commit but the remote untouched.

    Fix: commit happens once outside the retry loop; the retry
    loop only re-pushes (with intermediate ``pull --rebase`` on
    non-fast-forward). Below we monkeypatch ``_git`` to simulate
    the race and assert the second push actually runs."""

    def _make_fake_git(self, push_outcomes):
        """Build a fake _git that records call args and pops a
        prescribed push outcome from ``push_outcomes`` each time
        push is invoked. Other commands always succeed.

        ``git diff --cached --quiet`` reports rc=1 (staged changes
        present) so the v1.49.0+ commit-or-noop distinction
        proceeds to commit."""
        calls: list[tuple[str, ...]] = []
        remaining = list(push_outcomes)
        import subprocess as _sp

        def _fake(repo, args, *, timeout=30.0, check=True):
            calls.append(tuple(args))
            cmd = args[0]
            # The post-2026-05-14 commit-or-no-op check:
            if args[:3] == ["diff", "--cached", "--quiet"]:
                return _sp.CompletedProcess(
                    args=args, returncode=1, stdout="", stderr="",
                )
            if cmd == "push":
                outcome = remaining.pop(0) if remaining else "ok"
                if outcome == "rejected":
                    raise _sp.CalledProcessError(
                        returncode=1, cmd=["git"] + args,
                        stderr=(
                            "To origin\n ! [rejected] main -> main "
                            "(non-fast-forward)\n"
                        ),
                    )
            return _sp.CompletedProcess(
                args=args, returncode=0, stdout="", stderr="",
            )

        return calls, _fake

    def test_push_after_rebase_actually_pushes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """Reproducer for the reviewer's bug. Pre-fix this test
        would fail because the loop returned False before the
        second push.
        """
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / ".git").mkdir()
        state_file = repo_root / "state.json"
        state_file.write_text("{}")
        calls, fake = self._make_fake_git(["rejected", "ok"])
        monkeypatch.setattr(ss, "_git", fake)
        result = ss.push_state(state_file, message="hostA shard 000 claim")
        assert result is True
        # Expected sequence:
        #   add → commit → push(rejected) → pull --rebase → push(ok)
        # Buggy version would have been:
        #   add → commit → push(rejected) → pull --rebase → add →
        #   commit(rc=1 nothing-to-commit) → return False.
        # Load-bearing assertions: exactly ONE commit, exactly
        # TWO pushes, ONE pull --rebase.
        commit_calls = [c for c in calls if c[0] == "commit"]
        push_calls = [c for c in calls if c[0] == "push"]
        pull_calls = [c for c in calls if c[0] == "pull"]
        assert len(commit_calls) == 1, (
            f"commit ran {len(commit_calls)} times; expected 1. "
            f"Trace: {calls}"
        )
        assert len(push_calls) == 2, (
            f"push ran {len(push_calls)} times; expected 2. "
            f"Trace: {calls}"
        )
        assert len(pull_calls) == 1, (
            f"pull --rebase ran {len(pull_calls)} times; "
            f"expected 1. Trace: {calls}"
        )

    def test_clean_push_runs_one_commit_one_push(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """No race: add → commit → push (ok). One of each."""
        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / ".git").mkdir()
        state_file = repo_root / "state.json"
        state_file.write_text("{}")
        calls, fake = self._make_fake_git(["ok"])
        monkeypatch.setattr(ss, "_git", fake)
        result = ss.push_state(state_file, message="m")
        assert result is True
        commit_calls = [c for c in calls if c[0] == "commit"]
        push_calls = [c for c in calls if c[0] == "push"]
        assert len(commit_calls) == 1
        assert len(push_calls) == 1


class TestClaimFileStatus:
    """Reviewer P2: distinguish missing-vs-malformed claim files so
    sweep-stale can warn + release malformed ones after the stale
    threshold (instead of leaving the shard blocked forever)."""

    def test_missing_returns_missing_sentinel(self, tmp_path: Path):
        assert (
            ss.claim_file_status(tmp_path / ".claim")
            == ss.CLAIM_STATUS_MISSING
        )

    def test_valid_dict_returns_valid_sentinel(self, tmp_path: Path):
        cp = tmp_path / ".claim"
        cp.write_text(
            json.dumps({
                "host": "hostA", "pid": 123,
                "claimed_at": "2026-05-14T00:00:00+00:00",
            }),
            encoding="utf-8",
        )
        assert ss.claim_file_status(cp) == ss.CLAIM_STATUS_VALID

    def test_zero_byte_file_returns_malformed_sentinel(self, tmp_path: Path):
        """The canonical "crash after O_CREAT|O_EXCL but before
        json.dump" failure mode: a zero-byte .claim file. Pre-fix
        this would collapse into ``None`` and sweep-stale would
        skip the shard forever."""
        cp = tmp_path / ".claim"
        cp.write_text("")
        assert ss.claim_file_status(cp) == ss.CLAIM_STATUS_MALFORMED

    def test_truncated_json_returns_malformed_sentinel(self, tmp_path: Path):
        """A partial-write that ended mid-string: also malformed."""
        cp = tmp_path / ".claim"
        cp.write_text('{"host": "hostA", "pid":')
        assert ss.claim_file_status(cp) == ss.CLAIM_STATUS_MALFORMED

    def test_json_list_returns_malformed_sentinel(self, tmp_path: Path):
        """JSON-valid but not the expected dict shape. Treat as
        malformed since downstream code reads host/pid/claimed_at
        as dict keys."""
        cp = tmp_path / ".claim"
        cp.write_text("[1, 2, 3]")
        assert ss.claim_file_status(cp) == ss.CLAIM_STATUS_MALFORMED

    def test_json_scalar_returns_malformed_sentinel(self, tmp_path: Path):
        cp = tmp_path / ".claim"
        cp.write_text("\"just-a-string\"")
        assert ss.claim_file_status(cp) == ss.CLAIM_STATUS_MALFORMED

    def test_whitespace_only_returns_malformed_sentinel(self, tmp_path: Path):
        cp = tmp_path / ".claim"
        cp.write_text("   \n\t ")
        assert ss.claim_file_status(cp) == ss.CLAIM_STATUS_MALFORMED

    def test_read_claim_file_returns_none_for_non_dict(
        self, tmp_path: Path,
    ):
        """The existing read_claim_file API (dict | None) is
        preserved: non-dict JSON now also returns None, consistent
        with the missing/malformed collapse contract. This is a
        strictness improvement — previously a list-shape claim
        file would have leaked through as a list, which downstream
        sweep-stale couldn't use."""
        cp = tmp_path / ".claim"
        cp.write_text("[1, 2, 3]")
        assert ss.read_claim_file(cp) is None
