#!/usr/bin/env python3
"""Regression tests for the Phase-1 step 5 ClaimLicense migration.

The 1.29.0 ``claim_license.ClaimLicense`` helper shipped as the
canonical "what this result licenses / does not license" block,
with ``from_legacy()`` adapting older harness dicts. As of 1.30.0
all three older harnesses (validation_harness, voice_validation_
harness, general_imposters) render the structured block in their
markdown reports.

Tests verify:

  * Each harness's markdown report carries the structured block's
    "## What this result licenses" header.
  * The legacy ``claim_license`` dict shape is still present in
    JSON output (downstream consumers depend on it; the migration
    is rendering-layer only).
  * ``from_legacy()`` correctly carries the dict's licenses /
    does_not_license fields into the structured block, and the
    rendered markdown includes both.
  * The structured block carries the surface-specific comparison
    context (manifest path, n_pairs, candidate persona, etc.).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

from claim_license import ClaimLicense, from_legacy  # type: ignore


# ------------------- from_legacy() basics -----------------------


class TestFromLegacy:
    def test_carries_licenses_and_does_not_license(self):
        legacy = {
            "licenses": "X reports A.",
            "does_not_license": "X does not report B.",
        }
        lic = from_legacy(legacy, task_surface="validation")
        assert lic.licenses == "X reports A."
        assert lic.does_not_license == "X does not report B."
        assert lic.task_surface == "validation"

    def test_gray_zone_lands_in_caveats(self):
        legacy = {
            "licenses": "x", "does_not_license": "y",
            "gray_zone": "Some refusal range applies.",
        }
        lic = from_legacy(legacy, task_surface="voice_coherence")
        assert "Some refusal range applies." in lic.additional_caveats

    def test_missing_fields_default_to_empty(self):
        lic = from_legacy({}, task_surface="validation")
        assert lic.licenses == ""
        assert lic.does_not_license == ""
        assert lic.additional_caveats == []

    def test_render_block_after_legacy_adapter(self):
        legacy = {
            "licenses": "Reports A.",
            "does_not_license": "Does not report B.",
        }
        lic = from_legacy(legacy, task_surface="validation")
        block = lic.render_block()
        assert "## What this result licenses" in block
        assert "Reports A." in block
        assert "Does not report B." in block


# ------------------- general_imposters migration ----------------


class TestGeneralImpostersMigration:
    def test_render_markdown_includes_structured_block(self):
        import general_imposters as gi  # type: ignore
        result = gi.GIResult(
            target_id="t",
            candidate_persona="alice",
            candidate_n_docs=3,
            n_impostors=8,
            impostor_personas=["a", "b", "c", "d", "e"],
            iterations=100,
            feature_fraction=0.5,
            top_n_features=200,
            wins=92, losses=8,
            proportion=0.92,
            proportion_ci_95=(0.85, 0.96),
            refused=False,
            refusal_reason="",
            decision="consistent_with_candidate",
        )
        md = gi.render_markdown(result)
        assert "## What this result licenses" in md
        # Structured block carries comparison context.
        assert "candidate persona" in md.lower()
        # Decision regions caveat is surfaced.
        assert "gray-zone refusal" in md.lower()

    def test_refused_result_still_carries_license(self):
        import general_imposters as gi  # type: ignore
        result = gi.GIResult(
            target_id="t", candidate_persona="alice",
            candidate_n_docs=3, n_impostors=2, impostor_personas=["a", "b"],
            iterations=0, feature_fraction=0.5, top_n_features=200,
            wins=0, losses=0, proportion=float("nan"),
            proportion_ci_95=None, refused=True,
            refusal_reason="Need at least 5 distinct impostor personas.",
            decision="refused",
        )
        md = gi.render_markdown(result)
        assert "## What this result licenses" in md
        # Refusal section is also present.
        assert "## Refusal" in md

    def test_to_dict_still_carries_legacy_claim_license(self):
        """Backward compat: JSON consumers reading the legacy dict
        shape continue to work."""
        import general_imposters as gi  # type: ignore
        result = gi.GIResult(
            target_id="t", candidate_persona="alice",
            candidate_n_docs=3, n_impostors=8,
            impostor_personas=["a", "b", "c", "d", "e"],
            iterations=100, feature_fraction=0.5, top_n_features=200,
            wins=92, losses=8, proportion=0.92,
            proportion_ci_95=(0.85, 0.96), refused=False,
            refusal_reason="", decision="consistent_with_candidate",
        )
        d = result.to_dict()
        assert "claim_license" in d
        assert "licenses" in d["claim_license"]
        assert "does_not_license" in d["claim_license"]


# ------------------- validation_harness migration ----------------


class TestValidationHarnessMigration:
    def test_claim_license_block_legacy_dict_unchanged(self):
        """The legacy dict-shape function still emits the same fields."""
        import validation_harness as vh  # type: ignore
        block = vh.claim_license_block({
            "operating_point": {"fpr_target": 0.01, "available": True},
        })
        for k in ("licenses", "does_not_license", "operating_point"):
            assert k in block

    def test_structured_block_renders_for_validation_surface(self):
        """The migration adapts the legacy dict via from_legacy() and
        renders a markdown block with the validation task-surface
        label. End-to-end ``render_report`` is exercised by the
        existing ``test_validation_harness_check_corpus.py`` against
        a real harness run; here we just confirm the structured-block
        builder uses the right inputs."""
        legacy = {
            "licenses": "Reports manifest performance.",
            "does_not_license": "Does not generalize beyond manifest.",
            "operating_point": "Threshold at FPR 0.01.",
        }
        lic = from_legacy(legacy, task_surface="validation")
        lic.fpr_target = 0.01
        lic.comparison_set = {
            "manifest": "fake.jsonl",
            "evaluated_surface": "smoothing_diagnosis",
        }
        lic.additional_caveats = [legacy["operating_point"]]
        block = lic.render_block()
        assert "## What this result licenses" in block
        assert "validation / labeled-corpus harness" in block
        assert "Reports manifest performance." in block
        assert "0.01" in block  # fpr_target


# ------------------- voice_validation_harness migration ----------


class TestVoiceValidationHarnessMigration:
    def test_structured_block_renders_for_voice_coherence_surface(self):
        """The voice-validation harness's structured block: legacy
        dict + comparison set (n_pairs, manifest, etc.) + optional
        FPR target. End-to-end ``render_report`` is exercised by
        ``test_voice_validation_harness.py`` against real harness
        output; here we only verify the structured-block construction
        uses the right pieces."""
        legacy = {
            "licenses": "Reports voice-coherence discrimination.",
            "does_not_license": "Does not certify authorship.",
            "operating_point": "Threshold at FPR 0.05.",
        }
        lic = from_legacy(legacy, task_surface="voice_coherence")
        lic.comparison_set = {
            "manifest": "fake.jsonl",
            "n_pairs": 30,
            "n_same_author": 12,
            "n_different_author": 18,
            "label_by": "author",
            "bootstrap_method": "document_cluster",
        }
        lic.fpr_target = 0.05
        block = lic.render_block()
        assert "## What this result licenses" in block
        assert "voice-coherence comparison" in block
        assert "Reports voice-coherence discrimination." in block
        # comparison set surfaces the n_pairs / manifest fields
        assert "n pairs" in block.lower()
        assert "0.05" in block  # fpr_target


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
