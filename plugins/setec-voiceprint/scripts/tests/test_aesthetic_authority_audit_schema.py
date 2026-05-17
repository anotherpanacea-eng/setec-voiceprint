#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on aesthetic_authority_audit.

Wave 2 of the output-schema unification track. The compound audit
preserves its legacy block under ``results`` so function-call
consumers (variance_audit) stay green; the envelope adds the
canonical top-level metadata + structured claim_license.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import aesthetic_authority_audit as aaa  # type: ignore


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


def _fake_audit_block(register: str = "contemporary_essay") -> dict:
    """Construct a minimal block matching the audit function's return
    shape. Avoids the spaCy + Brysbaert dependencies the real audit
    needs while still exercising build_audit_payload's plumbing.
    """
    return {
        "signal_path": "aic_8_9.aesthetic_authority_audit",
        "family": "aic-8-9-compound",
        "status": "provisional",
        "task_surface": "smoothing_diagnosis",
        "claim_license": "voice_diagnostic",
        "aic_9_kicker_density": {
            "value": 0.12,
            "paragraphs": [{"paragraph_index": 0, "is_kicker": True}],
        },
        "aic_8_image_conjunction": {
            "value": 1.4,
            "conjunctions": [],
            "diagnostics": {
                "total_tokens": 2400,
                "total_paragraphs": 18,
                "conjunction_count": 4,
            },
        },
        "aic_8_prestige_metaphor": {
            "value": 0.8,
            "conjunctions": [],
            "diagnostics": {
                "total_tokens": 2400,
                "total_paragraphs": 18,
                "conjunction_count": 4,
            },
        },
        "compound": {
            "kicker_paragraph_count": 3,
            "kicker_with_image_conjunction_count": 2,
            "kicker_with_prestige_metaphor_count": 1,
            "all_three_co_occurrence_count": 1,
            "kicker_with_image_conjunction_rate": 0.67,
            "kicker_with_prestige_metaphor_rate": 0.33,
            "all_three_co_occurrence_rate": 0.33,
            "signal_path": "aic_8_9.aesthetic_authority_compound",
            "family": "aic-8-9-compound",
            "task_surface": "smoothing_diagnosis",
            "claim_license": "voice_diagnostic",
        },
        "diagnostics": {
            "register": register,
            "thresholds": {
                "kicker_word_limit": 15,
                "t1_concreteness_gap": 2.5,
                "t2_embedding_similarity": 0.4,
                "t3_scatter_entropy": 0.7,
            },
            "use_wordnet": True,
        },
    }


@pytest.fixture
def envelope():
    block = _fake_audit_block()
    text = "Sample prose. " * 400
    return aaa.build_audit_payload(
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
        assert envelope["tool"] == "aesthetic_authority_audit"
        assert envelope["version"] == aaa.SCRIPT_VERSION

    def test_available(self, envelope):
        assert envelope["available"] is True


class TestResultsPayload:
    def test_results_carries_legacy_block(self, envelope):
        r = envelope["results"]
        assert r["signal_path"] == "aic_8_9.aesthetic_authority_audit"
        assert r["family"] == "aic-8-9-compound"
        assert "aic_9_kicker_density" in r
        assert "aic_8_image_conjunction" in r
        assert "aic_8_prestige_metaphor" in r
        assert "compound" in r

    def test_legacy_claim_license_tag_preserved_in_inner_blocks(self, envelope):
        # The function-level "claim_license: voice_diagnostic" tag on
        # the legacy block stays for downstream function-call
        # consumers like variance_audit. The envelope's top-level
        # claim_license is the new structured 11-key dict; do not
        # confuse the two.
        assert envelope["results"]["claim_license"] == "voice_diagnostic"

    def test_envelope_does_not_contain_legacy_top_keys(self, envelope):
        for legacy in (
            "signal_path", "family", "status",
            "aic_9_kicker_density", "aic_8_image_conjunction",
            "aic_8_prestige_metaphor", "compound", "diagnostics",
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

    def test_substantive_text(self, envelope):
        assert len(envelope["claim_license"]["licenses"]) > 80
        assert len(envelope["claim_license"]["does_not_license"]) > 80

    def test_rendered_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )

    def test_comparison_set_carries_register(self, envelope):
        cs = envelope["claim_license"]["comparison_set"]
        assert cs["register"] == "contemporary_essay"


class TestTargetMetadata:
    def test_target_words_from_diagnostics(self, envelope):
        # 2400 from the synthetic block's diagnostics.total_tokens
        assert envelope["target"]["words"] == 2400

    def test_register_lifted_to_target(self, envelope):
        assert envelope["target"]["register"] == "contemporary_essay"


class TestNoRegisterPath:
    def test_no_register_omits_target_register(self):
        block = _fake_audit_block(register=None)
        block["diagnostics"]["register"] = None
        text = "Sample. " * 200
        envelope = aaa.build_audit_payload(
            block, target_path=Path("x.md"), text=text,
        )
        assert "register" not in envelope["target"]
