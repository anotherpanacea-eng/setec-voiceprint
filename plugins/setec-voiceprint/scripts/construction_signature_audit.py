#!/usr/bin/env python3
"""construction_signature_audit.py — interpretable syntactic-construction
density audit (paired-release schedule Release 8, Surfaces Tier 3).

The right answer to "POS-bigram KL is opaque." Where
`variance_audit.py` reports a numerical KL divergence between
target and baseline POS-tag-pair distributions, this module names
the *constructions* that drive that divergence and reports each
one's density per 1,000 words. The signal becomes readable to
craft editors who know what a fronted adverbial is but never
needed to look at a tag-bigram heatmap.

Constructions detected (v1):

  Regex-only (no spaCy required):
  - **cleft**: "It is/was X that/who/which Y" / "It's X that Y"
  - **pseudo_cleft**: "What X (is|was|are|were) Y" / "What matters is..."
  - **existential_there**: "There is/are/was/were/has been/have been..."
  - **extraposition**: "It is/was Adj/N to V..." / "It is X that Y"
  - **correlative**: not only/but also; either/or; neither/nor;
    both/and; not just/but
  - **concessive_opener**: sentence-initial Although/While/Even though/
    Despite/Whereas
  - **participial_opener**: sentence-initial -ing or -ed verb +
    comma + main clause
  - **fronted_adverbial**: sentence-initial PP / adverbial clause +
    comma + main clause (heuristic: comma after first 2-7 words
    where the prefix doesn't look like a quoted attribution)
  - **parenthetical_insertion**: comma-bounded clause-medial
    insertion (heuristic: subject ", X, " predicate)

  spaCy-enhanced (require ``en_core_web_sm``):
  - **agented_passive**: be + past-participle + by-phrase
  - **agentless_passive**: be + past-participle without a by-phrase
  - **stacked_prepositional_phrases**: 3+ consecutive PPs at the
    end of a clause

When spaCy is unavailable, the spaCy-enhanced constructions report
``available: false`` rather than producing degraded results — same
convention the rest of the framework uses for spaCy-only signals.

Output shape mirrors ``aic_pattern_audit.py``: ``patterns.<key>.
density_per_1k`` per construction, top hits, optional baseline
comparison via ``--baseline-dir``. Pairs naturally with the AIC
density audit (same shape, different unit: rhetorical figures vs.
syntactic constructions).

Usage:

    python3 scripts/construction_signature_audit.py target.md
    python3 scripts/construction_signature_audit.py target.md \\
        --baseline-dir baseline/
    python3 scripts/construction_signature_audit.py target.md \\
        --construction cleft --construction agented_passive --json

task_surface: voice_coherence (the construction signature is a
voice-coherence layer over POS-bigram material). Refuses the
classifier reading: high construction density on any single
pattern is craft signal, not provenance evidence.
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

from claim_license import (  # type: ignore
    ClaimLicense,
    with_state_caveats,
)

# Reuse spaCy loader and tokenizer from variance_audit.
try:
    from variance_audit import HAS_SPACY, _NLP, split_sentences  # type: ignore
except ImportError:
    HAS_SPACY = False
    _NLP = None

    def split_sentences(text: str) -> list[str]:  # type: ignore
        return re.split(r"(?<=[.!?])\s+", text.strip())


TASK_SURFACE = "voice_coherence"
TOOL_NAME = "construction_signature_audit"
SCRIPT_VERSION = "1.0"


# ---------- data model ----------


@dataclass
class ConstructionHit:
    """One detected construction occurrence."""
    construction: str
    sentence_index: int
    text: str
    span: str
    note: str = ""


@dataclass
class ConstructionResult:
    construction: str
    label: str
    description: str
    requires_spacy: bool = False
    available: bool = True
    hits: list[ConstructionHit] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.hits)


# ---------- Regex patterns (no spaCy required) ----------

# Cleft: "It is/was X that/who/which Y" where X is a foregrounded
# noun phrase. Diagnostic: X starts with a definite article (the),
# demonstrative (this/that/those/these), proper noun (capital
# letter), or pronoun. This excludes "It is clear that..." (which
# is extraposition) by requiring an NP introducer, not a bare
# adjective.
_CLEFT_RE = re.compile(
    r"\b(?:[Ii]t\s+(?:is|was|has\s+been|had\s+been)|[Ii]t['’]s)\s+"
    r"(?:the|this|that|those|these|my|your|his|her|its|our|their|"
    r"a|an|[A-Z][a-z]+|[A-Z]+|he|she|they|we|you|I)\b"
    r"[^.,;]{0,80}?\s+(?:that|who|whom|which)\s+\S",
)

# Pseudo-cleft: "What X (is|are|was|were) Y".
_PSEUDO_CLEFT_RE = re.compile(
    r"\bWhat\s+[A-Za-z][^.,;]{1,80}?\s+(?:is|are|was|were|matters)\b",
)

# Existential there: "There is/are/was/were/has been/have been ...".
_EXISTENTIAL_RE = re.compile(
    r"\bThere\s+(?:is|are|was|were|has\s+been|have\s+been|will\s+be|would\s+be|could\s+be|might\s+be|may\s+be)\b",
)

# Predicative adjectives common in extraposition. Curated from
# corpus-linguistic studies of the "It is X that/to" frame —
# adjectives that take a sentential or infinitival complement
# without making the construction a cleft.
_EXTRAPOSITION_PREDICATES = (
    r"clear|obvious|evident|true|false|important|necessary|useful|"
    r"possible|impossible|likely|unlikely|surprising|telling|easy|"
    r"hard|difficult|common|rare|apparent|plain|certain|unfortunate|"
    r"fortunate|remarkable|striking|notable|regrettable|expected|"
    r"unexpected|natural|normal|strange|odd|curious|interesting|"
    r"ironic|paradoxical|fitting|unsurprising|predictable|crucial|"
    r"critical|essential|imperative|vital|sad|tragic|good|bad|wise|"
    r"foolish|reasonable|sensible|misleading|tempting|conceivable|"
    r"plausible|implausible|well\s+known|well-known|widely\s+known"
)

# Extraposition: two cases.
#   1) "It is/was X to V" — always extraposition (cleft can't take
#      a "to V" continuation in standard English).
#   2) "It is/was [predicate-adj] that Y" — extraposition when the
#      X is a predicative adjective from the curated list.
_EXTRAPOSITION_TO_RE = re.compile(
    r"\b(?:[Ii]t\s+(?:is|was|has\s+been)|[Ii]t['’]s)\s+"
    r"[a-z]+(?:\s+[a-z]+){0,3}\s+to\s+\S",
)
_EXTRAPOSITION_THAT_RE = re.compile(
    r"\b(?:[Ii]t\s+(?:is|was|has\s+been)|[Ii]t['’]s)\s+"
    r"(?:" + _EXTRAPOSITION_PREDICATES + r")\s+that\s+\S",
    re.IGNORECASE,
)

# Correlative constructions. Case-insensitive so sentence-initial
# capitalized forms (Either / Neither / Both) match too.
_CORRELATIVE_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    (
        "not only / but also",
        re.compile(
            r"\bnot\s+only\b[^.]{1,150}?\bbut\s+(?:also|even|rather)?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "not just / but",
        re.compile(
            r"\bnot\s+just\b[^.]{1,120}?\bbut\b", re.IGNORECASE,
        ),
    ),
    (
        "either / or",
        re.compile(
            r"\beither\b[^.]{1,100}?\bor\b", re.IGNORECASE,
        ),
    ),
    (
        "neither / nor",
        re.compile(
            r"\bneither\b[^.]{1,100}?\bnor\b", re.IGNORECASE,
        ),
    ),
    (
        "both / and",
        re.compile(
            r"\bboth\b\s+\S[^.]{1,80}?\band\s+\S", re.IGNORECASE,
        ),
    ),
)

# Concessive openers (sentence-initial).
_CONCESSIVE_OPENER_RE = re.compile(
    r"^\s*(?:Although|Though|Even\s+though|While|Whereas|Despite|In\s+spite\s+of|Notwithstanding)\b",
)

# Sentence-initial participial phrase: starts with -ing or -ed
# verb-form, comma after first ~6 words.
# Examples: "Walking down the street, he saw...",
#           "Frustrated by the delay, she..."
_PARTICIPIAL_OPENER_RE = re.compile(
    r"^\s*([A-Z][a-z]+(?:ing|ed))\b[^,.]{0,80},\s",
)

# Sentence-initial fronted adverbial / PP / subordinate clause +
# comma + main clause. Heuristic: comma after 2-7 words that are
# NOT a single -ing/-ed (that's the participial opener) and NOT a
# concessive opener.
_FRONTED_ADVERBIAL_RE = re.compile(
    r"^\s*([A-Z][^,.]{3,80}),\s+[A-Za-z]",
)

# Parenthetical insertion (clause-medial, comma-bounded).
# Heuristic: subject + ", X, " + predicate. Excludes appositive
# proper-noun forms (those are usually short NP, NP, NP).
_PARENTHETICAL_RE = re.compile(
    r"\b\w+(?:\s+\w+){0,3}\s*,\s+([a-z][^,]{4,80}),\s+\w",
)


# ---------- spaCy-enhanced detectors ----------


def _detect_passive(
    sentence: str, sent_idx: int,
) -> tuple[ConstructionHit | None, ConstructionHit | None]:
    """Return (agented_hit, agentless_hit) for the sentence.

    Uses spaCy's ``nsubjpass`` + ``auxpass`` + optional ``agent``
    dependency to find passive constructions and classify them.
    Returns (None, None) when spaCy is unavailable or no passive
    is detected. (At most one of the two will be non-None per
    sentence, but the function returns both slots so the caller
    can pack them into the right ConstructionResult.)
    """
    if not HAS_SPACY or _NLP is None:
        return None, None
    try:
        doc = _NLP(sentence)
    except Exception:
        return None, None

    # Find subjects with passive auxiliary.
    for token in doc:
        if token.dep_ != "nsubjpass":
            continue
        # Find the head verb (the past participle).
        head = token.head
        if head is None:
            continue
        # Look for an agent (`by`-phrase) attached to the head.
        has_agent = any(
            child.dep_ == "agent" for child in head.children
        )
        span = doc[token.left_edge.i : head.right_edge.i + 1].text
        hit = ConstructionHit(
            construction=(
                "agented_passive" if has_agent else "agentless_passive"
            ),
            sentence_index=sent_idx,
            text=sentence,
            span=span,
            note=(
                "passive with `by`-phrase" if has_agent
                else "passive without explicit agent"
            ),
        )
        if has_agent:
            return hit, None
        return None, hit
    return None, None


def _detect_stacked_pps(
    sentence: str, sent_idx: int, *, min_run: int = 3,
) -> ConstructionHit | None:
    """Detect 3+ consecutive prepositional phrases at clause end.

    Uses spaCy's POS tags. Returns a single ConstructionHit when a
    run of ``min_run`` or more PPs (preposition + nominal) is
    found near the end of the sentence; returns None otherwise.
    """
    if not HAS_SPACY or _NLP is None:
        return None
    try:
        doc = _NLP(sentence)
    except Exception:
        return None

    # Find consecutive PPs by walking the dep tree: each "prep"
    # head followed by another "prep" attached to the same chain.
    run_count = 0
    run_start_token = None
    last_prep_end = -1
    best_run = 0
    best_start = None
    for token in doc:
        if token.dep_ in {"prep", "ADP"} or token.tag_ in {"IN", "ADP"}:
            if run_count == 0:
                run_start_token = token
            run_count += 1
            last_prep_end = token.i
            if run_count > best_run:
                best_run = run_count
                best_start = run_start_token
        elif token.is_alpha and token.pos_ not in {"NOUN", "PROPN", "PRON", "DET", "ADJ", "NUM"}:
            run_count = 0
            run_start_token = None

    if best_run >= min_run and best_start is not None:
        span = doc[best_start.i : last_prep_end + 1].text
        return ConstructionHit(
            construction="stacked_prepositional_phrases",
            sentence_index=sent_idx,
            text=sentence,
            span=span,
            note=f"{best_run} consecutive preposition heads",
        )
    return None


# ---------- Per-sentence scan ----------


# Construction registry: (key, label, description, requires_spacy).
_CONSTRUCTION_REGISTRY: tuple[tuple[str, str, str, bool], ...] = (
    (
        "cleft", "Cleft",
        "It-cleft: \"It is X that/who Y\". Foregrounds X. "
        "Over-density reads as authoritative-emphasis cadence.",
        False,
    ),
    (
        "pseudo_cleft", "Pseudo-cleft",
        "Wh-cleft: \"What X is Y\" / \"What matters is...\". "
        "Common in essayistic / explanatory prose.",
        False,
    ),
    (
        "existential_there", "Existential there",
        "\"There is/are X\" frame. Over-density flattens the "
        "subject layer; common AI smoothing artifact.",
        False,
    ),
    (
        "extraposition", "Extraposition",
        "\"It is X to Y\" / \"It is X that Y\" with predicative X. "
        "Standard in academic prose; over-density reads as "
        "institutional cadence.",
        False,
    ),
    (
        "correlative", "Correlative construction",
        "Paired conjunctions: not only / but also, either / or, "
        "neither / nor, both / and, not just / but. Voice-"
        "characteristic at moderate density; AI-characteristic at "
        "high density.",
        False,
    ),
    (
        "concessive_opener", "Concessive opener",
        "Sentence-initial Although / Though / While / Even though "
        "/ Despite / Whereas. Over-density reads as essayistic "
        "balance-seeking rhythm.",
        False,
    ),
    (
        "participial_opener", "Participial opener",
        "Sentence-initial -ing or -ed phrase + comma + main "
        "clause. Voice-characteristic in literary prose; over-"
        "density reads as MFA-workshop rhythm.",
        False,
    ),
    (
        "fronted_adverbial", "Fronted adverbial",
        "Sentence-initial PP or adverbial clause + comma + main "
        "clause. High density indicates frequent topicalization "
        "of circumstances over subjects.",
        False,
    ),
    (
        "parenthetical_insertion", "Parenthetical insertion",
        "Comma-bounded clause-medial insertion. Voice-"
        "characteristic in essayistic prose; over-density reads "
        "as Schultzian tic.",
        False,
    ),
    (
        "agented_passive", "Agented passive",
        "Passive voice with explicit by-phrase. Preserves agency.",
        True,
    ),
    (
        "agentless_passive", "Agentless passive",
        "Passive voice WITHOUT a by-phrase. Erases agency. Over-"
        "density reads as bureaucratic / institutional register.",
        True,
    ),
    (
        "stacked_prepositional_phrases", "Stacked PPs",
        "3+ consecutive prepositional phrases at clause end. "
        "Reads as nominalized / abstract cadence.",
        True,
    ),
)

# Public list of construction keys, exposed so the CLI's
# argparse `choices` and external callers can validate filter
# names against the registry without depending on the private
# tuple shape.
CONSTRUCTION_KEYS: tuple[str, ...] = tuple(
    c[0] for c in _CONSTRUCTION_REGISTRY
)


def _new_results() -> dict[str, ConstructionResult]:
    """Initialize empty per-construction result containers."""
    return {
        key: ConstructionResult(
            construction=key,
            label=label,
            description=description,
            requires_spacy=requires_spacy,
            available=(not requires_spacy) or HAS_SPACY,
        )
        for key, label, description, requires_spacy
        in _CONSTRUCTION_REGISTRY
    }


def detect_constructions(
    text: str,
    *,
    keep_quotes: bool = False,
) -> tuple[dict[str, ConstructionResult], int]:
    """Scan ``text`` and return (per-construction results, n_words).

    Strips Markdown blockquotes by default (lines starting with
    ``>``) so quoted material doesn't inflate the writer's
    construction density. Same convention `aic_pattern_audit` uses.
    """
    if not keep_quotes:
        text = "\n".join(
            line for line in text.splitlines()
            if not line.lstrip().startswith(">")
        )

    sentences = split_sentences(text)
    n_words = len(re.findall(r"\b\w+\b", text))
    results = _new_results()

    for idx, sent in enumerate(sentences):
        sent = sent.strip()
        if not sent:
            continue

        # --- regex-only constructions ---
        # Extraposition is detected FIRST: when X in "It is X that/to Y"
        # is a predicative adjective / nominal, the construction is
        # extraposition, not cleft. The cleft step then excludes
        # spans already claimed by extraposition.

        extraposition_spans: list[tuple[int, int]] = []
        for regex in (_EXTRAPOSITION_TO_RE, _EXTRAPOSITION_THAT_RE):
            for m in regex.finditer(sent):
                # Avoid double-counting if both regexes hit the same span.
                if any(
                    not (m.end() <= s[0] or m.start() >= s[1])
                    for s in extraposition_spans
                ):
                    continue
                results["extraposition"].hits.append(ConstructionHit(
                    construction="extraposition",
                    sentence_index=idx,
                    text=sent, span=m.group(0)[:80],
                ))
                extraposition_spans.append((m.start(), m.end()))

        for m in _CLEFT_RE.finditer(sent):
            # Skip if this cleft span overlaps an extraposition span.
            overlap = any(
                not (m.end() <= s[0] or m.start() >= s[1])
                for s in extraposition_spans
            )
            if overlap:
                continue
            results["cleft"].hits.append(ConstructionHit(
                construction="cleft", sentence_index=idx,
                text=sent, span=m.group(0)[:80],
            ))

        for m in _PSEUDO_CLEFT_RE.finditer(sent):
            results["pseudo_cleft"].hits.append(ConstructionHit(
                construction="pseudo_cleft", sentence_index=idx,
                text=sent, span=m.group(0)[:80],
            ))

        for m in _EXISTENTIAL_RE.finditer(sent):
            results["existential_there"].hits.append(ConstructionHit(
                construction="existential_there", sentence_index=idx,
                text=sent, span=m.group(0)[:80],
            ))

        for label, regex in _CORRELATIVE_PATTERNS:
            for m in regex.finditer(sent):
                results["correlative"].hits.append(ConstructionHit(
                    construction="correlative", sentence_index=idx,
                    text=sent, span=m.group(0)[:80],
                    note=label,
                ))

        if _CONCESSIVE_OPENER_RE.match(sent):
            opener = sent.split(",", 1)[0]
            results["concessive_opener"].hits.append(ConstructionHit(
                construction="concessive_opener", sentence_index=idx,
                text=sent, span=opener[:80],
            ))

        m_part = _PARTICIPIAL_OPENER_RE.match(sent)
        if m_part:
            results["participial_opener"].hits.append(ConstructionHit(
                construction="participial_opener",
                sentence_index=idx,
                text=sent, span=m_part.group(0)[:80],
            ))

        # Fronted adverbial: sentence has comma after first 2-7
        # words AND the prefix is not the participial opener AND
        # not the concessive opener AND no quote-attribution.
        if (
            not m_part
            and not _CONCESSIVE_OPENER_RE.match(sent)
            and '"' not in sent[:30]
            and "'" not in sent[:30]
        ):
            m_front = _FRONTED_ADVERBIAL_RE.match(sent)
            if m_front:
                prefix = m_front.group(1)
                # Filter out very short prefixes (likely vocatives /
                # interjections) and very long prefixes (likely
                # mis-bounded). 2-7 words = reasonable adverbial.
                n_prefix_words = len(prefix.split())
                if 2 <= n_prefix_words <= 8:
                    results["fronted_adverbial"].hits.append(
                        ConstructionHit(
                            construction="fronted_adverbial",
                            sentence_index=idx,
                            text=sent, span=prefix[:80],
                        )
                    )

        for m in _PARENTHETICAL_RE.finditer(sent):
            results["parenthetical_insertion"].hits.append(
                ConstructionHit(
                    construction="parenthetical_insertion",
                    sentence_index=idx,
                    text=sent, span=m.group(1)[:80],
                )
            )

        # --- spaCy-enhanced constructions ---
        if HAS_SPACY and _NLP is not None:
            agented, agentless = _detect_passive(sent, idx)
            if agented:
                results["agented_passive"].hits.append(agented)
            if agentless:
                results["agentless_passive"].hits.append(agentless)

            stacked = _detect_stacked_pps(sent, idx)
            if stacked:
                results["stacked_prepositional_phrases"].hits.append(
                    stacked
                )

    return results, n_words


# ---------- Baseline aggregation ----------


def aggregate_baseline_densities(
    baseline_dir: Path,
    *,
    keep_quotes: bool = False,
    target_path: Path | None = None,
) -> tuple[dict[str, float], int, list[Path], list[Path]]:
    """Walk a baseline directory and aggregate per-construction
    density-per-1k. Returns ``(densities, total_words, loaded,
    skipped)``.

    When ``target_path`` is supplied, any baseline entry whose
    resolved path equals the resolved target path is filtered
    out — same self-overlap-guard convention `paragraph_audit`
    (1.34.1), `general_imposters` (1.29.1), and `controls_audit`
    (1.37.1) use. The audited target must not be its own baseline.

    Mirrors the aggregation shape `aic_pattern_audit` uses.
    """
    if not baseline_dir.exists():
        raise FileNotFoundError(
            f"Baseline directory not found: {baseline_dir}"
        )
    if not baseline_dir.is_dir():
        raise NotADirectoryError(
            f"--baseline-dir is not a directory: {baseline_dir}"
        )

    counts: dict[str, int] = {
        key: 0 for key, *_ in _CONSTRUCTION_REGISTRY
    }
    total_words = 0
    loaded: list[Path] = []
    skipped: list[Path] = []

    target_resolved = (
        target_path.resolve() if target_path else None
    )

    for path in sorted(baseline_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {
            ".txt", ".md", ".markdown", ".rst",
        }:
            skipped.append(path)
            continue
        # Self-overlap guard: drop the target itself if it lives
        # under the baseline directory.
        if (
            target_resolved is not None
            and path.resolve() == target_resolved
        ):
            skipped.append(path)
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            skipped.append(path)
            continue
        if not text.strip():
            skipped.append(path)
            continue
        results, n_words = detect_constructions(
            text, keep_quotes=keep_quotes,
        )
        if n_words == 0:
            skipped.append(path)
            continue
        loaded.append(path)
        total_words += n_words
        for key, result in results.items():
            counts[key] += result.count

    densities = {
        key: (counts[key] / total_words * 1000) if total_words else 0.0
        for key in counts
    }
    return densities, total_words, loaded, skipped


# ---------- Audit assembly ----------


def build_audit(
    *,
    target_path: Path,
    target_text: str,
    target_results: dict[str, ConstructionResult],
    target_words: int,
    baseline_density_per_1k: dict[str, float] | None,
    baseline_loaded: list[Path],
    baseline_skipped: list[Path],
    baseline_words: int,
    top: int,
    construction_filter: list[str] | None,
    include_baseline_filenames: bool,
    target_ai_status: str | None = None,
) -> dict[str, Any]:
    keys = list(target_results.keys())
    if construction_filter:
        keys = [k for k in keys if k in construction_filter]

    out: dict[str, Any] = {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "target": str(target_path),
        "target_words": target_words,
        "spacy_available": HAS_SPACY,
        "constructions": {},
    }

    if baseline_density_per_1k is not None:
        if include_baseline_filenames:
            out["baseline_files_loaded"] = [
                str(p) for p in baseline_loaded
            ]
            out["baseline_files_skipped"] = [
                str(p) for p in baseline_skipped
            ]
        else:
            # Privacy-by-default: anonymized counts only.
            out["baseline_files_loaded_count"] = len(baseline_loaded)
            out["baseline_files_skipped_count"] = len(baseline_skipped)
        out["baseline_words"] = baseline_words

    for k in keys:
        r = target_results[k]
        target_density = (
            r.count / target_words * 1000 if target_words else 0.0
        )
        block: dict[str, Any] = {
            "label": r.label,
            "description": r.description,
            "requires_spacy": r.requires_spacy,
            "available": r.available,
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
        out["constructions"][k] = block

    out["claim_license"] = _claim_license_dict(
        target_words=target_words,
        baseline_loaded=len(baseline_loaded),
        n_constructions_available=sum(
            1 for r in target_results.values() if r.available
        ),
        target_ai_status=target_ai_status,
    )
    # B.3: surface ai_status at the top of the audit dict so JSON
    # consumers can route on state without re-passing the flag.
    if target_ai_status:
        out["ai_status"] = target_ai_status
    return out


def _claim_license_dict(
    *,
    target_words: int,
    baseline_loaded: int,
    n_constructions_available: int,
    target_ai_status: str | None = None,
) -> dict[str, Any]:
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Per-construction density (count and per-1k) for each "
            "of up to 12 named syntactic constructions in the "
            "target text, with optional baseline comparison. The "
            "audit makes the writer's syntactic-construction "
            "vocabulary visible at the level of named figures "
            "(\"this draft uses fronted adverbials at 2.4× the "
            "writer's baseline density\")."
        ),
        does_not_license=(
            "A provenance verdict. Construction density is a "
            "**voice-coherence layer**, not a classifier. AI-"
            "shaped prose has characteristic construction "
            "preferences (high agentless passive, high "
            "extraposition, high stacked PPs), but so do many "
            "legitimate registers (institutional / legal / "
            "academic prose). Pair with the confounder audit and "
            "the evidentiary-conditions gate before drawing any "
            "conclusion."
        ),
        comparison_set={
            "target_words": target_words,
            "n_baseline_files": baseline_loaded,
            "n_constructions_available": n_constructions_available,
            "spacy_available": HAS_SPACY,
        },
        additional_caveats=[
            "Three constructions (agented passive, agentless "
            "passive, stacked PPs) require spaCy + en_core_web_sm; "
            "they report `available: false` when spaCy is "
            "unavailable rather than producing degraded results.",
            "The regex-only detectors are heuristic. They "
            "favor recall over precision: occasional false "
            "positives are expected; the per-construction `hits` "
            "list lets the user audit them.",
            "Densities are calibration-pending. No corpus-wide "
            "construction-density bands have shipped yet — "
            "interpret in absolute terms (counts) until "
            "calibration land.",
        ],
    )
    # B.3: append state-routed caveats when the operator supplied
    # --ai-status. No-op when target_ai_status is None — pre-B.3
    # callers keep their previous behavior.
    lic = with_state_caveats(lic, target_ai_status=target_ai_status)
    block = lic.render_block().rstrip()
    return {"rendered": block}


# ---------- Markdown rendering ----------


def render_report(audit: dict[str, Any]) -> str:
    constructions = audit.get("constructions", {})
    spacy_available = audit.get("spacy_available", False)

    lines: list[str] = [
        "# Construction signature audit",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Target:** `{audit.get('target')}`",
        f"**Target words:** {audit.get('target_words', 0)}",
        f"**spaCy available:** {'yes' if spacy_available else 'no'}",
        "",
    ]

    if "baseline_files_loaded" in audit or "baseline_files_loaded_count" in audit:
        n_loaded = (
            len(audit.get("baseline_files_loaded", []))
            if "baseline_files_loaded" in audit
            else audit.get("baseline_files_loaded_count", 0)
        )
        lines.append(
            f"**Baseline:** {n_loaded} files, "
            f"{audit.get('baseline_words', 0)} words"
        )
        lines.append("")

    # Per-construction table.
    lines.append("## Per-construction density")
    lines.append("")
    headers = ["construction", "count", "per_1k"]
    if any(
        "baseline_density_per_1k" in b for b in constructions.values()
    ):
        headers += ["baseline_per_1k", "Δ_per_1k"]
    headers += ["available"]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for key, block in constructions.items():
        row = [
            block.get("label", key),
            str(block.get("count", 0)),
            f"{block.get('density_per_1k', 0.0):.2f}",
        ]
        if "baseline_density_per_1k" in block:
            row.append(f"{block['baseline_density_per_1k']:.2f}")
            row.append(f"{block.get('delta_per_1k', 0.0):+.2f}")
        row.append(
            "yes" if block.get("available", True) else "no"
        )
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # Top hits per construction (only those with hits).
    nonzero = [
        (key, block) for key, block in constructions.items()
        if block.get("count", 0) > 0
    ]
    if nonzero:
        lines.append("## Top hits")
        lines.append("")
        for key, block in nonzero:
            lines.append(f"### `{block.get('label', key)}`")
            lines.append("")
            lines.append(block.get("description", ""))
            lines.append("")
            for h in block.get("hits", []):
                excerpt = h.get("text", "").strip()
                if len(excerpt) > 200:
                    excerpt = excerpt[:200] + "…"
                lines.append(
                    f"- **[{h.get('sentence_index')}]** "
                    f"`{h.get('span', '')}` — {excerpt}"
                )
            lines.append("")

    license_block = audit.get("claim_license", {}).get("rendered", "")
    if license_block:
        lines.append(license_block)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------- CLI ----------


def _read_target(path_str: str) -> tuple[Path, str]:
    p = Path(path_str).expanduser()
    if not p.is_file():
        raise FileNotFoundError(
            f"Target file not found: {path_str}"
        )
    return p, p.read_text(encoding="utf-8", errors="ignore")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="construction_signature_audit.py",
        description=(
            "Per-construction syntactic-construction density audit. "
            "12 named constructions: cleft, pseudo-cleft, existential "
            "there, extraposition, correlative, concessive opener, "
            "participial opener, fronted adverbial, parenthetical "
            "insertion, agented passive, agentless passive, stacked "
            "PPs (last three need spaCy)."
        ),
    )
    p.add_argument(
        "target",
        help="Path to the target text (.txt / .md / .rst).",
    )
    p.add_argument(
        "--baseline-dir",
        help="Directory of baseline files for density comparison.",
    )
    p.add_argument(
        "--top", type=int, default=20,
        help="Top N hits per construction to include "
             "(default 20).",
    )
    p.add_argument(
        "--construction", action="append", dest="constructions",
        choices=list(CONSTRUCTION_KEYS),
        help="Restrict to specific constructions. Repeat for "
             "multiple. Default: all. argparse `choices` rejects "
             "typos at parse time so a misspelled construction "
             "name fails loudly rather than producing an empty "
             "audit.",
    )
    p.add_argument(
        "--keep-quotes", action="store_true",
        help="Don't strip Markdown blockquotes (lines starting "
             "with `>`). Default: strip them.",
    )
    p.add_argument(
        "--include-baseline-filenames", action="store_true",
        help="Privacy-default opt-out: include baseline filenames "
             "in the JSON output. Default: anonymize (counts only).",
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--out")
    # B.3 (v1.53.0+): authorship-state routing for the ClaimLicense
    # block. The operator's manifest entry for the target carries
    # an `ai_status` value (pre_ai_human, ai_generated_from_outline,
    # etc.). Surface it to the audit so the rendered license block
    # carries the matching state-specific caveats. Per SPEC §9.2,
    # this is the operational consequence of the B.2 vocabulary —
    # not threshold-shipping, just per-state licensure language.
    p.add_argument(
        "--ai-status",
        default=None,
        help=(
            "Manifest ai_status value for the target text (e.g., "
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
        target_path, target_text = _read_target(args.target)
    except FileNotFoundError as exc:
        sys.stderr.write(f"target: {exc}\n")
        return 2

    target_results, target_words = detect_constructions(
        target_text, keep_quotes=args.keep_quotes,
    )
    if target_words == 0:
        sys.stderr.write(
            f"target: file is empty (0 words): {args.target}\n"
        )
        return 2

    baseline_density: dict[str, float] | None = None
    baseline_loaded: list[Path] = []
    baseline_skipped: list[Path] = []
    baseline_words = 0
    if args.baseline_dir:
        try:
            (
                baseline_density,
                baseline_words,
                baseline_loaded,
                baseline_skipped,
            ) = aggregate_baseline_densities(
                Path(args.baseline_dir).expanduser(),
                keep_quotes=args.keep_quotes,
                target_path=target_path,
            )
        except (FileNotFoundError, NotADirectoryError) as exc:
            sys.stderr.write(f"--baseline-dir: {exc}\n")
            return 2
        if not baseline_loaded:
            sys.stderr.write(
                "--baseline-dir: no readable .txt/.md/.rst "
                "baseline files remained after filtering. The "
                "target file cannot also be its own baseline; "
                "supply a directory of OTHER files.\n"
            )
            return 2

    audit = build_audit(
        target_path=target_path,
        target_text=target_text,
        target_results=target_results,
        target_words=target_words,
        baseline_density_per_1k=baseline_density,
        baseline_loaded=baseline_loaded,
        baseline_skipped=baseline_skipped,
        baseline_words=baseline_words,
        top=args.top,
        construction_filter=args.constructions,
        include_baseline_filenames=args.include_baseline_filenames,
        target_ai_status=args.ai_status,
    )

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
