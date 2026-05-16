#!/usr/bin/env python3
"""task_surfaces.py — multi-task dispatch for shard_runner.

The sharded-calibration toolchain was originally built around one
task: variance_audit calibration surveys (the
``calibration_survey`` task). v1.45.0 generalizes the orchestrator
so any per-row scoring-then-aggregation pipeline can plug into the
shard/work/aggregate machinery — sharding, atomic claims, SIGTERM
checkpointing, multi-worker coordination, git-synced state — without
re-implementing it.

Contract
========

Each task surface registers a :class:`TaskSurface` dataclass with:

  * ``name``: the ``--task`` string operators pass on the CLI.
  * ``score_shard``: a callable with the signature

        score_shard(
            *,
            shard_manifest_path: Path,
            cache_path: Path,
            sigterm_event: Any,
            flush_every: int,
            task_params: dict[str, Any],
            run_context: dict[str, Any],
        ) -> ShardResult

    Returns a :class:`ShardResult` TypedDict with ``records``
    (list), ``meta`` (dict), and ``cache_hit`` (bool). May raise
    :class:`shard_runner.SigtermInterrupt` mid-shard if it opts
    into the checkpoint contract; the orchestrator catches that
    and marks the shard ``claimed_pending_resume``.

  * ``aggregate_records``: a callable with the signature

        aggregate_records(
            *,
            all_records: list[dict[str, Any]],
            meta_list: list[dict[str, Any]],
            contributing_shards: list[str],
            state: dict[str, Any],
            args: argparse.Namespace,
        ) -> dict[str, Any]

    Returns the task-specific payload that
    ``shard_runner cmd_aggregate`` writes out.

  * ``default_task_params``: dict of default param values for the
    task. ``cmd_shard`` reads these and stores them under
    ``state["task_params"]`` so workers and the aggregator can
    re-read them without re-parsing CLI flags.

  * ``required_state_fields``: list of state.json keys the task
    expects to find populated. Used by a future ``verify-task``
    sanity check; not enforced at runtime in v1.45.0.

The registry (``TASK_REGISTRY``) is module-level so registration
is a side-effect of import. Tests can register fake task surfaces
via :func:`register_task`.

Backwards compat
================

When ``state["task"]`` is missing (an older state.json from
before v1.45.0), :func:`task_for_state` returns the
``calibration_survey`` surface. Existing CLI invocations without
``--task`` continue to behave identically.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypedDict


# --------------- ShardResult typed dict --------------------------


class ShardResult(TypedDict, total=False):
    """The shape every task surface's ``score_shard`` returns.

    ``total=False`` because ``cache_hit`` is optional in practice
    (defaults to False when omitted) — the existing
    calibration_survey path always sets it, but a minimal task
    surface that doesn't cache can leave it off.
    """

    records: list[dict[str, Any]]
    meta: dict[str, Any]
    cache_hit: bool


# --------------- TaskSurface dataclass --------------------------


@dataclass
class TaskSurface:
    """A registered task surface.

    Values, not subclasses: keep the registry inversion-of-control
    minimal so tests can register fake surfaces with a one-line
    ``register_task(TaskSurface(name="x", score_shard=..., ...))``.
    """

    name: str
    score_shard: Callable[..., ShardResult]
    aggregate_records: Callable[..., dict[str, Any]]
    default_task_params: dict[str, Any] = field(default_factory=dict)
    required_state_fields: list[str] = field(default_factory=list)


# --------------- Registry ---------------------------------------


TASK_REGISTRY: dict[str, TaskSurface] = {}


def register_task(surface: TaskSurface) -> None:
    """Register a task surface in the module-level registry.

    Idempotent: re-registering the same name overwrites the
    previous entry (useful for tests that swap implementations).
    """
    TASK_REGISTRY[surface.name] = surface


def get_task(name: str) -> TaskSurface:
    """Look up a registered task surface by name. Raises
    :class:`KeyError` if the name is unknown."""
    if name not in TASK_REGISTRY:
        raise KeyError(
            f"Unknown task surface: {name!r}. "
            f"Registered: {sorted(TASK_REGISTRY)}"
        )
    return TASK_REGISTRY[name]


def task_for_state(state: dict[str, Any]) -> TaskSurface:
    """Look up the task surface this state.json belongs to.

    Backwards-compat shim: a state.json written before v1.45.0
    has no ``task`` field; we map missing -> ``calibration_survey``
    so the existing operator paths keep working.
    """
    name = state.get("task", "calibration_survey")
    return get_task(name)


def registered_task_names() -> list[str]:
    """Return registered task names in sorted order. Used by the
    CLI ``--task`` choices list."""
    return sorted(TASK_REGISTRY)


# --------------- calibration_survey scorer adapter --------------


def _score_shard_calibration_survey(
    *,
    shard_manifest_path: Path,
    cache_path: Path,
    sigterm_event: Any,
    flush_every: int,
    task_params: dict[str, Any],
    run_context: dict[str, Any],
) -> ShardResult:
    """Wrap shard_runner.DEFAULT_SCORER's existing signature into
    the new task-surface contract.

    Reads tier1/tier2/tier3/fpr_target from ``task_params`` and
    ``use`` from ``run_context``, then delegates to the existing
    ``DEFAULT_SCORER`` callable. Preserves the test hook that
    monkeypatches ``shard_runner.DEFAULT_SCORER`` to inject a
    stub.
    """
    # Lazy import so this module imports cleanly even when
    # shard_runner itself is mid-initialization (it imports us at
    # module-load time once we're registered as the default).
    import shard_runner as sr  # type: ignore

    # 1.80.0+: pass Tier 4 + model aliases through to DEFAULT_SCORER.
    # task_params carries them when the operator passed --tier4 /
    # --embedding-model / --surprisal-model to ``shard_runner shard``;
    # absent keys fall through to None / False so legacy state.json
    # files (no tier4/model keys) keep producing pre-1.80 behavior.
    # Embedding model + revision are ALSO populated from the top-level
    # state.json fields (pre-1.80 partial wiring stored them there);
    # task_params wins when both are present.
    embedding_model = task_params.get(
        "embedding_model",
        run_context.get("embedding_model"),
    )
    embedding_revision = task_params.get(
        "embedding_revision",
        run_context.get("embedding_revision"),
    )
    result = sr.DEFAULT_SCORER(
        shard_manifest_path,
        fpr_target=task_params.get("fpr_target", 0.01),
        tier1=task_params.get("tier1", True),
        tier2=task_params.get("tier2", False),
        tier3=task_params.get("tier3", False),
        use=run_context.get("use", "validation"),
        cache_path=cache_path,
        flush_every=flush_every,
        sigterm_event=sigterm_event,
        tier4=bool(task_params.get("tier4", False)),
        embedding_model=embedding_model,
        embedding_revision=embedding_revision,
        surprisal_model=task_params.get("surprisal_model"),
        surprisal_revision=task_params.get("surprisal_revision"),
    )
    # The legacy scorer returns a plain dict; normalize to
    # ShardResult shape.
    return {
        "records": result.get("records") or [],
        "meta": result.get("meta") or {},
        "cache_hit": bool(result.get("cache_hit", False)),
    }


# --------------- calibration_survey aggregator ------------------
#
# The calibration aggregator is the parallel hot path: at MAGE / RAID
# scale it dispatches per-signal bootstrap CIs across workers. The
# implementation layers four memory + concurrency defenses ("belt,
# suspenders, buttons, and zip"):
#
#   1. **Belt — pre-extract per-signal pairs in main.** The 17-signal
#      sweep needs only the (label, score) pairs per signal, not the
#      full per-record dicts. Extract once in the parent (cheap, ~1
#      pass over records); each worker receives a ~4 MB pair list
#      instead of a ~2 GB record list at MAGE scale (>99% memory
#      drop). Implemented via the ``pre_extracted_pairs`` fast-path
#      added to ``derive_threshold_from_records``.
#
#   2. **Suspenders — ``--executor thread`` (default).** A
#      ThreadPoolExecutor shares the parent's address space, so the
#      pair lists never copy or pickle. The bootstrap inner loop
#      releases the GIL during NumPy ops, so per-signal CIs overlap
#      on multiple cores. Note: GIL contention can cap effective
#      parallelism well below the requested worker count when a
#      signal spends time in pure-Python sweep / polarity-gate work.
#      ``--executor process`` is available for users who explicitly
#      want process-isolation parallelism.
#
#   3. **Buttons — ``multiprocessing.shared_memory.SharedMemory``
#      under ``--executor process``.** Even with pre-extracted pairs,
#      ProcessPoolExecutor on Windows uses spawn, which pickles the
#      task arguments per call. Shared memory keeps one physical copy
#      of each signal's pair arrays across workers; tasks pickle only
#      the SharedMemory names.
#
#   4. **Zip — adaptive worker cap from ``psutil``.** When
#      ``psutil`` is available, cap the requested worker count to
#      what fits in ``virtual_memory().available``, with conservative
#      per-worker estimates that account for parent baseline + the
#      auto-sized 500 MB bootstrap transient. Without ``psutil``,
#      honor the requested count and warn. The cap protects the
#      operator's machine from the silent OOM-and-zombie pattern
#      where a too-greedy ``--aggregate-workers`` pegs RAM, the pool
#      wedges, and progress goes silent for hours.

# Per-worker RSS estimates (GB) used by the adaptive cap. Empirical
# from the MAGE 436K-record corpus: thread workers add only the
# bootstrap transient (~500 MB); process workers add the same
# transient plus a small per-spawn baseline (~100 MB Python interp +
# imports). The numbers don't need to be tight — the cap is a safety
# net to prevent silent OOM, not a precision sizing tool.
_THREAD_PER_WORKER_RSS_GB = 0.5
_PROCESS_PER_WORKER_RSS_GB = 0.6


def _cap_workers(
    requested: int,
    executor_kind: str,
    max_rss_gb: float | None,
) -> tuple[int, str]:
    """Cap ``requested`` workers based on available RAM. Layer 4
    (Zip) of the parallel-aggregator hardening.

    Returns ``(capped_workers, reason_str)``. ``reason_str`` is a
    one-line description suitable for logging — empty string when
    the requested count is honored unchanged.

    Algorithm:
      * Base per-worker estimate: 0.5 GB (thread) / 0.6 GB (process).
      * If ``psutil`` is importable, read available RAM and floor-
        divide by the per-worker estimate to get the system cap.
      * If ``max_rss_gb`` is set, also floor-divide it by the per-
        worker estimate to get a user-imposed budget cap.
      * Final cap = ``min(requested, system_cap, budget_cap)``,
        floored at 1.
      * If ``psutil`` is not importable, honor ``requested`` and
        return a one-line warning.
    """
    if executor_kind == "thread":
        per_worker_gb = _THREAD_PER_WORKER_RSS_GB
    else:
        per_worker_gb = _PROCESS_PER_WORKER_RSS_GB

    cap = max(1, int(requested))
    reason_parts: list[str] = []

    try:
        import psutil  # type: ignore
        free_gb = psutil.virtual_memory().available / (1024 ** 3)
        sys_cap = max(1, int(free_gb / per_worker_gb))
        if sys_cap < cap:
            reason_parts.append(
                f"system: free={free_gb:.1f}GB, per-worker≈"
                f"{per_worker_gb:.1f}GB → cap={sys_cap}"
            )
            cap = sys_cap
    except ImportError:
        if cap > 1:
            reason_parts.append(
                "psutil not installed; cannot cap workers by free "
                "RAM. Install via `pip install psutil` to enable "
                "the adaptive cap. Honoring --aggregate-workers as-"
                "is; if the run goes silent, reduce manually."
            )

    if max_rss_gb is not None and max_rss_gb > 0:
        budget_cap = max(1, int(max_rss_gb / per_worker_gb))
        if budget_cap < cap:
            reason_parts.append(
                f"budget: max_rss_gb={max_rss_gb:.1f}GB → "
                f"cap={budget_cap}"
            )
            cap = budget_cap

    if cap < requested:
        reason = (
            f"capped {requested} → {cap} workers ("
            + "; ".join(reason_parts) + ")"
        )
    elif reason_parts:
        reason = "; ".join(reason_parts)
    else:
        reason = ""
    return cap, reason


def _build_signal_namespace(
    sig_name: str,
    *,
    state: dict[str, Any],
    args: argparse.Namespace,
    contributing_shards: list[str],
    fpr_target: float,
    iso_date: str,
) -> argparse.Namespace:
    """Build the argparse.Namespace ``derive_threshold_from_records``
    expects for one signal. Centralized so the serial, thread, and
    process dispatch paths produce identical Namespaces.

    Propagates ``bootstrap_engine``, ``bootstrap_chunk_size``, and
    ``bootstrap_device`` from the aggregator's CLI args (the wiring
    gap that PR #53 / #55 / #60 left unclosed in the sharded path).
    """
    run_id = state.get("run_id", "sharded_run")
    return argparse.Namespace(
        signal=sig_name,
        manifest=str(state.get("source_manifest_path") or ""),
        fpr_target=fpr_target,
        bootstrap_seed=getattr(args, "bootstrap_seed", 42),
        bootstrap_resamples=getattr(args, "bootstrap_resamples", 2000),
        bootstrap_confidence=getattr(args, "bootstrap_confidence", 0.95),
        bootstrap_engine=getattr(args, "bootstrap_engine", "loop"),
        bootstrap_chunk_size=getattr(args, "bootstrap_chunk_size", None),
        bootstrap_device=getattr(args, "bootstrap_device", None),
        slug=(
            f"sharded_{run_id}_{sig_name}_fpr"
            f"{fpr_target}_{iso_date}"
        ),
        use=getattr(args, "use", "validation"),
        notes=(
            f"Sharded calibration run {run_id!r}. "
            f"Aggregated from {len(contributing_shards)} shard "
            f"cache(s). See sharded-run state.json for shard-level "
            f"metadata."
        ),
    )


def _derive_one_from_pairs(
    sig_name: str,
    pairs: list[tuple[int, float]] | None,
    ns: argparse.Namespace,
    scoring_meta: dict[str, Any],
    records_fallback: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Per-signal worker body. Module-level so process pools can
    pickle it. Returns ``(sig_name, entry_or_error_dict)``.

    When ``pairs`` is provided, dispatches via the fast path
    (``pre_extracted_pairs`` kwarg). When ``pairs`` is None,
    dispatches via the legacy path (full ``records_fallback`` list)
    so test fixtures and back-compat callers that built bad
    ``signal_path`` specs still work.

    Wraps the standard SystemExit / Exception paths the existing
    aggregator catches per signal — a single bad signal must not
    abort the whole survey.
    """
    import calibrate_thresholds as ct  # type: ignore
    try:
        if pairs is not None:
            entry = ct.derive_threshold_from_records(
                [],  # ignored; pairs below are the input
                args=ns,
                scoring_meta=scoring_meta,
                pre_extracted_pairs=pairs,
            )
        else:
            entry = ct.derive_threshold_from_records(
                records_fallback or [],
                args=ns,
                scoring_meta=scoring_meta,
            )
        return sig_name, entry
    except SystemExit as exc:
        return sig_name, {"error": f"derive_threshold failed: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return sig_name, {
            "error": f"{type(exc).__name__}: {exc}",
        }


def _derive_one_via_shared_memory(
    sig_name: str,
    labels_shm_name: str,
    scores_shm_name: str,
    n: int,
    ns_kwargs: dict[str, Any],
    scoring_meta: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """ProcessPoolExecutor worker body that attaches to SharedMemory
    instead of receiving the pair list via pickle. Layer 3 (Buttons)
    of the parallel-aggregator hardening.

    Pickle payload per task: just the two SharedMemory names + ``n`` +
    a serializable Namespace dict — < 1 KB regardless of corpus
    size. The pair arrays are physically allocated once in the
    parent and mapped read-only in each worker.
    """
    from multiprocessing import shared_memory  # noqa: PLC0415
    import numpy as np  # type: ignore  # noqa: PLC0415

    sm_l = None
    sm_s = None
    try:
        sm_l = shared_memory.SharedMemory(name=labels_shm_name)
        sm_s = shared_memory.SharedMemory(name=scores_shm_name)
        labels = np.ndarray((n,), dtype=np.int8, buffer=sm_l.buf)
        scores = np.ndarray((n,), dtype=np.float64, buffer=sm_s.buf)
        # Materialize pairs from the shared arrays. ~4 MB at MAGE
        # scale; the cost is negligible vs. the bootstrap.
        pairs = list(zip(labels.tolist(), scores.tolist()))
    except Exception as exc:  # noqa: BLE001
        if sm_l is not None:
            sm_l.close()
        if sm_s is not None:
            sm_s.close()
        return sig_name, {
            "error": (
                f"SharedMemory attach failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        }

    try:
        ns = argparse.Namespace(**ns_kwargs, signal=sig_name)
        return _derive_one_from_pairs(sig_name, pairs, ns, scoring_meta)
    finally:
        sm_l.close()
        sm_s.close()


def _allocate_shared_pair_arrays(
    pairs: list[tuple[int, float]],
) -> tuple[Any, Any, str, str, int]:
    """Allocate two SharedMemory blocks (labels int8, scores float64)
    and copy ``pairs`` into them. Returns the two handles plus their
    names plus ``n`` so the parent can pass names to workers and
    ``unlink()`` after the pool finishes.

    Caller is responsible for ``close()`` + ``unlink()`` on the
    returned handles. On Windows the SharedMemory block is reference-
    counted; the parent's handle keeps it alive for the lifetime of
    the pool's tasks.
    """
    from multiprocessing import shared_memory  # noqa: PLC0415
    import numpy as np  # type: ignore  # noqa: PLC0415

    n = len(pairs)
    if n == 0:
        # Allocate trivial 1-byte blocks so the worker's
        # SharedMemory(name=...) call doesn't fail; worker's n=0
        # short-circuit returns the empty pair list.
        sm_l = shared_memory.SharedMemory(create=True, size=1)
        sm_s = shared_memory.SharedMemory(create=True, size=1)
        return sm_l, sm_s, sm_l.name, sm_s.name, 0
    labels_arr = np.fromiter(
        (p[0] for p in pairs), dtype=np.int8, count=n,
    )
    scores_arr = np.fromiter(
        (p[1] for p in pairs), dtype=np.float64, count=n,
    )
    sm_l = shared_memory.SharedMemory(create=True, size=labels_arr.nbytes)
    sm_s = shared_memory.SharedMemory(create=True, size=scores_arr.nbytes)
    np.ndarray(
        labels_arr.shape, dtype=labels_arr.dtype, buffer=sm_l.buf,
    )[:] = labels_arr
    np.ndarray(
        scores_arr.shape, dtype=scores_arr.dtype, buffer=sm_s.buf,
    )[:] = scores_arr
    return sm_l, sm_s, sm_l.name, sm_s.name, n


def _build_aggregator_payload(
    *,
    state: dict[str, Any],
    fpr_target: float | None,
    contributing_shards: list[str],
    n_records: int,
    per_signal: dict[str, Any],
    perf_meta: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    """Build the aggregator's return payload. Factored out so the
    incremental-save path (after each signal completion) can emit
    the same shape the final return uses, with only ``status``
    flipping from ``"in_progress"`` to ``"complete"``."""
    return {
        "task_surface": "calibration",
        "tool": "shard_runner",
        "tool_version": "1.0",
        "run_id": state.get("run_id"),
        "source_manifest_sha256": state.get("source_manifest_sha256"),
        "fpr_target": (
            state.get("fpr_target")
            if state.get("fpr_target") is not None
            else state.get("task_params", {}).get("fpr_target")
        ),
        "n_records": n_records,
        "n_shards_contributed": len(contributing_shards),
        "contributing_shards": contributing_shards,
        "embedding_model": state.get("embedding_model"),
        "embedding_revision": state.get("embedding_revision"),
        "per_signal": per_signal,
        "aggregator_perf": perf_meta or None,
        "status": status,
        "aggregated_at": _dt.datetime.now(
            _dt.timezone.utc,
        ).isoformat(timespec="seconds"),
    }


def _save_aggregator_partial(
    payload: dict[str, Any], out_path: Path,
) -> None:
    """Atomic write of the aggregator payload to ``out_path``.

    Used for both incremental in-progress saves and the final
    complete save. The tmp + rename dance prevents leaving a
    partially-written JSON on disk if the script crashes mid-write
    (which would defeat the purpose of checkpointing).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    tmp.replace(out_path)


def _load_resume_state(
    out_path: Path,
) -> dict[str, Any] | None:
    """Load a prior aggregate payload from ``out_path`` for resume.

    Returns the raw parsed payload dict on success, or ``None``
    when the file doesn't exist or can't be parsed. Callers do
    the compatibility check against the current run's inputs
    via :func:`_resume_compat_reason` before carrying entries
    forward — silently inheriting per_signal entries from a
    stale run is the bug codex flagged on PR #64.
    """
    if not out_path.exists():
        return None
    try:
        return json.loads(out_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(
            f"  could not parse prior --out {out_path} for resume: "
            f"{type(exc).__name__}: {exc}. Starting fresh.\n"
        )
        return None


def _resume_compat_reason(
    prior_payload: dict[str, Any],
    *,
    state: dict[str, Any],
    contributing_shards: list[str],
    fpr_target: float | None,
    args: argparse.Namespace,
) -> str | None:
    """Check whether ``prior_payload`` is compatible with the
    current run's inputs. Returns ``None`` when compatible (resume
    can proceed) or a one-line human-readable reason string when
    NOT compatible (caller refuses resume and starts fresh).

    Fields compared (codex P2 on PR #64):
      * ``source_manifest_sha256`` — the canonical "are we
        looking at the same corpus" hash. A missing field on the
        prior payload (older partial cache) is tolerated; a
        present-but-different value is the canonical staleness
        signal.
      * ``fpr_target`` — different FPR target shifts every
        signal's chosen operating point; resuming would mix
        thresholds derived under different targets into one
        survey.
      * ``contributing_shards`` — different shard set means the
        record union is different, so per_signal entries from
        the prior subset don't represent the same population.
      * ``aggregator_perf.bootstrap_engine`` — different engine
        means the bootstrap CIs aren't comparable.
      * ``aggregator_perf.bootstrap_resamples`` — different N
        for bootstrap CIs means different CI widths.

    Tolerates fields missing on the prior payload (pre-fix
    partial caches won't have all of them). Refuses when both
    sides have the field but disagree.
    """
    prior_manifest_sha = prior_payload.get("source_manifest_sha256")
    current_manifest_sha = state.get("source_manifest_sha256")
    if (
        prior_manifest_sha is not None
        and current_manifest_sha is not None
        and prior_manifest_sha != current_manifest_sha
    ):
        return (
            f"source_manifest_sha256 differs: prior="
            f"{prior_manifest_sha[:16]}..., current="
            f"{current_manifest_sha[:16]}..."
        )

    prior_fpr = prior_payload.get("fpr_target")
    if (
        prior_fpr is not None
        and fpr_target is not None
        and prior_fpr != fpr_target
    ):
        return (
            f"fpr_target differs: prior={prior_fpr}, "
            f"current={fpr_target}"
        )

    prior_shards = prior_payload.get("contributing_shards")
    if prior_shards is not None:
        prior_set = sorted(prior_shards)
        current_set = sorted(contributing_shards)
        if prior_set != current_set:
            return (
                f"contributing_shards differs: prior n="
                f"{len(prior_set)}, current n={len(current_set)} "
                f"(missing-from-current="
                f"{sorted(set(prior_set) - set(current_set))[:3]!r}, "
                f"new-in-current="
                f"{sorted(set(current_set) - set(prior_set))[:3]!r})"
            )

    prior_perf = prior_payload.get("aggregator_perf") or {}
    prior_engine = prior_perf.get("bootstrap_engine")
    current_engine = getattr(args, "bootstrap_engine", "loop")
    if (
        prior_engine is not None
        and prior_engine != current_engine
    ):
        return (
            f"bootstrap_engine differs: prior={prior_engine!r}, "
            f"current={current_engine!r}"
        )

    prior_resamples = prior_perf.get("bootstrap_resamples")
    current_resamples = int(
        getattr(args, "bootstrap_resamples", 2000)
    )
    if (
        prior_resamples is not None
        and prior_resamples != current_resamples
    ):
        return (
            f"bootstrap_resamples differs: prior={prior_resamples}, "
            f"current={current_resamples}"
        )

    return None


def _aggregate_calibration_records(
    *,
    all_records: list[dict[str, Any]],
    meta_list: list[dict[str, Any]],
    contributing_shards: list[str],
    state: dict[str, Any],
    args: argparse.Namespace,
    shard_cache_paths: list[Path] | None = None,
) -> dict[str, Any]:
    """Run ``calibrate_thresholds.derive_threshold_from_records``
    once per signal and emit the aggregated survey payload.

    Memory-hardened parallel implementation with checkpoint +
    resume + optional streaming pre-extraction. See the module-
    level "calibration_survey aggregator" comment for the four-
    layer memory design (belt, suspenders, buttons, zip).

    Recognized CLI args (read via ``getattr`` for back-compat with
    callers that don't set them):

      * ``bootstrap_engine`` / ``bootstrap_chunk_size`` /
        ``bootstrap_device`` — propagated to each per-signal
        ``derive_threshold_from_records`` call. PR #53 / #60.
      * ``aggregate_workers`` — number of concurrent signals.
        Default 1 (serial). PR #55.
      * ``executor`` — ``"thread"`` (default, zero-copy) or
        ``"process"`` (uses SharedMemory for pair arrays).
      * ``max_worker_rss_gb`` — user-imposed RSS budget; cap
        ``aggregate_workers`` so total ≤ this. None disables.
      * ``no_derive`` — skip per-signal derivation; emit only
        aggregated records + meta.
      * ``out`` — destination JSON path. When set, the aggregator
        writes a partial payload after every signal completion
        (status="in_progress") so a crash mid-sweep doesn't lose
        the work done so far. The final write flips status to
        "complete". Atomic writes (tmp + rename) prevent partial-
        file corruption.
      * ``resume`` — when True (default), and ``--out`` exists with
        a parseable prior payload, skip signals already in its
        ``per_signal`` dict. Pass ``--no-resume`` to force a fresh
        sweep regardless of any prior partial.
      * ``stream_pair_extraction`` — when True AND
        ``shard_cache_paths`` is provided, the aggregator opens
        shard caches one at a time, extracts per-signal pairs
        incrementally, and discards records after each shard.
        The full records list never goes resident; peak parent
        RSS is bounded by (one-shard records + all per-signal
        pair arrays). At RAID scale (8.3M records, ~40 GB if
        co-resident) this is the difference between OOM-on-load
        and finishing in minutes. The fast path's legacy fallback
        (records-list dispatch when pair extraction fails) is
        UNAVAILABLE in streaming mode — signals that can't be
        pre-extracted return as ``no usable pairs`` errors.

    ``shard_cache_paths`` (new in 1.68.0): list of cache.json
    paths the caller (typically ``shard_runner.cmd_aggregate``)
    has already validated for existence + SHA. Required for
    streaming mode; ignored otherwise.
    """
    if not getattr(args, "no_derive", False):
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
    perf_meta: dict[str, Any] = {}
    # n_records_effective is the count used in the payload + per-
    # signal partial saves. In streaming mode the streaming pass
    # overwrites it; otherwise ``len(all_records)`` is the truth.
    n_records_effective: int = len(all_records or [])

    # --- Resume from prior partial (if --out exists and --resume).
    # The compat check (codex P2 on PR #64) refuses to carry
    # entries forward when the prior payload was produced under
    # different inputs (different manifest hash, FPR target,
    # contributing shards, bootstrap engine, or resamples). Silent
    # acceptance of stale entries would mix incompatible thresholds
    # into one survey.
    out_path_str = getattr(args, "out", None)
    out_path = Path(out_path_str).expanduser() if out_path_str else None
    resume = bool(getattr(args, "resume", True))
    resumed_signals: list[str] = []
    # We need fpr_target / contributing_shards available now for
    # the compat check, even though they're also computed inside
    # the per-signal sweep block below. Compute once here.
    _resume_fpr_target = state.get("fpr_target")
    if _resume_fpr_target is None:
        _resume_fpr_target = (
            state.get("task_params") or {}
        ).get("fpr_target", 0.01)
    if out_path is not None and resume:
        prior_payload = _load_resume_state(out_path)
        if prior_payload is not None:
            incompat_reason = _resume_compat_reason(
                prior_payload, state=state,
                contributing_shards=contributing_shards,
                fpr_target=_resume_fpr_target, args=args,
            )
            if incompat_reason is not None:
                sys.stderr.write(
                    f"  resume: REFUSED — prior --out {out_path} "
                    f"is incompatible with this run "
                    f"({incompat_reason}). Pass --no-resume to "
                    f"overwrite, or use a different --out path "
                    f"to preserve the prior payload.\n"
                )
                perf_meta["resume_refused_reason"] = incompat_reason
            else:
                prior_per_signal = prior_payload.get("per_signal") or {}
                prior_status = prior_payload.get("status")
                if prior_per_signal:
                    per_signal.update(prior_per_signal)
                    resumed_signals = sorted(prior_per_signal.keys())
                    sys.stderr.write(
                        f"  resume: carried forward "
                        f"{len(resumed_signals)} signal(s) from "
                        f"prior --out {out_path} (prior status="
                        f"{prior_status!r}). Pass --no-resume to "
                        f"force a fresh sweep.\n"
                    )
                    perf_meta["resumed_from_partial"] = True
                    perf_meta["resumed_signal_count"] = (
                        len(resumed_signals)
                    )

    # Decide whether to enter the per-signal sweep at all. The
    # streaming path is a yes IF we have shard_cache_paths AND the
    # streaming flag is set (regardless of whether all_records is
    # populated — the caller may have skipped materialization on
    # purpose). The non-streaming path requires all_records be
    # populated.
    streaming = bool(
        shard_cache_paths is not None
        and getattr(args, "stream_pair_extraction", False)
    )
    has_input = streaming or bool(all_records)

    if ct is not None and has_input:
        merged_meta = meta_list[0] if meta_list else {}
        fpr_target = state.get("fpr_target")
        if fpr_target is None:
            fpr_target = state.get("task_params", {}).get(
                "fpr_target", 0.01,
            )
        iso_date = _dt.date.today().isoformat()

        # --- Layer 1 (Belt): pre-extract per-signal pairs once.
        try:
            from validation_harness import (  # type: ignore
                collect_signal_records,
            )
        except ImportError as exc:
            sys.stderr.write(
                f"  could not import collect_signal_records: {exc}. "
                f"Falling back to records-list dispatch (legacy path).\n"
            )
            collect_signal_records = None

        signals = sorted(ct.COMPRESSION_HEURISTICS.keys())
        per_signal_pairs: dict[str, list[tuple[int, float]]] = {}
        # n_records_total: in streaming mode the caller hasn't
        # counted records (they're never co-resident); we count
        # here during the stream so the perf block + payload have
        # the right number. In non-streaming mode we use
        # ``len(all_records)`` further down — this stays None.
        streamed_n_records: int | None = None

        if streaming and collect_signal_records is not None:
            # ----- Layer 5 (the RAID-unblocker): stream pre-
            # extraction from shard caches. Each shard is read,
            # extracted, and dropped before the next loads. Peak
            # parent RSS is bounded by (one shard's records + all
            # per-signal pair accumulators).
            extract_t0 = _dt.datetime.now()
            for sig_name in signals:
                per_signal_pairs[sig_name] = []
            streamed_n_records = 0
            extraction_failures: set[str] = set()
            # Codex P2 on PR #66: an unreadable shard cache used to
            # be silently skipped, leaving the operator with
            # calibration thresholds derived from a strictly smaller
            # set of records than they thought. Default behavior is
            # now to error out at the end of the loop if any shard
            # was unreadable. ``--allow-unreadable-shards`` (passed
            # through args) opts into the old skip-and-warn
            # behavior, in which case the dropped shard list is
            # still surfaced in ``perf_meta`` so the survey JSON
            # carries the audit trail.
            unreadable_shards: list[dict[str, str]] = []
            allow_unreadable_shards = bool(
                getattr(args, "allow_unreadable_shards", False)
            )
            for cp in shard_cache_paths or []:
                try:
                    with cp.open("r", encoding="utf-8") as fh:
                        shard_cache = json.load(fh)
                except Exception as exc:  # noqa: BLE001
                    unreadable_shards.append({
                        "path": str(cp),
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    })
                    sys.stderr.write(
                        f"  streaming: unreadable shard cache "
                        f"{cp}: {type(exc).__name__}: {exc}\n"
                    )
                    continue
                shard_records = shard_cache.get("records") or []
                streamed_n_records += len(shard_records)
                for sig_name in signals:
                    if sig_name in extraction_failures:
                        continue
                    spec = ct.COMPRESSION_HEURISTICS[sig_name]
                    try:
                        pairs = collect_signal_records(
                            shard_records, spec.signal_path,
                        )
                    except Exception:  # noqa: BLE001
                        # Signal can't be extracted (bad spec, etc.) —
                        # record it as failed and skip on future shards
                        # to avoid burning O(shards) on the same error.
                        extraction_failures.add(sig_name)
                        per_signal_pairs.pop(sig_name, None)
                        continue
                    per_signal_pairs[sig_name].extend(pairs)
                # shard_records goes out of scope; CPython refcount
                # reclaims it before the next iteration starts the
                # next cache load. This is the RAM-bound we paid
                # for.
                del shard_records
                del shard_cache
            extract_s = (
                _dt.datetime.now() - extract_t0
            ).total_seconds()
            perf_meta["pair_extraction_s"] = round(extract_s, 3)
            perf_meta["pair_extraction_signals_fast_path"] = (
                len(per_signal_pairs)
            )
            perf_meta["pair_extraction_signals_legacy_path"] = 0
            perf_meta["pair_extraction_mode"] = "streaming"
            perf_meta["pair_extraction_shards_streamed"] = (
                len(shard_cache_paths or [])
            ) - len(unreadable_shards)
            perf_meta["pair_extraction_shards_unreadable_count"] = (
                len(unreadable_shards)
            )
            if unreadable_shards:
                # Always surface the audit trail, whether we error
                # or proceed under --allow-unreadable-shards.
                perf_meta["pair_extraction_shards_unreadable"] = (
                    unreadable_shards
                )
                shard_paths_str = ", ".join(
                    s["path"] for s in unreadable_shards[:5]
                )
                if len(unreadable_shards) > 5:
                    shard_paths_str += (
                        f" (and {len(unreadable_shards) - 5} more)"
                    )
                if not allow_unreadable_shards:
                    raise SystemExit(
                        f"streaming aggregate: {len(unreadable_shards)} "
                        f"of {len(shard_cache_paths or [])} shard "
                        f"cache(s) were unreadable; refusing to emit "
                        f"calibration thresholds derived from a "
                        f"truncated record set. Unreadable: "
                        f"{shard_paths_str}. Re-run the failing "
                        f"shard(s), or pass "
                        f"--allow-unreadable-shards to proceed with "
                        f"the audit trail recorded in "
                        f"aggregator_perf.pair_extraction_shards_"
                        f"unreadable."
                    )
                sys.stderr.write(
                    f"  streaming: proceeding under --allow-"
                    f"unreadable-shards with "
                    f"{len(unreadable_shards)} dropped shard(s); "
                    f"calibration is based on a strict subset of "
                    f"the requested records.\n"
                )
        elif collect_signal_records is not None:
            extract_t0 = _dt.datetime.now()
            for sig_name in signals:
                spec = ct.COMPRESSION_HEURISTICS[sig_name]
                try:
                    per_signal_pairs[sig_name] = collect_signal_records(
                        all_records, spec.signal_path,
                    )
                except Exception:  # noqa: BLE001
                    # Couldn't pre-extract — typically a bad spec
                    # shape from a test fixture, or any other failure
                    # in collect_signal_records. Don't poison
                    # per_signal here; just skip the fast-path entry
                    # and let the dispatcher fall back to the legacy
                    # records-list path, which preserves the prior
                    # behavior (and surfaces any real failure as a
                    # normal SystemExit from derive_threshold_from_
                    # records).
                    pass
            extract_s = (
                _dt.datetime.now() - extract_t0
            ).total_seconds()
            perf_meta["pair_extraction_s"] = round(extract_s, 3)
            perf_meta["pair_extraction_signals_fast_path"] = (
                len(per_signal_pairs)
            )
            perf_meta["pair_extraction_signals_legacy_path"] = (
                len(signals) - len(per_signal_pairs)
            )
            perf_meta["pair_extraction_mode"] = "in_memory"

        # Dispatch every signal NOT already in per_signal (from
        # resume). Workers decide per-signal whether to use the fast
        # path (pre-extracted pairs available) or the legacy path
        # (pass the full records list — slower, but what the existing
        # tests cover and what the pre-PR implementation always did).
        dispatch_signals = [
            s for s in signals if s not in per_signal
        ]
        if resumed_signals:
            sys.stderr.write(
                f"  resume: skipping {len(resumed_signals)} already-"
                f"complete signal(s); dispatching "
                f"{len(dispatch_signals)} remaining.\n"
            )

        # --- Layer 4 (Zip) + Layer 2/3 selection: choose executor.
        requested_workers = max(1, int(getattr(
            args, "aggregate_workers", 1,
        )))
        executor_kind = str(getattr(
            args, "executor", "thread",
        )).lower()
        if executor_kind not in ("thread", "process"):
            sys.stderr.write(
                f"  unknown --executor {executor_kind!r}; "
                f"falling back to 'thread'.\n"
            )
            executor_kind = "thread"
        max_rss_gb = getattr(args, "max_worker_rss_gb", None)
        capped_workers, cap_reason = _cap_workers(
            requested_workers, executor_kind, max_rss_gb,
        )
        if cap_reason:
            sys.stderr.write(f"  worker cap: {cap_reason}\n")
        perf_meta.update({
            "executor": executor_kind,
            "requested_workers": requested_workers,
            "capped_workers": capped_workers,
            "worker_cap_reason": cap_reason or None,
            "bootstrap_engine": getattr(
                args, "bootstrap_engine", "loop",
            ),
            # Recorded so future resume runs can detect a change
            # in --bootstrap-resamples via the resume compat check
            # (codex P2 on PR #64).
            "bootstrap_resamples": int(
                getattr(args, "bootstrap_resamples", 2000),
            ),
        })

        # Per-signal: fast path if we have pre-extracted pairs,
        # legacy path otherwise. The legacy path is what the existing
        # tests cover and what the pre-PR implementation always did;
        # the fast path is the >99%-memory-drop production path.
        def _pairs_for(sig: str) -> list[tuple[int, float]] | None:
            return per_signal_pairs.get(sig) if sig in per_signal_pairs else None

        # Effective record count for payload reporting. In streaming
        # mode the full records list never goes resident, so
        # ``len(all_records)`` is 0 and meaningless; the streaming
        # pass counted them as it went.
        if streamed_n_records is not None:
            n_records_effective = streamed_n_records

        # Per-signal completion logger + checkpoint writer.
        # Operators running aggregates at MAGE / RAID scale (5-30
        # min wall-clock) need (a) progress visibility as signals
        # land, not silence followed by a single "written" line, and
        # (b) a partial JSON on disk after every signal so a crash
        # mid-sweep doesn't lose the work done so far. Both happen
        # here, in the same per-signal-completion callback.
        def _log_signal_done(name: str, entry: dict, t_start) -> None:
            t = (_dt.datetime.now() - t_start).total_seconds()
            err = entry.get("error") if isinstance(entry, dict) else None
            tag = f"ERROR: {err}" if err else "ok"
            sys.stderr.write(
                f"  [{t:6.1f}s] --> {name}: {tag}\n"
            )
            sys.stderr.flush()
            # Checkpoint: snapshot the partial payload so a crash
            # on a later signal doesn't discard this one.
            if out_path is not None:
                try:
                    partial = _build_aggregator_payload(
                        state=state, fpr_target=fpr_target,
                        contributing_shards=contributing_shards,
                        n_records=n_records_effective,
                        per_signal=per_signal,
                        perf_meta=perf_meta,
                        status="in_progress",
                    )
                    _save_aggregator_partial(partial, out_path)
                except Exception as exc:  # noqa: BLE001
                    sys.stderr.write(
                        f"  WARNING: partial-save to {out_path} "
                        f"failed: {type(exc).__name__}: {exc}. "
                        f"Continuing without checkpoint.\n"
                    )

        sweep_t0 = _dt.datetime.now()
        if capped_workers <= 1 or len(dispatch_signals) <= 1:
            # Serial path.
            for sig_name in dispatch_signals:
                ns = _build_signal_namespace(
                    sig_name, state=state, args=args,
                    contributing_shards=contributing_shards,
                    fpr_target=fpr_target, iso_date=iso_date,
                )
                name, entry = _derive_one_from_pairs(
                    sig_name,
                    _pairs_for(sig_name),
                    ns,
                    merged_meta,
                    records_fallback=all_records,
                )
                per_signal[name] = entry
                _log_signal_done(name, entry, sweep_t0)
        elif executor_kind == "thread":
            # --- Layer 2 (Suspenders): ThreadPoolExecutor.
            from concurrent.futures import (  # noqa: PLC0415
                ThreadPoolExecutor, as_completed,
            )
            with ThreadPoolExecutor(
                max_workers=capped_workers,
            ) as pool:
                future_to_sig = {
                    pool.submit(
                        _derive_one_from_pairs,
                        sig_name,
                        _pairs_for(sig_name),
                        _build_signal_namespace(
                            sig_name, state=state, args=args,
                            contributing_shards=contributing_shards,
                            fpr_target=fpr_target, iso_date=iso_date,
                        ),
                        merged_meta,
                        all_records,
                    ): sig_name
                    for sig_name in dispatch_signals
                }
                for fut in as_completed(future_to_sig):
                    name, entry = fut.result()
                    per_signal[name] = entry
                    _log_signal_done(name, entry, sweep_t0)
        else:
            # --- Layer 3 (Buttons): ProcessPoolExecutor + SharedMemory.
            from concurrent.futures import (  # noqa: PLC0415
                ProcessPoolExecutor, as_completed,
            )
            #
            # Split dispatch_signals into two cohorts (codex P2 on
            # PR #63):
            #   * pre_extracted: signals with pairs in
            #     per_signal_pairs → run via the process pool with
            #     SharedMemory dispatch (the fast path).
            #   * legacy_fallback: signals whose pair extraction
            #     failed (bad spec shape, etc.) → run via the
            #     parent's serial loop with records_fallback=
            #     all_records, exactly like the thread/serial paths
            #     handle them. Without this split the process
            #     executor would silently drop them as "no usable
            #     pairs" errors — a feature-availability regression
            #     vs the other executors.
            pre_extracted = [
                s for s in dispatch_signals
                if s in per_signal_pairs
            ]
            legacy_fallback = [
                s for s in dispatch_signals
                if s not in per_signal_pairs
            ]
            if legacy_fallback:
                sys.stderr.write(
                    f"  process executor: {len(legacy_fallback)} "
                    f"signal(s) lack pre-extracted pairs and will "
                    f"run via the parent's legacy records-list "
                    f"path serially before the process pool starts "
                    f"(no silent drop). Pre-extracted via process "
                    f"pool: {len(pre_extracted)} signal(s).\n"
                )
                for sig_name in legacy_fallback:
                    ns = _build_signal_namespace(
                        sig_name, state=state, args=args,
                        contributing_shards=contributing_shards,
                        fpr_target=fpr_target, iso_date=iso_date,
                    )
                    name, entry = _derive_one_from_pairs(
                        sig_name,
                        None,  # no pairs → legacy path
                        ns,
                        merged_meta,
                        records_fallback=all_records,
                    )
                    per_signal[name] = entry
                    _log_signal_done(name, entry, sweep_t0)

            shm_handles: list[Any] = []
            try:
                # Pre-allocate SharedMemory pair arrays per signal
                # in the pre-extracted cohort.
                shm_per_sig: dict[
                    str, tuple[str, str, int]
                ] = {}
                for sig_name in pre_extracted:
                    pairs = per_signal_pairs.get(sig_name, [])
                    sm_l, sm_s, l_name, s_name, n = (
                        _allocate_shared_pair_arrays(pairs)
                    )
                    shm_handles.extend([sm_l, sm_s])
                    shm_per_sig[sig_name] = (l_name, s_name, n)
                # Build per-signal Namespace as a dict (Namespace
                # itself isn't pickle-friendly across all Python
                # versions for nested attributes, but a dict is).
                ns_kwargs_template = {
                    "manifest": str(
                        state.get("source_manifest_path") or "",
                    ),
                    "fpr_target": fpr_target,
                    "bootstrap_seed": getattr(args, "bootstrap_seed", 42),
                    "bootstrap_resamples": getattr(
                        args, "bootstrap_resamples", 2000,
                    ),
                    "bootstrap_confidence": getattr(
                        args, "bootstrap_confidence", 0.95,
                    ),
                    "bootstrap_engine": getattr(
                        args, "bootstrap_engine", "loop",
                    ),
                    "bootstrap_chunk_size": getattr(
                        args, "bootstrap_chunk_size", None,
                    ),
                    "bootstrap_device": getattr(
                        args, "bootstrap_device", None,
                    ),
                    "use": getattr(args, "use", "validation"),
                    "notes": (
                        f"Sharded calibration run "
                        f"{state.get('run_id', 'sharded_run')!r}. "
                        f"Aggregated from {len(contributing_shards)} "
                        f"shard cache(s) via SharedMemory pair-array "
                        f"dispatch."
                    ),
                }
                if pre_extracted:
                    # Per-signal slug carried in kwargs at submit time.
                    with ProcessPoolExecutor(
                        max_workers=capped_workers,
                    ) as pool:
                        future_to_sig = {}
                        for sig_name in pre_extracted:
                            l_name, s_name, n = shm_per_sig[sig_name]
                            ns_kwargs = dict(ns_kwargs_template)
                            ns_kwargs["slug"] = (
                                f"sharded_{state.get('run_id', 'sharded_run')}"
                                f"_{sig_name}_fpr{fpr_target}_{iso_date}"
                            )
                            fut = pool.submit(
                                _derive_one_via_shared_memory,
                                sig_name, l_name, s_name, n,
                                ns_kwargs, merged_meta,
                            )
                            future_to_sig[fut] = sig_name
                        for fut in as_completed(future_to_sig):
                            name, entry = fut.result()
                            per_signal[name] = entry
                            _log_signal_done(name, entry, sweep_t0)
            finally:
                # Close + unlink all SharedMemory blocks. Skipping
                # unlink leaks /dev/shm entries on POSIX or named
                # objects on Windows.
                for sm in shm_handles:
                    try:
                        sm.close()
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        sm.unlink()
                    except Exception:  # noqa: BLE001
                        pass
            # Record perf metadata about the split so post-hoc
            # audits can see which cohort each signal landed in.
            perf_meta["process_signals_pre_extracted"] = (
                len(pre_extracted)
            )
            perf_meta["process_signals_legacy_fallback"] = (
                len(legacy_fallback)
            )
        sweep_s = (_dt.datetime.now() - sweep_t0).total_seconds()
        perf_meta["sweep_s"] = round(sweep_s, 3)
        perf_meta["n_signals_dispatched"] = len(dispatch_signals)

    # Final fpr_target lookup (for the no-records / no-derive paths
    # where the inner block didn't compute it).
    final_fpr = (
        state.get("fpr_target")
        if state.get("fpr_target") is not None
        else (state.get("task_params") or {}).get("fpr_target")
    )
    return _build_aggregator_payload(
        state=state, fpr_target=final_fpr,
        contributing_shards=contributing_shards,
        n_records=n_records_effective,
        per_signal=per_signal,
        perf_meta=perf_meta,
        status="complete",
    )


# --------------- corpus_hygiene scorer adapter ------------------


def _score_shard_corpus_hygiene(
    *,
    shard_manifest_path: Path,
    cache_path: Path,
    sigterm_event: Any,
    flush_every: int,
    task_params: dict[str, Any],
    run_context: dict[str, Any],
) -> ShardResult:
    """Run ``check_corpus.score_manifest_rows`` against the shard's
    manifest slice and emit per-file hygiene records + a meta
    summary.

    The shard manifest contains rows that originated from the
    operator's source manifest, each carrying a ``path`` field that
    points at the file on disk to audit. We pass the manifest path
    through to ``score_manifest_rows`` so it can resolve relative
    paths the same way ``check_corpus.paths_from_manifest`` does.
    """
    # Lazy import for the same reasons as the calibration-survey
    # adapter: shard_runner / check_corpus may import this module
    # before their own dependencies are ready.
    import check_corpus as cc  # type: ignore

    warn_threshold = float(
        task_params.get("warn_threshold", cc.DEFAULT_WARN_THRESHOLD)
    )
    fail_threshold = float(
        task_params.get("fail_threshold", cc.DEFAULT_FAIL_THRESHOLD)
    )
    strip_rules = task_params.get("strip_rules") or None
    strip_aggressive = bool(task_params.get("strip_aggressive", False))
    collect_stripped = bool(task_params.get("collect_stripped", False))

    records, summary = cc.score_manifest_rows(
        Path(shard_manifest_path),
        strip_rules=strip_rules,
        strip_aggressive=strip_aggressive,
        collect_stripped=collect_stripped,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
    )
    meta = {
        "scorer_version": "corpus_hygiene-1.0",
        "warn_threshold": warn_threshold,
        "fail_threshold": fail_threshold,
        "strip_rules": strip_rules,
        "strip_aggressive": strip_aggressive,
        "summary": summary,
    }
    # Write the cache ourselves: the orchestrator's "write cache if
    # the scorer didn't" fallback works, but doing it here keeps
    # the corpus_hygiene path symmetric with the calibration_survey
    # path (the real scorer writes its own cache via
    # load_or_score_corpus).
    cp = Path(cache_path)
    cp.parent.mkdir(parents=True, exist_ok=True)
    with cp.open("w", encoding="utf-8") as fh:
        json.dump(
            {"records": records, "meta": meta},
            fh,
            sort_keys=True,
        )
    return {
        "records": records,
        "meta": meta,
        "cache_hit": False,
    }


# --------------- corpus_hygiene aggregator ----------------------


def _aggregate_corpus_hygiene_records(
    *,
    all_records: list[dict[str, Any]],
    meta_list: list[dict[str, Any]],
    contributing_shards: list[str],
    state: dict[str, Any],
    args: argparse.Namespace,
    shard_cache_paths: list[Path] | None = None,
) -> dict[str, Any]:
    """Roll up per-file hygiene records into the aggregate shape
    ``check_corpus.check_corpus_paths`` produces for single-process
    runs.

    ``shard_cache_paths`` accepted for surface-contract uniformity
    (1.68.0+) but currently UNUSED — corpus_hygiene does not yet
    support streaming. When the operator passes ``--stream-pair-
    extraction`` to a corpus_hygiene aggregate, this surface emits
    a stderr warning and proceeds with ``all_records`` (which will
    be empty in streaming mode). Streaming support is a follow-up
    PR.

    We delegate to the same ``_summarize_hygiene_records`` helper
    the single-process path uses, then layer on the cross-shard
    bookkeeping (contributing_shards, run_id, etc.) so a future
    parity test can confirm the sharded artifact matches the
    single-process artifact on the same input.
    """
    # Lazy import — same reasons as the scorer adapter.
    import check_corpus as cc  # type: ignore

    if shard_cache_paths is not None and not all_records:
        # Operator passed --stream-pair-extraction; corpus_hygiene
        # doesn't yet support streaming. Warn and proceed with the
        # empty all_records — the summary will reflect "0 records".
        sys.stderr.write(
            "  corpus_hygiene aggregator: streaming pre-extraction "
            "is not yet supported for this surface. The aggregate "
            "will report 0 records. Drop --stream-pair-extraction, "
            "or wait for the corpus_hygiene streaming PR.\n"
        )

    task_params = state.get("task_params") or {}
    warn_threshold = float(
        task_params.get("warn_threshold", cc.DEFAULT_WARN_THRESHOLD)
    )
    fail_threshold = float(
        task_params.get("fail_threshold", cc.DEFAULT_FAIL_THRESHOLD)
    )

    summary = cc._summarize_hygiene_records(
        all_records,
        warn_threshold=warn_threshold,
        fail_threshold=fail_threshold,
    )
    summary.update({
        "tool": "shard_runner",
        "tool_version": "1.0",
        "task": "corpus_hygiene",
        "run_id": state.get("run_id"),
        "source_manifest_sha256": state.get("source_manifest_sha256"),
        "n_shards_contributed": len(contributing_shards),
        "contributing_shards": contributing_shards,
        "files": all_records,
        "aggregated_at": _dt.datetime.now(
            _dt.timezone.utc,
        ).isoformat(timespec="seconds"),
    })
    return summary


# --------------- Initial registrations --------------------------


register_task(TaskSurface(
    name="calibration_survey",
    score_shard=_score_shard_calibration_survey,
    aggregate_records=_aggregate_calibration_records,
    default_task_params={
        "fpr_target": 0.01,
        "tier1": True,
        "tier2": False,
        "tier3": False,
    },
    required_state_fields=[
        "source_manifest_path",
        "source_manifest_sha256",
    ],
))


register_task(TaskSurface(
    name="corpus_hygiene",
    score_shard=_score_shard_corpus_hygiene,
    aggregate_records=_aggregate_corpus_hygiene_records,
    default_task_params={
        "warn_threshold": 0.01,
        "fail_threshold": 0.05,
        "strip_rules": None,
        "strip_aggressive": False,
        "collect_stripped": False,
    },
    required_state_fields=[
        "source_manifest_path",
        "source_manifest_sha256",
    ],
))
