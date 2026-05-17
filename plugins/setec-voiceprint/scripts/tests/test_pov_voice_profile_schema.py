#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on pov_voice_profile.

Wave 4 of the output-schema unification track. Resolves the third
of three pre-migration claim_license shape incompatibilities: pre-
1.85 emitted claim_license as the legacy 2-key dict. Migration
replaces with the structured 11-key form via build_output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pov_voice_profile as pvp  # type: ignore


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
    from pov_voice_profile import POVProfile

    profiles = {
        "Hamilton": POVProfile(
            label="Hamilton", n_docs=5, n_words=18000,
            feature_items=[],
            pov_centroids={"function_words": {"the": 0.058}},
        ),
        "Madison": POVProfile(
            label="Madison", n_docs=4, n_words=15000,
            feature_items=[],
            pov_centroids={"function_words": {"the": 0.062}},
        ),
    }
    family_distances = {
        "function_words": {
            ("Hamilton", "Madison"): {
                "burrows_delta": 1.4,
                "cosine_distance": 0.08,
            },
        },
    }
    weighted_distances = {
        ("Hamilton", "Madison"): {
            "burrows_delta": 1.4,
            "cosine_distance": 0.08,
        },
    }
    pov_vs_mean = {
        "Hamilton": {"burrows_delta": 0.8, "cosine_distance": 0.05},
        "Madison": {"burrows_delta": 0.7, "cosine_distance": 0.04},
    }
    distinguishing = {
        "Hamilton": {"function_words": [{"feature": "establish", "z": 1.2}]},
        "Madison": {"function_words": [{"feature": "republic", "z": 1.4}]},
    }
    collapse_verdict = [
        {
            "pov_a": "Hamilton", "pov_b": "Madison",
            "verdict": "distinct", "burrows_delta": 1.4,
        },
    ]
    return {
        "profiles": profiles,
        "family_distances": family_distances,
        "weighted_distances": weighted_distances,
        "pov_vs_mean": pov_vs_mean,
        "distinguishing": distinguishing,
        "collapse_verdict": collapse_verdict,
        "dropped_povs": [],
        "inputs": {
            "manifest": "manifest.jsonl",
            "min_words_per_pov": 5000,
        },
    }


@pytest.fixture
def envelope():
    return json.loads(pvp.render_json(**_fake_render_inputs()))


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "pov_voice_profile"
        assert envelope["version"] == pvp.SCRIPT_VERSION


class TestTargetAndBaseline:
    def test_target_words_sums_pov_words(self, envelope):
        # 18000 + 15000
        assert envelope["target"]["words"] == 33000

    def test_baseline_is_null(self, envelope):
        """The corpus IS the target; there's no separate baseline."""
        assert envelope["baseline"] is None


class TestResultsPayload:
    def test_results_carries_pov_data(self, envelope):
        r = envelope["results"]
        for k in (
            "n_povs", "povs", "inputs", "dropped_povs",
            "cross_pov_distances_per_family",
            "cross_pov_distances_weighted",
            "pov_vs_corpus_mean", "distinguishing_features",
            "voice_collapse_verdict",
        ):
            assert k in r, f"missing results key: {k}"

    def test_n_povs(self, envelope):
        assert envelope["results"]["n_povs"] == 2

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "n_povs", "povs", "inputs", "dropped_povs",
            "cross_pov_distances_per_family",
            "cross_pov_distances_weighted",
            "pov_vs_corpus_mean", "distinguishing_features",
            "voice_collapse_verdict",
        ):
            assert legacy not in envelope


class TestClaimLicense:
    def test_structured_block_11_keys(self, envelope):
        """Pre-1.85 emitted claim_license as the legacy 2-key dict.
        Post-1.85 emits the full 11-key ClaimLicense.to_dict().
        """
        assert set(envelope["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_legacy_text_preserved(self, envelope):
        assert (
            envelope["claim_license"]["licenses"]
            == pvp.CLAIM_LICENSE["licenses"]
        )
        assert (
            envelope["claim_license"]["does_not_license"]
            == pvp.CLAIM_LICENSE["does_not_license"]
        )

    def test_comparison_set_carries_povs(self, envelope):
        cs = envelope["claim_license"]["comparison_set"]
        assert cs["n_povs"] == 2
        assert "Hamilton" in cs["pov_labels"]
        assert cs["n_docs_per_pov"]["Hamilton"] == 5
        assert cs["n_collapse_flags"] == 0

    def test_rendered_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestCollapseSurfacing:
    def test_collapse_flag_counted(self):
        inputs = _fake_render_inputs()
        inputs["collapse_verdict"] = [
            {
                "pov_a": "A", "pov_b": "B",
                "verdict": "potentially_collapsed", "burrows_delta": 0.3,
            },
        ]
        envelope = json.loads(pvp.render_json(**inputs))
        cs = envelope["claim_license"]["comparison_set"]
        assert cs["n_collapse_flags"] == 1


# Markdown rendering uses a richer distinguishing-features dict
# shape than the synthetic _fake_render_inputs() above; the
# pre-existing tests in test_pov_voice_profile.py
# (test_markdown_output_includes_distance_table) cover that path
# end-to-end via a real Federalist-corpus run.
