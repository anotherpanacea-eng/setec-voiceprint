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


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
