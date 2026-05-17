#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on mimicry_cosplay_audit.

Wave 4 of the output-schema unification track.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mimicry_cosplay_audit as mca  # type: ignore


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
        "The committee deliberated. The proposal landed on Tuesday. "
        "Daria reviewed the budget. The room remained quiet."
    ) * 4


def _idiolect() -> dict:
    return {
        "preservation_list": [
            {"phrase": "the committee", "score": 1.2},
            {"phrase": "landed on Tuesday", "score": 1.1},
        ],
    }


@pytest.fixture
def envelope():
    text = _sample_text()
    audit = mca.audit_cosplay(
        target_text=text,
        idiolect=_idiolect(),
        voice_distance=None,
        variance=None,
    )
    return mca.build_audit_payload(
        audit, target_path=Path("draft.md"), target_text=text,
    )


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "mimicry_cosplay_audit"
        assert envelope["version"] == mca.SCRIPT_VERSION


class TestResultsPayload:
    def test_results_carries_audit_signals(self, envelope):
        r = envelope["results"]
        for k in (
            "idiolect_survival", "voice_distance",
            "pos_bigram_kl", "verdict", "shapes", "thresholds_used",
        ):
            assert k in r, f"missing results key: {k}"

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "idiolect_survival", "voice_distance",
            "verdict", "shapes", "thresholds_used",
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

    def test_comparison_set_carries_verdict(self, envelope):
        cs = envelope["claim_license"]["comparison_set"]
        assert "verdict" in cs

    def test_rendered_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestAiStatusRouting:
    def test_state_routed_caveats_added(self):
        text = _sample_text()
        audit = mca.audit_cosplay(
            target_text=text,
            idiolect=_idiolect(),
            voice_distance=None,
            variance=None,
            target_ai_status="ai_generated_from_outline",
        )
        envelope = mca.build_audit_payload(
            audit, target_path=Path("draft.md"), target_text=text,
        )
        assert envelope["ai_status"] == "ai_generated_from_outline"
        caveats = envelope["claim_license"]["additional_caveats"]
        assert any("outline" in c.lower() for c in caveats)
