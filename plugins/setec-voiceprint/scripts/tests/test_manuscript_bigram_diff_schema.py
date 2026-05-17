#!/usr/bin/env python3
"""Pins schema_version 1.0 envelope on manuscript_bigram_diff.

Wave 5 of the output-schema unification track. The script compares
two corpora (A vs B); by convention corpus A flows into envelope.
target and corpus B into envelope.baseline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import manuscript_bigram_diff as mbd  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


def _diff_rows():
    return [
        {"bigram": "NOUN_VERB", "kl_contrib": 0.05, "a_p": 0.10, "b_p": 0.06},
        {"bigram": "DET_NOUN", "kl_contrib": -0.03, "a_p": 0.08, "b_p": 0.11},
    ]


@pytest.fixture
def envelope():
    json_str = mbd.render_json(
        a_label="hamilton",
        b_label="madison",
        a_loaded=[Path("a/file1.txt"), Path("a/file2.txt")],
        b_loaded=[Path("b/file1.txt"), Path("b/file2.txt"), Path("b/file3.txt")],
        a_skipped=[],
        b_skipped=[],
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

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "manuscript_bigram_diff"
        assert envelope["version"] == mbd.SCRIPT_VERSION


class TestCorpusMapping:
    def test_corpus_a_to_target(self, envelope):
        assert envelope["target"]["label"] == "hamilton"
        assert envelope["target"]["n_files"] == 2

    def test_corpus_b_to_baseline(self, envelope):
        assert envelope["baseline"]["label"] == "madison"
        assert envelope["baseline"]["n_files"] == 3


class TestResultsPayload:
    def test_results_carries_diffs_and_labels(self, envelope):
        r = envelope["results"]
        assert r["label_a"] == "hamilton"
        assert r["label_b"] == "madison"
        assert "pooled" in r["diffs"]
        assert "mean" in r["diffs"]

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "label_a", "label_b",
            "corpus_a_files_loaded", "corpus_b_files_loaded",
            "corpus_a_files_skipped", "corpus_b_files_skipped",
            "smoothing_alpha", "min_count", "diffs",
        ):
            assert legacy not in envelope


class TestClaimLicense:
    def test_structured(self, envelope):
        cs = envelope["claim_license"]["comparison_set"]
        assert cs["label_a"] == "hamilton"
        assert cs["label_b"] == "madison"
        assert cs["n_corpus_a_files"] == 2
        assert cs["n_corpus_b_files"] == 3


class TestSkippedFiles:
    def test_corpus_a_skipped_emits_warning(self):
        json_str = mbd.render_json(
            a_label="A", b_label="B",
            a_loaded=[Path("a/x.txt")],
            b_loaded=[Path("b/y.txt")],
            a_skipped=[Path("a/bad.txt")],
            b_skipped=[],
            pooled_diff=_diff_rows(),
            mean_diff=None,
            top=10, alpha=1.0, min_count=1,
        )
        env = json.loads(json_str)
        assert any("corpus-A" in w for w in env["warnings"])
