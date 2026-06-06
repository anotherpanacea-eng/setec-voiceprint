#!/usr/bin/env python3
"""Regression tests for the compression-polarity fix (2026-06-02).

Guards the bug where variance_audit's compression integrator flagged
high-variance / anti-smoothed prose as "smoothed" because its UNBASELINED
default surprisal directions were the MAGE AI-detection directions, the
OPPOSITE of the standalone surprisal band.

Contract pinned here:
  * the compression integrator's default direction for each surprisal
    signal == the canonical surprisal_backend.SMOOTHED_DIRECTION
    (single source of truth), which itself matches surprisal_audit's
    standalone band semantics;
  * the MAGE AI-detection directions survive only as per-comparator
    overrides (opposite the smoothing default);
  * adjacent_cosine_mean (polarity corpus-unstable) is gated from the
    unbaselined band but participates when a comparator_class is given.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import surprisal_backend as sb  # noqa: E402
import variance_audit as va  # noqa: E402

SURPRISAL_SIGNALS = ("surprisal_mean", "surprisal_sd", "surprisal_acf_lag1")


def test_unbaselined_defaults_match_smoothed_direction():
    """Default direction for each surprisal signal == SMOOTHED_DIRECTION."""
    for sig in SURPRISAL_SIGNALS:
        assert va.COMPRESSION_HEURISTICS[sig].direction == sb.SMOOTHED_DIRECTION[sig], sig


def test_smoothed_direction_matches_standalone_band():
    """SMOOTHED_DIRECTION agrees with surprisal_audit's standalone band:
    mean/sd smoothed = below ``flat_below`` (-> 'lt'); acf smoothed =
    above ``smoothed_above`` (-> 'gt'). This is the cross-module
    direction-consistency the bug report required."""
    import surprisal_audit as sa
    bt = sa.PROVISIONAL_BAND_THRESHOLDS
    assert "flat_below" in bt["mean_surprisal_bits"]
    assert sb.SMOOTHED_DIRECTION["surprisal_mean"] == "lt"
    assert "flat_below" in bt["sd_surprisal_bits"]
    assert sb.SMOOTHED_DIRECTION["surprisal_sd"] == "lt"
    assert "smoothed_above" in bt["acf_lag1"]
    assert sb.SMOOTHED_DIRECTION["surprisal_acf_lag1"] == "gt"


def test_mage_overrides_oppose_smoothing_default():
    """MAGE AI-detection directions are preserved as per-comparator
    overrides, opposite the smoothing default for each surprisal signal."""
    opposite = {"lt": "gt", "gt": "lt"}
    for sig in SURPRISAL_SIGNALS:
        spec = va.COMPRESSION_HEURISTICS[sig]
        mage = (spec.direction_by_comparator or {}).get("mage")
        assert mage == opposite[spec.direction], sig


def _audit_anti_smoothed() -> dict:
    """Audit dict resembling varied / anti-smoothed prose: HIGH surprisal
    SD, near-zero ACF, LOW adjacent cohesion (the bug report's profile)."""
    return {
        "summary": {"n_words": 5000, "n_sentences": 300},
        "tier1": {
            "sentence_length": {"burstiness_B": -0.005, "sd": 7.3},
            "connective_density": {"per_1000_tokens": 0.38},
            "mattr": {"value": 0.804}, "mtld": 87.4, "yules_k": 85.0,
            "shannon_entropy_bits": 9.28, "fkgl": {"sd": 5.7},
        },
        "tier2": {"mdd": {"sd": 0.83}},
        "tier3": {"adjacent_cosine": {"mean": 0.26, "sd": 0.153}},
        "tier4": {"surprisal": {"mean": 4.0, "sd": 3.58,
                                "autocorrelation": {"lag_1": 0.0}}},
    }


def test_unbaselined_does_not_flag_high_sd_or_low_acf():
    """The reported artifact: high SD + near-zero ACF anti-smoothed prose
    must NOT flag surprisal_sd / surprisal_acf_lag1 unbaselined."""
    res = va.classify_compression(_audit_anti_smoothed())
    assert "surprisal_sd" not in res["flagged_signals"]
    assert "surprisal_acf_lag1" not in res["flagged_signals"]


def test_adjacent_cosine_mean_gated_when_unbaselined():
    """adjacent_cosine_mean is gated from the unbaselined band (not
    flagged) but participates (and here flags, 0.26 < 0.60 'lt') when a
    comparator_class is supplied."""
    audit = _audit_anti_smoothed()
    assert "adjacent_cosine_mean" not in va.classify_compression(audit)["flagged_signals"]
    mage = va.classify_compression(audit, comparator_class="mage")
    assert "adjacent_cosine_mean" in mage["flagged_signals"]
