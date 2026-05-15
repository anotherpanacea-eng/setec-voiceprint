#!/usr/bin/env python3
"""Tests for the two bootstrap-CI engines in
``calibrate_thresholds.fixed_threshold_bootstrap_ci``.

The loop engine is the bit-exact reference for pre-1.60 ledger
entries; the numpy engine is the 50-200x-faster vectorized
implementation introduced in 1.60.0. These tests cover:

  - Identical return-dict schema across engines (so the
    aggregator and ledger consumers don't break when the engine
    swaps).
  - Statistical equivalence: CI bounds from both engines lie
    within Monte Carlo noise for 2000+ resamples on the same
    seed.
  - Engine-dispatch correctness: the ``engine=`` kwarg routes
    to the right implementation; unknown engines raise.
  - Edge cases: empty input, single-class input, n=1 trivial,
    threshold beyond observed scores.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))

from calibrate_thresholds import (  # type: ignore  # noqa: E402
    _fixed_threshold_bootstrap_ci_loop,
    _fixed_threshold_bootstrap_ci_numpy,
    fixed_threshold_bootstrap_ci,
)


def _make_pairs(n: int, *, seed: int = 0) -> list[tuple[int, float]]:
    """Build a deterministic mock pair list. Half label=1 with
    scores normally clustered around 1.0, half label=0 around
    -1.0 — well-separated so per-resample stats are stable."""
    import random as _r
    rng = _r.Random(seed)
    pairs: list[tuple[int, float]] = []
    for i in range(n):
        label = 1 if i % 2 == 0 else 0
        # Center positives at +1, negatives at -1, both with
        # sigma=0.5. A threshold of 0.0 should give roughly
        # symmetric TPR/FPR.
        score = (1.0 if label == 1 else -1.0) + rng.gauss(0, 0.5)
        pairs.append((label, score))
    return pairs


# ----- Dispatcher behaviour -------------------------------------


class TestEngineDispatch:
    """The public ``fixed_threshold_bootstrap_ci`` dispatches on
    the ``engine`` kwarg. Default is ``"loop"`` for bit-exact
    backward compat."""

    def test_default_engine_is_loop(self):
        pairs = _make_pairs(50)
        result = fixed_threshold_bootstrap_ci(
            pairs, threshold=0.0, direction="gt",
            resamples=100, confidence=0.95, seed=42,
        )
        assert result is not None
        assert result["engine"] == "loop"

    def test_explicit_loop_engine(self):
        pairs = _make_pairs(50)
        result = fixed_threshold_bootstrap_ci(
            pairs, threshold=0.0, direction="gt",
            resamples=100, confidence=0.95, seed=42,
            engine="loop",
        )
        assert result is not None
        assert result["engine"] == "loop"

    def test_numpy_engine_returns_marker(self):
        pairs = _make_pairs(50)
        result = fixed_threshold_bootstrap_ci(
            pairs, threshold=0.0, direction="gt",
            resamples=100, confidence=0.95, seed=42,
            engine="numpy",
        )
        assert result is not None
        assert result["engine"] == "numpy"

    def test_unknown_engine_raises(self):
        pairs = _make_pairs(10)
        with pytest.raises(ValueError, match="Unknown bootstrap engine"):
            fixed_threshold_bootstrap_ci(
                pairs, threshold=0.0, direction="gt",
                resamples=10, confidence=0.95, seed=42,
                engine="cuda",
            )


# ----- Schema parity --------------------------------------------


class TestSchemaParity:
    """Both engines must return the same dict-key set so the
    aggregator can consume either output. The new ``engine``
    field is the only intentional schema delta."""

    def test_schema_keys_identical_except_engine(self):
        pairs = _make_pairs(100)
        loop_out = _fixed_threshold_bootstrap_ci_loop(
            pairs, threshold=0.0, direction="gt",
            resamples=200, confidence=0.95, seed=42,
        )
        numpy_out = _fixed_threshold_bootstrap_ci_numpy(
            pairs, threshold=0.0, direction="gt",
            resamples=200, confidence=0.95, seed=42,
        )
        assert loop_out is not None
        assert numpy_out is not None
        assert set(loop_out.keys()) == set(numpy_out.keys())

    def test_ci_field_shapes_match(self):
        pairs = _make_pairs(100)
        for engine in ("loop", "numpy"):
            result = fixed_threshold_bootstrap_ci(
                pairs, threshold=0.0, direction="gt",
                resamples=200, confidence=0.95, seed=42,
                engine=engine,
            )
            assert result is not None
            for key in ("tpr_ci", "fpr_ci", "precision_ci"):
                assert key in result
                assert isinstance(result[key], list)
                assert len(result[key]) == 2  # [lo, hi]
                assert result[key][0] <= result[key][1]


# ----- Statistical equivalence ---------------------------------


class TestStatisticalEquivalence:
    """The whole point of the vectorized engine is to be a
    drop-in replacement for the loop engine. Bit-exact agreement
    is impossible (different RNG streams), but CI bounds should
    converge to within Monte Carlo noise."""

    def test_ci_bounds_within_noise_for_well_separated_classes(self):
        # Well-separated classes (centered at +1 and -1, sigma 0.5)
        # mean both engines see effectively the same true TPR/FPR;
        # bootstrap-CI noise across 2000 resamples should give
        # bounds that agree to 3 significant figures.
        pairs = _make_pairs(500, seed=7)
        loop_out = _fixed_threshold_bootstrap_ci_loop(
            pairs, threshold=0.0, direction="gt",
            resamples=2000, confidence=0.95, seed=42,
        )
        numpy_out = _fixed_threshold_bootstrap_ci_numpy(
            pairs, threshold=0.0, direction="gt",
            resamples=2000, confidence=0.95, seed=42,
        )
        assert loop_out is not None and numpy_out is not None
        # Tolerance: each CI bound should agree to within 0.03
        # (3 percentage points) at this N and resample count.
        # Wider than strict Monte Carlo theory predicts because
        # bootstrap-CI tails can be noisy; 0.03 is generous but
        # still tight enough to catch implementation bugs.
        for key in ("tpr_ci", "fpr_ci", "precision_ci"):
            l_lo, l_hi = loop_out[key]
            n_lo, n_hi = numpy_out[key]
            assert abs(l_lo - n_lo) < 0.03, (
                f"{key}[0]: loop={l_lo} numpy={n_lo}"
            )
            assert abs(l_hi - n_hi) < 0.03, (
                f"{key}[1]: loop={l_hi} numpy={n_hi}"
            )

    def test_resample_counts_match(self):
        """Both engines should drop the same proportion of
        single-class resamples and report identical (or near-
        identical) ``resamples`` counts. With well-separated
        well-balanced inputs basically no resamples should be
        dropped, so the counts should match exactly."""
        pairs = _make_pairs(200, seed=11)
        loop_out = _fixed_threshold_bootstrap_ci_loop(
            pairs, threshold=0.0, direction="gt",
            resamples=500, confidence=0.95, seed=42,
        )
        numpy_out = _fixed_threshold_bootstrap_ci_numpy(
            pairs, threshold=0.0, direction="gt",
            resamples=500, confidence=0.95, seed=42,
        )
        assert loop_out is not None and numpy_out is not None
        # Tight bound: |delta| <= 1 (in case one engine's RNG
        # produces exactly one all-positive or all-negative
        # resample the other doesn't).
        assert abs(loop_out["resamples"] - numpy_out["resamples"]) <= 1


# ----- Direction handling --------------------------------------


class TestDirectionAware:
    """Both engines must respect ``direction='lt'`` (predict
    positive when score < threshold) and ``direction='gt'``
    (predict positive when score > threshold). Polarity
    inversion is one of the cathedral correctness gates."""

    @pytest.mark.parametrize("engine", ["loop", "numpy"])
    def test_gt_direction_picks_correct_class(self, engine):
        # Centered-around-+1 positives vs -1 negatives, threshold
        # 0.0, ``gt`` direction → high TPR expected.
        pairs = _make_pairs(500, seed=13)
        result = fixed_threshold_bootstrap_ci(
            pairs, threshold=0.0, direction="gt",
            resamples=200, confidence=0.95, seed=42,
            engine=engine,
        )
        assert result is not None
        # TPR CI's lower bound should be well above 0.5 (the
        # synthetic data has well-separated classes).
        assert result["tpr_ci"][0] > 0.7

    @pytest.mark.parametrize("engine", ["loop", "numpy"])
    def test_lt_direction_inverts(self, engine):
        # Same data, threshold 0.0, but ``lt`` direction means
        # predict-positive-when-score-<-0. That inverts which
        # class gets called positive at this threshold: the
        # actual-negatives (centered at -1) score below 0,
        # actual-positives (centered at +1) score above. So
        # the bootstrap TPR (true-positive rate among
        # actual-positives, who score >0) should be LOW.
        pairs = _make_pairs(500, seed=13)
        result = fixed_threshold_bootstrap_ci(
            pairs, threshold=0.0, direction="lt",
            resamples=200, confidence=0.95, seed=42,
            engine=engine,
        )
        assert result is not None
        assert result["tpr_ci"][1] < 0.3

    def test_invalid_direction_raises_numpy(self):
        pairs = _make_pairs(50)
        with pytest.raises(ValueError, match="direction must"):
            _fixed_threshold_bootstrap_ci_numpy(
                pairs, threshold=0.0, direction="bogus",
                resamples=100, confidence=0.95, seed=42,
            )


# ----- Edge cases ----------------------------------------------


class TestEdgeCases:
    @pytest.mark.parametrize("engine", ["loop", "numpy"])
    def test_empty_pairs_returns_none(self, engine):
        result = fixed_threshold_bootstrap_ci(
            [], threshold=0.0, direction="gt",
            resamples=100, confidence=0.95, seed=42,
            engine=engine,
        )
        assert result is None

    @pytest.mark.parametrize("engine", ["loop", "numpy"])
    def test_single_class_pairs_no_valid_resamples(self, engine):
        # All label=1 → every resample is "all positive", which
        # the both-classes-present filter drops. Expect None.
        pairs: list[tuple[int, float]] = [
            (1, float(i)) for i in range(20)
        ]
        result = fixed_threshold_bootstrap_ci(
            pairs, threshold=0.0, direction="gt",
            resamples=200, confidence=0.95, seed=42,
            engine=engine,
        )
        # Both engines should agree on None for this pathological
        # input.
        assert result is None

    def test_threshold_at_extreme_returns_consistent_bounds(self):
        """Threshold below every observed score (gt direction):
        every row predicted positive. TPR = 1, FPR = 1. Both
        engines should report CIs tightly around 1.0."""
        pairs = [(1 if i % 2 else 0, float(i)) for i in range(100)]
        for engine in ("loop", "numpy"):
            result = fixed_threshold_bootstrap_ci(
                pairs, threshold=-100.0, direction="gt",
                resamples=200, confidence=0.95, seed=42,
                engine=engine,
            )
            assert result is not None
            # All-positive predictions → TPR == 1 across all
            # resamples → CI = [1.0, 1.0].
            assert result["tpr_ci"] == [1.0, 1.0]
            assert result["fpr_ci"] == [1.0, 1.0]


# ----- Performance smoke (not a regression test) ---------------


class TestPerformanceSmoke:
    """Sanity-check the numpy engine is meaningfully faster than
    the loop engine on a moderately-sized input. Not a strict
    benchmark — those are too sensitive to per-host noise — but
    a coarse upper bound: numpy must beat loop on N=5000, R=500.
    If this test ever fails it means the vectorized
    implementation has regressed badly enough to be slower than
    pure Python, which would be a real bug."""

    def test_numpy_faster_than_loop_on_5k_rows_500_resamples(self):
        import time
        pairs = _make_pairs(5000, seed=21)
        t0 = time.perf_counter()
        loop_out = _fixed_threshold_bootstrap_ci_loop(
            pairs, threshold=0.0, direction="gt",
            resamples=500, confidence=0.95, seed=42,
        )
        loop_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        numpy_out = _fixed_threshold_bootstrap_ci_numpy(
            pairs, threshold=0.0, direction="gt",
            resamples=500, confidence=0.95, seed=42,
        )
        numpy_s = time.perf_counter() - t0
        assert loop_out is not None and numpy_out is not None
        # At N=5K, R=500 the loop takes ~10s and numpy ~0.1s on
        # a typical box (100x). Assert at least 5x to leave
        # headroom for slow CI runners; if the ratio drops below
        # this something has gone wrong.
        assert numpy_s * 5 < loop_s, (
            f"numpy ({numpy_s:.2f}s) should be >=5x faster than "
            f"loop ({loop_s:.2f}s) at N=5000, R=500"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
