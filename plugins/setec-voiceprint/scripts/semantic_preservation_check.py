#!/usr/bin/env python3
"""semantic_preservation_check.py — semantic guardrails over the
before / after restoration pair (paired-release schedule Release 8,
Trustworthiness Tier 3).

Extends `before_after_restoration.py`'s post-check loop. The
existing post-check flags metric-gaming (signal moved while a
gaming-aggregate moved against it) and signal direction
(improved / no_change / degraded / gamed). What it does NOT catch
is the failure mode where voice restoration accidentally makes
an argument more forceful, less accurate, or less careful — the
prose got smoother, but the *meaning* shifted. This module adds
that semantic-guardrail layer.

Seven preservation categories tracked:

  1. **claim_inventory** — declarative-sentence count (sentence-
     level proxy for "how many propositions does the prose
     assert?")
  2. **named_entities** — proper-noun + capitalized-multi-word
     phrases (regex fallback when spaCy's NER is unavailable)
  3. **citations_and_authorities** — "according to X" / "X said" /
     "X argued" / parenthetical citations / "research shows" /
     "studies have found"
  4. **stance_markers** — stance-bearing lexicon (claim verbs:
     argue / contend / maintain / suggest / propose; evaluative
     adverbs: clearly / surprisingly / importantly)
  5. **modal_verbs** — modal auxiliaries (must / should / may /
     might / can / could / will / would / ought / shall)
  6. **causal_claims** — causal-claim markers (because / due to /
     therefore / thus / hence / leads to / results in / causes /
     enables)
  7. **hedges** — uncertainty markers (perhaps / maybe / possibly /
     roughly / approximately / arguably / seems / appears /
     suggests / it is possible)

For each category, the report compares the BEFORE text and the
AFTER text and produces:

  - count_before, count_after
  - items_dropped (present in BEFORE, missing in AFTER) — lost
  - items_added (present in AFTER, missing in BEFORE) — gained
    (potential fabrication / over-confident restoration)
  - items_shared (in both)
  - verdict: preserved / shifted_dropped / shifted_added /
    shifted_changed / unknown

The `shifted_added` verdict is the load-bearing one. Voice
restoration that *adds* causal claims, stance markers, or
hedges-removed the writer didn't have is exactly the kind of
quiet over-confident edit a stylometric tool can otherwise
encourage. The check surfaces it explicitly so the writer can
audit each added item.

Usage:

    python3 scripts/semantic_preservation_check.py \\
        --before original.md \\
        --after revised.md \\
        --json --out report.json

    python3 scripts/semantic_preservation_check.py \\
        --before original.md --after revised.md \\
        --category causal_claims --category modal_verbs

task_surface: craft_restoration. Refuses authorship verdicts;
explicitly refuses "the revision is better" verdicts. The check
reports preservation, not quality.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_schema import build_output  # type: ignore
from claim_license import (  # type: ignore
    ClaimLicense,
    with_state_caveats,
)

try:
    from variance_audit import HAS_SPACY, _NLP, split_sentences  # type: ignore
except ImportError:
    HAS_SPACY = False
    _NLP = None

    def split_sentences(text: str) -> list[str]:  # type: ignore
        return re.split(r"(?<=[.!?])\s+", text.strip())


TASK_SURFACE = "craft_restoration"
TOOL_NAME = "semantic_preservation_check"
SCRIPT_VERSION = "1.0"


# ---------- Lexical inventories ----------


# Stance-bearing markers: claim verbs and evaluative adverbs.
_STANCE_LEXICON: tuple[str, ...] = (
    # Claim verbs
    "argue", "argues", "argued", "contend", "contends", "contended",
    "maintain", "maintains", "maintained", "suggest", "suggests",
    "suggested", "propose", "proposes", "proposed", "claim", "claims",
    "claimed", "assert", "asserts", "asserted", "insist", "insists",
    "insisted", "hold", "holds", "held", "deny", "denies", "denied",
    "concede", "concedes", "conceded", "acknowledge", "acknowledges",
    "acknowledged",
    # Evaluative / stance adverbs
    "clearly", "surprisingly", "importantly", "notably", "tellingly",
    "remarkably", "obviously", "evidently", "apparently", "ironically",
    "paradoxically", "unsurprisingly", "predictably", "regrettably",
    "unfortunately", "fortunately", "naturally", "characteristically",
    "tragically", "thankfully",
)

# Modal verbs (auxiliary).
_MODAL_VERBS: tuple[str, ...] = (
    "must", "should", "shall", "may", "might", "can", "could",
    "will", "would", "ought",
)

# Causal-claim markers. Multi-word phrases are normalized via
# regex (`\s+` between tokens). Lemma-loose: doesn't try to catch
# every form, just the common surface markers.
_CAUSAL_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("because", re.compile(r"\bbecause\b", re.IGNORECASE)),
    ("due to", re.compile(r"\bdue\s+to\b", re.IGNORECASE)),
    ("therefore", re.compile(r"\btherefore\b", re.IGNORECASE)),
    ("thus", re.compile(r"\bthus\b", re.IGNORECASE)),
    ("hence", re.compile(r"\bhence\b", re.IGNORECASE)),
    ("leads to", re.compile(r"\bleads?\s+to\b", re.IGNORECASE)),
    ("results in", re.compile(r"\bresults?\s+in\b", re.IGNORECASE)),
    ("causes", re.compile(r"\bcauses?\b", re.IGNORECASE)),
    ("caused by", re.compile(r"\bcaused\s+by\b", re.IGNORECASE)),
    ("enables", re.compile(r"\benables?\b", re.IGNORECASE)),
    (
        "as a result of",
        re.compile(r"\bas\s+a\s+result\s+of\b", re.IGNORECASE),
    ),
    (
        "consequently",
        re.compile(r"\bconsequently\b", re.IGNORECASE),
    ),
    ("so that", re.compile(r"\bso\s+that\b", re.IGNORECASE)),
)

# Hedges (epistemic uncertainty markers).
_HEDGE_LEXICON: tuple[str, ...] = (
    "perhaps", "maybe", "possibly", "probably", "roughly",
    "approximately", "arguably", "presumably", "ostensibly",
    "seemingly", "apparently",
    # Epistemic verb forms (these get treated as forms, not lemmas)
    "seems", "seem", "seemed", "appears", "appear", "appeared",
    "suggests", "suggest", "suggested", "indicates", "indicate",
    "indicated",
)
_HEDGE_PHRASES: tuple[tuple[str, re.Pattern], ...] = (
    (
        "it is possible",
        re.compile(r"\bit\s+is\s+possible\b", re.IGNORECASE),
    ),
    (
        "it is likely",
        re.compile(r"\bit\s+is\s+likely\b", re.IGNORECASE),
    ),
    (
        "more or less",
        re.compile(r"\bmore\s+or\s+less\b", re.IGNORECASE),
    ),
    ("kind of", re.compile(r"\bkind\s+of\b", re.IGNORECASE)),
    ("sort of", re.compile(r"\bsort\s+of\b", re.IGNORECASE)),
    (
        "to some extent",
        re.compile(r"\bto\s+some\s+extent\b", re.IGNORECASE),
    ),
)

# Citation / authority markers.
_CITATION_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    (
        "according to X",
        re.compile(r"\baccording\s+to\s+\S", re.IGNORECASE),
    ),
    (
        "X said",
        re.compile(
            r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s+(?:said|says|stated|states|wrote|writes|notes|noted)\b",
        ),
    ),
    (
        "X argued",
        re.compile(
            r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s+(?:argued|argues|claims|claimed|contends|contended|maintains|maintained)\b",
        ),
    ),
    (
        "research shows",
        re.compile(
            r"\b(?:research|study|studies|the\s+data|evidence|scholars|experts|scientists|researchers)\s+"
            r"(?:show|shows|find|finds|found|suggest|suggests|indicate|indicates|argue|argues|argued|reveal|reveals)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "parenthetical citation",
        re.compile(r"\(\s*[A-Z][a-z]+(?:\s+(?:and|&)\s+[A-Z][a-z]+)?\s*,?\s*\d{4}[a-z]?\s*\)"),
    ),
)


# ---------- Category result ----------


@dataclass
class CategoryResult:
    name: str
    description: str
    available: bool = True
    count_before: int = 0
    count_after: int = 0
    items_before: list[str] = field(default_factory=list)
    items_after: list[str] = field(default_factory=list)
    items_dropped: list[str] = field(default_factory=list)
    items_added: list[str] = field(default_factory=list)
    items_shared: list[str] = field(default_factory=list)
    verdict: str = "unknown"
    notes: list[str] = field(default_factory=list)


# ---------- Extraction helpers ----------


def _strip_blockquotes(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith(">")
    )


def _count_declaratives(text: str) -> tuple[int, list[str]]:
    """Count declarative sentences (non-question, non-imperative
    sentences ending in `.`). Returns (count, sample sentences).
    """
    sentences = split_sentences(text)
    declaratives: list[str] = []
    for s in sentences:
        s_strip = s.strip()
        if not s_strip:
            continue
        if s_strip.endswith("?"):
            continue
        if s_strip.endswith("!") and len(s_strip) < 80:
            # Treat short bang-sentences as exclamatives (not
            # declaratives).
            continue
        declaratives.append(s_strip)
    return len(declaratives), declaratives


def _extract_named_entities(text: str) -> list[str]:
    """Return a list of named entities. Uses spaCy NER when
    available; falls back to a regex over capitalized
    multi-token phrases (which is much noisier but degrades
    gracefully without spaCy)."""
    if HAS_SPACY and _NLP is not None:
        try:
            doc = _NLP(text)
            return sorted({ent.text.strip() for ent in doc.ents if ent.text.strip()})
        except Exception:
            pass
    # Regex fallback: capitalized multi-word phrases. Skip the
    # first word of each sentence (always capitalized).
    tokens = re.split(r"(?<=[.!?])\s+", text)
    candidates: set[str] = set()
    for sent in tokens:
        words = sent.split()
        if not words:
            continue
        # Drop the sentence-initial word, scan the rest for
        # consecutive capitalized words.
        rest = words[1:]
        run: list[str] = []
        for w in rest:
            stripped = w.strip(".,;:()\"'")
            if (
                stripped
                and stripped[0].isupper()
                and len(stripped) > 1
            ):
                run.append(stripped)
            else:
                if len(run) >= 1:
                    candidates.add(" ".join(run))
                run = []
        if len(run) >= 1:
            candidates.add(" ".join(run))
    return sorted(candidates)


def _extract_lexicon_hits(
    text: str, lexicon: tuple[str, ...],
) -> list[str]:
    """Find every word-boundary occurrence of any lexicon item.
    Returns a list of (lowercased) hits in document order."""
    text_lower = text.lower()
    hits: list[str] = []
    # Sort by length descending to try longer matches first.
    for word in sorted(lexicon, key=len, reverse=True):
        for m in re.finditer(rf"\b{re.escape(word)}\b", text_lower):
            hits.append(word)
    return hits


def _extract_pattern_hits(
    text: str,
    patterns: tuple[tuple[str, re.Pattern], ...],
) -> list[str]:
    hits: list[str] = []
    for label, regex in patterns:
        for _m in regex.finditer(text):
            hits.append(label)
    return hits


def _extract_modals(text: str) -> list[str]:
    return _extract_lexicon_hits(text, _MODAL_VERBS)


def _extract_stance(text: str) -> list[str]:
    return _extract_lexicon_hits(text, _STANCE_LEXICON)


def _extract_causals(text: str) -> list[str]:
    return _extract_pattern_hits(text, _CAUSAL_PATTERNS)


def _extract_hedges(text: str) -> list[str]:
    word_hits = _extract_lexicon_hits(text, _HEDGE_LEXICON)
    phrase_hits = _extract_pattern_hits(text, _HEDGE_PHRASES)
    return word_hits + phrase_hits


def _extract_citations(text: str) -> list[str]:
    return _extract_pattern_hits(text, _CITATION_PATTERNS)


# ---------- Per-category preservation check ----------


def _diff_lists(
    before: list[str], after: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Return (dropped, added, shared) as multiset diffs.

    ``dropped`` = items in BEFORE missing in AFTER (multiplicity-aware).
    ``added``  = items in AFTER missing in BEFORE.
    ``shared`` = items present in both (each counted by min count).
    """
    from collections import Counter
    cb = Counter(before)
    ca = Counter(after)
    dropped_counter = cb - ca
    added_counter = ca - cb
    shared_counter = cb & ca
    dropped = sorted(dropped_counter.elements())
    added = sorted(added_counter.elements())
    shared = sorted(shared_counter.elements())
    return dropped, added, shared


def _classify_count_only_verdict(
    *,
    count_before: int,
    count_after: int,
    drift_threshold: float = 0.20,
    min_count_for_threshold: int = 5,
) -> str:
    """Map (before, after) counts to a verdict using counts ONLY,
    with no reliance on per-item diffs.

    Used for `claim_inventory`, where sentence-level identity is too
    brittle to diff but a meaningful count change still warrants a
    `shifted_added` / `shifted_dropped` flag. The default
    `_classify_verdict` falls through to `preserved` when the item
    diffs are intentionally empty (which would hide a 5 → 10
    declarative-count drift, exactly the case this helper exists to
    catch).

    Verdicts mirror `_classify_verdict`'s ladder:
      - ``unknown`` — both counts effectively zero.
      - ``shifted_added`` — count grew by ≥ drift_threshold ratio
        (above the small-count floor) OR by ≥ 2 absolute (below the
        floor).
      - ``shifted_dropped`` — symmetric for shrink.
      - ``preserved`` — change within the noise band.
    """
    if count_before == 0 and count_after == 0:
        return "unknown"
    delta = count_after - count_before
    base = max(count_before, 1)
    ratio = delta / base

    # Below the small-count floor, demand absolute movement of ≥ 2.
    if max(count_before, count_after) < min_count_for_threshold:
        if delta >= 2:
            return "shifted_added"
        if delta <= -2:
            return "shifted_dropped"
        return "preserved"

    if ratio > drift_threshold:
        return "shifted_added"
    if ratio < -drift_threshold:
        return "shifted_dropped"
    return "preserved"


def _classify_verdict(
    *,
    count_before: int,
    count_after: int,
    items_dropped: list[str],
    items_added: list[str],
    drift_threshold: float = 0.20,
    min_count_for_threshold: int = 5,
) -> str:
    """Map (before, after, dropped, added) to a preservation verdict.

    Verdicts:
      - ``preserved`` — count change within ±drift_threshold AND
        not many adds or drops.
      - ``shifted_dropped`` — large reduction in count or items.
      - ``shifted_added`` — significant new items in after that
        weren't in before. Load-bearing for fabrication detection.
      - ``shifted_changed`` — counts similar but the specific
        items changed substantially (large dropped+added,
        small net delta).
      - ``unknown`` — both counts effectively zero.
    """
    if count_before == 0 and count_after == 0:
        return "unknown"

    n_dropped = len(items_dropped)
    n_added = len(items_added)
    delta = count_after - count_before
    base = max(count_before, 1)
    ratio = delta / base

    # Below the small-count floor, demand absolute movement
    # rather than ratio movement.
    if max(count_before, count_after) < min_count_for_threshold:
        if n_added >= 2 and n_dropped <= 1:
            return "shifted_added"
        if n_dropped >= 2 and n_added <= 1:
            return "shifted_dropped"
        if n_added >= 2 and n_dropped >= 2:
            return "shifted_changed"
        return "preserved"

    if ratio > drift_threshold and n_added > n_dropped:
        return "shifted_added"
    if ratio < -drift_threshold and n_dropped > n_added:
        return "shifted_dropped"
    if (
        n_dropped >= count_before * drift_threshold
        and n_added >= count_after * drift_threshold
    ):
        return "shifted_changed"
    return "preserved"


def _build_category_result(
    *,
    name: str,
    description: str,
    items_before: list[str],
    items_after: list[str],
    available: bool = True,
    notes: list[str] | None = None,
) -> CategoryResult:
    dropped, added, shared = _diff_lists(items_before, items_after)
    verdict = _classify_verdict(
        count_before=len(items_before),
        count_after=len(items_after),
        items_dropped=dropped,
        items_added=added,
    )
    return CategoryResult(
        name=name,
        description=description,
        available=available,
        count_before=len(items_before),
        count_after=len(items_after),
        items_before=items_before[:200],
        items_after=items_after[:200],
        items_dropped=dropped[:50],
        items_added=added[:50],
        items_shared=shared[:50],
        verdict=verdict,
        notes=list(notes or []),
    )


# ---------- Top-level check ----------


def check_preservation(
    *,
    before_text: str,
    after_text: str,
    keep_quotes: bool = False,
    category_filter: list[str] | None = None,
    target_ai_status: str | None = None,
) -> dict[str, Any]:
    """Run the seven-category preservation check on a (before,
    after) pair. Returns a dict shaped for both JSON serialization
    and ``render_report``."""
    if not keep_quotes:
        before_text = _strip_blockquotes(before_text)
        after_text = _strip_blockquotes(after_text)

    categories: dict[str, CategoryResult] = {}

    # 1. Claim inventory.
    before_count, before_decls = _count_declaratives(before_text)
    after_count, after_decls = _count_declaratives(after_text)
    # For claim inventory, the "items" comparison is too noisy
    # (sentences rarely match exactly). Use counts only via the
    # dedicated count-only classifier — the default
    # `_classify_verdict` honors counts only when item diffs are
    # non-empty, so passing intentionally-empty items here would
    # collapse to `preserved` even on a 5 → 10 count change.
    claim_verdict = _classify_count_only_verdict(
        count_before=before_count,
        count_after=after_count,
    )
    categories["claim_inventory"] = CategoryResult(
        name="claim_inventory",
        description=(
            "Declarative-sentence count. A coarse proxy for the "
            "number of propositions the prose asserts."
        ),
        count_before=before_count,
        count_after=after_count,
        items_before=before_decls[:5],
        items_after=after_decls[:5],
        items_dropped=[],
        items_added=[],
        items_shared=[],
        verdict=claim_verdict,
        notes=[
            "Counts declarative sentences only (skips questions "
            "and short exclamatives).",
            "Per-item diff is intentionally not surfaced — "
            "sentence-level identity is too brittle. The verdict "
            "reads counts only.",
        ],
    )

    # 2. Named entities.
    ne_before = _extract_named_entities(before_text)
    ne_after = _extract_named_entities(after_text)
    categories["named_entities"] = _build_category_result(
        name="named_entities",
        description=(
            "Proper-noun and capitalized-multi-word entities. "
            "Uses spaCy NER when available; falls back to a "
            "capitalized-phrase regex (noisier)."
        ),
        items_before=ne_before,
        items_after=ne_after,
        notes=(
            ["spaCy NER active."] if (HAS_SPACY and _NLP is not None)
            else [
                "spaCy unavailable; using capitalized-phrase regex "
                "fallback. Expect false positives (sentence-initial "
                "capitalized common nouns get caught).",
            ]
        ),
    )

    # 3. Citations / authorities.
    cit_before = _extract_citations(before_text)
    cit_after = _extract_citations(after_text)
    categories["citations_and_authorities"] = _build_category_result(
        name="citations_and_authorities",
        description=(
            "Authority and citation markers: \"according to X\", "
            "\"X said / argued / claimed\", \"research shows\", "
            "parenthetical (Author, YEAR) citations."
        ),
        items_before=cit_before,
        items_after=cit_after,
    )

    # 4. Stance markers.
    stance_before = _extract_stance(before_text)
    stance_after = _extract_stance(after_text)
    categories["stance_markers"] = _build_category_result(
        name="stance_markers",
        description=(
            "Stance-bearing lexicon: claim verbs (argue / contend "
            "/ maintain / suggest) and evaluative adverbs "
            "(clearly / surprisingly / importantly)."
        ),
        items_before=stance_before,
        items_after=stance_after,
    )

    # 5. Modal verbs.
    modal_before = _extract_modals(before_text)
    modal_after = _extract_modals(after_text)
    categories["modal_verbs"] = _build_category_result(
        name="modal_verbs",
        description=(
            "Modal auxiliaries: must / should / may / might / "
            "can / could / will / would / ought / shall."
        ),
        items_before=modal_before,
        items_after=modal_after,
    )

    # 6. Causal claims.
    causal_before = _extract_causals(before_text)
    causal_after = _extract_causals(after_text)
    categories["causal_claims"] = _build_category_result(
        name="causal_claims",
        description=(
            "Causal markers: because / due to / therefore / thus "
            "/ leads to / results in / causes / enables / as a "
            "result of / consequently / so that."
        ),
        items_before=causal_before,
        items_after=causal_after,
    )

    # 7. Hedges.
    hedge_before = _extract_hedges(before_text)
    hedge_after = _extract_hedges(after_text)
    categories["hedges"] = _build_category_result(
        name="hedges",
        description=(
            "Epistemic uncertainty markers: perhaps / maybe / "
            "possibly / arguably / seems / appears / it is "
            "possible / kind of / to some extent."
        ),
        items_before=hedge_before,
        items_after=hedge_after,
    )

    if category_filter:
        unknown_filters = [
            k for k in category_filter if k not in categories
        ]
        if unknown_filters:
            raise ValueError(
                f"Unknown category name(s) in --category: "
                f"{', '.join(repr(k) for k in unknown_filters)}. "
                f"Valid categories: "
                f"{', '.join(sorted(categories.keys()))}."
            )
        categories = {
            k: v for k, v in categories.items()
            if k in category_filter
        }
        if not categories:
            # Defensive: shouldn't be reachable given the unknown
            # check above, but a `--category` invocation should
            # never produce an empty audit.
            raise ValueError(
                "--category filter resolved to an empty category "
                "set; no semantic preservation work to do."
            )

    overall = _overall_verdict(categories)

    out: dict[str, Any] = {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "spacy_available": HAS_SPACY,
        "categories": {
            name: _category_to_dict(cat)
            for name, cat in categories.items()
        },
        "overall_verdict": overall,
        "claim_license": _claim_license_dict(
            categories=categories,
            overall=overall,
            target_ai_status=target_ai_status,
        ),
    }
    # B.3: surface ai_status at the top of the report dict so JSON
    # consumers can route on state without re-passing the flag.
    if target_ai_status:
        out["ai_status"] = target_ai_status
    return out


def _category_to_dict(cat: CategoryResult) -> dict[str, Any]:
    return {
        "name": cat.name,
        "description": cat.description,
        "available": cat.available,
        "count_before": cat.count_before,
        "count_after": cat.count_after,
        "items_dropped": cat.items_dropped,
        "items_added": cat.items_added,
        "items_shared": cat.items_shared,
        "verdict": cat.verdict,
        "notes": cat.notes,
    }


def _overall_verdict(
    categories: dict[str, CategoryResult],
) -> str:
    """Aggregate per-category verdicts to an overall preservation
    verdict. Conservative: any single ``shifted_added`` flips
    the overall to ``shifted_added`` (load-bearing for
    fabrication / over-confident-restoration detection).

    An empty categories dict returns ``unknown`` rather than
    falling through to ``preserved`` via the empty-``all``
    truthiness path. The CLI hard-fails on unknown filter names,
    so ``unknown`` here is a defense-in-depth — any future code
    path that reaches the aggregator with an empty dict gets the
    conservative reading.
    """
    verdicts = [c.verdict for c in categories.values()]
    if not verdicts:
        return "unknown"
    if "shifted_added" in verdicts:
        return "shifted_added"
    if "shifted_changed" in verdicts:
        return "shifted_changed"
    if "shifted_dropped" in verdicts:
        return "shifted_dropped"
    if all(v in {"preserved", "unknown"} for v in verdicts):
        return "preserved"
    return "mixed"


def _claim_license(
    *,
    categories: dict,
    overall: str,
    target_ai_status: str | None = None,
) -> ClaimLicense:
    n_categories = len(categories)
    n_shifted = sum(
        1 for c in categories.values()
        if c.verdict.startswith("shifted")
    )
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "A semantic-preservation comparison between an "
            "original (pre-restoration) text and a revised "
            "(post-restoration) text across seven categories: "
            "claim inventory, named entities, citations and "
            "authorities, stance markers, modal verbs, causal "
            "claims, and hedges. For each category the report "
            "names the items dropped, the items added, the "
            "items shared, and a preservation verdict. The "
            "overall verdict aggregates conservatively."
        ),
        does_not_license=(
            "A judgment that the revision is \"better\" or "
            "\"worse.\" The check reports preservation, not "
            "quality. A revision that reduces hedges might be "
            "more concise OR less careful — the framework "
            "refuses to choose. The author's call. Likewise, "
            "a `shifted_added` causal-claims verdict is a "
            "**flag for the author to audit**, not evidence of "
            "fabrication. Voice restoration may legitimately "
            "introduce a causal connector the author would "
            "endorse on review; this check ensures the author "
            "actually reviews it."
        ),
        comparison_set={
            "n_categories": n_categories,
            "n_shifted_categories": n_shifted,
            "overall_verdict": overall,
            "spacy_available": HAS_SPACY,
        },
        additional_caveats=[
            "Lexical inventories are heuristic and English-only. "
            "Causal connectors and stance markers in other "
            "languages are not detected.",
            "The named-entity diff falls back to a capitalized-"
            "phrase regex when spaCy is unavailable; expect "
            "false positives on sentence-initial common nouns.",
            "The check operates at the lexical / count level; "
            "it cannot detect semantic equivalence at the "
            "phrase or proposition level. \"Ten percent\" → "
            "\"approximately one-tenth\" reads as no change to "
            "the hedges category but a stylistic equivalent. "
            "The author's review remains the load-bearing step.",
            "The verdict thresholds are heuristic (default 20% "
            "ratio movement, 5-item small-count floor). "
            "Calibration-pending against labeled "
            "before/after-restoration corpora.",
        ],
    )
    # B.3: append state-routed caveats when the operator supplied
    # --ai-status. No-op when target_ai_status is None.
    return with_state_caveats(lic, target_ai_status=target_ai_status)


def _claim_license_dict(
    *,
    categories: dict,
    overall: str,
    target_ai_status: str | None = None,
) -> dict[str, Any]:
    """Legacy rendered-only shape preserved for the report dict."""
    return {
        "rendered": _claim_license(
            categories=categories,
            overall=overall,
            target_ai_status=target_ai_status,
        ).render_block().rstrip(),
    }


def build_audit_payload(
    report: dict[str, Any],
    *,
    before_path: Any,
    after_path: Any = None,
) -> dict[str, Any]:
    """Wrap the semantic-preservation report in the schema_version 1.0
    envelope per ``internal/SPEC_output_schema_unification.md``.
    """
    results_payload: dict[str, Any] = {}
    for k in (
        "spacy_available", "categories",
        "overall_verdict", "n_dropped", "n_added", "n_shared",
    ):
        if k in report:
            results_payload[k] = report[k]
    # Reconstruct a structured ClaimLicense from the categories.
    categories = report.get("categories", {}) or {}
    overall = report.get("overall_verdict", "unknown")
    # Categories is a dict of dicts (not CategoryResult instances)
    # when consumed from the report dict; emulate the .verdict access.
    class _C:
        def __init__(self, d): self.verdict = d.get("verdict", "unknown")
    cat_objs = {k: _C(v) for k, v in categories.items()}
    lic = _claim_license(
        categories=cat_objs,
        overall=overall,
        target_ai_status=report.get("ai_status"),
    )
    target_extra: dict[str, Any] = {}
    if after_path is not None:
        target_extra["after_path"] = str(after_path)
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=before_path,
        target_words=0,
        baseline=None,
        results=results_payload,
        claim_license=lic,
        ai_status=report.get("ai_status"),
        target_extra=target_extra or None,
    )


# ---------- Markdown rendering ----------


_VERDICT_GLYPH = {
    "preserved": "✓",
    "shifted_dropped": "↓",
    "shifted_added": "↑",
    "shifted_changed": "≠",
    "mixed": "·",
    "unknown": "—",
}


def render_report(report: dict[str, Any]) -> str:
    categories = report.get("categories", {})
    overall = report.get("overall_verdict", "unknown")

    lines: list[str] = [
        "# Semantic preservation check",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**spaCy available:** "
        f"{'yes' if report.get('spacy_available') else 'no'}",
        f"**Overall verdict:** "
        f"`{overall}` {_VERDICT_GLYPH.get(overall, '?')}",
        "",
        "## Per-category preservation",
        "",
        "Glyph legend: ✓ preserved, ↓ shifted_dropped, ↑ "
        "shifted_added, ≠ shifted_changed, · mixed, — unknown.",
        "",
        "| category | before | after | dropped | added | verdict |",
        "|---|---|---|---|---|---|",
    ]
    for name, cat in categories.items():
        verdict = cat.get("verdict", "unknown")
        glyph = _VERDICT_GLYPH.get(verdict, "?")
        lines.append(
            f"| {name} | {cat.get('count_before', 0)} | "
            f"{cat.get('count_after', 0)} | "
            f"{len(cat.get('items_dropped', []))} | "
            f"{len(cat.get('items_added', []))} | "
            f"`{verdict}` {glyph} |"
        )
    lines.append("")

    # Per-category details for non-preserved verdicts.
    interesting = [
        (name, cat) for name, cat in categories.items()
        if cat.get("verdict", "unknown")
        not in {"preserved", "unknown"}
    ]
    if interesting:
        lines.append("## Notable categories")
        lines.append("")
        for name, cat in interesting:
            lines.append(f"### `{name}` — {cat.get('verdict')}")
            lines.append("")
            lines.append(cat.get("description", ""))
            lines.append("")
            dropped = cat.get("items_dropped", [])
            added = cat.get("items_added", [])
            if dropped:
                lines.append("**Dropped:**")
                for item in dropped[:20]:
                    lines.append(f"- `{item}`")
                lines.append("")
            if added:
                lines.append("**Added:**")
                for item in added[:20]:
                    lines.append(f"- `{item}`")
                lines.append("")
            for note in cat.get("notes", []):
                lines.append(f"_Note: {note}_")
            lines.append("")

    license_block = report.get("claim_license", {}).get("rendered", "")
    if license_block:
        lines.append(license_block)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------- CLI ----------


def _read_text(path_str: str, *, label: str) -> str:
    p = Path(path_str).expanduser()
    if not p.is_file():
        raise FileNotFoundError(
            f"--{label} file not found: {path_str}"
        )
    return p.read_text(encoding="utf-8", errors="ignore")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="semantic_preservation_check.py",
        description=(
            "Semantic preservation check across seven categories "
            "between an original and a revised text. Catches "
            "the failure mode where voice restoration moves the "
            "stylometric signals but quietly shifts the meaning."
        ),
    )
    p.add_argument(
        "--before", required=True,
        help="Path to the BEFORE (original / pre-restoration) text.",
    )
    p.add_argument(
        "--after", required=True,
        help="Path to the AFTER (revised / post-restoration) text.",
    )
    p.add_argument(
        "--category", action="append", dest="categories",
        help="Restrict to specific categories. Repeat for "
             "multiple. Default: all seven.",
    )
    p.add_argument(
        "--keep-quotes", action="store_true",
        help="Don't strip Markdown blockquotes from either text.",
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--out")
    # B.3 (v1.58.0+): authorship-state routing for the ClaimLicense
    # block. The operator's manifest entry for the target (AFTER)
    # text carries an `ai_status` value (pre_ai_human,
    # ai_generated_from_outline, etc.). Surface it to the audit so
    # the rendered license block carries the matching state-specific
    # caveats. Per SPEC §9.2, this is the operational consequence
    # of the B.2 vocabulary — not threshold-shipping, just per-
    # state licensure language.
    p.add_argument(
        "--ai-status",
        default=None,
        help=(
            "Manifest ai_status value for the AFTER text (e.g., "
            "pre_ai_human, ai_generated, ai_generated_from_outline, "
            "ai_assisted, ai_edited, mixed, unknown). When supplied, "
            "the ClaimLicense block gains state-specific caveats per "
            "SPEC_authorship_states.md §9.2."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        before = _read_text(args.before, label="before")
        after = _read_text(args.after, label="after")
    except FileNotFoundError as exc:
        sys.stderr.write(f"Input error: {exc}\n")
        return 2

    if not before.strip():
        sys.stderr.write(
            f"--before file is empty: {args.before}\n"
        )
        return 2
    if not after.strip():
        sys.stderr.write(
            f"--after file is empty: {args.after}\n"
        )
        return 2

    try:
        report = check_preservation(
            before_text=before,
            after_text=after,
            keep_quotes=args.keep_quotes,
            category_filter=args.categories,
            target_ai_status=args.ai_status,
        )
    except ValueError as exc:
        sys.stderr.write(f"--category: {exc}\n")
        return 2

    if args.json:
        payload = build_audit_payload(
            report,
            before_path=args.before,
            after_path=args.after,
        )
        out = json.dumps(payload, indent=2, default=str)
    else:
        out = render_report(report)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
