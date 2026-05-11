#!/usr/bin/env python3
"""Regression tests for sharding.py.

Pins the contract `internal/SPEC_sharded_calibration.md` §2.1 made
load-bearing:

  * Deterministic given (seed, source row multiset).
  * Stratified along the configured axes — each shard's
    label-distribution is roughly the source's.
  * Approximately equal sizes (±5% in spec terms).
  * Coverage: every source row lands in exactly one shard.

Tests use synthetic rows; we don't depend on a real RAID/MAGE
fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "calibration") not in sys.path:
    sys.path.insert(0, str(ROOT / "calibration"))

import sharding  # type: ignore


# --------------- Helpers ----------------------------------------


def _make_rows(
    *, register_counts: dict[str, int], ai_status_counts: dict[str, int],
) -> list[dict]:
    """Build a row list with the requested label counts.

    Rows are tagged with both register and ai_status using a
    round-robin over the smaller-count list to mix label
    combinations.
    """
    rows = []
    registers = []
    for reg, count in register_counts.items():
        registers.extend([reg] * count)
    statuses = []
    for status, count in ai_status_counts.items():
        statuses.extend([status] * count)
    n = min(len(registers), len(statuses))
    for i in range(n):
        rows.append({
            "text_id": f"row_{i:06d}",
            "register": registers[i],
            "ai_status": statuses[i],
            "use": "validation",
            "privacy": "shareable",
        })
    return rows


# --------------- Coverage + determinism --------------------------


def test_every_row_lands_in_exactly_one_shard():
    rows = _make_rows(
        register_counts={"literary_fiction": 30, "blog_essay": 30, "academic_philosophy": 30},
        ai_status_counts={"pre_ai_human": 45, "ai_generated": 45},
    )
    shards = sharding.split_into_shards(rows, n_shards=5)
    assignment: dict[str, int] = {}
    for shard_idx, shard in enumerate(shards):
        for row in shard:
            tid = row["text_id"]
            assert tid not in assignment, (
                f"Row {tid} appears in shards {assignment[tid]} and {shard_idx}"
            )
            assignment[tid] = shard_idx
    assert len(assignment) == len(rows), (
        f"{len(rows) - len(assignment)} rows missing from shards"
    )


def test_split_is_deterministic_given_seed():
    rows = _make_rows(
        register_counts={"literary_fiction": 50, "blog_essay": 50},
        ai_status_counts={"pre_ai_human": 50, "ai_generated": 50},
    )
    a = sharding.split_into_shards(rows, n_shards=4, seed=42)
    b = sharding.split_into_shards(rows, n_shards=4, seed=42)
    # Same shard contents in same order.
    for sa, sb in zip(a, b):
        assert [r["text_id"] for r in sa] == [r["text_id"] for r in sb]


def test_different_seeds_produce_different_assignments():
    rows = _make_rows(
        register_counts={"literary_fiction": 50, "blog_essay": 50},
        ai_status_counts={"pre_ai_human": 50, "ai_generated": 50},
    )
    a = sharding.split_into_shards(rows, n_shards=4, seed=1)
    b = sharding.split_into_shards(rows, n_shards=4, seed=2)
    # At least one shard's row ordering differs.
    a_orders = [tuple(r["text_id"] for r in s) for s in a]
    b_orders = [tuple(r["text_id"] for r in s) for s in b]
    assert a_orders != b_orders


def test_split_is_invariant_to_source_row_order():
    """Reordering the source manifest must not change shard
    membership — the function must depend on the multiset of row
    contents, not insertion order. This is what makes a sharded
    calibration reproducible even if the upstream manifest was
    sorted differently between runs.
    """
    rows = _make_rows(
        register_counts={"literary_fiction": 20, "blog_essay": 20},
        ai_status_counts={"pre_ai_human": 20, "ai_generated": 20},
    )
    a = sharding.split_into_shards(rows, n_shards=4, seed=42)
    # Reverse and re-split.
    b = sharding.split_into_shards(list(reversed(rows)), n_shards=4, seed=42)
    # Shard-membership sets are equal (order within shard may differ).
    for sa, sb in zip(a, b):
        assert {r["text_id"] for r in sa} == {r["text_id"] for r in sb}


# --------------- Stratification ---------------------------------


def test_stratification_distributes_labels_evenly():
    """Each shard should have a label distribution close to the
    source's. With 30 rows of each register × 2 ai_status combos,
    spread across 3 shards, each shard should get ~10 of each
    combo.
    """
    rows = _make_rows(
        register_counts={"a": 30, "b": 30, "c": 30},
        ai_status_counts={"pre_ai_human": 45, "ai_generated": 45},
    )
    shards = sharding.split_into_shards(
        rows, n_shards=3, stratify_by=["register", "ai_status"], seed=42,
    )
    for shard in shards:
        # Each shard should have rough register balance:
        # ~10 of each register.
        from collections import Counter
        reg_counts = Counter(r["register"] for r in shard)
        for reg in ("a", "b", "c"):
            # ±2 tolerance for round-robin remainder.
            assert abs(reg_counts[reg] - 10) <= 2, (
                f"Shard register balance off: {reg_counts}"
            )


def test_stratification_with_missing_field_uses_placeholder():
    rows = [
        {"text_id": f"r{i}", "register": "x", "ai_status": "pre_ai_human"}
        if i % 2 == 0 else {"text_id": f"r{i}", "ai_status": "pre_ai_human"}
        for i in range(20)
    ]
    shards = sharding.split_into_shards(
        rows, n_shards=3, stratify_by=["register", "ai_status"],
    )
    # All 20 rows should be assigned.
    total = sum(len(s) for s in shards)
    assert total == 20


def test_disabled_stratification_uses_one_bucket():
    rows = _make_rows(
        register_counts={"a": 30, "b": 30},
        ai_status_counts={"pre_ai_human": 30, "ai_generated": 30},
    )
    shards = sharding.split_into_shards(
        rows, n_shards=4, stratify_by=[], seed=42,
    )
    # Coverage still 1:1.
    assignment = {r["text_id"] for shard in shards for r in shard}
    assert len(assignment) == len(rows)


# --------------- Edge cases -------------------------------------


def test_empty_source_produces_empty_shards():
    shards = sharding.split_into_shards([], n_shards=5)
    assert len(shards) == 5
    assert all(not s for s in shards)


def test_n_shards_must_be_positive():
    with pytest.raises(ValueError):
        sharding.split_into_shards([{"a": 1}], n_shards=0)


def test_n_shards_larger_than_rows_leaves_some_empty():
    rows = _make_rows(
        register_counts={"a": 3}, ai_status_counts={"pre_ai_human": 3},
    )
    shards = sharding.split_into_shards(rows, n_shards=5)
    assert len(shards) == 5
    n_total = sum(len(s) for s in shards)
    assert n_total == 3


# --------------- Helper functions -------------------------------


def test_shard_summary_reports_counts():
    rows = _make_rows(
        register_counts={"a": 3, "b": 3}, ai_status_counts={"pre_ai_human": 6},
    )
    summary = sharding.shard_summary(rows, ["register", "ai_status"])
    assert summary["n_entries"] == 6
    # 3 each of (a, pre_ai_human) and (b, pre_ai_human).
    assert summary["stratum_counts"]["a|pre_ai_human"] == 3
    assert summary["stratum_counts"]["b|pre_ai_human"] == 3


def test_compute_shard_count():
    assert sharding.compute_shard_count(0, shard_size_target=100) == 1
    assert sharding.compute_shard_count(50, shard_size_target=100) == 1
    assert sharding.compute_shard_count(100, shard_size_target=100) == 1
    assert sharding.compute_shard_count(150, shard_size_target=100) == 2
    assert sharding.compute_shard_count(8_000_000, shard_size_target=100_000) == 80


def test_estimate_stratum_balance():
    rows = _make_rows(
        register_counts={"big": 100, "small": 5},
        ai_status_counts={"pre_ai_human": 100, "ai_generated": 5},
    )
    bal = sharding.estimate_stratum_balance(rows, ["register", "ai_status"])
    assert bal["n_strata"] >= 1
    assert bal["smallest_stratum_size"] <= bal["largest_stratum_size"]
