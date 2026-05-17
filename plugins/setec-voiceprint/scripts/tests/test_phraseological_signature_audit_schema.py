#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on phraseological_signature_audit.

Wave 3 of the output-schema unification track. The pre-1.84 audit
dict carried `claim_license: {"rendered": "..."}` (markdown only);
post-migration the envelope ships the full 11-key structured
ClaimLicense via build_output, with the rendered markdown moved
to `claim_license_rendered`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import phraseological_signature_audit as psa  # type: ignore


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


def _target() -> str:
    return (
        "It seems to me that, on the one hand, the prose is fine. "
        "On the other hand, it could improve. The bottom line is "
        "that voice matters. In other words, frame reuse is the "
        "writer's voice. By and large, the work holds together."
    ) * 4


@pytest.fixture
def envelope():
    audit = psa.audit_phraseology(target_text=_target())
    return psa.build_audit_payload(
        audit, target_path=Path("draft.md"), baseline_dir=None,
    )


class TestEnvelopeKeys:
    def test_required_keys(self, envelope):
        assert set(envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, envelope):
        assert envelope["schema_version"] == "1.0"

    def test_task_surface(self, envelope):
        assert envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, envelope):
        assert envelope["tool"] == "phraseological_signature_audit"
        assert envelope["version"] == psa.SCRIPT_VERSION


class TestResultsPayload:
    def test_results_carries_categories(self, envelope):
        assert "categories" in envelope["results"]
        cats = envelope["results"]["categories"]
        assert isinstance(cats, dict)

    def test_no_legacy_top_level_keys(self, envelope):
        for legacy in (
            "categories", "target_words", "baseline_words",
            "n_baseline_files",
        ):
            assert legacy not in envelope


class TestClaimLicense:
    def test_structured_block_11_keys(self, envelope):
        """The pre-migration audit dict carried claim_license as
        `{"rendered": "...markdown..."}`. Post-migration the envelope
        carries the full structured 11-key ClaimLicense.to_dict()
        shape, with the rendered markdown lifted out to
        claim_license_rendered.
        """
        assert set(envelope["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_task_surface_matches(self, envelope):
        assert (
            envelope["claim_license"]["task_surface"]
            == envelope["task_surface"]
        )

    def test_comparison_set_has_word_counts(self, envelope):
        cs = envelope["claim_license"]["comparison_set"]
        assert "target_words" in cs
        assert "baseline_words" in cs
        assert "n_categories_active" in cs

    def test_rendered_block_starts_with_header(self, envelope):
        assert envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestBaseline:
    def test_baseline_block_when_path_supplied(self):
        text = _target()
        baseline_a = "On the one hand, the data. " * 5
        baseline_b = "By and large, the patterns hold. " * 5
        audit = psa.audit_phraseology(
            target_text=text,
            baseline_texts=[baseline_a, baseline_b],
        )
        envelope = psa.build_audit_payload(
            audit,
            target_path=Path("draft.md"),
            baseline_dir=Path("baseline/"),
        )
        assert envelope["baseline"] is not None
        assert envelope["baseline"]["n_files"] == 2
        assert envelope["baseline"]["words"] > 0
        # pathlib normalizes the trailing slash; compare on prefix.
        assert envelope["baseline"]["path"].rstrip("/") == "baseline"

    def test_baseline_null_when_no_baseline(self, envelope):
        assert envelope["baseline"] is None
