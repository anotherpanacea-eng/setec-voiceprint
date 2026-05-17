#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on bigram_diff.

Wave 5 of the output-schema unification track.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bigram_diff as bd  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


def _diff_rows():
    return [
        {"bigram": "NOUN_VERB", "kl_contrib": 0.04, "target_p": 0.10, "cluster_p": 0.06},
        {"bigram": "DET_NOUN", "kl_contrib": -0.02, "target_p": 0.08, "cluster_p": 0.10},
    ]


@pytest.fixture
def envelope():
    json_str = bd.render_json(
        target_path=Path("draft.txt"),
        target_counts={"NOUN_VERB": 50, "DET_NOUN": 40, "ADJ_NOUN": 30},
        cluster_loaded=[Path("a.txt"), Path("b.txt")],
        cluster_skipped=[],
        pooled_diff=_diff_rows(),
        mean_diff=_diff_rows(),
        top=10, alpha=1.0, min_count=2,
    )
    return json.loads(json_str)


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "smoothing_diagnosis"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "bigram_diff"
        assert envelope["version"] == bd.SCRIPT_VERSION


class TestResultsAndBaseline:
    def test_results_carries_diffs(self, envelope):
        r = envelope["results"]
        assert r["target_bigrams"] == 120
        assert r["target_unique"] == 3
        assert "pooled" in r["diffs"]
        assert "mean" in r["diffs"]

    def test_baseline_n_files(self, envelope):
        assert envelope["baseline"]["n_files"] == 2
        assert envelope["baseline"]["files_loaded"] == ["a.txt", "b.txt"]

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "target_bigrams", "target_unique",
            "cluster_files_loaded", "cluster_files_skipped",
            "smoothing_alpha", "min_count", "diffs",
        ):
            assert legacy not in envelope


class TestClaimLicense:
    def test_structured(self, envelope):
        cs = envelope["claim_license"]["comparison_set"]
        assert cs["target_bigrams"] == 120
        assert cs["n_cluster_files"] == 2
        assert cs["smoothing_alpha"] == 1.0


class TestSkippedClusterFiles:
    def test_skipped_files_produce_warning(self):
        json_str = bd.render_json(
            target_path=Path("draft.txt"),
            target_counts={"X_Y": 10},
            cluster_loaded=[Path("a.txt")],
            cluster_skipped=[Path("bad.txt"), Path("locked.txt")],
            pooled_diff=_diff_rows(),
            mean_diff=None,
            top=10, alpha=1.0, min_count=1,
        )
        env = json.loads(json_str)
        assert env["warnings"]
        assert any("skipped" in w.lower() for w in env["warnings"])
