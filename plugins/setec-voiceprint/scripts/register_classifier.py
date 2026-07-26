#!/usr/bin/env python3
"""register_classifier.py — heuristic register / genre detection.

Phase-1 trustworthiness layer (Release 1, paired-release schedule).
Voiceprint manifests tag entries with canonical document-type
registers owned by ``manifest_validator.ALLOWED_REGISTER``. The
classifier emits a smaller family vocabulary that reflects what its
eight surface-heuristic scorers can honestly distinguish. The framework's
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
        "primary": "first_person_essay",
        "confidence": 0.62,
        "secondary": ["short_social"],
        "scores": {"first_person_essay": 0.62, "short_social": 0.41, ...},
        "evidence": {"citation_density_per_1k": 0.0,
                     "dialogue_ratio": 0.05, ...},
        "warning": None,
        "taxonomy": "register_families/v2",
        "refusal_reason": None,
    }

    register_match(target_register, baseline_registers) -> {
        "strength": "strong" | "moderate" | "weak" | "mismatch",
        "rationale": str,
        "target": "personal",
        "baseline_distribution": {"personal": 12, "blog_essay": 3},
        "target_family": "first_person_essay",
        "baseline_family_distribution": {"first_person_essay": 15},
        "taxonomy": "register_families/v2",
    }

Honest framing: this is heuristic, not labeled-corpus-validated.
Use the output as a *prompt to ask register match questions*, not
as a definitive register call.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable

TASK_SURFACE = "validation"

# Classifier output taxonomy. Manifest document-type registers map into these
# scorer-backed families; ``unknown`` is a refusal sentinel and is never scored.
REGISTER_TAXONOMY = "register_families/v2"
REGISTER_REFUSAL_REASONS: tuple[str, ...] = (
    "short_text",
    "all_weak",
    "exact_top_tie",
)
REGISTER_FAMILIES: tuple[str, ...] = (
    "formal_legal_policy",
    "formal_first_person",
    "academic",
    "journalism",
    "narrative_fiction",
    "first_person_essay",
    "promotional",
    "short_social",
    "unknown",
)
KNOWN_REGISTERS = REGISTER_FAMILIES

CANONICAL_REGISTER_TO_FAMILY: dict[str, str] = {
    "literary_fiction": "narrative_fiction",
    "literary_horror": "narrative_fiction",
    "blog_essay": "first_person_essay",
    "personal": "first_person_essay",
    "academic_philosophy": "academic",
    "scholarly_article": "academic",
    "testimony_policy": "formal_first_person",
    "expert_affidavit": "formal_first_person",
    "policy_brief": "formal_legal_policy",
    "legal_brief": "formal_legal_policy",
    "regulatory_comment": "formal_legal_policy",
    "grant_proposal": "formal_legal_policy",
    "policy_advocacy": "formal_legal_policy",
    "professional_letter": "formal_first_person",
    "teaching": "academic",
    "message.imessage": "short_social",
}

LEGACY_REGISTER_TO_FAMILY: dict[str, str] = {
    "personal_essay": "first_person_essay",
    "commercial_fiction": "narrative_fiction",
    "academic_general": "academic",
    "legal_memo": "formal_legal_policy",
    "policy_memo": "formal_legal_policy",
    "newsletter": "first_person_essay",
    "marketing": "promotional",
    "report_prose": "journalism",
    "social_thread": "short_social",
    "email": "formal_first_person",
}


def resolve_family(value: str | None) -> str:
    """Resolve family, canonical, and deprecated spellings in that order."""
    normalized = (value or "unknown").strip() or "unknown"
    if normalized in REGISTER_FAMILIES:
        return normalized
    if normalized in CANONICAL_REGISTER_TO_FAMILY:
        return CANONICAL_REGISTER_TO_FAMILY[normalized]
    if normalized in LEGACY_REGISTER_TO_FAMILY:
        return LEGACY_REGISTER_TO_FAMILY[normalized]
    return "unknown"


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
    "formal_legal_policy": _score_legal_or_policy_memo,
    "formal_first_person": _score_testimony_policy,
    "academic": _score_academic,
    "journalism": _score_journalism,
    "narrative_fiction": _score_literary_fiction,
    "first_person_essay": _score_blog_or_personal_essay,
    "promotional": _score_marketing,
    "short_social": _score_social_thread,
}


# --- Public API ------------------------------------------------


def _unrecognized_hint_warning(hint: str) -> str:
    return f"Ignored unrecognized register hint {hint!r}."


def _short_text_warning(n_words: int, min_words: int) -> str:
    return (
        f"Text has {n_words} words; register classification "
        f"requires at least {min_words}. Returning 'unknown'."
    )


def _exact_top_tie_warning(tied: list[str]) -> str:
    return (
        "Exact top register-family tie among "
        + ", ".join(f"`{register}`" for register in tied)
        + "; returning 'unknown'."
    )


def classify_register(
    text: str,
    *,
    hint: str | None = None,
    min_words: int = 100,
) -> dict[str, Any]:
    """Heuristic register classification.

    Returns a dict with `primary` (best match), `confidence` (the
    primary score in [0, 1]), `secondary` (registers within 0.10 of
    the primary), `scores` (per-register), `evidence` (the feature
    vector), `warning` (advisory prose or ``None``), `taxonomy`, and
    `refusal_reason` (one of :data:`REGISTER_REFUSAL_REASONS` or
    ``None``). ``primary == "unknown"`` if and only if
    ``refusal_reason`` is a member of
    :data:`REGISTER_REFUSAL_REASONS`.

    Below ``min_words``, the classifier refuses with primary
    ``"unknown"`` and confidence 0.0 — heuristics are noisy on short
    texts. ``hint`` (if provided) shifts the matching register's
    score by a small bonus; useful when the user knows the register
    but wants the classifier to confirm.
    """
    features = _features(text)
    hint_family = resolve_family(hint) if hint else None
    warnings: list[str] = []
    if hint and hint_family == "unknown" and hint.strip() != "unknown":
        warnings.append(_unrecognized_hint_warning(hint))
    n_words = features.get("n_words", 0) or 0
    if n_words < min_words:
        warnings.append(_short_text_warning(n_words, min_words))
        return {
            "primary": "unknown",
            "confidence": 0.0,
            "secondary": [],
            "scores": {},
            "evidence": features,
            "warning": "; ".join(warnings),
            "taxonomy": REGISTER_TAXONOMY,
            "refusal_reason": "short_text",
        }

    scores: dict[str, float] = {}
    for register, scorer in _SCORERS.items():
        scores[register] = round(scorer(features), 4)
    if hint_family and hint_family in scores:
        scores[hint_family] = round(min(1.0, scores[hint_family] + 0.05), 4)

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    primary = ranked[0][0] if ranked else "unknown"
    primary_score = ranked[0][1] if ranked else 0.0
    refusal_reason: str | None = None
    if primary_score < 0.30:
        primary = "unknown"
        refusal_reason = "all_weak"
        secondary: list[str] = [
            register for register, score in ranked[1:]
            if (primary_score - score) < 0.10 and score > 0.30
        ]
    else:
        tied = [register for register, score in ranked if score == primary_score]
        if len(tied) > 1:
            primary = "unknown"
            refusal_reason = "exact_top_tie"
            secondary = tied + [
                register for register, score in ranked
                if register not in tied
                and (primary_score - score) < 0.10
                and score > 0.30
            ]
            warnings.append(_exact_top_tie_warning(tied))
        else:
            secondary = [
                register for register, score in ranked[1:]
                if (primary_score - score) < 0.10 and score > 0.30
            ]

    return {
        "primary": primary,
        "confidence": round(primary_score, 4),
        "secondary": secondary,
        "scores": scores,
        "evidence": features,
        "warning": "; ".join(warnings) if warnings else None,
        "taxonomy": REGISTER_TAXONOMY,
        "refusal_reason": refusal_reason,
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
    target_family = resolve_family(target)
    counter: Counter[str] = Counter()
    family_counter: Counter[str] = Counter()
    for r in baseline_registers:
        raw = (r or "unknown").strip() or "unknown"
        counter[raw] += 1
        family_counter[resolve_family(raw)] += 1
    total = sum(counter.values())
    if total == 0:
        return {
            "strength": "mismatch",
            "rationale": "Baseline contains no registered entries.",
            "target": target,
            "baseline_distribution": {},
            "taxonomy": REGISTER_TAXONOMY,
            "target_family": target_family,
            "baseline_family_distribution": {},
        }
    target_in_baseline = family_counter.get(target_family, 0)
    fraction = target_in_baseline / total

    if target_family == "unknown":
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
            "taxonomy": REGISTER_TAXONOMY,
            "target_family": target_family,
            "baseline_family_distribution": dict(family_counter),
        }

    if fraction >= 0.80:
        strength = "strong"
        rationale = (
            f"{target_in_baseline}/{total} baseline entries match "
            f"target register family `{target_family}`."
        )
    elif fraction >= 0.50:
        strength = "moderate"
        rationale = (
            f"{target_in_baseline}/{total} baseline entries match "
            f"target register family `{target_family}`. Other families present: "
            + ", ".join(
                f"{k}={v}"
                for k, v in family_counter.most_common()
                if k != target_family
            ) + "."
        )
    elif fraction >= 0.20:
        strength = "weak"
        rationale = (
            f"Only {target_in_baseline}/{total} baseline entries "
            f"match target register family `{target_family}`. Comparison strength "
            "reduced; consider filtering the baseline."
        )
    else:
        strength = "mismatch"
        biggest = family_counter.most_common(1)[0]
        rationale = (
            f"Target register family `{target_family}` is rare in baseline "
            f"({target_in_baseline}/{total}); baseline is "
            f"dominantly `{biggest[0]}` ({biggest[1]}/{total}). "
            "Reading any cross-register voice distance as voice "
            "drift is unsafe."
        )

    matched_raw = sorted(
        raw for raw in counter
        if resolve_family(raw) == target_family
    )
    if target_in_baseline and (
        len(matched_raw) >= 2 or any(raw != target for raw in matched_raw)
    ):
        described = set(matched_raw)
        if target not in REGISTER_FAMILIES:
            described.add(target)
        rationale += (
            " Family-level match: target and baseline resolve to "
            f"`{target_family}`; raw values represented here: "
            + ", ".join(f"`{value}`" for value in sorted(described))
            + "; even a strong family match does not distinguish document types "
            "within the family."
        )

    return {
        "strength": strength,
        "rationale": rationale,
        "target": target,
        "baseline_distribution": dict(counter),
        "taxonomy": REGISTER_TAXONOMY,
        "target_family": target_family,
        "baseline_family_distribution": dict(family_counter),
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
    "REGISTER_FAMILIES",
    "REGISTER_TAXONOMY",
    "REGISTER_REFUSAL_REASONS",
    "CANONICAL_REGISTER_TO_FAMILY",
    "LEGACY_REGISTER_TO_FAMILY",
    "resolve_family",
    "classify_register",
    "register_match",
    "render_register_match_block",
]
