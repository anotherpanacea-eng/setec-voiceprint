#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on voice_distance.

Wave 4 of the output-schema unification track. voice_distance compares
a target text against a baseline; both target and baseline are
populated. Function-call consumers (callers of compare_to_baseline)
see no change.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import voice_distance as vd  # type: ignore


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


def _fake_result() -> dict:
    """Mirror compare_to_baseline's return shape with the minimum
    fields build_audit_payload reads. Avoids the spaCy + stylometric
    feature load needed for a real run.
    """
    return {
        "task_surface": "voice_coherence",
        "target_summary": {
            "n_words": 3200,
            "n_sentences": 180,
        },
        "baseline_summary": {
            "n_files": 8,
            "total_words": 22000,
            "mean_words": 2750,
            "min_words": 1200,
            "max_words": 5400,
        },
        "overall": {
            "weighted_delta": 1.4,
            "band": "Moderate drift",
        },
        "families": {
            "function_words": {
                "delta_normalized": 1.2,
                "top_features": [
                    {"feature": "the", "z": 0.5},
                ],
            },
            "char_ngrams_3": {
                "delta_normalized": 1.5,
                "top_features": [
                    {"feature": "th_", "z": 1.0},
                ],
            },
        },
        "warnings": [],
    }


@pytest.fixture
def envelope():
    return vd.build_audit_payload(
        _fake_result(), target_path=Path("draft.md"),
    )


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "voice_distance"
        assert envelope["version"] == vd.SCRIPT_VERSION


class TestTargetAndBaseline:
    def test_target_words_from_target_summary(self, envelope):
        assert envelope["target"]["words"] == 3200

    def test_target_carries_n_sentences(self, envelope):
        assert envelope["target"]["n_sentences"] == 180

    def test_baseline_n_files_and_words(self, envelope):
        assert envelope["baseline"]["n_files"] == 8
        assert envelope["baseline"]["words"] == 22000

    def test_baseline_carries_mean_min_max(self, envelope):
        assert envelope["baseline"]["mean_words"] == 2750
        assert envelope["baseline"]["min_words"] == 1200
        assert envelope["baseline"]["max_words"] == 5400


class TestResultsPayload:
    def test_results_carries_overall_and_families(self, envelope):
        r = envelope["results"]
        assert "overall" in r
        assert "families" in r

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "target_summary", "baseline_summary",
            "overall", "families", "preprocessing",
        ):
            assert legacy not in envelope


class TestClaimLicense:
    def test_structured_block_11_keys(self, envelope):
        assert set(envelope["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_task_surface_matches(self, envelope):
        assert (
            envelope["claim_license"]["task_surface"]
            == envelope["task_surface"]
        )

    def test_comparison_set_carries_distance_summary(self, envelope):
        cs = envelope["claim_license"]["comparison_set"]
        assert cs["band"] == "Moderate drift"
        assert cs["weighted_delta"] == 1.4
        assert cs["n_baseline_files"] == 8

    def test_rendered_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestOptionalBlocks:
    def test_register_match_under_results(self):
        result = _fake_result()
        result["register_match"] = {
            "target_classification": {
                "primary": "literary_fiction",
                "confidence": 0.8,
            },
            "match": {"verdict": "match"},
        }
        envelope = vd.build_audit_payload(
            result, target_path=Path("draft.md"),
        )
        assert "register_match" in envelope["results"]

    def test_length_matched_bootstrap_under_results(self):
        result = _fake_result()
        result["length_matched_bootstrap"] = {
            "available": True,
            "percentile": 0.65,
        }
        envelope = vd.build_audit_payload(
            result, target_path=Path("draft.md"),
        )
        assert "length_matched_bootstrap" in envelope["results"]


class TestWarningsPropagate:
    def test_warnings_forwarded(self):
        result = _fake_result()
        result["warnings"] = ["Small baseline: <20K words."]
        envelope = vd.build_audit_payload(
            result, target_path=Path("draft.md"),
        )
        assert envelope["warnings"] == ["Small baseline: <20K words."]
