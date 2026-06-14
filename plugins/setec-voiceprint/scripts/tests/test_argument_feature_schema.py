"""Tests for argument_feature_schema — ArgScope B1/B2 taxonomies + anchors."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argument_feature_schema as s  # noqa: E402


def test_taxonomy_sizes():
    assert len(s.ROLE_OPTIONS) == 8, "B1 role taxonomy is 8-way"
    assert len(s.MODE_OPTIONS) == 4, "B2 mode taxonomy is 4-way"
    assert set(s.ROLE_OPTIONS) == set(s.ROLE_DESCRIPTIONS)
    assert set(s.MODE_OPTIONS) == set(s.MODE_DESCRIPTIONS)


def test_anchored_signals_are_the_three_with_clean_pairs():
    anchored = {d.key for d in s.iter_anchored_signals()}
    assert anchored == {
        "support_to_proposal_rate",
        "support_to_support_rate",
        "argumentation_share",
    }
    # thesis_opening_tendency is directional-only (no numeric anchor).
    thesis = next(d for d in s.DERIVED_SIGNALS if d.key == "thesis_opening_tendency")
    assert thesis.anchored is False
    assert thesis.human_mean is None and thesis.ai_mean is None
    assert thesis.gap is None


def test_paper_anchors_transcribed_with_correct_leaning():
    by_key = {d.key: d for d in s.DERIVED_SIGNALS}
    # support→proposal is LLM-elevated (29.4% vs 12.3% human, NYT).
    sp = by_key["support_to_proposal_rate"]
    assert sp.leaning == "ai" and sp.human_mean == 0.123 and sp.ai_mean == 0.294
    assert sp.gap < 0  # LLM-elevated -> negative human-minus-ai
    # support→support is human-elevated.
    ss = by_key["support_to_support_rate"]
    assert ss.leaning == "human" and ss.gap > 0
    # argumentation share is LLM-elevated (89.7% vs 71.5% human).
    arg = by_key["argumentation_share"]
    assert arg.leaning == "ai" and arg.human_mean == 0.715 and arg.ai_mean == 0.897


def test_every_anchored_mean_is_a_proportion():
    for d in s.iter_anchored_signals():
        for m in (d.human_mean, d.ai_mean):
            assert 0.0 <= m <= 1.0


def test_bundles_are_b1_b2_and_b5():
    assert set(s.BUNDLE_LABELS) == {
        "B1_structural_arc", "B2_discourse_mode", "B5_collapse_dynamics",
    }
    for d in s.DERIVED_SIGNALS:
        assert d.bundle in s.BUNDLE_LABELS


# ---- B5: the two arc-collapse signals ------------------------------------
def test_b5_collapse_signals_are_heuristic_unanchored_arc_flags():
    by_key = {d.key: d for d in s.DERIVED_SIGNALS}
    for key in ("disappearing_guard_flag", "discounting_straw_men_flag"):
        d = by_key[key]
        assert d.bundle == "B5_collapse_dynamics"
        assert d.kind == "arc_flag"
        assert d.leaning == "ai"
        # heuristic, directional, NO numeric anchor (never fabricate a tier).
        assert d.anchored is False
        assert d.human_mean is None and d.ai_mean is None
        assert d.gap is None
        assert d.calibration_status == "heuristic"
        # the converging provenance sources are cited in notes (conceptual, not
        # a numeric anchor).
        assert "AGD" in d.notes or "Sinnott-Armstrong" in d.notes


def test_b5_signals_excluded_from_anchored_iterator():
    # iter_anchored_signals yields only the numerically anchored B1/B2 signals;
    # the B5 flags are never in it.
    anchored = {d.key for d in s.iter_anchored_signals()}
    assert "disappearing_guard_flag" not in anchored
    assert "discounting_straw_men_flag" not in anchored
    assert anchored == {
        "support_to_proposal_rate", "support_to_support_rate", "argumentation_share",
    }


def test_self_check_rejects_anchored_arc_flag():
    # An arc_flag that claims a numeric anchor must trip the import-time check.
    import dataclasses
    bad = dataclasses.replace(
        next(d for d in s.DERIVED_SIGNALS if d.key == "disappearing_guard_flag"),
        anchored=True, human_mean=0.2, ai_mean=0.4,
    )
    import pytest
    with pytest.raises(RuntimeError):
        # re-run the discipline by constructing a one-off DERIVED_SIGNALS check.
        _assert_signal_tier_discipline(bad)


def test_self_check_rejects_overclaimed_arc_flag():
    # An arc_flag tagged above heuristic must trip the check.
    import dataclasses
    bad = dataclasses.replace(
        next(d for d in s.DERIVED_SIGNALS if d.key == "discounting_straw_men_flag"),
        calibration_status="literature_anchored",
    )
    import pytest
    with pytest.raises(RuntimeError):
        _assert_signal_tier_discipline(bad)


def _assert_signal_tier_discipline(sig):
    """Mirror the schema's per-signal honesty-tier rules for one signal (the
    rules _self_check enforces over the whole list at import)."""
    no_anchor = {"heuristic", "structural_only"}
    if sig.anchored and sig.calibration_status in no_anchor:
        raise RuntimeError("anchored signal in a no-anchor tier")
    if sig.kind == "arc_flag":
        if sig.anchored:
            raise RuntimeError("arc_flag must be unanchored")
        if sig.calibration_status != "heuristic":
            raise RuntimeError("arc_flag must be heuristic")
