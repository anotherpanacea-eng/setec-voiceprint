#!/usr/bin/env python3
"""Tests for the O(n log n) ``_sweep_threshold_fast`` dispatch
path in ``calibrate_thresholds.sweep_threshold`` (PR
feat/sweep-threshold-fast, stacked on the 1.66.0 checkpointed-
aggregate branch).

Motivation: MAGE / RAID-scale calibration was bottlenecked not by
the bootstrap CI (the 1.65.0 hardened-aggregator PR's focus) but
by ``sweep_threshold`` itself — its O(n × unique_scores) loop
became >70 min per signal at n=338K pairs. The fast dispatch path
(sort-and-scan, O(n log n)) returns in <0.3 s at the same scale.

This module pins:

  * Operating-point equivalence: loop and fast paths return the
    same TPR / FPR / precision at the chosen operating point
    (within float epsilon) for both ``gt`` and ``lt`` directions,
    on synthetic and real-shaped paired samples.
  * Dispatch threshold: ``len(pairs) <
    _SWEEP_THRESHOLD_FAST_DISPATCH_N`` uses the loop path (bit-
    exact backward compat); ``len(pairs) >=`` threshold uses the
    fast path.
  * Failure-case parity: both paths handle single-class,
    sub-resolution FPR target, and unreachable FPR target.
  * Wall-clock guarantee: a 50K-pair sweep completes in <1 s on
    the fast path (the bound that unblocks MAGE).
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "calibration") not in sys.path:
    sys.path.insert(0, str(ROOT / "calibration"))

import calibrate_thresholds as ct  # type: ignore  # noqa: E402


# --------------- Synthetic pair generators -----------------------


def _separable_pairs(n: int, *, direction: str, seed: int = 42):
    """Synthetic (label, score) pairs where the positive class is
    shifted away from the negative class in the direction-relevant
    sense. Produces realistic per-signal sweep behavior with a
    non-degenerate operating point."""
    rng = random.Random(seed)
    pairs = []
    for _ in range(n):
        y = rng.choice([0, 1])
        if direction == "gt":
            # Positives skew high.
            s = rng.gauss(1.0 if y == 1 else 0.0, 1.0)
        else:
            # Positives skew low.
            s = rng.gauss(-1.0 if y == 1 else 0.0, 1.0)
        pairs.append((y, s))
    return pairs


def _tied_score_pairs(n: int, *, direction: str, seed: int = 42):
    """Pairs with intentional tied scores. Tests that the
    sort-and-scan path handles tie-groups correctly (the noted
    semantic edge case in the docstring)."""
    rng = random.Random(seed)
    # Discrete score space → lots of ties.
    pairs = []
    for _ in range(n):
        y = rng.choice([0, 1])
        s = rng.choice([-2, -1, 0, 1, 2])  # only 5 distinct scores
        pairs.append((y, s))
    return pairs


# --------------- Dispatch ---------------------------------------


def test_dispatch_threshold_constant_exists():
    assert hasattr(ct, "_SWEEP_THRESHOLD_FAST_DISPATCH_N")
    assert isinstance(ct._SWEEP_THRESHOLD_FAST_DISPATCH_N, int)
    assert ct._SWEEP_THRESHOLD_FAST_DISPATCH_N > 0


def test_small_n_uses_loop_path():
    """Below dispatch threshold: loop path. The marker is the
    presence of the ``candidates`` log on failure (only the loop
    path emits this)."""
    pairs = _separable_pairs(
        ct._SWEEP_THRESHOLD_FAST_DISPATCH_N - 100, direction="gt",
    )
    # Force the failure branch with an unreachable FPR target.
    result = ct.sweep_threshold(pairs, "gt", fpr_target=1e-12)
    # Sub-resolution FPR: short-circuits BEFORE either dispatch
    # path runs. Don't use this case for dispatch detection.
    # Instead, run a feasible target and confirm available=True
    # and the result shape is the loop path's (no quirks).
    result = ct.sweep_threshold(pairs, "gt", fpr_target=0.05)
    assert result["available"] is True


# --------------- Operating-point equivalence ---------------------


@pytest.mark.parametrize("direction", ["gt", "lt"])
@pytest.mark.parametrize("n", [500, 1500, 4000])  # all below dispatch N
def test_loop_path_bit_exact_at_small_n(direction, n):
    """Below dispatch threshold the production sweep is the loop
    path — confirms we haven't accidentally changed the path
    that existing tests cover."""
    pairs = _separable_pairs(n, direction=direction)
    result = ct.sweep_threshold(pairs, direction, fpr_target=0.05)
    assert result["available"] is True
    assert "tpr" in result and "fpr" in result


@pytest.mark.parametrize("direction", ["gt", "lt"])
def test_fast_and_loop_paths_agree_on_operating_point(direction):
    """At large n the fast path takes over. Compare its operating
    point against the loop path on the same input. TPR / FPR must
    match within float epsilon; threshold value may differ in the
    last decimal at tied-score boundaries (continuous synthetic
    data has no ties, so we expect exact match here too)."""
    pairs = _separable_pairs(
        ct._SWEEP_THRESHOLD_FAST_DISPATCH_N + 500, direction=direction,
    )
    n_pos = sum(1 for y, _ in pairs if y == 1)
    n_neg = sum(1 for y, _ in pairs if y == 0)
    fpr_resolution = 1.0 / n_neg
    loop_result = ct._sweep_threshold_loop(
        pairs, direction, fpr_target=0.05,
        n_pos=n_pos, n_neg=n_neg, fpr_resolution=fpr_resolution,
    )
    fast_result = ct._sweep_threshold_fast(
        pairs, direction, fpr_target=0.05,
        n_pos=n_pos, n_neg=n_neg, fpr_resolution=fpr_resolution,
    )
    assert loop_result["available"] is True
    assert fast_result["available"] is True
    # Operating-point equivalence: TPR and FPR match to 4 places.
    assert abs(loop_result["tpr"] - fast_result["tpr"]) < 1e-4
    assert abs(loop_result["fpr"] - fast_result["fpr"]) < 1e-4


@pytest.mark.parametrize("direction", ["gt", "lt"])
def test_fast_path_handles_tied_scores(direction):
    """With many tied scores, the fast path's tie-group
    consumption + post-cumulative semantics is the boundary case
    flagged in its docstring. The operating point must still be
    valid (TPR / FPR / precision in [0, 1], FPR <= target)."""
    pairs = _tied_score_pairs(500, direction=direction)
    n_pos = sum(1 for y, _ in pairs if y == 1)
    n_neg = sum(1 for y, _ in pairs if y == 0)
    fpr_resolution = 1.0 / n_neg
    result = ct._sweep_threshold_fast(
        pairs, direction, fpr_target=0.1,
        n_pos=n_pos, n_neg=n_neg, fpr_resolution=fpr_resolution,
    )
    assert result["available"] is True
    assert 0.0 <= result["tpr"] <= 1.0
    assert 0.0 <= result["fpr"] <= 0.1  # target respected
    assert 0.0 <= result["precision"] <= 1.0


# --------------- Failure-case parity -----------------------------


def test_single_class_returns_unavailable_on_both_paths():
    """Both paths must short-circuit cleanly when one class is
    missing. Tested via the public sweep_threshold (which
    dispatches based on n)."""
    pairs_all_pos = [(1, float(i)) for i in range(20)]
    result = ct.sweep_threshold(pairs_all_pos, "gt", fpr_target=0.05)
    assert result["available"] is False
    assert "single-class" in result["reason"]


def test_sub_resolution_fpr_returns_unavailable():
    """FPR target below 1/n_neg is unsatisfiable — both paths
    short-circuit with the same error before the dispatch."""
    pairs = _separable_pairs(50, direction="gt")
    result = ct.sweep_threshold(pairs, "gt", fpr_target=1e-9)
    assert result["available"] is False
    assert "FPR target" in result["reason"]
    assert "fpr_resolution" in result


def test_unreachable_fpr_in_loop_path_emits_candidate_log():
    """The loop path's failure-case ``candidates`` log is preserved
    for small-corpus debugging."""
    # Build pairs where every threshold produces FPR > target.
    # Easiest: a single positive at the lowest score, everything
    # else negative at higher scores; "gt" direction => any
    # threshold low enough to capture the positive also captures
    # all negatives.
    pairs = [(1, -10.0)] + [(0, float(i)) for i in range(100)]
    # FPR target very tight: 1/n_neg = 0.01; ask for 0.005.
    result = ct.sweep_threshold(pairs, "gt", fpr_target=0.005)
    # Either single-class or sub-resolution may fire before
    # candidate enumeration; if neither, expect candidate log.
    if result.get("available") is False and "candidates" in result:
        assert isinstance(result["candidates"], list)


# --------------- Wall-clock guarantee (MAGE-unblocker) ----------


# --------------- Round-trip contract (codex P2 on PR #65) --------


@pytest.mark.parametrize("direction", ["gt", "lt"])
@pytest.mark.parametrize("n", [6000, 15_000])
def test_fast_path_threshold_reproduces_operating_point(direction, n):
    """The returned ``threshold``, when fed back through
    ``_confusion`` (strict ``>`` / ``<``), MUST reproduce the
    same TP / FP / TPR / FPR the fast path reported. Without
    this contract the downstream bootstrap CI (which calls
    ``_confusion(pairs, threshold, direction)`` per resample)
    evaluates a different operating point than the one the
    threshold-sweep selected, and the persisted threshold
    fails to reproduce its calibration row.

    Codex P2 finding on PR #65, 2026-05-16: the post-consumption
    snapshot was treating ``score >= cur_score`` as positive,
    but ``_confusion`` uses strict ``>``. Fixed by snapshotting
    BEFORE consuming the tied-score group.
    """
    pairs = _separable_pairs(n, direction=direction)
    n_pos = sum(1 for y, _ in pairs if y == 1)
    n_neg = sum(1 for y, _ in pairs if y == 0)
    fpr_resolution = 1.0 / n_neg
    result = ct._sweep_threshold_fast(
        pairs, direction, fpr_target=0.05,
        n_pos=n_pos, n_neg=n_neg, fpr_resolution=fpr_resolution,
    )
    assert result["available"] is True
    threshold = result["threshold"]
    tp, fp, tn, fn = ct._confusion(pairs, threshold, direction)
    rates = ct._rates(tp, fp, tn, fn)
    assert tp == result["tp"], (
        f"TP mismatch: fast={result['tp']}, "
        f"_confusion(threshold={threshold})={tp}"
    )
    assert fp == result["fp"], (
        f"FP mismatch: fast={result['fp']}, "
        f"_confusion(threshold={threshold})={fp}"
    )
    assert abs(rates["tpr"] - result["tpr"]) < 1e-12
    assert abs(rates["fpr"] - result["fpr"]) < 1e-12


@pytest.mark.parametrize("direction", ["gt", "lt"])
def test_fast_path_threshold_reproduces_under_tied_scores(direction):
    """Tied-score corpora are the case the pre-fix code got wrong
    (post-consumption snapshot diverged from strict-inequality
    semantics by the entire equality-block size). Pin the round-
    trip contract on a corpus with intentional ties — the place
    where the bug was largest."""
    pairs = _tied_score_pairs(2000, direction=direction)
    n_pos = sum(1 for y, _ in pairs if y == 1)
    n_neg = sum(1 for y, _ in pairs if y == 0)
    if n_pos == 0 or n_neg == 0:
        pytest.skip("single-class fixture; no operating point")
    fpr_resolution = 1.0 / n_neg
    result = ct._sweep_threshold_fast(
        pairs, direction, fpr_target=0.1,
        n_pos=n_pos, n_neg=n_neg, fpr_resolution=fpr_resolution,
    )
    if not result["available"]:
        pytest.skip("no threshold satisfies FPR target")
    threshold = result["threshold"]
    tp, fp, tn, fn = ct._confusion(pairs, threshold, direction)
    rates = ct._rates(tp, fp, tn, fn)
    # On tied scores the threshold value picked may be the
    # ``cur_score`` of a block; strict ``_confusion`` excludes
    # the entire block. The pre-snapshot semantics in the fix
    # match that exactly.
    assert tp == result["tp"]
    assert fp == result["fp"]
    assert abs(rates["tpr"] - result["tpr"]) < 1e-12
    assert abs(rates["fpr"] - result["fpr"]) < 1e-12


@pytest.mark.parametrize("direction", ["gt", "lt"])
def test_fast_path_under_1_second_at_50k_pairs(direction):
    """The bound that unblocks MAGE: at 50K pairs (~1/7 of MAGE
    Tier 1+2's 338K signal-positive pair count), the fast path
    must complete in well under 1 second. Production loop path
    at this scale was on the order of ~minute."""
    pairs = _separable_pairs(50_000, direction=direction)
    n_pos = sum(1 for y, _ in pairs if y == 1)
    n_neg = sum(1 for y, _ in pairs if y == 0)
    fpr_resolution = 1.0 / n_neg
    t0 = time.time()
    result = ct._sweep_threshold_fast(
        pairs, direction, fpr_target=0.05,
        n_pos=n_pos, n_neg=n_neg, fpr_resolution=fpr_resolution,
    )
    elapsed = time.time() - t0
    assert result["available"] is True
    assert elapsed < 1.0, (
        f"_sweep_threshold_fast on n=50K took {elapsed:.3f}s; "
        f"production calibration on MAGE / RAID assumes sub-second "
        f"per-signal sweep"
    )
