#!/usr/bin/env python3
"""Pins schema_version 1.0 envelope on manifest_validator. Wave 6."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import manifest_validator as mv  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


def _fake_result(n_errors=0):
    return {
        "task_surface": "validation",
        "manifest_path": "manifest.jsonl",
        "n_entries": 25,
        "n_errors": n_errors,
        "n_warnings": 1,
        "issues": [
            {"severity": "warning", "lineno": 7, "id": "doc_07", "field": "privacy", "message": "..."},
        ],
        "summary": {"by_use": {"baseline": 18, "validation": 7}},
    }


@pytest.fixture
def envelope():
    return mv.build_audit_payload(
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
        assert envelope["tool"] == "manifest_validator"
        assert envelope["version"] == mv.SCRIPT_VERSION


class TestResultsPayload:
    def test_results_carries_validation_data(self, envelope):
        r = envelope["results"]
        assert r["n_entries"] == 25
        assert r["n_warnings"] == 1
        assert "issues" in r
        assert "summary" in r

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "manifest_path", "n_entries", "n_errors", "n_warnings",
            "issues", "summary",
        ):
            assert legacy not in envelope


class TestErrorWarning:
    def test_errors_emit_envelope_warning(self):
        env = mv.build_audit_payload(
            _fake_result(n_errors=3), target_path=Path("m.jsonl"),
        )
        assert any("error" in w.lower() for w in env["warnings"])


class TestClaimLicense:
    def test_structured(self, envelope):
        cs = envelope["claim_license"]["comparison_set"]
        assert cs["n_entries"] == 25
        assert cs["n_errors"] == 0
