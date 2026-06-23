#!/usr/bin/env python3
"""biber_features.py — Biber lexico-grammatical register feature family (spec neurobiber-v2).

Descriptive Biber register panel: a fixed, named panel of 96 Biber lexico-grammatical register
features derived from Neurobiber (arXiv:2502.18590), a small, fast neural tagger that predicts
register rates for a passage. This module provides:

  - BIBER_FEATURE_SCHEMA — the canonical ordered list of 96 (feature_id, label) pairs.
  - biber_family_features(vector) — normalizer: fills missing → 0.0, silently drops unknown
    keys, returns {feature_id: rate} over the fixed schema in order.
  - A standalone main() that, given a target + an injected tagger, emits a biber_panel result.
    Abstains (available:false / missing_dependency) when no tagger is wired and no model is
    installed (the dependency_distance_audit.py whole-surface abstain pattern).

M1 (model-free, CI): uses the BIBER_FEATURE_SCHEMA + normalizer + the injectable tagger seam.
M2 (deferred): the real Neurobiber tagger behind a lazy torch/transformers import.

arXiv root: Neurobiber: Fast and Interpretable Stylistic Feature Extraction (arXiv:2502.18590).
Dual-use citation: Do LLMs Write Like Humans? Variation in Grammatical and Rhetorical Styles
  (arXiv:2410.16107) — Biber features separate human-vs-LLM AND LLM-vs-LLM; claim-license
  refuses model-family attribution, AI/human, and authorship.

Anti-Goodhart boundary: this descriptive surface MUST NOT be fed as a labeled feature into any
SETEC discrimination/attribution surface (model_family_attribution.py does NOT import this
module; that is tested in test_biber_features.py). Corpus-grounded description is in-bounds;
SETEC-targeting is out. See voicewright anti-Goodhart boundary in AGENTS.md.

TASK_SURFACE: voice_coherence (existing — reuses the claim_license_surfaces/voice_coherence.txt
  fragment; no new claim_license_surfaces/*.txt needed).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_schema import build_error_output, build_output  # noqa: E402
from claim_license import from_legacy  # noqa: E402

TASK_SURFACE = "voice_coherence"
TOOL_NAME = "biber_features"
SCRIPT_VERSION = "1.0"

# ---------------------------------------------------------------------------
# BIBER_FEATURE_SCHEMA — the canonical 96-feature Neurobiber panel.
#
# This is the SINGLE SOURCE OF TRUTH for the feature count and order.
# The AC1 test asserts len(BIBER_FEATURE_SCHEMA) == 96, unique ids, no
# duplicates. Every n_features literal in tests, stubs, and goldens uses 96.
# PINNED at 96 per arXiv:2502.18590 (M0-verified; the prior draft's 67 is
# superseded).
#
# Feature ids follow the BIN_* convention (Biber INdicator prefix).
# Labels are human-readable descriptions from the Neurobiber panel.
# ---------------------------------------------------------------------------

BIBER_FEATURE_SCHEMA: tuple[tuple[str, str], ...] = (
    ("BIN_past_tense_verb", "Past tense verbs"),
    ("BIN_perfect_aspect", "Perfect aspect (have + past participle)"),
    ("BIN_present_tense_verb", "Present tense verbs"),
    ("BIN_place_adverb", "Place adverbials"),
    ("BIN_time_adverb", "Time adverbials"),
    ("BIN_first_person_pronoun", "First-person pronouns (I/me/my/mine/myself)"),
    ("BIN_second_person_pronoun", "Second-person pronouns (you/your/yourself)"),
    ("BIN_third_person_pronoun", "Third-person pronouns (he/she/it/they)"),
    ("BIN_pronoun_it", "Pronoun 'it'"),
    ("BIN_demonstrative_pronoun", "Demonstrative pronouns (this/that/these/those)"),
    ("BIN_indefinite_pronoun", "Indefinite pronouns (anyone/everyone/something)"),
    ("BIN_nominalization", "Nominalizations (-tion/-ness/-ity/-ment suffixes)"),
    ("BIN_noun_phrase_head", "Noun phrase heads (simple NP density proxy)"),
    ("BIN_gerund_noun", "Gerunds as nouns (-ing nominal)"),
    ("BIN_attributive_adjective", "Attributive adjectives (pre-nominal)"),
    ("BIN_predicative_adjective", "Predicative adjectives (post-copula)"),
    ("BIN_adverb_rate", "General adverb rate"),
    ("BIN_modal_possibility", "Possibility modals (can/could/may/might)"),
    ("BIN_modal_necessity", "Necessity modals (must/shall/should/need)"),
    ("BIN_modal_predictive", "Predictive modals (will/would)"),
    ("BIN_public_verb", "Public verbs (say/show/tell/argue/claim)"),
    ("BIN_private_verb", "Private verbs (think/know/believe/feel/find)"),
    ("BIN_suasive_verb", "Suasive verbs (suggest/recommend/require/propose)"),
    ("BIN_seem_appear", "Seem/appear (stance verbs)"),
    ("BIN_that_clause_verb", "Verb + that-clause complement"),
    ("BIN_that_clause_adjective", "Adjective + that-clause complement"),
    ("BIN_that_clause_noun", "Noun + that-clause complement"),
    ("BIN_to_clause_verb", "Verb + to-infinitive complement"),
    ("BIN_to_clause_adjective", "Adjective + to-infinitive complement"),
    ("BIN_wh_clause", "WH-clause complement"),
    ("BIN_pied_piping", "Pied-piping (preposition stranding avoidance)"),
    ("BIN_prep_phrase", "Prepositional phrases per sentence"),
    ("BIN_adj_attr_count", "Attributive adjective count (raw rate)"),
    ("BIN_type_token_ratio", "Type-token ratio (lexical diversity proxy)"),
    ("BIN_word_length_mean", "Mean word length in characters"),
    ("BIN_coordinating_conjunction", "Coordinating conjunctions (and/but/or)"),
    ("BIN_subordinating_conjunction", "Subordinating conjunctions (because/although/while)"),
    ("BIN_conditional_subordinator", "Conditional subordinators (if/unless)"),
    ("BIN_concessive_adverb", "Concessive adverbs (however/nevertheless/nonetheless)"),
    ("BIN_discourse_particle", "Discourse particles (well/now/of_course/I_mean)"),
    ("BIN_hedge", "Hedges (maybe/perhaps/possibly/probably/sort_of/kind_of)"),
    ("BIN_amplifier", "Amplifiers (very/really/absolutely/extremely)"),
    ("BIN_downtoner", "Downtoners (almost/barely/hardly/merely/nearly)"),
    ("BIN_emphatic", "Emphatics (for_sure/a_lot/such_a)"),
    ("BIN_agentless_passive", "Agentless passives"),
    ("BIN_by_passive", "By-passives (passive with by-phrase)"),
    ("BIN_passive_participle", "Passive participial clauses (past part. post-NP)"),
    ("BIN_existential_there", "Existential there (there is/there are)"),
    ("BIN_demonstrative_that", "Demonstrative 'that' (not complementizer)"),
    ("BIN_stranded_preposition", "Stranded prepositions (end-of-clause prep)"),
    ("BIN_split_infinitive", "Split infinitives (to X verb)"),
    ("BIN_split_auxiliary", "Split auxiliaries (e.g. have always been)"),
    ("BIN_sentence_length_mean", "Mean sentence length in words"),
    ("BIN_question_rate", "Direct question rate"),
    ("BIN_direct_speech", "Direct speech / quoted material rate"),
    ("BIN_contraction", "Contraction rate (n't/'ve/'ll etc.)"),
    ("BIN_negation", "Negation rate (not/never/no)"),
    ("BIN_analytic_negation", "Analytic negation (not separate token)"),
    ("BIN_synthetic_negation", "Synthetic negation (-n't clitic)"),
    ("BIN_causative_adverbial", "Causative adverbials (because/since/as)"),
    ("BIN_concessive_clause", "Concessive clauses (although/though/even_though)"),
    ("BIN_conditional_clause", "Conditional clauses (if/unless/provided_that)"),
    ("BIN_relative_clause_subject", "Relative clauses on subject NP"),
    ("BIN_relative_clause_object", "Relative clauses on object NP"),
    ("BIN_wh_relative_subject", "WH relative clauses on subject (who/which)"),
    ("BIN_wh_relative_object", "WH relative clauses on object (who/which)"),
    ("BIN_that_relative", "That-relative clauses"),
    ("BIN_zero_relative", "Zero relative clauses (omitted relativizer)"),
    ("BIN_comparative_adj", "Comparative adjectives (-er/more)"),
    ("BIN_superlative_adj", "Superlative adjectives (-est/most)"),
    ("BIN_attributive_adj_superlative", "Attributive superlative adjectives"),
    ("BIN_predicative_adj_superlative", "Predicative superlative adjectives"),
    ("BIN_verb_nominalization", "Verb-derived nominalizations (-tion/-sion/-ment)"),
    ("BIN_adjective_nominalization", "Adjective-derived nominalizations (-ness/-ity/-acy)"),
    ("BIN_participle_clause", "Participial clauses (ed/ing adjuncts)"),
    ("BIN_adverbial_subordinate", "Adverbial subordinate clauses (all types)"),
    ("BIN_noun_complement", "Noun complement clauses (fact_that/idea_that)"),
    ("BIN_final_preposition", "Final prepositions (preposition at sentence end)"),
    ("BIN_extraposed_subject", "Extraposed subjects (it is important that)"),
    ("BIN_passive_agentless_short", "Short passives without agent (agentless subset)"),
    ("BIN_discourse_new_this", "Discourse-new demonstrative 'this'"),
    ("BIN_discourse_new_that", "Discourse-old demonstrative 'that'"),
    ("BIN_first_person_plural", "First-person plural (we/us/our/ours/ourselves)"),
    ("BIN_third_person_animate", "Third-person animate (he/she/him/her)"),
    ("BIN_third_person_inanimate", "Third-person inanimate (it/its/itself)"),
    ("BIN_indefinite_article", "Indefinite article (a/an)"),
    ("BIN_definite_article", "Definite article (the)"),
    ("BIN_numeral", "Numeral rate (cardinal + ordinal)"),
    ("BIN_foreign_word", "Foreign word / loan rate"),
    ("BIN_modal_epistemic", "Epistemic modals (may/might/could/would in epistemic use)"),
    ("BIN_modal_deontic", "Deontic modals (must/should/shall/need in deontic use)"),
    ("BIN_stance_adverb", "Stance adverbs (certainly/apparently/obviously/frankly)"),
    ("BIN_focus_particle", "Focus particles (only/even/just/also/at_least)"),
    ("BIN_question_tag", "Question tags (isn't it/don't you)"),
    ("BIN_imperative", "Imperative clauses (bare verb at sentence start)"),
    ("BIN_passive_progressive", "Progressive passive constructions (is being done)"),
)

# Verify the schema has exactly 96 entries — a load-time assertion so any
# accidental edit is caught on import, not only when the test runs.
assert len(BIBER_FEATURE_SCHEMA) == 96, (
    f"BIBER_FEATURE_SCHEMA must have exactly 96 entries; found {len(BIBER_FEATURE_SCHEMA)}"
)
# Verify all ids are unique.
_schema_ids = [fid for fid, _ in BIBER_FEATURE_SCHEMA]
assert len(_schema_ids) == len(set(_schema_ids)), (
    "BIBER_FEATURE_SCHEMA contains duplicate feature_ids"
)


def biber_family_features(vector: dict[str, float]) -> dict[str, float]:
    """Normalize a raw Biber vector against BIBER_FEATURE_SCHEMA.

    Rules:
    - Every schema feature id is present in the output (missing → 0.0).
    - Unknown keys NOT in the schema are SILENTLY DROPPED (no raise), so a
      richer-than-schema tagger output is harmless.
    - Output dict keys are exactly the 96 BIBER_FEATURE_SCHEMA ids, in schema order.
    - Pure stdlib, deterministic, no model import.

    Args:
        vector: {feature_id: rate} — any subset/superset of the schema.

    Returns:
        {feature_id: float} — exactly the 96 schema ids, schema order, missing filled 0.0.
    """
    return {fid: float(vector.get(fid, 0.0)) for fid, _ in BIBER_FEATURE_SCHEMA}


# ---------------------------------------------------------------------------
# Claim license helpers
# ---------------------------------------------------------------------------

def _claim_license() -> dict[str, str]:
    """Per-tool does_not_license text for the standalone biber_panel surface.

    Refuses: AI/human provenance, model-family attribution, authorship verdict.
    Names arXiv:2410.16107 dual-use explicitly.
    """
    return {
        "licenses": (
            "Descriptive Biber lexico-grammatical register panel (96 named feature rates) "
            "for the target passage, via the Neurobiber tagger (arXiv:2502.18590). "
            "Reports per-feature rates and (on the comparative path) baseline-relative "
            "z-deviations, Burrows-delta, cosine distances, and a provisional band. "
            "No verdict; thresholds operator-side / PROVISIONAL."
        ),
        "does_not_license": (
            "This result does NOT license an authorship verdict, AI/human provenance "
            "determination, or model-family attribution. "
            "Biber features separate human-vs-LLM AND LLM-vs-LLM "
            "(arXiv:2410.16107) — that dual-use is REFUSED here: do not use this "
            "panel as a labeled feature in any discrimination/attribution surface "
            "(see anti-Goodhart boundary in AGENTS.md). "
            "NOT length-controlled (rates covary with length and genre). "
            "English only. Calibration is PROVISIONAL."
        ),
    }


# ---------------------------------------------------------------------------
# Standalone panel surface (§3b — biber_panel, the dependency_distance_audit shape)
# ---------------------------------------------------------------------------

def run_biber_panel(
    text: str,
    *,
    biber_tagger: Callable[[str], dict[str, float]] | None = None,
    biber_vector: dict[str, float] | None = None,
    target_path: str | Path | None = None,
) -> tuple[dict[str, Any], list[str] | None]:
    """Compute the standalone biber_panel result dict.

    Caller is responsible for ensuring at least one of biber_tagger or
    biber_vector is supplied; if neither is available the caller should use
    build_error_output (the dependency_distance_audit.py:201-206 abstain path
    is handled in _run() via _try_load_real_tagger()).

    Args:
        text: the target text (UTF-8 prose).
        biber_tagger: injectable callable str -> {feature_id: float}.
        biber_vector: precomputed vector (overrides tagger if both supplied).
        target_path: for envelope construction.

    Returns:
        Tuple of (result_dict, warnings_list_or_None).
        result_dict is suitable for build_output's results kwarg.
    """
    raw_vector: dict[str, float]
    if biber_vector is not None:
        raw_vector = biber_vector
        tagger_name = "precomputed"
    elif biber_tagger is not None:
        raw_vector = biber_tagger(text)
        tagger_name = getattr(biber_tagger, "_tagger_name", "stub")
    else:
        # Neither supplied: should not reach here from _run(); used in tests
        # that explicitly pass a tagger. Raise so callers see the contract.
        raise ValueError("run_biber_panel requires biber_vector or biber_tagger")

    features = biber_family_features(raw_vector)

    words = text.split()
    n_words = len(words)
    warnings: list[str] | None = None
    if n_words < 150:
        warnings = [
            f"target has only {n_words} words (< 150); Biber rates may be unstable at this length"
        ]

    result: dict[str, Any] = {
        "biber_panel": {
            "n_features": len(BIBER_FEATURE_SCHEMA),
            "features": features,
            "tagger": tagger_name,
            "calibration_status": "provisional",
            "assumptions": {
                "method": "Biber lexico-grammatical register panel (Neurobiber, arXiv:2502.18590)",
                "schema": "96 named Biber features, fixed order (BIBER_FEATURE_SCHEMA)",
                "length_confound": (
                    "feature rates are length- and genre-sensitive; not length-controlled"
                ),
                "tagger_tier": (
                    "model-CPU (Neurobiber) in production; "
                    "deterministic injected/stub vector in M1/CI"
                ),
            },
        },
        "n_words": n_words,
    }
    return result, warnings


def _run(args: argparse.Namespace) -> dict[str, Any]:
    """Execute the standalone biber_panel surface from CLI args."""
    target_path = Path(args.target)
    try:
        text = target_path.read_text(encoding="utf-8")
    except OSError as e:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=f"cannot read target: {e}",
            reason_category="bad_input",
        )

    # M1 production default: no tagger wired, no model installed → abstain.
    # M2: pass a real NeurobiberTagger instance via --tagger-module (out of scope here;
    # the seam is the biber_tagger kwarg in run_biber_panel).
    biber_tagger = _try_load_real_tagger()
    if biber_tagger is None:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=(
                "Biber tagger not available. Install the neurobiber package "
                "(pip install neurobiber) or torch+transformers with the Neurobiber "
                "checkpoint to run this surface."
            ),
            reason_category="missing_dependency",
        )

    result, warnings = run_biber_panel(text, biber_tagger=biber_tagger)

    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=str(target_path), target_words=result["n_words"], baseline=None,
        results={k: v for k, v in result.items() if k != "n_words"},
        claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        target_extra={},
        warnings=warnings,
        validate_bounds=True,
    )


def _try_load_real_tagger() -> Callable[[str], dict[str, float]] | None:
    """Attempt to load the real Neurobiber tagger (M2 path).

    Returns None when torch/transformers/neurobiber are absent (the M1/CI
    default). Keeps the import lazy — never called in CI.

    M2 body (deferred, out of scope for this build): instantiate a
    _NeurobiberTagger whose _load() does:
        import torch
        import transformers
        ...
    modelled on voice_fingerprint._LUAREncoder._load() (voice_fingerprint.py:196-201).
    """
    try:
        import neurobiber  # type: ignore  # noqa: F401
        raise NotImplementedError(
            "M2 neurobiber tagger integration is deferred; "
            "implement _NeurobiberTagger here when the package is ready."
        )
    except ImportError:
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("target", help="Path to the target text file.")
    ap.add_argument("--json", action="store_true", help="Emit the JSON envelope to stdout.")
    ap.add_argument("--out", help="Write the JSON envelope to this path.")
    args = ap.parse_args(argv)

    envelope = _run(args)
    text = json.dumps(envelope, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.json or not args.out:
        print(text)
    return 0 if envelope.get("available", True) else 3


if __name__ == "__main__":
    raise SystemExit(main())
