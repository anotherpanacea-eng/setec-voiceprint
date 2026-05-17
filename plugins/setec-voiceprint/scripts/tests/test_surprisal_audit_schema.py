#!/usr/bin/env python3
"""Pins schema_version 1.0 envelope on surprisal_audit. Wave 5."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import surprisal_audit as sa  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


def _flat_stub(text, **kwargs):
    # Surprisal series that's flat enough to exercise summary math.
    return [4.0, 4.5, 4.2, 4.8, 4.1, 4.6, 4.3, 4.7, 4.0, 4.4] * 10


@pytest.fixture
def envelope():
    audit = sa.audit_surprisal("the cat sat on the mat", score_fn=_flat_stub)
    audit["backend"] = {"id": "stub-model", "revision": "stub"}
    return sa.build_audit_payload(audit, target_path=Path("draft.txt"))


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "surprisal_audit"
        assert envelope["version"] == sa.SCRIPT_VERSION


class TestResultsPayload:
    def test_results_carries_signals(self, envelope):
        r = envelope["results"]
        for k in (
            "n_tokens_scored", "series_length", "summary",
            "top_k_tokens", "sliding_window", "band", "backend",
        ):
            assert k in r

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "n_tokens_scored", "series_length", "summary",
            "top_k_tokens", "sliding_window", "band", "backend",
        ):
            assert legacy not in envelope


class TestUnavailable:
    def test_unavailable_audit(self):
        audit = {"task_surface": "smoothing_diagnosis", "tool": "surprisal_audit",
                 "version": "1.0", "available": False, "reason": "text too short"}
        envelope = sa.build_audit_payload(audit, target_path=Path("x.txt"))
        assert envelope["available"] is False
        assert envelope["claim_license"] is None
        assert "text too short" in envelope["warnings"]


class TestClaimLicense:
    def test_structured(self, envelope):
        assert envelope["claim_license"]["task_surface"] == "smoothing_diagnosis"

    def test_rendered_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )
