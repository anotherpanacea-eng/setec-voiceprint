#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on stance_modality_audit.

Wave 3 of the output-schema unification track.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import stance_modality_audit as sma  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


def _sample_text() -> str:
    return (
        "The committee may consider the proposal. Members must "
        "review the timeline. Clearly the budget is constrained. "
        "Possibly the deadline could be extended. We believe the "
        "team can deliver. It seems reasonable to defer. Critics "
        "argue otherwise. We urge caution. The evidence shows the "
        "approach is viable. To be honest, the timeline is tight."
    ) * 6


@pytest.fixture
def envelope():
    text = _sample_text()
    audit = sma.audit_stance_modality(text)
    return sma.build_audit_payload(
        audit,
        target_path=Path("draft.md"),
        baseline_block=None,
        baseline_comparison=None,
    )


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "stance_modality_audit"
        assert envelope["version"] == sma.SCRIPT_VERSION


class TestResultsPayload:
    def test_results_carries_audit_signals(self, envelope):
        r = envelope["results"]
        for k in (
            "category_counts", "category_densities_per_1k",
            "total_marker_density_per_1k",
            "stance_entropy_bits", "hedge_booster_ratio",
            "compression",
        ):
            assert k in r, f"missing results key: {k}"

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "n_words", "category_counts", "category_densities_per_1k",
            "total_marker_density_per_1k", "stance_entropy_bits",
            "hedge_booster_ratio", "compression",
            "baseline_block", "baseline_comparison",
        ):
            assert legacy not in envelope


class TestClaimLicense:
    def test_structured_block_present(self, envelope):
        cl = envelope["claim_license"]
        assert cl["task_surface"] == "voice_coherence"
        assert len(cl["licenses"]) > 80
        assert len(cl["does_not_license"]) > 80

    def test_rendered_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestAiStatusRouting:
    def test_state_routed_caveats_added(self):
        text = _sample_text()
        audit = sma.audit_stance_modality(text)
        audit["ai_status"] = "ai_generated_from_outline"
        envelope = sma.build_audit_payload(
            audit,
            target_path=Path("draft.md"),
            baseline_block=None,
            baseline_comparison=None,
        )
        assert envelope["ai_status"] == "ai_generated_from_outline"
        caveats = envelope["claim_license"]["additional_caveats"]
        assert any("outline" in c.lower() for c in caveats)


class TestUnavailable:
    def test_empty_text(self):
        audit = sma.audit_stance_modality("")
        envelope = sma.build_audit_payload(
            audit,
            target_path=Path("empty.md"),
            baseline_block=None,
            baseline_comparison=None,
        )
        assert envelope["available"] is False
        assert envelope["claim_license"] is None
        assert envelope["warnings"]


class TestBaseline:
    def test_baseline_block_populated(self):
        text = _sample_text()
        audit = sma.audit_stance_modality(text)
        baseline_block = {
            "n_files": 5, "n_words": 25000,
            "per_category_summary": {"hedge": 8.0},
        }
        envelope = sma.build_audit_payload(
            audit,
            target_path=Path("draft.md"),
            baseline_block=baseline_block,
            baseline_comparison={"available": True, "z_scores": {}},
        )
        assert envelope["baseline"]["n_files"] == 5
        assert envelope["baseline"]["words"] == 25000
        assert "per_category_summary" in envelope["baseline"]
        assert envelope["results"]["baseline_comparison"]["available"] is True
