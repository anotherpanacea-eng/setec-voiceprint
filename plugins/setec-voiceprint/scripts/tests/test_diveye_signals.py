#!/usr/bin/env python3
"""Tests for diveye_signals.py (spec 32, M1) — DivEye surprisal-DIVERSITY
signal aggregation, model-free over injected surprisal series.

Stdlib, deterministic. No model, no torch, no GPU. Covers:

  * The four new signals F5–F8 (delta / acceleration / histogram entropy /
    accel ACF) against hand-computable inputs + edge cases.
  * ``aggregate_diveye_signals`` shape (the 11-key DivEye vector) and the
    reuse-of-surprisal_audit-helpers invariants.
  * The SIGN / direction pin (the surprisal-detector family's shared
    silent-inversion failure mode): AI-like narrow-band series has LOWER
    variance / entropy / delta-SD than a human-like wide-band series.
  * The output_schema range-check trap (REVIEW C1): a negative-skew /
    negative-ACF aggregate passes ``validate_results_bounds`` with NO raise.
  * The separation/posture guard: no torch/transformers/sklearn/xgboost on
    ``import diveye_signals``; no verdict/band/calibration_status symbol; no
    import from the fitness/calibration/discrimination/validation/loop modules.

ESL note: the "AI-like" direction fixture (narrow uniform band) is structurally
indistinguishable from a restricted-register or ESL *human* passage under
``surprisal_entropy``. This is expected — the unit suite deliberately includes
no ESL fixture because the signal alone cannot separate them; that failure mode
is owned by the M2 claim-license ``does_not_license`` block, not by this math.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import diveye_signals as dv  # type: ignore  # noqa: E402


# --------------- F5: delta series ---------------------------------


def test_delta_series_basic():
    s = [1.0, 2.0, 1.5, 3.0, 2.5]
    d = dv.delta_series(s)
    assert d == [1.0, -0.5, 1.5, -0.5]
    assert len(d) == len(s) - 1


def test_delta_series_edge_cases():
    assert dv.delta_series([]) == []
    assert dv.delta_series([4.2]) == []
    two = dv.delta_series([1.0, 3.0])
    assert two == [2.0]
    assert len(two) == 1


# --------------- F6: acceleration series --------------------------


def test_accel_series_basic():
    s = [1.0, 2.0, 1.5, 3.0, 2.5]
    d = dv.delta_series(s)          # [1.0, -0.5, 1.5, -0.5]
    a = dv.accel_series(d)
    assert a == [-1.5, 2.0, -2.0]
    assert len(a) == len(s) - 2


def test_accel_series_edge_cases():
    assert dv.accel_series([]) == []
    assert dv.accel_series([1.0]) == []          # delta len 1 -> []
    two = dv.accel_series([1.0, 3.0])            # delta len 2 -> len 1
    assert two == [2.0]
    assert len(two) == 1


# --------------- F7: histogram entropy ----------------------------


def test_surprisal_entropy_uniform():
    # Constant series -> degenerate (zero range) -> None.
    assert dv.surprisal_histogram_entropy([3.0] * 50) is None

    # A maximally-spread series (one value per bin) approaches log2(n_bins).
    spread = [0.5 + 0.5 * i for i in range(50)]   # 50 distinct ascending values
    e_spread = dv.surprisal_histogram_entropy(spread, n_bins=50)
    assert e_spread is not None
    import math
    assert e_spread == pytest.approx(math.log2(50), abs=0.05)

    # A narrow series (all values in one bin) has LOWER entropy than the spread.
    narrow = [4.0 + 0.001 * (i % 3) for i in range(50)]
    e_narrow = dv.surprisal_histogram_entropy(narrow, n_bins=50)
    assert e_narrow is not None
    assert e_narrow < e_spread

    # Entropy increases as the distribution spreads (monotone sanity check).
    tight = [4.0, 4.0, 4.0, 4.0, 5.0]
    wide = [1.0, 3.0, 5.0, 7.0, 9.0]
    assert (
        dv.surprisal_histogram_entropy(tight, n_bins=10)
        < dv.surprisal_histogram_entropy(wide, n_bins=10)
    )


def test_surprisal_entropy_short():
    assert dv.surprisal_histogram_entropy([4.2]) is None
    assert dv.surprisal_histogram_entropy([]) is None


def test_surprisal_entropy_nonnegative():
    # Entropy is always >= 0 (range-safe under the output_schema >= 0 gate).
    for series in ([1.0, 9.0], [1.0, 2.0, 2.0, 3.0], [0.0, 0.0, 1.0]):
        e = dv.surprisal_histogram_entropy(series, n_bins=8)
        assert e is None or e >= 0.0


# --------------- aggregate: shape + reuse invariants --------------


def test_aggregate_diveye_signals_basic():
    s = [1.0, 2.0, 1.5, 3.0, 2.5, 2.0, 3.5, 1.0, 2.2, 2.8]
    out = dv.aggregate_diveye_signals(s)

    expected_keys = {
        "surprisal_mean", "surprisal_var", "skew", "excess_kurtosis",
        "delta_mean", "delta_sd", "accel_mean", "accel_sd",
        "surprisal_entropy", "accel_acf1", "acf1",
    }
    assert set(out.keys()) == expected_keys
    assert len(out) == 11

    # delta_mean is the mean of the delta series; accel_mean the mean of accel.
    d = dv.delta_series(s)
    a = dv.accel_series(d)
    assert out["delta_mean"] == pytest.approx(sum(d) / len(d))
    assert out["accel_mean"] == pytest.approx(sum(a) / len(a))

    # surprisal_entropy is finite (or None for degenerate) — here it's finite.
    import math
    assert out["surprisal_entropy"] is not None
    assert math.isfinite(out["surprisal_entropy"])
    # surprisal_mean reuses the same mean as surprisal_audit.
    assert out["surprisal_mean"] == pytest.approx(sum(s) / len(s))


def test_aggregate_acf_threshold_and_short_series():
    # Below min_acf_length, both ACF members are None (degenerate-flag posture).
    short = [1.0, 2.0, 1.0, 2.0, 1.0]
    out = dv.aggregate_diveye_signals(short, min_acf_length=30)
    assert out["acf1"] is None
    assert out["accel_acf1"] is None

    # A constant-ish series still yields bounded (non-None where defined) means.
    out2 = dv.aggregate_diveye_signals([3.0])
    assert out2["delta_mean"] == 0.0
    assert out2["accel_mean"] == 0.0
    assert out2["surprisal_entropy"] is None
    assert out2["surprisal_var"] == 0.0


# --------------- SIGN / direction pin -----------------------------


def test_aggregate_diveye_signals_direction():
    """Pin the hypothesised AI-vs-human SIGN of the diversity signals so a
    silent inversion (the family's shared failure mode) is caught in CI.

    NOTE: the AI-like fixture (narrow uniform band) is indistinguishable from
    an ESL / restricted-register *human* passage under these signals. That is
    expected; the M2 claim-license owns that false-positive failure mode.
    """
    # AI-like: narrow band, surprisal values CONCENTRATED near one level
    # (locally smooth — small jitter around 4.0, so few distinct values and a
    # tight histogram). This is the low-diversity profile DivEye hypothesises
    # for machine prose.
    ai_like = [4.0 + 0.05 * ((i * 7) % 5 - 2) for i in range(40)]
    # Human-like: surprisal values SPREAD across a wide range with many distinct
    # levels (locally bursty — creative/idiosyncratic tokens spike). High
    # diversity: wider variance, wider entropy, larger step-to-step moves.
    human_like = [1.0 + 8.0 * ((i * 13 + 5) % 17) / 17.0 for i in range(40)]

    ai = dv.aggregate_diveye_signals(ai_like)
    hu = dv.aggregate_diveye_signals(human_like)

    # Direction (DivEye hypothesis): AI-like is LOWER on variance, histogram
    # entropy, and delta-SD than human-like. Pins the SIGN so a silent
    # inversion is caught.
    assert ai["surprisal_var"] < hu["surprisal_var"]
    assert ai["surprisal_entropy"] < hu["surprisal_entropy"]
    assert ai["delta_sd"] < hu["delta_sd"]


# --------------- output_schema range-check trap (REVIEW C1) -------


def test_aggregate_output_passes_output_schema_bounds():
    """The full aggregate dict must pass validate_results_bounds even when
    skew / kurtosis / ACF are negative — the C1 ship-blocker. A flat
    ``surprisal_skew`` / ``surprisal_acf1`` key would raise; the un-prefixed
    ``skew`` / ``acf1`` / ``accel_acf1`` keys must not.
    """
    from output_schema import validate_results_bounds, OutputValidityError

    # A long, left-skewed / anti-correlated series so skew < 0 and ACF < 0.
    # Alternating high/low gives negative lag-1 ACF; a heavy low tail gives
    # negative skew.
    series = []
    for i in range(60):
        series.append(1.0 if i % 2 == 0 else 6.0)
    # inject a few extreme lows to push skew negative
    series = [0.0, 0.0, 0.0] + series

    out = dv.aggregate_diveye_signals(series, min_acf_length=10)
    assert out["acf1"] is not None and out["acf1"] < 0.0  # anti-correlated

    # The load-bearing assertion: no OutputValidityError on the whole vector.
    try:
        validate_results_bounds({"diveye_features": out})
    except OutputValidityError as exc:  # pragma: no cover
        pytest.fail(f"aggregate tripped the R4 bounds gate: {exc}")


def test_negative_signed_moments_would_trip_prefixed_key():
    """Sanity: prove the trap is real — a SURPRISAL-prefixed signed key WOULD
    raise, which is exactly why the aggregate uses un-prefixed names."""
    from output_schema import validate_results_bounds, OutputValidityError

    # A bare un-prefixed key is fine even when negative.
    validate_results_bounds({"acf1": -0.4})
    validate_results_bounds({"skew": -1.2})

    # The prefixed form trips the >= 0 surprisal/entropy check.
    with pytest.raises(OutputValidityError):
        validate_results_bounds({"surprisal_acf1": -0.4})
    with pytest.raises(OutputValidityError):
        validate_results_bounds({"surprisal_entropy": -0.1})


# --------------- separation / posture guard -----------------------


def test_import_is_stdlib():
    """`import diveye_signals` must not pull torch/transformers/scipy/
    sklearn/xgboost (checked in a clean subprocess)."""
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
        "import diveye_signals\n"
        "heavy = [m for m in "
        "('torch','transformers','scipy','sklearn','xgboost') "
        "if m in sys.modules]\n"
        "print(','.join(heavy))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, check=True,
    )
    assert proc.stdout.strip() == "", (
        f"diveye_signals pulled heavy deps: {proc.stdout.strip()!r}"
    )


def test_separation_guard_no_verdict_symbols():
    # No verdict / band / calibration_status surface on the module.
    for forbidden in ("verdict", "band", "calibration_status", "is_ai",
                      "is_human"):
        assert not hasattr(dv, forbidden)
    # TASK_SURFACE is None — not a registered detection surface.
    assert dv.TASK_SURFACE is None

    # The module source imports none of the forbidden coupling modules.
    src = Path(dv.__file__).read_text(encoding="utf-8")
    for forbidden_mod in ("import fitness", "import calibration",
                          "import binoculars_audit", "import validation_harness",
                          "import setec_signals", "import loop"):
        assert forbidden_mod not in src, (
            f"diveye_signals must not couple to {forbidden_mod!r}"
        )
