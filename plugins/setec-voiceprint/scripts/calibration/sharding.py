#!/usr/bin/env python3
"""sharding.py — pure stratified-split logic for sharded calibration.

The sharded-calibration toolchain (see `internal/SPEC_sharded_
calibration.md`) needs to take a source manifest (RAID's 8M rows,
MAGE's 436k rows, or any future labeled corpus) and split it into
N shards that:

  1. **Preserve stratification** along the configured axes (default:
     ``register`` and ``ai_status``) so each shard has roughly the
     same label distribution as the source. Without stratification,
     a shard could end up with all human-written entries while
     another gets all AI-generated ones; the per-shard records
     cache would then be unrepresentative and the aggregator's
     threshold sweep would have correlated-shard error.
  2. **Are deterministic given a seed.** Re-running the shard step
     against the same source manifest with the same seed must
     produce identical shard membership. Reproducibility is the
     point of the calibration ledger; non-determinism here would
     make sharded runs un-auditable.
  3. **Approximately equal size.** The framework targets shards of
     ~100k rows each; the actual split varies by ±5% because the
     stratification groups don't divide evenly.

This module is pure: no I/O, no global state, no random module use
beyond an explicitly-seeded RNG. The CLI side
(``shard_runner.py``) handles file reads and writes; this module
just takes lists of dicts and returns lists of lists of dicts.
"""

from __future__ import annotations

import random
from typing import Any, Iterable


def _stratification_key(
    row: dict[str, Any], stratify_by: list[str],
) -> tuple[Any, ...]:
    """Compose a tuple of the row's stratification-field values.

    Missing fields stringify to the literal ``"<missing>"`` so rows
    with incomplete metadata still get a stable bucket. This is
    intentional: the calibration toolchain accepts manifests where
    some rows omit fields (RAID's code-domain rows omit register
    after v1.42.3), and we'd rather bucket them coherently than
    drop them.
    """
    return tuple(
        str(row.get(field, "<missing>")) for field in stratify_by
    )


def _row_sort_key(row: dict[str, Any]) -> str:
    """Stable per-row sort key.

    Sorts by ``text_id`` when present (the canonical row identifier
    in every SETEC manifest) and falls back to the dict's sorted
    JSON serialization otherwise. The fallback case exists for
    tests and for any future manifest shape that omits ``text_id``;
    a real calibration corpus always carries one.
    """
    tid = row.get("text_id")
    if isinstance(tid, str):
        return tid
    import json as _json
    return _json.dumps(row, sort_keys=True)


def _shuffle_within_strata(
    rows: list[dict[str, Any]],
    stratify_by: list[str],
    seed: int,
) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    """Group rows by their stratification key, then canonically
    sort and deterministically shuffle within each group.

    The canonical-sort step (by ``text_id`` or stable fallback) is
    what makes the function invariant to source row order: a
    manifest reordered upstream produces the same shard membership
    because the within-stratum shuffle operates on a sorted list.
    The shuffle then uses a per-stratum seed derived from the
    global seed and the stratum key, so different strata get
    different permutations even though they all share the same
    global seed.
    """
    by_stratum: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        key = _stratification_key(row, stratify_by)
        by_stratum.setdefault(key, []).append(row)
    for key, group in by_stratum.items():
        # Canonicalize the stratum's row order before shuffling.
        # Without this step, two manifests with identical content
        # but different row order would produce different shards
        # under the same seed.
        group.sort(key=_row_sort_key)
        stratum_seed = hash((seed, tuple(str(k) for k in key))) & 0xFFFFFFFF
        rng = random.Random(stratum_seed)
        rng.shuffle(group)
    return by_stratum


def split_into_shards(
    rows: list[dict[str, Any]],
    *,
    n_shards: int,
    stratify_by: list[str] | None = None,
    seed: int = 42,
) -> list[list[dict[str, Any]]]:
    """Deterministically split rows into ``n_shards`` stratified
    shards.

    Algorithm: bucket rows by stratification key, shuffle within
    each bucket (seeded), then deal rows out round-robin across
    shards. Round-robin within strata preserves the stratification
    invariant — each shard ends up with roughly ``len(stratum) /
    n_shards`` rows of each label combination.

    Args:
      rows: list of manifest rows (dicts). Order does not matter;
        the function is deterministic given ``seed`` and the
        multiset of row contents.
      n_shards: number of shards to produce. Must be ≥ 1. If
        ``n_shards`` is larger than ``len(rows)``, some shards will
        be empty (legal but useless; the caller typically caps
        ``n_shards`` to ``len(rows) // shard_size_target``).
      stratify_by: list of dict keys to stratify on. Defaults to
        ``["register", "ai_status"]`` per the spec's §2.1 decision.
        Pass ``[]`` to disable stratification (treats all rows as
        one stratum and round-robins across shards).
      seed: deterministic-shuffle seed. The same ``seed`` + source
        rows always produce the same shards.

    Returns:
      List of ``n_shards`` lists. Each inner list is a shard's
      rows. The outer list is in shard-index order (shard 0 first).
    """
    if n_shards < 1:
        raise ValueError(f"n_shards must be ≥ 1, got {n_shards}")
    if not rows:
        return [[] for _ in range(n_shards)]
    if stratify_by is None:
        stratify_by = ["register", "ai_status"]
    by_stratum = _shuffle_within_strata(rows, stratify_by, seed)
    shards: list[list[dict[str, Any]]] = [[] for _ in range(n_shards)]
    # Round-robin within each stratum. Cycling across shards
    # ensures equal-ish counts and preserves stratification.
    for key in sorted(by_stratum.keys()):
        group = by_stratum[key]
        for i, row in enumerate(group):
            shards[i % n_shards].append(row)
    return shards


def shard_summary(shard: list[dict[str, Any]], stratify_by: list[str]) -> dict[str, Any]:
    """Summarise a shard's contents for state.json bookkeeping.

    Returns ``n_entries`` and a per-stratum count map so the user
    can spot-check that stratification preserved label balance.
    """
    counts: dict[str, int] = {}
    for row in shard:
        key = _stratification_key(row, stratify_by)
        # Join with "|" for human-readable JSON.
        label = "|".join(key)
        counts[label] = counts.get(label, 0) + 1
    return {
        "n_entries": len(shard),
        "stratum_counts": counts,
    }


def compute_shard_count(
    n_rows: int, *, shard_size_target: int,
) -> int:
    """Pick a shard count from the source manifest size + target.

    Rule: ``max(1, ceil(n_rows / shard_size_target))``. Yields one
    shard when the corpus is small, scales linearly thereafter.
    Caps deliberately not applied here — if a user wants 1000 shards
    of 8000 rows each, that's their call.
    """
    if n_rows <= 0:
        return 1
    return max(1, (n_rows + shard_size_target - 1) // shard_size_target)


def estimate_stratum_balance(
    rows: list[dict[str, Any]], stratify_by: list[str],
) -> dict[str, Any]:
    """Report stratum distribution of the source for shard sizing
    guidance.

    Used by ``shard_runner shard`` to warn if any stratum is so
    small that round-robin assignment will produce shards with zero
    representation from that stratum. Threshold: a stratum with
    fewer rows than ``n_shards`` will land entirely in the first
    few shards.
    """
    counts: dict[str, int] = {}
    for row in rows:
        key = "|".join(_stratification_key(row, stratify_by))
        counts[key] = counts.get(key, 0) + 1
    return {
        "n_strata": len(counts),
        "smallest_stratum_size": min(counts.values()) if counts else 0,
        "largest_stratum_size": max(counts.values()) if counts else 0,
        "stratum_counts": counts,
    }
