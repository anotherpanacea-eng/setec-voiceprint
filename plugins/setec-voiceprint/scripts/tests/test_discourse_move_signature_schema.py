#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on discourse_move_signature.

Wave 2 of the output-schema unification track.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import discourse_move_signature as dms  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


def _sample_text() -> str:
    return (
        "First, the committee reviewed the proposal. However, the "
        "timeline remained ambiguous. Moreover, the budget needed "
        "revisiting. In contrast, the original deadline was firm. "
        "Therefore, the schedule was extended. For example, the "
        "implementation phase was pushed back. Nevertheless, the "
        "project remained on track. In summary, three workstreams "
        "advanced. To clarify, scope was narrowed. To be clear, the "
        "committee's mandate did not change. Importantly, the goal "
        "stood firm. To be precise, the deliverable shifted by two "
        "weeks. In short, the project survived."
    ) * 4


@pytest.fixture
def envelope():
    text = _sample_text()
    audit = dms.audit_discourse_moves(text)
    return dms.build_audit_payload(
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
        assert envelope["task_surface"] == "smoothing_diagnosis"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "discourse_move_signature"
        assert envelope["version"] == dms.SCRIPT_VERSION

    def test_target_carries_sentences(self, envelope):
        assert "sentences" in envelope["target"]
        assert envelope["target"]["sentences"] > 0


class TestResultsPayload:
    def test_results_carries_audit_signals(self, envelope):
        r = envelope["results"]
        for k in (
            "category_counts", "category_densities_per_1k",
            "total_marker_density_per_1k",
            "move_sequence", "move_sequence_bigrams",
            "move_sequence_entropy_bits",
            "marked_only_entropy_bits", "relation_distribution",
            "compression",
        ):
            assert k in r, f"missing results key: {k}"

    def test_relation_distribution_flows_into_results(self, envelope):
        """The PDTB relation layer rides in `results` and survives the
        R4 bounds walk (entropy fields are >= 0; fractions/counts/
        densities are unmatched by the surprisal/probability gates)."""
        rel = envelope["results"]["relation_distribution"]
        assert rel["calibration_status"] == "uncalibrated"
        assert rel["buckets"] == [
            "comparison", "contingency", "expansion", "temporal",
        ]
        assert 0.0 <= rel["relation_entropy_bits"] <= rel[
            "relation_entropy_max_bits"
        ] == 2.0

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "n_words", "n_sentences", "category_counts",
            "category_densities_per_1k", "total_marker_density_per_1k",
            "move_sequence", "compression", "baseline_block",
            "baseline_comparison",
        ):
            assert legacy not in envelope


class TestClaimLicense:
    def test_structured_block_present(self, envelope):
        cl = envelope["claim_license"]
        assert cl is not None
        assert cl["task_surface"] == envelope["task_surface"]
        assert cl["licenses"]
        assert cl["does_not_license"]

    def test_rendered_block_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestAiStatusRouting:
    def test_ai_status_flows_through(self):
        text = _sample_text()
        audit = dms.audit_discourse_moves(text)
        audit["ai_status"] = "ai_generated_from_outline"
        envelope = dms.build_audit_payload(
            audit,
            target_path=Path("draft.md"),
            baseline_block=None,
            baseline_comparison=None,
        )
        assert envelope["ai_status"] == "ai_generated_from_outline"
        # State-routed caveats land in additional_caveats per B.3.
        caveats = envelope["claim_license"]["additional_caveats"]
        assert any("outline" in c.lower() for c in caveats)


class TestUnavailable:
    def test_empty_text(self):
        audit = dms.audit_discourse_moves("")
        envelope = dms.build_audit_payload(
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
        audit = dms.audit_discourse_moves(text)
        baseline_block = {
            "n_files": 4, "n_words": 20000,
            "categories_summary": {"contrast": 0.05},
        }
        envelope = dms.build_audit_payload(
            audit,
            target_path=Path("draft.md"),
            baseline_block=baseline_block,
            baseline_comparison={"available": True, "category_density_z_scores": {}},
        )
        assert envelope["baseline"]["n_files"] == 4
        assert envelope["baseline"]["words"] == 20000
        assert "categories_summary" in envelope["baseline"]


class TestSerialization:
    def test_json_round_trip(self, envelope):
        s = json.dumps(envelope, default=str)
        parsed = json.loads(s)
        assert parsed["schema_version"] == "1.0"
        assert parsed["tool"] == "discourse_move_signature"
