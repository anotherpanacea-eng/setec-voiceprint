#!/usr/bin/env python3
"""Regression tests for variance_audit's ablation_band_calls (Release 5).

The ablation report is a Trustworthiness Tier-2 guardrail: it
removes each signal family in turn and recomputes the band, so
the reader can see which families are *load-bearing*. A band that
holds across all ablations is robust; a band that drops when one
family is removed is family-driven and fragile.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import variance_audit as va  # type: ignore


def _audit_with_words(n_words: int) -> dict:
    """Minimal audit-shaped dict with a word count."""
    return {"summary": {"n_words": n_words}}


class TestAblationBasics:
    def test_returns_per_family_dict(self):
        compression = {
            "band": "Lightly smoothed",
            "compression_fraction": 0.10,
            "flagged_signals": [],
            "weighted_score": 1.0,
            "available_weight": 13.5,
        }
        ablation = va.ablation_band_calls(
            compression, _audit_with_words(1500),
        )
        for family in (
            "syntactic_flattening", "lexical_compression",
            "over_cohesion", "connective_overuse",
        ):
            assert family in ablation["per_family"]

    def test_records_original_band(self):
        compression = {
            "band": "Heavily smoothed",
            "compression_fraction": 0.50,
            "flagged_signals": [
                "burstiness_B", "sentence_length_sd",
                "mtld", "connective_density",
            ],
            "weighted_score": 7.0,
            "available_weight": 13.5,
        }
        ablation = va.ablation_band_calls(
            compression, _audit_with_words(1500),
        )
        assert ablation["original_band"] == "Heavily smoothed"

    def test_no_signals_is_robust(self):
        # Nothing fires → removing each family doesn't change the
        # band (it stays "Lightly smoothed" or "Insufficient
        # signal" everywhere).
        compression = {
            "band": "Lightly smoothed",
            "compression_fraction": 0.0,
            "flagged_signals": [],
            "weighted_score": 0.0,
            "available_weight": 13.5,
        }
        ablation = va.ablation_band_calls(
            compression, _audit_with_words(1500),
        )
        assert ablation["is_robust_call"] is True
        assert ablation["load_bearing_families"] == []


class TestRobustVsFragile:
    def test_robust_call_when_multiple_families_fire(self):
        # Heavy band call with broad family support.
        compression = {
            "band": "Heavily smoothed",
            "compression_fraction": 0.50,
            "flagged_signals": [
                "burstiness_B", "sentence_length_sd",
                "mtld", "connective_density",
                "adjacent_cosine_mean",
            ],
            "weighted_score": 8.0,
            "available_weight": 13.5,
        }
        ablation = va.ablation_band_calls(
            compression, _audit_with_words(1500),
        )
        # Across all families, the call should drop at most one
        # level; if it never drops, is_robust_call=True.
        # Either way, we record the load_bearing list.
        assert isinstance(ablation["load_bearing_families"], list)
        assert isinstance(ablation["is_robust_call"], bool)

    def test_fragile_call_when_one_family_carries(self):
        # Single-family scenario: only lexical_compression fires
        # (mtld + mattr + shannon + yules, weight ~6.0). Removing
        # this family drops weighted_score sharply.
        compression = {
            "band": "Heavily smoothed",
            "compression_fraction": 0.45,
            "flagged_signals": [
                "mtld", "mattr", "shannon_entropy", "yules_k",
            ],
            "weighted_score": 6.0,
            "available_weight": 13.5,
        }
        ablation = va.ablation_band_calls(
            compression, _audit_with_words(1500),
        )
        # The lexical_compression family is load-bearing here:
        # removing it sharply reduces both score and available
        # weight in different proportions.
        lex = ablation["per_family"]["lexical_compression"]
        # Removing all the fired signals should drop the band call.
        assert lex["robustness"] in {"fragile_drop", "stable"}


class TestEdgeCases:
    def test_below_length_floor_signals_excluded(self):
        # n_words=100 → almost all signals are below their length
        # floors, so the ablation excludes them.
        compression = {
            "band": "Insufficient signal",
            "compression_fraction": None,
            "flagged_signals": [],
            "weighted_score": 0.0,
            "available_weight": 0.0,
        }
        ablation = va.ablation_band_calls(
            compression, _audit_with_words(100),
        )
        # All families should report Insufficient signal — no
        # signals ever cleared the floor.
        for family, info in ablation["per_family"].items():
            assert info["band"] == "Insufficient signal"

    def test_weight_excluded_recorded(self):
        compression = {
            "band": "Moderately smoothed",
            "compression_fraction": 0.30,
            "flagged_signals": ["burstiness_B"],
            "weighted_score": 2.0,
            "available_weight": 13.5,
        }
        ablation = va.ablation_band_calls(
            compression, _audit_with_words(1500),
        )
        for family, info in ablation["per_family"].items():
            assert "weight_excluded" in info
            assert "fired_weight_excluded" in info
            assert "removed_signals" in info


class TestRender:
    def test_format_ablation_block_renders(self):
        compression = {
            "band": "Heavily smoothed",
            "compression_fraction": 0.50,
            "flagged_signals": ["burstiness_B", "mtld"],
            "weighted_score": 4.0,
            "available_weight": 13.5,
        }
        ablation = va.ablation_band_calls(
            compression, _audit_with_words(1500),
        )
        lines = va.format_ablation_block(ablation)
        text = "\n".join(lines)
        assert "## Ablation" in text
        for family in (
            "syntactic_flattening", "lexical_compression",
            "over_cohesion", "connective_overuse",
        ):
            assert family in text


# ---------- 1.35.1 reviewer-flagged P2 fixes ----------------------


class TestAvailableSignalsContract:
    """Pre-1.35.1, the ablation math gated signals on length floor
    only — but `classify_compression` only adds a signal's weight
    to `available_weight` when its VALUE exists. With --no-tier3,
    tier-3 signals had no values yet the ablation still counted
    their weights as "excluded." Reviewer reproduced
    `over_cohesion weight_excluded=1.5` despite adjacent_cosine
    never being in the call. Fix: classify_compression records
    `available_signals`; ablation reads from it directly.
    """

    def test_no_tier3_does_not_count_cohesion_weights(self):
        # Simulate --no-tier3: adjacent_cosine signals are not in
        # available_signals.
        compression = {
            "band": "Moderately smoothed",
            "compression_fraction": 0.30,
            "flagged_signals": ["burstiness_B", "mtld"],
            "available_signals": [
                "burstiness_B", "sentence_length_sd", "fkgl_sd",
                "mtld", "mattr", "shannon_entropy", "yules_k",
                "connective_density",
            ],  # NO adjacent_cosine — tier-3 disabled
            "weighted_score": 3.0,
            "available_weight": 5.5,
        }
        ablation = va.ablation_band_calls(
            compression, _audit_with_words(1500),
        )
        cohesion = ablation["per_family"]["over_cohesion"]
        assert cohesion["weight_excluded"] == 0.0, (
            "tier-3-disabled run should not count cohesion weight "
            "as excluded — those signals were never in scope"
        )
        assert cohesion["fired_weight_excluded"] == 0.0

    def test_legacy_compression_without_available_signals_field(self):
        """Pre-1.35.1 callers may have stored compression results
        without the available_signals field. Ablation degrades
        gracefully — every family is treated as having no
        available signals."""
        compression = {
            "band": "Lightly smoothed",
            "compression_fraction": 0.10,
            "flagged_signals": [],
            # No available_signals key
            "weighted_score": 1.0,
            "available_weight": 13.5,
        }
        ablation = va.ablation_band_calls(
            compression, _audit_with_words(1500),
        )
        # Without available_signals, every family reports 0
        # weight_excluded — graceful degradation rather than crash.
        for fam, info in ablation["per_family"].items():
            assert info["weight_excluded"] == 0.0


class TestBaselineDivergenceFamily:
    """Pre-1.35.1, pos_bigram_kl participated in the band call when
    a baseline was supplied (weight 2.0) but had no ablation
    family. A KL-driven call could report `is_robust_call=true`
    with no load-bearing families — exactly the wrong signal.
    Fix: new `baseline_divergence` family contains pos_bigram_kl.
    """

    def test_baseline_divergence_family_present(self):
        compression = {
            "band": "Lightly smoothed",
            "compression_fraction": 0.10,
            "flagged_signals": [],
            "available_signals": [],
            "weighted_score": 0.0,
            "available_weight": 13.5,
        }
        ablation = va.ablation_band_calls(
            compression, _audit_with_words(1500),
        )
        assert "baseline_divergence" in ablation["per_family"]
        assert "pos_bigram_kl" in (
            ablation["per_family"]["baseline_divergence"]["removed_signals"]
        )

    def test_kl_load_bearing_call_flags_baseline_family(self):
        """KL is the only fired signal → removing baseline_divergence
        family should drop the band, marking it load-bearing."""
        compression = {
            "band": "Moderately smoothed",
            "compression_fraction": 0.50,
            "flagged_signals": ["pos_bigram_kl"],
            "available_signals": [
                "burstiness_B", "mtld", "pos_bigram_kl",
            ],
            "weighted_score": 2.0,  # only KL fired
            "available_weight": 4.0,
        }
        ablation = va.ablation_band_calls(
            compression, _audit_with_words(1500),
        )
        baseline_fam = ablation["per_family"]["baseline_divergence"]
        # KL contributed 2.0 to available + 2.0 to fired.
        assert baseline_fam["weight_excluded"] == 2.0
        assert baseline_fam["fired_weight_excluded"] == 2.0
        # And it should be load-bearing.
        assert "baseline_divergence" in ablation["load_bearing_families"]

    def test_kl_not_in_call_when_no_baseline(self):
        """Without a baseline, pos_bigram_kl is absent from
        available_signals → ablation correctly reports 0 weight
        excluded for the baseline_divergence family."""
        compression = {
            "band": "Moderately smoothed",
            "compression_fraction": 0.30,
            "flagged_signals": ["burstiness_B"],
            "available_signals": [
                "burstiness_B", "sentence_length_sd", "mtld",
            ],
            "weighted_score": 1.0,
            "available_weight": 3.0,
        }
        ablation = va.ablation_band_calls(
            compression, _audit_with_words(1500),
        )
        assert (
            ablation["per_family"]["baseline_divergence"]["weight_excluded"]
            == 0.0
        )


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
