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
M2 (VP-RS2): the real Neurobiber tagger (Blablablab/neurobiber, a RoBERTa multi-label
  classifier) behind a lazy torch/transformers import. Each feature's value is the mean
  sigmoid probability across the passage's chunks (the model card thresholds the max at
  0.5; the mean keeps a graded value for distances), and the share of chunks where the
  feature is detected is reported beside it.

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

from setec.contract.output_schema import build_error_output, build_output  # noqa: E402
from setec.contract.claim_license import from_legacy  # noqa: E402

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
# Ids and order are the real Neurobiber output labels: biberplus
# BIBER_PLUS_TAGS with the BIN_ prefix, identical to the checkpoint's
# config.id2label (Blablablab/neurobiber). The real tagger checks its
# id2label against this tuple and refuses to run on any mismatch.
# Labels are short human-readable glosses of the biberplus tag rules.
# ---------------------------------------------------------------------------

BIBER_FEATURE_SCHEMA: tuple[tuple[str, str], ...] = (
    ("BIN_QUAN", "Quantifiers (each/all/every/many/much/few/several/some/any)"),
    ("BIN_QUPR", "Quantifier pronouns (everybody/somebody/anything/nothing ...)"),
    ("BIN_AMP", "Amplifiers (absolutely/extremely/completely/totally/very ...)"),
    ("BIN_PASS", "Agentless passives"),
    ("BIN_XX0", "Analytic negation (not / n't)"),
    ("BIN_JJ", "Attributive adjectives"),
    ("BIN_BEMA", "Be as main verb"),
    ("BIN_CAUS", "Causative adverbial subordinator (because)"),
    ("BIN_CONC", "Concessive adverbial subordinators (although/though)"),
    ("BIN_COND", "Conditional adverbial subordinators (if/unless)"),
    ("BIN_CONJ", "Conjuncts (however/therefore/thus/furthermore ...)"),
    ("BIN_CONT", "Contractions"),
    ("BIN_DPAR", "Discourse particles (well/now/anyhow/anyways, clause-initial)"),
    ("BIN_DWNT", "Downtoners (almost/barely/hardly/merely/nearly/only ...)"),
    ("BIN_EX", "Existential there"),
    ("BIN_FPP1", "First-person pronouns"),
    ("BIN_GER", "Gerunds"),
    ("BIN_RB", "Adverbs (any)"),
    ("BIN_PIN", "Prepositional phrases (total prepositions)"),
    ("BIN_INPR", "Indefinite pronouns (anybody/anyone/everything/nobody ...)"),
    ("BIN_TO", "Infinitives (to + verb)"),
    ("BIN_NEMD", "Necessity modals (ought/should/must)"),
    ("BIN_OSUB", "Other adverbial subordinators (since/while/whereas/so that ...)"),
    ("BIN_PASTP", "Past participial clauses"),
    ("BIN_VBD", "Past tense verbs"),
    ("BIN_PHC", "Phrasal coordination (and joining like categories)"),
    ("BIN_PIRE", "Pied-piping relative clauses (preposition + who/whom/whose/which)"),
    ("BIN_PLACE", "Place adverbials"),
    ("BIN_POMD", "Possibility modals (can/may/might/could)"),
    ("BIN_PRMD", "Predictive modals (will/would/shall)"),
    ("BIN_WZPRES", "Present participial WHIZ deletion relatives"),
    ("BIN_VPRT", "Present tense verbs"),
    ("BIN_PRIV", "Private verbs (think/know/believe/feel/find ...)"),
    ("BIN_PIT", "Pronoun it"),
    ("BIN_PUBV", "Public verbs (say/tell/argue/claim/report ...)"),
    ("BIN_SPP2", "Second-person pronouns (incl. thou/thee/thy)"),
    ("BIN_SMP", "Seem / appear"),
    ("BIN_SERE", "Sentence relatives (, which)"),
    ("BIN_STPR", "Stranded prepositions"),
    ("BIN_SUAV", "Suasive verbs (suggest/recommend/insist/propose ...)"),
    ("BIN_SYNE", "Synthetic negation (no/neither/nor + noun or adjective)"),
    ("BIN_TPP3", "Third-person personal pronouns (excluding it)"),
    ("BIN_TIME", "Time adverbials"),
    ("BIN_NOMZ", "Nominalizations (-tion/-ment/-ness/-ity)"),
    ("BIN_BYPA", "By-passives"),
    ("BIN_PRED", "Predicative adjectives"),
    ("BIN_TOBJ", "That relative clauses on object position"),
    ("BIN_TSUB", "That relative clauses on subject position"),
    ("BIN_THVC", "That verb complements"),
    ("BIN_NN", "Other nouns (not nominalizations or gerunds)"),
    ("BIN_DEMP", "Demonstrative pronouns"),
    ("BIN_DEMO", "Demonstratives (determiner use)"),
    ("BIN_WHQU", "Direct WH-questions"),
    ("BIN_EMPH", "Emphatics (for sure/a lot/such a/really/just ...)"),
    ("BIN_HDG", "Hedges (at about/something like/more or less/almost/maybe)"),
    ("BIN_WZPAST", "Past participial WHIZ deletion relatives"),
    ("BIN_THAC", "That adjective complements"),
    ("BIN_PEAS", "Perfect aspect"),
    ("BIN_ANDC", "Independent clause coordination (clause-initial and)"),
    ("BIN_PRESP", "Present participial clauses"),
    ("BIN_PROD", "Pro-verb do"),
    ("BIN_SPAU", "Split auxiliaries"),
    ("BIN_SPIN", "Split infinitives"),
    ("BIN_THATD", "Subordinator that deletion"),
    ("BIN_WHOBJ", "WH relative clauses on object position"),
    ("BIN_WHSUB", "WH relative clauses on subject position"),
    ("BIN_WHCL", "WH clauses (after public/private/suasive verbs)"),
    ("BIN_ART", "Articles"),
    ("BIN_AUXB", "Auxiliary be"),
    ("BIN_CAP", "Capitalized words"),
    ("BIN_SCONJ", "Subordinating conjunctions"),
    ("BIN_CCONJ", "Coordinating conjunctions"),
    ("BIN_DET", "Determiners"),
    ("BIN_EMOJ", "Emoji"),
    ("BIN_EMOT", "Emoticons"),
    ("BIN_EXCL", "Exclamation marks"),
    ("BIN_HASH", "Hashtags"),
    ("BIN_INF", "Infinitive verb forms"),
    ("BIN_UH", "Interjections"),
    ("BIN_NUM", "Numerals"),
    ("BIN_LAUGH", "Laughter acronyms (lol/haha ...)"),
    ("BIN_PRP", "Possessive pronouns"),
    ("BIN_PREP", "Prepositions"),
    ("BIN_NNP", "Proper nouns"),
    ("BIN_QUES", "Question marks"),
    ("BIN_QUOT", "Quotation marks"),
    ("BIN_AT", "@-mentions"),
    ("BIN_SBJP", "Subject pronouns"),
    ("BIN_URL", "URLs"),
    ("BIN_WH", "WH words"),
    ("BIN_INDA", "Indefinite articles"),
    ("BIN_ACCU", "Accusative-case pronouns"),
    ("BIN_PGAS", "Progressive aspect"),
    ("BIN_CMADJ", "Comparative adjectives"),
    ("BIN_SPADJ", "Superlative adjectives"),
    ("BIN_X", "Tokens with no part-of-speech tag (other)"),
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
    detail: dict[str, Any] | None = None
    if biber_vector is not None:
        raw_vector = biber_vector
        tagger_name = "precomputed"
    elif biber_tagger is not None and hasattr(biber_tagger, "detail"):
        raw_vector, shares, n_chunks = biber_tagger.detail(text)
        detail = {"detection_share": biber_family_features(shares), "n_chunks": n_chunks}
        tagger_name = getattr(biber_tagger, "_tagger_name", "stub")
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
            **(detail or {}),
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
                "feature_value": (
                    "Neurobiber: mean per-chunk sigmoid probability that the feature is "
                    "present (not a per-word rate); detection_share is the share of "
                    "chunks above 0.5"
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

    biber_tagger = _try_load_real_tagger(chunk_words=args.chunk_words, device=args.device)
    if biber_tagger is None:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=(
                "Biber tagger not available. The Neurobiber tagger needs torch and "
                "transformers (pip install -r requirements-surprisal.txt) and the "
                f"{NEUROBIBER_MODEL_ID} checkpoint (downloaded on first use)."
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


NEUROBIBER_MODEL_ID = "Blablablab/neurobiber"
NEUROBIBER_MAX_TOKENS = 512  # trained with max_length=512 (model card)
DEFAULT_CHUNK_WORDS = 512    # model card chunks on whitespace words before tokenizing


class _NeurobiberTagger:
    """Real Neurobiber tagger (M2). Never loaded in CI.

    Follows the model card: split on whitespace into ``chunk_words`` chunks,
    tokenize each to at most 512 tokens, sigmoid the 96 logits. Where the card
    thresholds the max over chunks at 0.5, this keeps the mean probability
    (graded, so baseline distances move smoothly) and reports the share of
    chunks above 0.5 beside it.
    """

    _tagger_name = "neurobiber"

    def __init__(self, *, chunk_words: int = DEFAULT_CHUNK_WORDS, device: str | None = None,
                 batch_size: int = 16) -> None:
        import torch  # type: ignore
        from transformers import AutoModelForSequenceClassification, AutoTokenizer  # type: ignore

        self._torch = torch
        self._tok = AutoTokenizer.from_pretrained(NEUROBIBER_MODEL_ID, use_fast=True)
        self._model = AutoModelForSequenceClassification.from_pretrained(NEUROBIBER_MODEL_ID)
        self._model.eval()
        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._model.to(self._device)
        id2label = {int(k): v for k, v in self._model.config.id2label.items()}
        self._ids = [id2label[i] for i in range(len(id2label))]
        if self._ids != [fid for fid, _ in BIBER_FEATURE_SCHEMA]:
            raise RuntimeError(
                f"{NEUROBIBER_MODEL_ID} id2label does not match BIBER_FEATURE_SCHEMA; "
                "refusing to map features by position"
            )
        self._chunk_words = max(1, int(chunk_words))
        self._batch_size = batch_size

    def detail(self, text: str) -> tuple[dict[str, float], dict[str, float], int]:
        torch = self._torch
        words = text.split()
        chunks = [" ".join(words[i:i + self._chunk_words])
                  for i in range(0, len(words), self._chunk_words)]
        if not chunks:
            zeros = {fid: 0.0 for fid in self._ids}
            return zeros, dict(zeros), 0
        probs = []
        with torch.no_grad():
            for i in range(0, len(chunks), self._batch_size):
                enc = self._tok(chunks[i:i + self._batch_size], return_tensors="pt", padding=True,
                                truncation=True, max_length=NEUROBIBER_MAX_TOKENS).to(self._device)
                probs.append(torch.sigmoid(self._model(**enc).logits.float()).cpu())
        p = torch.cat(probs, dim=0)
        means = p.mean(dim=0).tolist()
        shares = (p > 0.5).float().mean(dim=0).tolist()
        return (dict(zip(self._ids, means)), dict(zip(self._ids, shares)), len(chunks))

    def __call__(self, text: str) -> dict[str, float]:
        return self.detail(text)[0]


def _try_load_real_tagger(*, chunk_words: int = DEFAULT_CHUNK_WORDS,
                          device: str | None = None) -> Callable[[str], dict[str, float]] | None:
    """Load the real Neurobiber tagger, or None when it cannot run here.

    None (never a raise) keeps the CLI's missing_dependency abstention intact:
    torch/transformers absent, or the checkpoint unavailable (offline, no
    cache). Whether the separate ``neurobiber`` pip package is installed does
    not matter; the tagger loads the checkpoint through transformers.
    """
    try:
        import torch  # type: ignore  # noqa: F401
        import transformers  # type: ignore  # noqa: F401
    except ImportError:
        return None
    try:
        return _NeurobiberTagger(chunk_words=chunk_words, device=device)
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("target", help="Path to the target text file.")
    ap.add_argument("--json", action="store_true", help="Emit the JSON envelope to stdout.")
    ap.add_argument("--out", help="Write the JSON envelope to this path.")
    ap.add_argument("--chunk-words", type=int, default=DEFAULT_CHUNK_WORDS,
                    help="Whitespace words per Neurobiber chunk (default 512, as the model card).")
    ap.add_argument("--device", default=None, help="torch device for the tagger (default: cuda if available).")
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
