#!/usr/bin/env python3
"""
aic_pattern_audit.py
Layer B / Layer C named-pattern density audit.

Counts the named rhetorical patterns from references/aic-flags.md and
references/source-triage.md in a target document, reports density per
thousand words, and (optionally) compares against a baseline corpus to
flag patterns whose density exceeds the writer's voice envelope.

This is the framework's first scriptable Layer B/C tool. The earlier
scripts (variance_audit.py, voice_distance.py, etc.) operate at Layer A
distributional signals or Layer C voice-distance comparison; this
script identifies specific rhetorical figures and reports per-pattern
density. Layer C source triage (earned vs. unearned per instance) still
requires the writer's judgment; the script's role is to surface
candidate instances and quantitative density signals so the writer
can adjudicate efficiently.

Patterns detected (v1):

Fiction patterns (per source-triage.md):
  - Negation Hedge:   "Not X. Not Y." sentence-initial structures
  - Disguised Correctio: "not X(,/.) (but/yet) Y" inline + "Not X. (next sentence)"
  - Pseudo-Aphorism: gnomic frames ("X as Y", "X is the Y of Z", "There is a kind of X")
  - Manifesto Cadence: 3+ consecutive sentences with anaphoric heads

Nonfiction parallel patterns (per aic-flags.md / source-triage.md):
  - False-Balance Construction: "while reasonable people may disagree" + variants
  - Hedge-and-Affirm: "while X, it is also true that Y" + variants
  - Recommendation Template: "DC must commit to" / "We urge X to" + actor+modal+verb
  - Authority Laundering: "research has shown / scholars have argued / experts agree"

Structural / craft patterns:
  - Triplet (3-item list with "and"): "X, Y, and Z"
  - Professional-Parallel Stack: 2+ adjacent paragraphs with same "A X may Y" frame

Patterns deferred to v2 (require NER / context analysis):
  - Abstraction Shielding (needs named-entity + abstractness)
  - Indefinite-Pronoun Gesture (needs context analysis)

Layer C source triage is the writer's call per instance. The script
reports candidates and density; "earned" vs. "unearned" verdicts live
with the writer.

Known v1 limitations:
  - The correctio detector matches only the explicit "not X, but Y" inline
    form and the "It is not X. It is Y" frame. Subtler multi-sentence
    correctios ("Detection measures X. What it cannot do is Y") and
    correctios with non-pronominal subjects ("the issue is not Z, but W")
    are not yet captured. v2 will add a sentence-pair detector that
    looks for negation-then-affirmation patterns across two sentences.
  - Markdown blockquotes (lines starting with '>') are stripped by
    default to prevent quoted passages from inflating the writer's
    pattern density. Pass --keep-quotes to disable stripping. Plain-text
    quoted material still requires manual handling.

Usage:
    python3 aic_pattern_audit.py target.md
    python3 aic_pattern_audit.py target.md --baseline-dir baseline/
    python3 aic_pattern_audit.py target.md --top 30 --json
    python3 aic_pattern_audit.py target.md --baseline-dir baseline/ \\
        --pattern correctio --pattern manifesto_cadence
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Reuse spaCy loader and tokenizer from variance_audit
try:
    from variance_audit import HAS_SPACY, _NLP, split_sentences  # type: ignore
except ImportError:
    HAS_SPACY = False
    _NLP = None
    def split_sentences(text: str) -> list[str]:  # type: ignore
        # Fallback if running without the framework on path
        return re.split(r"(?<=[.!?])\s+", text.strip())

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_baseline_metadata, build_output  # type: ignore

TASK_SURFACE = "craft_restoration"
TOOL_NAME = "aic_pattern_audit"
SCRIPT_VERSION = "1.0"


# ---------- pattern detectors ----------

@dataclass
class PatternHit:
    pattern: str          # canonical pattern key
    sentence_index: int   # 0-based sentence index in the document
    text: str             # the matching sentence (or sentence pair, etc.)
    span: str             # the regex-matched substring or marker
    note: str = ""        # optional context note


@dataclass
class PatternResult:
    pattern: str
    label: str            # human-readable label
    severity_note: str    # what density usually means
    hits: list[PatternHit] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.hits)


# Sentence-level regex patterns. These match within a single sentence
# unless explicitly noted (sentence-pair patterns are handled separately).

# Disguised correctio: "not X, but/yet Y" or "X, not Y, but Z" inline
# Variations: "not just X, but Y", "not merely X, but Y"
CORRECTIO_INLINE = re.compile(
    r"\b(?:not\s+(?:just|merely|only|simply)?\s*)\S[^.]{2,80}?[,;]\s+but\s+\S",
    re.IGNORECASE,
)
# "It is not X, it is Y" / "It is not X. It is Y" frame
CORRECTIO_IT_IS_NOT = re.compile(
    r"\b(?:it|that|this)\s+(?:is|was|will be)\s+not\s+\S",
    re.IGNORECASE,
)

# Sentence-initial negation hedge: "Not X." (the next sentence will affirm)
NEGATION_HEDGE_INITIAL = re.compile(r"^\s*Not\s+[A-Za-z]", re.MULTILINE)

# Pseudo-aphorism gnomic frames
PSEUDO_APHORISM_FRAMES = [
    re.compile(r"\bthere\s+is\s+a\s+kind\s+of\s+\S+\s+in\s+(every|all|each)\s+\S", re.IGNORECASE),
    re.compile(r"\b(?:is|was)\s+the\s+\S+\s+of\s+\S+(?:\s+\S+){0,3}\s*[.!?]", re.IGNORECASE),
    re.compile(r"\b\S+\s+as\s+\S+,\s+(?:the|a)\s+\S+\s+as\s+\S", re.IGNORECASE),
]

# Nonfiction parallel patterns (per source-triage.md)

FALSE_BALANCE_FRAMES = [
    re.compile(r"\bwhile\s+reasonable\s+people\s+(?:may\s+)?disagree", re.IGNORECASE),
    re.compile(r"\b(?:there\s+are|we\s+find)\s+valid\s+concerns\s+on\s+both\s+sides", re.IGNORECASE),
    re.compile(r"\bsome\s+have\s+argued\s+\S+.{10,150}\s+others\s+have\s+argued", re.IGNORECASE),
    re.compile(r"\breasonable\s+(?:people|minds)\s+(?:can|may|might)\s+disagree", re.IGNORECASE),
    re.compile(r"\bboth\s+sides\s+(?:have|make|raise)\s+(?:valid|reasonable|legitimate)", re.IGNORECASE),
]

HEDGE_AND_AFFIRM_FRAMES = [
    re.compile(r"\bwhile\s+\S[^.]{5,80}?\s+is\s+(?:generally|broadly|largely)\s+true", re.IGNORECASE),
    re.compile(r"\balthough\s+\S[^.]{5,100}?,\s+it\s+is\s+also\s+(?:true|important|worth)", re.IGNORECASE),
    re.compile(r"\bwhile\s+\S[^.]{5,100}?,\s+(?:in\s+some\s+cases|sometimes|at\s+times)", re.IGNORECASE),
    re.compile(r"\b\S[^.]{5,80}?,\s+though\s+of\s+course\s+", re.IGNORECASE),
    re.compile(r"\bit\s+is\s+(?:important|worth|essential)\s+to\s+(?:note|consider|recognize)\s+that", re.IGNORECASE),
]

RECOMMENDATION_TEMPLATE_FRAMES = [
    re.compile(r"\b(?:DC|the\s+(?:City|Council|Mayor|Department|Agency|Administration))\s+(?:must|should|needs?\s+to)\s+(?:commit|invest|prioritize|ensure|provide|expand|adopt|develop)", re.IGNORECASE),
    re.compile(r"\b(?:we|the\s+\S+)\s+(?:urge|call\s+on|implore|encourage)\s+\S+\s+to", re.IGNORECASE),
    re.compile(r"\bit\s+is\s+(?:essential|imperative|critical|necessary|vital)\s+that", re.IGNORECASE),
    re.compile(r"\bgoing\s+forward,?\s+\S+\s+should\s+(?:prioritize|ensure|focus|invest)", re.IGNORECASE),
    re.compile(r"\b(?:more|greater|additional)\s+(?:investment|funding|attention|focus)\s+(?:is|will\s+be)\s+(?:needed|required|essential)", re.IGNORECASE),
]

AUTHORITY_LAUNDERING_FRAMES = [
    re.compile(r"\b(?:research|studies|evidence|data|literature)\s+(?:has|have)\s+(?:shown|demonstrated|established|consistently\s+shown|repeatedly\s+shown)", re.IGNORECASE),
    re.compile(r"\b(?:scholars|researchers|experts|practitioners|economists|sociologists)\s+(?:have\s+)?(?:argued|agree|note|maintain|contend|find)", re.IGNORECASE),
    re.compile(r"\bit\s+(?:is|has\s+been)\s+(?:widely|broadly|generally)\s+(?:acknowledged|recognized|accepted|understood)", re.IGNORECASE),
    re.compile(r"\bthe\s+(?:research|literature|evidence|data)\s+(?:suggests|indicates|supports|points\s+to)", re.IGNORECASE),
]

# Triplet: "X, Y, and Z" with three or more comma-separated items
# Match short noun-phrase or adjective sequences. Three- and four-item
# lists count as "triplets" per the capybara appendix.
TRIPLET_PATTERN = re.compile(
    r"(?:\b\w+(?:\s+\w+){0,2}\b,\s+){2,4}(?:and|or)\s+\b\w+(?:\s+\w+){0,2}\b",
    re.IGNORECASE,
)


# Sentence-pair / multi-sentence patterns

def detect_negation_hedge_pairs(sentences: list[str]) -> list[PatternHit]:
    """Negation Hedge: 'Not X.' followed immediately by an affirming sentence.

    Sentence i starts with 'Not' and is short (a discrimination); sentence
    i+1 carries the affirming claim. This is the named pattern from
    source-triage.md.
    """
    hits: list[PatternHit] = []
    for i, s in enumerate(sentences[:-1]):
        s_strip = s.strip()
        if not s_strip:
            continue
        if not s_strip.startswith("Not "):
            continue
        # Must be a relatively short discrimination (< 25 words)
        if len(s_strip.split()) > 25:
            continue
        nxt = sentences[i + 1].strip()
        if not nxt:
            continue
        # The next sentence should not also start with "Not" (that's still
        # in the negation list, not the affirm). If it does, look further.
        if nxt.startswith("Not "):
            continue
        text = f"{s_strip} {nxt}"
        hits.append(PatternHit(
            pattern="negation_hedge",
            sentence_index=i,
            text=text[:300],
            span=s_strip[:120],
            note="sentence-initial negation followed by affirming sentence",
        ))
    return hits


def detect_disguised_correctio(sentences: list[str]) -> list[PatternHit]:
    """Disguised correctio: inline 'not X, but Y' or 'It is not X, it is Y'.

    Distinguished from negation_hedge in that the negate and affirm are
    in the same sentence (or fused via the 'It is not / it is' frame).
    """
    hits: list[PatternHit] = []
    for i, s in enumerate(sentences):
        s_strip = s.strip()
        m_inline = CORRECTIO_INLINE.search(s_strip)
        if m_inline:
            hits.append(PatternHit(
                pattern="correctio",
                sentence_index=i,
                text=s_strip[:300],
                span=m_inline.group(0)[:160],
                note="inline 'not X, but Y'",
            ))
            continue
        # 'It is not X, it is Y' / 'It is not X. It is Y' frame
        # Scoped: require both halves to be present in this or paired sentence.
        m_itnot = CORRECTIO_IT_IS_NOT.search(s_strip)
        if m_itnot:
            # Look for the affirming half in the same sentence or the next
            same = re.search(
                r"\b(?:it|that|this)\s+(?:is|was|will be)\s+not\s+\S[^.]{1,100}?[.,;]\s+(?:It|That|This|It's|It is)",
                s_strip,
                re.IGNORECASE,
            )
            paired = False
            if not same and i + 1 < len(sentences):
                nxt = sentences[i + 1].strip()
                if re.match(r"^(?:It|That|This|It's|It is)\s+\S", nxt):
                    paired = True
            if same or paired:
                hits.append(PatternHit(
                    pattern="correctio",
                    sentence_index=i,
                    text=s_strip[:300] + (" / " + sentences[i + 1].strip()[:200] if paired else ""),
                    span=m_itnot.group(0)[:160],
                    note="'It is not X. It is Y' frame",
                ))
    return hits


def detect_pseudo_aphorism(sentences: list[str]) -> list[PatternHit]:
    """Pseudo-aphorism: gnomic generalization frames."""
    hits: list[PatternHit] = []
    for i, s in enumerate(sentences):
        s_strip = s.strip()
        for rgx in PSEUDO_APHORISM_FRAMES:
            m = rgx.search(s_strip)
            if m:
                hits.append(PatternHit(
                    pattern="pseudo_aphorism",
                    sentence_index=i,
                    text=s_strip[:300],
                    span=m.group(0)[:160],
                    note="gnomic frame",
                ))
                break
    return hits


def detect_manifesto_cadence(sentences: list[str], min_run: int = 3) -> list[PatternHit]:
    """Manifesto cadence: N+ consecutive sentences with same anaphoric head.

    Compares the first 1-3 tokens of each sentence; flags runs where the
    same opener repeats `min_run` or more times consecutively.
    """
    hits: list[PatternHit] = []
    if len(sentences) < min_run:
        return hits

    def head_of(s: str) -> str:
        words = s.strip().split()
        if not words:
            return ""
        # First two words (or one if punctuation-trimmed)
        head = " ".join(words[:2]).lower()
        head = re.sub(r"[^\w\s]", "", head)
        return head

    i = 0
    while i < len(sentences):
        head = head_of(sentences[i])
        if not head or len(head) < 2:
            i += 1
            continue
        run = 1
        while i + run < len(sentences) and head_of(sentences[i + run]) == head:
            run += 1
        if run >= min_run:
            block = " // ".join(s.strip() for s in sentences[i:i + run])
            hits.append(PatternHit(
                pattern="manifesto_cadence",
                sentence_index=i,
                text=block[:500],
                span=f"{run} sentences with anaphoric head '{head}'",
                note=f"run length {run}",
            ))
            i += run
        else:
            i += 1
    return hits


def detect_triplets(sentences: list[str]) -> list[PatternHit]:
    """3- or 4-item lists separated by commas terminating in 'and X' / 'or X'."""
    hits: list[PatternHit] = []
    for i, s in enumerate(sentences):
        s_strip = s.strip()
        m = TRIPLET_PATTERN.search(s_strip)
        if m:
            hits.append(PatternHit(
                pattern="triplet",
                sentence_index=i,
                text=s_strip[:300],
                span=m.group(0)[:160],
                note="3- or 4-item comma-separated list",
            ))
    return hits


def detect_professional_parallel_stack(text: str) -> list[PatternHit]:
    """N+ adjacent paragraphs with the same opening clause structure.

    Detects paragraphs starting with patterns like 'A professor may use',
    'A researcher may use', 'A student can submit'. Looks for runs of
    paragraphs sharing the first 3-5 tokens after a determiner.
    """
    hits: list[PatternHit] = []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    def opener_of(p: str) -> str:
        # First sentence of paragraph; first 2 words after any leading determiner
        first_sent = re.split(r"(?<=[.!?])\s", p, maxsplit=1)[0]
        words = first_sent.split()
        if not words:
            return ""
        # Strip leading "A/An/The/These/Those/My/etc." then take 2 words
        determiners = {"a", "an", "the", "these", "those", "my", "our", "their", "his", "her"}
        idx = 0
        if words[0].lower() in determiners:
            idx = 1
        # Use POS-shape proxy: take next 2 words
        head = " ".join(w.lower() for w in words[idx:idx + 2])
        head = re.sub(r"[^\w\s]", "", head)
        return head

    # Look for runs where openers share a structural pattern. We use the
    # second-position content word (e.g. "professor may", "researcher may",
    # "student can") and check if multiple adjacent paragraphs share the
    # MODAL/VERB at position 2 even if the noun differs.
    def modal_at_2(opener: str) -> str:
        words = opener.split()
        if len(words) < 2:
            return ""
        return words[1]

    i = 0
    while i < len(paragraphs):
        op = opener_of(paragraphs[i])
        modal = modal_at_2(op)
        if not modal or modal in {"the", "a", "an"}:
            i += 1
            continue
        run = 1
        while i + run < len(paragraphs):
            nxt_op = opener_of(paragraphs[i + run])
            if modal_at_2(nxt_op) == modal:
                run += 1
            else:
                break
        if run >= 3:
            block = " // ".join(p[:120] for p in paragraphs[i:i + run])
            hits.append(PatternHit(
                pattern="professional_parallel_stack",
                sentence_index=i,
                text=block[:600],
                span=f"{run} adjacent paragraphs sharing modal/verb '{modal}' at position 2",
                note=f"run length {run}",
            ))
            i += run
        else:
            i += 1
    return hits


def detect_frame_pattern(
    sentences: list[str],
    pattern_key: str,
    label: str,
    frames: list[re.Pattern[str]],
    note: str,
) -> list[PatternHit]:
    """Generic detector: any of `frames` matching anywhere in a sentence."""
    hits: list[PatternHit] = []
    for i, s in enumerate(sentences):
        s_strip = s.strip()
        for rgx in frames:
            m = rgx.search(s_strip)
            if m:
                hits.append(PatternHit(
                    pattern=pattern_key,
                    sentence_index=i,
                    text=s_strip[:300],
                    span=m.group(0)[:160],
                    note=note,
                ))
                break
    return hits


# ---------- pattern registry ----------

def all_patterns(text: str, sentences: list[str]) -> dict[str, PatternResult]:
    """Run every pattern detector and return a dict of PatternResult."""
    results: dict[str, PatternResult] = {}

    results["negation_hedge"] = PatternResult(
        pattern="negation_hedge",
        label="Negation Hedge",
        severity_note="'Not X.' / 'Not X. Not Y.' sentence-initial cognitive sorting (earned) or narrator hedge (unearned)",
        hits=detect_negation_hedge_pairs(sentences),
    )
    results["correctio"] = PatternResult(
        pattern="correctio",
        label="Disguised Correctio",
        severity_note="'not X, but Y' inline + 'It is not X. It is Y' frame; cuts on payoff test if affirm repeats negate",
        hits=detect_disguised_correctio(sentences),
    )
    results["pseudo_aphorism"] = PatternResult(
        pattern="pseudo_aphorism",
        label="Pseudo-Aphorism",
        severity_note="gnomic generalization ('X as Y', 'is the Y of Z', 'There is a kind of'); often has real image right after that does the work",
        hits=detect_pseudo_aphorism(sentences),
    )
    results["manifesto_cadence"] = PatternResult(
        pattern="manifesto_cadence",
        label="Manifesto Cadence",
        severity_note="3+ parallel sentences with same anaphoric head; earned when each escalates/restricts/reveals, unearned when parallel substitutes for development",
        hits=detect_manifesto_cadence(sentences, min_run=3),
    )
    results["triplet"] = PatternResult(
        pattern="triplet",
        label="Triplet",
        severity_note="3- or 4-item comma-and lists; classical figure but at high density reads as rhythmic fill",
        hits=detect_triplets(sentences),
    )
    results["professional_parallel_stack"] = PatternResult(
        pattern="professional_parallel_stack",
        label="Professional-Parallel Stack",
        severity_note="3+ adjacent paragraphs with same opening clause structure ('A X may Y', 'A Z may Y'); performs comprehensiveness without differentiating",
        hits=detect_professional_parallel_stack(text),
    )

    # Nonfiction parallel patterns
    results["false_balance"] = PatternResult(
        pattern="false_balance",
        label="False-Balance Construction",
        severity_note="'while reasonable people may disagree', both-sidesing without specifying the disagreement",
        hits=detect_frame_pattern(sentences, "false_balance", "False-Balance Construction",
                                  FALSE_BALANCE_FRAMES, "false-balance frame"),
    )
    results["hedge_and_affirm"] = PatternResult(
        pattern="hedge_and_affirm",
        label="Hedge-and-Affirm",
        severity_note="'while X is generally true, in some cases Y' performs caution while saying nothing definite",
        hits=detect_frame_pattern(sentences, "hedge_and_affirm", "Hedge-and-Affirm",
                                   HEDGE_AND_AFFIRM_FRAMES, "hedge-and-affirm frame"),
    )
    results["recommendation_template"] = PatternResult(
        pattern="recommendation_template",
        label="Recommendation Template",
        severity_note="'DC must commit to', 'we urge X', generic-actor + modal + generic-verb without specifying action",
        hits=detect_frame_pattern(sentences, "recommendation_template", "Recommendation Template",
                                   RECOMMENDATION_TEMPLATE_FRAMES, "recommendation-template frame"),
    )
    results["authority_laundering"] = PatternResult(
        pattern="authority_laundering",
        label="Authority Laundering",
        severity_note="'research has shown', 'experts agree' without naming the research or the experts",
        hits=detect_frame_pattern(sentences, "authority_laundering", "Authority Laundering",
                                   AUTHORITY_LAUNDERING_FRAMES, "authority-laundering frame"),
    )

    return results


# ---------- baseline aggregation ----------

def list_baseline_paths(baseline_dir: str | Path) -> list[Path]:
    base = Path(baseline_dir)
    paths = sorted(base.glob("*.txt")) + sorted(base.glob("*.md"))
    return [
        p for p in paths
        if not p.name.lower().startswith("readme")
        and not p.name.startswith(".")
    ]


def baseline_density(
    baseline_paths: list[Path],
    pattern_keys: list[str],
) -> tuple[dict[str, float], int, list[Path], list[Path]]:
    """Aggregate per-pattern density (per 1000 words) across baseline files.

    Returns ``(density_per_1k, total_words, loaded, skipped)``. Density is
    computed as ``total_hits / total_words * 1000``, pooled across all
    baseline files.
    """
    total_words = 0
    total_hits: dict[str, int] = {k: 0 for k in pattern_keys}
    loaded: list[Path] = []
    skipped: list[Path] = []
    for p in baseline_paths:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            skipped.append(p)
            continue
        if not text.strip():
            skipped.append(p)
            continue
        sents = split_sentences(text)
        words = len(re.findall(r"\b\w+\b", text))
        if words == 0:
            skipped.append(p)
            continue
        results = all_patterns(text, sents)
        for k in pattern_keys:
            total_hits[k] += results[k].count
        total_words += words
        loaded.append(p)
    if total_words == 0:
        return {k: 0.0 for k in pattern_keys}, 0, loaded, skipped
    density = {k: total_hits[k] / total_words * 1000 for k in pattern_keys}
    return density, total_words, loaded, skipped


# ---------- rendering ----------

def render_report(
    target_path: Path,
    target_words: int,
    target_results: dict[str, PatternResult],
    baseline_density_per_1k: dict[str, float] | None,
    baseline_loaded: list[Path],
    baseline_skipped: list[Path],
    baseline_words: int,
    *,
    top: int,
    pattern_filter: list[str] | None,
) -> str:
    lines: list[str] = []
    lines.append(f"# AIC pattern audit: `{target_path.name}`")
    lines.append("")
    lines.append(f"**Task surface:** `{TASK_SURFACE}`")
    lines.append("")
    lines.append(f"Target: {target_words} words, {sum(r.count for r in target_results.values())} pattern hits across {len(target_results)} families.")
    if baseline_density_per_1k is not None:
        lines.append(
            f"Baseline: {len(baseline_loaded)} files loaded ({baseline_words} words)"
            + (f", {len(baseline_skipped)} skipped." if baseline_skipped else ".")
        )
    lines.append("")
    lines.append("Per-pattern density per 1,000 words. Comparison column shows target density minus baseline density (positive = target uses pattern more than baseline).")
    lines.append("")

    keys = list(target_results.keys())
    if pattern_filter:
        keys = [k for k in keys if k in pattern_filter]

    # Summary table
    if baseline_density_per_1k is not None:
        lines.append("| Pattern | Target hits | Target /1k | Baseline /1k | Δ /1k | Severity flag |")
        lines.append("|---|---:|---:|---:|---:|:--|")
    else:
        lines.append("| Pattern | Target hits | Target /1k | Severity note |")
        lines.append("|---|---:|---:|:--|")
    for k in keys:
        r = target_results[k]
        target_density = r.count / target_words * 1000 if target_words else 0
        if baseline_density_per_1k is not None:
            base_d = baseline_density_per_1k.get(k, 0.0)
            delta = target_density - base_d
            # Severity heuristic: target density > 2× baseline AND > 0.5/1k absolute
            flag = ""
            if base_d > 0 and target_density > 2 * base_d and target_density >= 0.5:
                flag = "**above 2× baseline**"
            elif target_density > base_d + 5:
                flag = "**+5/k above baseline**"
            elif target_density > 0 and base_d == 0:
                flag = "absent in baseline"
            lines.append(
                f"| {r.label} | {r.count} | {target_density:.2f} | {base_d:.2f} "
                f"| {delta:+.2f} | {flag} |"
            )
        else:
            lines.append(
                f"| {r.label} | {r.count} | {target_density:.2f} "
                f"| {r.severity_note} |"
            )
    lines.append("")

    # Per-pattern flagged instances
    for k in keys:
        r = target_results[k]
        if r.count == 0:
            continue
        lines.append(f"## {r.label}")
        lines.append("")
        lines.append(f"_{r.severity_note}_")
        lines.append("")
        lines.append(f"**{r.count} hits.** Showing first {min(top, r.count)}.")
        lines.append("")
        for hit in r.hits[:top]:
            lines.append(f"- _sent {hit.sentence_index}_: {hit.text}")
            if hit.span and hit.span != hit.text[:len(hit.span)]:
                lines.append(f"  - matched: `{hit.span}`")
        lines.append("")

    return "\n".join(lines)


def _claim_license(
    *,
    target_words: int,
    baseline_words: int,
    n_patterns_reported: int,
    has_baseline: bool,
) -> ClaimLicense:
    """Build the structured ClaimLicense block for this audit.

    The license describes what an AIC pattern density report entitles
    a reader to claim, what comparison set produced it, and what it
    explicitly does NOT license. Per ``internal/SPEC_output_schema_
    unification.md`` §11, scripts that lacked a claim_license gain
    one as part of their schema migration; the content here matches
    the framework's existing claim-license discipline.
    """
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "A density report for named rhetorical patterns "
            "(Disguised Correctio, Manifesto Cadence, Triplet, "
            "Pseudo-Aphorism, and the nonfiction parallel set: "
            "False-Balance, Hedge-and-Affirm, Recommendation "
            "Template, Authority Laundering). For each pattern "
            "the report names per-1000-word density in the target "
            "and (when a baseline is supplied) the density in the "
            "baseline plus the per-1000-word delta. Per-instance "
            "hits are surfaced for source triage."
        ),
        does_not_license=(
            "An authorship verdict. Pattern density is voice-"
            "coherence evidence and Layer B/C source-triage input, "
            "not authorship certification. Each detector is a "
            "regex / structural heuristic with known false-positive "
            "modes (the script's docstring lists the v1 "
            "limitations); 'earned vs. unearned' per instance is "
            "the writer's call. Heuristic thresholds are anchored "
            "to single-author case studies, not corpus calibration."
        ),
        comparison_set={
            "target_words": target_words,
            "baseline_words": baseline_words if has_baseline else 0,
            "has_baseline": has_baseline,
            "n_patterns_reported": n_patterns_reported,
        },
        additional_caveats=[
            "Markdown blockquotes are stripped by default to keep "
            "quoted passages from inflating density. Pass "
            "`--keep-quotes` to disable. Plain-text quoted "
            "material still requires manual handling.",
            "The correctio detector matches the explicit `not X, "
            "but Y` inline form and the `It is not X. It is Y` "
            "two-sentence frame. Subtler multi-sentence correctios "
            "are not yet captured.",
            "Abstraction Shielding and Indefinite-Pronoun Gesture "
            "are deferred to v2 (need NER / contextual analysis).",
        ],
        references=[
            "references/aic-flags.md",
            "references/source-triage.md",
        ],
    )


def build_audit_payload(
    target_path: Path,
    target_words: int,
    target_results: dict[str, PatternResult],
    baseline_density_per_1k: dict[str, float] | None,
    baseline_loaded: list[Path],
    baseline_skipped: list[Path],
    baseline_words: int,
    *,
    top: int,
    pattern_filter: list[str] | None,
) -> dict[str, Any]:
    """Produce the schema_version 1.0 envelope as a dict.

    Returns the envelope (caller serializes). Per
    ``internal/SPEC_output_schema_unification.md`` §3.2 the
    script-specific payload lives under ``results.patterns``.
    """
    keys = list(target_results.keys())
    if pattern_filter:
        keys = [k for k in keys if k in pattern_filter]

    patterns_block: dict[str, dict[str, Any]] = {}
    for k in keys:
        r = target_results[k]
        target_density = r.count / target_words * 1000 if target_words else 0
        block: dict[str, Any] = {
            "label": r.label,
            "severity_note": r.severity_note,
            "count": r.count,
            "density_per_1k": target_density,
            "hits": [
                {
                    "sentence_index": h.sentence_index,
                    "text": h.text,
                    "span": h.span,
                    "note": h.note,
                }
                for h in r.hits[:top]
            ],
        }
        if baseline_density_per_1k is not None:
            base_d = baseline_density_per_1k.get(k, 0.0)
            block["baseline_density_per_1k"] = base_d
            block["delta_per_1k"] = target_density - base_d
        patterns_block[k] = block

    has_baseline = baseline_density_per_1k is not None
    baseline_meta = (
        build_baseline_metadata(
            n_files=len(baseline_loaded),
            words=baseline_words,
            files_loaded=baseline_loaded,
            files_skipped=baseline_skipped,
        )
        if has_baseline
        else None
    )

    lic = _claim_license(
        target_words=target_words,
        baseline_words=baseline_words,
        n_patterns_reported=len(patterns_block),
        has_baseline=has_baseline,
    )

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=baseline_meta,
        results={"patterns": patterns_block},
        claim_license=lic,
    )


def render_json(
    target_path: Path,
    target_words: int,
    target_results: dict[str, PatternResult],
    baseline_density_per_1k: dict[str, float] | None,
    baseline_loaded: list[Path],
    baseline_skipped: list[Path],
    baseline_words: int,
    *,
    top: int,
    pattern_filter: list[str] | None,
) -> str:
    payload = build_audit_payload(
        target_path, target_words, target_results,
        baseline_density_per_1k, baseline_loaded,
        baseline_skipped, baseline_words,
        top=top, pattern_filter=pattern_filter,
    )
    return json.dumps(payload, indent=2, default=float)


# ---------- main ----------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Layer B/C named-pattern density audit (correctio, manifesto cadence, etc.)."
    )
    parser.add_argument("target", help="Path to target text file.")
    parser.add_argument(
        "--baseline-dir",
        help="Optional directory of baseline files for density comparison.",
    )
    parser.add_argument(
        "--pattern", action="append", dest="patterns",
        help="Run only specific pattern(s). Repeatable. "
             "Keys: negation_hedge, correctio, pseudo_aphorism, manifesto_cadence, "
             "triplet, professional_parallel_stack, false_balance, hedge_and_affirm, "
             "recommendation_template, authority_laundering.",
    )
    parser.add_argument(
        "--top", type=int, default=20,
        help="Show top N flagged instances per pattern (default 20).",
    )
    parser.add_argument(
        "--manifesto-min-run", type=int, default=3,
        help="Minimum consecutive sentences for manifesto cadence (default 3).",
    )
    parser.add_argument(
        "--keep-quotes", action="store_true",
        help="Don't strip markdown blockquotes (lines starting with '>'). "
             "By default they are removed because they usually contain "
             "quoted passages from other writers.",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    parser.add_argument("--out", help="Write output to file instead of stdout.")
    args = parser.parse_args()

    target_path = Path(args.target)
    if not target_path.exists():
        print(f"Target file not found: {target_path}", file=sys.stderr)
        return 1
    text = target_path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        print(f"Target file empty: {target_path}", file=sys.stderr)
        return 1
    if not args.keep_quotes:
        # Strip markdown blockquote lines (start with '>') to keep quoted
        # passages from inflating the writer's own pattern density.
        text = re.sub(r"^\s*>.*$", "", text, flags=re.MULTILINE)

    sentences = split_sentences(text)
    target_words = len(re.findall(r"\b\w+\b", text))
    if target_words == 0:
        print("Target has zero words.", file=sys.stderr)
        return 1

    target_results = all_patterns(text, sentences)

    baseline_density_per_1k: dict[str, float] | None = None
    baseline_loaded: list[Path] = []
    baseline_skipped: list[Path] = []
    baseline_words = 0
    if args.baseline_dir:
        baseline_paths = list_baseline_paths(args.baseline_dir)
        if not baseline_paths:
            print(f"No .txt or .md files in {args.baseline_dir}", file=sys.stderr)
            return 1
        baseline_density_per_1k, baseline_words, baseline_loaded, baseline_skipped = baseline_density(
            baseline_paths, list(target_results.keys()),
        )
        if baseline_skipped:
            print(
                "Warning: could not read baseline files: "
                + ", ".join(p.name for p in baseline_skipped)
                + ". Their content is absent from the baseline density.",
                file=sys.stderr,
            )

    if args.json:
        output = render_json(
            target_path, target_words, target_results,
            baseline_density_per_1k, baseline_loaded, baseline_skipped, baseline_words,
            top=args.top, pattern_filter=args.patterns,
        )
    else:
        output = render_report(
            target_path, target_words, target_results,
            baseline_density_per_1k, baseline_loaded, baseline_skipped, baseline_words,
            top=args.top, pattern_filter=args.patterns,
        )

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
