#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on punctuation_cadence_audit.

Wave 3 of the output-schema unification track.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import punctuation_cadence_audit as pca  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


def _sample_text() -> str:
    return (
        "The committee — meeting briefly — endorsed the proposal. "
        "Members reviewed the timeline; some objected. The room "
        "(crowded, warm) deliberated. Daria, speaking for the "
        "working group, summarized the concerns. \"What now?\" "
        "asked the chair. She paused. The budget, contested, "
        "shifted again. The deadline holds. To be clear: scope "
        "matters. Did the team consider alternatives? They did, "
        "twice. The vote passed."
    ) * 5


@pytest.fixture
def envelope():
    text = _sample_text()
    audit = pca.audit_punctuation_cadence(text)
    return pca.build_audit_payload(
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
        assert envelope["tool"] == "punctuation_cadence_audit"
        assert envelope["version"] == pca.SCRIPT_VERSION


class TestResultsPayload:
    def test_results_carries_audit_signals(self, envelope):
        r = envelope["results"]
        for k in (
            "n_sentence_final", "raw_counts", "densities_per_1k",
            "sentence_final_distribution", "interruption_grammar",
            "punctuation_bigrams", "comma_period_share", "compression",
        ):
            assert k in r, f"missing results key: {k}"

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "n_words", "n_sentence_final", "raw_counts",
            "densities_per_1k", "sentence_final_distribution",
            "interruption_grammar", "punctuation_bigrams",
            "comma_period_share", "compression",
            "baseline_block", "baseline_comparison",
        ):
            assert legacy not in envelope


class TestClaimLicense:
    def test_structured_block(self, envelope):
        cl = envelope["claim_license"]
        assert cl["task_surface"] == "voice_coherence"
        assert len(cl["licenses"]) > 80

    def test_rendered_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestAiStatusRouting:
    def test_state_routed_caveats_added(self):
        text = _sample_text()
        audit = pca.audit_punctuation_cadence(text)
        audit["ai_status"] = "pre_ai_human"
        envelope = pca.build_audit_payload(
            audit,
            target_path=Path("draft.md"),
            baseline_block=None,
            baseline_comparison=None,
        )
        assert envelope["ai_status"] == "pre_ai_human"
        caveats = envelope["claim_license"]["additional_caveats"]
        assert any("pre-AI" in c or "pre_ai" in c for c in caveats)


class TestUnavailable:
    def test_empty_text(self):
        audit = pca.audit_punctuation_cadence("")
        envelope = pca.build_audit_payload(
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
        audit = pca.audit_punctuation_cadence(text)
        baseline_block = {
            "n_files": 6, "n_words": 30000,
            "comma_period_share_summary": {"mean": 0.65},
        }
        envelope = pca.build_audit_payload(
            audit,
            target_path=Path("draft.md"),
            baseline_block=baseline_block,
            baseline_comparison={"available": True, "z_scores": {}},
        )
        assert envelope["baseline"]["n_files"] == 6
        assert envelope["baseline"]["words"] == 30000
        assert "comma_period_share_summary" in envelope["baseline"]
