#!/usr/bin/env python3
"""Pins schema_version 1.0 envelope on manuscript_audit. Wave 5."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import manuscript_audit as ma  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


def _fake_result(with_baseline=False):
    return {
        "task_surface": "smoothing_diagnosis",
        "preprocessing": {
            "chapters": {"opt_out": False, "tokens_stripped": 0},
            "baseline": {"opt_out": False, "tokens_stripped": 50} if with_baseline else None,
        },
        "n_chapters": 3,
        "n_baseline_files": 5 if with_baseline else 0,
        "chapters": [
            {"label": "Chapter 1", "n_words": 4000, "compression": {"band": "Lightly smoothed"}},
            {"label": "Chapter 2", "n_words": 4500, "compression": {"band": "Moderately smoothed"}},
            {"label": "Chapter 3", "n_words": 3800, "compression": {"band": "Lightly smoothed"}},
        ],
        "baseline_stats": (
            {"signal_summary": {"burstiness_B": {"mean": -0.1, "sd": 0.05, "n": 5}}}
            if with_baseline else None
        ),
    }


@pytest.fixture
def envelope():
    return ma.build_audit_payload(
        _fake_result(), target_path=Path("manuscript.md"),
    )


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "manuscript_audit"
        assert envelope["version"] == ma.SCRIPT_VERSION


class TestTargetAndBaseline:
    def test_target_words_sums_chapters(self, envelope):
        assert envelope["target"]["words"] == 12300

    def test_target_carries_n_chapters(self, envelope):
        assert envelope["target"]["n_chapters"] == 3

    def test_baseline_null_when_no_baseline(self, envelope):
        assert envelope["baseline"] is None

    def test_baseline_populated_when_supplied(self):
        env = ma.build_audit_payload(
            _fake_result(with_baseline=True),
            target_path=Path("m.md"),
        )
        assert env["baseline"] is not None
        assert env["baseline"]["n_files"] == 5


class TestResultsPayload:
    def test_results_carries_chapters_and_baseline_stats(self, envelope):
        r = envelope["results"]
        assert "chapters" in r
        assert len(r["chapters"]) == 3

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "n_chapters", "n_baseline_files",
            "chapters", "baseline_stats", "preprocessing",
        ):
            assert legacy not in envelope


class TestClaimLicense:
    def test_structured(self, envelope):
        cl = envelope["claim_license"]
        cs = cl["comparison_set"]
        assert cs["n_chapters"] == 3
        assert len(cl["licenses"]) > 80
