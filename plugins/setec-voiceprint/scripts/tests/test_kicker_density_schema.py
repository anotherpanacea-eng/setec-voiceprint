#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on kicker_density.

Wave 2 of the output-schema unification track. kicker_density is
called as a function by variance_audit and aesthetic_authority_audit;
the function's return shape stays legacy for those consumers. The
envelope is added on the CLI path only via build_audit_payload.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import kicker_density as kd  # type: ignore


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
        "The committee deliberated through the afternoon. "
        "The room was warm. The decision came down to a single "
        "vote. Maria signed it. Time passed. The agenda continued. "
        "What matters is that the proposal moved.\n\n"
        "A second paragraph proceeds with care. There were "
        "concerns about scope. There were concerns about budget. "
        "Adjustment is hard.\n\n"
        "A third paragraph rests its case. Daria reviewed the "
        "details. Stakeholders deferred to the working group. "
        "The work was done.\n\n"
        "The dashboard now shows progress. Numbers do not lie."
    )


@pytest.fixture
def envelope():
    text = _sample_text()
    block = kd.kicker_density(text, nlp=None)
    return kd.build_audit_payload(
        block,
        target_path=Path("draft.md"),
        text=text,
    )


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "smoothing_diagnosis"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "kicker_density"
        assert envelope["version"] == kd.SCRIPT_VERSION

    def test_target_words_counts_from_text(self, envelope):
        assert envelope["target"]["words"] > 0


class TestResultsPayload:
    def test_results_carries_legacy_block(self, envelope):
        r = envelope["results"]
        assert r["signal_path"] == "aic_8_9.kicker_density"
        assert r["family"] == "aic-9-closure-inflation"
        assert "value" in r
        assert "spacing_variance" in r
        assert "paragraphs" in r
        assert "diagnostics" in r

    def test_legacy_claim_license_tag_preserved(self, envelope):
        # Function-call consumers (variance_audit) read this tag.
        assert envelope["results"]["claim_license"] == "voice_diagnostic"

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "signal_path", "family", "value", "spacing_variance",
            "polarity", "status", "paragraphs", "diagnostics",
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

    def test_comparison_set_carries_diagnostics(self, envelope):
        cs = envelope["claim_license"]["comparison_set"]
        assert "total_paragraphs" in cs
        assert "kicker_count" in cs
        assert "word_limit" in cs

    def test_rendered_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestFunctionContractStaysLegacy:
    def test_kicker_density_function_returns_legacy_block_shape(self):
        """variance_audit and aesthetic_authority_audit call
        kicker_density() as a function and read top-level
        `signal_path` / `family` / `value` keys. The migration must
        not change the function's return shape.
        """
        block = kd.kicker_density(_sample_text(), nlp=None)
        assert block["signal_path"] == "aic_8_9.kicker_density"
        assert block["family"] == "aic-9-closure-inflation"
        assert "value" in block
        assert block["task_surface"] == "smoothing_diagnosis"
        assert block["claim_license"] == "voice_diagnostic"


class TestBaselinePath:
    def test_function_baseline_comparison_lives_under_results(self):
        text = _sample_text()
        block = kd.kicker_density(
            text, nlp=None,
            baseline_value=0.10,
            baseline_source="test_register",
        )
        envelope = kd.build_audit_payload(
            block, target_path=Path("draft.md"), text=text,
        )
        bc = envelope["results"]["baseline_comparison"]
        assert bc["baseline_source"] == "test_register"
        assert bc["baseline_value"] == 0.10
