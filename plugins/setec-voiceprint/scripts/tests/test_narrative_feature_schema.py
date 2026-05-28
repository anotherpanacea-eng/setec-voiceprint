#!/usr/bin/env python3
"""Regression tests for narrative_feature_schema.py.

Pins the schema's structural invariants against the paper's Table 12:

  * exactly 30 features, 33 signals;
  * exactly 3 dual-leaning features ("Subplot Integration", "Reference
    Explicitness", "Dominant Emotional Expression");
  * every signal's leaning is consistent with the sign of its
    paper-reported (human_mean − ai_mean) gap;
  * every signal belongs to a known bundle and dimension;
  * every feature's response_options is non-empty and unique.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import narrative_feature_schema as s  # type: ignore  # noqa: E402


def test_thirty_features_thirty_three_signals():
    """Paper Table 12 has 30 distinct features and 33 signal rows."""
    assert len(s.CORE_FEATURES) == 30, (
        f"expected 30 core features per Russell et al. 2026; "
        f"got {len(s.CORE_FEATURES)}"
    )
    total_signals = sum(len(f.signals) for f in s.CORE_FEATURES)
    assert total_signals == 33, (
        f"expected 33 total signals (Table 12 row count); got "
        f"{total_signals}"
    )


def test_dual_leaning_features():
    """Exactly three features carry both an AI- and a human-leaning
    signal (paper's Table 12 lists each twice in the AI / human halves
    of the appendix)."""
    duals = [f for f in s.CORE_FEATURES if f.is_dual_leaning]
    keys = sorted(f.key for f in duals)
    expected = sorted([
        "subplot_integration",
        "reference_explicitness",
        "dominant_emotional_expression",
    ])
    assert keys == expected, (
        f"dual-leaning feature set drifted from paper: got {keys}"
    )


def test_leaning_matches_gap_sign():
    """For every signal, leaning='ai' iff gap<0 and leaning='human'
    iff gap>0. Catches transcription errors where a paper value was
    copied to the wrong column."""
    for f in s.CORE_FEATURES:
        for sig in f.signals:
            gap = sig.gap
            expected = "human" if gap > 0 else "ai" if gap < 0 else None
            assert sig.leaning == expected, (
                f"{f.key} option={sig.option!r}: leaning "
                f"{sig.leaning!r} inconsistent with gap {gap:+.3f}"
            )


def test_bundles_and_dimensions_are_known():
    for f in s.CORE_FEATURES:
        assert f.dimension in s.DIMENSION_LABELS, (
            f"{f.key}: unknown dimension {f.dimension!r}"
        )
        for sig in f.signals:
            assert sig.bundle in s.BUNDLE_LABELS, (
                f"{f.key}: unknown bundle {sig.bundle!r}"
            )


def test_response_options_nonempty_and_unique():
    for f in s.CORE_FEATURES:
        assert len(f.response_options) > 0, (
            f"{f.key}: empty response_options"
        )
        assert len(set(f.response_options)) == len(f.response_options), (
            f"{f.key}: duplicate response_options"
        )


def test_named_option_signals_reference_real_options():
    """Named-option signals must name an option that actually exists
    in the feature's response_options. Naming an option is legal for
    categorical, multi, and binary features (a binary feature with
    option='yes' degenerates to the same numeric encoding as the
    option=None form but reads more naturally in the per-signal
    contribution table)."""
    for f in s.CORE_FEATURES:
        for sig in f.signals:
            if sig.option is None:
                assert f.feature_type in (
                    "scale", "ordinal", "binary",
                ), (
                    f"{f.key}: option=None on a "
                    f"{f.feature_type} feature"
                )
            else:
                assert f.feature_type in (
                    "categorical", "multi", "binary",
                ), (
                    f"{f.key}: option={sig.option!r} on a "
                    f"{f.feature_type} feature"
                )
                assert sig.option in f.response_options, (
                    f"{f.key}: option {sig.option!r} not in "
                    f"response_options {f.response_options}"
                )


def test_scale_features_use_1_to_5_options():
    for f in s.CORE_FEATURES:
        if f.feature_type == "scale":
            assert list(f.response_options) == [
                "1", "2", "3", "4", "5",
            ], (
                f"{f.key}: scale type but options "
                f"{f.response_options}"
            )


def test_binary_features_use_no_yes():
    for f in s.CORE_FEATURES:
        if f.feature_type == "binary":
            assert set(f.response_options) == {"no", "yes"}, (
                f"{f.key}: binary type but options "
                f"{f.response_options}"
            )


def test_paper_means_are_within_expected_range():
    for f in s.CORE_FEATURES:
        for sig in f.signals:
            # Likerts: [1, 5]. Ordinals: [0, N-1]. Binary / proportions:
            # [0, 1]. The loose bound caught early during transcription.
            for m in (sig.human_mean, sig.ai_mean):
                assert 0.0 <= m <= 5.0, (
                    f"{f.key} {sig.option!r}: mean {m} out of "
                    f"expected envelope"
                )


def test_iter_signals_count_matches_total_signals():
    pairs = list(s.iter_signals())
    assert len(pairs) == sum(len(f.signals) for f in s.CORE_FEATURES)


if __name__ == "__main__":
    import traceback
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                print(f"FAIL {name}")
                traceback.print_exc()
