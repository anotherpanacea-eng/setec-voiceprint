#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on manuscript_repetition_audit.

Wave 5 of the output-schema unification track.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import manuscript_repetition_audit as mra  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


def _fake_result(skipped=False):
    return {
        "task_surface": "smoothing_diagnosis",
        "n_chapters": 4,
        "n_baseline_files": 8,
        "n_baseline_files_skipped": 1 if skipped else 0,
        "baseline_files_loaded": [Path(f"baseline/file_{i}.md") for i in range(8)],
        "baseline_files_skipped": [Path("baseline/locked.md")] if skipped else [],
        "baseline_words": 40000,
        "total_target_words": 22000,
        "chapters": [
            {"label": "Chapter 1", "n_target_words": 5500, "candidates": []},
            {"label": "Chapter 2", "n_target_words": 5500, "candidates": []},
            {"label": "Chapter 3", "n_target_words": 5500, "candidates": []},
            {"label": "Chapter 4", "n_target_words": 5500, "candidates": []},
        ],
        "aggregated": [
            {"word": "forge", "n_chapters": 3, "median_ratio": 4.5},
        ],
    }


@pytest.fixture
def envelope():
    return mra.build_audit_payload(
        _fake_result(), target_path=Path("manuscript.md"),
    )


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "smoothing_diagnosis"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "manuscript_repetition_audit"
        assert envelope["version"] == mra.SCRIPT_VERSION


class TestTargetAndBaseline:
    def test_target_words_sums_chapter_words(self, envelope):
        assert envelope["target"]["words"] == 22000

    def test_target_carries_n_chapters(self, envelope):
        assert envelope["target"]["n_chapters"] == 4

    def test_baseline_block_populated(self, envelope):
        assert envelope["baseline"]["n_files"] == 8
        assert envelope["baseline"]["words"] == 40000


class TestResultsPayload:
    def test_results_carries_chapters_and_aggregated(self, envelope):
        r = envelope["results"]
        assert r["n_chapters"] == 4
        assert len(r["chapters"]) == 4
        assert len(r["aggregated"]) == 1

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "n_chapters", "n_baseline_files", "baseline_words",
            "total_target_words", "chapters", "aggregated",
            "baseline_files_loaded", "baseline_files_skipped",
        ):
            assert legacy not in envelope


class TestClaimLicense:
    def test_structured(self, envelope):
        cs = envelope["claim_license"]["comparison_set"]
        assert cs["n_chapters"] == 4
        assert cs["n_baseline_files"] == 8


class TestSkippedFilesWarning:
    def test_skipped_files_produce_warning(self):
        env = mra.build_audit_payload(
            _fake_result(skipped=True), target_path=Path("m.md"),
        )
        assert env["warnings"]
        assert any("skipped" in w.lower() for w in env["warnings"])
