#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope on aic_pattern_audit.

aic_pattern_audit is the first proof migration for the output-schema
unification wave (see ``internal/SPEC_output_schema_unification.md``).
These tests guard the envelope shape so subsequent waves can reuse
the pattern with confidence.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import aic_pattern_audit as aic  # type: ignore


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


def _sample_text() -> str:
    """A passage with enough sentences to give the pattern detectors
    something to chew on. Includes a correctio, a triplet, and at
    least three anaphoric heads in a row to fire manifesto cadence.
    """
    return (
        "The committee was not, in the end, persuaded; rather, "
        "it was convinced. What matters is not the vote, but the "
        "deliberation that produced it. The room held three "
        "speakers, four observers, and one moderator. We must "
        "remember the constraint. We must remember the timeline. "
        "We must remember the room. It is not the budget, but the "
        "scope, that breaks the proposal. There is a kind of "
        "patience that policy work requires. Reasonable people "
        "may disagree about the path. The framing is, "
        "however, in our control."
    ) * 6


@pytest.fixture
def audit_payload():
    text = _sample_text()
    sentences = aic.split_sentences(text)
    target_words = len(
        [w for w in text.split() if any(c.isalpha() for c in w)]
    )
    results = aic.all_patterns(text, sentences)
    return aic.build_audit_payload(
        target_path=Path("draft.md"),
        target_words=target_words,
        target_results=results,
        baseline_density_per_1k=None,
        baseline_loaded=[],
        baseline_skipped=[],
        baseline_words=0,
        top=20,
        pattern_filter=None,
    )


class TestEnvelopeKeys:
    def test_required_top_level_keys_present(self, audit_payload):
        assert set(audit_payload.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, audit_payload):
        assert audit_payload["schema_version"] == "1.0"

    def test_task_surface(self, audit_payload):
        assert audit_payload["task_surface"] == "craft_restoration"

    def test_tool(self, audit_payload):
        assert audit_payload["tool"] == "aic_pattern_audit"

    def test_version(self, audit_payload):
        assert audit_payload["version"] == aic.SCRIPT_VERSION

    def test_available_true(self, audit_payload):
        assert audit_payload["available"] is True

    def test_target_block(self, audit_payload):
        target = audit_payload["target"]
        assert "path" in target and "words" in target
        assert target["path"] == "draft.md"
        assert target["words"] > 0

    def test_baseline_is_null_when_not_supplied(self, audit_payload):
        assert audit_payload["baseline"] is None

    def test_warnings_is_list(self, audit_payload):
        assert isinstance(audit_payload["warnings"], list)


class TestResultsPayload:
    def test_results_has_patterns_dict(self, audit_payload):
        assert "patterns" in audit_payload["results"]
        assert isinstance(audit_payload["results"]["patterns"], dict)

    def test_no_legacy_top_level_keys(self, audit_payload):
        # Pre-migration, baseline_files_loaded / baseline_files_skipped /
        # baseline_words / target / target_words / patterns all lived
        # at the top level. Post-migration they live inside the
        # envelope's target / baseline / results blocks.
        for legacy_key in (
            "baseline_files_loaded", "baseline_files_skipped",
            "baseline_words", "target_words", "patterns",
        ):
            assert legacy_key not in audit_payload, (
                f"legacy top-level key {legacy_key!r} should now live "
                f"inside the envelope's target/baseline/results blocks"
            )


class TestClaimLicense:
    def test_structured_block_has_11_keys(self, audit_payload):
        assert set(audit_payload["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_task_surface_matches_envelope(self, audit_payload):
        assert (
            audit_payload["claim_license"]["task_surface"]
            == audit_payload["task_surface"]
        )

    def test_licenses_text_is_substantive(self, audit_payload):
        # Guard against accidental empty-string regression.
        assert len(audit_payload["claim_license"]["licenses"]) > 50
        assert len(audit_payload["claim_license"]["does_not_license"]) > 50

    def test_comparison_set_carries_word_counts(self, audit_payload):
        cs = audit_payload["claim_license"]["comparison_set"]
        assert "target_words" in cs
        assert "baseline_words" in cs
        assert "has_baseline" in cs

    def test_rendered_block_starts_with_header(self, audit_payload):
        assert audit_payload["claim_license_rendered"].startswith(
            "## What this result licenses"
        )

    def test_rendered_carries_licenses_text(self, audit_payload):
        rendered = audit_payload["claim_license_rendered"]
        # First chunk of the licenses text appears in the rendering.
        assert "density report" in rendered.lower()

    def test_references_block_includes_aic_flags(self, audit_payload):
        refs = audit_payload["claim_license"]["references"]
        assert any("aic-flags.md" in r for r in refs)


class TestSerialization:
    def test_render_json_returns_valid_json(self):
        text = _sample_text()
        sentences = aic.split_sentences(text)
        results = aic.all_patterns(text, sentences)
        out = aic.render_json(
            Path("draft.md"), len(text.split()), results,
            None, [], [], 0,
            top=5, pattern_filter=None,
        )
        parsed = json.loads(out)
        assert parsed["schema_version"] == "1.0"
        assert parsed["tool"] == "aic_pattern_audit"


class TestBaselinePath:
    def test_baseline_block_populated_when_supplied(self, tmp_path):
        text = _sample_text()
        sentences = aic.split_sentences(text)
        target_words = len(text.split())
        results = aic.all_patterns(text, sentences)

        # Construct a synthetic baseline density-per-1k dict.
        baseline_densities = {k: 0.5 for k in results.keys()}
        loaded = [Path("baseline/a.txt"), Path("baseline/b.txt")]
        skipped = []
        payload = aic.build_audit_payload(
            target_path=Path("draft.md"),
            target_words=target_words,
            target_results=results,
            baseline_density_per_1k=baseline_densities,
            baseline_loaded=loaded,
            baseline_skipped=skipped,
            baseline_words=12345,
            top=20,
            pattern_filter=None,
        )
        assert payload["baseline"] is not None
        assert payload["baseline"]["n_files"] == 2
        assert payload["baseline"]["words"] == 12345
        assert payload["baseline"]["files_loaded"] == [
            "baseline/a.txt", "baseline/b.txt",
        ]
        # The per-pattern block carries baseline_density_per_1k +
        # delta_per_1k when a baseline was supplied.
        sample_key = next(iter(payload["results"]["patterns"]))
        assert "baseline_density_per_1k" in payload["results"]["patterns"][sample_key]
        assert "delta_per_1k" in payload["results"]["patterns"][sample_key]
