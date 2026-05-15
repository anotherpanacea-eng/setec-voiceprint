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
    )
    # The legacy scorer returns a plain dict; normalize to
    # ShardResult shape.
    return {
        "records": result.get("records") or [],
        "meta": result.get("meta") or {},
        "cache_hit": bool(result.get("cache_hit", False)),
    }


# --------------- calibration_survey aggregator ------------------


def _aggregate_calibration_records(
    *,
    all_records: list[dict[str, Any]],
    meta_list: list[dict[str, Any]],
    contributing_shards: list[str],
    state: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Run ``calibrate_thresholds.derive_threshold_from_records``
    once per signal and emit the aggregated survey payload.

    This is the body lifted out of ``shard_runner.cmd_aggregate``
    so the calibration-survey case keeps producing the same
    artifact it always has. The orchestrator is now task-dispatched;
    the actual derivation logic lives here.
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
    if ct is not None and all_records:
        merged_meta = meta_list[0] if meta_list else {}
        run_id = state.get("run_id", "sharded_run")
        fpr_target = state.get("fpr_target")
        if fpr_target is None:
            # task_params holds it for v1.45.0+ state files; fall
            # back to that path before defaulting.
            fpr_target = state.get("task_params", {}).get(
                "fpr_target", 0.01,
            )
        iso_date = _dt.date.today().isoformat()
        for sig_name in sorted(ct.COMPRESSION_HEURISTICS.keys()):
            try:
                # `derive_threshold_from_records` reads `args.slug`,
                # `args.use`, and `args.notes` in addition to the
                # signal / manifest / fpr / bootstrap fields.
                ns = argparse.Namespace(
                    signal=sig_name,
                    manifest=str(state.get("source_manifest_path") or ""),
                    fpr_target=fpr_target,
                    bootstrap_seed=42,
                    bootstrap_resamples=2000,
                    bootstrap_confidence=0.95,
                    slug=(
                        f"sharded_{run_id}_{sig_name}_fpr"
                        f"{fpr_target}_{iso_date}"
                    ),
                    use=getattr(args, "use", "validation"),
                    notes=(
                        f"Sharded calibration run {run_id!r}. "
                        f"Aggregated from {len(contributing_shards)} shard "
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
        "n_records": len(all_records),
        "n_shards_contributed": len(contributing_shards),
        "contributing_shards": contributing_shards,
        "embedding_model": state.get("embedding_model"),
        "embedding_revision": state.get("embedding_revision"),
        "per_signal": per_signal,
        "aggregated_at": _dt.datetime.now(
            _dt.timezone.utc,
        ).isoformat(timespec="seconds"),
    }


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
) -> dict[str, Any]:
    """Roll up per-file hygiene records into the aggregate shape
    ``check_corpus.check_corpus_paths`` produces for single-process
    runs.

    We delegate to the same ``_summarize_hygiene_records`` helper
    the single-process path uses, then layer on the cross-shard
    bookkeeping (contributing_shards, run_id, etc.) so a future
    parity test can confirm the sharded artifact matches the
    single-process artifact on the same input.
    """
    # Lazy import — same reasons as the scorer adapter.
    import check_corpus as cc  # type: ignore

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
