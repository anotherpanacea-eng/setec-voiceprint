#!/usr/bin/env python3
"""register_classifier.py — heuristic register / genre detection.

Phase-1 trustworthiness layer (Release 1, paired-release schedule).
The framework's manifests already tag entries with a `register`
field (`blog_essay`, `literary_fiction`, `academic_philosophy`,
`policy_advocacy`, `testimony_policy`, etc.). The framework's
claim-license blocks already say "matched register." But the match
isn't operationalized — when a target text is supplied without a
register declaration, or when target and baseline registers
disagree, the framework currently has no way to surface that.

This module fills the gap with a *lightweight heuristic*
classifier. It is not a machine-learning model and not intended to
be one. The primary value is honest claim-licensing — when target
and baseline registers diverge, the report should say so explicitly
rather than silently produce numbers as if the comparison were
clean.

Heuristic taxonomy: signal-driven, not learned. Each register is
keyed by a small set of structural / lexical patterns that
empirically distinguish it from neighbors. The classifier returns
the register with the highest score plus the secondary candidates
plus the per-feature evidence.

Public API:

    classify_register(text, hint=None) -> {
        "primary": "blog_essay",
        "confidence": 0.62,
        "secondary": ["personal_essay"],
        "scores": {"blog_essay": 0.62, "personal_essay": 0.41, ...},
        "evidence": {"citation_density_per_1k": 0.0,
                     "dialogue_ratio": 0.05, ...},
    }

    register_match(target_register, baseline_registers) -> {
        "strength": "strong" | "moderate" | "weak" | "mismatch",
        "rationale": str,
        "target": "blog_essay",
        "baseline_distribution": {"blog_essay": 12, "personal_essay": 3},
    }

Honest framing: this is heuristic, not labeled-corpus-validated.
Use the output as a *prompt to ask register match questions*, not
as a definitive register call.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable, Sequence

TASK_SURFACE = "validation"

# Canonical register taxonomy — the manifest's `register` field
# uses these slugs. Classifier returns from this set or "unknown".
KNOWN_REGISTERS: tuple[str, ...] = (
    "blog_essay",
    "personal_essay",
    "literary_fiction",
    "commercial_fiction",
    "literary_horror",
    "academic_philosophy",
    "academic_general",
    "legal_memo",
    "policy_advocacy",
    "policy_memo",
    "testimony_policy",
    "journalism",
    "marketing",
    "newsletter",
    "report_prose",
    "social_thread",
    "email",
    "unknown",
)


# --- Feature extractors -----------------------------------------


_HEADING_RE = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+\S")
_SENTENCE_TERMINATORS = re.compile(r"[.!?]+\s+")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_FIRST_PERSON = re.compile(r"\b(?:I|we|my|our|me|us|mine|ours)\b", re.IGNORECASE)
_SECOND_PERSON = re.compile(r"\b(?:you|your|yours)\b", re.IGNORECASE)
_DIALOGUE_QUOTE = re.compile(r'["“][^"”\n]{1,200}["”]')
_QUESTION = re.compile(r"\?")
_EXCLAMATION = re.compile(r"!")
_INLINE_CITATION = re.compile(
    r"\([A-Z][A-Za-z\-]+(?:\s+(?:and|&)\s+[A-Z][A-Za-z\-]+)?,\s*\d{4}[a-z]?\)"
    r"|\[[A-Z][A-Za-z\-]+\s+\d{4}[a-z]?\]"
)
_STATUTORY = re.compile(
    r"\b(?:U\.?S\.?C\.?\s*§|Pub\.\s*L\.|Fed\.\s*R\.|§\s*\d+|"
    r"[A-Z][A-Za-z\-']+\s+v\.\s+[A-Z][A-Za-z\-']+)"
)
_FORMAL_ADDRESS = re.compile(
    r"\b(?:Mr\.\s+Chairman|Madam\s+Chair|"
    r"the\s+Committee|Honorable|Senator\s+[A-Z]|"
    r"Representative\s+[A-Z]|Dear\s+(?:Senator|Mr|Ms|Mrs|Dr)\b)",
    re.IGNORECASE,
)
_SHALL_PURSUANT = re.compile(
    r"\b(?:shall\s+(?:not\s+)?(?:be|have|apply|provide|"
    r"include|exclude|govern|prevail|require|prohibit)|"
    r"pursuant\s+to|notwithstanding\s+the\s+foregoing|"
    r"hereinafter|hereinbefore|whereas|aforementioned)\b",
    re.IGNORECASE,
)
_ATTRIBUTED_QUOTE = re.compile(
    r"\baccording\s+to\b|"
    r"\b(?:said|told|stated|reported|announced|added|noted)\b\s+(?:[A-Z]|the)",
    re.IGNORECASE,
)
_IMPERATIVE_OPEN = re.compile(
    r"(?m)^[ \t]{0,3}(?:Get|Buy|Try|Click|Sign|Subscribe|"
    r"Discover|Unlock|Transform|Boost|Maximize|Don't\s+miss|"
    r"Start|Join|Save|Order|Schedule)\b",
)
_PAST_TENSE_NARRATIVE = re.compile(
    r"\b(?:walked|looked|said|knew|thought|wondered|whispered|"
    r"remembered|noticed|watched|wanted|felt|saw|heard|believed)\b",
    re.IGNORECASE,
)
_ACADEMIC_VOICE = re.compile(
    r"\b(?:we\s+(?:argue|propose|suggest|claim|show|demonstrate|find|"
    r"conclude)|this\s+(?:paper|article|study|essay)|in\s+section\s+\d|"
    r"as\s+(?:argued|noted|shown)\s+(?:above|below|earlier))\b",
    re.IGNORECASE,
)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _per_thousand(count: int, n_words: int) -> float:
    if n_words <= 0:
        return 0.0
    return 1000.0 * count / n_words


def _features(text: str) -> dict[str, float]:
    """Compute the per-text feature vector. Densities are per-1000-words
    so they're comparable across short and long texts."""
    n_words = _word_count(text)
    n_chars = len(text)
    if n_words == 0:
        return {"n_words": 0, "n_chars": n_chars}

    sentences = _SENTENCE_TERMINATORS.split(text)
    sentences = [s for s in sentences if s.strip()]
    n_sentences = max(1, len(sentences))
    paragraphs = [
        p for p in _PARAGRAPH_BREAK.split(text) if p.strip()
    ]
    n_paragraphs = max(1, len(paragraphs))
    para_word_counts = [_word_count(p) for p in paragraphs]
    mean_para = sum(para_word_counts) / n_paragraphs

    # Dialogue ratio: tokens inside quotes / total tokens.
    dialogue_tokens = sum(
        _word_count(m.group(0))
        for m in _DIALOGUE_QUOTE.finditer(text)
    )

    return {
        "n_words": n_words,
        "n_chars": n_chars,
        "n_sentences": n_sentences,
        "n_paragraphs": n_paragraphs,
        "mean_paragraph_words": mean_para,
        "heading_density_per_1k": _per_thousand(
            len(_HEADING_RE.findall(text)), n_words,
        ),
        "first_person_per_1k": _per_thousand(
            len(_FIRST_PERSON.findall(text)), n_words,
        ),
        "second_person_per_1k": _per_thousand(
            len(_SECOND_PERSON.findall(text)), n_words,
        ),
        "dialogue_ratio": dialogue_tokens / n_words,
        "question_per_1k": _per_thousand(
            len(_QUESTION.findall(text)), n_words,
        ),
        "exclamation_per_1k": _per_thousand(
            len(_EXCLAMATION.findall(text)), n_words,
        ),
        "inline_citation_per_1k": _per_thousand(
            len(_INLINE_CITATION.findall(text)), n_words,
        ),
        "statutory_per_1k": _per_thousand(
            len(_STATUTORY.findall(text)), n_words,
        ),
        "formal_address_per_1k": _per_thousand(
            len(_FORMAL_ADDRESS.findall(text)), n_words,
        ),
        "shall_pursuant_per_1k": _per_thousand(
            len(_SHALL_PURSUANT.findall(text)), n_words,
        ),
        "attributed_quote_per_1k": _per_thousand(
            len(_ATTRIBUTED_QUOTE.findall(text)), n_words,
        ),
        "imperative_open_per_1k": _per_thousand(
            len(_IMPERATIVE_OPEN.findall(text)), n_words,
        ),
        "past_tense_narrative_per_1k": _per_thousand(
            len(_PAST_TENSE_NARRATIVE.findall(text)), n_words,
        ),
        "academic_voice_per_1k": _per_thousand(
            len(_ACADEMIC_VOICE.findall(text)), n_words,
        ),
    }


# --- Register scoring ------------------------------------------
#
# Each register is a function (features) -> [0, 1] score. Scores
# are not probabilities — they're heuristic compatibility scores.
# Higher = more compatible. The classifier picks the highest, with
# the second-highest reported as a secondary candidate.
#
# Each scoring function is a sum of [0, 1] sub-scores per signal,
# normalized by the count of signals. Sub-scores use a soft
# threshold (sigmoid-like clamp) so a single missing signal doesn't
# tank the register score.


def _soft(value: float, threshold: float, *, invert: bool = False) -> float:
    """[0, 1] sub-score: 1.0 when value >= threshold (or <= threshold
    if inverted), 0.0 when value is far the wrong side, smooth in
    between. Saturates at 1.5x threshold."""
    if threshold <= 0:
        return 0.0
    if invert:
        # Lower is better.
        if value <= 0:
            return 1.0
        ratio = max(0.0, 1.0 - value / threshold)
        return min(1.0, ratio)
    if value <= 0:
        return 0.0
    return min(1.0, value / threshold)


def _score_legal_or_policy_memo(f: dict[str, float]) -> float:
    sub = [
        _soft(f.get("statutory_per_1k", 0.0), 1.5),
        _soft(f.get("shall_pursuant_per_1k", 0.0), 2.0),
        _soft(f.get("inline_citation_per_1k", 0.0), 0.5),
        _soft(f.get("dialogue_ratio", 0.0), 0.05, invert=True),
        _soft(f.get("first_person_per_1k", 0.0), 8.0, invert=True),
    ]
    return sum(sub) / len(sub)


def _score_testimony_policy(f: dict[str, float]) -> float:
    sub = [
        _soft(f.get("formal_address_per_1k", 0.0), 1.0),
        _soft(f.get("first_person_per_1k", 0.0), 8.0),
        _soft(f.get("statutory_per_1k", 0.0), 0.5),
        _soft(f.get("dialogue_ratio", 0.0), 0.05, invert=True),
    ]
    return sum(sub) / len(sub)


def _score_academic(f: dict[str, float]) -> float:
    sub = [
        _soft(f.get("inline_citation_per_1k", 0.0), 2.0),
        _soft(f.get("academic_voice_per_1k", 0.0), 1.5),
        _soft(f.get("dialogue_ratio", 0.0), 0.03, invert=True),
        _soft(f.get("imperative_open_per_1k", 0.0), 0.5, invert=True),
    ]
    return sum(sub) / len(sub)


def _score_journalism(f: dict[str, float]) -> float:
    sub = [
        _soft(f.get("attributed_quote_per_1k", 0.0), 2.0),
        _soft(f.get("dialogue_ratio", 0.0), 0.10),
        _soft(f.get("mean_paragraph_words", 0.0), 80.0, invert=True),
        _soft(f.get("first_person_per_1k", 0.0), 8.0, invert=True),
        _soft(f.get("inline_citation_per_1k", 0.0), 1.0, invert=True),
    ]
    return sum(sub) / len(sub)


def _score_literary_fiction(f: dict[str, float]) -> float:
    sub = [
        _soft(f.get("dialogue_ratio", 0.0), 0.15),
        _soft(f.get("past_tense_narrative_per_1k", 0.0), 8.0),
        _soft(f.get("inline_citation_per_1k", 0.0), 0.5, invert=True),
        _soft(f.get("statutory_per_1k", 0.0), 0.2, invert=True),
        _soft(f.get("heading_density_per_1k", 0.0), 0.5, invert=True),
    ]
    return sum(sub) / len(sub)


def _score_blog_or_personal_essay(f: dict[str, float]) -> float:
    sub = [
        _soft(f.get("first_person_per_1k", 0.0), 10.0),
        _soft(f.get("inline_citation_per_1k", 0.0), 1.0, invert=True),
        _soft(f.get("statutory_per_1k", 0.0), 0.3, invert=True),
        _soft(f.get("dialogue_ratio", 0.0), 0.10, invert=True),
        _soft(f.get("attributed_quote_per_1k", 0.0), 1.0, invert=True),
        _soft(f.get("mean_paragraph_words", 0.0), 30.0),
    ]
    return sum(sub) / len(sub)


def _score_marketing(f: dict[str, float]) -> float:
    sub = [
        _soft(f.get("imperative_open_per_1k", 0.0), 1.5),
        _soft(f.get("exclamation_per_1k", 0.0), 5.0),
        _soft(f.get("second_person_per_1k", 0.0), 10.0),
        _soft(f.get("inline_citation_per_1k", 0.0), 0.5, invert=True),
    ]
    return sum(sub) / len(sub)


def _score_social_thread(f: dict[str, float]) -> float:
    sub = [
        _soft(f.get("mean_paragraph_words", 0.0), 50.0, invert=True),
        _soft(f.get("question_per_1k", 0.0), 10.0),
        _soft(f.get("first_person_per_1k", 0.0), 10.0),
        _soft(f.get("inline_citation_per_1k", 0.0), 0.5, invert=True),
    ]
    return sum(sub) / len(sub)


_SCORERS = {
    "legal_memo": _score_legal_or_policy_memo,
    "policy_memo": _score_legal_or_policy_memo,
    "testimony_policy": _score_testimony_policy,
    "academic_philosophy": _score_academic,
    "academic_general": _score_academic,
    "journalism": _score_journalism,
    "literary_fiction": _score_literary_fiction,
    "commercial_fiction": _score_literary_fiction,
    "literary_horror": _score_literary_fiction,
    "blog_essay": _score_blog_or_personal_essay,
    "personal_essay": _score_blog_or_personal_essay,
    "newsletter": _score_blog_or_personal_essay,
    "marketing": _score_marketing,
    "social_thread": _score_social_thread,
}


# --- Public API ------------------------------------------------


def classify_register(
    text: str,
    *,
    hint: str | None = None,
    min_words: int = 100,
) -> dict[str, Any]:
    """Heuristic register classification.

    Returns a dict with `primary` (best match), `confidence` (the
    primary score in [0, 1]), `secondary` (registers within 0.10 of
    the primary), `scores` (per-register), and `evidence` (the
    feature vector).

    Below ``min_words``, the classifier refuses with primary
    ``"unknown"`` and confidence 0.0 — heuristics are noisy on short
    texts. ``hint`` (if provided) shifts the matching register's
    score by a small bonus; useful when the user knows the register
    but wants the classifier to confirm.
    """
    features = _features(text)
    n_words = features.get("n_words", 0) or 0
    if n_words < min_words:
        return {
            "primary": "unknown",
            "confidence": 0.0,
            "secondary": [],
            "scores": {},
            "evidence": features,
            "warning": (
                f"Text has {n_words} words; register classification "
                f"requires at least {min_words}. Returning 'unknown'."
            ),
        }

    scores: dict[str, float] = {}
    for register, scorer in _SCORERS.items():
        scores[register] = round(scorer(features), 4)
    if hint and hint in scores:
        scores[hint] = min(1.0, scores[hint] + 0.05)

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    primary = ranked[0][0] if ranked else "unknown"
    primary_score = ranked[0][1] if ranked else 0.0
    secondary = [
        r for r, s in ranked[1:]
        if (primary_score - s) < 0.10 and s > 0.30
    ]
    if primary_score < 0.30:
        primary = "unknown"

    return {
        "primary": primary,
        "confidence": round(primary_score, 4),
        "secondary": secondary,
        "scores": scores,
        "evidence": features,
        "warning": None,
    }


def register_match(
    target_register: str | None,
    baseline_registers: Iterable[str | None],
) -> dict[str, Any]:
    """Compare a target register against a baseline's register
    distribution and report a strength label.

    Returns:
      - ``strength``: ``strong`` (>=80% of baseline matches target),
        ``moderate`` (>=50%), ``weak`` (>=20%), or ``mismatch``.
      - ``rationale``: human-readable explanation.
      - ``target``: the target register (or ``unknown``).
      - ``baseline_distribution``: count by baseline register.

    Used by claim-license blocks to surface register mismatch
    explicitly rather than silently producing unanchored numbers.
    """
    target = (target_register or "unknown").strip() or "unknown"
    counter: Counter[str] = Counter()
    for r in baseline_registers:
        counter[(r or "unknown").strip() or "unknown"] += 1
    total = sum(counter.values())
    if total == 0:
        return {
            "strength": "mismatch",
            "rationale": "Baseline contains no registered entries.",
            "target": target,
            "baseline_distribution": {},
        }
    target_in_baseline = counter.get(target, 0)
    fraction = target_in_baseline / total

    if target == "unknown":
        return {
            "strength": "weak",
            "rationale": (
                "Target register is unknown; baseline has "
                f"{total} entries across "
                f"{len([k for k, v in counter.items() if v > 0])} "
                "register(s). Comparison strength reduced."
            ),
            "target": target,
            "baseline_distribution": dict(counter),
        }

    if fraction >= 0.80:
        strength = "strong"
        rationale = (
            f"{target_in_baseline}/{total} baseline entries match "
            f"target register `{target}`."
        )
    elif fraction >= 0.50:
        strength = "moderate"
        rationale = (
            f"{target_in_baseline}/{total} baseline entries match "
            f"target register `{target}`. Other registers present: "
            + ", ".join(
                f"{k}={v}"
                for k, v in counter.most_common()
                if k != target
            ) + "."
        )
    elif fraction >= 0.20:
        strength = "weak"
        rationale = (
            f"Only {target_in_baseline}/{total} baseline entries "
            f"match target register `{target}`. Comparison strength "
            "reduced; consider filtering the baseline."
        )
    else:
        strength = "mismatch"
        biggest = counter.most_common(1)[0]
        rationale = (
            f"Target register `{target}` is rare in baseline "
            f"({target_in_baseline}/{total}); baseline is "
            f"dominantly `{biggest[0]}` ({biggest[1]}/{total}). "
            "Reading any cross-register voice distance as voice "
            "drift is unsafe."
        )

    return {
        "strength": strength,
        "rationale": rationale,
        "target": target,
        "baseline_distribution": dict(counter),
    }


def render_register_match_block(match: dict[str, Any]) -> str:
    """Markdown one-paragraph render of a register_match() result.

    For embedding in claim-license blocks or harness reports.
    """
    return (
        f"**Register match:** `{match['strength']}` — "
        f"{match['rationale']}"
    )


__all__ = [
    "TASK_SURFACE",
    "KNOWN_REGISTERS",
    "classify_register",
    "register_match",
    "render_register_match_block",
]
