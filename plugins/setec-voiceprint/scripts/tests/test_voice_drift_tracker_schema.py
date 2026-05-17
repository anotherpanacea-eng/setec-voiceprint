#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on voice_drift_tracker.

Wave 4 of the output-schema unification track. voice_drift_tracker
previously emitted `claim_license: {"licenses": ..., "does_not_license":
...}` — the legacy 2-key dict (different from phraseological /
construction's `{"rendered": ...}` form). Migration replaces with
the structured 11-key shape.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import voice_drift_tracker as vdt  # type: ignore


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


def _fake_render_inputs():
    """Synthetic render_json inputs that exercise build_output's
    plumbing without needing a real drift run."""
    from voice_drift_tracker import PeriodProfile

    profiles = {
        "1787": PeriodProfile(
            label="1787",
            n_docs=3,
            n_words=12000,
            feature_items=[],
            period_centroids={"function_words": {"the": 0.06}},
        ),
        "1788": PeriodProfile(
            label="1788",
            n_docs=4,
            n_words=16000,
            feature_items=[],
            period_centroids={"function_words": {"the": 0.058}},
        ),
    }
    family_distances = {
        "function_words": {
            ("1787", "1788"): {
                "burrows_delta": 0.6,
                "cosine_distance": 0.04,
            },
        },
    }
    weighted_distances = {
        ("1787", "1788"): {
            "burrows_delta": 0.6,
            "cosine_distance": 0.04,
        },
    }
    drift = {
        "function_words": {
            "drifting_features": [
                {"feature": "the", "cv": 0.15},
            ],
            "stable_features": [],
        },
    }
    return {
        "profiles": profiles,
        "family_distances": family_distances,
        "weighted_distances": weighted_distances,
        "drift": drift,
        "dropped_periods": [],
        "inputs": {
            "manifest": "manifest.jsonl",
            "min_docs_per_period": 1,
        },
        "granularity": "year",
    }


@pytest.fixture
def envelope():
    json_str = vdt.render_json(**_fake_render_inputs())
    return json.loads(json_str)


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "voice_drift_tracker"
        assert envelope["version"] == vdt.SCRIPT_VERSION


class TestTargetAndBaseline:
    def test_target_words_sums_period_words(self, envelope):
        # 12000 + 16000
        assert envelope["target"]["words"] == 28000

    def test_target_carries_granularity(self, envelope):
        assert envelope["target"]["granularity"] == "year"

    def test_baseline_is_null(self, envelope):
        """voice_drift_tracker analyzes a date-tagged corpus; the
        corpus IS the target. There's no separate comparison set.
        """
        assert envelope["baseline"] is None


class TestResultsPayload:
    def test_results_carries_drift_data(self, envelope):
        r = envelope["results"]
        for k in (
            "n_periods", "periods", "granularity", "inputs",
            "dropped_periods", "cross_period_distances_per_family",
            "cross_period_distances_weighted", "drift_scores",
        ):
            assert k in r, f"missing results key: {k}"

    def test_n_periods_value(self, envelope):
        assert envelope["results"]["n_periods"] == 2

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "n_periods", "periods", "granularity", "inputs",
            "dropped_periods",
            "cross_period_distances_per_family",
            "cross_period_distances_weighted", "drift_scores",
        ):
            assert legacy not in envelope


class TestClaimLicense:
    def test_structured_block_11_keys(self, envelope):
        """Pre-1.85 emitted claim_license as a 2-key dict
        (licenses, does_not_license). Post-1.85 emits the full
        structured ClaimLicense.to_dict() shape.
        """
        assert set(envelope["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_legacy_licenses_text_preserved(self, envelope):
        assert (
            envelope["claim_license"]["licenses"]
            == vdt.CLAIM_LICENSE["licenses"]
        )
        assert (
            envelope["claim_license"]["does_not_license"]
            == vdt.CLAIM_LICENSE["does_not_license"]
        )

    def test_comparison_set_carries_periods(self, envelope):
        cs = envelope["claim_license"]["comparison_set"]
        assert cs["granularity"] == "year"
        assert cs["n_periods"] == 2
        assert "1787" in cs["period_labels"]
        assert "1788" in cs["period_labels"]
        assert cs["n_docs_per_period"]["1787"] == 3

    def test_rendered_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestMarkdownPathStillWorks:
    def test_render_markdown_consumes_legacy_claim_license(self):
        """render_markdown reads CLAIM_LICENSE["licenses"] directly.
        The migration keeps CLAIM_LICENSE alive as a constant for
        this path; the markdown render must not break.
        """
        md = vdt.render_markdown(**{
            k: v for k, v in _fake_render_inputs().items()
            if k not in {"inputs"}  # render_markdown takes a different signature
        })
        assert "Voice Drift Report" in md
        # Some chunk of the legacy CLAIM_LICENSE["licenses"] string
        # appears in the markdown.
        assert "voiceprint summary" in md
