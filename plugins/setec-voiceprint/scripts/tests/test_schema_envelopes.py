#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope contract across 29 audit surfaces.

Consolidated from 29 formerly-standalone ``test_<surface>_schema.py``
files. Each file pinned the same output-schema-unification contract
(REQUIRED_TOP_LEVEL_KEYS / CLAIM_LICENSE_KEYS, schema_version=="1.0", the
tool/version/task_surface fields, and a per-surface ``results``/``target``/
``baseline`` shape) against its own audit module's real return shape, using
a synthetic ``envelope``/fixture built by hand per surface to avoid the
spaCy/stylometric dependencies the real audit needs.

The two module-level constants that WERE byte-identical across every file
(REQUIRED_TOP_LEVEL_KEYS, CLAIM_LICENSE_KEYS — verified via ast.dump()
structural comparison) are hoisted once below instead of redefined 29/11
times. Everything else (fixtures, helper builders, and every test class)
is preserved verbatim from its original file: only identifiers that would
collide once 29 files share one module (class names, the ``envelope``
fixture, per-surface helper functions/constants) were renamed, via
whole-word source substitution — no assertion, fixture body, or test
scenario was changed.

Not part of this merge: test_output_schema.py (tests the SHARED
output_schema.py library directly — build_output()/build_baseline_metadata()
— not a per-surface pinned envelope), test_argument_feature_schema.py and
test_narrative_feature_schema.py (both test taxonomy self-consistency of
their respective *_feature_schema.py modules, not an envelope at all).
These three were miscounted as part of the "32 *_schema.py files" family in
the original audit; they don't share the skeleton this file consolidates
and were left alone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
import pytest
from register_classifier import REGISTER_TAXONOMY
import aesthetic_authority_audit as aaudit
import agency_abstraction_audit as aaa
import aic_pattern_audit as aic
import bigram_diff as bd
import chapter_distinctiveness_audit as cda
import check_corpus as cc
import construction_signature_audit as csa
import controls_audit as ca
import discourse_move_signature as dms
import function_word_grammar_audit as fwg
import idiolect_detector as idd
import kicker_density as kd
import known_editor_profile as kep
import manifest_validator as mv
import manuscript_audit as ma
import manuscript_bigram_diff as mbd
import manuscript_repetition_audit as mra
import mimicry_cosplay_audit as mca
import paragraph_audit as pa
import phraseological_signature_audit as psa
import pov_voice_profile as pvp
import punctuation_cadence_audit as pca
import repetition_audit as ra
import stance_modality_audit as sma
import stylometry_core as sc
import surprisal_audit as sa
import variance_audit as va
import voice_distance as vd
import voice_drift_tracker as vdt
import voice_profile as vp


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


# ===========================================================================
# aesthetic_authority_audit  (formerly test_aesthetic_authority_audit_schema.py)
# ===========================================================================


def _aesthetic_authority_audit_fake_audit_block(register: str = "contemporary_essay") -> dict:
    """Construct a minimal block matching the audit function's return
    shape. Avoids the spaCy + Brysbaert dependencies the real audit
    needs while still exercising build_audit_payload's plumbing.
    """
    return {
        "signal_path": "aic_8_9.aesthetic_authority_audit",
        "family": "aic-8-9-compound",
        "status": "provisional",
        "task_surface": "smoothing_diagnosis",
        "claim_license": "voice_diagnostic",
        "aic_9_kicker_density": {
            "value": 0.12,
            "paragraphs": [{"paragraph_index": 0, "is_kicker": True}],
        },
        "aic_8_image_conjunction": {
            "value": 1.4,
            "conjunctions": [],
            "diagnostics": {
                "total_tokens": 2400,
                "total_paragraphs": 18,
                "conjunction_count": 4,
            },
        },
        "aic_8_prestige_metaphor": {
            "value": 0.8,
            "conjunctions": [],
            "diagnostics": {
                "total_tokens": 2400,
                "total_paragraphs": 18,
                "conjunction_count": 4,
            },
        },
        "compound": {
            "kicker_paragraph_count": 3,
            "kicker_with_image_conjunction_count": 2,
            "kicker_with_prestige_metaphor_count": 1,
            "all_three_co_occurrence_count": 1,
            "kicker_with_image_conjunction_rate": 0.67,
            "kicker_with_prestige_metaphor_rate": 0.33,
            "all_three_co_occurrence_rate": 0.33,
            "signal_path": "aic_8_9.aesthetic_authority_compound",
            "family": "aic-8-9-compound",
            "task_surface": "smoothing_diagnosis",
            "claim_license": "voice_diagnostic",
        },
        "diagnostics": {
            "register": register,
            "thresholds": {
                "kicker_word_limit": 15,
                "t1_concreteness_gap": 2.5,
                "t2_embedding_similarity": 0.4,
                "t3_scatter_entropy": 0.7,
            },
            "use_wordnet": True,
        },
    }


@pytest.fixture
def _aesthetic_authority_audit_envelope():
    block = _aesthetic_authority_audit_fake_audit_block()
    text = "Sample prose. " * 400
    return aaudit.build_audit_payload(
        block,
        target_path=Path("draft.md"),
        text=text,
    )


class TestAestheticAuthorityAuditEnvelopeKeys:
    def test_required_keys(self, _aesthetic_authority_audit_envelope):
        assert set(_aesthetic_authority_audit_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _aesthetic_authority_audit_envelope):
        assert _aesthetic_authority_audit_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _aesthetic_authority_audit_envelope):
        assert _aesthetic_authority_audit_envelope["task_surface"] == "smoothing_diagnosis"

    def test_tool_and_version(self, _aesthetic_authority_audit_envelope):
        assert _aesthetic_authority_audit_envelope["tool"] == "aesthetic_authority_audit"
        assert _aesthetic_authority_audit_envelope["version"] == aaudit.SCRIPT_VERSION

    def test_available(self, _aesthetic_authority_audit_envelope):
        assert _aesthetic_authority_audit_envelope["available"] is True


class TestAestheticAuthorityAuditResultsPayload:
    def test_results_carries_legacy_block(self, _aesthetic_authority_audit_envelope):
        r = _aesthetic_authority_audit_envelope["results"]
        assert r["signal_path"] == "aic_8_9.aesthetic_authority_audit"
        assert r["family"] == "aic-8-9-compound"
        assert "aic_9_kicker_density" in r
        assert "aic_8_image_conjunction" in r
        assert "aic_8_prestige_metaphor" in r
        assert "compound" in r

    def test_legacy_claim_license_tag_preserved_in_inner_blocks(self, _aesthetic_authority_audit_envelope):
        # The function-level "claim_license: voice_diagnostic" tag on
        # the legacy block stays for downstream function-call
        # consumers like variance_audit. The _aesthetic_authority_audit_envelope's top-level
        # claim_license is the new structured 11-key dict; do not
        # confuse the two.
        assert _aesthetic_authority_audit_envelope["results"]["claim_license"] == "voice_diagnostic"

    def test_envelope_does_not_contain_legacy_top_keys(self, _aesthetic_authority_audit_envelope):
        for legacy in (
            "signal_path", "family", "status",
            "aic_9_kicker_density", "aic_8_image_conjunction",
            "aic_8_prestige_metaphor", "compound", "diagnostics",
        ):
            assert legacy not in _aesthetic_authority_audit_envelope


class TestAestheticAuthorityAuditClaimLicense:
    def test_structured_block_11_keys(self, _aesthetic_authority_audit_envelope):
        assert set(_aesthetic_authority_audit_envelope["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_task_surface_matches(self, _aesthetic_authority_audit_envelope):
        assert (
            _aesthetic_authority_audit_envelope["claim_license"]["task_surface"]
            == _aesthetic_authority_audit_envelope["task_surface"]
        )

    def test_substantive_text(self, _aesthetic_authority_audit_envelope):
        assert len(_aesthetic_authority_audit_envelope["claim_license"]["licenses"]) > 80
        assert len(_aesthetic_authority_audit_envelope["claim_license"]["does_not_license"]) > 80

    def test_rendered_header(self, _aesthetic_authority_audit_envelope):
        assert _aesthetic_authority_audit_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )

    def test_comparison_set_carries_register(self, _aesthetic_authority_audit_envelope):
        cs = _aesthetic_authority_audit_envelope["claim_license"]["comparison_set"]
        assert cs["register"] == "contemporary_essay"


class TestAestheticAuthorityAuditTargetMetadata:
    def test_target_words_from_diagnostics(self, _aesthetic_authority_audit_envelope):
        # 2400 from the synthetic block's diagnostics.total_tokens
        assert _aesthetic_authority_audit_envelope["target"]["words"] == 2400

    def test_register_lifted_to_target(self, _aesthetic_authority_audit_envelope):
        assert _aesthetic_authority_audit_envelope["target"]["register"] == "contemporary_essay"


class TestAestheticAuthorityAuditNoRegisterPath:
    def test_no_register_omits_target_register(self):
        block = _aesthetic_authority_audit_fake_audit_block(register=None)
        block["diagnostics"]["register"] = None
        text = "Sample. " * 200
        _aesthetic_authority_audit_envelope = aaudit.build_audit_payload(
            block, target_path=Path("x.md"), text=text,
        )
        assert "register" not in _aesthetic_authority_audit_envelope["target"]


# ===========================================================================
# agency_abstraction_audit  (formerly test_agency_abstraction_audit_schema.py)
# ===========================================================================


def _agency_abstraction_audit_sample_text() -> str:
    return (
        "The committee proposes consideration of the recommendation. "
        "The proposal was reviewed and the timeline was extended. "
        "Implementation will commence following authorization. "
        "Daria signed the agreement on Tuesday. The dashboard "
        "highlighted regional activity. Stakeholders requested "
        "further analysis. The agency-level coordination role was "
        "delegated to the working group. Maria reviewed the budget "
        "with three regional partners. Decisions remained pending "
        "while clarification was sought."
    ) * 8


@pytest.fixture
def _agency_abstraction_audit_envelope():
    text = _agency_abstraction_audit_sample_text()
    audit = aaa.audit_agency_abstraction(text)
    return aaa.build_audit_payload(
        audit,
        target_path=Path("draft.md"),
        baseline_block=None,
        baseline_comparison=None,
    )


class TestAgencyAbstractionAuditEnvelopeKeys:
    def test_required_top_level_keys(self, _agency_abstraction_audit_envelope):
        assert set(_agency_abstraction_audit_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _agency_abstraction_audit_envelope):
        assert _agency_abstraction_audit_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _agency_abstraction_audit_envelope):
        assert _agency_abstraction_audit_envelope["task_surface"] == "smoothing_diagnosis"

    def test_tool_and_version(self, _agency_abstraction_audit_envelope):
        assert _agency_abstraction_audit_envelope["tool"] == "agency_abstraction_audit"
        assert _agency_abstraction_audit_envelope["version"] == aaa.SCRIPT_VERSION

    def test_available_true(self, _agency_abstraction_audit_envelope):
        assert _agency_abstraction_audit_envelope["available"] is True

    def test_target_has_required_subkeys(self, _agency_abstraction_audit_envelope):
        assert "path" in _agency_abstraction_audit_envelope["target"]
        assert "words" in _agency_abstraction_audit_envelope["target"]
        assert _agency_abstraction_audit_envelope["target"]["path"] == "draft.md"
        assert _agency_abstraction_audit_envelope["target"]["words"] > 0

    def test_baseline_null_when_not_supplied(self, _agency_abstraction_audit_envelope):
        assert _agency_abstraction_audit_envelope["baseline"] is None


class TestAgencyAbstractionAuditResultsPayload:
    def test_results_carries_audit_signals(self, _agency_abstraction_audit_envelope):
        r = _agency_abstraction_audit_envelope["results"]
        assert "raw_counts" in r
        assert "densities_per_1k" in r
        assert "entity_to_action_ratio" in r
        assert "compression" in r

    def test_no_legacy_top_level_keys(self, _agency_abstraction_audit_envelope):
        for legacy in (
            "n_words", "raw_counts", "densities_per_1k",
            "entity_to_action_ratio", "compression",
            "baseline_block", "baseline_comparison",
        ):
            assert legacy not in _agency_abstraction_audit_envelope, (
                f"legacy top-level key {legacy!r} should now live "
                f"inside the _agency_abstraction_audit_envelope's target/baseline/results blocks"
            )


class TestAgencyAbstractionAuditClaimLicense:
    def test_structured_block_11_keys(self, _agency_abstraction_audit_envelope):
        assert set(_agency_abstraction_audit_envelope["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_task_surface_matches(self, _agency_abstraction_audit_envelope):
        assert (
            _agency_abstraction_audit_envelope["claim_license"]["task_surface"]
            == _agency_abstraction_audit_envelope["task_surface"]
        )

    def test_rendered_starts_with_header(self, _agency_abstraction_audit_envelope):
        assert _agency_abstraction_audit_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestAgencyAbstractionAuditUnavailablePath:
    def test_empty_text_emits_well_formed_envelope(self):
        audit = aaa.audit_agency_abstraction("")
        _agency_abstraction_audit_envelope = aaa.build_audit_payload(
            audit,
            target_path=Path("empty.md"),
            baseline_block=None,
            baseline_comparison=None,
        )
        assert _agency_abstraction_audit_envelope["available"] is False
        assert _agency_abstraction_audit_envelope["claim_license"] is None
        assert _agency_abstraction_audit_envelope["claim_license_rendered"] is None
        assert _agency_abstraction_audit_envelope["warnings"]
        assert _agency_abstraction_audit_envelope["results"] == {}


class TestAgencyAbstractionAuditBaselinePath:
    def test_baseline_block_populated(self):
        text = _agency_abstraction_audit_sample_text()
        audit = aaa.audit_agency_abstraction(text)
        baseline_block = {
            "n_files": 3, "n_words": 12000,
            "per_file_summaries": [
                {"path": "baseline_001", "n_words": 4000},
                {"path": "baseline_002", "n_words": 4000},
                {"path": "baseline_003", "n_words": 4000},
            ],
        }
        _agency_abstraction_audit_envelope = aaa.build_audit_payload(
            audit,
            target_path=Path("draft.md"),
            baseline_block=baseline_block,
            baseline_comparison={"available": True, "z_scores": {}},
        )
        assert _agency_abstraction_audit_envelope["baseline"]["n_files"] == 3
        assert _agency_abstraction_audit_envelope["baseline"]["words"] == 12000
        assert "per_file_summaries" in _agency_abstraction_audit_envelope["baseline"]
        assert _agency_abstraction_audit_envelope["results"]["baseline_comparison"]["available"] is True


class TestAgencyAbstractionAuditSerialization:
    def test_envelope_round_trips_through_json(self, _agency_abstraction_audit_envelope):
        s = json.dumps(_agency_abstraction_audit_envelope, default=str)
        parsed = json.loads(s)
        assert parsed["schema_version"] == "1.0"
        assert parsed["tool"] == "agency_abstraction_audit"


# ===========================================================================
# aic_pattern_audit  (formerly test_aic_pattern_audit_schema.py)
# ===========================================================================


def _aic_pattern_audit_sample_text() -> str:
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
def _aic_pattern_audit_audit_payload():
    text = _aic_pattern_audit_sample_text()
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


class TestAicPatternAuditEnvelopeKeys:
    def test_required_top_level_keys_present(self, _aic_pattern_audit_audit_payload):
        assert set(_aic_pattern_audit_audit_payload.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _aic_pattern_audit_audit_payload):
        assert _aic_pattern_audit_audit_payload["schema_version"] == "1.0"

    def test_task_surface(self, _aic_pattern_audit_audit_payload):
        assert _aic_pattern_audit_audit_payload["task_surface"] == "craft_restoration"

    def test_tool(self, _aic_pattern_audit_audit_payload):
        assert _aic_pattern_audit_audit_payload["tool"] == "aic_pattern_audit"

    def test_version(self, _aic_pattern_audit_audit_payload):
        assert _aic_pattern_audit_audit_payload["version"] == aic.SCRIPT_VERSION

    def test_available_true(self, _aic_pattern_audit_audit_payload):
        assert _aic_pattern_audit_audit_payload["available"] is True

    def test_target_block(self, _aic_pattern_audit_audit_payload):
        target = _aic_pattern_audit_audit_payload["target"]
        assert "path" in target and "words" in target
        assert target["path"] == "draft.md"
        assert target["words"] > 0

    def test_baseline_is_null_when_not_supplied(self, _aic_pattern_audit_audit_payload):
        assert _aic_pattern_audit_audit_payload["baseline"] is None

    def test_warnings_is_list(self, _aic_pattern_audit_audit_payload):
        assert isinstance(_aic_pattern_audit_audit_payload["warnings"], list)


class TestAicPatternAuditResultsPayload:
    def test_results_has_patterns_dict(self, _aic_pattern_audit_audit_payload):
        assert "patterns" in _aic_pattern_audit_audit_payload["results"]
        assert isinstance(_aic_pattern_audit_audit_payload["results"]["patterns"], dict)

    def test_no_legacy_top_level_keys(self, _aic_pattern_audit_audit_payload):
        # Pre-migration, baseline_files_loaded / baseline_files_skipped /
        # baseline_words / target / target_words / patterns all lived
        # at the top level. Post-migration they live inside the
        # envelope's target / baseline / results blocks.
        for legacy_key in (
            "baseline_files_loaded", "baseline_files_skipped",
            "baseline_words", "target_words", "patterns",
        ):
            assert legacy_key not in _aic_pattern_audit_audit_payload, (
                f"legacy top-level key {legacy_key!r} should now live "
                f"inside the envelope's target/baseline/results blocks"
            )


class TestAicPatternAuditClaimLicense:
    def test_structured_block_has_11_keys(self, _aic_pattern_audit_audit_payload):
        assert set(_aic_pattern_audit_audit_payload["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_task_surface_matches_envelope(self, _aic_pattern_audit_audit_payload):
        assert (
            _aic_pattern_audit_audit_payload["claim_license"]["task_surface"]
            == _aic_pattern_audit_audit_payload["task_surface"]
        )

    def test_licenses_text_is_substantive(self, _aic_pattern_audit_audit_payload):
        # Guard against accidental empty-string regression.
        assert len(_aic_pattern_audit_audit_payload["claim_license"]["licenses"]) > 50
        assert len(_aic_pattern_audit_audit_payload["claim_license"]["does_not_license"]) > 50

    def test_comparison_set_carries_word_counts(self, _aic_pattern_audit_audit_payload):
        cs = _aic_pattern_audit_audit_payload["claim_license"]["comparison_set"]
        assert "target_words" in cs
        assert "baseline_words" in cs
        assert "has_baseline" in cs

    def test_rendered_block_starts_with_header(self, _aic_pattern_audit_audit_payload):
        assert _aic_pattern_audit_audit_payload["claim_license_rendered"].startswith(
            "## What this result licenses"
        )

    def test_rendered_carries_licenses_text(self, _aic_pattern_audit_audit_payload):
        rendered = _aic_pattern_audit_audit_payload["claim_license_rendered"]
        # First chunk of the licenses text appears in the rendering.
        assert "density report" in rendered.lower()

    def test_references_block_includes_aic_flags(self, _aic_pattern_audit_audit_payload):
        refs = _aic_pattern_audit_audit_payload["claim_license"]["references"]
        assert any("aic-flags.md" in r for r in refs)


class TestAicPatternAuditSerialization:
    def test_render_json_returns_valid_json(self):
        text = _aic_pattern_audit_sample_text()
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


class TestAicPatternAuditBaselinePath:
    def test_baseline_block_populated_when_supplied(self, tmp_path):
        text = _aic_pattern_audit_sample_text()
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


# ===========================================================================
# bigram_diff  (formerly test_bigram_diff_schema.py)
# ===========================================================================


def _bigram_diff_diff_rows():
    return [
        {"bigram": "NOUN_VERB", "kl_contrib": 0.04, "target_p": 0.10, "cluster_p": 0.06},
        {"bigram": "DET_NOUN", "kl_contrib": -0.02, "target_p": 0.08, "cluster_p": 0.10},
    ]


@pytest.fixture
def _bigram_diff_envelope():
    json_str = bd.render_json(
        target_path=Path("draft.txt"),
        target_counts={"NOUN_VERB": 50, "DET_NOUN": 40, "ADJ_NOUN": 30},
        cluster_loaded=[Path("a.txt"), Path("b.txt")],
        cluster_skipped=[],
        pooled_diff=_bigram_diff_diff_rows(),
        mean_diff=_bigram_diff_diff_rows(),
        top=10, alpha=1.0, min_count=2,
    )
    return json.loads(json_str)


class TestBigramDiffEnvelopeKeys:
    def test_required_keys(self, _bigram_diff_envelope):
        assert set(_bigram_diff_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _bigram_diff_envelope):
        assert _bigram_diff_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _bigram_diff_envelope):
        assert _bigram_diff_envelope["task_surface"] == "smoothing_diagnosis"

    def test_tool_and_version(self, _bigram_diff_envelope):
        assert _bigram_diff_envelope["tool"] == "bigram_diff"
        assert _bigram_diff_envelope["version"] == bd.SCRIPT_VERSION


class TestBigramDiffResultsAndBaseline:
    def test_results_carries_diffs(self, _bigram_diff_envelope):
        r = _bigram_diff_envelope["results"]
        assert r["target_bigrams"] == 120
        assert r["target_unique"] == 3
        assert "pooled" in r["diffs"]
        assert "mean" in r["diffs"]

    def test_baseline_n_files(self, _bigram_diff_envelope):
        assert _bigram_diff_envelope["baseline"]["n_files"] == 2
        assert _bigram_diff_envelope["baseline"]["files_loaded"] == ["a.txt", "b.txt"]

    def test_no_legacy_top_level_keys(self, _bigram_diff_envelope):
        for legacy in (
            "target_bigrams", "target_unique",
            "cluster_files_loaded", "cluster_files_skipped",
            "smoothing_alpha", "min_count", "diffs",
        ):
            assert legacy not in _bigram_diff_envelope


class TestBigramDiffClaimLicense:
    def test_structured(self, _bigram_diff_envelope):
        cs = _bigram_diff_envelope["claim_license"]["comparison_set"]
        assert cs["target_bigrams"] == 120
        assert cs["n_cluster_files"] == 2
        assert cs["smoothing_alpha"] == 1.0


class TestBigramDiffSkippedClusterFiles:
    def test_skipped_files_produce_warning(self):
        json_str = bd.render_json(
            target_path=Path("draft.txt"),
            target_counts={"X_Y": 10},
            cluster_loaded=[Path("a.txt")],
            cluster_skipped=[Path("bad.txt"), Path("locked.txt")],
            pooled_diff=_bigram_diff_diff_rows(),
            mean_diff=None,
            top=10, alpha=1.0, min_count=1,
        )
        env = json.loads(json_str)
        assert env["warnings"]
        assert any("skipped" in w.lower() for w in env["warnings"])


# ===========================================================================
# chapter_distinctiveness_audit  (formerly test_chapter_distinctiveness_audit_schema.py)
# ===========================================================================


def _chapter_distinctiveness_audit_chapters():
    return [
        {
            "label": "Chapter 1",
            "text": (
                "The forge glowed. Iron sang under the hammer. The "
                "smith counted his blows. The forge glowed brighter. "
            ) * 6,
        },
        {
            "label": "Chapter 2",
            "text": (
                "The river ran quick. The boat tilted at the bend. "
                "Brackish water lapped the hull. The bend opened wide. "
            ) * 6,
        },
        {
            "label": "Chapter 3",
            "text": (
                "The committee deliberated. The proposal landed. The "
                "budget contracted. Daria signed off on the timeline. "
            ) * 6,
        },
    ]


@pytest.fixture
def _chapter_distinctiveness_audit_envelope():
    result = cda.audit_chapter_distinctiveness(
        _chapter_distinctiveness_audit_chapters(),
        function_words=set(),
        anchor_words=set(),
        min_count=2,
        min_word_len=4,
        cluster_window=300,
        min_ratio=1.0,
    )
    return cda.build_audit_payload(
        result, target_path=Path("manuscript.md"),
    )


class TestChapterDistinctivenessAuditEnvelopeKeys:
    def test_required_keys(self, _chapter_distinctiveness_audit_envelope):
        assert set(_chapter_distinctiveness_audit_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _chapter_distinctiveness_audit_envelope):
        assert _chapter_distinctiveness_audit_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _chapter_distinctiveness_audit_envelope):
        assert _chapter_distinctiveness_audit_envelope["task_surface"] == "smoothing_diagnosis"

    def test_tool_and_version(self, _chapter_distinctiveness_audit_envelope):
        assert _chapter_distinctiveness_audit_envelope["tool"] == "chapter_distinctiveness_audit"
        assert _chapter_distinctiveness_audit_envelope["version"] == cda.SCRIPT_VERSION


class TestChapterDistinctivenessAuditTargetAndBaseline:
    def test_target_words(self, _chapter_distinctiveness_audit_envelope):
        assert _chapter_distinctiveness_audit_envelope["target"]["words"] > 0

    def test_target_carries_n_chapters(self, _chapter_distinctiveness_audit_envelope):
        assert _chapter_distinctiveness_audit_envelope["target"]["n_chapters"] == 3

    def test_baseline_is_null(self, _chapter_distinctiveness_audit_envelope):
        """Internal-baseline (leave-one-out); no external baseline."""
        assert _chapter_distinctiveness_audit_envelope["baseline"] is None


class TestChapterDistinctivenessAuditResultsPayload:
    def test_results_carries_chapters(self, _chapter_distinctiveness_audit_envelope):
        r = _chapter_distinctiveness_audit_envelope["results"]
        assert r["n_chapters"] == 3
        assert len(r["chapters"]) == 3
        for ch in r["chapters"]:
            assert "label" in ch
            assert "n_target_words" in ch
            assert "candidates" in ch

    def test_no_legacy_top_level_keys(self, _chapter_distinctiveness_audit_envelope):
        for legacy in (
            "n_chapters", "total_target_words", "chapters",
        ):
            assert legacy not in _chapter_distinctiveness_audit_envelope


class TestChapterDistinctivenessAuditClaimLicense:
    def test_structured(self, _chapter_distinctiveness_audit_envelope):
        cl = _chapter_distinctiveness_audit_envelope["claim_license"]
        assert cl["task_surface"] == "smoothing_diagnosis"
        assert len(cl["licenses"]) > 80

    def test_rendered_header(self, _chapter_distinctiveness_audit_envelope):
        assert _chapter_distinctiveness_audit_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


# ===========================================================================
# check_corpus  (formerly test_check_corpus_schema.py)
# ===========================================================================


def _check_corpus_fake_result(status="clean"):
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
def _check_corpus_envelope():
    return cc.build_audit_payload(
        _check_corpus_fake_result(), target_path=Path("manifest.jsonl"),
    )


class TestCheckCorpusEnvelopeKeys:
    def test_required_keys(self, _check_corpus_envelope):
        assert set(_check_corpus_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _check_corpus_envelope):
        assert _check_corpus_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _check_corpus_envelope):
        assert _check_corpus_envelope["task_surface"] == "validation"

    def test_tool_and_version(self, _check_corpus_envelope):
        assert _check_corpus_envelope["tool"] == "check_corpus"
        assert _check_corpus_envelope["version"] == cc.SCRIPT_VERSION


class TestCheckCorpusResultsAndTarget:
    def test_target_words_from_tokens_before(self, _check_corpus_envelope):
        assert _check_corpus_envelope["target"]["words"] == 50000

    def test_results_carries_status_and_counts(self, _check_corpus_envelope):
        r = _check_corpus_envelope["results"]
        assert r["status"] == "clean"
        assert r["n_files"] == 12
        assert r["thresholds"]["warn_threshold"] == 0.05
        assert r["dominant_rule"] == "html"

    def test_baseline_is_null(self, _check_corpus_envelope):
        assert _check_corpus_envelope["baseline"] is None


class TestCheckCorpusClaimLicense:
    def test_structured(self, _check_corpus_envelope):
        cs = _check_corpus_envelope["claim_license"]["comparison_set"]
        assert cs["n_files"] == 12
        assert cs["status"] == "clean"


class TestCheckCorpusWarningOnNonCleanStatus:
    def test_warning_status_emits_envelope_warning(self):
        env = cc.build_audit_payload(
            _check_corpus_fake_result(status="warning"), target_path=Path("m.jsonl"),
        )
        assert env["warnings"]
        assert "warning" in env["warnings"][0].lower()


# ===========================================================================
# construction_signature_audit  (formerly test_construction_signature_audit_schema.py)
# ===========================================================================


def _construction_signature_audit_sample() -> str:
    return (
        "There is a draft. What matters is the voice. It is "
        "important to revise. Although tired, she continued. "
        "Despite the timeline, the team produced a draft. The "
        "report, somewhat surprisingly, landed on time. To begin "
        "with, the framing helped. From the outset, the budget "
        "constrained the scope."
    ) * 3


def _construction_signature_audit_audit(text=None, baseline_density=None, baseline_loaded=None):
    text = text or _construction_signature_audit_sample()
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
def _construction_signature_audit_envelope():
    return csa.build_audit_payload(_construction_signature_audit_audit())


class TestConstructionSignatureAuditEnvelopeKeys:
    def test_required_keys(self, _construction_signature_audit_envelope):
        assert set(_construction_signature_audit_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _construction_signature_audit_envelope):
        assert _construction_signature_audit_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _construction_signature_audit_envelope):
        assert _construction_signature_audit_envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, _construction_signature_audit_envelope):
        assert _construction_signature_audit_envelope["tool"] == "construction_signature_audit"
        assert _construction_signature_audit_envelope["version"] == csa.SCRIPT_VERSION

    def test_target_path_and_words(self, _construction_signature_audit_envelope):
        assert _construction_signature_audit_envelope["target"]["path"] == "draft.md"
        assert _construction_signature_audit_envelope["target"]["words"] > 0

    def test_target_carries_spacy_available(self, _construction_signature_audit_envelope):
        # Script-specific environment metadata rides under target_extra.
        assert "spacy_available" in _construction_signature_audit_envelope["target"]


class TestConstructionSignatureAuditResultsPayload:
    def test_results_carries_constructions(self, _construction_signature_audit_envelope):
        assert "constructions" in _construction_signature_audit_envelope["results"]
        cons = _construction_signature_audit_envelope["results"]["constructions"]
        assert isinstance(cons, dict)
        assert len(cons) > 0

    def test_no_legacy_top_level_keys(self, _construction_signature_audit_envelope):
        for legacy in (
            "target_words", "constructions", "spacy_available",
            "baseline_words", "baseline_files_loaded_count",
            "baseline_files_skipped_count",
        ):
            assert legacy not in _construction_signature_audit_envelope


class TestConstructionSignatureAuditClaimLicense:
    def test_structured_block_11_keys(self, _construction_signature_audit_envelope):
        assert set(_construction_signature_audit_envelope["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_task_surface_matches(self, _construction_signature_audit_envelope):
        assert (
            _construction_signature_audit_envelope["claim_license"]["task_surface"]
            == _construction_signature_audit_envelope["task_surface"]
        )

    def test_rendered_header(self, _construction_signature_audit_envelope):
        assert _construction_signature_audit_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )

    def test_comparison_set_carries_keys(self, _construction_signature_audit_envelope):
        cs = _construction_signature_audit_envelope["claim_license"]["comparison_set"]
        for k in (
            "target_words", "n_baseline_files",
            "n_constructions_available", "spacy_available",
        ):
            assert k in cs


class TestConstructionSignatureAuditBaselinePath:
    def test_baseline_populated_when_supplied(self):
        baseline_density = {"existential_there": 1.0}
        audit = _construction_signature_audit_audit(
            baseline_density=baseline_density,
            baseline_loaded=[Path("base/a.txt")],
        )
        _construction_signature_audit_envelope = csa.build_audit_payload(audit)
        assert _construction_signature_audit_envelope["baseline"] is not None
        # n_files comes from baseline_files_loaded_count (privacy-
        # default; filename list is suppressed by build_audit's
        # include_baseline_filenames=False path).
        assert _construction_signature_audit_envelope["baseline"]["n_files"] == 1
        assert _construction_signature_audit_envelope["baseline"]["words"] == 5000
        # File list is absent when privacy-default is in effect.
        assert "files_loaded" not in _construction_signature_audit_envelope["baseline"]


class TestConstructionSignatureAuditBuildAuditUnchanged:
    def test_build_audit_still_returns_legacy_shape(self):
        """build_audit's legacy top-level keys stay because internal
        tests (test_build_audit_includes_required_fields) pin them.
        """
        audit = _construction_signature_audit_audit()
        for k in (
            "task_surface", "tool", "version", "target",
            "target_words", "spacy_available", "constructions",
            "claim_license",
        ):
            assert k in audit
        assert isinstance(audit["claim_license"], dict)
        assert "rendered" in audit["claim_license"]


# ===========================================================================
# controls_audit  (formerly test_controls_audit_schema.py)
# ===========================================================================


_CONTROLS_AUDIT_BASELINE = [
    "The committee deliberated through the afternoon. The room was warm.",
    "Maria signed the agreement. Daria reviewed it after lunch.",
    "There was a meeting. The team produced a draft.",
    "Although tired, the working group continued.",
    "By the end of the day, three workstreams advanced.",
]


@pytest.fixture
def _controls_audit_envelope():
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
        baseline_texts=_CONTROLS_AUDIT_BASELINE,
        negative_control_text=negative,
        positive_control_text=positive,
    )
    return ca.build_audit_payload(
        report, target_path=Path("questioned.md"),
    )


class TestControlsAuditEnvelopeKeys:
    def test_required_keys(self, _controls_audit_envelope):
        assert set(_controls_audit_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _controls_audit_envelope):
        assert _controls_audit_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _controls_audit_envelope):
        assert _controls_audit_envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, _controls_audit_envelope):
        assert _controls_audit_envelope["tool"] == "controls_audit"
        assert _controls_audit_envelope["version"] == ca.SCRIPT_VERSION


class TestControlsAuditResultsPayload:
    def test_results_carries_questioned_and_controls(self, _controls_audit_envelope):
        r = _controls_audit_envelope["results"]
        for k in (
            "questioned", "negative_control",
            "positive_control", "classification",
        ):
            assert k in r, f"missing results key: {k}"

    def test_no_legacy_top_level_keys(self, _controls_audit_envelope):
        for legacy in (
            "questioned", "negative_control",
            "positive_control", "classification", "n_baseline_files",
        ):
            assert legacy not in _controls_audit_envelope


class TestControlsAuditClaimLicense:
    def test_structured_block(self, _controls_audit_envelope):
        cl = _controls_audit_envelope["claim_license"]
        assert cl["task_surface"] == "voice_coherence"
        cs = cl["comparison_set"]
        assert cs["negative_control_supplied"] is True
        assert cs["positive_control_supplied"] is True
        assert cs["n_baseline_files"] > 0

    def test_rendered_header(self, _controls_audit_envelope):
        assert _controls_audit_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestControlsAuditUnavailable:
    def test_empty_baseline(self):
        report = ca.run_controls_audit(
            questioned_text="Some questioned text.",
            baseline_texts=[],
            negative_control_text=None,
            positive_control_text=None,
        )
        _controls_audit_envelope = ca.build_audit_payload(
            report, target_path=Path("q.md"),
        )
        assert _controls_audit_envelope["available"] is False
        assert _controls_audit_envelope["claim_license"] is None
        assert _controls_audit_envelope["warnings"]


class TestControlsAuditBaseline:
    def test_baseline_block_n_files_populated(self, _controls_audit_envelope):
        assert _controls_audit_envelope["baseline"] is not None
        assert _controls_audit_envelope["baseline"]["n_files"] == len(_CONTROLS_AUDIT_BASELINE)
        # words is 0 because run_controls_audit does not surface
        # baseline word counts in its return shape.
        assert _controls_audit_envelope["baseline"]["words"] == 0


# ===========================================================================
# discourse_move_signature  (formerly test_discourse_move_signature_schema.py)
# ===========================================================================


def _discourse_move_signature_sample_text() -> str:
    return (
        "First, the committee reviewed the proposal. However, the "
        "timeline remained ambiguous. Moreover, the budget needed "
        "revisiting. In contrast, the original deadline was firm. "
        "Therefore, the schedule was extended. For example, the "
        "implementation phase was pushed back. Nevertheless, the "
        "project remained on track. In summary, three workstreams "
        "advanced. To clarify, scope was narrowed. To be clear, the "
        "committee's mandate did not change. Importantly, the goal "
        "stood firm. To be precise, the deliverable shifted by two "
        "weeks. In short, the project survived."
    ) * 4


@pytest.fixture
def _discourse_move_signature_envelope():
    text = _discourse_move_signature_sample_text()
    audit = dms.audit_discourse_moves(text)
    return dms.build_audit_payload(
        audit,
        target_path=Path("draft.md"),
        baseline_block=None,
        baseline_comparison=None,
    )


class TestDiscourseMoveSignatureEnvelopeKeys:
    def test_required_keys(self, _discourse_move_signature_envelope):
        assert set(_discourse_move_signature_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _discourse_move_signature_envelope):
        assert _discourse_move_signature_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _discourse_move_signature_envelope):
        assert _discourse_move_signature_envelope["task_surface"] == "smoothing_diagnosis"

    def test_tool_and_version(self, _discourse_move_signature_envelope):
        assert _discourse_move_signature_envelope["tool"] == "discourse_move_signature"
        assert _discourse_move_signature_envelope["version"] == dms.SCRIPT_VERSION

    def test_target_carries_sentences(self, _discourse_move_signature_envelope):
        assert "sentences" in _discourse_move_signature_envelope["target"]
        assert _discourse_move_signature_envelope["target"]["sentences"] > 0


class TestDiscourseMoveSignatureResultsPayload:
    def test_results_carries_audit_signals(self, _discourse_move_signature_envelope):
        r = _discourse_move_signature_envelope["results"]
        for k in (
            "category_counts", "category_densities_per_1k",
            "total_marker_density_per_1k",
            "move_sequence", "move_sequence_bigrams",
            "move_sequence_entropy_bits",
            "marked_only_entropy_bits", "relation_distribution",
            "compression",
        ):
            assert k in r, f"missing results key: {k}"

    def test_relation_distribution_flows_into_results(self, _discourse_move_signature_envelope):
        """The PDTB relation layer rides in `results` and survives the
        R4 bounds walk (entropy fields are >= 0; fractions/counts/
        densities are unmatched by the surprisal/probability gates)."""
        rel = _discourse_move_signature_envelope["results"]["relation_distribution"]
        assert rel["calibration_status"] == "uncalibrated"
        assert rel["buckets"] == [
            "comparison", "contingency", "expansion", "temporal",
        ]
        assert 0.0 <= rel["relation_entropy_bits"] <= rel[
            "relation_entropy_max_bits"
        ] == 2.0

    def test_no_legacy_top_level_keys(self, _discourse_move_signature_envelope):
        for legacy in (
            "n_words", "n_sentences", "category_counts",
            "category_densities_per_1k", "total_marker_density_per_1k",
            "move_sequence", "compression", "baseline_block",
            "baseline_comparison",
        ):
            assert legacy not in _discourse_move_signature_envelope


class TestDiscourseMoveSignatureClaimLicense:
    def test_structured_block_present(self, _discourse_move_signature_envelope):
        cl = _discourse_move_signature_envelope["claim_license"]
        assert cl is not None
        assert cl["task_surface"] == _discourse_move_signature_envelope["task_surface"]
        assert cl["licenses"]
        assert cl["does_not_license"]

    def test_rendered_block_header(self, _discourse_move_signature_envelope):
        assert _discourse_move_signature_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestDiscourseMoveSignatureAiStatusRouting:
    def test_ai_status_flows_through(self):
        text = _discourse_move_signature_sample_text()
        audit = dms.audit_discourse_moves(text)
        audit["ai_status"] = "ai_generated_from_outline"
        _discourse_move_signature_envelope = dms.build_audit_payload(
            audit,
            target_path=Path("draft.md"),
            baseline_block=None,
            baseline_comparison=None,
        )
        assert _discourse_move_signature_envelope["ai_status"] == "ai_generated_from_outline"
        # State-routed caveats land in additional_caveats per B.3.
        caveats = _discourse_move_signature_envelope["claim_license"]["additional_caveats"]
        assert any("outline" in c.lower() for c in caveats)


class TestDiscourseMoveSignatureUnavailable:
    def test_empty_text(self):
        audit = dms.audit_discourse_moves("")
        _discourse_move_signature_envelope = dms.build_audit_payload(
            audit,
            target_path=Path("empty.md"),
            baseline_block=None,
            baseline_comparison=None,
        )
        assert _discourse_move_signature_envelope["available"] is False
        assert _discourse_move_signature_envelope["claim_license"] is None
        assert _discourse_move_signature_envelope["warnings"]


class TestDiscourseMoveSignatureBaseline:
    def test_baseline_block_populated(self):
        text = _discourse_move_signature_sample_text()
        audit = dms.audit_discourse_moves(text)
        baseline_block = {
            "n_files": 4, "n_words": 20000,
            "categories_summary": {"contrast": 0.05},
        }
        _discourse_move_signature_envelope = dms.build_audit_payload(
            audit,
            target_path=Path("draft.md"),
            baseline_block=baseline_block,
            baseline_comparison={"available": True, "category_density_z_scores": {}},
        )
        assert _discourse_move_signature_envelope["baseline"]["n_files"] == 4
        assert _discourse_move_signature_envelope["baseline"]["words"] == 20000
        assert "categories_summary" in _discourse_move_signature_envelope["baseline"]


class TestDiscourseMoveSignatureSerialization:
    def test_json_round_trip(self, _discourse_move_signature_envelope):
        s = json.dumps(_discourse_move_signature_envelope, default=str)
        parsed = json.loads(s)
        assert parsed["schema_version"] == "1.0"
        assert parsed["tool"] == "discourse_move_signature"


# ===========================================================================
# function_word_grammar_audit  (formerly test_function_word_grammar_audit_schema.py)
# ===========================================================================


def _function_word_grammar_audit_sample_text() -> str:
    return (
        "The committee that gathered in the afternoon was the one "
        "which had been waiting. She said that he would consider "
        "the proposal. Although tired, the team continued. In the "
        "long run, the budget that was approved will determine "
        "whether the project succeeds. Despite the timeline, the "
        "work has begun. From the outset, scope mattered most."
    ) * 4


@pytest.fixture
def _function_word_grammar_audit_envelope():
    text = _function_word_grammar_audit_sample_text()
    audit = fwg.audit_function_word_grammar(text)
    return fwg.build_audit_payload(
        audit,
        target_path=Path("draft.md"),
        baseline_block=None,
        baseline_comparison=None,
    )


class TestFunctionWordGrammarAuditEnvelopeKeys:
    def test_required_keys(self, _function_word_grammar_audit_envelope):
        assert set(_function_word_grammar_audit_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _function_word_grammar_audit_envelope):
        assert _function_word_grammar_audit_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _function_word_grammar_audit_envelope):
        assert _function_word_grammar_audit_envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, _function_word_grammar_audit_envelope):
        assert _function_word_grammar_audit_envelope["tool"] == "function_word_grammar_audit"
        assert _function_word_grammar_audit_envelope["version"] == fwg.SCRIPT_VERSION


class TestFunctionWordGrammarAuditResultsPayload:
    def test_results_carries_audit_signals(self, _function_word_grammar_audit_envelope):
        r = _function_word_grammar_audit_envelope["results"]
        for k in (
            "n_function_words", "function_word_ratio",
            "function_bigrams", "function_bigram_entropy_bits",
            "preposition_counts", "preposition_entropy_bits",
            "subordinator_counts", "auxiliary_chain_count",
            "pronoun_transition", "compression",
        ):
            assert k in r, f"missing results key: {k}"

    def test_no_legacy_top_level_keys(self, _function_word_grammar_audit_envelope):
        for legacy in (
            "n_words", "n_function_words", "function_word_ratio",
            "function_bigrams", "compression",
            "baseline_block", "baseline_comparison",
        ):
            assert legacy not in _function_word_grammar_audit_envelope


class TestFunctionWordGrammarAuditClaimLicense:
    def test_structured_block(self, _function_word_grammar_audit_envelope):
        cl = _function_word_grammar_audit_envelope["claim_license"]
        assert cl["task_surface"] == "voice_coherence"
        assert len(cl["licenses"]) > 80

    def test_rendered_header(self, _function_word_grammar_audit_envelope):
        assert _function_word_grammar_audit_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestFunctionWordGrammarAuditUnavailable:
    def test_empty_text(self):
        audit = fwg.audit_function_word_grammar("")
        _function_word_grammar_audit_envelope = fwg.build_audit_payload(
            audit,
            target_path=Path("empty.md"),
            baseline_block=None,
            baseline_comparison=None,
        )
        assert _function_word_grammar_audit_envelope["available"] is False
        assert _function_word_grammar_audit_envelope["claim_license"] is None
        assert _function_word_grammar_audit_envelope["warnings"]


class TestFunctionWordGrammarAuditBaseline:
    def test_baseline_block_populated(self):
        text = _function_word_grammar_audit_sample_text()
        audit = fwg.audit_function_word_grammar(text)
        baseline_block = {
            "n_files": 4, "n_words": 16000,
            "preposition_counts_summary": {"of": 200},
        }
        _function_word_grammar_audit_envelope = fwg.build_audit_payload(
            audit,
            target_path=Path("draft.md"),
            baseline_block=baseline_block,
            baseline_comparison={"available": True, "z_scores": {}},
        )
        assert _function_word_grammar_audit_envelope["baseline"]["n_files"] == 4
        assert _function_word_grammar_audit_envelope["baseline"]["words"] == 16000
        assert "preposition_counts_summary" in _function_word_grammar_audit_envelope["baseline"]
        assert _function_word_grammar_audit_envelope["results"]["baseline_comparison"]["available"] is True


# ===========================================================================
# idiolect_detector  (formerly test_idiolect_detector_schema.py)
# ===========================================================================


def _idiolect_detector_fake_result() -> dict:
    return {
        "task_surface": "voice_coherence",
        "privacy": idd.PRIVACY_WARNING,
        "target_summary": {
            "label": "target",
            "n_files": 5,
            "n_tokens": 12000,
            "files": [
                {"id": "t0", "path": "t0.md", "metadata": {}},
                {"id": "t1", "path": "t1.md", "metadata": {}},
            ],
        },
        "reference_summary": {
            "label": "reference",
            "n_files": 12,
            "n_tokens": 60000,
            "files": [],
        },
        "method": {
            "keyness": "log_likelihood",
            "n_values": [1, 2, 3],
            "smoothing_alpha": 0.5,
            "min_target_count": 3,
            "min_reference_count": 0,
            "min_total_count": 5,
        },
        "preprocessing": {
            "target": {"tokens_stripped": 50},
            "reference": {"tokens_stripped": 200},
        },
        "rankings": {
            "1": [
                {
                    "display": "afternoon",
                    "target_count": 12,
                    "reference_count": 3,
                    "target_per_1000": 1.0,
                    "reference_per_1000": 0.05,
                    "score": 8.5,
                },
            ],
        },
        "preservation_list": [
            {"display": "afternoon", "score": 8.5, "n": 1},
            {"display": "Daria signed", "score": 7.1, "n": 2},
        ],
    }


@pytest.fixture
def _idiolect_detector_envelope():
    return idd.build_audit_payload(
        _idiolect_detector_fake_result(),
        target_path=Path("target_dir/"),
        reference_path=Path("reference_dir/"),
    )


class TestIdiolectDetectorEnvelopeKeys:
    def test_required_keys(self, _idiolect_detector_envelope):
        assert set(_idiolect_detector_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _idiolect_detector_envelope):
        assert _idiolect_detector_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _idiolect_detector_envelope):
        assert _idiolect_detector_envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, _idiolect_detector_envelope):
        assert _idiolect_detector_envelope["tool"] == "idiolect_detector"
        assert _idiolect_detector_envelope["version"] == idd.SCRIPT_VERSION


class TestIdiolectDetectorTargetAndBaseline:
    def test_target_words_from_n_tokens(self, _idiolect_detector_envelope):
        assert _idiolect_detector_envelope["target"]["words"] == 12000

    def test_target_carries_privacy(self, _idiolect_detector_envelope):
        assert _idiolect_detector_envelope["target"]["privacy"] == idd.PRIVACY_WARNING

    def test_target_carries_n_files(self, _idiolect_detector_envelope):
        assert _idiolect_detector_envelope["target"]["n_files"] == 5

    def test_target_carries_preprocessing(self, _idiolect_detector_envelope):
        assert _idiolect_detector_envelope["target"]["preprocessing"]["tokens_stripped"] == 50

    def test_baseline_n_files_words(self, _idiolect_detector_envelope):
        assert _idiolect_detector_envelope["baseline"]["n_files"] == 12
        assert _idiolect_detector_envelope["baseline"]["words"] == 60000

    def test_baseline_carries_reference_preprocessing(self, _idiolect_detector_envelope):
        assert _idiolect_detector_envelope["baseline"]["preprocessing"]["tokens_stripped"] == 200

    def test_baseline_carries_path(self, _idiolect_detector_envelope):
        assert _idiolect_detector_envelope["baseline"]["path"].rstrip("/") == "reference_dir"


class TestIdiolectDetectorResultsPayload:
    def test_results_carries_method_rankings_preservation(self, _idiolect_detector_envelope):
        r = _idiolect_detector_envelope["results"]
        assert "method" in r
        assert "rankings" in r
        assert "preservation_list" in r

    def test_no_legacy_top_level_keys(self, _idiolect_detector_envelope):
        for legacy in (
            "target_summary", "reference_summary",
            "method", "rankings", "preservation_list",
            "privacy", "preprocessing",
        ):
            assert legacy not in _idiolect_detector_envelope


class TestIdiolectDetectorClaimLicense:
    def test_task_surface_matches(self, _idiolect_detector_envelope):
        assert (
            _idiolect_detector_envelope["claim_license"]["task_surface"]
            == _idiolect_detector_envelope["task_surface"]
        )

    def test_does_not_license_flags_voice_cloning(self, _idiolect_detector_envelope):
        # Preservation list is voice-cloning-grade input. The license
        # MUST flag this; pin to guard against accidental softening.
        text = _idiolect_detector_envelope["claim_license"]["does_not_license"].lower()
        assert "voice-cloning" in text or "private" in text

    def test_comparison_set_carries_corpus_summary(self, _idiolect_detector_envelope):
        cs = _idiolect_detector_envelope["claim_license"]["comparison_set"]
        assert cs["target_n_tokens"] == 12000
        assert cs["reference_n_tokens"] == 60000
        assert cs["n_preservation_entries"] == 2

    def test_rendered_header(self, _idiolect_detector_envelope):
        assert _idiolect_detector_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


# ===========================================================================
# kicker_density  (formerly test_kicker_density_schema.py)
# ===========================================================================


def _kicker_density_sample_text() -> str:
    return (
        "The committee deliberated through the afternoon. "
        "The room was warm. The decision came down to a single "
        "vote. Maria signed it. Time passed. The agenda continued. "
        "What matters is that the proposal moved.\n\n"
        "A second paragraph proceeds with care. There were "
        "concerns about scope. There were concerns about budget. "
        "Adjustment is hard.\n\n"
        "A third paragraph rests its case. Daria reviewed the "
        "details. Stakeholders deferred to the working group. "
        "The work was done.\n\n"
        "The dashboard now shows progress. Numbers do not lie."
    )


@pytest.fixture
def _kicker_density_envelope():
    text = _kicker_density_sample_text()
    block = kd.kicker_density(text, nlp=None)
    return kd.build_audit_payload(
        block,
        target_path=Path("draft.md"),
        text=text,
    )


class TestKickerDensityEnvelopeKeys:
    def test_required_keys(self, _kicker_density_envelope):
        assert set(_kicker_density_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _kicker_density_envelope):
        assert _kicker_density_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _kicker_density_envelope):
        assert _kicker_density_envelope["task_surface"] == "smoothing_diagnosis"

    def test_tool_and_version(self, _kicker_density_envelope):
        assert _kicker_density_envelope["tool"] == "kicker_density"
        assert _kicker_density_envelope["version"] == kd.SCRIPT_VERSION

    def test_target_words_counts_from_text(self, _kicker_density_envelope):
        assert _kicker_density_envelope["target"]["words"] > 0


class TestKickerDensityResultsPayload:
    def test_results_carries_legacy_block(self, _kicker_density_envelope):
        r = _kicker_density_envelope["results"]
        assert r["signal_path"] == "aic_8_9.kicker_density"
        assert r["family"] == "aic-9-closure-inflation"
        assert "value" in r
        assert "spacing_variance" in r
        assert "paragraphs" in r
        assert "diagnostics" in r

    def test_legacy_claim_license_tag_preserved(self, _kicker_density_envelope):
        # Function-call consumers (variance_audit) read this tag.
        assert _kicker_density_envelope["results"]["claim_license"] == "voice_diagnostic"

    def test_no_legacy_top_level_keys(self, _kicker_density_envelope):
        for legacy in (
            "signal_path", "family", "value", "spacing_variance",
            "polarity", "status", "paragraphs", "diagnostics",
        ):
            assert legacy not in _kicker_density_envelope


class TestKickerDensityClaimLicense:
    def test_structured_block_11_keys(self, _kicker_density_envelope):
        assert set(_kicker_density_envelope["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_task_surface_matches(self, _kicker_density_envelope):
        assert (
            _kicker_density_envelope["claim_license"]["task_surface"]
            == _kicker_density_envelope["task_surface"]
        )

    def test_comparison_set_carries_diagnostics(self, _kicker_density_envelope):
        cs = _kicker_density_envelope["claim_license"]["comparison_set"]
        assert "total_paragraphs" in cs
        assert "kicker_count" in cs
        assert "word_limit" in cs

    def test_rendered_header(self, _kicker_density_envelope):
        assert _kicker_density_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestKickerDensityFunctionContractStaysLegacy:
    def test_kicker_density_function_returns_legacy_block_shape(self):
        """variance_audit and aesthetic_authority_audit call
        kicker_density() as a function and read top-level
        `signal_path` / `family` / `value` keys. The migration must
        not change the function's return shape.
        """
        block = kd.kicker_density(_kicker_density_sample_text(), nlp=None)
        assert block["signal_path"] == "aic_8_9.kicker_density"
        assert block["family"] == "aic-9-closure-inflation"
        assert "value" in block
        assert block["task_surface"] == "smoothing_diagnosis"
        assert block["claim_license"] == "voice_diagnostic"


class TestKickerDensityBaselinePath:
    def test_function_baseline_comparison_lives_under_results(self):
        text = _kicker_density_sample_text()
        block = kd.kicker_density(
            text, nlp=None,
            baseline_value=0.10,
            baseline_source="test_register",
        )
        _kicker_density_envelope = kd.build_audit_payload(
            block, target_path=Path("draft.md"), text=text,
        )
        bc = _kicker_density_envelope["results"]["baseline_comparison"]
        assert bc["baseline_source"] == "test_register"
        assert bc["baseline_value"] == 0.10


# ===========================================================================
# known_editor_profile  (formerly test_known_editor_profile_schema.py)
# ===========================================================================


def _known_editor_profile_fake_match_report(verdict="matches_profile"):
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
def _known_editor_profile_envelope():
    return kep.build_audit_payload(
        _known_editor_profile_fake_match_report(),
        before_path=Path("before.md"),
        after_path=Path("after.md"),
    )


class TestKnownEditorProfileEnvelopeKeys:
    def test_required_keys(self, _known_editor_profile_envelope):
        assert set(_known_editor_profile_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _known_editor_profile_envelope):
        assert _known_editor_profile_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _known_editor_profile_envelope):
        assert _known_editor_profile_envelope["task_surface"] == "validation"

    def test_tool_and_version(self, _known_editor_profile_envelope):
        assert _known_editor_profile_envelope["tool"] == "known_editor_profile"
        assert _known_editor_profile_envelope["version"] == kep.SCRIPT_VERSION


class TestKnownEditorProfileResultsAndTarget:
    def test_results_carries_match_report(self, _known_editor_profile_envelope):
        r = _known_editor_profile_envelope["results"]
        assert r["verdict"] == "matches_profile"
        assert r["profile_label"] == "test_editor"
        assert "per_signal" in r

    def test_target_extra_carries_after_path(self, _known_editor_profile_envelope):
        assert "after_path" in _known_editor_profile_envelope["target"]

    def test_no_legacy_top_level_keys(self, _known_editor_profile_envelope):
        for legacy in (
            "profile_label", "profile_n_pairs", "z_threshold",
            "per_signal", "verdict",
        ):
            assert legacy not in _known_editor_profile_envelope


class TestKnownEditorProfileClaimLicense:
    def test_structured(self, _known_editor_profile_envelope):
        cs = _known_editor_profile_envelope["claim_license"]["comparison_set"]
        assert cs["verdict"] == "matches_profile"
        assert cs["profile_n_pairs"] == 5


# ===========================================================================
# manifest_validator  (formerly test_manifest_validator_schema.py)
# ===========================================================================


def _manifest_validator_fake_result(n_errors=0):
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
def _manifest_validator_envelope():
    return mv.build_audit_payload(
        _manifest_validator_fake_result(), target_path=Path("manifest.jsonl"),
    )


class TestManifestValidatorEnvelopeKeys:
    def test_required_keys(self, _manifest_validator_envelope):
        assert set(_manifest_validator_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _manifest_validator_envelope):
        assert _manifest_validator_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _manifest_validator_envelope):
        assert _manifest_validator_envelope["task_surface"] == "validation"

    def test_tool_and_version(self, _manifest_validator_envelope):
        assert _manifest_validator_envelope["tool"] == "manifest_validator"
        assert _manifest_validator_envelope["version"] == mv.SCRIPT_VERSION


class TestManifestValidatorResultsPayload:
    def test_results_carries_validation_data(self, _manifest_validator_envelope):
        r = _manifest_validator_envelope["results"]
        assert r["n_entries"] == 25
        assert r["n_warnings"] == 1
        assert "issues" in r
        assert "summary" in r

    def test_no_legacy_top_level_keys(self, _manifest_validator_envelope):
        for legacy in (
            "manifest_path", "n_entries", "n_errors", "n_warnings",
            "issues", "summary",
        ):
            assert legacy not in _manifest_validator_envelope


class TestManifestValidatorErrorWarning:
    def test_errors_emit_envelope_warning(self):
        env = mv.build_audit_payload(
            _manifest_validator_fake_result(n_errors=3), target_path=Path("m.jsonl"),
        )
        assert any("error" in w.lower() for w in env["warnings"])


class TestManifestValidatorClaimLicense:
    def test_structured(self, _manifest_validator_envelope):
        cs = _manifest_validator_envelope["claim_license"]["comparison_set"]
        assert cs["n_entries"] == 25
        assert cs["n_errors"] == 0


# ===========================================================================
# manuscript_audit  (formerly test_manuscript_audit_schema.py)
# ===========================================================================


def _manuscript_audit_fake_result(with_baseline=False):
    return {
        "task_surface": "smoothing_diagnosis",
        "preprocessing": {
            "chapters": {"opt_out": False, "tokens_stripped": 0},
            "baseline": {"opt_out": False, "tokens_stripped": 50} if with_baseline else None,
        },
        "n_chapters": 3,
        "n_baseline_files": 5 if with_baseline else 0,
        "chapters": [
            {"label": "Chapter 1", "n_words": 4000, "compression": {"band": "Lightly smoothed"}},
            {"label": "Chapter 2", "n_words": 4500, "compression": {"band": "Moderately smoothed"}},
            {"label": "Chapter 3", "n_words": 3800, "compression": {"band": "Lightly smoothed"}},
        ],
        "baseline_stats": (
            {"signal_summary": {"burstiness_B": {"mean": -0.1, "sd": 0.05, "n": 5}}}
            if with_baseline else None
        ),
    }


@pytest.fixture
def _manuscript_audit_envelope():
    return ma.build_audit_payload(
        _manuscript_audit_fake_result(), target_path=Path("manuscript.md"),
    )


class TestManuscriptAuditEnvelopeKeys:
    def test_required_keys(self, _manuscript_audit_envelope):
        assert set(_manuscript_audit_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _manuscript_audit_envelope):
        assert _manuscript_audit_envelope["schema_version"] == "1.0"

    def test_tool_and_version(self, _manuscript_audit_envelope):
        assert _manuscript_audit_envelope["tool"] == "manuscript_audit"
        assert _manuscript_audit_envelope["version"] == ma.SCRIPT_VERSION


class TestManuscriptAuditTargetAndBaseline:
    def test_target_words_sums_chapters(self, _manuscript_audit_envelope):
        assert _manuscript_audit_envelope["target"]["words"] == 12300

    def test_target_carries_n_chapters(self, _manuscript_audit_envelope):
        assert _manuscript_audit_envelope["target"]["n_chapters"] == 3

    def test_baseline_null_when_no_baseline(self, _manuscript_audit_envelope):
        assert _manuscript_audit_envelope["baseline"] is None

    def test_baseline_populated_when_supplied(self):
        env = ma.build_audit_payload(
            _manuscript_audit_fake_result(with_baseline=True),
            target_path=Path("m.md"),
        )
        assert env["baseline"] is not None
        assert env["baseline"]["n_files"] == 5


class TestManuscriptAuditResultsPayload:
    def test_results_carries_chapters_and_baseline_stats(self, _manuscript_audit_envelope):
        r = _manuscript_audit_envelope["results"]
        assert "chapters" in r
        assert len(r["chapters"]) == 3

    def test_no_legacy_top_level_keys(self, _manuscript_audit_envelope):
        for legacy in (
            "n_chapters", "n_baseline_files",
            "chapters", "baseline_stats", "preprocessing",
        ):
            assert legacy not in _manuscript_audit_envelope


class TestManuscriptAuditClaimLicense:
    def test_structured(self, _manuscript_audit_envelope):
        cl = _manuscript_audit_envelope["claim_license"]
        cs = cl["comparison_set"]
        assert cs["n_chapters"] == 3
        assert len(cl["licenses"]) > 80


# ===========================================================================
# manuscript_bigram_diff  (formerly test_manuscript_bigram_diff_schema.py)
# ===========================================================================


def _manuscript_bigram_diff_diff_rows():
    return [
        {"bigram": "NOUN_VERB", "kl_contrib": 0.05, "a_p": 0.10, "b_p": 0.06},
        {"bigram": "DET_NOUN", "kl_contrib": -0.03, "a_p": 0.08, "b_p": 0.11},
    ]


@pytest.fixture
def _manuscript_bigram_diff_envelope():
    json_str = mbd.render_json(
        a_label="hamilton",
        b_label="madison",
        a_loaded=[Path("a/file1.txt"), Path("a/file2.txt")],
        b_loaded=[Path("b/file1.txt"), Path("b/file2.txt"), Path("b/file3.txt")],
        a_skipped=[],
        b_skipped=[],
        pooled_diff=_manuscript_bigram_diff_diff_rows(),
        mean_diff=_manuscript_bigram_diff_diff_rows(),
        top=10, alpha=1.0, min_count=2,
    )
    return json.loads(json_str)


class TestManuscriptBigramDiffEnvelopeKeys:
    def test_required_keys(self, _manuscript_bigram_diff_envelope):
        assert set(_manuscript_bigram_diff_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _manuscript_bigram_diff_envelope):
        assert _manuscript_bigram_diff_envelope["schema_version"] == "1.0"

    def test_tool_and_version(self, _manuscript_bigram_diff_envelope):
        assert _manuscript_bigram_diff_envelope["tool"] == "manuscript_bigram_diff"
        assert _manuscript_bigram_diff_envelope["version"] == mbd.SCRIPT_VERSION


class TestManuscriptBigramDiffCorpusMapping:
    def test_corpus_a_to_target(self, _manuscript_bigram_diff_envelope):
        assert _manuscript_bigram_diff_envelope["target"]["label"] == "hamilton"
        assert _manuscript_bigram_diff_envelope["target"]["n_files"] == 2

    def test_corpus_b_to_baseline(self, _manuscript_bigram_diff_envelope):
        assert _manuscript_bigram_diff_envelope["baseline"]["label"] == "madison"
        assert _manuscript_bigram_diff_envelope["baseline"]["n_files"] == 3


class TestManuscriptBigramDiffResultsPayload:
    def test_results_carries_diffs_and_labels(self, _manuscript_bigram_diff_envelope):
        r = _manuscript_bigram_diff_envelope["results"]
        assert r["label_a"] == "hamilton"
        assert r["label_b"] == "madison"
        assert "pooled" in r["diffs"]
        assert "mean" in r["diffs"]

    def test_no_legacy_top_level_keys(self, _manuscript_bigram_diff_envelope):
        for legacy in (
            "label_a", "label_b",
            "corpus_a_files_loaded", "corpus_b_files_loaded",
            "corpus_a_files_skipped", "corpus_b_files_skipped",
            "smoothing_alpha", "min_count", "diffs",
        ):
            assert legacy not in _manuscript_bigram_diff_envelope


class TestManuscriptBigramDiffClaimLicense:
    def test_structured(self, _manuscript_bigram_diff_envelope):
        cs = _manuscript_bigram_diff_envelope["claim_license"]["comparison_set"]
        assert cs["label_a"] == "hamilton"
        assert cs["label_b"] == "madison"
        assert cs["n_corpus_a_files"] == 2
        assert cs["n_corpus_b_files"] == 3


class TestManuscriptBigramDiffSkippedFiles:
    def test_corpus_a_skipped_emits_warning(self):
        json_str = mbd.render_json(
            a_label="A", b_label="B",
            a_loaded=[Path("a/x.txt")],
            b_loaded=[Path("b/y.txt")],
            a_skipped=[Path("a/bad.txt")],
            b_skipped=[],
            pooled_diff=_manuscript_bigram_diff_diff_rows(),
            mean_diff=None,
            top=10, alpha=1.0, min_count=1,
        )
        env = json.loads(json_str)
        assert any("corpus-A" in w for w in env["warnings"])


# ===========================================================================
# manuscript_repetition_audit  (formerly test_manuscript_repetition_audit_schema.py)
# ===========================================================================


def _manuscript_repetition_audit_fake_result(skipped=False):
    return {
        "task_surface": "smoothing_diagnosis",
        "n_chapters": 4,
        "n_baseline_files": 8,
        "n_baseline_files_skipped": 1 if skipped else 0,
        "baseline_files_loaded": [Path(f"baseline/file_{i}.md") for i in range(8)],
        "baseline_files_skipped": [Path("baseline/locked.md")] if skipped else [],
        "baseline_words": 40000,
        "total_target_words": 22000,
        "chapters": [
            {"label": "Chapter 1", "n_target_words": 5500, "candidates": []},
            {"label": "Chapter 2", "n_target_words": 5500, "candidates": []},
            {"label": "Chapter 3", "n_target_words": 5500, "candidates": []},
            {"label": "Chapter 4", "n_target_words": 5500, "candidates": []},
        ],
        "aggregated": [
            {"word": "forge", "n_chapters": 3, "median_ratio": 4.5},
        ],
    }


@pytest.fixture
def _manuscript_repetition_audit_envelope():
    return mra.build_audit_payload(
        _manuscript_repetition_audit_fake_result(), target_path=Path("manuscript.md"),
    )


class TestManuscriptRepetitionAuditEnvelopeKeys:
    def test_required_keys(self, _manuscript_repetition_audit_envelope):
        assert set(_manuscript_repetition_audit_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _manuscript_repetition_audit_envelope):
        assert _manuscript_repetition_audit_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _manuscript_repetition_audit_envelope):
        assert _manuscript_repetition_audit_envelope["task_surface"] == "smoothing_diagnosis"

    def test_tool_and_version(self, _manuscript_repetition_audit_envelope):
        assert _manuscript_repetition_audit_envelope["tool"] == "manuscript_repetition_audit"
        assert _manuscript_repetition_audit_envelope["version"] == mra.SCRIPT_VERSION


class TestManuscriptRepetitionAuditTargetAndBaseline:
    def test_target_words_sums_chapter_words(self, _manuscript_repetition_audit_envelope):
        assert _manuscript_repetition_audit_envelope["target"]["words"] == 22000

    def test_target_carries_n_chapters(self, _manuscript_repetition_audit_envelope):
        assert _manuscript_repetition_audit_envelope["target"]["n_chapters"] == 4

    def test_baseline_block_populated(self, _manuscript_repetition_audit_envelope):
        assert _manuscript_repetition_audit_envelope["baseline"]["n_files"] == 8
        assert _manuscript_repetition_audit_envelope["baseline"]["words"] == 40000


class TestManuscriptRepetitionAuditResultsPayload:
    def test_results_carries_chapters_and_aggregated(self, _manuscript_repetition_audit_envelope):
        r = _manuscript_repetition_audit_envelope["results"]
        assert r["n_chapters"] == 4
        assert len(r["chapters"]) == 4
        assert len(r["aggregated"]) == 1

    def test_no_legacy_top_level_keys(self, _manuscript_repetition_audit_envelope):
        for legacy in (
            "n_chapters", "n_baseline_files", "baseline_words",
            "total_target_words", "chapters", "aggregated",
            "baseline_files_loaded", "baseline_files_skipped",
        ):
            assert legacy not in _manuscript_repetition_audit_envelope


class TestManuscriptRepetitionAuditClaimLicense:
    def test_structured(self, _manuscript_repetition_audit_envelope):
        cs = _manuscript_repetition_audit_envelope["claim_license"]["comparison_set"]
        assert cs["n_chapters"] == 4
        assert cs["n_baseline_files"] == 8


class TestManuscriptRepetitionAuditSkippedFilesWarning:
    def test_skipped_files_produce_warning(self):
        env = mra.build_audit_payload(
            _manuscript_repetition_audit_fake_result(skipped=True), target_path=Path("m.md"),
        )
        assert env["warnings"]
        assert any("skipped" in w.lower() for w in env["warnings"])


# ===========================================================================
# mimicry_cosplay_audit  (formerly test_mimicry_cosplay_audit_schema.py)
# ===========================================================================


def _mimicry_cosplay_audit_sample_text() -> str:
    return (
        "The committee deliberated. The proposal landed on Tuesday. "
        "Daria reviewed the budget. The room remained quiet."
    ) * 4


def _mimicry_cosplay_audit_idiolect() -> dict:
    return {
        "preservation_list": [
            {"phrase": "the committee", "score": 1.2},
            {"phrase": "landed on Tuesday", "score": 1.1},
        ],
    }


@pytest.fixture
def _mimicry_cosplay_audit_envelope():
    text = _mimicry_cosplay_audit_sample_text()
    audit = mca.audit_cosplay(
        target_text=text,
        idiolect=_mimicry_cosplay_audit_idiolect(),
        voice_distance=None,
        variance=None,
    )
    return mca.build_audit_payload(
        audit, target_path=Path("draft.md"), target_text=text,
    )


class TestMimicryCosplayAuditEnvelopeKeys:
    def test_required_keys(self, _mimicry_cosplay_audit_envelope):
        assert set(_mimicry_cosplay_audit_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _mimicry_cosplay_audit_envelope):
        assert _mimicry_cosplay_audit_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _mimicry_cosplay_audit_envelope):
        assert _mimicry_cosplay_audit_envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, _mimicry_cosplay_audit_envelope):
        assert _mimicry_cosplay_audit_envelope["tool"] == "mimicry_cosplay_audit"
        assert _mimicry_cosplay_audit_envelope["version"] == mca.SCRIPT_VERSION


class TestMimicryCosplayAuditResultsPayload:
    def test_results_carries_audit_signals(self, _mimicry_cosplay_audit_envelope):
        r = _mimicry_cosplay_audit_envelope["results"]
        for k in (
            "idiolect_survival", "voice_distance",
            "pos_bigram_kl", "verdict", "shapes", "thresholds_used",
        ):
            assert k in r, f"missing results key: {k}"

    def test_no_legacy_top_level_keys(self, _mimicry_cosplay_audit_envelope):
        for legacy in (
            "idiolect_survival", "voice_distance",
            "verdict", "shapes", "thresholds_used",
        ):
            assert legacy not in _mimicry_cosplay_audit_envelope


class TestMimicryCosplayAuditClaimLicense:
    def test_structured_block_11_keys(self, _mimicry_cosplay_audit_envelope):
        assert set(_mimicry_cosplay_audit_envelope["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_task_surface_matches(self, _mimicry_cosplay_audit_envelope):
        assert (
            _mimicry_cosplay_audit_envelope["claim_license"]["task_surface"]
            == _mimicry_cosplay_audit_envelope["task_surface"]
        )

    def test_comparison_set_carries_verdict(self, _mimicry_cosplay_audit_envelope):
        cs = _mimicry_cosplay_audit_envelope["claim_license"]["comparison_set"]
        assert "verdict" in cs

    def test_rendered_header(self, _mimicry_cosplay_audit_envelope):
        assert _mimicry_cosplay_audit_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestMimicryCosplayAuditAiStatusRouting:
    def test_state_routed_caveats_added(self):
        text = _mimicry_cosplay_audit_sample_text()
        audit = mca.audit_cosplay(
            target_text=text,
            idiolect=_mimicry_cosplay_audit_idiolect(),
            voice_distance=None,
            variance=None,
            target_ai_status="ai_generated_from_outline",
        )
        _mimicry_cosplay_audit_envelope = mca.build_audit_payload(
            audit, target_path=Path("draft.md"), target_text=text,
        )
        assert _mimicry_cosplay_audit_envelope["ai_status"] == "ai_generated_from_outline"
        caveats = _mimicry_cosplay_audit_envelope["claim_license"]["additional_caveats"]
        assert any("outline" in c.lower() for c in caveats)


# ===========================================================================
# paragraph_audit  (formerly test_paragraph_audit_schema.py)
# ===========================================================================


_PARAGRAPH_AUDIT_PROSE = (
    "The committee deliberated.\n\n"
    "Members reviewed the budget. The proposal landed on Tuesday. "
    "Daria signed off after lunch.\n\n"
    "The room was warm. Quiet. Patient.\n\n"
    "By the end of the afternoon, three workstreams advanced and "
    "two stalled, with one waiting on legal review and another "
    "needing a vendor decision.\n\n"
    "Onward.\n\n"
) * 4


@pytest.fixture
def _paragraph_audit_envelope():
    audit = pa.audit_paragraphs(_PARAGRAPH_AUDIT_PROSE)
    return pa.build_audit_payload(
        audit,
        target_path=Path("draft.md"),
        baseline_block=None,
        baseline_comparison=None,
    )


class TestParagraphAuditEnvelopeKeys:
    def test_required_keys(self, _paragraph_audit_envelope):
        assert set(_paragraph_audit_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _paragraph_audit_envelope):
        assert _paragraph_audit_envelope["schema_version"] == "1.0"

    def test_tool_and_version(self, _paragraph_audit_envelope):
        assert _paragraph_audit_envelope["tool"] == "paragraph_audit"
        assert _paragraph_audit_envelope["version"] == pa.SCRIPT_VERSION


class TestParagraphAuditResultsPayload:
    def test_results_carries_rhythm_signals(self, _paragraph_audit_envelope):
        r = _paragraph_audit_envelope["results"]
        for k in (
            "n_paragraphs", "paragraph_word_counts",
            "length_summary", "rhythm_signals", "compression",
        ):
            assert k in r

    def test_no_legacy_top_level_keys(self, _paragraph_audit_envelope):
        for legacy in (
            "n_paragraphs", "paragraph_word_counts",
            "length_summary", "rhythm_signals", "compression",
        ):
            assert legacy not in _paragraph_audit_envelope


class TestParagraphAuditUnavailable:
    def test_empty_text(self):
        audit = pa.audit_paragraphs("")
        _paragraph_audit_envelope = pa.build_audit_payload(
            audit,
            target_path=Path("empty.md"),
            baseline_block=None,
            baseline_comparison=None,
        )
        assert _paragraph_audit_envelope["available"] is False
        assert _paragraph_audit_envelope["claim_license"] is None


class TestParagraphAuditClaimLicense:
    def test_structured(self, _paragraph_audit_envelope):
        assert _paragraph_audit_envelope["claim_license"]["task_surface"] == "smoothing_diagnosis"

    def test_rendered_header(self, _paragraph_audit_envelope):
        assert _paragraph_audit_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


# ===========================================================================
# phraseological_signature_audit  (formerly test_phraseological_signature_audit_schema.py)
# ===========================================================================


def _phraseological_signature_audit_target() -> str:
    return (
        "It seems to me that, on the one hand, the prose is fine. "
        "On the other hand, it could improve. The bottom line is "
        "that voice matters. In other words, frame reuse is the "
        "writer's voice. By and large, the work holds together."
    ) * 4


@pytest.fixture
def _phraseological_signature_audit_envelope():
    audit = psa.audit_phraseology(target_text=_phraseological_signature_audit_target())
    return psa.build_audit_payload(
        audit, target_path=Path("draft.md"), baseline_dir=None,
    )


class TestPhraseologicalSignatureAuditEnvelopeKeys:
    def test_required_keys(self, _phraseological_signature_audit_envelope):
        assert set(_phraseological_signature_audit_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _phraseological_signature_audit_envelope):
        assert _phraseological_signature_audit_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _phraseological_signature_audit_envelope):
        assert _phraseological_signature_audit_envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, _phraseological_signature_audit_envelope):
        assert _phraseological_signature_audit_envelope["tool"] == "phraseological_signature_audit"
        assert _phraseological_signature_audit_envelope["version"] == psa.SCRIPT_VERSION


class TestPhraseologicalSignatureAuditResultsPayload:
    def test_results_carries_categories(self, _phraseological_signature_audit_envelope):
        assert "categories" in _phraseological_signature_audit_envelope["results"]
        cats = _phraseological_signature_audit_envelope["results"]["categories"]
        assert isinstance(cats, dict)

    def test_no_legacy_top_level_keys(self, _phraseological_signature_audit_envelope):
        for legacy in (
            "categories", "target_words", "baseline_words",
            "n_baseline_files",
        ):
            assert legacy not in _phraseological_signature_audit_envelope


class TestPhraseologicalSignatureAuditClaimLicense:
    def test_structured_block_11_keys(self, _phraseological_signature_audit_envelope):
        """The pre-migration audit dict carried claim_license as
        `{"rendered": "...markdown..."}`. Post-migration the _phraseological_signature_audit_envelope
        carries the full structured 11-key ClaimLicense.to_dict()
        shape, with the rendered markdown lifted out to
        claim_license_rendered.
        """
        assert set(_phraseological_signature_audit_envelope["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_task_surface_matches(self, _phraseological_signature_audit_envelope):
        assert (
            _phraseological_signature_audit_envelope["claim_license"]["task_surface"]
            == _phraseological_signature_audit_envelope["task_surface"]
        )

    def test_comparison_set_has_word_counts(self, _phraseological_signature_audit_envelope):
        cs = _phraseological_signature_audit_envelope["claim_license"]["comparison_set"]
        assert "target_words" in cs
        assert "baseline_words" in cs
        assert "n_categories_active" in cs

    def test_rendered_block_starts_with_header(self, _phraseological_signature_audit_envelope):
        assert _phraseological_signature_audit_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestPhraseologicalSignatureAuditBaseline:
    def test_baseline_block_when_path_supplied(self):
        text = _phraseological_signature_audit_target()
        baseline_a = "On the one hand, the data. " * 5
        baseline_b = "By and large, the patterns hold. " * 5
        audit = psa.audit_phraseology(
            target_text=text,
            baseline_texts=[baseline_a, baseline_b],
        )
        _phraseological_signature_audit_envelope = psa.build_audit_payload(
            audit,
            target_path=Path("draft.md"),
            baseline_dir=Path("baseline/"),
        )
        assert _phraseological_signature_audit_envelope["baseline"] is not None
        assert _phraseological_signature_audit_envelope["baseline"]["n_files"] == 2
        assert _phraseological_signature_audit_envelope["baseline"]["words"] > 0
        # pathlib normalizes the trailing slash; compare on prefix.
        assert _phraseological_signature_audit_envelope["baseline"]["path"].rstrip("/") == "baseline"

    def test_baseline_null_when_no_baseline(self, _phraseological_signature_audit_envelope):
        assert _phraseological_signature_audit_envelope["baseline"] is None


# ===========================================================================
# pov_voice_profile  (formerly test_pov_voice_profile_schema.py)
# ===========================================================================


def _pov_voice_profile_fake_render_inputs():
    from pov_voice_profile import POVProfile

    profiles = {
        "Hamilton": POVProfile(
            label="Hamilton", n_docs=5, n_words=18000,
            feature_items=[],
            pov_centroids={"function_words": {"the": 0.058}},
        ),
        "Madison": POVProfile(
            label="Madison", n_docs=4, n_words=15000,
            feature_items=[],
            pov_centroids={"function_words": {"the": 0.062}},
        ),
    }
    family_distances = {
        "function_words": {
            ("Hamilton", "Madison"): {
                "burrows_delta": 1.4,
                "cosine_distance": 0.08,
            },
        },
    }
    weighted_distances = {
        ("Hamilton", "Madison"): {
            "burrows_delta": 1.4,
            "cosine_distance": 0.08,
        },
    }
    pov_vs_mean = {
        "Hamilton": {"burrows_delta": 0.8, "cosine_distance": 0.05},
        "Madison": {"burrows_delta": 0.7, "cosine_distance": 0.04},
    }
    distinguishing = {
        "Hamilton": {"function_words": [{"feature": "establish", "z": 1.2}]},
        "Madison": {"function_words": [{"feature": "republic", "z": 1.4}]},
    }
    collapse_verdict = [
        {
            "pov_a": "Hamilton", "pov_b": "Madison",
            "verdict": "distinct", "burrows_delta": 1.4,
        },
    ]
    return {
        "profiles": profiles,
        "family_distances": family_distances,
        "weighted_distances": weighted_distances,
        "pov_vs_mean": pov_vs_mean,
        "distinguishing": distinguishing,
        "collapse_verdict": collapse_verdict,
        "dropped_povs": [],
        "inputs": {
            "manifest": "manifest.jsonl",
            "min_words_per_pov": 5000,
        },
    }


@pytest.fixture
def _pov_voice_profile_envelope():
    return json.loads(pvp.render_json(**_pov_voice_profile_fake_render_inputs()))


class TestPovVoiceProfileEnvelopeKeys:
    def test_required_keys(self, _pov_voice_profile_envelope):
        assert set(_pov_voice_profile_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _pov_voice_profile_envelope):
        assert _pov_voice_profile_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _pov_voice_profile_envelope):
        assert _pov_voice_profile_envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, _pov_voice_profile_envelope):
        assert _pov_voice_profile_envelope["tool"] == "pov_voice_profile"
        assert _pov_voice_profile_envelope["version"] == pvp.SCRIPT_VERSION


class TestPovVoiceProfileTargetAndBaseline:
    def test_target_words_sums_pov_words(self, _pov_voice_profile_envelope):
        # 18000 + 15000
        assert _pov_voice_profile_envelope["target"]["words"] == 33000

    def test_baseline_is_null(self, _pov_voice_profile_envelope):
        """The corpus IS the target; there's no separate baseline."""
        assert _pov_voice_profile_envelope["baseline"] is None


class TestPovVoiceProfileResultsPayload:
    def test_results_carries_pov_data(self, _pov_voice_profile_envelope):
        r = _pov_voice_profile_envelope["results"]
        for k in (
            "n_povs", "povs", "inputs", "dropped_povs",
            "cross_pov_distances_per_family",
            "cross_pov_distances_weighted",
            "pov_vs_corpus_mean", "distinguishing_features",
            "voice_collapse_verdict",
        ):
            assert k in r, f"missing results key: {k}"

    def test_n_povs(self, _pov_voice_profile_envelope):
        assert _pov_voice_profile_envelope["results"]["n_povs"] == 2

    def test_no_legacy_top_level_keys(self, _pov_voice_profile_envelope):
        for legacy in (
            "n_povs", "povs", "inputs", "dropped_povs",
            "cross_pov_distances_per_family",
            "cross_pov_distances_weighted",
            "pov_vs_corpus_mean", "distinguishing_features",
            "voice_collapse_verdict",
        ):
            assert legacy not in _pov_voice_profile_envelope


class TestPovVoiceProfileClaimLicense:
    def test_structured_block_11_keys(self, _pov_voice_profile_envelope):
        """Pre-1.85 emitted claim_license as the legacy 2-key dict.
        Post-1.85 emits the full 11-key ClaimLicense.to_dict().
        """
        assert set(_pov_voice_profile_envelope["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_legacy_text_preserved(self, _pov_voice_profile_envelope):
        assert (
            _pov_voice_profile_envelope["claim_license"]["licenses"]
            == pvp.CLAIM_LICENSE["licenses"]
        )
        assert (
            _pov_voice_profile_envelope["claim_license"]["does_not_license"]
            == pvp.CLAIM_LICENSE["does_not_license"]
        )

    def test_comparison_set_carries_povs(self, _pov_voice_profile_envelope):
        cs = _pov_voice_profile_envelope["claim_license"]["comparison_set"]
        assert cs["n_povs"] == 2
        assert "Hamilton" in cs["pov_labels"]
        assert cs["n_docs_per_pov"]["Hamilton"] == 5
        assert cs["n_collapse_flags"] == 0

    def test_rendered_header(self, _pov_voice_profile_envelope):
        assert _pov_voice_profile_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestPovVoiceProfileCollapseSurfacing:
    def test_collapse_flag_counted(self):
        inputs = _pov_voice_profile_fake_render_inputs()
        inputs["collapse_verdict"] = [
            {
                "pov_a": "A", "pov_b": "B",
                "verdict": "potentially_collapsed", "burrows_delta": 0.3,
            },
        ]
        _pov_voice_profile_envelope = json.loads(pvp.render_json(**inputs))
        cs = _pov_voice_profile_envelope["claim_license"]["comparison_set"]
        assert cs["n_collapse_flags"] == 1


# Markdown rendering uses a richer distinguishing-features dict
# shape than the synthetic _pov_voice_profile_fake_render_inputs() above; the
# pre-existing tests in test_pov_voice_profile.py
# (test_markdown_output_includes_distance_table) cover that path
# end-to-end via a real Federalist-corpus run.


# ===========================================================================
# punctuation_cadence_audit  (formerly test_punctuation_cadence_audit_schema.py)
# ===========================================================================


def _punctuation_cadence_audit_sample_text() -> str:
    return (
        "The committee — meeting briefly — endorsed the proposal. "
        "Members reviewed the timeline; some objected. The room "
        "(crowded, warm) deliberated. Daria, speaking for the "
        "working group, summarized the concerns. \"What now?\" "
        "asked the chair. She paused. The budget, contested, "
        "shifted again. The deadline holds. To be clear: scope "
        "matters. Did the team consider alternatives? They did, "
        "twice. The vote passed."
    ) * 5


@pytest.fixture
def _punctuation_cadence_audit_envelope():
    text = _punctuation_cadence_audit_sample_text()
    audit = pca.audit_punctuation_cadence(text)
    return pca.build_audit_payload(
        audit,
        target_path=Path("draft.md"),
        baseline_block=None,
        baseline_comparison=None,
    )


class TestPunctuationCadenceAuditEnvelopeKeys:
    def test_required_keys(self, _punctuation_cadence_audit_envelope):
        assert set(_punctuation_cadence_audit_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _punctuation_cadence_audit_envelope):
        assert _punctuation_cadence_audit_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _punctuation_cadence_audit_envelope):
        assert _punctuation_cadence_audit_envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, _punctuation_cadence_audit_envelope):
        assert _punctuation_cadence_audit_envelope["tool"] == "punctuation_cadence_audit"
        assert _punctuation_cadence_audit_envelope["version"] == pca.SCRIPT_VERSION


class TestPunctuationCadenceAuditResultsPayload:
    def test_results_carries_audit_signals(self, _punctuation_cadence_audit_envelope):
        r = _punctuation_cadence_audit_envelope["results"]
        for k in (
            "n_sentence_final", "raw_counts", "densities_per_1k",
            "sentence_final_distribution", "interruption_grammar",
            "punctuation_bigrams", "comma_period_share", "compression",
        ):
            assert k in r, f"missing results key: {k}"

    def test_no_legacy_top_level_keys(self, _punctuation_cadence_audit_envelope):
        for legacy in (
            "n_words", "n_sentence_final", "raw_counts",
            "densities_per_1k", "sentence_final_distribution",
            "interruption_grammar", "punctuation_bigrams",
            "comma_period_share", "compression",
            "baseline_block", "baseline_comparison",
        ):
            assert legacy not in _punctuation_cadence_audit_envelope


class TestPunctuationCadenceAuditClaimLicense:
    def test_structured_block(self, _punctuation_cadence_audit_envelope):
        cl = _punctuation_cadence_audit_envelope["claim_license"]
        assert cl["task_surface"] == "voice_coherence"
        assert len(cl["licenses"]) > 80

    def test_rendered_header(self, _punctuation_cadence_audit_envelope):
        assert _punctuation_cadence_audit_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestPunctuationCadenceAuditAiStatusRouting:
    def test_state_routed_caveats_added(self):
        text = _punctuation_cadence_audit_sample_text()
        audit = pca.audit_punctuation_cadence(text)
        audit["ai_status"] = "pre_ai_human"
        _punctuation_cadence_audit_envelope = pca.build_audit_payload(
            audit,
            target_path=Path("draft.md"),
            baseline_block=None,
            baseline_comparison=None,
        )
        assert _punctuation_cadence_audit_envelope["ai_status"] == "pre_ai_human"
        caveats = _punctuation_cadence_audit_envelope["claim_license"]["additional_caveats"]
        assert any("pre-AI" in c or "pre_ai" in c for c in caveats)


class TestPunctuationCadenceAuditUnavailable:
    def test_empty_text(self):
        audit = pca.audit_punctuation_cadence("")
        _punctuation_cadence_audit_envelope = pca.build_audit_payload(
            audit,
            target_path=Path("empty.md"),
            baseline_block=None,
            baseline_comparison=None,
        )
        assert _punctuation_cadence_audit_envelope["available"] is False
        assert _punctuation_cadence_audit_envelope["claim_license"] is None
        assert _punctuation_cadence_audit_envelope["warnings"]


class TestPunctuationCadenceAuditBaseline:
    def test_baseline_block_populated(self):
        text = _punctuation_cadence_audit_sample_text()
        audit = pca.audit_punctuation_cadence(text)
        baseline_block = {
            "n_files": 6, "n_words": 30000,
            "comma_period_share_summary": {"mean": 0.65},
        }
        _punctuation_cadence_audit_envelope = pca.build_audit_payload(
            audit,
            target_path=Path("draft.md"),
            baseline_block=baseline_block,
            baseline_comparison={"available": True, "z_scores": {}},
        )
        assert _punctuation_cadence_audit_envelope["baseline"]["n_files"] == 6
        assert _punctuation_cadence_audit_envelope["baseline"]["words"] == 30000
        assert "comma_period_share_summary" in _punctuation_cadence_audit_envelope["baseline"]


# ===========================================================================
# repetition_audit  (formerly test_repetition_audit_schema.py)
# ===========================================================================


def _repetition_audit_candidates():
    return [
        {"word": "forge", "count": 12, "per_1000": 4.0, "baseline_per_1000": 0.5, "ratio": 8.0, "cluster_max": 4},
        {"word": "hammer", "count": 8, "per_1000": 2.7, "baseline_per_1000": 0.3, "ratio": 9.0, "cluster_max": 3},
    ]


@pytest.fixture
def _repetition_audit_envelope():
    return ra.build_audit_payload(
        target_path=Path("draft.txt"),
        target_words=3000,
        candidates=_repetition_audit_candidates(),
        baseline_files_loaded=[Path("a.md"), Path("b.md")],
        baseline_files_skipped=[],
        baseline_tokens=15000,
    )


class TestRepetitionAuditEnvelopeKeys:
    def test_required_keys(self, _repetition_audit_envelope):
        assert set(_repetition_audit_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _repetition_audit_envelope):
        assert _repetition_audit_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _repetition_audit_envelope):
        assert _repetition_audit_envelope["task_surface"] == "smoothing_diagnosis"

    def test_tool_and_version(self, _repetition_audit_envelope):
        assert _repetition_audit_envelope["tool"] == "repetition_audit"
        assert _repetition_audit_envelope["version"] == ra.SCRIPT_VERSION


class TestRepetitionAuditTargetAndBaseline:
    def test_target_words(self, _repetition_audit_envelope):
        assert _repetition_audit_envelope["target"]["words"] == 3000

    def test_baseline_n_files_words(self, _repetition_audit_envelope):
        assert _repetition_audit_envelope["baseline"]["n_files"] == 2
        assert _repetition_audit_envelope["baseline"]["words"] == 15000

    def test_baseline_files_loaded(self, _repetition_audit_envelope):
        assert _repetition_audit_envelope["baseline"]["files_loaded"] == ["a.md", "b.md"]


class TestRepetitionAuditResultsPayload:
    def test_results_carries_candidates(self, _repetition_audit_envelope):
        assert _repetition_audit_envelope["results"]["candidates"] == _repetition_audit_candidates()

    def test_no_legacy_top_level_keys(self, _repetition_audit_envelope):
        for legacy in (
            "target", "target_words", "candidates",
            "baseline_files_loaded", "baseline_files_skipped",
            "baseline_tokens",
        ):
            # `target` is now an _repetition_audit_envelope key (dict), but as a string
            # key with the legacy file-path value it must not appear.
            if legacy == "target":
                assert not isinstance(_repetition_audit_envelope["target"], str)
                continue
            assert legacy not in _repetition_audit_envelope


class TestRepetitionAuditClaimLicense:
    def test_structured(self, _repetition_audit_envelope):
        assert _repetition_audit_envelope["claim_license"]["task_surface"] == "smoothing_diagnosis"
        assert len(_repetition_audit_envelope["claim_license"]["licenses"]) > 80

    def test_rendered_header(self, _repetition_audit_envelope):
        assert _repetition_audit_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )

    def test_comparison_set_carries_word_counts(self, _repetition_audit_envelope):
        cs = _repetition_audit_envelope["claim_license"]["comparison_set"]
        assert cs["target_words"] == 3000
        assert cs["baseline_tokens"] == 15000
        assert cs["n_candidates"] == 2


class TestRepetitionAuditSkippedFilesWarning:
    def test_skipped_files_produce_warning(self):
        env = ra.build_audit_payload(
            target_path=Path("draft.txt"),
            target_words=3000,
            candidates=_repetition_audit_candidates(),
            baseline_files_loaded=[Path("a.md")],
            baseline_files_skipped=[Path("bad.pdf"), Path("locked.md")],
            baseline_tokens=12000,
        )
        assert env["warnings"]
        assert any("skipped" in w.lower() for w in env["warnings"])
        # Baseline must record both loaded and skipped lists.
        assert env["baseline"]["files_skipped"] == ["bad.pdf", "locked.md"]


# ===========================================================================
# stance_modality_audit  (formerly test_stance_modality_audit_schema.py)
# ===========================================================================


def _stance_modality_audit_sample_text() -> str:
    return (
        "The committee may consider the proposal. Members must "
        "review the timeline. Clearly the budget is constrained. "
        "Possibly the deadline could be extended. We believe the "
        "team can deliver. It seems reasonable to defer. Critics "
        "argue otherwise. We urge caution. The evidence shows the "
        "approach is viable. To be honest, the timeline is tight."
    ) * 6


@pytest.fixture
def _stance_modality_audit_envelope():
    text = _stance_modality_audit_sample_text()
    audit = sma.audit_stance_modality(text)
    return sma.build_audit_payload(
        audit,
        target_path=Path("draft.md"),
        baseline_block=None,
        baseline_comparison=None,
    )


class TestStanceModalityAuditEnvelopeKeys:
    def test_required_keys(self, _stance_modality_audit_envelope):
        assert set(_stance_modality_audit_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _stance_modality_audit_envelope):
        assert _stance_modality_audit_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _stance_modality_audit_envelope):
        assert _stance_modality_audit_envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, _stance_modality_audit_envelope):
        assert _stance_modality_audit_envelope["tool"] == "stance_modality_audit"
        assert _stance_modality_audit_envelope["version"] == sma.SCRIPT_VERSION


class TestStanceModalityAuditResultsPayload:
    def test_results_carries_audit_signals(self, _stance_modality_audit_envelope):
        r = _stance_modality_audit_envelope["results"]
        for k in (
            "category_counts", "category_densities_per_1k",
            "total_marker_density_per_1k",
            "stance_entropy_bits", "hedge_booster_ratio",
            "compression",
        ):
            assert k in r, f"missing results key: {k}"

    def test_no_legacy_top_level_keys(self, _stance_modality_audit_envelope):
        for legacy in (
            "n_words", "category_counts", "category_densities_per_1k",
            "total_marker_density_per_1k", "stance_entropy_bits",
            "hedge_booster_ratio", "compression",
            "baseline_block", "baseline_comparison",
        ):
            assert legacy not in _stance_modality_audit_envelope


class TestStanceModalityAuditClaimLicense:
    def test_structured_block_present(self, _stance_modality_audit_envelope):
        cl = _stance_modality_audit_envelope["claim_license"]
        assert cl["task_surface"] == "voice_coherence"
        assert len(cl["licenses"]) > 80
        assert len(cl["does_not_license"]) > 80

    def test_rendered_header(self, _stance_modality_audit_envelope):
        assert _stance_modality_audit_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestStanceModalityAuditAiStatusRouting:
    def test_state_routed_caveats_added(self):
        text = _stance_modality_audit_sample_text()
        audit = sma.audit_stance_modality(text)
        audit["ai_status"] = "ai_generated_from_outline"
        _stance_modality_audit_envelope = sma.build_audit_payload(
            audit,
            target_path=Path("draft.md"),
            baseline_block=None,
            baseline_comparison=None,
        )
        assert _stance_modality_audit_envelope["ai_status"] == "ai_generated_from_outline"
        caveats = _stance_modality_audit_envelope["claim_license"]["additional_caveats"]
        assert any("outline" in c.lower() for c in caveats)


class TestStanceModalityAuditUnavailable:
    def test_empty_text(self):
        audit = sma.audit_stance_modality("")
        _stance_modality_audit_envelope = sma.build_audit_payload(
            audit,
            target_path=Path("empty.md"),
            baseline_block=None,
            baseline_comparison=None,
        )
        assert _stance_modality_audit_envelope["available"] is False
        assert _stance_modality_audit_envelope["claim_license"] is None
        assert _stance_modality_audit_envelope["warnings"]


class TestStanceModalityAuditBaseline:
    def test_baseline_block_populated(self):
        text = _stance_modality_audit_sample_text()
        audit = sma.audit_stance_modality(text)
        baseline_block = {
            "n_files": 5, "n_words": 25000,
            "per_category_summary": {"hedge": 8.0},
        }
        _stance_modality_audit_envelope = sma.build_audit_payload(
            audit,
            target_path=Path("draft.md"),
            baseline_block=baseline_block,
            baseline_comparison={"available": True, "z_scores": {}},
        )
        assert _stance_modality_audit_envelope["baseline"]["n_files"] == 5
        assert _stance_modality_audit_envelope["baseline"]["words"] == 25000
        assert "per_category_summary" in _stance_modality_audit_envelope["baseline"]
        assert _stance_modality_audit_envelope["results"]["baseline_comparison"]["available"] is True


# ===========================================================================
# surprisal_audit  (formerly test_surprisal_audit_schema.py)
# ===========================================================================


def _surprisal_audit_flat_stub(text, **kwargs):
    # Surprisal series that's flat enough to exercise summary math.
    return [4.0, 4.5, 4.2, 4.8, 4.1, 4.6, 4.3, 4.7, 4.0, 4.4] * 10


@pytest.fixture
def _surprisal_audit_envelope():
    audit = sa.audit_surprisal("the cat sat on the mat", score_fn=_surprisal_audit_flat_stub)
    audit["backend"] = {"id": "stub-model", "revision": "stub"}
    return sa.build_audit_payload(audit, target_path=Path("draft.txt"))


class TestSurprisalAuditEnvelopeKeys:
    def test_required_keys(self, _surprisal_audit_envelope):
        assert set(_surprisal_audit_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _surprisal_audit_envelope):
        assert _surprisal_audit_envelope["schema_version"] == "1.0"

    def test_tool_and_version(self, _surprisal_audit_envelope):
        assert _surprisal_audit_envelope["tool"] == "surprisal_audit"
        assert _surprisal_audit_envelope["version"] == sa.SCRIPT_VERSION


class TestSurprisalAuditResultsPayload:
    def test_results_carries_signals(self, _surprisal_audit_envelope):
        r = _surprisal_audit_envelope["results"]
        for k in (
            "n_tokens_scored", "series_length", "summary",
            "top_k_tokens", "sliding_window", "band", "backend",
        ):
            assert k in r

    def test_no_legacy_top_level_keys(self, _surprisal_audit_envelope):
        for legacy in (
            "n_tokens_scored", "series_length", "summary",
            "top_k_tokens", "sliding_window", "band", "backend",
        ):
            assert legacy not in _surprisal_audit_envelope


class TestSurprisalAuditUnavailable:
    def test_unavailable_audit(self):
        audit = {"task_surface": "smoothing_diagnosis", "tool": "surprisal_audit",
                 "version": "1.0", "available": False, "reason": "text too short"}
        _surprisal_audit_envelope = sa.build_audit_payload(audit, target_path=Path("x.txt"))
        assert _surprisal_audit_envelope["available"] is False
        assert _surprisal_audit_envelope["claim_license"] is None
        assert "text too short" in _surprisal_audit_envelope["warnings"]


class TestSurprisalAuditClaimLicense:
    def test_structured(self, _surprisal_audit_envelope):
        assert _surprisal_audit_envelope["claim_license"]["task_surface"] == "smoothing_diagnosis"

    def test_rendered_header(self, _surprisal_audit_envelope):
        assert _surprisal_audit_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


# ===========================================================================
# variance_audit  (formerly test_variance_audit_schema.py)
# ===========================================================================


def _variance_audit_fake_output(with_baseline=False, with_windows=False):
    """Mirror audit_text()'s real return shape: word/sentence counts
    live under `audit["summary"]`, not at the audit top level. Codex
    P2 on PR #84 caught the original fake fixture using a non-real
    flat shape — fixed here to match audit_text() exactly.
    """
    base = {
        "task_surface": "smoothing_diagnosis",
        "preprocessing": {"opt_out": False, "tokens_stripped": 0},
        "audit": {
            "summary": {
                "n_words": 3500,
                "n_sentences": 220,
                "n_words_original": 3500,
                "reliable": True,
            },
            "tier1": {"sentence_length": {"sd": 8.2, "burstiness_B": -0.15}},
            "tier2": {"pos_bigrams": {"entropy_bits": 7.8}},
            "tier3": {"adjacent_cosine": {"mean": 0.55, "sd": 0.10}},
        },
        "compression": {
            "band": "Lightly smoothed",
            "compression_fraction": 0.2,
            "flagged_signals": ["sentence_length_sd"],
        },
    }
    if with_baseline:
        base["baseline"] = {
            "n_files": 8,
            "aggregate": {"tier1": {"sentence_length": {"sd": {"mean": 9.0, "sd": 1.5}}}},
            "preprocessing": {"opt_out": False},
        }
        base["baseline_comparison"] = {"sentence_length_sd": -0.5}
    if with_windows:
        base["windows"] = {
            "window_size": 500,
            "stride": 250,
            "n_windows": 7,
            "results": [],
        }
    return base


@pytest.fixture
def _variance_audit_envelope():
    return va.build_audit_payload(
        _variance_audit_fake_output(), target_path=Path("draft.md"),
    )


class TestVarianceAuditEnvelopeKeys:
    def test_required_keys(self, _variance_audit_envelope):
        assert set(_variance_audit_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _variance_audit_envelope):
        assert _variance_audit_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _variance_audit_envelope):
        assert _variance_audit_envelope["task_surface"] == "smoothing_diagnosis"

    def test_tool_and_version(self, _variance_audit_envelope):
        assert _variance_audit_envelope["tool"] == "variance_audit"
        assert _variance_audit_envelope["version"] == va.SCRIPT_VERSION


class TestVarianceAuditTargetAndBaseline:
    def test_target_words(self, _variance_audit_envelope):
        assert _variance_audit_envelope["target"]["words"] == 3500

    def test_target_carries_preprocessing(self, _variance_audit_envelope):
        assert "preprocessing" in _variance_audit_envelope["target"]

    def test_baseline_null_without_supply(self, _variance_audit_envelope):
        assert _variance_audit_envelope["baseline"] is None

    def test_baseline_populated_when_supplied(self):
        # baseline_n_words is computed from baseline_block["audits"]
        # in main() and passed explicitly; the synthetic
        # output["baseline"] no longer carries an n_words field
        # (matches the real main() trim).
        env = va.build_audit_payload(
            _variance_audit_fake_output(with_baseline=True),
            target_path=Path("d.md"),
            baseline_n_words=25000,
        )
        assert env["baseline"]["n_files"] == 8
        assert env["baseline"]["words"] == 25000
        assert "aggregate" in env["baseline"]

    def test_baseline_words_defaults_to_zero_when_not_supplied(self):
        """Codex P2 contract: callers that have the full
        baseline_block in scope must pre-compute and pass
        baseline_n_words. Without it, baseline.words is 0 (n_files
        still surfaces correctly).
        """
        env = va.build_audit_payload(
            _variance_audit_fake_output(with_baseline=True), target_path=Path("d.md"),
        )
        assert env["baseline"]["n_files"] == 8
        assert env["baseline"]["words"] == 0


class TestVarianceAuditResultsPayload:
    def test_results_carries_audit_and_compression(self, _variance_audit_envelope):
        r = _variance_audit_envelope["results"]
        assert "audit" in r
        assert "compression" in r
        # Real audit_text() shape: word count under audit.summary.
        assert r["audit"]["summary"]["n_words"] == 3500
        assert r["compression"]["band"] == "Lightly smoothed"

    def test_baseline_comparison_under_results(self):
        env = va.build_audit_payload(
            _variance_audit_fake_output(with_baseline=True), target_path=Path("d.md"),
        )
        assert "baseline_comparison" in env["results"]

    def test_windows_under_results(self):
        env = va.build_audit_payload(
            _variance_audit_fake_output(with_windows=True), target_path=Path("d.md"),
        )
        assert "windows" in env["results"]
        assert env["results"]["windows"]["n_windows"] == 7

    def test_no_legacy_top_level_keys(self, _variance_audit_envelope):
        for legacy in (
            "preprocessing", "audit", "compression",
            "ablation", "baseline_comparison",
            "baseline_divergences", "baseline_bootstrap", "windows",
        ):
            assert legacy not in _variance_audit_envelope


class TestVarianceAuditClaimLicense:
    def test_structured(self, _variance_audit_envelope):
        cs = _variance_audit_envelope["claim_license"]["comparison_set"]
        assert cs["n_words"] == 3500
        assert cs["n_sentences"] == 220
        assert cs["band"] == "Lightly smoothed"
        assert cs["has_baseline"] is False
        assert cs["windowed"] is False

    def test_does_not_license_flags_cross_corpus_inversion(self, _variance_audit_envelope):
        text = _variance_audit_envelope["claim_license"]["does_not_license"].lower()
        assert "many causes" in text

    def test_rendered_header(self, _variance_audit_envelope):
        assert _variance_audit_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestVarianceAuditRealAuditShape:
    """Reviewer-reproduced regression (Codex P2 on PR #84).

    Pre-fix: `build_audit_payload()` read `audit["n_words"]`, but
    `audit_text()` stores the count at `audit["summary"]["n_words"]`.
    CLI runs reported `target.words: 0` while the actual word count
    was non-zero. The fake fixture above only passed because it used
    the wrong shape; this test exercises the real audit_text() path
    end-to-end and asserts non-zero target / baseline counts.

    Pinned at the build_audit_payload() boundary so any future
    refactor that breaks the audit→_variance_audit_envelope path fails here.
    """

    def test_real_audit_text_produces_nonzero_target_words(self):
        """A real audit_text() call produces audit["summary"]
        ["n_words"] > 0; the _variance_audit_envelope should surface that value at
        _variance_audit_envelope.target.words, NOT zero."""
        text = (
            "The committee deliberated through the afternoon. "
            "The proposal landed on Tuesday. The budget contracted. "
            "Daria signed off after lunch. The dashboard reflected "
            "regional activity. Stakeholders requested further "
            "analysis. The agency coordination role was delegated. "
        ) * 10
        try:
            audit = va.audit_text(text)
        except Exception:
            pytest.skip("audit_text dependencies unavailable in env")
            return
        # Real audit_text return shape: summary.n_words.
        assert audit["summary"]["n_words"] > 0
        # Build the _variance_audit_envelope from a main()-shaped output dict.
        output = {
            "task_surface": va.TASK_SURFACE,
            "audit": audit,
            "compression": {"band": "Lightly smoothed",
                            "compression_fraction": 0.1,
                            "flagged_signals": []},
        }
        _variance_audit_envelope = va.build_audit_payload(
            output, target_path=Path("draft.md"),
        )
        assert _variance_audit_envelope["target"]["words"] == audit["summary"]["n_words"]
        assert _variance_audit_envelope["target"]["words"] > 0
        # Comparison_set should also surface the real word count.
        cs = _variance_audit_envelope["claim_license"]["comparison_set"]
        assert cs["n_words"] == audit["summary"]["n_words"]

    def test_sum_baseline_n_words_aggregates_audits_list(self):
        """_sum_baseline_n_words walks the baseline_block.audits list
        and sums per-file summary.n_words. Without this helper, the
        _variance_audit_envelope's baseline.words would always be 0 (because
        output['baseline'] is trimmed in main() and loses the
        audits list)."""
        baseline_block = {
            "n_files": 3,
            "audits": [
                {"file": "a.md", "audit": {"summary": {"n_words": 800}}},
                {"file": "b.md", "audit": {"summary": {"n_words": 1200}}},
                {"file": "c.md", "audit": {"summary": {"n_words": 600}}},
            ],
        }
        assert va._sum_baseline_n_words(baseline_block) == 2600

    def test_sum_baseline_n_words_handles_failed_entries(self):
        """Per the audit_baseline contract, baseline entries that
        failed to parse have an 'error' key instead of 'audit'.
        The summer should skip them without raising."""
        baseline_block = {
            "n_files": 2,
            "audits": [
                {"file": "a.md", "audit": {"summary": {"n_words": 500}}},
                {"file": "b.md", "error": "could not read"},
            ],
        }
        assert va._sum_baseline_n_words(baseline_block) == 500

    def test_sum_baseline_n_words_handles_none(self):
        assert va._sum_baseline_n_words(None) == 0
        assert va._sum_baseline_n_words({}) == 0
        assert va._sum_baseline_n_words({"audits": []}) == 0


class TestVarianceAuditFunctionContractStaysLegacy:
    """variance_audit.audit_text() is called as a function by many
    other scripts (validation_harness, calibration_survey,
    sliding_window_heatmap, etc.). The migration must NOT change the
    audit dict's return shape. This test guards the contract.
    """

    def test_audit_text_returns_legacy_shape(self):
        """audit_text exists and returns a dict with the canonical
        top-level keys callers depend on (n_words, n_sentences, tier1
        / tier2 / tier3 nesting).
        """
        # Use a tiny synthetic text and the simplest call signature.
        text = "The committee met. The proposal landed. " * 60
        try:
            out = va.audit_text(text)
        except Exception:
            pytest.skip("audit_text dependencies unavailable in test env")
            return
        # Canonical keys that callers read.
        assert "tier1" in out
        # The audit dict still carries task_surface for legacy
        # function-call consumers (variance_audit's audit_text shape
        # is unchanged by this migration).
        assert "n_words" in out or "tier1" in out


# ===========================================================================
# voice_distance  (formerly test_voice_distance_schema.py)
# ===========================================================================


# compare_to_baseline builds `baseline_summary` by calling
# stylometry_core.summarize_entries on the baseline features, so the
# fixture does too. Hand-typing the block made the register-tier
# _voice_distance_envelope assertions tautological: build_audit_payload copies
# baseline_summary wholesale, so they only re-read keys the fixture
# itself inserted. Deriving the block makes them transitively pin real
# emission. The word counts reproduce the previously hand-written
# n_files / total_words / mean / min / max exactly.
_VOICE_DISTANCE_BASELINE_WORD_COUNTS = (1200, 5400, 2500, 2500, 2500, 2500, 2500, 2900)


def _voice_distance_baseline_entries(registers: list[str | None] | None = None) -> list[dict]:
    """Entry dicts in summarize_entries' input shape.

    ``registers`` defaults to an all-public_composed baseline; pass
    ``None`` in a slot for an entry that declares no register, or an
    unregistered leaf name for one the closed registry cannot resolve.
    """
    if registers is None:
        registers = ["blog_essay"] * len(_VOICE_DISTANCE_BASELINE_WORD_COUNTS)
    entries = []
    for index, n_words in enumerate(_VOICE_DISTANCE_BASELINE_WORD_COUNTS):
        register = registers[index]
        entries.append({
            "id": f"prior_{index}",
            "path": f"prior_{index}.md",
            "summary": {"n_words": n_words},
            "metadata": {} if register is None else {"register": register},
        })
    return entries


def _voice_distance_fake_result() -> dict:
    """Mirror compare_to_baseline's return shape with the minimum
    fields build_audit_payload reads. Avoids the spaCy + stylometric
    feature load needed for a real run.
    """
    return {
        "task_surface": "voice_coherence",
        "target_summary": {
            "n_words": 3200,
            "n_sentences": 180,
        },
        "baseline_summary": sc.summarize_entries(_voice_distance_baseline_entries()),
        "overall": {
            "weighted_delta": 1.4,
            "band": "Moderate drift",
        },
        "families": {
            "function_words": {
                "delta_normalized": 1.2,
                "top_features": [
                    {"feature": "the", "z": 0.5},
                ],
            },
            "char_ngrams_3": {
                "delta_normalized": 1.5,
                "top_features": [
                    {"feature": "th_", "z": 1.0},
                ],
            },
        },
        "warnings": [],
    }


@pytest.fixture
def _voice_distance_envelope():
    return vd.build_audit_payload(
        _voice_distance_fake_result(), target_path=Path("draft.md"),
    )


class TestVoiceDistanceEnvelopeKeys:
    def test_required_keys(self, _voice_distance_envelope):
        assert set(_voice_distance_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _voice_distance_envelope):
        assert _voice_distance_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _voice_distance_envelope):
        assert _voice_distance_envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, _voice_distance_envelope):
        assert _voice_distance_envelope["tool"] == "voice_distance"
        assert _voice_distance_envelope["version"] == vd.SCRIPT_VERSION


class TestVoiceDistanceTargetAndBaseline:
    def test_target_words_from_target_summary(self, _voice_distance_envelope):
        assert _voice_distance_envelope["target"]["words"] == 3200

    def test_target_carries_n_sentences(self, _voice_distance_envelope):
        assert _voice_distance_envelope["target"]["n_sentences"] == 180

    def test_baseline_n_files_and_words(self, _voice_distance_envelope):
        assert _voice_distance_envelope["baseline"]["n_files"] == 8
        assert _voice_distance_envelope["baseline"]["words"] == 22000

    def test_baseline_carries_mean_min_max(self, _voice_distance_envelope):
        assert _voice_distance_envelope["baseline"]["mean_words"] == 2750
        assert _voice_distance_envelope["baseline"]["min_words"] == 1200
        assert _voice_distance_envelope["baseline"]["max_words"] == 5400

    def test_baseline_carries_register_tier_composition(self, _voice_distance_envelope):
        assert _voice_distance_envelope["baseline"]["register_tier_counts"] == {
            "private_composed": 0,
            "private_dyadic": 0,
            "public_composed": 8,
            "public_responsive": 0,
        }
        assert _voice_distance_envelope["baseline"]["unresolved_register_count"] == 0

    def test_unresolved_baseline_registers_reach_the_envelope(self):
        """A NON-ZERO unresolved count must survive onto the consumed
        surface. `baseline.unresolved_register_count` is the only signal
        a consumer gets that a pooled reference holds entries whose
        privacy tier could not be resolved; asserting only ``== 0``
        cannot tell a working counter from one stuck at zero.
        """
        result = _voice_distance_fake_result()
        result["baseline_summary"] = sc.summarize_entries(
            _voice_distance_baseline_entries(
                ["blog_essay"] * 6 + [None, "not.registered"]
            )
        )
        _voice_distance_envelope = vd.build_audit_payload(
            result, target_path=Path("draft.md"),
        )
        assert _voice_distance_envelope["baseline"]["unresolved_register_count"] == 2
        assert _voice_distance_envelope["baseline"]["register_tier_counts"] == {
            "private_composed": 0,
            "private_dyadic": 0,
            "public_composed": 6,
            "public_responsive": 0,
        }


class TestVoiceDistanceResultsPayload:
    def test_results_carries_overall_and_families(self, _voice_distance_envelope):
        r = _voice_distance_envelope["results"]
        assert "overall" in r
        assert "families" in r

    def test_no_legacy_top_level_keys(self, _voice_distance_envelope):
        for legacy in (
            "target_summary", "baseline_summary",
            "overall", "families", "preprocessing",
        ):
            assert legacy not in _voice_distance_envelope


class TestVoiceDistanceClaimLicense:
    def test_structured_block_11_keys(self, _voice_distance_envelope):
        assert set(_voice_distance_envelope["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_task_surface_matches(self, _voice_distance_envelope):
        assert (
            _voice_distance_envelope["claim_license"]["task_surface"]
            == _voice_distance_envelope["task_surface"]
        )

    def test_comparison_set_carries_distance_summary(self, _voice_distance_envelope):
        cs = _voice_distance_envelope["claim_license"]["comparison_set"]
        assert cs["band"] == "Moderate drift"
        assert cs["weighted_delta"] == 1.4
        assert cs["n_baseline_files"] == 8

    def test_rendered_header(self, _voice_distance_envelope):
        assert _voice_distance_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )

class TestVoiceDistanceOptionalBlocks:
    def test_register_match_under_results(self):
        result = _voice_distance_fake_result()
        result["register_match"] = {
            "target_classification": {
                "primary": "narrative_fiction",
                "confidence": 0.8,
                "taxonomy": REGISTER_TAXONOMY,
            },
            "match": {
                "strength": "strong",
                "taxonomy": REGISTER_TAXONOMY,
            },
        }
        _voice_distance_envelope = vd.build_audit_payload(
            result, target_path=Path("draft.md"),
        )
        assert "register_match" in _voice_distance_envelope["results"]
        assert (
            _voice_distance_envelope["claim_license"]["comparison_set"]["register_match"]
            == "strong"
        )

    def test_length_matched_bootstrap_under_results(self):
        result = _voice_distance_fake_result()
        result["length_matched_bootstrap"] = {
            "available": True,
            "percentile": 0.65,
        }
        _voice_distance_envelope = vd.build_audit_payload(
            result, target_path=Path("draft.md"),
        )
        assert "length_matched_bootstrap" in _voice_distance_envelope["results"]


class TestVoiceDistanceWarningsPropagate:
    def test_warnings_forwarded(self):
        result = _voice_distance_fake_result()
        result["warnings"] = ["Small baseline: <20K words."]
        _voice_distance_envelope = vd.build_audit_payload(
            result, target_path=Path("draft.md"),
        )
        assert _voice_distance_envelope["warnings"] == ["Small baseline: <20K words."]


# ---------------------------------------------------------------------------
# AC10 (neurobiber-v2): biber_features opt-in is OFF by default; the existing
# compare_to_baseline consumer path produces no 'biber_features' key anywhere
# in the results _voice_distance_envelope when --include-biber is not passed.
# Recursive key walk (NOT substring) — mirrors the _walk_keys / _FORBIDDEN_KEYS
# pattern from test_dependency_distance_audit.py:151-159.
# ---------------------------------------------------------------------------

def _voice_distance_walk_keys_vd(obj):
    """Yield every dict key reachable in a nested payload (lists too)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _voice_distance_walk_keys_vd(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _voice_distance_walk_keys_vd(item)


class TestVoiceDistanceBiberFeaturesAbsentByDefault:
    """AC10: biber_features does not appear in the default voice_distance _voice_distance_envelope.

    This protects the downstream drift gate for apodictic and setec-voicewright:
    their pinned contract fixtures are against the default (no --include-biber)
    output, which must be byte-identical before and after this change.
    """

    def test_biber_features_absent_from_default_envelope(self):
        """'biber_features' not in any key of the default build_audit_payload _voice_distance_envelope."""
        _voice_distance_envelope = vd.build_audit_payload(
            _voice_distance_fake_result(), target_path=Path("draft.md"),
        )
        keys = set(_voice_distance_walk_keys_vd(_voice_distance_envelope["results"]))
        assert "biber_features" not in keys, (
            "'biber_features' key found in default voice_distance results _voice_distance_envelope "
            "(include_biber defaults to False — this key must NOT appear without --include-biber)"
        )


# ---------------------------------------------------------------------------
# Codex P1 regression: voice_distance --include-biber must emit a clean
# missing_dependency _voice_distance_envelope (available:false) rather than crashing with
# ValueError when no real Biber tagger is configured (always the case in
# the M1 build — there is no real tagger yet).
# Ref: Codex P1 finding on voice_distance.py:754
# ---------------------------------------------------------------------------

def _voice_distance_run_vd_main(argv: list[str]) -> int:
    """Invoke vd.main() with a patched sys.argv."""
    import sys as _sys
    orig = _sys.argv
    _sys.argv = argv
    try:
        return vd.main()
    finally:
        _sys.argv = orig


class TestVoiceDistanceIncludeBiberMissingDependencyCLI:
    """Codex P1: voice_distance --include-biber with no tagger must NOT crash.

    Pre-fix: compare_to_baseline raises ValueError (include_biber requires
    biber_vector or biber_tagger) and the script exits unclean with a traceback.
    Post-fix: the CLI intercepts the missing-tagger condition BEFORE calling
    compare_to_baseline and emits available:false / reason_category=missing_dependency.
    """

    def test_include_biber_no_tagger_emits_missing_dependency(
        self, tmp_path, capsys
    ):
        """--include-biber with no M2 tagger → available:false, missing_dependency."""
        import json as _json

        # Build a minimal two-file baseline so load_entries succeeds.
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        (baseline_dir / "a.md").write_text(
            "The committee deliberated through the afternoon. " * 20,
            encoding="utf-8",
        )
        (baseline_dir / "b.md").write_text(
            "Members reviewed the budget on Tuesday. " * 20,
            encoding="utf-8",
        )
        target = tmp_path / "target.md"
        target.write_text(
            "Officials noted that the process followed established guidelines. " * 10,
            encoding="utf-8",
        )

        rc = _voice_distance_run_vd_main([
            "voice_distance.py",
            str(target),
            "--baseline-dir", str(baseline_dir),
            "--no-spacy",
            "--include-biber",
            "--json",
        ])

        captured = capsys.readouterr()
        # Must not crash (no uncaught ValueError → non-zero rc from exception)
        # The error _voice_distance_envelope exits with EXIT_CONTRACT (3 per setec_run convention)
        # or at minimum does NOT raise an unhandled exception.
        assert rc != 0, (
            "Expected a non-zero exit code (missing_dependency _voice_distance_envelope), "
            f"got rc={rc}"
        )
        # The JSON _voice_distance_envelope must be on stdout.
        assert captured.out.strip(), (
            "Expected a JSON _voice_distance_envelope on stdout; got nothing"
        )
        _voice_distance_envelope = _json.loads(captured.out)
        assert _voice_distance_envelope["available"] is False, (
            f"Expected available:false, got available={_voice_distance_envelope['available']}"
        )
        assert _voice_distance_envelope["reason_category"] == "missing_dependency", (
            f"Expected reason_category='missing_dependency', "
            f"got {_voice_distance_envelope['reason_category']!r}"
        )
        # Reason must mention the Biber tagger so users understand the gap.
        assert "biber" in _voice_distance_envelope["reason"].lower() or "tagger" in _voice_distance_envelope["reason"].lower(), (
            f"Expected 'biber' or 'tagger' in reason, got: {_voice_distance_envelope['reason']!r}"
        )

    def test_include_biber_abstains_when_neurobiber_importable(
        self, tmp_path, capsys, monkeypatch
    ):
        """Codex round-2 P2: --include-biber with a PRESENT neurobiber still abstains cleanly.

        Pre-fix: _try_load_real_tagger() raised NotImplementedError as soon as
        `neurobiber` was importable, escaping the CLI guard as an uncaught
        traceback. Post-fix: the deferred M2 adapter returns None, so the CLI
        emits available:false / missing_dependency with rc=3.
        """
        import json as _json
        import types as _types

        # Package PRESENT — inject a stub so `import neurobiber` SUCCEEDS.
        monkeypatch.setitem(sys.modules, "neurobiber", _types.ModuleType("neurobiber"))

        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        (baseline_dir / "a.md").write_text(
            "The committee deliberated through the afternoon. " * 20,
            encoding="utf-8",
        )
        (baseline_dir / "b.md").write_text(
            "Members reviewed the budget on Tuesday. " * 20,
            encoding="utf-8",
        )
        target = tmp_path / "target.md"
        target.write_text(
            "Officials noted that the process followed established guidelines. " * 10,
            encoding="utf-8",
        )

        # Must NOT raise (pre-fix: NotImplementedError escapes vd.main()).
        rc = _voice_distance_run_vd_main([
            "voice_distance.py",
            str(target),
            "--baseline-dir", str(baseline_dir),
            "--no-spacy",
            "--include-biber",
            "--json",
        ])

        captured = capsys.readouterr()
        assert rc == 3, (
            f"Expected rc=3 (missing_dependency _voice_distance_envelope), got rc={rc}"
        )
        _voice_distance_envelope = _json.loads(captured.out)
        assert _voice_distance_envelope["available"] is False
        assert _voice_distance_envelope["reason_category"] == "missing_dependency"


# ===========================================================================
# voice_drift_tracker  (formerly test_voice_drift_tracker_schema.py)
# ===========================================================================


def _voice_drift_tracker_fake_render_inputs():
    """Synthetic render_json inputs that exercise build_output's
    plumbing without needing a real drift run."""
    from voice_drift_tracker import PeriodProfile

    profiles = {
        "1787": PeriodProfile(
            label="1787",
            n_docs=3,
            n_words=12000,
            feature_items=[],
            period_centroids={"function_words": {"the": 0.06}},
        ),
        "1788": PeriodProfile(
            label="1788",
            n_docs=4,
            n_words=16000,
            feature_items=[],
            period_centroids={"function_words": {"the": 0.058}},
        ),
    }
    family_distances = {
        "function_words": {
            ("1787", "1788"): {
                "burrows_delta": 0.6,
                "cosine_distance": 0.04,
            },
        },
    }
    weighted_distances = {
        ("1787", "1788"): {
            "burrows_delta": 0.6,
            "cosine_distance": 0.04,
        },
    }
    drift = {
        "function_words": {
            "drifting_features": [
                {"feature": "the", "cv": 0.15},
            ],
            "stable_features": [],
        },
    }
    return {
        "profiles": profiles,
        "family_distances": family_distances,
        "weighted_distances": weighted_distances,
        "drift": drift,
        "dropped_periods": [],
        "inputs": {
            "manifest": "manifest.jsonl",
            "min_docs_per_period": 1,
        },
        "granularity": "year",
    }


@pytest.fixture
def _voice_drift_tracker_envelope():
    json_str = vdt.render_json(**_voice_drift_tracker_fake_render_inputs())
    return json.loads(json_str)


class TestVoiceDriftTrackerEnvelopeKeys:
    def test_required_keys(self, _voice_drift_tracker_envelope):
        assert set(_voice_drift_tracker_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _voice_drift_tracker_envelope):
        assert _voice_drift_tracker_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _voice_drift_tracker_envelope):
        assert _voice_drift_tracker_envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, _voice_drift_tracker_envelope):
        assert _voice_drift_tracker_envelope["tool"] == "voice_drift_tracker"
        assert _voice_drift_tracker_envelope["version"] == vdt.SCRIPT_VERSION


class TestVoiceDriftTrackerTargetAndBaseline:
    def test_target_words_sums_period_words(self, _voice_drift_tracker_envelope):
        # 12000 + 16000
        assert _voice_drift_tracker_envelope["target"]["words"] == 28000

    def test_target_carries_granularity(self, _voice_drift_tracker_envelope):
        assert _voice_drift_tracker_envelope["target"]["granularity"] == "year"

    def test_baseline_is_null(self, _voice_drift_tracker_envelope):
        """voice_drift_tracker analyzes a date-tagged corpus; the
        corpus IS the target. There's no separate comparison set.
        """
        assert _voice_drift_tracker_envelope["baseline"] is None


class TestVoiceDriftTrackerResultsPayload:
    def test_results_carries_drift_data(self, _voice_drift_tracker_envelope):
        r = _voice_drift_tracker_envelope["results"]
        for k in (
            "n_periods", "periods", "granularity", "inputs",
            "dropped_periods", "cross_period_distances_per_family",
            "cross_period_distances_weighted", "drift_scores",
        ):
            assert k in r, f"missing results key: {k}"

    def test_n_periods_value(self, _voice_drift_tracker_envelope):
        assert _voice_drift_tracker_envelope["results"]["n_periods"] == 2

    def test_no_legacy_top_level_keys(self, _voice_drift_tracker_envelope):
        for legacy in (
            "n_periods", "periods", "granularity", "inputs",
            "dropped_periods",
            "cross_period_distances_per_family",
            "cross_period_distances_weighted", "drift_scores",
        ):
            assert legacy not in _voice_drift_tracker_envelope


class TestVoiceDriftTrackerClaimLicense:
    def test_structured_block_11_keys(self, _voice_drift_tracker_envelope):
        """Pre-1.85 emitted claim_license as a 2-key dict
        (licenses, does_not_license). Post-1.85 emits the full
        structured ClaimLicense.to_dict() shape.
        """
        assert set(_voice_drift_tracker_envelope["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_legacy_licenses_text_preserved(self, _voice_drift_tracker_envelope):
        assert (
            _voice_drift_tracker_envelope["claim_license"]["licenses"]
            == vdt.CLAIM_LICENSE["licenses"]
        )
        assert (
            _voice_drift_tracker_envelope["claim_license"]["does_not_license"]
            == vdt.CLAIM_LICENSE["does_not_license"]
        )

    def test_comparison_set_carries_periods(self, _voice_drift_tracker_envelope):
        cs = _voice_drift_tracker_envelope["claim_license"]["comparison_set"]
        assert cs["granularity"] == "year"
        assert cs["n_periods"] == 2
        assert "1787" in cs["period_labels"]
        assert "1788" in cs["period_labels"]
        assert cs["n_docs_per_period"]["1787"] == 3

    def test_rendered_header(self, _voice_drift_tracker_envelope):
        assert _voice_drift_tracker_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestVoiceDriftTrackerMarkdownPathStillWorks:
    def test_render_markdown_consumes_legacy_claim_license(self):
        """render_markdown reads CLAIM_LICENSE["licenses"] directly.
        The migration keeps CLAIM_LICENSE alive as a constant for
        this path; the markdown render must not break.
        """
        md = vdt.render_markdown(**{
            k: v for k, v in _voice_drift_tracker_fake_render_inputs().items()
            if k not in {"inputs"}  # render_markdown takes a different signature
        })
        assert "Voice Drift Report" in md
        # Some chunk of the legacy CLAIM_LICENSE["licenses"] string
        # appears in the markdown.
        assert "voiceprint summary" in md


# ===========================================================================
# voice_profile  (formerly test_voice_profile_schema.py)
# ===========================================================================


# build_profile builds `baseline_summary` by calling
# stylometry_core.summarize_entries on the baseline features, so the
# fixture does too. Hand-typing the block made the register-tier
# assertion tautological: build_audit_payload copies the profile dict
# wholesale into `results`, so the assertion only re-read a key the
# fixture itself inserted. Deriving the block makes it transitively pin
# real emission. The word counts reproduce the previously hand-written
# n_files / total_words / min / max exactly.
_VOICE_PROFILE_BASELINE_WORD_COUNTS = (400, 6000) + (1860,) * 10


def _voice_profile_baseline_entries(registers: list[str | None] | None = None) -> list[dict]:
    """Entry dicts in summarize_entries' input shape.

    ``registers`` defaults to an all-private_composed baseline; pass
    ``None`` in a slot for an entry that declares no register, or an
    unregistered leaf name for one the closed registry cannot resolve.
    """
    if registers is None:
        registers = ["personal"] * len(_VOICE_PROFILE_BASELINE_WORD_COUNTS)
    entries = []
    for index, n_words in enumerate(_VOICE_PROFILE_BASELINE_WORD_COUNTS):
        register = registers[index]
        entries.append({
            "id": f"prior_{index}",
            "path": f"prior_{index}.md",
            "summary": {"n_words": n_words},
            "metadata": {} if register is None else {"register": register},
        })
    return entries


def _voice_profile_fake_profile() -> dict:
    """Construct a minimal profile dict mirroring build_profile's
    return shape. Avoids the spaCy + corpus load the real script
    needs while exercising build_audit_payload's plumbing.
    """
    return {
        "task_surface": "voice_coherence",
        "privacy": "private",
        "baseline_summary": sc.summarize_entries(_voice_profile_baseline_entries()),
        "preprocessing": {
            "opt_out": False,
            "tokens_stripped": 120,
            "strip_ratio": 0.005,
            "dominant_rule": "html_strip",
        },
        "selected_features": {
            "function_words": 100,
            "char_ngrams_3": 200,
            "char_ngrams_4": 200,
            "pos_trigrams": 300,
        },
        "families": {
            "function_words": {
                "top_features": [
                    {"feature": "the", "mean": 0.06, "sd": 0.01, "cv": 0.17},
                ],
                "most_stable_features": [
                    {"feature": "of", "mean": 0.03, "sd": 0.004, "cv": 0.13},
                ],
            },
        },
        "warnings": [],
    }


@pytest.fixture
def _voice_profile_envelope():
    return vp.build_audit_payload(
        _voice_profile_fake_profile(),
        target_path=Path("baselines/personal/"),
    )


class TestVoiceProfileEnvelopeKeys:
    def test_required_keys(self, _voice_profile_envelope):
        assert set(_voice_profile_envelope.keys()) == REQUIRED_TOP_LEVEL_KEYS

    def test_schema_version(self, _voice_profile_envelope):
        assert _voice_profile_envelope["schema_version"] == "1.0"

    def test_task_surface(self, _voice_profile_envelope):
        assert _voice_profile_envelope["task_surface"] == "voice_coherence"

    def test_tool_and_version(self, _voice_profile_envelope):
        assert _voice_profile_envelope["tool"] == "voice_profile"
        assert _voice_profile_envelope["version"] == vp.SCRIPT_VERSION


class TestVoiceProfileTargetAndBaseline:
    def test_target_words_from_total_words(self, _voice_profile_envelope):
        assert _voice_profile_envelope["target"]["words"] == 25000

    def test_target_carries_privacy(self, _voice_profile_envelope):
        assert _voice_profile_envelope["target"]["privacy"] == "private"

    def test_target_carries_n_files(self, _voice_profile_envelope):
        assert _voice_profile_envelope["target"]["n_files"] == 12

    def test_target_carries_preprocessing(self, _voice_profile_envelope):
        assert "preprocessing" in _voice_profile_envelope["target"]
        assert _voice_profile_envelope["target"]["preprocessing"]["dominant_rule"] == "html_strip"

    def test_baseline_is_null(self, _voice_profile_envelope):
        """voice_profile profiles a corpus; the corpus IS the target.
        ``baseline`` (= comparison set) is None by design — there is
        nothing to compare against.
        """
        assert _voice_profile_envelope["baseline"] is None


class TestVoiceProfileResultsPayload:
    def test_results_carries_profile_data(self, _voice_profile_envelope):
        r = _voice_profile_envelope["results"]
        assert "baseline_summary" in r
        assert r["baseline_summary"]["register_tier_counts"]["private_composed"] == 12
        assert r["baseline_summary"]["unresolved_register_count"] == 0
        assert "selected_features" in r
        assert "families" in r

    def test_unresolved_baseline_registers_reach_the_envelope(self):
        """A NON-ZERO unresolved count must survive onto the consumed
        surface. `results.baseline_summary.unresolved_register_count` is
        the only signal a consumer gets that the profiled corpus holds
        entries whose privacy tier could not be resolved; asserting only
        ``== 0`` cannot tell a working counter from one stuck at zero.
        """
        profile = _voice_profile_fake_profile()
        profile["baseline_summary"] = sc.summarize_entries(
            _voice_profile_baseline_entries(
                ["personal"] * 9 + [None, "not.registered", None]
            )
        )
        _voice_profile_envelope = vp.build_audit_payload(
            profile, target_path=Path("baselines/personal/"),
        )
        summary = _voice_profile_envelope["results"]["baseline_summary"]
        assert summary["unresolved_register_count"] == 3
        assert summary["register_tier_counts"] == {
            "private_composed": 9,
            "private_dyadic": 0,
            "public_composed": 0,
            "public_responsive": 0,
        }

    def test_no_legacy_top_level_keys(self, _voice_profile_envelope):
        # `warnings` is intentionally a top-level _voice_profile_envelope key
        # (SPEC §1.1); it does NOT belong in the legacy list.
        for legacy in (
            "baseline_summary", "selected_features", "families",
            "preprocessing", "privacy",
        ):
            assert legacy not in _voice_profile_envelope


class TestVoiceProfileClaimLicense:
    def test_structured_block_11_keys(self, _voice_profile_envelope):
        assert set(_voice_profile_envelope["claim_license"].keys()) == CLAIM_LICENSE_KEYS

    def test_task_surface_matches(self, _voice_profile_envelope):
        assert (
            _voice_profile_envelope["claim_license"]["task_surface"]
            == _voice_profile_envelope["task_surface"]
        )

    def test_does_not_license_names_privacy_constraint(self, _voice_profile_envelope):
        # voice_profile outputs are voice-cloning-grade. The license
        # MUST flag this; the test guards against accidental softening.
        text = _voice_profile_envelope["claim_license"]["does_not_license"].lower()
        assert "voice-cloning" in text or "private" in text

    def test_rendered_header(self, _voice_profile_envelope):
        assert _voice_profile_envelope["claim_license_rendered"].startswith(
            "## What this result licenses"
        )


class TestVoiceProfileWarningsForwarded:
    def test_warnings_propagate(self):
        profile = _voice_profile_fake_profile()
        profile["warnings"] = ["Baseline corpus is small."]
        _voice_profile_envelope = vp.build_audit_payload(
            profile, target_path=Path("baselines/personal/"),
        )
        assert _voice_profile_envelope["warnings"] == ["Baseline corpus is small."]


class TestVoiceProfileStdoutPrivacyGate:
    """Reviewer-reproduced regression (Codex P2 on PR #82).

    Pre-fix: `voice_profile.py --json` with no --out dumped a
    voice-cloning-grade profile to stdout with exit 0, bypassing
    the ai-prose-baselines-private/ path check (stdout has no
    path, so the path-based guard never fired). Post-fix: stdout
    is refused unless `--allow-public-output` is passed, mirroring
    the same default-private posture voice_drift_tracker and
    pov_voice_profile enforce.
    """

    def test_cli_refuses_stdout_without_allow_flag(self, tmp_path, capsys):
        """No --out, no --allow-public-output → exit 2 + stderr,
        nothing on stdout."""
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        (baseline_dir / "a.md").write_text(
            "The committee deliberated through the afternoon. " * 20,
            encoding="utf-8",
        )
        (baseline_dir / "b.md").write_text(
            "Members reviewed the budget on Tuesday. " * 20,
            encoding="utf-8",
        )
        # voice_profile.main() reads sys.argv directly via
        # parser.parse_args() without an argv kwarg, so patch argv.
        import sys as _sys
        orig_argv = _sys.argv
        _sys.argv = [
            "voice_profile.py",
            "--baseline-dir", str(baseline_dir),
            "--json",
        ]
        try:
            rc = vp.main()
        finally:
            _sys.argv = orig_argv
        assert rc == 2
        captured = capsys.readouterr()
        # The refusal message lands on stderr; stdout MUST be empty
        # (no profile leak).
        assert "stdout" in captured.err.lower()
        assert "allow-public-output" in captured.err
        assert captured.out == ""

    def test_cli_allows_stdout_with_allow_flag(self, tmp_path, capsys):
        """No --out, but --allow-public-output → exit 0; _voice_profile_envelope on
        stdout."""
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        (baseline_dir / "a.md").write_text(
            "The committee deliberated through the afternoon. " * 20,
            encoding="utf-8",
        )
        (baseline_dir / "b.md").write_text(
            "Members reviewed the budget on Tuesday. " * 20,
            encoding="utf-8",
        )
        import sys as _sys
        orig_argv = _sys.argv
        _sys.argv = [
            "voice_profile.py",
            "--baseline-dir", str(baseline_dir),
            "--json", "--allow-public-output",
        ]
        try:
            rc = vp.main()
        finally:
            _sys.argv = orig_argv
        assert rc == 0
        captured = capsys.readouterr()
        # Profile _voice_profile_envelope appears on stdout.
        import json as _json
        payload = _json.loads(captured.out)
        assert payload["schema_version"] == "1.0"
        assert payload["tool"] == "voice_profile"


def _voice_profile_write_baseline(tmp_path) -> Path:
    """A minimal two-file prose baseline the real script can profile."""
    baseline_dir = tmp_path / "baseline"
    baseline_dir.mkdir()
    (baseline_dir / "a.md").write_text(
        "The committee deliberated through the afternoon. " * 20,
        encoding="utf-8",
    )
    (baseline_dir / "b.md").write_text(
        "Members reviewed the budget on Tuesday. " * 20,
        encoding="utf-8",
    )
    return baseline_dir


def _voice_profile_run_main(argv):
    """Invoke vp.main() with a patched argv (the script reads sys.argv
    directly via parser.parse_args() with no argv kwarg)."""
    import sys as _sys
    orig = _sys.argv
    _sys.argv = argv
    try:
        return vp.main()
    finally:
        _sys.argv = orig


class TestVoiceProfileJsonOutFileDelivery:
    """The R2/R3 dispatcher file-delivery contract (json_delivery: file).

    setec_run injects a private ``--json-out`` under
    ``ai-prose-baselines-private/``, the script writes the schema_version
    1.0 _voice_profile_envelope there, and the dispatcher reads it back and projects it to
    stdout. Mirrors pov_voice_profile.py's --json-out. The same
    default-private posture as --out / stdout applies: a public --json-out
    path is refused without --allow-public-output, so nothing voice-cloning-
    grade ever lands outside ai-prose-baselines-private/.
    """

    def test_json_out_to_private_path_writes_envelope(self, tmp_path, capsys):
        baseline_dir = _voice_profile_write_baseline(tmp_path)
        artifact = tmp_path / "ai-prose-baselines-private" / "profile.json"
        rc = _voice_profile_run_main([
            "voice_profile.py",
            "--baseline-dir", str(baseline_dir),
            "--no-spacy",
            "--json-out", str(artifact),
        ])
        assert rc == 0
        captured = capsys.readouterr()
        # The _voice_profile_envelope goes to the private file, never to stdout.
        assert captured.out == ""
        import json as _json
        payload = _json.loads(artifact.read_text(encoding="utf-8"))
        assert payload["schema_version"] == "1.0"
        assert payload["tool"] == "voice_profile"

    def test_json_out_to_public_path_refused(self, tmp_path, capsys):
        baseline_dir = _voice_profile_write_baseline(tmp_path)
        artifact = tmp_path / "profile.json"  # NOT under the private dir
        rc = _voice_profile_run_main([
            "voice_profile.py",
            "--baseline-dir", str(baseline_dir),
            "--no-spacy",
            "--json-out", str(artifact),
        ])
        assert rc == 2
        captured = capsys.readouterr()
        assert "ai-prose-baselines-private" in captured.err
        assert "allow-public-output" in captured.err
        # Refusal happens BEFORE any write — nothing leaked.
        assert not artifact.exists()

    def test_json_out_public_with_allow_flag(self, tmp_path, capsys):
        baseline_dir = _voice_profile_write_baseline(tmp_path)
        artifact = tmp_path / "profile.json"
        rc = _voice_profile_run_main([
            "voice_profile.py",
            "--baseline-dir", str(baseline_dir),
            "--no-spacy",
            "--json-out", str(artifact),
            "--allow-public-output",
        ])
        assert rc == 0
        import json as _json
        payload = _json.loads(artifact.read_text(encoding="utf-8"))
        assert payload["tool"] == "voice_profile"


# ---------------------------------------------------------------------------
# AC10 (neurobiber-v2): biber_features opt-in is OFF by default; the existing
# build_profile / build_audit_payload consumer path produces no 'biber_features'
# key anywhere in the results _voice_profile_envelope when --include-biber is not passed.
# Recursive key walk (NOT substring) — mirrors the _walk_keys / _FORBIDDEN_KEYS
# pattern from test_dependency_distance_audit.py:151-159.
# ---------------------------------------------------------------------------

def _voice_profile_walk_keys_vp(obj):
    """Yield every dict key reachable in a nested payload (lists too)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _voice_profile_walk_keys_vp(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _voice_profile_walk_keys_vp(item)


class TestVoiceProfileBiberFeaturesAbsentByDefault:
    """AC10: biber_features does not appear in the default voice_profile _voice_profile_envelope.

    This protects the downstream drift gate for apodictic and setec-voicewright:
    their pinned contract fixtures are against the default (no --include-biber)
    output, which must be byte-identical before and after this change.
    """

    def test_biber_features_absent_from_default_envelope(self):
        """'biber_features' not in any key of the default build_audit_payload _voice_profile_envelope."""
        _voice_profile_envelope = vp.build_audit_payload(
            _voice_profile_fake_profile(),
            target_path=Path("baselines/personal/"),
        )
        keys = set(_voice_profile_walk_keys_vp(_voice_profile_envelope["results"]))
        assert "biber_features" not in keys, (
            "'biber_features' key found in default voice_profile results _voice_profile_envelope "
            "(include_biber defaults to False — this key must NOT appear without --include-biber)"
        )


# ---------------------------------------------------------------------------
# Codex P1 regression: voice_profile --include-biber must emit a clean
# missing_dependency _voice_profile_envelope (available:false) rather than crashing with
# ValueError when no real Biber tagger is configured (always the case in
# the M1 build — there is no real tagger yet).
# Ref: Codex P1 finding on voice_distance.py:754 (same posture, both CLIs)
# ---------------------------------------------------------------------------

class TestVoiceProfileIncludeBiberMissingDependencyCLI:
    """Codex P1: voice_profile --include-biber with no tagger must NOT crash.

    Pre-fix: build_profile raises ValueError (include_biber requires
    biber_vector or biber_tagger) and the script exits unclean with a traceback.
    Post-fix: the CLI intercepts the missing-tagger condition BEFORE calling
    build_profile and emits available:false / reason_category=missing_dependency.
    """

    def test_include_biber_no_tagger_emits_missing_dependency(
        self, tmp_path, capsys
    ):
        """--include-biber with no M2 tagger → available:false, missing_dependency."""
        import json as _json

        baseline_dir = _voice_profile_write_baseline(tmp_path)

        rc = _voice_profile_run_main([
            "voice_profile.py",
            "--baseline-dir", str(baseline_dir),
            "--no-spacy",
            "--include-biber",
            "--json",
            "--allow-public-output",
        ])

        captured = capsys.readouterr()
        # Must not crash with an unhandled ValueError.
        assert rc != 0, (
            "Expected a non-zero exit code (missing_dependency _voice_profile_envelope), "
            f"got rc={rc}"
        )
        # The JSON _voice_profile_envelope must be on stdout.
        assert captured.out.strip(), (
            "Expected a JSON _voice_profile_envelope on stdout; got nothing"
        )
        _voice_profile_envelope = _json.loads(captured.out)
        assert _voice_profile_envelope["available"] is False, (
            f"Expected available:false, got available={_voice_profile_envelope['available']}"
        )
        assert _voice_profile_envelope["reason_category"] == "missing_dependency", (
            f"Expected reason_category='missing_dependency', "
            f"got {_voice_profile_envelope['reason_category']!r}"
        )
        # Reason must mention the Biber tagger so users understand the gap.
        assert "biber" in _voice_profile_envelope["reason"].lower() or "tagger" in _voice_profile_envelope["reason"].lower(), (
            f"Expected 'biber' or 'tagger' in reason, got: {_voice_profile_envelope['reason']!r}"
        )

    def test_include_biber_abstains_when_neurobiber_importable(
        self, tmp_path, capsys, monkeypatch
    ):
        """Codex round-2 P2: --include-biber with a PRESENT neurobiber still abstains cleanly.

        Pre-fix: _try_load_real_tagger() raised NotImplementedError as soon as
        `neurobiber` was importable, escaping the CLI guard as an uncaught
        traceback. Post-fix: the deferred M2 adapter returns None, so the CLI
        emits available:false / missing_dependency with rc=3.
        """
        import json as _json
        import types as _types

        # Package PRESENT — inject a stub so `import neurobiber` SUCCEEDS.
        monkeypatch.setitem(sys.modules, "neurobiber", _types.ModuleType("neurobiber"))

        baseline_dir = _voice_profile_write_baseline(tmp_path)

        # Must NOT raise (pre-fix: NotImplementedError escapes vp.main()).
        rc = _voice_profile_run_main([
            "voice_profile.py",
            "--baseline-dir", str(baseline_dir),
            "--no-spacy",
            "--include-biber",
            "--json",
            "--allow-public-output",
        ])

        captured = capsys.readouterr()
        assert rc == 3, (
            f"Expected rc=3 (missing_dependency _voice_profile_envelope), got rc={rc}"
        )
        _voice_profile_envelope = _json.loads(captured.out)
        assert _voice_profile_envelope["available"] is False
        assert _voice_profile_envelope["reason_category"] == "missing_dependency"
