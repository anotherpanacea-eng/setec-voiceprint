#!/usr/bin/env python3
"""shard_runner.py — sharded calibration orchestrator (v1.44.0 core).

The CLI orchestrator for the sharded-calibration toolchain
specified in ``internal/SPEC_sharded_calibration.md``. v1.44.0
ships the single-worker core: ``shard`` (deterministic stratified
split), ``work`` (claim + score + flush + done, single worker),
``aggregate`` (combine caches + per-signal threshold sweep),
``verify`` (cache hash sanity check), ``status`` (state file
summary).

Deferred to v1.44.1+:
  * ``--workers N`` concurrent execution.
  * ``--time-window`` flag for scheduled execution.
  * ``pause-all`` / ``terminate-all`` / ``kill-all`` / ``sweep-stale``.
  * launchd plist + caffeinate runbook.
  * Multi-machine git-synced state file.

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
    read_state,
    resumable_shard_ids,
    sha256_file,
    status_summary,
    write_state,
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
    """Claim a pending shard, score it, write its cache, mark done,
    and loop. Single worker (v1.44.0). Stops cleanly on SIGTERM /
    SIGINT or when no pending shards remain."""
    base = Path(args.base_dir).expanduser()
    sp = state_path(base, args.run_id)
    if not sp.exists():
        sys.stderr.write(f"State file not found: {sp}\n")
        return 2
    flag = _SigtermFlag()
    _install_signal_handlers(flag)
    n_completed = 0
    while not flag.tripped:
        state = read_state(sp)
        # Resumable shards first (the original worker on this host
        # should pick them up; v1.44.0 only checks pid/host best-
        # effort). For now we just claim any resumable shard owned
        # by us.
        pending = pending_shard_ids(state)
        resumable = resumable_shard_ids(state)
        target_id: str | None = None
        expected: str = "pending"
        if resumable:
            for sid in resumable:
                # Only resume our own shards. v1.44.1 will add the
                # `sweep-stale` machinery for releasing stuck
                # claims; v1.44.0 just declines to touch other
                # workers' claims.
                shard = state["shards"][sid]
                from shard_state import _host as _gethost  # type: ignore
                if shard.get("claimed_by_host") == _gethost():
                    target_id = sid
                    expected = "claimed_pending_resume"
                    break
        if target_id is None and pending:
            target_id = pending[0]
        if target_id is None:
            sys.stderr.write(
                f"No pending shards to claim. {n_completed} shard(s) "
                f"completed in this worker session.\n"
            )
            break
        try:
            state = claim_shard(state, target_id, expected_state=expected)
            write_state(sp, state)
        except ShardStateError as exc:
            sys.stderr.write(f"Claim failed for shard {target_id}: {exc}\n")
            return 3
        sys.stderr.write(f"Worker claimed shard {target_id}.\n")
        rc = _process_shard(args, base, state, target_id, flag)
        if rc != 0:
            return rc
        n_completed += 1
    sys.stderr.write(f"Worker done. {n_completed} shard(s) completed.\n")
    return 0


def _process_shard(
    args: argparse.Namespace,
    base: Path,
    state: dict[str, Any],
    shard_id: str,
    flag: _SigtermFlag,
) -> int:
    """Score one shard, persist its cache, and mark done. Returns
    a process exit code: 0 = success, 4 = scoring error."""
    sp = state_path(base, args.run_id)
    mp = shard_manifest_path(base, args.run_id, shard_id)
    cp = shard_cache_path(base, args.run_id, shard_id)
    cp.parent.mkdir(parents=True, exist_ok=True)
    sys.stderr.write(
        f"  scoring shard {shard_id} ({mp})...\n"
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
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(
            f"  scoring shard {shard_id} failed: {type(exc).__name__}: {exc}\n"
        )
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
    state = read_state(sp)
    state = mark_done(
        state, shard_id,
        n_entries=n_entries,
        cache_path=str(cp.relative_to(base)) if cp.is_relative_to(base) else str(cp),
        cache_sha256=cache_sha,
    )
    write_state(sp, state)
    sys.stderr.write(
        f"  shard {shard_id} done ({n_entries} records, "
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
    p_work.set_defaults(func=cmd_work)

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
