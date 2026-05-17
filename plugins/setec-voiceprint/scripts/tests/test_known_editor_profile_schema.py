#!/usr/bin/env python3
"""Pins schema_version 1.0 envelope on known_editor_profile (match
mode). Wave 7. The "learn" mode emits a profile JSON intentionally
bypassing the envelope (the profile is consumed by --profile on
later runs)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import known_editor_profile as kep  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


def _fake_match_report(verdict="matches_profile"):
    return {
        "task_surface": "validation",
        "tool": "known_editor_profile",
        "version": "1.0",
        "profile_label": "test_editor",
        "profile_n_pairs": 5,
        "z_threshold": 2.0,
        "per_signal": {
            "burstiness_B": {"delta": -0.05, "z": -0.8, "inside_band": True},
        },
        "n_signals_inside": 6,
        "n_signals_outside": 0,
        "n_signals_ambiguous": 1,
        "verdict": verdict,
        "claim_license": {"rendered": "..."},
    }


@pytest.fixture
def envelope():
    return kep.build_audit_payload(
        _fake_match_report(),
        before_path=Path("before.md"),
        after_path=Path("after.md"),
    )


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "validation"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "known_editor_profile"
        assert envelope["version"] == kep.SCRIPT_VERSION


class TestResultsAndTarget:
    def test_results_carries_match_report(self, envelope):
        r = envelope["results"]
        assert r["verdict"] == "matches_profile"
        assert r["profile_label"] == "test_editor"
        assert "per_signal" in r

    def test_target_extra_carries_after_path(self, envelope):
        assert "after_path" in envelope["target"]

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "profile_label", "profile_n_pairs", "z_threshold",
            "per_signal", "verdict",
        ):
            assert legacy not in envelope


class TestClaimLicense:
    def test_structured(self, envelope):
        cs = envelope["claim_license"]["comparison_set"]
        assert cs["verdict"] == "matches_profile"
        assert cs["profile_n_pairs"] == 5
