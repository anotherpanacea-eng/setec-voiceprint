#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on chapter_distinctiveness_audit.

Wave 5 of the output-schema unification track.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import chapter_distinctiveness_audit as cda  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


def _chapters():
    return [
        {
            "label": "Chapter 1",
            "text": (
                "The forge glowed. Iron sang under the hammer. The "
                "smith counted his blows. The forge glowed brighter. "
            ) * 6,
        },
        {
            "label": "Chapter 2",
            "text": (
                "The river ran quick. The boat tilted at the bend. "
                "Brackish water lapped the hull. The bend opened wide. "
            ) * 6,
        },
        {
            "label": "Chapter 3",
            "text": (
                "The committee deliberated. The proposal landed. The "
                "budget contracted. Daria signed off on the timeline. "
            ) * 6,
        },
    ]


@pytest.fixture
def envelope():
    result = cda.audit_chapter_distinctiveness(
        _chapters(),
        function_words=set(),
        anchor_words=set(),
        min_count=2,
        min_word_len=4,
        cluster_window=300,
        min_ratio=1.0,
    )
    return cda.build_audit_payload(
        result, target_path=Path("manuscript.md"),
    )


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "smoothing_diagnosis"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "chapter_distinctiveness_audit"
        assert envelope["version"] == cda.SCRIPT_VERSION


class TestTargetAndBaseline:
    def test_target_words(self, envelope):
        assert envelope["target"]["words"] > 0

    def test_target_carries_n_chapters(self, envelope):
        assert envelope["target"]["n_chapters"] == 3

    def test_baseline_is_null(self, envelope):
        """Internal-baseline (leave-one-out); no external baseline."""
        assert envelope["baseline"] is None


class TestResultsPayload:
    def test_results_carries_chapters(self, envelope):
        r = envelope["results"]
        assert r["n_chapters"] == 3
        assert len(r["chapters"]) == 3
        for ch in r["chapters"]:
            assert "label" in ch
            assert "n_target_words" in ch
            assert "candidates" in ch

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "n_chapters", "total_target_words", "chapters",
        ):
            assert legacy not in envelope


class TestClaimLicense:
    def test_structured(self, envelope):
        cl = envelope["claim_license"]
        assert cl["task_surface"] == "smoothing_diagnosis"
        assert len(cl["licenses"]) > 80

    def test_rendered_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )
