#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on controls_audit.

Wave 3 of the output-schema unification track.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import controls_audit as ca  # type: ignore


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})


_BASELINE = [
    "The committee deliberated through the afternoon. The room was warm.",
    "Maria signed the agreement. Daria reviewed it after lunch.",
    "There was a meeting. The team produced a draft.",
    "Although tired, the working group continued.",
    "By the end of the day, three workstreams advanced.",
]


@pytest.fixture
def envelope():
    questioned = (
        "The team has produced a draft. There are concerns about scope."
    )
    negative = (
        "Daria and Maria reviewed the deliverable. The dashboard "
        "shows progress."
    )
    positive = (
        "We must consider whether the strategic alignment can be "
        "achieved within the parameters established by the framework."
    )
    report = ca.run_controls_audit(
        questioned_text=questioned,
        baseline_texts=_BASELINE,
        negative_control_text=negative,
        positive_control_text=positive,
    )
    return ca.build_audit_payload(
        report, target_path=Path("questioned.md"),
    )


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "controls_audit"
        assert envelope["version"] == ca.SCRIPT_VERSION


class TestResultsPayload:
    def test_results_carries_questioned_and_controls(self, envelope):
        r = envelope["results"]
        for k in (
            "questioned", "negative_control",
            "positive_control", "classification",
        ):
            assert k in r, f"missing results key: {k}"

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "questioned", "negative_control",
            "positive_control", "classification", "n_baseline_files",
        ):
            assert legacy not in envelope


class TestClaimLicense:
    def test_structured_block(self, envelope):
        cl = envelope["claim_license"]
        assert cl["task_surface"] == "voice_coherence"
        cs = cl["comparison_set"]
        assert cs["negative_control_supplied"] is True
        assert cs["positive_control_supplied"] is True
        assert cs["n_baseline_files"] > 0

    def test_rendered_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestUnavailable:
    def test_empty_baseline(self):
        report = ca.run_controls_audit(
            questioned_text="Some questioned text.",
            baseline_texts=[],
            negative_control_text=None,
            positive_control_text=None,
        )
        envelope = ca.build_audit_payload(
            report, target_path=Path("q.md"),
        )
        assert envelope["available"] is False
        assert envelope["claim_license"] is None
        assert envelope["warnings"]


class TestBaseline:
    def test_baseline_block_n_files_populated(self, envelope):
        assert envelope["baseline"] is not None
        assert envelope["baseline"]["n_files"] == len(_BASELINE)
        # words is 0 because run_controls_audit does not surface
        # baseline word counts in its return shape.
        assert envelope["baseline"]["words"] == 0
