#!/usr/bin/env python3
"""shard_state.py — state.json read/write for sharded calibration.

Each sharded run owns one ``state.json`` file (under
``calibration_runs/<run_id>/``) that records:

  * Run metadata: source manifest hash, shard count, stratification
    spec, embedding model + revision, ROCm/PyTorch versions.
  * Per-shard state: ``pending`` / ``claimed`` /
    ``claimed_pending_resume`` / ``done`` / ``failed``, plus
    timestamps, claiming host + PID, and cache-file SHA-256 on
    completion.

The file is the framework's coordination point for multi-worker
runs (even within a single machine — see v1.44.1 for the
``--workers N`` concurrent path, and v1.44.2 for the multi-machine
git-synced path). v1.44.0 ships single-worker only; the state file
is still load-bearing because SIGTERM checkpointing depends on
durable per-shard state.

Atomicity contract: every write goes through ``write_state`` which
writes to a temp file in the same directory and renames over the
target. POSIX rename is atomic, so a crash during write leaves
either the old state or the new state — never a partial file. The
``claim_shard`` and ``mark_done`` operations are
read-modify-write; concurrent claims are not race-free in v1.44.0
because we only support single-worker. v1.44.1's ``--workers N``
will add optimistic-locking (re-read before write, retry on
conflict).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import socket
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SHARD_STATE_VERSION = "1.0"

VALID_SHARD_STATES = frozenset((
    "pending",
    "claimed",
    "claimed_pending_resume",
    "done",
    "failed",
))


class ShardStateError(RuntimeError):
    """Raised when state-file operations encounter an invariant
    violation (unknown shard id, invalid state transition, hash
    mismatch on verify, etc.). Typed so the CLI can catch it
    separately from generic IO errors."""


# --------------- File-level read / write ------------------------


def write_state(state_path: Path, state: dict[str, Any]) -> None:
    """Atomically write ``state`` as pretty-printed JSON to
    ``state_path``.

    Uses ``NamedTemporaryFile`` in the same directory as the target
    so the rename is on the same filesystem (cross-fs rename is not
    atomic). ``os.replace`` works on POSIX and Windows; on POSIX
    it's atomic by spec, on Windows it's atomic for "well-behaved"
    filesystems (NTFS in particular). The sharded-calibration spec
    targets WSL2 + ext4 for the calibration host, which is fully
    POSIX.
    """
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file in the same directory, then rename.
    fd, tmp_name = tempfile.mkstemp(
        prefix=".state-",
        suffix=".tmp",
        dir=str(state_path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
            fh.write("\n")
            # fsync to be sure the bytes are on disk before rename.
            # On most filesystems this is overkill, but it cheaply
            # closes the "rename succeeded but file content is
            # zero" failure mode some filesystems exhibit under
            # crash.
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, state_path)
    except Exception:
        # Best-effort cleanup of the temp file on error.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_state(state_path: Path) -> dict[str, Any]:
    """Read and JSON-decode a state file. Raises ``ShardStateError``
    if the file is missing or malformed."""
    state_path = Path(state_path)
    if not state_path.exists():
        raise ShardStateError(f"State file not found: {state_path}")
    try:
        with state_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ShardStateError(
            f"Failed to read state file {state_path}: {exc}"
        ) from exc


# --------------- File hashing helper ----------------------------


def sha256_file(path: Path, *, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of a file's contents, streamed. Used for both source-
    manifest hashes (in initial state.json) and per-shard cache
    hashes (recorded on shard completion, verified during the
    ``verify`` subcommand)."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------- State construction ----------------------------


def build_initial_state(
    *,
    run_id: str,
    source_manifest_path: Path,
    source_manifest_sha256: str,
    shard_count: int,
    shard_size_target: int,
    stratify_by: list[str],
    shuffle_seed: int,
    fpr_target: float,
    tier1: bool,
    tier2: bool,
    tier3: bool,
    embedding_model: str | None,
    embedding_revision: str | None,
    shard_summaries: list[dict[str, Any]],
    created_at: str | None = None,
) -> dict[str, Any]:
    """Compose the initial state dict before the first write.

    All shard entries start in ``pending`` state. ``shard_summaries``
    must be aligned with shard indices 0..N-1 and contain per-shard
    ``n_entries`` and ``stratum_counts`` from
    ``sharding.shard_summary``.
    """
    created_at = created_at or _dt.datetime.now(
        _dt.timezone.utc,
    ).isoformat(timespec="seconds")
    return {
        "schema_version": SHARD_STATE_VERSION,
        "run_id": run_id,
        "source_manifest_path": str(source_manifest_path),
        "source_manifest_sha256": source_manifest_sha256,
        "shard_count": shard_count,
        "shard_size_target": shard_size_target,
        "stratify_by": list(stratify_by),
        "shuffle_seed": shuffle_seed,
        "fpr_target": fpr_target,
        "tier1": tier1,
        "tier2": tier2,
        "tier3": tier3,
        "embedding_model": embedding_model,
        "embedding_revision": embedding_revision,
        "created_at": created_at,
        "shards": {
            _shard_id(i): {
                "state": "pending",
                "n_entries_planned": shard_summaries[i].get("n_entries", 0),
                "stratum_counts": shard_summaries[i].get("stratum_counts", {}),
            }
            for i in range(shard_count)
        },
    }


def _shard_id(index: int) -> str:
    """Three-digit zero-padded shard id. Matches the directory layout
    ``shards/000/manifest.jsonl``."""
    return f"{index:03d}"


# --------------- State transitions ----------------------------


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _host() -> str:
    """Best-effort hostname for claim attribution."""
    try:
        return socket.gethostname()
    except OSError:
        return "unknown-host"


def claim_shard(
    state: dict[str, Any],
    shard_id: str,
    *,
    host: str | None = None,
    pid: int | None = None,
    expected_state: str = "pending",
) -> dict[str, Any]:
    """Transition a shard to ``claimed``.

    Reads the current state of the shard, verifies it matches
    ``expected_state`` (default ``pending``; the resume path passes
    ``claimed_pending_resume``), and writes the claim metadata.
    Returns the **updated** state dict (mutates in place but the
    return makes test assertions easier).

    Raises ``ShardStateError`` for unknown shard ids or invalid
    transitions. The CLI catches and surfaces the error cleanly.
    """
    if shard_id not in state.get("shards", {}):
        raise ShardStateError(f"Unknown shard id: {shard_id!r}")
    shard = state["shards"][shard_id]
    current = shard.get("state", "pending")
    if current != expected_state:
        raise ShardStateError(
            f"Cannot claim shard {shard_id} in state {current!r}; "
            f"expected {expected_state!r}"
        )
    shard["state"] = "claimed"
    shard["claimed_by_host"] = host or _host()
    shard["claimed_by_pid"] = pid if pid is not None else os.getpid()
    shard["claimed_at"] = _now_iso()
    return state


def mark_done(
    state: dict[str, Any],
    shard_id: str,
    *,
    n_entries: int,
    cache_path: str,
    cache_sha256: str,
) -> dict[str, Any]:
    """Transition a shard from ``claimed`` to ``done`` with the
    final cache metadata."""
    if shard_id not in state.get("shards", {}):
        raise ShardStateError(f"Unknown shard id: {shard_id!r}")
    shard = state["shards"][shard_id]
    if shard.get("state") not in ("claimed", "claimed_pending_resume"):
        raise ShardStateError(
            f"Cannot mark shard {shard_id} done in state "
            f"{shard.get('state')!r}; must be claimed or resuming"
        )
    shard["state"] = "done"
    shard["completed_at"] = _now_iso()
    shard["n_entries"] = n_entries
    shard["cache_path"] = cache_path
    shard["cache_sha256"] = cache_sha256
    return state


def mark_failed(
    state: dict[str, Any],
    shard_id: str,
    *,
    failure_reason: str,
) -> dict[str, Any]:
    """Transition a shard to ``failed`` with a reason string. Failed
    shards survive across runs so a maintainer can inspect the
    failure rather than have it silently re-claimed."""
    if shard_id not in state.get("shards", {}):
        raise ShardStateError(f"Unknown shard id: {shard_id!r}")
    shard = state["shards"][shard_id]
    shard["state"] = "failed"
    shard["failed_at"] = _now_iso()
    shard["failure_reason"] = failure_reason
    return state


def mark_pending_resume(
    state: dict[str, Any],
    shard_id: str,
    *,
    n_entries_flushed: int,
    n_entries_total: int,
    last_flush_at: str | None = None,
) -> dict[str, Any]:
    """Transition a shard to ``claimed_pending_resume``. Called by
    the SIGTERM handler when the worker is interrupted mid-shard."""
    if shard_id not in state.get("shards", {}):
        raise ShardStateError(f"Unknown shard id: {shard_id!r}")
    shard = state["shards"][shard_id]
    if shard.get("state") != "claimed":
        raise ShardStateError(
            f"Cannot mark shard {shard_id} for resume from state "
            f"{shard.get('state')!r}; must currently be claimed"
        )
    shard["state"] = "claimed_pending_resume"
    shard["n_entries_flushed"] = n_entries_flushed
    shard["n_entries_total"] = n_entries_total
    shard["last_flush_at"] = last_flush_at or _now_iso()
    return state


# --------------- Query helpers --------------------------------


def pending_shard_ids(state: dict[str, Any]) -> list[str]:
    """Return shard ids in ``pending`` state, in sorted order."""
    return sorted(
        sid for sid, shard in state.get("shards", {}).items()
        if shard.get("state") == "pending"
    )


def resumable_shard_ids(state: dict[str, Any]) -> list[str]:
    """Shards in ``claimed_pending_resume`` state. Per the spec
    §2.4, these are claimed by their original worker and must be
    resumed by that worker (or have their claim manually released
    via `sweep-stale`)."""
    return sorted(
        sid for sid, shard in state.get("shards", {}).items()
        if shard.get("state") == "claimed_pending_resume"
    )


# --------------- Atomic claim files (v1.44.1) -----------------
#
# Multi-worker coordination uses per-shard claim files at
# ``shards/<id>/.claim``. Workers create these atomically via
# ``O_CREAT | O_EXCL | O_WRONLY``; the kernel guarantees that only
# one process wins the race when multiple workers target the same
# shard simultaneously. The claim file's content is JSON with the
# winning worker's host, pid, and timestamp — readable by
# ``sweep-stale`` (v1.44.1.B) to identify dead-worker claims.
#
# The claim file is the load-bearing coordination point. state.json
# updates still happen, but they reflect what the claim file
# established, not the other way around. This decouples "who owns
# the work" from "what the run looks like in aggregate," letting
# state.json updates be eventually-consistent across workers
# (serialized via the state-update lock below) without sacrificing
# claim correctness.


def process_start_time_epoch(pid: int) -> float | None:
    """Return the start time (epoch seconds) of the process with
    the given pid, or ``None`` if it can't be determined.

    Reviewer P2 (2026-05-14): used by ``terminate-all`` /
    ``kill-all`` to verify the process the framework is about to
    signal is the same process that originally claimed the shard,
    not an unrelated process that happened to inherit a reused PID.
    Without this check, a stale ``.claim`` file pointing at a PID
    the OS recycled into a different program could make
    ``terminate-all`` SIGTERM that unrelated program.

    Implementation: ``ps -o lstart= -p PID``. The ``lstart`` column
    is the same human-readable BSD-style timestamp on macOS and
    Linux ("Wed Oct  1 14:30:00 2025"), and Python's strptime with
    ``%a %b %d %H:%M:%S %Y`` parses both. We normalize whitespace
    first so single-digit days padded with a leading space parse
    cleanly. Returns ``None`` if:

      * ``ps`` is missing (unlikely on POSIX; would break the
        framework's reliance on POSIX shell tools more broadly).
      * The PID doesn't exist (process gone).
      * The timestamp doesn't parse (locale / ps-format mismatch).

    Treat ``None`` as "couldn't verify identity"; callers should
    conservatively skip the signal rather than send blindly.
    """
    import subprocess as _sp
    try:
        result = _sp.run(
            ["ps", "-o", "lstart=", "-p", str(int(pid))],
            capture_output=True, text=True, timeout=5, check=True,
        )
    except (_sp.CalledProcessError, _sp.TimeoutExpired,
            FileNotFoundError, ValueError):
        return None
    except OSError:
        # Reviewer P2 (2026-05-14 round 4): sandboxed environments
        # (Codex's review sandbox, certain CI containers, locked-down
        # macOS sandbox profiles) refuse subprocess spawn with
        # PermissionError or a generic OSError instead of just
        # making `ps` exit non-zero. The function's documented
        # contract is "return None when start time can't be
        # determined" — propagating PermissionError out of here
        # would break the entire `work` path before a claim could
        # be created. None tells the downstream identity check
        # "unverifiable," which already refuses to signal — the
        # conservative behavior we want when we can't read process
        # start times at all.
        return None
    raw = result.stdout.strip()
    if not raw:
        return None
    # Normalize internal whitespace so single-digit days
    # ("Wed Oct  1 14:30:00 2025") parse the same as double-digit.
    normalized = " ".join(raw.split())
    try:
        parsed = _dt.datetime.strptime(
            normalized, "%a %b %d %H:%M:%S %Y",
        )
    except ValueError:
        return None
    return parsed.timestamp()


def try_claim_shard_atomically(
    claim_path: Path,
    *,
    host: str | None = None,
    pid: int | None = None,
) -> bool:
    """Attempt to create ``claim_path`` atomically.

    Returns True if this worker won the claim, False if the claim
    file already exists (some other worker beat us to it). Uses
    ``O_CREAT | O_EXCL | O_WRONLY`` — the kernel guarantees exactly
    one process succeeds when multiple race.

    The file's content is a JSON object with ``host``, ``pid``,
    ``claimed_at``, ``start_time_epoch``, and ``tool`` so
    ``sweep-stale`` can later identify and reclaim dead-worker
    claims AND ``terminate-all`` / ``kill-all`` can verify the
    process they're about to signal is the same process that
    claimed (defending against PID reuse — the reviewer P2 fix
    from 2026-05-14). Cross-platform: works on POSIX (the
    calibration host) and on Windows-native (fallback environments;
    start_time_epoch will be None there but the host/pid fields
    still pin the claim's owner).

    Parent directory must exist; the caller is responsible for
    creating ``shards/<id>/`` before calling this. We don't create
    it here because the caller already does it via the manifest-
    write path.
    """
    host = host or _host()
    pid = pid if pid is not None else os.getpid()
    # Reviewer P2 (2026-05-14): record the claiming process's start
    # time so terminate-all / kill-all can verify identity before
    # signaling. Captured at claim time (not signal time) because
    # at signal time we want to compare what's-there against
    # what-the-original-worker-was. A None result (ps unavailable,
    # process already gone) gets stored as None and the identity
    # check at signal time will refuse to signal — safer than
    # trying to back-fill the start time later.
    start_time_epoch = process_start_time_epoch(pid)
    payload = json.dumps({
        "host": host,
        "pid": pid,
        "claimed_at": _now_iso(),
        "start_time_epoch": start_time_epoch,
        "tool": "shard_runner",
    }, sort_keys=True).encode("utf-8")
    try:
        fd = os.open(
            str(claim_path),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o644,
        )
    except FileExistsError:
        return False
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    return True


def refresh_claim_file(
    claim_path: Path,
    *,
    host: str | None = None,
    pid: int | None = None,
) -> None:
    """Overwrite an existing claim file with fresh ownership data.

    Reviewer P2 (2026-05-14 round 4): the resume path skipped
    recreating the claim file because the original worker's file
    "already exists from the original worker's first claim." But
    that file still recorded the dead original-worker pid + start
    time. After ``claim_shard`` updated state.json with the
    resumed worker's new pid, ``terminate-all`` / ``kill-all``
    still read the dead pid from the .claim file and skipped
    signaling — meaning a live resumed worker was unsignalable.

    This helper writes a fresh claim file with the current pid +
    start_time_epoch (alongside the existing host / claimed_at /
    tool fields). Resume paths call it before transitioning state.

    Atomicity: writes to a temp file in the same directory, then
    ``os.replace``s over the existing claim file. POSIX rename is
    atomic, so a crash during refresh leaves either the old claim
    (the resuming worker's predecessor's data) or the new claim
    (the resuming worker's data) — never a partial. The temp file
    suffix is ``.claim-refresh-*.tmp`` to distinguish from the
    state.json temp files.
    """
    host = host or _host()
    pid = pid if pid is not None else os.getpid()
    start_time_epoch = process_start_time_epoch(pid)
    payload = json.dumps({
        "host": host,
        "pid": pid,
        "claimed_at": _now_iso(),
        "start_time_epoch": start_time_epoch,
        "tool": "shard_runner",
    }, sort_keys=True).encode("utf-8")
    claim_path = Path(claim_path)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".claim-refresh-",
        suffix=".tmp",
        dir=str(claim_path.parent),
    )
    try:
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_name, claim_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def release_claim(claim_path: Path) -> None:
    """Delete the per-shard claim file. Idempotent — silently
    succeeds if the file is already gone.

    Workers call this on shard completion (after marking done) and
    on graceful shutdown. ``sweep-stale`` (v1.44.1.B) calls this
    for dead-worker claims that have been verified as stale.
    """
    try:
        Path(claim_path).unlink()
    except FileNotFoundError:
        pass


def read_claim_file(claim_path: Path) -> dict[str, Any] | None:
    """Read the claim metadata. Returns ``None`` if the file is
    missing or malformed (the caller decides what to do — typically
    treat it as "no active claim"). Used by ``sweep-stale`` and by
    ``status`` to surface which worker holds each claim.
    """
    if not Path(claim_path).exists():
        return None
    try:
        return json.loads(Path(claim_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def pid_alive(pid: int) -> bool:
    """Best-effort check whether a process is alive on the local
    host.

    Uses ``os.kill(pid, 0)`` which sends signal 0 — a no-op signal
    used precisely for liveness checks. Returns ``True`` if the
    process exists, ``False`` if it doesn't (``ProcessLookupError``)
    or if we lack permission to signal it (``PermissionError`` —
    treated as alive because we can't conclusively say it's gone).

    Important caveats:

      * Only meaningful for processes on this host. Cross-host
        liveness requires a different signal (heartbeat file, etc.)
        and is not in scope for v1.44.1. ``sweep-stale`` callers
        compare the claim file's recorded host against the local
        host and only attempt liveness checks for local-host pids.
      * PID reuse: a long-stale claim file could record a pid that
        the OS has since recycled into an unrelated process. This is
        why ``sweep-stale`` requires both a dead pid AND a claim
        age beyond the configured threshold before releasing — the
        age gate guards against the rare same-pid race.
    """
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # We can't signal it, but it exists — treat as alive.
        return True
    except OSError:
        # Unknown error talking to the kernel; conservative path
        # is "treat as alive" so sweep-stale never releases on
        # incomplete information.
        return True
    return True


# --------------- State-update lock (v1.44.1) ------------------


import contextlib


@contextlib.contextmanager
def state_update_lock(state_path: Path):
    """Acquire an exclusive lock for read-modify-write on state.json.

    Multiple workers may call ``write_state`` concurrently. Without
    coordination, two workers can read the same state, modify
    different shards, and overwrite each other's writes — the
    last writer wins, the other shard's update is lost.

    This context manager wraps the read-modify-write window in
    ``fcntl.flock(LOCK_EX)`` on POSIX (which the calibration host
    runs as WSL2 Linux). Workers serialize on the lock; the actual
    state-file writes still go through ``write_state``'s atomic
    rename. On Windows-native (a fallback environment SETEC does
    not target as the calibration host), this is a no-op — accept
    the race; per-shard claim files still coordinate correctly.

    The lock file lives at ``state.json.lock`` next to state.json
    so the lock survives across processes.

    Usage::

        with state_update_lock(state_path):
            state = read_state(state_path)
            state = claim_shard(state, shard_id, ...)
            write_state(state_path, state)
    """
    state_path = Path(state_path)
    lock_path = state_path.parent / f"{state_path.name}.lock"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl  # type: ignore  # POSIX only
    except ImportError:
        # Windows-native fallback: no locking, accept the race.
        # The calibration host is WSL2 Linux per SPEC_embedding_
        # model_choice.md §6.3, so this path is not exercised in
        # the supported deployment.
        yield
        return
    # Open the lock file (creating if needed). LOCK_EX serializes
    # readers and writers; multiple workers block until they get
    # the lock in turn.
    fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# --------------- Git sync layer (v1.44.2) ----------------------
#
# Multi-machine coordination uses git itself as the cross-host
# coordination layer. When state.json lives inside a git working
# tree (the operator has committed ``calibration_runs/<run_id>/``
# to a repo with a remote), workers pull before reading and commit
# + push after writing. Different shards by different machines
# produce disjoint diffs and merge trivially via ``git pull
# --rebase``. Same-shard concurrent claims (which the file-system
# .claim primitive does NOT prevent cross-host) get caught at push
# time as merge conflicts, and ``shard_runner resolve-conflict``
# offers a structured 3-way merge for the JSON.
#
# Auto-detect: ``find_git_repo(path)`` walks up from ``path``
# looking for ``.git``. When found, sync is active by default.
# When not found, the helpers silently no-op so the v1.44.0 /
# v1.44.1.x single-host paths are unaffected.
#
# Failure resilience (spec §4.3): pull / push errors are
# non-fatal. The worker logs the error and continues with the
# local state; the next successful sync brings everything back
# into agreement. The cost of an occasional transient blip is
# at most "this transition wasn't visible to the other host until
# the next push," which is acceptable for a calibration run that
# spans hours or days.


import subprocess


class SyncError(RuntimeError):
    """Raised when git sync encounters an unrecoverable error
    (unresolved merge conflict, missing remote, etc.). Transient
    network errors do NOT raise this — the caller handles those
    by logging and retrying on the next transition."""


def find_git_repo(path: Path) -> Path | None:
    """Walk up from ``path`` looking for a ``.git`` entry.

    Returns the directory containing ``.git`` (the repo root), or
    ``None`` if no git working tree is found within the path's
    ancestors. Used by the sync helpers to decide whether
    state.json is git-tracked.

    Pure Python — no subprocess, so this is cheap enough to call
    on every state-update transition without measurable overhead.
    """
    current = Path(path).resolve()
    if current.is_file():
        current = current.parent
    # Walk up until we hit the filesystem root.
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    # Check the root directory itself (rare but possible: /.git).
    if (current / ".git").exists():
        return current
    return None


def is_git_synced(state_path: Path) -> bool:
    """Whether ``state_path`` lives inside a git working tree.

    True means workers will attempt pull/commit/push around state
    transitions; False means they treat state.json as a purely
    local file. The auto-detect approach means operators don't
    have to pass a flag to opt into sync — committing the
    calibration_runs/ directory to a git repo IS the opt-in.
    """
    return find_git_repo(state_path) is not None


def _git(
    repo_root: Path,
    args: list[str],
    *,
    timeout: float = 30.0,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Invoke ``git -C <repo_root> <args>`` with sensible defaults.

    Centralized so the test suite can monkeypatch one function
    rather than dozens of subprocess calls. ``check=True`` raises
    ``subprocess.CalledProcessError`` on non-zero exit, the same
    error class operators see when they run git manually.
    """
    cmd = ["git", "-C", str(repo_root), *args]
    return subprocess.run(
        cmd,
        check=check,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def pull_state(
    state_path: Path,
    *,
    enabled: bool = True,
    remote: str = "origin",
    branch: str | None = None,
) -> bool:
    """Run ``git pull --rebase`` for the repo containing
    ``state_path``.

    Returns True if a pull was actually attempted (state was in a
    git repo and ``enabled`` was True), False if the pull was
    skipped (not in a repo, or operator passed ``--no-sync-state``).
    Network errors raise ``SyncError`` so the caller can log and
    continue — they are not fatal to the worker loop.

    Why ``--rebase`` and not a merge? A rebase keeps the state-
    transition history linear, which makes operator inspection
    easier. The state.json edits done by individual workers are
    semantically commutative (different shards), so the rebase
    almost never produces a real conflict.
    """
    if not enabled:
        return False
    repo_root = find_git_repo(state_path)
    if repo_root is None:
        return False
    args = ["pull", "--rebase", "--quiet", remote]
    if branch:
        args.append(branch)
    try:
        _git(repo_root, args)
        return True
    except subprocess.CalledProcessError as exc:
        # Distinguish "real merge conflict on state.json" (caller
        # should run resolve-conflict) from "transient network
        # error" (caller can ignore and retry). Conflict markers
        # in state.json show up after a failed rebase; the
        # presence of CONFLICT in stderr is a strong signal.
        if "CONFLICT" in (exc.stderr or "") or "conflict" in (exc.stderr or "").lower():
            raise SyncError(
                f"git pull --rebase produced a conflict in "
                f"{state_path}; run `shard_runner resolve-conflict` "
                f"to inspect and merge."
            ) from exc
        # Transient: network down, push race, etc. Not fatal —
        # the caller logs and continues.
        raise SyncError(
            f"git pull failed for {state_path}: "
            f"{exc.stderr or exc.stdout or exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SyncError(
            f"git pull timed out for {state_path} (network slow "
            f"or remote unreachable)"
        ) from exc


def push_state(
    state_path: Path,
    *,
    message: str,
    enabled: bool = True,
    remote: str = "origin",
    branch: str | None = None,
    retries: int = 3,
) -> bool:
    """Stage ``state_path``, commit with ``message``, and push.

    Retries up to ``retries`` times on push race (another machine
    pushed first); between retries, runs ``git pull --rebase`` to
    bring in the other side's commit. On final failure, raises
    ``SyncError`` so the caller can log and continue — the local
    state is still consistent, and the next successful push will
    catch the remote up.

    Returns True if the push succeeded, False if there was nothing
    to commit (state unchanged since the last commit). False is
    informational; the caller treats it the same as success.
    """
    if not enabled:
        return False
    repo_root = find_git_repo(state_path)
    if repo_root is None:
        return False
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            _git(repo_root, ["add", str(state_path)])
            # `git commit` exits non-zero when there's nothing to
            # commit. That's informational, not an error.
            result = _git(
                repo_root, ["commit", "-m", message],
                check=False,
            )
            if result.returncode != 0:
                # No changes staged; nothing to push.
                return False
            push_args = ["push", "--quiet", remote]
            if branch:
                push_args.append(branch)
            _git(repo_root, push_args)
            return True
        except subprocess.CalledProcessError as exc:
            last_error = exc
            stderr = (exc.stderr or "").lower()
            # Push race: rebase and retry.
            if (
                attempt < retries - 1
                and ("rejected" in stderr or "non-fast-forward" in stderr)
            ):
                try:
                    _git(
                        repo_root,
                        ["pull", "--rebase", "--quiet", remote],
                        check=True,
                    )
                except subprocess.CalledProcessError:
                    # Rebase itself failed (real conflict). Bail.
                    raise SyncError(
                        f"git push race with a real conflict in "
                        f"{state_path}; run `shard_runner "
                        f"resolve-conflict`."
                    ) from exc
                continue
            # Non-retryable or out of retries.
            raise SyncError(
                f"git push failed for {state_path}: "
                f"{exc.stderr or exc.stdout or exc}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            last_error = exc
            if attempt < retries - 1:
                continue
            raise SyncError(
                f"git push timed out for {state_path}"
            ) from exc
    # Exhausted retries without raising — shouldn't normally happen
    # since the loop always raises or returns. Defensive fallback:
    raise SyncError(
        f"git push exhausted retries for {state_path}: {last_error}"
    )


# --------------- Structured 3-way merge for state.json ----------


def _shard_state_rank(state_str: str | None) -> int:
    """Order the shard-state strings by "how much work has been
    done." Higher is more advanced.

    pending(0) < claimed(1) < claimed_pending_resume(2) < done(3)

    ``failed`` is intentionally OMITTED from the rank ladder and
    handled out-of-band by :func:`merge_state_files` (it's terminal
    unless the other side recorded ``done``). Callers that rely on
    rank ordering should special-case ``failed`` first.

    The rank for the unknown state is conservatively 0 (treated as
    ``pending``) so a typo doesn't accidentally win a merge.
    """
    return {
        "pending": 0,
        "claimed": 1,
        "claimed_pending_resume": 2,
        "done": 3,
    }.get(state_str or "pending", 0)


def merge_state_files(
    base: dict[str, Any],
    ours: dict[str, Any],
    theirs: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Three-way structured merge over two competing state.json
    revisions.

    Inputs:
      * ``base``: the common ancestor (typically the state.json
        from the merge base — what both sides started from).
      * ``ours``: this host's local revision.
      * ``theirs``: the remote revision (the other host's).

    Returns ``(merged_state, unresolved_shard_ids)``:
      * ``merged_state``: the auto-merged JSON dict.
      * ``unresolved_shard_ids``: shard ids that both sides
        modified in incompatible ways. These require manual
        review per spec §4.6.

    Merge policy (per shard):
      * If only one side changed the shard relative to base: take
        that side.
      * If both changed it identically: take ours (arbitrary; the
        merged result is the same).
      * If both changed it differently:
          - If ours' state rank >= theirs' state rank: take ours
            (this host has progressed further).
          - If theirs' state rank > ours' state rank: take theirs
            (remote host has progressed further).
          - If the ranks tie AND the host/pid differ: SAME-SHARD
            CONCURRENT CLAIM. Report in unresolved_shard_ids;
            keep ours in the merged output as a placeholder.

    Top-level non-shard fields (run_id, shard_count, etc.) are
    taken from ``ours`` because they should be identical anyway —
    the run metadata is set at ``shard_runner shard`` time and
    never changes during the run.
    """
    merged = dict(ours)  # start from ours; we'll overwrite shards
    base_shards = base.get("shards", {})
    ours_shards = ours.get("shards", {})
    theirs_shards = theirs.get("shards", {})
    merged_shards: dict[str, Any] = {}
    unresolved: list[str] = []
    all_sids = sorted(
        set(base_shards) | set(ours_shards) | set(theirs_shards)
    )
    for sid in all_sids:
        b = base_shards.get(sid)
        o = ours_shards.get(sid)
        t = theirs_shards.get(sid)
        if o == t:
            # Both sides agree (including both-equal-to-base).
            merged_shards[sid] = o if o is not None else (b or {})
            continue
        if o == b:
            # We didn't change it; theirs did.
            merged_shards[sid] = t
            continue
        if t == b:
            # They didn't change it; we did.
            merged_shards[sid] = o
            continue
        # Both sides changed and the results differ.
        #
        # Special case: ``failed`` is terminal — the only state that
        # overrides ``failed`` is ``done`` (the other host genuinely
        # re-ran the shard and it succeeded). Any non-``done``
        # competing state must yield to ``failed`` so a downstream
        # sweep / re-claim / pull cannot silently resurrect a failed
        # shard back to ``pending`` / ``claimed`` /
        # ``claimed_pending_resume``. Codex PR #27 review P0.
        o_state = (o or {}).get("state")
        t_state = (t or {}).get("state")
        if o_state == "failed" and t_state == "done":
            merged_shards[sid] = t
            continue
        if t_state == "failed" and o_state == "done":
            merged_shards[sid] = o
            continue
        if o_state == "failed" and t_state != "done":
            merged_shards[sid] = o
            continue
        if t_state == "failed" and o_state != "done":
            merged_shards[sid] = t
            continue
        # Neither side is failed (or both are; rare but symmetric).
        # Use rank ordering to pick the more-advanced state.
        o_rank = _shard_state_rank(o_state)
        t_rank = _shard_state_rank(t_state)
        if o_rank > t_rank:
            merged_shards[sid] = o
        elif t_rank > o_rank:
            merged_shards[sid] = t
        else:
            # Tied rank. Same-shard concurrent claim is the canonical
            # ambiguous case: both sides say "claimed" but by
            # different hosts/pids. We mark it unresolved and keep
            # ours as the placeholder (the caller decides what to
            # do — typically write the merged file with conflict
            # comments, alert the operator, and abort).
            o_host = (o or {}).get("claimed_by_host")
            t_host = (t or {}).get("claimed_by_host")
            if o_host != t_host:
                unresolved.append(sid)
                merged_shards[sid] = o
            else:
                # Same host, same rank, different content. Probably
                # a transient race between two pids on one machine
                # — pick the more recent timestamp.
                o_ts = (o or {}).get("claimed_at") or ""
                t_ts = (t or {}).get("claimed_at") or ""
                merged_shards[sid] = t if t_ts > o_ts else o
    merged["shards"] = merged_shards
    return merged, unresolved


def status_summary(state: dict[str, Any]) -> dict[str, Any]:
    """Compute a counts-by-state summary for the ``status`` CLI
    subcommand."""
    shards = state.get("shards", {})
    counts: dict[str, int] = {s: 0 for s in VALID_SHARD_STATES}
    for shard in shards.values():
        s = shard.get("state", "pending")
        counts[s] = counts.get(s, 0) + 1
    return {
        "run_id": state.get("run_id"),
        "shard_count": state.get("shard_count"),
        "created_at": state.get("created_at"),
        "counts": counts,
        "fraction_done": (
            counts.get("done", 0) / state.get("shard_count", 1)
            if state.get("shard_count") else 0.0
        ),
    }
