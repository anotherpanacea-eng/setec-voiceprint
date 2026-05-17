#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on construction_signature_audit.

Wave 3 of the output-schema unification track. ``build_audit()`` keeps
its legacy mixed-shape return (internal tests pin its top-level keys);
``build_audit_payload()`` wraps it in the envelope.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import construction_signature_audit as csa  # type: ignore


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


def _sample() -> str:
    return (
        "There is a draft. What matters is the voice. It is "
        "important to revise. Although tired, she continued. "
        "Despite the timeline, the team produced a draft. The "
        "report, somewhat surprisingly, landed on time. To begin "
        "with, the framing helped. From the outset, the budget "
        "constrained the scope."
    ) * 3


def _audit(text=None, baseline_density=None, baseline_loaded=None):
    text = text or _sample()
    results, n_words = csa.detect_constructions(text)
    return csa.build_audit(
        target_path=Path("draft.md"),
        target_text=text,
        target_results=results,
        target_words=n_words,
        baseline_density_per_1k=baseline_density,
        baseline_loaded=baseline_loaded or [],
        baseline_skipped=[],
        baseline_words=0 if baseline_density is None else 5000,
        top=10,
        construction_filter=None,
        include_baseline_filenames=False,
    )


@pytest.fixture
def envelope():
    return csa.build_audit_payload(_audit())


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "construction_signature_audit"
        assert envelope["version"] == csa.SCRIPT_VERSION

    def test_target_path_and_words(self, envelope):
        assert envelope["target"]["path"] == "draft.md"
        assert envelope["target"]["words"] > 0

    def test_target_carries_spacy_available(self, envelope):
        # Script-specific environment metadata rides under target_extra.
        assert "spacy_available" in envelope["target"]


class TestResultsPayload:
    def test_results_carries_constructions(self, envelope):
        assert "constructions" in envelope["results"]
        cons = envelope["results"]["constructions"]
        assert isinstance(cons, dict)
        assert len(cons) > 0

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "target_words", "constructions", "spacy_available",
            "baseline_words", "baseline_files_loaded_count",
            "baseline_files_skipped_count",
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

    def test_rendered_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )

    def test_comparison_set_carries_keys(self, envelope):
        cs = envelope["claim_license"]["comparison_set"]
        for k in (
            "target_words", "n_baseline_files",
            "n_constructions_available", "spacy_available",
        ):
            assert k in cs


class TestBaselinePath:
    def test_baseline_populated_when_supplied(self):
        baseline_density = {"existential_there": 1.0}
        audit = _audit(
            baseline_density=baseline_density,
            baseline_loaded=[Path("base/a.txt")],
        )
        envelope = csa.build_audit_payload(audit)
        assert envelope["baseline"] is not None
        # n_files comes from baseline_files_loaded_count (privacy-
        # default; filename list is suppressed by build_audit's
        # include_baseline_filenames=False path).
        assert envelope["baseline"]["n_files"] == 1
        assert envelope["baseline"]["words"] == 5000
        # File list is absent when privacy-default is in effect.
        assert "files_loaded" not in envelope["baseline"]


class TestBuildAuditUnchanged:
    def test_build_audit_still_returns_legacy_shape(self):
        """build_audit's legacy top-level keys stay because internal
        tests (test_build_audit_includes_required_fields) pin them.
        """
        audit = _audit()
        for k in (
            "task_surface", "tool", "version", "target",
            "target_words", "spacy_available", "constructions",
            "claim_license",
        ):
            assert k in audit
        assert isinstance(audit["claim_license"], dict)
        assert "rendered" in audit["claim_license"]
