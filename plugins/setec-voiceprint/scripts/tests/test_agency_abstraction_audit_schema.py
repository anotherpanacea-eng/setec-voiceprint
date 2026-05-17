#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on agency_abstraction_audit.

Wave 2 of the output-schema unification track (see
``internal/SPEC_output_schema_unification.md``). Tests guard the
envelope shape and the no-legacy-keys invariant so downstream
consumers can pin against ``schema_version: \"1.0\"``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agency_abstraction_audit as aaa  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})

CLAIM_LICENSE_KEYS = frozenset({
    "task_surface", "licenses", "does_not_license", "comparison_set",
    "length_range_words", "register_match", "language_match",
    "fpr_target", "confidence_interval_95", "additional_caveats",
    "references",
})


def _sample_text() -> str:
    return (
        "The committee proposes consideration of the recommendation. "
        "The proposal was reviewed and the timeline was extended. "
        "Implementation will commence following authorization. "
        "Daria signed the agreement on Tuesday. The dashboard "
        "highlighted regional activity. Stakeholders requested "
        "further analysis. The agency-level coordination role was "
        "delegated to the working group. Maria reviewed the budget "
        "with three regional partners. Decisions remained pending "
        "while clarification was sought."
    ) * 8


@pytest.fixture
def envelope():
    text = _sample_text()
    audit = aaa.audit_agency_abstraction(text)
    return aaa.build_audit_payload(
        audit,
        target_path=Path("draft.md"),
        baseline_block=None,
        baseline_comparison=None,
    )


class TestEnvelopeKeys:
    def test_required_top_level_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "smoothing_diagnosis"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "agency_abstraction_audit"
        assert envelope["version"] == aaa.SCRIPT_VERSION

    def test_available_true(self, envelope):
        assert envelope["available"] is True

    def test_target_has_required_subkeys(self, envelope):
        assert "path" in envelope["target"]
        assert "words" in envelope["target"]
        assert envelope["target"]["path"] == "draft.md"
        assert envelope["target"]["words"] > 0

    def test_baseline_null_when_not_supplied(self, envelope):
        assert envelope["baseline"] is None


class TestResultsPayload:
    def test_results_carries_audit_signals(self, envelope):
        r = envelope["results"]
        assert "raw_counts" in r
        assert "densities_per_1k" in r
        assert "entity_to_action_ratio" in r
        assert "compression" in r

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "n_words", "raw_counts", "densities_per_1k",
            "entity_to_action_ratio", "compression",
            "baseline_block", "baseline_comparison",
        ):
            assert legacy not in envelope, (
                f"legacy top-level key {legacy!r} should now live "
                f"inside the envelope's target/baseline/results blocks"
            )


class TestClaimLicense:
    def test_structured_block_11_keys(self, envelope):
        assert set(envelope["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_task_surface_matches(self, envelope):
        assert (
            envelope["claim_license"]["task_surface"]
            == envelope["task_surface"]
        )

    def test_rendered_starts_with_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestUnavailablePath:
    def test_empty_text_emits_well_formed_envelope(self):
        audit = aaa.audit_agency_abstraction("")
        envelope = aaa.build_audit_payload(
            audit,
            target_path=Path("empty.md"),
            baseline_block=None,
            baseline_comparison=None,
        )
        assert envelope["available"] is False
        assert envelope["claim_license"] is None
        assert envelope["claim_license_rendered"] is None
        assert envelope["warnings"]
        assert envelope["results"] == {}


class TestBaselinePath:
    def test_baseline_block_populated(self):
        text = _sample_text()
        audit = aaa.audit_agency_abstraction(text)
        baseline_block = {
            "n_files": 3, "n_words": 12000,
            "per_file_summaries": [
                {"path": "baseline_001", "n_words": 4000},
                {"path": "baseline_002", "n_words": 4000},
                {"path": "baseline_003", "n_words": 4000},
            ],
        }
        envelope = aaa.build_audit_payload(
            audit,
            target_path=Path("draft.md"),
            baseline_block=baseline_block,
            baseline_comparison={"available": True, "z_scores": {}},
        )
        assert envelope["baseline"]["n_files"] == 3
        assert envelope["baseline"]["words"] == 12000
        assert "per_file_summaries" in envelope["baseline"]
        assert envelope["results"]["baseline_comparison"]["available"] is True


class TestSerialization:
    def test_envelope_round_trips_through_json(self, envelope):
        s = json.dumps(envelope, default=str)
        parsed = json.loads(s)
        assert parsed["schema_version"] == "1.0"
        assert parsed["tool"] == "agency_abstraction_audit"
