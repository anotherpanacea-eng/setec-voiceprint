#!/usr/bin/env python3
"""shard_runner.py — sharded calibration orchestrator.

The CLI orchestrator for the sharded-calibration toolchain
specified in ``internal/SPEC_sharded_calibration.md``.

Phase contents:

  * v1.44.0 — Core: ``shard`` (deterministic stratified split),
    ``work`` (single-worker claim/score/done loop with between-shard
    SIGTERM safety), ``aggregate`` (per-signal threshold sweep),
    ``verify``, ``status``.
  * v1.44.1.A — Concurrent workers: ``--workers N`` spawns N
    subprocesses coordinating via atomic per-shard claim files
    plus a state-update lock on state.json.
  * v1.44.1.B (this commit) — Scheduling + control plane:
    ``--time-window HH:MM-HH:MM`` for hour-of-day worker gates,
    ``pause-all`` / ``terminate-all`` / ``kill-all`` / ``sweep-stale``
    subcommands, and the ``SigtermInterrupt`` contract for
    mid-shard SIGTERM checkpointing by opt-in scorers.

Deferred to v1.44.1.C+:
  * launchd plist + caffeinate runbook for macOS nightly setup.
  * Multi-machine git-synced state file (v1.44.2).
  * Default scorer's opt-in to ``SigtermInterrupt`` (the contract
    is in place; ``load_or_score_corpus`` integration ships once
    we have a real RAID-scale run to test against).

Why v1.44.0 alone is useful: the single-worker sharded path lets a
maintainer run RAID's 8M-row calibration on a single laptop with
SIGTERM-safe checkpointing — interrupting and resuming becomes
free. The 8× wall-clock improvement waits for v1.44.1, but the
crash-safety improvement is immediate.

End-to-end example::

    # One-time: split the source manifest into ~80 shards of ~100k
    # rows each, stratified by (register, ai_status).
    python3 scripts/calibration/shard_runner.py shard \\
        --source-manifest ai-prose-baselines-private/raid/manifest.jsonl \\
        --run-id raid_tier1_fpr0.01_2026-05-11 \\
        --shard-size 100000 \\
        --stratify register,ai_status \\
        --shuffle-seed 42 \\
        --no-tier2 --no-tier3 \\
        --fpr-target 0.01

    # Work the queue. Single worker per process for v1.44.0; the
    # script claims one shard, scores it, writes the cache, marks
    # done, and loops to the next.
    python3 scripts/calibration/shard_runner.py work \\
        --run-id raid_tier1_fpr0.01_2026-05-11

    # Inspect progress.
    python3 scripts/calibration/shard_runner.py status \\
        --run-id raid_tier1_fpr0.01_2026-05-11

    # When all shards are done, aggregate.
    python3 scripts/calibration/shard_runner.py aggregate \\
        --run-id raid_tier1_fpr0.01_2026-05-11 \\
        --out ai-prose-baselines-private/raid/_survey_sharded.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(SCRIPT_DIR.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR.parent))

from sharding import (  # type: ignore
    compute_shard_count,
    estimate_stratum_balance,
    shard_summary,
    split_into_shards,
)
from shard_state import (  # type: ignore
    ShardStateError,
    build_initial_state,
    claim_shard,
    mark_done,
    mark_failed,
    mark_pending_resume,
    pending_shard_ids,
    pid_alive,
    read_claim_file,
    read_state,
    release_claim,
    resumable_shard_ids,
    sha256_file,
    state_update_lock,
    status_summary,
    try_claim_shard_atomically,
    write_state,
    _host,
    _shard_id,
)


TASK_SURFACE = "calibration"
TOOL_NAME = "shard_runner"
SCRIPT_VERSION = "1.0"

# Default flush cadence (shard worker writes a partial cache every
# N entries scored). The spec §2.3 settled on a 5-10 minute target;
# at ~10 rows/sec on the M-series laptop that's 3000-6000 rows per
# flush, so we default to 5000.
DEFAULT_FLUSH_EVERY = 5000

# Default threshold for sweep-stale (v1.44.1.B). Per spec §2.2,
# claims older than this whose pid is dead are released back to
# pending. Six hours is conservative: it covers an overnight
# unattended-worker scenario (worker dies at 1 AM, operator runs
# sweep-stale at 9 AM) while still leaving a safety margin for
# slow long-running shards that might otherwise look "stale" but
# are actually making progress.
DEFAULT_STALE_THRESHOLD_HOURS = 6


# --------------- v1.44.1.B SIGTERM-resume contract ---------------


class SigtermInterrupt(Exception):
    """Raised by scorers that opt into mid-shard SIGTERM
    checkpointing.

    The worker installs a SIGTERM/SIGINT handler that sets a
    ``_SigtermFlag.tripped`` boolean. Scorers can poll the flag
    during a long-running scoring pass; when they observe it set,
    they should:

      1. Flush whatever records they've scored so far to the shard's
         cache (preferably the same JSON contract the production
         scorer uses on completion).
      2. Raise ``SigtermInterrupt(n_entries_flushed=K, n_entries_total=N)``.

    ``_process_shard`` catches the exception, marks the shard
    ``claimed_pending_resume`` with the flushed/total counts, leaves
    the claim file in place (so only the original host can resume
    per spec §2.4), and returns 0 so the worker exits cleanly.

    The default production scorer in v1.44.1.B does NOT yet honor
    this — it inherits ``load_or_score_corpus``'s end-of-run cache
    semantics, so SIGTERM mid-shard means "wait for the current
    shard to finish, then exit between shards." The contract is in
    place; the production scorer's opt-in lands in a follow-up
    (after v1.44.1.B's framework primitives are battle-tested).
    """

    def __init__(
        self,
        *,
        n_entries_flushed: int,
        n_entries_total: int | None = None,
        partial_cache_path: Path | None = None,
    ) -> None:
        super().__init__(
            f"SIGTERM mid-shard checkpoint: "
            f"{n_entries_flushed} of {n_entries_total} entries flushed"
        )
        self.n_entries_flushed = n_entries_flushed
        self.n_entries_total = n_entries_total
        self.partial_cache_path = partial_cache_path


# --------------- Run-directory layout ----------------------------


def run_dir(base: Path, run_id: str) -> Path:
    """Path to ``calibration_runs/<run_id>/`` under ``base``.

    The base defaults to the user's baselines folder
    (``$SETEC_BASELINES_DIR`` or the repo-sibling) under
    ``calibration_runs/``. Tests pass an explicit ``base`` to
    avoid touching the real folder.
    """
    return Path(base) / "calibration_runs" / run_id


def shards_dir(base: Path, run_id: str) -> Path:
    return run_dir(base, run_id) / "shards"


def state_path(base: Path, run_id: str) -> Path:
    return run_dir(base, run_id) / "state.json"


def shard_manifest_path(base: Path, run_id: str, shard_id: str) -> Path:
    return shards_dir(base, run_id) / shard_id / "manifest.jsonl"


def shard_cache_path(base: Path, run_id: str, shard_id: str) -> Path:
    return shards_dir(base, run_id) / shard_id / "cache.json"


def shard_claim_path(base: Path, run_id: str, shard_id: str) -> Path:
    """Path to the per-shard atomic-claim file. v1.44.1+ uses this
    for multi-worker coordination: workers create the file via
    ``O_CREAT | O_EXCL`` so only one wins each claim race. Released
    on shard completion or by ``sweep-stale``."""
    return shards_dir(base, run_id) / shard_id / ".claim"


def pause_marker_path(base: Path, run_id: str) -> Path:
    """Path to the run's pause marker. v1.44.1.B's ``pause-all``
    writes this file; workers check between shards and exit
    cleanly when present. Pause is reversible: ``pause-all --clear``
    removes the marker and the next worker invocation will resume.
    """
    return run_dir(base, run_id) / ".pause"


# --------------- Pause-marker helpers (v1.44.1.B) ----------------


def is_paused(base: Path, run_id: str) -> bool:
    """Check whether the pause marker exists for this run.

    Workers call this between shards (in ``_run_single_worker``'s
    loop). When True, the worker logs a pause message and exits
    cleanly with rc=0 — distinguishable from a real failure and
    from a SIGTERM exit (both also end the loop, but pause means
    "operator decision, expected to resume later").
    """
    return pause_marker_path(base, run_id).exists()


def write_pause_marker(
    base: Path, run_id: str, *, reason: str | None = None,
) -> Path:
    """Atomically write the pause marker. Optional ``reason`` is
    stored alongside the timestamp so an operator inspecting the
    file later sees why the run was paused.

    Uses the same temp-file + rename pattern as ``write_state`` so
    a crash during writing leaves either the old marker (None) or
    the new one (complete) — never a partial.

    Returns the path to the marker for callers that want to log it.
    """
    marker = pause_marker_path(base, run_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "paused_at": _dt.datetime.now(
            _dt.timezone.utc,
        ).isoformat(timespec="seconds"),
        "paused_by_host": _host(),
        "paused_by_pid": os.getpid(),
        "reason": reason or "",
    }
    # Same atomic-rename pattern as write_state. We don't use
    # write_state itself because the marker isn't state.json; it's
    # a separate single-purpose file with simpler semantics.
    import tempfile
    fd, tmp_name = tempfile.mkstemp(
        prefix=".pause-",
        suffix=".tmp",
        dir=str(marker.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, marker)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return marker


def clear_pause_marker(base: Path, run_id: str) -> bool:
    """Remove the pause marker. Returns True if a marker was
    removed, False if there was nothing to clear. Idempotent: safe
    to call when no marker exists."""
    marker = pause_marker_path(base, run_id)
    try:
        marker.unlink()
        return True
    except FileNotFoundError:
        return False


# --------------- Time-window helpers (v1.44.1.B) -----------------


def parse_time_window(
    spec: str,
) -> tuple[_dt.time, _dt.time]:
    """Parse a ``HH:MM-HH:MM`` time window spec into ``(start, end)``
    ``datetime.time`` instances.

    Examples::

        parse_time_window("23:00-06:00")  # crosses midnight
        parse_time_window("09:00-17:00")  # business hours

    Raises ``ValueError`` for malformed specs. The check is
    permissive: leading whitespace, lowercase letters, and a
    trailing newline are all tolerated. Both endpoints must parse
    as ``HH:MM`` (24-hour clock); seconds and timezone offsets are
    not accepted (windows align to minute boundaries).
    """
    cleaned = spec.strip().lower()
    if "-" not in cleaned:
        raise ValueError(
            f"Time window must be HH:MM-HH:MM; got {spec!r}"
        )
    start_s, end_s = cleaned.split("-", 1)
    try:
        start = _dt.time.fromisoformat(start_s.strip())
        end = _dt.time.fromisoformat(end_s.strip())
    except ValueError as exc:
        raise ValueError(
            f"Time window endpoints must parse as HH:MM; got {spec!r} ({exc})"
        ) from exc
    return start, end


def is_within_time_window(
    window: tuple[_dt.time, _dt.time] | None,
    *,
    now: _dt.datetime | None = None,
) -> bool:
    """Check whether ``now`` (default: current local time) falls
    inside the time window.

    A window like ``23:00-06:00`` crosses midnight; we handle that
    by treating the window as the *union* ``[start, 24:00) ∪ [00:00, end)``.
    A degenerate window where start == end is treated as "always
    in window" (24 hours).

    Returns True if no window is configured (None) — that's the
    same as "always allowed," matching the default behavior when
    the operator hasn't passed ``--time-window``.
    """
    if window is None:
        return True
    start, end = window
    if now is None:
        now = _dt.datetime.now()  # local time, per spec §2.5
    current = now.time().replace(microsecond=0)
    if start == end:
        return True  # 24-hour window
    if start < end:
        # Same-day window: e.g., 09:00-17:00.
        return start <= current < end
    # Crosses-midnight window: e.g., 23:00-06:00.
    return current >= start or current < end


# --------------- Manifest I/O -----------------------------------


def read_manifest(path: Path) -> list[dict[str, Any]]:
    """Read a JSON-lines manifest into memory. Caller is responsible
    for ensuring this fits — RAID at 8M rows is ~5 GB raw, ~10-15
    GB resident in Python dicts. The sharded toolchain reads the
    full source manifest exactly once (during ``shard``) and from
    then on only operates on per-shard slices."""
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Malformed JSON in {path}: {exc}"
                ) from exc
    return rows


def write_manifest(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write JSON-lines manifest, one object per line. Used to write
    each shard's slice of the source."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


# --------------- Default scorer (real path) ----------------------


def _default_scorer(
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
    """Call calibration_survey's load_or_score_corpus on the shard
    manifest.

    The default scorer is a thin adapter that translates
    shard_runner's vocabulary to calibration_survey's argparse
    Namespace. Tests inject a stub via the ``--scorer-callable``
    test hook (or by monkey-patching ``DEFAULT_SCORER``).

    Returns a dict with ``records`` (list) and ``meta`` (dict).
    The records list is the same shape calibration_survey produces:
    one dict per scored corpus row, with per-signal score columns
    plus the original label fields.
    """
    # Imported lazily so the module can be imported (and its other
    # subcommands invoked) in environments where calibration_survey
    # can't import its dependencies (e.g., spaCy missing).
    import calibrate_thresholds as ct  # type: ignore
    from argparse import Namespace
    inner = Namespace(
        manifest=str(shard_manifest_path),
        fpr_target=fpr_target,
        tier2=tier2,
        tier3=tier3,
        use=use,
        signal=list(ct.COMPRESSION_HEURISTICS.keys())[0],
        bootstrap_seed=42,
        bootstrap_resamples=2000,
        bootstrap_confidence=0.95,
        max_entries=None,
        max_entries_seed=42,
        records_cache=str(cache_path),
        refresh_cache=False,
        scorer_cache_version=getattr(ct, "SCORER_CACHE_VERSION", "v1"),
    )
    records, meta, cache_hit = ct.load_or_score_corpus(
        inner, cache_path=cache_path, refresh=False,
    )
    return {
        "records": records,
        "meta": meta,
        "cache_hit": cache_hit,
    }


# Tests inject by setting this; production keeps the default.
DEFAULT_SCORER: Callable[..., dict[str, Any]] = _default_scorer


# --------------- shard subcommand --------------------------------


def cmd_shard(args: argparse.Namespace) -> int:
    """Read source manifest, split into N shards, write shard
    manifests + initial state.json. Idempotent: re-running against
    an existing run_id refuses to overwrite (use ``--force`` to
    proceed)."""
    source = Path(args.source_manifest).expanduser()
    if not source.exists():
        sys.stderr.write(f"Source manifest not found: {source}\n")
        return 2
    base = Path(args.base_dir).expanduser()
    sp = state_path(base, args.run_id)
    if sp.exists() and not args.force:
        sys.stderr.write(
            f"State file already exists for run_id {args.run_id!r}: "
            f"{sp}. Pass --force to overwrite (DESTRUCTIVE).\n"
        )
        return 2

    sys.stderr.write(f"Reading source manifest: {source}\n")
    rows = read_manifest(source)
    sys.stderr.write(f"  {len(rows)} rows loaded.\n")
    source_sha = sha256_file(source)
    sys.stderr.write(f"  sha256={source_sha[:16]}...\n")
    stratify_by = (
        [s.strip() for s in args.stratify.split(",") if s.strip()]
        if args.stratify else ["register", "ai_status"]
    )
    n_shards = (
        args.shard_count
        if args.shard_count
        else compute_shard_count(
            len(rows), shard_size_target=args.shard_size,
        )
    )
    # Pre-flight: warn on tiny strata that won't fan across all shards.
    balance = estimate_stratum_balance(rows, stratify_by)
    if balance["smallest_stratum_size"] < n_shards:
        sys.stderr.write(
            f"  WARNING: smallest stratum has "
            f"{balance['smallest_stratum_size']} rows but "
            f"{n_shards} shards requested. Some shards will lack "
            f"representation from that stratum.\n"
        )
    sys.stderr.write(
        f"  Stratification: {stratify_by}; {balance['n_strata']} strata; "
        f"smallest {balance['smallest_stratum_size']}, "
        f"largest {balance['largest_stratum_size']}.\n"
    )
    sys.stderr.write(f"Splitting into {n_shards} shards (seed={args.shuffle_seed})...\n")
    shards = split_into_shards(
        rows, n_shards=n_shards,
        stratify_by=stratify_by, seed=args.shuffle_seed,
    )
    summaries: list[dict[str, Any]] = []
    for idx, shard in enumerate(shards):
        sid = _shard_id(idx)
        mp = shard_manifest_path(base, args.run_id, sid)
        write_manifest(mp, shard)
        summaries.append(shard_summary(shard, stratify_by))
        if (idx + 1) % 10 == 0:
            sys.stderr.write(f"  ... wrote shard {sid}\n")
    sys.stderr.write(f"  All {n_shards} shard manifests written.\n")
    state = build_initial_state(
        run_id=args.run_id,
        source_manifest_path=source,
        source_manifest_sha256=source_sha,
        shard_count=n_shards,
        shard_size_target=args.shard_size,
        stratify_by=stratify_by,
        shuffle_seed=args.shuffle_seed,
        fpr_target=args.fpr_target,
        tier1=args.tier1,
        tier2=args.tier2,
        tier3=args.tier3,
        embedding_model=args.embedding_model,
        embedding_revision=args.embedding_revision,
        shard_summaries=summaries,
    )
    write_state(sp, state)
    sys.stderr.write(f"State file written: {sp}\n")
    sys.stderr.write(
        f"Ready to work. Run:\n"
        f"  shard_runner work --run-id {args.run_id}\n"
    )
    return 0


# --------------- work subcommand ---------------------------------


class _SigtermFlag:
    """Tiny SIGTERM/SIGINT sentinel. The worker polls
    ``flag.tripped`` after each shard and exits cleanly when set.
    A more granular policy (preserve partial progress mid-shard)
    is in scope for v1.44.1; v1.44.0 only honors SIGTERM between
    shards."""

    def __init__(self) -> None:
        self.tripped = False
        self.signal_received: int | None = None

    def trip(self, signum: int, frame: Any) -> None:
        self.tripped = True
        self.signal_received = signum
        sys.stderr.write(
            f"\nSignal {signum} received; "
            f"finishing current shard then exiting cleanly.\n"
        )


def _install_signal_handlers(flag: _SigtermFlag) -> None:
    signal.signal(signal.SIGTERM, flag.trip)
    signal.signal(signal.SIGINT, flag.trip)


def cmd_work(args: argparse.Namespace) -> int:
    """Claim and score pending shards. Defaults to single-worker
    (v1.44.0 behavior); ``--workers N`` (v1.44.1) spawns N
    subprocesses that coordinate via atomic per-shard claim files
    and a state-update lock on state.json.

    Stops cleanly on SIGTERM / SIGINT or when no pending shards
    remain.
    """
    n_workers = max(1, int(getattr(args, "workers", 1) or 1))
    if n_workers == 1:
        return _run_single_worker(args, worker_label="worker-0")
    return _run_multi_worker(args, n_workers=n_workers)


def _run_multi_worker(args: argparse.Namespace, *, n_workers: int) -> int:
    """Spawn ``n_workers`` subprocesses, each running the single-
    worker loop. Coordination is via atomic .claim files plus the
    state_update_lock on state.json — workers serialize on the
    lock during state-file writes, and they race to create per-
    shard claim files (the kernel guarantees exactly one wins).

    Spawned via ``multiprocessing`` with the ``spawn`` start method
    so the subprocess gets a clean Python interpreter — important
    because the test suite monkeypatches ``DEFAULT_SCORER`` in the
    parent process and we want subprocesses to inherit the
    production scorer unless the test explicitly arranges
    otherwise.

    Returns 0 if all workers exit cleanly, 4 if any worker
    exited non-zero.
    """
    import multiprocessing as mp

    sys.stderr.write(
        f"Spawning {n_workers} workers; coordinating via atomic "
        f"claim files at shards/<id>/.claim and the "
        f"state.json.lock state-update lock.\n"
    )
    # Use fork on POSIX when available — the test suite relies on
    # subprocess inheritance of monkeypatched DEFAULT_SCORER. On
    # Windows / non-POSIX, fall back to spawn.
    try:
        ctx = mp.get_context("fork")
    except (ValueError, RuntimeError):
        ctx = mp.get_context("spawn")
    processes = []
    for i in range(n_workers):
        p = ctx.Process(
            target=_worker_subprocess_entry,
            args=(vars(args), i),
            name=f"shard-worker-{i}",
        )
        p.start()
        processes.append(p)
    for p in processes:
        p.join()
    failed = [
        (p.name, p.exitcode) for p in processes
        if p.exitcode is not None and p.exitcode != 0
    ]
    if failed:
        sys.stderr.write(
            f"{len(failed)} of {n_workers} workers exited non-zero: "
            f"{failed}\n"
        )
        return 4
    sys.stderr.write(
        f"All {n_workers} workers exited cleanly.\n"
    )
    return 0


def _worker_subprocess_entry(args_dict: dict, worker_index: int) -> None:
    """Entry point for `_run_multi_worker`'s spawned subprocesses.

    Reconstructs the argparse Namespace from a dict (multiprocessing
    can pickle dicts cleanly but Namespace would need extra setup),
    overrides ``workers`` to 1 so the subprocess runs the single-
    worker loop, and exits with the loop's return code.
    """
    args = argparse.Namespace(**args_dict)
    args.workers = 1
    rc = _run_single_worker(args, worker_label=f"worker-{worker_index}")
    sys.exit(rc)


def _run_single_worker(
    args: argparse.Namespace, *, worker_label: str = "worker-0",
) -> int:
    """One worker's claim-score-mark-done loop.

    Uses atomic .claim files for shard ownership and the state-
    update lock for state.json read-modify-writes. Safe to run
    multiple instances concurrently (either spawned by
    ``_run_multi_worker`` or by the user manually launching
    multiple ``shard_runner work`` invocations).

    v1.44.1.B adds two between-shard exit gates:

      * Pause marker: if ``.pause`` exists in the run directory,
        the worker exits cleanly (operator-driven pause; resume by
        clearing the marker and re-running ``work``).
      * Time window: if ``--time-window HH:MM-HH:MM`` was passed
        and the current local time is outside the window, the
        worker exits cleanly (so a launchd-scheduled nightly run
        ends itself at sunrise).

    Both gates trigger between shards only — a shard already in
    progress finishes (potentially well past the deadline) per
    spec §2.5.
    """
    base = Path(args.base_dir).expanduser()
    sp = state_path(base, args.run_id)
    if not sp.exists():
        sys.stderr.write(f"State file not found: {sp}\n")
        return 2
    # Parse --time-window once; subsequent loop iterations just
    # check the result.
    time_window: tuple[_dt.time, _dt.time] | None = None
    spec = getattr(args, "time_window", None)
    if spec:
        try:
            time_window = parse_time_window(spec)
        except ValueError as exc:
            sys.stderr.write(f"Invalid --time-window {spec!r}: {exc}\n")
            return 2
    flag = _SigtermFlag()
    _install_signal_handlers(flag)
    n_completed = 0
    while not flag.tripped:
        # Pause gate. Operator-driven; clears via `pause-all --clear`.
        if is_paused(base, args.run_id):
            sys.stderr.write(
                f"{worker_label}: pause marker present; exiting cleanly. "
                f"Clear with `shard_runner pause-all --clear --run-id "
                f"{args.run_id}`.\n"
            )
            break
        # Time-window gate. Outside the window we mimic the SIGTERM
        # exit path; the launchd template (v1.44.1.C) expects
        # ``SuccessfulExit: false`` to NOT auto-respawn, so a
        # clean exit is the right signal here.
        if not is_within_time_window(time_window):
            sys.stderr.write(
                f"{worker_label}: outside time window {spec!r}; "
                f"exiting cleanly. {n_completed} shard(s) completed.\n"
            )
            break
        state = read_state(sp)
        target_id, expected_state = _select_next_shard(
            state, base, args.run_id,
        )
        if target_id is None:
            sys.stderr.write(
                f"{worker_label}: no claimable shards remain. "
                f"{n_completed} shard(s) completed in this session.\n"
            )
            break
        # Atomic claim: try to create the .claim file. If we win,
        # we own this shard until we release the file. If we lose
        # (another worker raced ahead), try again on the next loop
        # iteration.
        claim_path = shard_claim_path(base, args.run_id, target_id)
        claim_path.parent.mkdir(parents=True, exist_ok=True)
        if expected_state == "pending":
            won = try_claim_shard_atomically(claim_path)
            if not won:
                # Another worker beat us; try again on next iter.
                continue
        else:
            # Resume path: claim file already exists from the
            # original worker's first claim. Don't try to re-create
            # it; just continue with the state-update step.
            pass
        # Update state.json under the lock to reflect the claim.
        try:
            with state_update_lock(sp):
                state = read_state(sp)
                state = claim_shard(
                    state, target_id, expected_state=expected_state,
                )
                write_state(sp, state)
        except ShardStateError as exc:
            sys.stderr.write(
                f"{worker_label}: state-update claim failed for "
                f"shard {target_id}: {exc}\n"
            )
            release_claim(claim_path)
            return 3
        sys.stderr.write(
            f"{worker_label} claimed shard {target_id}.\n"
        )
        rc = _process_shard(
            args, base, state, target_id, flag,
            worker_label=worker_label,
        )
        # Claim-file release policy depends on outcome:
        #   * Success (rc=0): release. The shard is done and other
        #     workers shouldn't see an active claim.
        #   * Failed (rc=4): release. The shard is in state=failed
        #     in state.json; an operator can rerun after fixing
        #     the underlying cause without needing to also
        #     hand-delete the claim file.
        #   * SIGTERM checkpoint (rc=5): KEEP the claim file. Per
        #     spec §2.4 only the original host may resume; the
        #     claim file with this host's identifier is how the
        #     resume path knows it owns the work. If we released
        #     it, another host could pick up the partially-scored
        #     shard, which the spec explicitly forbids.
        if rc != 5:
            release_claim(claim_path)
        if rc == 5:
            # Clean SIGTERM checkpoint exit. Worker shuts down so
            # ops/launchd can examine state. Returning rc=0 here
            # signals "everything's fine, just stopped."
            sys.stderr.write(
                f"{worker_label} checkpointed mid-shard and is "
                f"exiting cleanly. {n_completed} shard(s) completed "
                f"this session; one shard left in "
                f"claimed_pending_resume.\n"
            )
            return 0
        if rc != 0:
            return rc
        n_completed += 1
    sys.stderr.write(
        f"{worker_label} exiting. {n_completed} shard(s) completed.\n"
    )
    return 0


def _select_next_shard(
    state: dict[str, Any], base: Path, run_id: str,
) -> tuple[str | None, str]:
    """Pick the next shard to claim, preferring resumable shards
    owned by this host. Returns ``(shard_id, expected_state)`` or
    ``(None, "pending")`` if there's nothing to claim.

    Resumable shards take priority because their cache is partially
    populated; finishing them is cheaper than starting fresh. We
    only resume our own host's shards in v1.44.1.A (sweep-stale
    in v1.44.1.B will release dead-host claims).

    Pending shards with an existing claim file are skipped — another
    worker already owns that shard, even if state.json hasn't yet
    caught up to reflect the claim. Without this filter, two workers
    racing for the same shard could land in an infinite loop: one
    wins the claim file but hasn't updated state.json yet; the
    loser sees state.json still showing the shard as pending and
    retries the same shard forever. Filtering by claim-file presence
    breaks the loop and lets the loser move to the next shard.
    """
    pending = pending_shard_ids(state)
    resumable = resumable_shard_ids(state)
    my_host = _host()
    # Resumable shards owned by this host first.
    for sid in resumable:
        shard = state["shards"][sid]
        if shard.get("claimed_by_host") == my_host:
            return sid, "claimed_pending_resume"
    # Pending shards with no existing claim file (race-safe
    # candidate selection).
    for sid in pending:
        claim_path = shard_claim_path(base, run_id, sid)
        if not claim_path.exists():
            return sid, "pending"
    return None, "pending"


def _process_shard(
    args: argparse.Namespace,
    base: Path,
    state: dict[str, Any],
    shard_id: str,
    flag: _SigtermFlag,
    *,
    worker_label: str = "worker-0",
) -> int:
    """Score one shard, persist its cache, and mark done. Returns
    a process exit code: 0 = success, 4 = scoring error.

    State.json updates (mark_failed on error, mark_done on success)
    go through ``state_update_lock`` so concurrent workers
    serialize cleanly on the read-modify-write window.
    """
    sp = state_path(base, args.run_id)
    mp = shard_manifest_path(base, args.run_id, shard_id)
    cp = shard_cache_path(base, args.run_id, shard_id)
    cp.parent.mkdir(parents=True, exist_ok=True)
    sys.stderr.write(
        f"  {worker_label} scoring shard {shard_id} ({mp})...\n"
    )
    try:
        result = DEFAULT_SCORER(
            mp,
            fpr_target=state.get("fpr_target", 0.01),
            tier1=state.get("tier1", True),
            tier2=state.get("tier2", False),
            tier3=state.get("tier3", False),
            use=getattr(args, "use", "validation"),
            cache_path=cp,
            flush_every=DEFAULT_FLUSH_EVERY,
            sigterm_event=flag,
        )
    except SigtermInterrupt as exc:
        # Mid-shard SIGTERM checkpoint (v1.44.1.B contract). The
        # scorer wrote whatever it could before raising. We mark
        # the shard ``claimed_pending_resume`` so the original host
        # can pick it back up on the next ``work`` invocation —
        # per spec §2.4 only the original host may resume. The
        # claim file is intentionally NOT released; it's how the
        # resume path identifies eligible-resumer ownership.
        sys.stderr.write(
            f"  {worker_label} shard {shard_id} interrupted: "
            f"{exc.n_entries_flushed} of "
            f"{exc.n_entries_total or '?'} entries flushed. "
            f"Marking claimed_pending_resume.\n"
        )
        with state_update_lock(sp):
            state = read_state(sp)
            state = mark_pending_resume(
                state, shard_id,
                n_entries_flushed=exc.n_entries_flushed,
                n_entries_total=(
                    exc.n_entries_total
                    if exc.n_entries_total is not None
                    else exc.n_entries_flushed
                ),
            )
            write_state(sp, state)
        # Signal up the call stack: this is a clean checkpoint, not
        # a failure. Returning a distinguished code lets the worker
        # loop exit cleanly and the operator/launchd can resume.
        return 5
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(
            f"  {worker_label} scoring shard {shard_id} failed: "
            f"{type(exc).__name__}: {exc}\n"
        )
        with state_update_lock(sp):
            state = read_state(sp)
            state = mark_failed(
                state, shard_id,
                failure_reason=f"{type(exc).__name__}: {exc}",
            )
            write_state(sp, state)
        return 4
    # Some scorers (the real path) write their own cache; if not,
    # we write the records list ourselves.
    if not cp.exists():
        records = result.get("records") or []
        meta = result.get("meta") or {}
        with cp.open("w", encoding="utf-8") as fh:
            json.dump(
                {"records": records, "meta": meta},
                fh, sort_keys=True,
            )
    cache_sha = sha256_file(cp)
    n_entries = len(result.get("records") or [])
    with state_update_lock(sp):
        state = read_state(sp)
        state = mark_done(
            state, shard_id,
            n_entries=n_entries,
            cache_path=(
                str(cp.relative_to(base))
                if cp.is_relative_to(base) else str(cp)
            ),
            cache_sha256=cache_sha,
        )
        write_state(sp, state)
    sys.stderr.write(
        f"  {worker_label} shard {shard_id} done ({n_entries} records, "
        f"sha={cache_sha[:16]}...).\n"
    )
    return 0


# --------------- aggregate subcommand ----------------------------


def cmd_aggregate(args: argparse.Namespace) -> int:
    """Combine all shard caches and run derive_threshold_from_records
    per signal. Refuses to run unless all shards are done."""
    base = Path(args.base_dir).expanduser()
    sp = state_path(base, args.run_id)
    if not sp.exists():
        sys.stderr.write(f"State file not found: {sp}\n")
        return 2
    state = read_state(sp)
    shards = state.get("shards", {})
    not_done = [
        sid for sid, sh in shards.items() if sh.get("state") != "done"
    ]
    if not_done and not args.allow_partial:
        sys.stderr.write(
            f"Cannot aggregate: {len(not_done)} shard(s) not done "
            f"(states: "
            f"{sorted({sh.get('state') for sid, sh in shards.items() if sh.get('state') != 'done'})}). "
            f"Pass --allow-partial to aggregate available shards anyway.\n"
        )
        return 2
    all_records: list[dict[str, Any]] = []
    meta_list: list[dict[str, Any]] = []
    contributing: list[str] = []
    missing_done_shards: list[tuple[str, Path]] = []
    tampered_done_shards: list[tuple[str, str, str]] = []
    for sid in sorted(shards.keys()):
        sh = shards[sid]
        if sh.get("state") != "done":
            continue
        cp = base / sh.get("cache_path", "")
        if not cp.exists():
            # Some shards may have absolute cache_path.
            cp = Path(sh.get("cache_path", ""))
        if not cp.exists():
            # Done shards whose cache file is missing are a state-
            # integrity failure: state.json says the shard completed,
            # but the artifact it produced is gone. Under --allow-
            # partial, we tolerate this and report it. Without
            # --allow-partial, refusing to produce a silently-
            # incomplete aggregate is the safer default — the
            # alternative is a "complete" survey artifact whose
            # n_records and per-signal sweeps don't match what
            # state.json claims.
            missing_done_shards.append((sid, cp))
            continue
        # Integrity check: compare the on-disk cache's SHA-256 to the
        # value recorded in state.json when the shard was marked
        # done. The `verify` subcommand exists to do this on demand,
        # but `aggregate` is the artifact-producing command — the
        # one that creates the survey JSON consumers act on. It
        # must not depend on a separate manual `verify` step to
        # avoid producing a survey from tampered or stale caches.
        # If recorded_sha is missing (older state files), skip the
        # check rather than fail; the missing-cache check above
        # already catches the most common integrity failure.
        recorded_sha = sh.get("cache_sha256", "")
        if recorded_sha:
            actual_sha = sha256_file(cp)
            if actual_sha != recorded_sha:
                tampered_done_shards.append((sid, recorded_sha, actual_sha))
                continue
        with cp.open("r", encoding="utf-8") as fh:
            cache = json.load(fh)
        all_records.extend(cache.get("records") or [])
        if cache.get("meta"):
            meta_list.append(cache["meta"])
        contributing.append(sid)
    integrity_failures = (
        len(missing_done_shards) + len(tampered_done_shards)
    )
    if integrity_failures and not args.allow_partial:
        sys.stderr.write(
            f"Cannot aggregate: {integrity_failures} done shard(s) "
            f"have integrity failures.\n"
        )
        if missing_done_shards:
            sys.stderr.write("\n  Missing cache files:\n")
            for sid, cp in missing_done_shards:
                sys.stderr.write(f"    shard {sid}: cache missing at {cp}\n")
        if tampered_done_shards:
            sys.stderr.write("\n  Cache hash mismatches (tampered or stale):\n")
            for sid, recorded, actual in tampered_done_shards:
                sys.stderr.write(
                    f"    shard {sid}: recorded {recorded[:16]}..., "
                    f"actual {actual[:16]}...\n"
                )
        sys.stderr.write(
            "\nPass --allow-partial to aggregate the surviving shards "
            "anyway, or rerun `shard_runner work` to regenerate the "
            "affected caches. Without --allow-partial, aggregate refuses "
            "to produce a survey artifact when state.json's integrity "
            "claims cannot be verified.\n"
        )
        return 2
    if missing_done_shards or tampered_done_shards:
        sys.stderr.write(
            f"  (continuing with --allow-partial: "
            f"{len(missing_done_shards)} missing-cache shard(s) and "
            f"{len(tampered_done_shards)} tampered-cache shard(s) "
            f"skipped)\n"
        )
    sys.stderr.write(
        f"Aggregated {len(all_records)} records across "
        f"{len(contributing)} shard(s).\n"
    )
    # Build the aggregated payload. Per-signal threshold sweep
    # happens via calibrate_thresholds.derive_threshold_from_records,
    # which we import lazily (same reason as the scorer path).
    if not args.no_derive:
        try:
            import calibrate_thresholds as ct  # type: ignore
        except ImportError as exc:
            sys.stderr.write(
                f"  could not import calibrate_thresholds for "
                f"per-signal derivation: {exc}. Aggregating records "
                f"only.\n"
            )
            ct = None
    else:
        ct = None
    per_signal: dict[str, Any] = {}
    if ct is not None and all_records:
        merged_meta = meta_list[0] if meta_list else {}
        from argparse import Namespace
        run_id = state.get("run_id", "sharded_run")
        fpr_target = state.get("fpr_target", 0.01)
        iso_date = _dt.date.today().isoformat()
        for sig_name in sorted(ct.COMPRESSION_HEURISTICS.keys()):
            try:
                # `derive_threshold_from_records` reads `args.slug`,
                # `args.use`, and `args.notes` in addition to the
                # signal / manifest / fpr / bootstrap fields. A
                # minimal Namespace that omits any of those raises
                # AttributeError mid-derivation and gets caught
                # below as a per-signal error rather than producing
                # a real entry. Populate the full contract here.
                ns = Namespace(
                    signal=sig_name,
                    manifest=str(state.get("source_manifest_path") or ""),
                    fpr_target=fpr_target,
                    bootstrap_seed=42,
                    bootstrap_resamples=2000,
                    bootstrap_confidence=0.95,
                    slug=f"sharded_{run_id}_{sig_name}_fpr{fpr_target}_{iso_date}",
                    use=getattr(args, "use", "validation"),
                    notes=(
                        f"Sharded calibration run {run_id!r}. "
                        f"Aggregated from {len(contributing)} shard "
                        f"cache(s). See sharded-run state.json for "
                        f"shard-level metadata."
                    ),
                )
                entry = ct.derive_threshold_from_records(
                    all_records, args=ns, scoring_meta=merged_meta,
                )
                per_signal[sig_name] = entry
            except SystemExit as exc:
                per_signal[sig_name] = {
                    "error": f"derive_threshold failed: {exc}",
                }
            except Exception as exc:  # noqa: BLE001
                per_signal[sig_name] = {
                    "error": f"{type(exc).__name__}: {exc}",
                }
    payload = {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "tool_version": SCRIPT_VERSION,
        "run_id": state.get("run_id"),
        "source_manifest_sha256": state.get("source_manifest_sha256"),
        "fpr_target": state.get("fpr_target"),
        "n_records": len(all_records),
        "n_shards_contributed": len(contributing),
        "contributing_shards": contributing,
        "embedding_model": state.get("embedding_model"),
        "embedding_revision": state.get("embedding_revision"),
        "per_signal": per_signal,
        "aggregated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    out_path = Path(args.out).expanduser() if args.out else None
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        sys.stderr.write(f"Aggregated survey written: {out_path}\n")
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


# --------------- verify subcommand -------------------------------


def cmd_verify(args: argparse.Namespace) -> int:
    """Sanity-check shard caches against the SHA-256 hashes recorded
    in state.json. Reports per-shard pass/fail and exits non-zero
    if any shard fails."""
    base = Path(args.base_dir).expanduser()
    sp = state_path(base, args.run_id)
    if not sp.exists():
        sys.stderr.write(f"State file not found: {sp}\n")
        return 2
    state = read_state(sp)
    shards = state.get("shards", {})
    failures: list[str] = []
    for sid in sorted(shards.keys()):
        sh = shards[sid]
        if sh.get("state") != "done":
            continue
        rec_sha = sh.get("cache_sha256")
        cp = base / sh.get("cache_path", "")
        if not cp.exists():
            cp = Path(sh.get("cache_path", ""))
        if not cp.exists():
            failures.append(f"{sid}: cache missing at {cp}")
            continue
        actual = sha256_file(cp)
        if actual != rec_sha:
            failures.append(
                f"{sid}: hash mismatch (recorded {rec_sha[:16]}..., "
                f"actual {actual[:16]}...)"
            )
        else:
            sys.stderr.write(f"  {sid}: OK\n")
    if failures:
        sys.stderr.write(f"\nVerify FAILED: {len(failures)} shard(s)\n")
        for f in failures:
            sys.stderr.write(f"  - {f}\n")
        return 4
    sys.stderr.write(f"\nVerify OK across {sum(1 for sh in shards.values() if sh.get('state') == 'done')} done shard(s).\n")
    return 0


# --------------- pause-all subcommand (v1.44.1.B) ----------------


def cmd_pause_all(args: argparse.Namespace) -> int:
    """Write or clear the pause marker for a run.

    Without ``--clear``: writes ``.pause`` in the run directory.
    Workers detect it between shards and exit cleanly. Half-scored
    shards remain in their current state (claimed or
    claimed_pending_resume); the pause does NOT trigger mid-shard
    checkpointing. That's by design: ``pause-all`` is the
    cooperative-shutdown path, not the emergency-stop path.

    With ``--clear``: removes the marker. Returns 0 if a marker
    was cleared, 1 if there was nothing to clear (informational,
    not an error — useful for idempotent ops scripts).

    The pause marker survives across worker restarts. An operator
    invoking ``shard_runner work`` while paused will get the
    "pause marker present; exiting cleanly" message immediately
    and return rc=0 without doing any work. The marker must be
    explicitly cleared to resume.
    """
    base = Path(args.base_dir).expanduser()
    sp = state_path(base, args.run_id)
    if not sp.exists():
        sys.stderr.write(f"State file not found: {sp}\n")
        return 2
    if args.clear:
        cleared = clear_pause_marker(base, args.run_id)
        if cleared:
            sys.stderr.write(
                f"Pause marker cleared for run {args.run_id!r}. "
                f"Next `shard_runner work` invocation will resume.\n"
            )
            return 0
        sys.stderr.write(
            f"No pause marker to clear for run {args.run_id!r}.\n"
        )
        return 1
    marker = write_pause_marker(base, args.run_id, reason=args.reason)
    sys.stderr.write(
        f"Pause marker written: {marker}\n"
        f"Running workers will exit cleanly after their current shard. "
        f"Clear with `shard_runner pause-all --clear --run-id "
        f"{args.run_id}` to resume.\n"
    )
    return 0


# --------------- terminate-all / kill-all subcommands ------------


def _signal_active_workers(
    base: Path, run_id: str, sig: int, *, label: str,
) -> tuple[int, int, int]:
    """Send ``sig`` to every distinct worker pid that holds a claim
    on this run's local host.

    Returns ``(n_signaled, n_skipped_remote, n_dead)``. We only
    signal pids whose claim file's recorded host matches the
    local host — cross-host signaling isn't possible from POSIX
    user-space without out-of-band mechanisms (ssh, etc.), so
    ``terminate-all`` on host A only stops host A's workers.
    Operators with a multi-host run must invoke the command on
    each host.

    Skips dead pids (already-exited workers don't need signaling
    and OS-level pid reuse means signaling them risks killing an
    unrelated process). Skips remote-host claims with a warning.
    """
    n_signaled = 0
    n_skipped_remote = 0
    n_dead = 0
    sd = shards_dir(base, run_id)
    if not sd.exists():
        return 0, 0, 0
    seen_pids: set[int] = set()
    local_host = _host()
    for shard_dir in sorted(sd.iterdir()):
        if not shard_dir.is_dir():
            continue
        claim_file = shard_dir / ".claim"
        claim = read_claim_file(claim_file)
        if claim is None:
            continue
        claim_host = claim.get("host")
        claim_pid = claim.get("pid")
        if not isinstance(claim_pid, int):
            continue
        if claim_host != local_host:
            n_skipped_remote += 1
            sys.stderr.write(
                f"  shard {shard_dir.name}: claim on remote host "
                f"{claim_host!r} (pid {claim_pid}); cannot {label} from "
                f"{local_host!r}. Run `{label}` on {claim_host!r} to "
                f"reach it.\n"
            )
            continue
        if claim_pid in seen_pids:
            continue
        seen_pids.add(claim_pid)
        if not pid_alive(claim_pid):
            n_dead += 1
            sys.stderr.write(
                f"  shard {shard_dir.name}: pid {claim_pid} already "
                f"dead; skipping. Run `sweep-stale` to release the "
                f"claim.\n"
            )
            continue
        try:
            os.kill(claim_pid, sig)
            n_signaled += 1
            sys.stderr.write(
                f"  shard {shard_dir.name}: sent {label} to pid "
                f"{claim_pid} (host {claim_host}).\n"
            )
        except ProcessLookupError:
            n_dead += 1
            sys.stderr.write(
                f"  shard {shard_dir.name}: pid {claim_pid} exited "
                f"between liveness check and signal send.\n"
            )
        except PermissionError as exc:
            sys.stderr.write(
                f"  shard {shard_dir.name}: permission denied "
                f"signaling pid {claim_pid}: {exc}. Retry as the "
                f"owning user.\n"
            )
    return n_signaled, n_skipped_remote, n_dead


def cmd_terminate_all(args: argparse.Namespace) -> int:
    """SIGTERM every active worker on this host that holds a claim
    on this run.

    SIGTERM goes through the worker's signal handler, which:
      * Sets the between-shard exit flag (so the worker stops after
        finishing its current shard), OR
      * If the scorer opted into the SigtermInterrupt contract,
        triggers a mid-shard checkpoint that flushes partial state.

    Returns 0 if at least one worker was signaled, 1 if no workers
    were found to signal (informational — empty queues are not
    errors), 2 if state file missing.
    """
    base = Path(args.base_dir).expanduser()
    sp = state_path(base, args.run_id)
    if not sp.exists():
        sys.stderr.write(f"State file not found: {sp}\n")
        return 2
    sys.stderr.write(
        f"Sending SIGTERM to active workers on {_host()!r}...\n"
    )
    n_sig, n_remote, n_dead = _signal_active_workers(
        base, args.run_id, signal.SIGTERM, label="terminate-all",
    )
    sys.stderr.write(
        f"\nterminate-all summary: {n_sig} pid(s) signaled, "
        f"{n_remote} remote-host claim(s) skipped, "
        f"{n_dead} dead pid(s) skipped.\n"
    )
    return 0 if n_sig > 0 else 1


def cmd_kill_all(args: argparse.Namespace) -> int:
    """SIGKILL every active worker on this host that holds a claim
    on this run.

    SIGKILL bypasses signal handlers — workers cannot flush
    partial state. Shards in progress will leave their cache files
    in whatever state the worker had reached, and state.json will
    still show them as ``claimed``. Recovery requires
    ``sweep-stale`` to release the claims, then a worker re-claim
    that starts from scratch (the partial cache, if any, is
    ignored).

    This is the last-resort path. Operators should try
    ``terminate-all`` (cooperative) first and only escalate to
    ``kill-all`` if workers fail to exit within a reasonable
    timeout.
    """
    base = Path(args.base_dir).expanduser()
    sp = state_path(base, args.run_id)
    if not sp.exists():
        sys.stderr.write(f"State file not found: {sp}\n")
        return 2
    sys.stderr.write(
        f"Sending SIGKILL to active workers on {_host()!r} "
        f"(last-resort path; partial state will NOT be flushed)...\n"
    )
    n_sig, n_remote, n_dead = _signal_active_workers(
        base, args.run_id, signal.SIGKILL, label="kill-all",
    )
    sys.stderr.write(
        f"\nkill-all summary: {n_sig} pid(s) killed, "
        f"{n_remote} remote-host claim(s) skipped, "
        f"{n_dead} dead pid(s) skipped.\n"
        f"Run `sweep-stale` next to release any abandoned claims.\n"
    )
    return 0 if n_sig > 0 else 1


# --------------- sweep-stale subcommand --------------------------


def cmd_sweep_stale(args: argparse.Namespace) -> int:
    """Walk the run's shard directories, release claim files whose
    owning pid is dead AND whose claim age exceeds the threshold.

    Why both conditions? Either alone is insufficient:

      * "pid is dead" alone: a worker that legitimately just
        restarted (e.g., crash-then-immediate-relaunch) might have
        a recorded pid that is no longer alive but a new pid that
        is actively scoring the same shard. Releasing the claim
        would cause the new pid to lose ownership mid-work.
        Requiring the claim age to exceed a configurable threshold
        (default 6 hours) defeats this race.
      * "claim age > threshold" alone: a long-running shard might
        legitimately hold a claim for more than 6 hours without
        anything being wrong. Liveness-checking the pid (via
        ``os.kill(pid, 0)``) confirms the worker is actually gone
        before we release the claim.

    Cross-host claims are NEVER swept by this subcommand — we
    can't liveness-check pids on a different host from POSIX. An
    operator running ``sweep-stale`` on host A leaves host B's
    claims alone (even if they're stale). Multi-host stale-sweep
    is documented in v1.44.2's multi-machine runbook.

    By default, ``claimed_pending_resume`` shards are NOT swept
    even if their pid is dead — per spec §2.4 only the original
    host may resume, so keeping the claim file alive (even with a
    dead pid) preserves the eligibility check. Pass
    ``--include-resume`` to also release these (rare; usually only
    needed when the original host is permanently gone, e.g.,
    decommissioned laptop).

    Also releases the per-shard state in state.json: a swept
    ``claimed`` shard returns to ``pending``. A swept
    ``claimed_pending_resume`` shard (only with --include-resume)
    also returns to ``pending`` and its partial-progress fields
    are cleared.

    With ``--dry-run``: reports what would be released without
    actually releasing anything. Returns 0.
    """
    base = Path(args.base_dir).expanduser()
    sp = state_path(base, args.run_id)
    if not sp.exists():
        sys.stderr.write(f"State file not found: {sp}\n")
        return 2
    threshold_hours = float(
        getattr(args, "stale_hours", DEFAULT_STALE_THRESHOLD_HOURS)
        or DEFAULT_STALE_THRESHOLD_HOURS
    )
    threshold_seconds = threshold_hours * 3600.0
    include_resume = bool(getattr(args, "include_resume", False))
    dry_run = bool(getattr(args, "dry_run", False))
    now = _dt.datetime.now(_dt.timezone.utc)
    local_host = _host()
    sd = shards_dir(base, args.run_id)
    if not sd.exists():
        sys.stderr.write(f"No shard directory at {sd}; nothing to sweep.\n")
        return 0
    swept_ids: list[str] = []
    skipped_alive: list[str] = []
    skipped_remote: list[str] = []
    skipped_young: list[tuple[str, float]] = []
    skipped_resume: list[str] = []
    state = read_state(sp)
    for shard_dir in sorted(sd.iterdir()):
        if not shard_dir.is_dir():
            continue
        sid = shard_dir.name
        claim_file = shard_dir / ".claim"
        claim = read_claim_file(claim_file)
        if claim is None:
            continue
        claim_host = claim.get("host")
        claim_pid = claim.get("pid")
        claimed_at_iso = claim.get("claimed_at")
        if claim_host != local_host:
            skipped_remote.append(sid)
            continue
        if not isinstance(claim_pid, int):
            # Malformed claim file; safer to skip than to release.
            continue
        if pid_alive(claim_pid):
            skipped_alive.append(sid)
            continue
        # Pid is dead. Now check age.
        age_seconds = float("inf")
        if claimed_at_iso:
            try:
                claimed_at = _dt.datetime.fromisoformat(claimed_at_iso)
                if claimed_at.tzinfo is None:
                    claimed_at = claimed_at.replace(
                        tzinfo=_dt.timezone.utc,
                    )
                age_seconds = (now - claimed_at).total_seconds()
            except ValueError:
                pass
        if age_seconds < threshold_seconds:
            skipped_young.append((sid, age_seconds / 3600.0))
            continue
        # Honor the resume-protection rule unless --include-resume.
        shard_state = state.get("shards", {}).get(sid, {}).get("state")
        if shard_state == "claimed_pending_resume" and not include_resume:
            skipped_resume.append(sid)
            continue
        # All checks passed: release.
        swept_ids.append(sid)
        if not dry_run:
            try:
                claim_file.unlink()
            except FileNotFoundError:
                pass
            # Restore the shard's state-file entry to pending so a
            # worker can re-claim it. Clear the partial-progress
            # fields if we're sweeping a claimed_pending_resume
            # shard (operator opted in via --include-resume).
            with state_update_lock(sp):
                state = read_state(sp)
                shard = state.get("shards", {}).get(sid)
                if shard is not None:
                    shard["state"] = "pending"
                    for key in (
                        "claimed_by_host", "claimed_by_pid", "claimed_at",
                        "n_entries_flushed", "n_entries_total",
                        "last_flush_at",
                    ):
                        shard.pop(key, None)
                    write_state(sp, state)
    label = "Would release" if dry_run else "Released"
    sys.stderr.write(
        f"sweep-stale on {local_host!r}: {label} {len(swept_ids)} "
        f"claim(s) ({threshold_hours:.1f} h threshold; "
        f"--include-resume={include_resume}; dry-run={dry_run}).\n"
    )
    if swept_ids:
        for sid in swept_ids:
            sys.stderr.write(f"  {sid}: claim released\n")
    if skipped_alive:
        sys.stderr.write(
            f"  Skipped {len(skipped_alive)} claim(s): owning pid still alive.\n"
        )
    if skipped_remote:
        sys.stderr.write(
            f"  Skipped {len(skipped_remote)} claim(s): on remote host.\n"
        )
    if skipped_young:
        sys.stderr.write(
            f"  Skipped {len(skipped_young)} claim(s): dead pid but "
            f"age below threshold (need ≥{threshold_hours:.1f} h).\n"
        )
    if skipped_resume:
        sys.stderr.write(
            f"  Skipped {len(skipped_resume)} claimed_pending_resume "
            f"shard(s): pass --include-resume to also release these.\n"
        )
    return 0


# --------------- status subcommand -------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    base = Path(args.base_dir).expanduser()
    sp = state_path(base, args.run_id)
    if not sp.exists():
        sys.stderr.write(f"State file not found: {sp}\n")
        return 2
    state = read_state(sp)
    summary = status_summary(state)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    print(f"Run: {summary['run_id']}")
    print(f"Created: {summary['created_at']}")
    print(f"Shards: {summary['shard_count']}")
    counts = summary["counts"]
    for state_name in (
        "pending", "claimed", "claimed_pending_resume", "done", "failed",
    ):
        print(f"  {state_name}: {counts.get(state_name, 0)}")
    print(f"Fraction done: {summary['fraction_done']:.1%}")
    return 0


# --------------- CLI ---------------------------------------------


def _default_base() -> Path:
    """Default base directory: $SETEC_BASELINES_DIR if set,
    otherwise the user's documented baseline path. Mirrors
    ``acquisition_core.resolve_baselines_dir`` but with a simpler
    implementation here so tests don't need acquisition deps."""
    env_val = os.environ.get("SETEC_BASELINES_DIR")
    if env_val:
        return Path(env_val).expanduser()
    return Path.home() / "Documents" / "ai-prose-baselines-private"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shard_runner",
        description=(
            "Sharded-calibration orchestrator (v1.44.0 core). Splits "
            "labeled-corpus manifests into stratified shards, runs "
            "single-worker scoring with SIGTERM-safe checkpointing, "
            "and aggregates per-shard caches into a unified survey."
        ),
    )
    p.add_argument(
        "--base-dir",
        type=str,
        default=str(_default_base()),
        help=(
            "base directory for run state and shard caches "
            "(default: %(default)s; honors $SETEC_BASELINES_DIR)"
        ),
    )
    sub = p.add_subparsers(dest="subcommand", required=True)

    # shard
    p_shard = sub.add_parser(
        "shard",
        help="split a source manifest into stratified shards",
    )
    p_shard.add_argument("--source-manifest", required=True, type=str)
    p_shard.add_argument("--run-id", required=True, type=str)
    p_shard.add_argument("--shard-size", type=int, default=100000)
    p_shard.add_argument("--shard-count", type=int, default=None)
    p_shard.add_argument("--stratify", type=str, default="register,ai_status")
    p_shard.add_argument("--shuffle-seed", type=int, default=42)
    p_shard.add_argument("--fpr-target", type=float, default=0.01)
    p_shard.add_argument("--tier1", action="store_true", default=True)
    p_shard.add_argument("--no-tier1", dest="tier1", action="store_false")
    p_shard.add_argument("--tier2", action="store_true", default=False)
    p_shard.add_argument("--no-tier2", dest="tier2", action="store_false")
    p_shard.add_argument("--tier3", action="store_true", default=False)
    p_shard.add_argument("--no-tier3", dest="tier3", action="store_false")
    p_shard.add_argument("--embedding-model", type=str, default=None)
    p_shard.add_argument("--embedding-revision", type=str, default=None)
    p_shard.add_argument("--force", action="store_true", default=False)
    p_shard.set_defaults(func=cmd_shard)

    # work
    p_work = sub.add_parser(
        "work",
        help="claim and score pending shards (single worker)",
    )
    p_work.add_argument("--run-id", required=True, type=str)
    p_work.add_argument("--use", type=str, default="validation")
    p_work.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "number of concurrent worker subprocesses (v1.44.1+). "
            "Default 1 (single-worker, same as v1.44.0). Workers "
            "coordinate via atomic per-shard claim files at "
            "shards/<id>/.claim and a state-update lock on "
            "state.json.lock. Choose a value that fits the host's "
            "CPU and memory budget; the spec recommends 4-8 on a "
            "16 GB / 8-core machine for Tier 1 surveys."
        ),
    )
    p_work.add_argument(
        "--time-window",
        type=str,
        default=None,
        metavar="HH:MM-HH:MM",
        help=(
            "schedule gate (v1.44.1.B): worker exits cleanly between "
            "shards when the current local time is outside the window. "
            "Use 23:00-06:00 for a nightly run that crosses midnight. "
            "A shard in progress finishes (potentially past the "
            "deadline) per spec §2.5. Default: no time window (worker "
            "runs until the queue is empty or it receives SIGTERM)."
        ),
    )
    p_work.set_defaults(func=cmd_work)

    # pause-all
    p_pause = sub.add_parser(
        "pause-all",
        help=(
            "write a pause marker so running workers exit cleanly "
            "between shards"
        ),
    )
    p_pause.add_argument("--run-id", required=True, type=str)
    p_pause.add_argument(
        "--clear", action="store_true", default=False,
        help="remove the pause marker (resume)",
    )
    p_pause.add_argument(
        "--reason", type=str, default=None,
        help="optional reason recorded in the marker file",
    )
    p_pause.set_defaults(func=cmd_pause_all)

    # terminate-all
    p_term = sub.add_parser(
        "terminate-all",
        help=(
            "SIGTERM every active worker on this host that holds a "
            "claim on this run"
        ),
    )
    p_term.add_argument("--run-id", required=True, type=str)
    p_term.set_defaults(func=cmd_terminate_all)

    # kill-all
    p_kill = sub.add_parser(
        "kill-all",
        help=(
            "SIGKILL every active worker on this host (last resort; "
            "partial state will NOT be flushed)"
        ),
    )
    p_kill.add_argument("--run-id", required=True, type=str)
    p_kill.set_defaults(func=cmd_kill_all)

    # sweep-stale
    p_sweep = sub.add_parser(
        "sweep-stale",
        help=(
            "release claim files whose pid is dead AND age exceeds "
            "the stale threshold (default 6 h)"
        ),
    )
    p_sweep.add_argument("--run-id", required=True, type=str)
    p_sweep.add_argument(
        "--stale-hours", type=float,
        default=DEFAULT_STALE_THRESHOLD_HOURS,
        help=(
            f"age threshold in hours (default "
            f"{DEFAULT_STALE_THRESHOLD_HOURS})"
        ),
    )
    p_sweep.add_argument(
        "--include-resume", action="store_true", default=False,
        help=(
            "also release claimed_pending_resume shards (rare; "
            "use when the original host is permanently gone)"
        ),
    )
    p_sweep.add_argument(
        "--dry-run", action="store_true", default=False,
        help="report what would be released without releasing",
    )
    p_sweep.set_defaults(func=cmd_sweep_stale)

    # aggregate
    p_agg = sub.add_parser(
        "aggregate",
        help="combine shard caches + per-signal threshold sweep",
    )
    p_agg.add_argument("--run-id", required=True, type=str)
    p_agg.add_argument("--out", type=str, default=None)
    p_agg.add_argument("--allow-partial", action="store_true", default=False)
    p_agg.add_argument("--no-derive", action="store_true", default=False)
    p_agg.set_defaults(func=cmd_aggregate)

    # verify
    p_ver = sub.add_parser(
        "verify",
        help="check shard cache hashes against state.json",
    )
    p_ver.add_argument("--run-id", required=True, type=str)
    p_ver.set_defaults(func=cmd_verify)

    # status
    p_status = sub.add_parser(
        "status",
        help="print state-file summary",
    )
    p_status.add_argument("--run-id", required=True, type=str)
    p_status.add_argument("--json", action="store_true", default=False)
    p_status.set_defaults(func=cmd_status)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
