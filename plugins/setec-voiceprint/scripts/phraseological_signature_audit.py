#!/usr/bin/env python3
"""phraseological_signature_audit.py — phrase-frame mining over
the writer's reusable language frames (paired-release schedule
Release 11, Surfaces Tier 3).

The framework's `idiolect_detector.py` answers the keyness
question: *which words and phrases are over-represented in this
writer's prose?* This module asks the complementary phraseology
question: *what reusable language frames does this writer build
with?* Same surface (phrase-level material), different unit
(frames vs. tokens).

The shape difference matters:

  - **Keyness** finds tokens. "snowdrift" appears 23 times in the
    writer's baseline; "kerosene lamp" appears 17. The diagnostic
    is *what is preserved or lost*.
  - **Phraseology** finds *frames*. The writer reuses the slot-
    frame "not because X but because Y" with 14 different X/Y
    fillers. The diagnostic is *what shape of construction the
    writer reaches for repeatedly*. Two writers can share zero
    surface phrases and still differ at the frame level.

Five categories tracked (v1):

  1. **lexical_bundles** — fixed n-gram strings that recur in
     the baseline (3-grams and 4-grams, ≥ minimum-occurrence
     threshold). The writer's stable building blocks.
  2. **slot_frames** — phrase frames with one or more variable
     positions ("not just X but Y", "the X of the Y", "X, if X,
     is Y"). Detected via curated structural templates with
     fixed function-word anchors and variable content slots.
  3. **idioms** — set phrases from a curated English-idiom list
     (allusion to literary or proverbial sources). The writer's
     idiomatic register.
  4. **hapax_phrase_survival** — phrases that appear once in the
     baseline AND once in the target. The single-occurrence
     preservation case; specifically informative because hapax
     items are the most contingent — surviving them tracks the
     writer's idiosyncratic memory.
  5. **stance_intensifier_frames** — preferred stance / hedging
     / intensifier patterns ("really very", "perhaps it is
     that", "actually quite"). Voice-bearing functional reuse
     that idiolect_detector's word-level keyness misses.

For each category the audit reports:

  - **frames detected** in the target (per-frame counts and
    examples from the baseline)
  - **survival rate** of baseline frames in the target (frames
    that recur AND appear in the target)
  - **density** per 1,000 target words

Pairs naturally with idiolect_detector (same input shape: target
+ baseline-dir; output: phrase-level survival information). The
two compose into a complete phrase-level voice surface — keyness
reports tokens, phraseology reports frames.

Usage:

    python3 scripts/phraseological_signature_audit.py target.md \\
        --baseline-dir baseline/

    python3 scripts/phraseological_signature_audit.py target.md \\
        --baseline-dir baseline/ --category slot_frames --json

task_surface: voice_coherence. The audit refuses provenance
verdicts. Frame reuse is voice-coherence evidence, not authorship
certification.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore


TASK_SURFACE = "voice_coherence"
TOOL_NAME = "phraseological_signature_audit"
SCRIPT_VERSION = "1.0"

# Default categories tracked. Public so the CLI's argparse
# `choices` list and external callers can validate filter names
# without depending on private state.
CATEGORY_KEYS: tuple[str, ...] = (
    "lexical_bundles",
    "slot_frames",
    "idioms",
    "hapax_phrase_survival",
    "stance_intensifier_frames",
)


# ---------- Tokenization ----------


_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens for n-gram + frame extraction.
    Apostrophes and hyphens are preserved inside words so
    contractions and hyphenated compounds register as single
    tokens."""
    return [m.group(0).lower() for m in _WORD_RE.finditer(text)]


def _strip_blockquotes(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith(">")
    )


# ---------- Lexical bundles ----------


def _ngrams(tokens: list[str], n: int) -> Iterable[tuple[str, ...]]:
    for i in range(len(tokens) - n + 1):
        yield tuple(tokens[i : i + n])


def extract_lexical_bundles(
    tokens: list[str],
    *,
    n_values: tuple[int, ...] = (3, 4),
    min_count: int = 2,
) -> dict[tuple[str, ...], int]:
    """Return {ngram: count} for n-grams appearing at least
    ``min_count`` times. The framework's lexical-bundle
    convention: 3- and 4-grams that recur within the writer's
    baseline are the writer's stable phrase-level building
    blocks."""
    counts: Counter[tuple[str, ...]] = Counter()
    for n in n_values:
        for ng in _ngrams(tokens, n):
            counts[ng] += 1
    return {ng: c for ng, c in counts.items() if c >= min_count}


# ---------- Slot frames ----------


# Slot-frame patterns. Each frame is named, fixed function-word
# anchors are literal, variable slots are matched as `\S+`
# (non-whitespace runs). The patterns are case-insensitive.
_SLOT_FRAME_PATTERNS: tuple[tuple[str, str, re.Pattern], ...] = (
    (
        "not_X_but_Y",
        "Not just X, but Y / not because X but because Y / "
        "not only X but also Y",
        re.compile(
            r"\bnot\s+(?:just|only|merely|because|simply)?\s*"
            r"\S+(?:\s+\S+){0,5}?\s*[,;]?\s*but\s+"
            r"(?:also|even|because|rather)?\s*\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "the_X_of_the_Y",
        "The X of the Y (e.g., \"the heart of the matter\", "
        "\"the shape of the thing\")",
        re.compile(
            r"\bthe\s+\S+\s+of\s+the\s+\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "as_X_as_Y",
        "As X as Y (\"as much as it costs\", \"as far as I "
        "can tell\")",
        re.compile(
            r"\bas\s+\S+(?:\s+\S+){0,3}?\s+as\s+\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "X_if_X_is_Y",
        "X, if X, is Y (\"the question, if it is one, is\")",
        re.compile(
            r"\b\w+,\s+if\s+\w+(?:\s+\w+){0,4}?,\s+(?:is|are|was|were)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "what_X_is_Y",
        "What X is Y / what matters is Y (pseudo-cleft frames)",
        re.compile(
            r"\bWhat\s+\S+(?:\s+\S+){0,5}?\s+(?:is|are|was|were|matters)\s+\S+",
        ),
    ),
    (
        "neither_X_nor_Y",
        "Neither X nor Y",
        re.compile(
            r"\bneither\s+\S+(?:\s+\S+){0,5}?\s+nor\s+\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "either_X_or_Y",
        "Either X or Y",
        re.compile(
            r"\beither\s+\S+(?:\s+\S+){0,5}?\s+or\s+\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "between_X_and_Y",
        "Between X and Y",
        re.compile(
            r"\bbetween\s+\S+(?:\s+\S+){0,3}?\s+and\s+\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "from_X_to_Y",
        "From X to Y",
        re.compile(
            r"\bfrom\s+\S+(?:\s+\S+){0,3}?\s+to\s+\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "more_X_than_Y",
        "More X than Y",
        re.compile(
            r"\bmore\s+\S+(?:\s+\S+){0,3}?\s+than\s+\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "less_X_than_Y",
        "Less X than Y",
        re.compile(
            r"\bless\s+\S+(?:\s+\S+){0,3}?\s+than\s+\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "the_more_X_the_more_Y",
        "The more X, the more Y",
        re.compile(
            r"\bthe\s+more\s+\S+(?:\s+\S+){0,5}?[,;]?\s+the\s+more\s+\S+",
            re.IGNORECASE,
        ),
    ),
)


def extract_slot_frame_hits(
    text: str,
) -> dict[str, list[str]]:
    """Return {frame_name: [matched_substring, ...]} across all
    slot-frame patterns."""
    out: dict[str, list[str]] = {}
    for name, _description, regex in _SLOT_FRAME_PATTERNS:
        hits = [m.group(0) for m in regex.finditer(text)]
        if hits:
            out[name] = hits
    return out


def slot_frame_descriptions() -> dict[str, str]:
    return {
        name: description
        for name, description, _regex in _SLOT_FRAME_PATTERNS
    }


# ---------- Idioms ----------


# Curated English idioms — set phrases that read as voice-bearing
# when present, and as voice-loss when uniformly absent across a
# writer's revision. Hand-picked for stylistic load (these are
# the phrases a writer either reaches for or doesn't); intended
# as a v1 reasonable-scope list, not an exhaustive corpus.
_IDIOMS: tuple[str, ...] = (
    "all things considered",
    "as it were",
    "at the end of the day",
    "be that as it may",
    "beg the question",
    "by all accounts",
    "by and large",
    "come to terms with",
    "cut from the same cloth",
    "for all intents and purposes",
    "for what it's worth",
    "give or take",
    "in a manner of speaking",
    "in the long run",
    "in the short run",
    "in the same breath",
    "leave no stone unturned",
    "more or less",
    "needless to say",
    "no less",
    "now and then",
    "of a piece",
    "of late",
    "on balance",
    "on the contrary",
    "on the face of it",
    "on the one hand",
    "on the other hand",
    "once and for all",
    "out of hand",
    "par for the course",
    "point of departure",
    "rule of thumb",
    "speak volumes",
    "stand to reason",
    "take stock of",
    "the case in point",
    "the heart of the matter",
    "to be sure",
    "to put it mildly",
    "when all is said and done",
    "when in rome",
    "with the benefit of hindsight",
    "without further ado",
)

_IDIOM_REGEXES: tuple[tuple[str, re.Pattern], ...] = tuple(
    (
        idiom,
        re.compile(
            r"\b"
            + re.escape(idiom).replace(r"\ ", r"\s+")
            + r"\b",
            re.IGNORECASE,
        ),
    )
    for idiom in _IDIOMS
)


def extract_idiom_hits(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for idiom, regex in _IDIOM_REGEXES:
        n = sum(1 for _ in regex.finditer(text))
        if n > 0:
            out[idiom] = n
    return out


# ---------- Stance / intensifier frames ----------


# Functional frames the writer reuses for stance / hedging /
# intensifying. Curated short list — intended to capture the most
# voice-bearing reuse patterns rather than every attested form.
_STANCE_FRAME_PATTERNS: tuple[tuple[str, str, re.Pattern], ...] = (
    (
        "really_very",
        "Doubled intensifier (\"really very tired\", \"actually "
        "quite happy\").",
        re.compile(
            r"\b(?:really|actually|quite|rather|fairly|"
            r"genuinely|truly|particularly)\s+"
            r"(?:very|quite|rather|extremely|deeply|surprisingly)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "perhaps_it_is_that",
        "Hedged claim opener (\"perhaps it is that\", \"maybe "
        "it is the case that\").",
        re.compile(
            r"\b(?:perhaps|maybe|possibly)\s+it\s+is\s+(?:that|the)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "I_can_only_X",
        "Constrained-action frame (\"I can only assume\", \"one "
        "can only imagine\").",
        re.compile(
            r"\b(?:I|one|we)\s+can\s+only\s+\S+",
            re.IGNORECASE,
        ),
    ),
    (
        "the_question_is_whether",
        "Question-foregrounding frame (\"the question is whether\").",
        re.compile(
            r"\bthe\s+question\s+is\s+whether\b",
            re.IGNORECASE,
        ),
    ),
    (
        "it_seems_to_me",
        "Soft-claim frame (\"it seems to me\", \"it seems to me "
        "that\").",
        re.compile(
            r"\bit\s+seems\s+to\s+(?:me|us)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "what_is_X_is_Y",
        "Predicative-emphasis frame (\"what is striking is\", "
        "\"what matters is\").",
        re.compile(
            r"\bwhat\s+is\s+\S+\s+is\b",
            re.IGNORECASE,
        ),
    ),
    (
        "to_X_a_Y",
        "Loose-frame qualifier (\"to be honest\", \"to put it "
        "simply\", \"to use a phrase\").",
        re.compile(
            r"\bto\s+(?:be|put|use|borrow|coin)\s+\S+(?:\s+\S+){0,3}?",
            re.IGNORECASE,
        ),
    ),
)


def extract_stance_frame_hits(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for name, _description, regex in _STANCE_FRAME_PATTERNS:
        hits = [m.group(0) for m in regex.finditer(text)]
        if hits:
            out[name] = hits
    return out


def stance_frame_descriptions() -> dict[str, str]:
    return {
        name: description
        for name, description, _regex in _STANCE_FRAME_PATTERNS
    }


# ---------- Hapax phrase survival ----------


def extract_hapax_phrases(
    tokens: list[str],
    *,
    n: int = 3,
) -> set[tuple[str, ...]]:
    """Return n-grams that appear EXACTLY once in the corpus.

    Hapax legomena at the phrase level. By definition contingent
    — but their survival from baseline to target tracks the
    writer's idiosyncratic phrase memory more sharply than
    high-frequency bundles do.
    """
    counts: Counter[tuple[str, ...]] = Counter(_ngrams(tokens, n))
    return {ng for ng, c in counts.items() if c == 1}


def hapax_survival_rate(
    *,
    baseline_hapax: set[tuple[str, ...]],
    target_tokens: list[str],
    n: int = 3,
) -> tuple[int, int, list[tuple[str, ...]]]:
    """Return ``(n_surviving, n_baseline_hapax, sample_survivors)``.

    A hapax phrase from the baseline "survives" if it appears
    at least once in the target.
    """
    target_ngrams = set(_ngrams(target_tokens, n))
    survivors = [
        ng for ng in baseline_hapax if ng in target_ngrams
    ]
    return (
        len(survivors),
        len(baseline_hapax),
        survivors[:50],
    )


# ---------- Top-level audit ----------


@dataclass
class CategoryReport:
    name: str
    description: str
    target_count: int = 0
    target_density_per_1k: float = 0.0
    items: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def audit_phraseology(
    *,
    target_text: str,
    baseline_texts: list[str] | None = None,
    keep_quotes: bool = False,
    category_filter: list[str] | None = None,
    bundle_n_values: tuple[int, ...] = (3, 4),
    bundle_min_count: int = 2,
    hapax_n: int = 3,
) -> dict[str, Any]:
    """Run the audit. Each category reports its own counts and
    survival evidence; categories operate independently."""
    if not keep_quotes:
        target_text = _strip_blockquotes(target_text)
        if baseline_texts:
            baseline_texts = [
                _strip_blockquotes(t) for t in baseline_texts
            ]

    target_tokens = _tokenize(target_text)
    target_words = len(target_tokens)
    baseline_tokens: list[str] = []
    if baseline_texts:
        for t in baseline_texts:
            baseline_tokens.extend(_tokenize(t))

    if category_filter:
        unknown = [
            k for k in category_filter if k not in CATEGORY_KEYS
        ]
        if unknown:
            raise ValueError(
                f"Unknown category name(s) in --category: "
                f"{', '.join(repr(k) for k in unknown)}. "
                f"Valid categories: {', '.join(CATEGORY_KEYS)}."
            )
        active = set(category_filter)
    else:
        active = set(CATEGORY_KEYS)

    categories: dict[str, CategoryReport] = {}

    # 1. Lexical bundles.
    if "lexical_bundles" in active:
        baseline_bundles = (
            extract_lexical_bundles(
                baseline_tokens,
                n_values=bundle_n_values,
                min_count=bundle_min_count,
            )
            if baseline_tokens else {}
        )
        target_bundle_counts = Counter()
        for n in bundle_n_values:
            for ng in _ngrams(target_tokens, n):
                if ng in baseline_bundles:
                    target_bundle_counts[ng] += 1
        n_surviving = sum(
            1 for ng, c in target_bundle_counts.items() if c > 0
        )
        density = (
            sum(target_bundle_counts.values())
            / target_words * 1000
            if target_words else 0.0
        )
        sample = sorted(
            baseline_bundles.items(),
            key=lambda kv: -kv[1],
        )[:30]
        categories["lexical_bundles"] = CategoryReport(
            name="lexical_bundles",
            description=(
                "Recurrent 3- and 4-gram strings in the baseline "
                "(min_count default 2). The writer's stable "
                "phrase-level building blocks."
            ),
            target_count=sum(target_bundle_counts.values()),
            target_density_per_1k=density,
            items={
                "n_baseline_bundles": len(baseline_bundles),
                "n_surviving_in_target": n_surviving,
                "survival_rate": (
                    n_surviving / len(baseline_bundles)
                    if baseline_bundles else None
                ),
                "top_baseline_bundles": [
                    {"phrase": " ".join(ng), "baseline_count": c}
                    for ng, c in sample
                ],
            },
        )

    # 2. Slot frames.
    if "slot_frames" in active:
        target_slot_hits = extract_slot_frame_hits(target_text)
        baseline_slot_hits = (
            extract_slot_frame_hits(
                "\n".join(baseline_texts)
            )
            if baseline_texts else {}
        )
        descriptions = slot_frame_descriptions()
        per_frame: dict[str, dict[str, Any]] = {}
        n_target_total = 0
        for name in descriptions:
            t_hits = target_slot_hits.get(name, [])
            b_hits = baseline_slot_hits.get(name, [])
            n_target_total += len(t_hits)
            if t_hits or b_hits:
                per_frame[name] = {
                    "description": descriptions[name],
                    "target_count": len(t_hits),
                    "baseline_count": len(b_hits),
                    "target_examples": t_hits[:10],
                }
        density = (
            n_target_total / target_words * 1000
            if target_words else 0.0
        )
        categories["slot_frames"] = CategoryReport(
            name="slot_frames",
            description=(
                "Phrase frames with one or more variable slots "
                "(\"not X but Y\", \"the X of the Y\"). Curated "
                "structural templates with fixed function-word "
                "anchors."
            ),
            target_count=n_target_total,
            target_density_per_1k=density,
            items={
                "per_frame": per_frame,
                "n_frames_with_baseline_evidence": sum(
                    1 for f in per_frame.values()
                    if f.get("baseline_count", 0) > 0
                ),
            },
        )

    # 3. Idioms.
    if "idioms" in active:
        target_idiom_hits = extract_idiom_hits(target_text)
        baseline_idiom_hits = (
            extract_idiom_hits(
                "\n".join(baseline_texts)
            )
            if baseline_texts else {}
        )
        n_target_total = sum(target_idiom_hits.values())
        density = (
            n_target_total / target_words * 1000
            if target_words else 0.0
        )
        baseline_only = sorted(
            set(baseline_idiom_hits) - set(target_idiom_hits)
        )
        shared = sorted(
            set(baseline_idiom_hits) & set(target_idiom_hits)
        )
        categories["idioms"] = CategoryReport(
            name="idioms",
            description=(
                "Curated English idioms / set phrases. Voice-"
                "bearing register markers; their absence in a "
                "revision can signal voice-flattening."
            ),
            target_count=n_target_total,
            target_density_per_1k=density,
            items={
                "target_idioms": dict(
                    sorted(target_idiom_hits.items())
                ),
                "baseline_idioms": dict(
                    sorted(baseline_idiom_hits.items())
                ),
                "shared_idioms": shared,
                "baseline_only_idioms": baseline_only[:50],
                "n_idiom_dictionary_entries": len(_IDIOMS),
            },
        )

    # 4. Hapax phrase survival.
    if "hapax_phrase_survival" in active:
        baseline_hapax = (
            extract_hapax_phrases(baseline_tokens, n=hapax_n)
            if baseline_tokens else set()
        )
        n_surviving, n_baseline_hapax, sample = hapax_survival_rate(
            baseline_hapax=baseline_hapax,
            target_tokens=target_tokens,
            n=hapax_n,
        )
        survival_rate = (
            n_surviving / n_baseline_hapax
            if n_baseline_hapax else None
        )
        categories["hapax_phrase_survival"] = CategoryReport(
            name="hapax_phrase_survival",
            description=(
                "Phrases (3-grams) that appear exactly once in "
                "the baseline AND at least once in the target. "
                "Hapax legomena at the phrase level — contingent "
                "by definition; their survival tracks "
                "idiosyncratic phrase memory."
            ),
            target_count=n_surviving,
            target_density_per_1k=(
                n_surviving / target_words * 1000
                if target_words else 0.0
            ),
            items={
                "n_baseline_hapax": n_baseline_hapax,
                "n_surviving_in_target": n_surviving,
                "survival_rate": survival_rate,
                "hapax_n": hapax_n,
                "sample_survivors": [
                    " ".join(ng) for ng in sample
                ],
            },
        )

    # 5. Stance / intensifier frames.
    if "stance_intensifier_frames" in active:
        target_stance = extract_stance_frame_hits(target_text)
        baseline_stance = (
            extract_stance_frame_hits(
                "\n".join(baseline_texts)
            )
            if baseline_texts else {}
        )
        descriptions = stance_frame_descriptions()
        per_frame: dict[str, dict[str, Any]] = {}
        n_target_total = 0
        for name in descriptions:
            t_hits = target_stance.get(name, [])
            b_hits = baseline_stance.get(name, [])
            n_target_total += len(t_hits)
            if t_hits or b_hits:
                per_frame[name] = {
                    "description": descriptions[name],
                    "target_count": len(t_hits),
                    "baseline_count": len(b_hits),
                    "target_examples": t_hits[:10],
                }
        density = (
            n_target_total / target_words * 1000
            if target_words else 0.0
        )
        categories["stance_intensifier_frames"] = CategoryReport(
            name="stance_intensifier_frames",
            description=(
                "Preferred stance / hedging / intensifier "
                "frames (\"really very\", \"perhaps it is "
                "that\", \"the question is whether\"). Voice-"
                "bearing functional reuse beyond word-level "
                "keyness."
            ),
            target_count=n_target_total,
            target_density_per_1k=density,
            items={
                "per_frame": per_frame,
            },
        )

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "target_words": target_words,
        "baseline_words": len(baseline_tokens),
        "n_baseline_files": (
            len(baseline_texts) if baseline_texts else 0
        ),
        "categories": {
            name: _category_to_dict(cat)
            for name, cat in categories.items()
        },
        "claim_license": _claim_license_dict(
            target_words=target_words,
            baseline_words=len(baseline_tokens),
            n_categories=len(categories),
        ),
    }


def _category_to_dict(cat: CategoryReport) -> dict[str, Any]:
    return {
        "name": cat.name,
        "description": cat.description,
        "target_count": cat.target_count,
        "target_density_per_1k": cat.target_density_per_1k,
        "items": cat.items,
        "notes": cat.notes,
    }


def _claim_license_dict(
    *,
    target_words: int,
    baseline_words: int,
    n_categories: int,
) -> dict[str, Any]:
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "A phrase-frame mining report over the writer's "
            "reusable language frames. For each of up to five "
            "categories (lexical bundles, slot frames, idioms, "
            "hapax phrase survival, stance / intensifier "
            "frames), the report names the frames detected in "
            "target and baseline, the survival rate, and "
            "per-frame examples. Pairs naturally with "
            "`idiolect_detector` (token-level keyness): same "
            "surface, different unit."
        ),
        does_not_license=(
            "An authorship verdict. Frame reuse is voice-"
            "coherence evidence, not authorship certification. "
            "The slot-frame patterns are heuristic and curated; "
            "the idiom list is non-exhaustive (44 entries in "
            "v1); the stance-frame inventory is a small "
            "voice-bearing-functional-reuse cross-section. "
            "Treat each category's verdict as a signal in the "
            "framework's evidence stack, not as a certified "
            "claim."
        ),
        comparison_set={
            "target_words": target_words,
            "baseline_words": baseline_words,
            "n_categories_active": n_categories,
        },
        additional_caveats=[
            "Lexical bundles are extracted from the baseline "
            "with a default min_count of 2. Larger baselines "
            "produce more reliable bundles; a tiny baseline "
            "may produce few or no bundles by definition.",
            "Slot-frame regexes favor recall over precision. "
            "Some hits may be coincidental; the per-frame "
            "examples list lets the user audit them.",
            "The idiom dictionary is intentionally curated for "
            "stylistic load, not exhaustiveness. Idioms outside "
            "the dictionary are not detected; idiom DENSITY "
            "comparisons should be read against the same "
            "44-entry list on both sides.",
            "Hapax phrase survival is informative only when the "
            "baseline is long enough to contain meaningful "
            "hapax counts. Below a few thousand baseline words, "
            "hapax becomes nearly all baseline n-grams and the "
            "metric loses discriminative value.",
        ],
    )
    return {"rendered": lic.render_block().rstrip()}


# ---------- Markdown rendering ----------


def render_report(audit: dict[str, Any]) -> str:
    categories = audit.get("categories", {})

    lines: list[str] = [
        "# Phraseological signature audit",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Target words:** {audit.get('target_words', 0)}",
        f"**Baseline words:** {audit.get('baseline_words', 0)} "
        f"({audit.get('n_baseline_files', 0)} files)",
        "",
        "## Per-category summary",
        "",
        "| category | target_count | density_per_1k |",
        "|---|---|---|",
    ]
    for name, cat in categories.items():
        lines.append(
            f"| {name} | {cat.get('target_count', 0)} | "
            f"{cat.get('target_density_per_1k', 0):.2f} |"
        )
    lines.append("")

    for name, cat in categories.items():
        lines.append(f"## `{name}`")
        lines.append("")
        lines.append(cat.get("description", ""))
        lines.append("")
        items = cat.get("items", {})
        if name == "lexical_bundles":
            lines.append(
                f"- Baseline bundles found: "
                f"{items.get('n_baseline_bundles', 0)}"
            )
            lines.append(
                f"- Surviving in target: "
                f"{items.get('n_surviving_in_target', 0)}"
            )
            sr = items.get("survival_rate")
            lines.append(
                f"- Survival rate: "
                f"{f'{sr:.2%}' if sr is not None else 'n/a'}"
            )
            top = items.get("top_baseline_bundles", [])
            if top:
                lines.append("")
                lines.append("**Top baseline bundles:**")
                for b in top[:15]:
                    lines.append(
                        f"- `{b['phrase']}` "
                        f"({b['baseline_count']}×)"
                    )
        elif name in {"slot_frames", "stance_intensifier_frames"}:
            per_frame = items.get("per_frame", {})
            for fname, info in per_frame.items():
                lines.append(
                    f"- **{fname}** "
                    f"(target: {info.get('target_count', 0)}, "
                    f"baseline: {info.get('baseline_count', 0)}): "
                    f"{info.get('description', '')}"
                )
                examples = info.get("target_examples", [])
                for ex in examples[:3]:
                    lines.append(f"  - example: `{ex}`")
        elif name == "idioms":
            target_idioms = items.get("target_idioms", {})
            shared = items.get("shared_idioms", [])
            baseline_only = items.get("baseline_only_idioms", [])
            if target_idioms:
                lines.append("")
                lines.append("**Target idioms detected:**")
                for idiom, count in sorted(target_idioms.items()):
                    lines.append(f"- `{idiom}` ({count}×)")
            if shared:
                lines.append("")
                lines.append("**Shared idioms (in both):**")
                for idiom in shared[:25]:
                    lines.append(f"- `{idiom}`")
            if baseline_only:
                lines.append("")
                lines.append(
                    "**Baseline-only idioms "
                    "(in baseline, missing in target):**"
                )
                for idiom in baseline_only[:25]:
                    lines.append(f"- `{idiom}`")
        elif name == "hapax_phrase_survival":
            lines.append(
                f"- Baseline hapax {items.get('hapax_n', 3)}-grams: "
                f"{items.get('n_baseline_hapax', 0)}"
            )
            lines.append(
                f"- Surviving in target: "
                f"{items.get('n_surviving_in_target', 0)}"
            )
            sr = items.get("survival_rate")
            lines.append(
                f"- Survival rate: "
                f"{f'{sr:.2%}' if sr is not None else 'n/a'}"
            )
            sample = items.get("sample_survivors", [])
            if sample:
                lines.append("")
                lines.append("**Sample survivors:**")
                for s in sample[:15]:
                    lines.append(f"- `{s}`")
        lines.append("")

    license_block = audit.get("claim_license", {}).get("rendered", "")
    if license_block:
        lines.append(license_block)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------- CLI ----------


def _read_text(path_str: str, *, label: str) -> tuple[Path, str]:
    p = Path(path_str).expanduser()
    if not p.is_file():
        raise FileNotFoundError(
            f"--{label} file not found: {path_str}"
        )
    return p, p.read_text(encoding="utf-8", errors="ignore")


def _walk_baseline(
    baseline_dir: Path, target_path: Path | None,
) -> tuple[list[str], list[Path], list[Path]]:
    if not baseline_dir.exists():
        raise FileNotFoundError(
            f"Baseline directory not found: {baseline_dir}"
        )
    if not baseline_dir.is_dir():
        raise NotADirectoryError(
            f"--baseline-dir is not a directory: {baseline_dir}"
        )
    target_resolved = (
        target_path.resolve() if target_path else None
    )
    loaded: list[Path] = []
    skipped: list[Path] = []
    texts: list[str] = []
    for path in sorted(baseline_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {
            ".txt", ".md", ".markdown", ".rst",
        }:
            skipped.append(path)
            continue
        if (
            target_resolved is not None
            and path.resolve() == target_resolved
        ):
            skipped.append(path)
            continue
        try:
            text = path.read_text(
                encoding="utf-8", errors="ignore",
            )
        except OSError:
            skipped.append(path)
            continue
        if not text.strip():
            skipped.append(path)
            continue
        loaded.append(path)
        texts.append(text)
    return texts, loaded, skipped


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="phraseological_signature_audit.py",
        description=(
            "Phrase-frame mining over the writer's reusable "
            "language frames. Five categories: lexical bundles, "
            "slot frames, idioms, hapax phrase survival, "
            "stance / intensifier frames."
        ),
    )
    p.add_argument(
        "target",
        help="Path to the target text (.txt / .md / .rst).",
    )
    p.add_argument(
        "--baseline-dir",
        help="Directory of baseline files. Required for "
             "lexical-bundle and hapax-survival categories.",
    )
    p.add_argument(
        "--category", action="append", dest="categories",
        choices=list(CATEGORY_KEYS),
        help="Restrict to specific categories. Repeat for "
             "multiple. Default: all five.",
    )
    p.add_argument(
        "--keep-quotes", action="store_true",
        help="Don't strip Markdown blockquotes from target / "
             "baseline.",
    )
    p.add_argument(
        "--bundle-min-count", type=int, default=2,
        help="Minimum n-gram occurrence in baseline to count as "
             "a lexical bundle (default 2).",
    )
    p.add_argument(
        "--hapax-n", type=int, default=3,
        help="N-gram order for hapax-phrase analysis (default 3).",
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--out")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        target_path, target_text = _read_text(
            args.target, label="target",
        )
    except FileNotFoundError as exc:
        sys.stderr.write(f"target: {exc}\n")
        return 2
    if not target_text.strip():
        sys.stderr.write(
            f"target: file is empty: {args.target}\n"
        )
        return 2

    baseline_texts: list[str] | None = None
    if args.baseline_dir:
        try:
            baseline_texts, loaded, _skipped = _walk_baseline(
                Path(args.baseline_dir).expanduser(),
                target_path,
            )
        except (FileNotFoundError, NotADirectoryError) as exc:
            sys.stderr.write(f"--baseline-dir: {exc}\n")
            return 2
        if not loaded:
            sys.stderr.write(
                "--baseline-dir: no readable .txt/.md/.rst "
                "baseline files remained after filtering. The "
                "target file cannot also be its own baseline; "
                "supply a directory of OTHER files.\n"
            )
            return 2

    try:
        audit = audit_phraseology(
            target_text=target_text,
            baseline_texts=baseline_texts,
            keep_quotes=args.keep_quotes,
            category_filter=args.categories,
            bundle_min_count=args.bundle_min_count,
            hapax_n=args.hapax_n,
        )
    except ValueError as exc:
        sys.stderr.write(f"--category: {exc}\n")
        return 2

    out = (
        json.dumps(audit, indent=2, default=str)
        if args.json else render_report(audit)
    )
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
