#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on repetition_audit.

Wave 5 of the output-schema unification track.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import repetition_audit as ra  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


def _candidates():
    return [
        {"word": "forge", "count": 12, "per_1000": 4.0, "baseline_per_1000": 0.5, "ratio": 8.0, "cluster_max": 4},
        {"word": "hammer", "count": 8, "per_1000": 2.7, "baseline_per_1000": 0.3, "ratio": 9.0, "cluster_max": 3},
    ]


@pytest.fixture
def envelope():
    return ra.build_audit_payload(
        target_path=Path("draft.txt"),
        target_words=3000,
        candidates=_candidates(),
        baseline_files_loaded=[Path("a.md"), Path("b.md")],
        baseline_files_skipped=[],
        baseline_tokens=15000,
    )


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "smoothing_diagnosis"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "repetition_audit"
        assert envelope["version"] == ra.SCRIPT_VERSION


class TestTargetAndBaseline:
    def test_target_words(self, envelope):
        assert envelope["target"]["words"] == 3000

    def test_baseline_n_files_words(self, envelope):
        assert envelope["baseline"]["n_files"] == 2
        assert envelope["baseline"]["words"] == 15000

    def test_baseline_files_loaded(self, envelope):
        assert envelope["baseline"]["files_loaded"] == ["a.md", "b.md"]


class TestResultsPayload:
    def test_results_carries_candidates(self, envelope):
        assert envelope["results"]["candidates"] == _candidates()

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "target", "target_words", "candidates",
            "baseline_files_loaded", "baseline_files_skipped",
            "baseline_tokens",
        ):
            # `target` is now an envelope key (dict), but as a string
            # key with the legacy file-path value it must not appear.
            if legacy == "target":
                assert not isinstance(envelope["target"], str)
                continue
            assert legacy not in envelope


class TestClaimLicense:
    def test_structured(self, envelope):
        assert envelope["claim_license"]["task_surface"] == "smoothing_diagnosis"
        assert len(envelope["claim_license"]["licenses"]) > 80

    def test_rendered_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )

    def test_comparison_set_carries_word_counts(self, envelope):
        cs = envelope["claim_license"]["comparison_set"]
        assert cs["target_words"] == 3000
        assert cs["baseline_tokens"] == 15000
        assert cs["n_candidates"] == 2


class TestSkippedFilesWarning:
    def test_skipped_files_produce_warning(self):
        env = ra.build_audit_payload(
            target_path=Path("draft.txt"),
            target_words=3000,
            candidates=_candidates(),
            baseline_files_loaded=[Path("a.md")],
            baseline_files_skipped=[Path("bad.pdf"), Path("locked.md")],
            baseline_tokens=12000,
        )
        assert env["warnings"]
        assert any("skipped" in w.lower() for w in env["warnings"])
        # Baseline must record both loaded and skipped lists.
        assert env["baseline"]["files_skipped"] == ["bad.pdf", "locked.md"]
