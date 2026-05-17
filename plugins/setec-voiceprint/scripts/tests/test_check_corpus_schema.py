#!/usr/bin/env python3
"""Pins schema_version 1.0 envelope on check_corpus. Wave 6."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import check_corpus as cc  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


def _fake_result(status="clean"):
    return {
        "task_surface": "validation",
        "status": status,
        "thresholds": {"warn_threshold": 0.05, "fail_threshold": 0.20},
        "n_files": 12,
        "n_clean": 11 if status == "clean" else 9,
        "n_warning": 0 if status == "clean" else 2,
        "n_fail": 0 if status != "fail" else 1,
        "n_error": 0,
        "input_tokens_before": 50000,
        "input_tokens_after": 49000,
        "tokens_stripped": 1000,
        "strip_ratio": 0.02,
        "tokens_stripped_by_rule": {"html": 600, "code_block": 400},
        "dominant_rule": "html",
    }


@pytest.fixture
def envelope():
    return cc.build_audit_payload(
        _fake_result(), target_path=Path("manifest.jsonl"),
    )


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "validation"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "check_corpus"
        assert envelope["version"] == cc.SCRIPT_VERSION


class TestResultsAndTarget:
    def test_target_words_from_tokens_before(self, envelope):
        assert envelope["target"]["words"] == 50000

    def test_results_carries_status_and_counts(self, envelope):
        r = envelope["results"]
        assert r["status"] == "clean"
        assert r["n_files"] == 12
        assert r["thresholds"]["warn_threshold"] == 0.05
        assert r["dominant_rule"] == "html"

    def test_baseline_is_null(self, envelope):
        assert envelope["baseline"] is None


class TestClaimLicense:
    def test_structured(self, envelope):
        cs = envelope["claim_license"]["comparison_set"]
        assert cs["n_files"] == 12
        assert cs["status"] == "clean"


class TestWarningOnNonCleanStatus:
    def test_warning_status_emits_envelope_warning(self):
        env = cc.build_audit_payload(
            _fake_result(status="warning"), target_path=Path("m.jsonl"),
        )
        assert env["warnings"]
        assert "warning" in env["warnings"][0].lower()
