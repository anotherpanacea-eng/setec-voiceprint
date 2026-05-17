#!/usr/bin/env python3
"""Pins schema_version 1.0 envelope on paragraph_audit. Wave 5."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import paragraph_audit as pa  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


_PROSE = (
    "The committee deliberated.\n\n"
    "Members reviewed the budget. The proposal landed on Tuesday. "
    "Daria signed off after lunch.\n\n"
    "The room was warm. Quiet. Patient.\n\n"
    "By the end of the afternoon, three workstreams advanced and "
    "two stalled, with one waiting on legal review and another "
    "needing a vendor decision.\n\n"
    "Onward.\n\n"
) * 4


@pytest.fixture
def envelope():
    audit = pa.audit_paragraphs(_PROSE)
    return pa.build_audit_payload(
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

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "paragraph_audit"
        assert envelope["version"] == pa.SCRIPT_VERSION


class TestResultsPayload:
    def test_results_carries_rhythm_signals(self, envelope):
        r = envelope["results"]
        for k in (
            "n_paragraphs", "paragraph_word_counts",
            "length_summary", "rhythm_signals", "compression",
        ):
            assert k in r

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "n_paragraphs", "paragraph_word_counts",
            "length_summary", "rhythm_signals", "compression",
        ):
            assert legacy not in envelope


class TestUnavailable:
    def test_empty_text(self):
        audit = pa.audit_paragraphs("")
        envelope = pa.build_audit_payload(
            audit,
            target_path=Path("empty.md"),
            baseline_block=None,
            baseline_comparison=None,
        )
        assert envelope["available"] is False
        assert envelope["claim_license"] is None


class TestClaimLicense:
    def test_structured(self, envelope):
        assert envelope["claim_license"]["task_surface"] == "smoothing_diagnosis"

    def test_rendered_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )
