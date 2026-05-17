#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on voice_profile.

Wave 4 of the output-schema unification track. voice_profile profiles
a baseline corpus; the profiled corpus IS the envelope's target,
``envelope.baseline`` is therefore None.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import voice_profile as vp  # type: ignore


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


def _fake_profile() -> dict:
    """Construct a minimal profile dict mirroring build_profile's
    return shape. Avoids the spaCy + corpus load the real script
    needs while exercising build_audit_payload's plumbing.
    """
    return {
        "task_surface": "voice_coherence",
        "privacy": "private",
        "baseline_summary": {
            "n_files": 12,
            "total_words": 25000,
            "mean_words": 2083,
            "min_words": 400,
            "max_words": 6000,
        },
        "preprocessing": {
            "opt_out": False,
            "tokens_stripped": 120,
            "strip_ratio": 0.005,
            "dominant_rule": "html_strip",
        },
        "selected_features": {
            "function_words": 100,
            "char_ngrams_3": 200,
            "char_ngrams_4": 200,
            "pos_trigrams": 300,
        },
        "families": {
            "function_words": {
                "top_features": [
                    {"feature": "the", "mean": 0.06, "sd": 0.01, "cv": 0.17},
                ],
                "most_stable_features": [
                    {"feature": "of", "mean": 0.03, "sd": 0.004, "cv": 0.13},
                ],
            },
        },
        "warnings": [],
    }


@pytest.fixture
def envelope():
    return vp.build_audit_payload(
        _fake_profile(),
        target_path=Path("baselines/personal/"),
    )


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "voice_profile"
        assert envelope["version"] == vp.SCRIPT_VERSION


class TestTargetAndBaseline:
    def test_target_words_from_total_words(self, envelope):
        assert envelope["target"]["words"] == 25000

    def test_target_carries_privacy(self, envelope):
        assert envelope["target"]["privacy"] == "private"

    def test_target_carries_n_files(self, envelope):
        assert envelope["target"]["n_files"] == 12

    def test_target_carries_preprocessing(self, envelope):
        assert "preprocessing" in envelope["target"]
        assert envelope["target"]["preprocessing"]["dominant_rule"] == "html_strip"

    def test_baseline_is_null(self, envelope):
        """voice_profile profiles a corpus; the corpus IS the target.
        ``baseline`` (= comparison set) is None by design — there is
        nothing to compare against.
        """
        assert envelope["baseline"] is None


class TestResultsPayload:
    def test_results_carries_profile_data(self, envelope):
        r = envelope["results"]
        assert "baseline_summary" in r
        assert "selected_features" in r
        assert "families" in r

    def test_no_legacy_top_level_keys(self, envelope):
        # `warnings` is intentionally a top-level envelope key
        # (SPEC §1.1); it does NOT belong in the legacy list.
        for legacy in (
            "baseline_summary", "selected_features", "families",
            "preprocessing", "privacy",
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

    def test_does_not_license_names_privacy_constraint(self, envelope):
        # voice_profile outputs are voice-cloning-grade. The license
        # MUST flag this; the test guards against accidental softening.
        text = envelope["claim_license"]["does_not_license"].lower()
        assert "voice-cloning" in text or "private" in text

    def test_rendered_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestWarningsForwarded:
    def test_warnings_propagate(self):
        profile = _fake_profile()
        profile["warnings"] = ["Baseline corpus is small."]
        envelope = vp.build_audit_payload(
            profile, target_path=Path("baselines/personal/"),
        )
        assert envelope["warnings"] == ["Baseline corpus is small."]
