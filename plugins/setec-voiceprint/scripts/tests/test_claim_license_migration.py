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


# -------------------- B.3: state-routed caveats ----------------


from claim_license import (  # type: ignore  # noqa: E402
    COMPARISON_STATE_CAVEAT_TEMPLATES,
    TARGET_STATE_CAVEAT_TEMPLATES,
    state_routed_caveats,
    with_state_caveats,
)


class TestB3StateRoutedCaveats:
    """``state_routed_caveats(...)`` is a pure function — easy to
    test in isolation. Covers the seven canonical target states
    and the two anchored comparison-baseline cases plus the
    generic fallbacks."""

    def test_no_inputs_returns_empty(self):
        """Safe-by-default no-op when called with no state inputs."""
        assert state_routed_caveats() == []

    def test_unrecognized_target_state_returns_empty(self):
        """An ai_status value outside the SPEC vocabulary should
        NOT produce a caveat (no template), but the helper must
        not raise — defensive against operator-supplied free-text."""
        assert state_routed_caveats(target_ai_status="garbage") == []

    def test_all_canonical_target_states_have_templates(self):
        """Coverage: every value in ALLOWED_AI_STATUS gets a
        template, so a B.3-wired audit script always emits a
        caveat when --ai-status was passed with a valid value."""
        canonical = (
            "pre_ai_human", "ai_generated", "ai_generated_from_outline",
            "ai_assisted", "ai_edited", "mixed", "unknown",
        )
        for state in canonical:
            assert state in TARGET_STATE_CAVEAT_TEMPLATES, (
                f"missing target-state template for {state!r}"
            )
            cs = state_routed_caveats(target_ai_status=state)
            assert len(cs) == 1
            assert cs[0]  # non-empty

    def test_outline_caveat_distinguishes_from_thin_prompt(self):
        """SPEC §9.2: ai_generated_from_outline's caveat must
        emphasize the human-seed origin AND the lack of
        license-to-infer-about-fully-AI-generated."""
        cs = state_routed_caveats(
            target_ai_status="ai_generated_from_outline",
        )
        text = cs[0].lower()
        assert "human seed" in text or "outline" in text
        assert "fully-ai" in text or "thin-prompt" in text

    def test_pre_ai_human_baseline_caveat(self):
        """Exact-match comparison-state caveat for the
        pre-AI-only baseline case."""
        cs = state_routed_caveats(
            comparison_ai_statuses=["pre_ai_human"],
        )
        assert len(cs) == 1
        assert "pre-AI" in cs[0] or "pre_ai_human" in cs[0]

    def test_ai_generated_only_baseline_caveat(self):
        cs = state_routed_caveats(
            comparison_ai_statuses=["ai_generated"],
        )
        assert len(cs) == 1
        assert "ai_generated" in cs[0] or "LLM baseline" in cs[0]

    def test_mixed_baseline_falls_back_to_generic(self):
        """A multi-state baseline that doesn't exactly match a
        template generates the generic "mixes authorship states"
        caveat naming each state present."""
        cs = state_routed_caveats(
            comparison_ai_statuses=["pre_ai_human", "ai_assisted"],
        )
        assert len(cs) == 1
        text = cs[0]
        assert "`pre_ai_human`" in text
        assert "`ai_assisted`" in text

    def test_single_unrecognized_baseline_falls_back_to_generic(self):
        """A single-state baseline that's not pre_ai_human or
        ai_generated still produces a caveat — generic form."""
        cs = state_routed_caveats(
            comparison_ai_statuses=["ai_edited"],
        )
        assert len(cs) == 1
        assert "ai_edited" in cs[0]

    def test_target_and_comparison_combine(self):
        """Both inputs produce both caveats; target first, then
        comparison."""
        cs = state_routed_caveats(
            target_ai_status="ai_assisted",
            comparison_ai_statuses=["pre_ai_human"],
        )
        assert len(cs) == 2
        assert "pre_ai_human" in cs[1] or "pre-AI" in cs[1]


class TestB3WithStateCaveats:
    """``with_state_caveats(license_block, ...)`` returns a new
    ClaimLicense with state caveats appended to
    additional_caveats. Original block's other fields are
    preserved."""

    def test_no_state_inputs_returns_structural_copy(self):
        base = ClaimLicense(
            task_surface="smoothing_diagnosis",
            licenses="L",
            does_not_license="D",
            additional_caveats=["existing caveat"],
        )
        out = with_state_caveats(base)
        assert out.licenses == base.licenses
        assert out.does_not_license == base.does_not_license
        assert out.additional_caveats == ["existing caveat"]
        # Independent copy.
        out.additional_caveats.append("mutation")
        assert "mutation" not in base.additional_caveats

    def test_target_state_appends_caveat(self):
        base = ClaimLicense(
            task_surface="smoothing_diagnosis",
            licenses="L", does_not_license="D",
            additional_caveats=["pre-existing"],
        )
        out = with_state_caveats(
            base, target_ai_status="ai_generated_from_outline",
        )
        assert out.additional_caveats[0] == "pre-existing"
        assert len(out.additional_caveats) == 2
        assert (
            "outline" in out.additional_caveats[1].lower()
            or "human seed" in out.additional_caveats[1].lower()
        )

    def test_target_plus_comparison(self):
        base = ClaimLicense(
            task_surface="smoothing_diagnosis",
            licenses="L", does_not_license="D",
        )
        out = with_state_caveats(
            base,
            target_ai_status="mixed",
            comparison_ai_statuses=["pre_ai_human"],
        )
        assert len(out.additional_caveats) == 2

    def test_render_block_includes_state_caveats(self):
        """End-to-end: the rendered markdown block carries the
        state caveat in its ### Caveats section."""
        base = ClaimLicense(
            task_surface="voice_coherence",
            licenses="L", does_not_license="D",
        )
        out = with_state_caveats(
            base, target_ai_status="ai_generated_from_outline",
        )
        block = out.render_block()
        assert "### Caveats" in block
        assert (
            "outline" in block.lower() or "human seed" in block.lower()
        )

    def test_preserves_all_other_fields(self):
        """The new helper must not drop any of the surrounding
        context fields."""
        base = ClaimLicense(
            task_surface="validation",
            licenses="L", does_not_license="D",
            comparison_set={"n_pairs": 100, "label_by": "author"},
            length_range_words=(300, 5000),
            register_match=["literary_fiction"],
            language_match=["native"],
            fpr_target=0.01,
            confidence_interval_95=(0.45, 0.65),
            references=["ref1", "ref2"],
        )
        out = with_state_caveats(
            base, target_ai_status="ai_edited",
        )
        assert out.task_surface == "validation"
        assert out.comparison_set == {
            "n_pairs": 100, "label_by": "author",
        }
        assert out.length_range_words == (300, 5000)
        assert out.register_match == ["literary_fiction"]
        assert out.language_match == ["native"]
        assert out.fpr_target == 0.01
        assert out.confidence_interval_95 == (0.45, 0.65)
        assert out.references == ["ref1", "ref2"]


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
