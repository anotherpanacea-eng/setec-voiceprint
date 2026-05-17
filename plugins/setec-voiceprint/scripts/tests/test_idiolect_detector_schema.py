#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on idiolect_detector.

Wave 4 of the output-schema unification track. idiolect_detector
compares a target corpus against a reference corpus; the reference
corpus serves as the envelope's baseline.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import idiolect_detector as idd  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


def _fake_result() -> dict:
    return {
        "task_surface": "voice_coherence",
        "privacy": idd.PRIVACY_WARNING,
        "target_summary": {
            "label": "target",
            "n_files": 5,
            "n_tokens": 12000,
            "files": [
                {"id": "t0", "path": "t0.md", "metadata": {}},
                {"id": "t1", "path": "t1.md", "metadata": {}},
            ],
        },
        "reference_summary": {
            "label": "reference",
            "n_files": 12,
            "n_tokens": 60000,
            "files": [],
        },
        "method": {
            "keyness": "log_likelihood",
            "n_values": [1, 2, 3],
            "smoothing_alpha": 0.5,
            "min_target_count": 3,
            "min_reference_count": 0,
            "min_total_count": 5,
        },
        "preprocessing": {
            "target": {"tokens_stripped": 50},
            "reference": {"tokens_stripped": 200},
        },
        "rankings": {
            "1": [
                {
                    "display": "afternoon",
                    "target_count": 12,
                    "reference_count": 3,
                    "target_per_1000": 1.0,
                    "reference_per_1000": 0.05,
                    "score": 8.5,
                },
            ],
        },
        "preservation_list": [
            {"display": "afternoon", "score": 8.5, "n": 1},
            {"display": "Daria signed", "score": 7.1, "n": 2},
        ],
    }


@pytest.fixture
def envelope():
    return idd.build_audit_payload(
        _fake_result(),
        target_path=Path("target_dir/"),
        reference_path=Path("reference_dir/"),
    )


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "idiolect_detector"
        assert envelope["version"] == idd.SCRIPT_VERSION


class TestTargetAndBaseline:
    def test_target_words_from_n_tokens(self, envelope):
        assert envelope["target"]["words"] == 12000

    def test_target_carries_privacy(self, envelope):
        assert envelope["target"]["privacy"] == idd.PRIVACY_WARNING

    def test_target_carries_n_files(self, envelope):
        assert envelope["target"]["n_files"] == 5

    def test_target_carries_preprocessing(self, envelope):
        assert envelope["target"]["preprocessing"]["tokens_stripped"] == 50

    def test_baseline_n_files_words(self, envelope):
        assert envelope["baseline"]["n_files"] == 12
        assert envelope["baseline"]["words"] == 60000

    def test_baseline_carries_reference_preprocessing(self, envelope):
        assert envelope["baseline"]["preprocessing"]["tokens_stripped"] == 200

    def test_baseline_carries_path(self, envelope):
        assert envelope["baseline"]["path"].rstrip("/") == "reference_dir"


class TestResultsPayload:
    def test_results_carries_method_rankings_preservation(self, envelope):
        r = envelope["results"]
        assert "method" in r
        assert "rankings" in r
        assert "preservation_list" in r

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "target_summary", "reference_summary",
            "method", "rankings", "preservation_list",
            "privacy", "preprocessing",
        ):
            assert legacy not in envelope


class TestClaimLicense:
    def test_task_surface_matches(self, envelope):
        assert (
            envelope["claim_license"]["task_surface"]
            == envelope["task_surface"]
        )

    def test_does_not_license_flags_voice_cloning(self, envelope):
        # Preservation list is voice-cloning-grade input. The license
        # MUST flag this; pin to guard against accidental softening.
        text = envelope["claim_license"]["does_not_license"].lower()
        assert "voice-cloning" in text or "private" in text

    def test_comparison_set_carries_corpus_summary(self, envelope):
        cs = envelope["claim_license"]["comparison_set"]
        assert cs["target_n_tokens"] == 12000
        assert cs["reference_n_tokens"] == 60000
        assert cs["n_preservation_entries"] == 2

    def test_rendered_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )
