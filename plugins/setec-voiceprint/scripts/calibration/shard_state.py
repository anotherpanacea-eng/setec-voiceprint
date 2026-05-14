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
