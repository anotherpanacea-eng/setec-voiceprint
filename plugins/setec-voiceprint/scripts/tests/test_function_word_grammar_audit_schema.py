#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on function_word_grammar_audit.

Wave 3 of the output-schema unification track.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import function_word_grammar_audit as fwg  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


def _sample_text() -> str:
    return (
        "The committee that gathered in the afternoon was the one "
        "which had been waiting. She said that he would consider "
        "the proposal. Although tired, the team continued. In the "
        "long run, the budget that was approved will determine "
        "whether the project succeeds. Despite the timeline, the "
        "work has begun. From the outset, scope mattered most."
    ) * 4


@pytest.fixture
def envelope():
    text = _sample_text()
    audit = fwg.audit_function_word_grammar(text)
    return fwg.build_audit_payload(
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
        assert envelope["tool"] == "function_word_grammar_audit"
        assert envelope["version"] == fwg.SCRIPT_VERSION


class TestResultsPayload:
    def test_results_carries_audit_signals(self, envelope):
        r = envelope["results"]
        for k in (
            "n_function_words", "function_word_ratio",
            "function_bigrams", "function_bigram_entropy_bits",
            "preposition_counts", "preposition_entropy_bits",
            "subordinator_counts", "auxiliary_chain_count",
            "pronoun_transition", "compression",
        ):
            assert k in r, f"missing results key: {k}"

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "n_words", "n_function_words", "function_word_ratio",
            "function_bigrams", "compression",
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


class TestUnavailable:
    def test_empty_text(self):
        audit = fwg.audit_function_word_grammar("")
        envelope = fwg.build_audit_payload(
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
        audit = fwg.audit_function_word_grammar(text)
        baseline_block = {
            "n_files": 4, "n_words": 16000,
            "preposition_counts_summary": {"of": 200},
        }
        envelope = fwg.build_audit_payload(
            audit,
            target_path=Path("draft.md"),
            baseline_block=baseline_block,
            baseline_comparison={"available": True, "z_scores": {}},
        )
        assert envelope["baseline"]["n_files"] == 4
        assert envelope["baseline"]["words"] == 16000
        assert "preposition_counts_summary" in envelope["baseline"]
        assert envelope["results"]["baseline_comparison"]["available"] is True
