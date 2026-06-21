#!/usr/bin/env python3
"""discourse_move_signature.py — typed discourse markers + move sequences.

Surfaces Tier-1 build, paired-release schedule Release 3.
Consumed by confounder_audit.py as load-bearing evidence for the
differential diagnosis ("legal/policy memo style" vs. "AI smoothing"
vs. "professional copyediting"); without typed-discourse evidence
the confounder matrix can't separate institutional prose from
AI-smoothed prose.

The shipped Layer A suite measures *connective density* as a single
ratio. That captures "how much scaffolding is present" but not
"what kind of scaffolding." A writer who concedes-then-reverses-
then-narrows uses different markers than a policy memo that
elaborates-and-recommends-and-cautions, even if both are equally
"scaffolded." This module types the markers and surfaces both
per-category density and **move-sequence bigrams** — which
adjacent move-pairs the writer falls into.

Categories (typology):

  - contrast: however, but, yet, still, nevertheless, on the other hand
  - concession: admittedly, granted, of course, although, while, despite
  - consequence: therefore, so, thus, hence, consequently, as a result
  - elaboration: in other words, that is, namely, specifically
  - exemplification: for example, for instance, such as, including
  - sequencing: first, second, finally, next, then, subsequently
  - reframing: the better question, more precisely, what matters is
  - epistemic_stance: maybe, likely, apparently, perhaps, possibly
  - boosting: clearly, obviously, definitely, certainly, indeed
  - hedging: somewhat, sort of, more or less, arguably, to some extent
  - self_correction: or rather, not exactly, more accurately
  - metadiscourse: as discussed above, in this section, returning to

Output:

  - Per-category density (per 1000 words).
  - Move-sequence bigrams: count of consecutive (move_i, move_{i+1})
    transitions across sentences.
  - Move-sequence entropy in bits — low entropy means scripted
    argumentative cadence (concession→reversal→claim repeated, or
    elaboration→exemplification→consequence repeated).
  - Compression-fraction band call — heuristic, calibration pending.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import (  # type: ignore
    ClaimLicense, with_state_caveats,
)
from output_schema import build_baseline_metadata, build_output  # type: ignore
from preprocessing import strip_non_prose  # type: ignore

TASK_SURFACE = "smoothing_diagnosis"
TOOL_NAME = "discourse_move_signature"
SCRIPT_VERSION = "1.0"


# --- Marker typology -------------------------------------------
#
# Each category maps to a tuple of regex patterns. Patterns use
# (?im) flags (case-insensitive, multi-line) and assume word
# boundaries. Order matters within a category only for which
# pattern wins on overlapping matches; the per-category density is
# the deduplicated count regardless.

_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "contrast": (
        re.compile(r"\b(?:however|but|yet|still|nevertheless|nonetheless)\b", re.I),
        re.compile(r"\b(?:on the other hand|in contrast|conversely|by contrast|whereas)\b", re.I),
    ),
    "concession": (
        re.compile(r"\b(?:admittedly|granted|of course|to be sure|certainly|true)\b,", re.I),
        re.compile(r"\b(?:although|though|while|despite|in spite of|even though|even if)\b", re.I),
    ),
    "consequence": (
        re.compile(r"\b(?:therefore|thus|hence|consequently|accordingly)\b", re.I),
        re.compile(r"\b(?:as a result|so that|for that reason|that is why|which is why)\b", re.I),
    ),
    "elaboration": (
        re.compile(r"\b(?:in other words|that is|namely|specifically|in particular)\b", re.I),
        re.compile(r"\b(?:more (?:precisely|specifically|formally)|put (?:another|differently))\b", re.I),
    ),
    "exemplification": (
        re.compile(r"\b(?:for example|for instance|e\.?g\.?|such as|including|like)\b", re.I),
        re.compile(r"\b(?:consider|take|imagine)\s+(?:the|a|an)\s+\w+", re.I),
    ),
    "sequencing": (
        re.compile(r"\b(?:first(?:ly)?|second(?:ly)?|third(?:ly)?|finally|lastly)\b,?", re.I),
        re.compile(r"\b(?:next|then|subsequently|afterwards|meanwhile|in turn)\b", re.I),
    ),
    "reframing": (
        re.compile(r"\b(?:the (?:better|deeper|real|right) question is)\b", re.I),
        re.compile(r"\b(?:what matters is|the point is|more (?:importantly|to the point))\b", re.I),
    ),
    "epistemic_stance": (
        re.compile(r"\b(?:maybe|perhaps|possibly|likely|apparently|presumably|supposedly)\b", re.I),
        re.compile(r"\b(?:may|might|could)\s+(?:be|have|seem|suggest|indicate)\b", re.I),
        re.compile(r"\b(?:I\s+(?:think|believe|suspect|guess))\b", re.I),
    ),
    "boosting": (
        re.compile(r"\b(?:clearly|obviously|definitely|certainly|undeniably|indeed)\b", re.I),
        re.compile(r"\b(?:of course|without (?:doubt|question)|as everyone knows)\b", re.I),
    ),
    "hedging": (
        re.compile(r"\b(?:somewhat|sort of|kind of|more or less|to some extent|arguably)\b", re.I),
        re.compile(r"\b(?:in (?:some|certain) (?:sense|ways|cases)|to a (?:degree|certain extent))\b", re.I),
    ),
    "self_correction": (
        re.compile(r"\b(?:or rather|or more (?:accurately|precisely)|to put it (?:differently|another way))\b", re.I),
        re.compile(r"\b(?:not (?:exactly|quite)|better:?\s+|let me rephrase)\b", re.I),
    ),
    "metadiscourse": (
        re.compile(r"\b(?:as (?:discussed|noted|argued|shown) (?:above|earlier|previously|before))\b", re.I),
        re.compile(r"\b(?:in this (?:section|chapter|essay|piece)|returning to|coming back to)\b", re.I),
        re.compile(r"\b(?:as I (?:mentioned|said) (?:above|earlier|before))\b", re.I),
    ),
}

CATEGORIES = tuple(_PATTERNS.keys())


# --- PDTB explicit-connective relation layer -------------------
#
# An ADDITIVE, parallel read alongside the marker typology above.
# Each PDTB top-level relation sense (Comparison / Contingency /
# Expansion / Temporal) is matched independently over the full text
# from a static connective lexicon, and the document's explicit
# discourse-relation *mix* is emitted as descriptive stylometric
# shape (counts / per-1k densities / fractions / entropy). This is
# the EXPLICIT-CONNECTIVE PROXY of arXiv:2307.03378 ("Side-by-side
# Transformers for Implicit Discourse Relation Classification,
# PDTB-3", arXiv:2307.03378): the paper's actual contribution — a
# trained classifier for IMPLICIT (unsignalled) relations — is the
# gated model-CPU M2 seam (see the M2 note below `audit_explicit
# _relations`), NOT this stdlib M1 layer.
#
# Design (spec §3.2, D1/D2):
#   * Top-level 4-way taxonomy only (D4); second-level senses
#     (Cause vs Condition, Contrast vs Concession, …) need argument
#     spans the surface form alone can't supply → M2.
#   * Each connective is assigned a SINGLE majority top-level class
#     from PDTB-3 frequency (D1). M1 does not disambiguate
#     polysemous connectives per-occurrence (that needs the Arg1/
#     Arg2 spans → M2); instead the genuinely cross-bucket
#     connectives are tracked in `_AMBIGUOUS_CONNECTIVES` so
#     `ambiguous_connective_fraction` can report their share as an
#     honesty/confidence valve.
#   * This is a PARALLEL independent count over the full text (D2),
#     NOT a re-bucketing of `classify_sentence` (which is per-
#     sentence first-match). A sentence with two connectives
#     contributes two relation occurrences. The two layers answer
#     different questions and are allowed to disagree.
#
# Majority-class assignments for the balanced-polysemous connectives
# ("as", "while", "since", "so", "after") follow PDTB-3 published
# top-level frequency: "while"→Comparison (Concession/Contrast
# dominate its explicit use), "since"→Temporal (Asynchronous edges
# the Cause reading in explicit use), "as"→Temporal, "so"→
# Contingency (Result), "after"→Temporal. Any reasonable assignment
# is acceptable because `ambiguous_connective_fraction` flags them
# (spec §10); the value is corpus-independent (anti-Goodhart, spec
# §6 guard 15) — a static linguistic inventory, never fit to any
# SETEC validation/impostor corpus.

RELATION_BUCKETS = ("comparison", "contingency", "expansion", "temporal")

_PDTB_CONNECTIVES: dict[str, tuple[re.Pattern[str], ...]] = {
    # Comparison — Contrast / Concession / Similarity.
    "comparison": (
        re.compile(
            r"\b(?:however|but|yet|although|though|whereas|nevertheless"
            r"|nonetheless|conversely|similarly|likewise|while)\b",
            re.I,
        ),
        re.compile(
            r"\b(?:in contrast|on the other hand|by comparison|even though)\b",
            re.I,
        ),
    ),
    # Contingency — Cause / Condition / Purpose / Negative-condition.
    "contingency": (
        re.compile(
            r"\b(?:because|therefore|thus|hence|consequently|if|unless|so)\b",
            re.I,
        ),
        re.compile(
            r"\b(?:as a result|so that|in order to|for this reason)\b",
            re.I,
        ),
    ),
    # Expansion — Conjunction / Instantiation / Restatement / …
    "expansion": (
        re.compile(
            r"\b(?:and|also|furthermore|moreover|specifically|namely"
            r"|instead|rather|besides|indeed|or)\b",
            re.I,
        ),
        re.compile(
            r"\b(?:in addition|for example|for instance|in particular"
            r"|that is|in other words)\b",
            re.I,
        ),
    ),
    # Temporal — Synchronous / Asynchronous.
    "temporal": (
        re.compile(
            r"\b(?:then|next|after|before|when|meanwhile|subsequently"
            r"|finally|previously|simultaneously|until|once|since|as)\b",
            re.I,
        ),
        re.compile(r"\b(?:as soon as)\b", re.I),
    ),
}

# Genuinely cross-bucket PDTB connectives (Comparison(Concession) vs
# Temporal(Synchronous) for "while"; Contingency(Cause) vs Temporal
# (Asynchronous) for "since"; Contingency/Temporal/Comparison for
# "as"; Result vs Purpose-ish for "so"; …). Each is assigned a
# single majority class above; this set drives
# `ambiguous_connective_fraction`. Matched case-insensitively as
# whole words to mirror the lexicon's `\b…\b` matching.
#
# INVARIANT (mode-6 / honesty): every member here MUST also be a
# lexicon match (i.e. counted in `n_explicit_connectives`), so the
# ambiguous count is a strict subset of the explicit count and the
# reported fraction is a true share in [0, 1] — never an artifact of
# counting a word that the relation buckets don't. A regression test
# (`test_ambiguous_set_is_subset_of_lexicon`) pins this. ("still" /
# "yet" as bare intensifiers are deliberately excluded unless they
# are also a counted relation connective.)
_AMBIGUOUS_CONNECTIVES: frozenset[str] = frozenset({
    "while", "since", "as", "so", "after", "before", "when", "yet",
})

# NOTE: the ambiguous count is no longer a separate `findall` over an
# `_AMBIGUOUS_RE` — that re-counted bare `as` inside `as a result` /
# `as soon as` and could exceed the explicit count. It is now read off
# the SAME consumed, non-overlapping spans as the explicit count (see
# `audit_explicit_relations`), so the subset/honesty invariant holds
# by construction.


# --- Single-pass, non-overlapping, longest-match-first matcher -----
#
# THE LOAD-BEARING ONE-OCCURRENCE-ONE-BUCKET INVARIANT (spec §2):
# every text span is consumed by EXACTLY ONE connective and bucketed
# ONCE. The naive design (an independent ``findall`` per pattern) is
# wrong: a multi-word connective whose constituent words are also
# single-word lexicon connectives gets multi-counted, and a bare
# single-word connective embedded in a longer phrase from a DIFFERENT
# bucket leaks into the wrong bucket. Concretely, with independent
# passes: ``as a result`` -> contingency:1 + a spurious temporal:1
# (bare ``as``); ``as soon as`` -> temporal:3 (bare ``as`` twice +
# the phrase once); ``so that`` -> contingency:2; ``even though`` ->
# comparison:2. That inflates ``n_explicit_connectives`` and skews
# every downstream value (fractions / density / entropy / ambiguous
# fraction / baseline z-scores).
#
# Fix: extract every surface form from ``_PDTB_CONNECTIVES`` (kept as
# the single source of truth so the corpus-independence and
# ambiguous-subset invariants still hold), order them LONGEST-FIRST
# globally, and compile ONE combined alternation. ``re.finditer``
# consumes each span exactly once and, because alternatives are tried
# in order, the longest form at any start position wins
# (``as soon as`` beats ``as``; ``as a result`` beats ``as``). The
# ambiguous count is read off the SAME consumed spans, so it is a
# strict subset of the explicit count by construction.

_ALT_RE = re.compile(r"^\\b\(\?:(.*)\)\\b$", re.S)


def _extract_surface_forms(
) -> tuple[tuple[str, str], ...]:
    """Flatten ``_PDTB_CONNECTIVES`` into ``(surface_form, bucket)``
    pairs, ordered longest-form-first so the combined alternation is
    longest-match-first (e.g. ``as soon as`` is tried before ``as``).
    Derived from the compiled lexicon patterns so the lexicon stays
    the single source of truth.
    """
    pairs: list[tuple[str, str]] = []
    for bucket, patterns in _PDTB_CONNECTIVES.items():
        for pattern in patterns:
            m = _ALT_RE.match(pattern.pattern)
            if m is None:  # pragma: no cover - guards lexicon shape
                raise ValueError(
                    f"PDTB lexicon pattern for {bucket!r} is not a "
                    f"simple \\b(?:...)\\b alternation: "
                    f"{pattern.pattern!r}"
                )
            for form in m.group(1).split("|"):
                pairs.append((form, bucket))
    # Longest surface form first (more words, then more chars), so a
    # multi-word connective always out-competes a single-word form
    # that is its substring. Stable tie-break on the form keeps the
    # ordering deterministic.
    pairs.sort(key=lambda fb: (-len(fb[0].split()), -len(fb[0]), fb[0]))
    return tuple(pairs)


_SURFACE_FORMS: tuple[tuple[str, str], ...] = _extract_surface_forms()

# One combined alternation, longest-first. We recover the bucket from
# the matched text via ``_FORM_TO_BUCKET`` (below) rather than from N
# named groups, because a bare-string lookup is simpler and Python
# caps named groups at 100.
_COMBINED_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(f) for f, _ in _SURFACE_FORMS) + r")\b",
    re.I,
)

# Map a matched (lowercased) surface form back to its bucket. Built
# from the same source so it cannot drift. A surface form appears in
# exactly one bucket in the current lexicon; if a future edit puts the
# same form in two buckets, the longest-first order above still makes
# the match deterministic and this map takes the first-seen bucket.
_FORM_TO_BUCKET: dict[str, str] = {}
for _form, _bucket in _SURFACE_FORMS:
    _FORM_TO_BUCKET.setdefault(_form.lower(), _bucket)


_RELATION_ENTROPY_MAX_BITS = math.log2(len(RELATION_BUCKETS))  # 2.0 for 4 buckets


_SENTENCE_TERMINATORS = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(])")
_WORD_RE = re.compile(r"\b\w+\b")


def _split_sentences(text: str) -> list[str]:
    return [
        s.strip()
        for s in _SENTENCE_TERMINATORS.split(text)
        if s.strip()
    ]


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _entropy(counts: dict[Any, int]) -> float:
    """Shannon entropy in bits over a count dict."""
    total = sum(counts.values())
    if total == 0:
        return 0.0
    h = 0.0
    for c in counts.values():
        if c <= 0:
            continue
        p = c / total
        h -= p * math.log2(p)
    return h


def classify_sentence(sentence: str) -> str | None:
    """Return the move category whose markers appear first in the
    sentence, or ``None`` if no marker fires.

    First-match wins so a sentence opening with "However, the
    point is..." classifies as `contrast` (its leading marker)
    rather than `reframing`. This is rough; a sentence with a
    leading "However" and a body "the better question is" is
    plausibly more contrast-led than reframing-led, and the move
    sequence bigram captures the structural pattern either way.
    """
    earliest_pos: int | None = None
    earliest_category: str | None = None
    for category, patterns in _PATTERNS.items():
        for pattern in patterns:
            m = pattern.search(sentence)
            if m is None:
                continue
            pos = m.start()
            if earliest_pos is None or pos < earliest_pos:
                earliest_pos = pos
                earliest_category = category
    return earliest_category


def audit_explicit_relations(text: str, n_words: int) -> dict[str, Any]:
    """Explicit-connective PDTB relation distribution (M1).

    A parallel, independent count over the full text (NOT a re-
    bucketing of ``classify_sentence``): each PDTB connective
    surface form is matched from ``_PDTB_CONNECTIVES`` and bucketed
    to its majority top-level relation class (Comparison /
    Contingency / Expansion / Temporal). Emits the document's
    explicit discourse-relation *mix* as descriptive shape — counts,
    per-1k densities, fractions, and Shannon entropy over the four
    fractions.

    Descriptive, NO-VERDICT: every leaf is a VALUE (count / density
    / fraction / entropy); ``calibration_status`` is ``uncalibrated``
    because there is no calibrated reference for a "normal" explicit-
    relation mix (spec §3.3, D3). The implicit-relation layer (the
    subject of arXiv:2307.03378) is out of M1 scope by construction.

    ``n_words`` is passed in (already computed by the caller) so the
    densities share the host's word count exactly.
    """
    # ONE non-overlapping, longest-match-first pass over the combined
    # lexicon (see `_COMBINED_RE`). Each text span is consumed exactly
    # once and bucketed exactly once, enforcing the spec §2 one-
    # occurrence-one-bucket invariant. The naive per-pattern `findall`
    # loop double/triple-counts overlapping connectives (`as soon as`,
    # `as a result`, `so that`, `even though`) — this pass does not.
    counts: Counter[str] = Counter()
    # Always seed every bucket (zero-filled) so all four are present
    # even when nothing fires.
    for bucket in RELATION_BUCKETS:
        counts[bucket] = 0
    n_ambiguous = 0
    for m in _COMBINED_RE.finditer(text):
        form = " ".join(m.group(0).lower().split())
        bucket = _FORM_TO_BUCKET.get(form)
        if bucket is None:  # pragma: no cover - defensive
            continue
        counts[bucket] += 1
        # The ambiguous count is read off the SAME consumed spans, so
        # it is a strict subset of the explicit count by construction
        # (the honesty invariant — no bare `as` re-counted inside
        # `as a result` / `as soon as`).
        if form in _AMBIGUOUS_CONNECTIVES:
            n_ambiguous += 1

    n_explicit = sum(counts.values())

    density_per_1k = {
        b: (1000.0 * counts[b] / n_words) if n_words else 0.0
        for b in RELATION_BUCKETS
    }
    if n_explicit > 0:
        fractions = {b: counts[b] / n_explicit for b in RELATION_BUCKETS}
    else:
        fractions = {b: 0.0 for b in RELATION_BUCKETS}

    # Shannon entropy in bits over the relation fractions; 0.0 when
    # all mass is in one bucket, max (2.0) when the four are equal.
    relation_entropy = _entropy({b: counts[b] for b in RELATION_BUCKETS})

    ambiguous_fraction = (
        n_ambiguous / n_explicit if n_explicit > 0 else 0.0
    )
    # An ambiguous connective is also a lexicon match, so its share
    # of explicit connectives is bounded by 1.0; clamp defensively
    # against any matching skew (e.g. an ambiguous form the bucket
    # lexicon spells differently) so the field cannot exceed [0, 1].
    ambiguous_fraction = min(1.0, max(0.0, ambiguous_fraction))

    block: dict[str, Any] = {
        "calibration_status": "uncalibrated",
        "n_explicit_connectives": n_explicit,
        "buckets": list(RELATION_BUCKETS),
        "counts": {b: counts[b] for b in RELATION_BUCKETS},
        "density_per_1k": density_per_1k,
        "fractions": fractions,
        "relation_entropy_bits": relation_entropy,
        "relation_entropy_max_bits": _RELATION_ENTROPY_MAX_BITS,
        "ambiguous_connective_fraction": ambiguous_fraction,
    }
    if n_explicit == 0:
        # Present-but-zero is an informative descriptive fact about
        # the writer (no explicitly-signalled relations), not an
        # error (spec D7).
        block["reason"] = "no explicit PDTB connectives matched"
    return block


# --- M2 seam (NOT built in M1) ---------------------------------
#
# arXiv:2307.03378's actual contribution is a TRAINED transformer
# that types the relation between two argument spans with NO
# explicit connective — the IMPLICIT case (the majority of the PDTB
# corpus). That is model-CPU (transformer inference, needs
# `transformers`/`torch` + upstream argument-span segmentation), so
# it is the gated M2 seam, not this stdlib M1 layer.
#
# Seam contract (mirrors the repo's established lazy-import +
# importorskip pattern — e.g. the lazy transformers import in
# `surprisal_backend.py` / `voice_fingerprint.py`, gated in tests by
# `pytest.importorskip` as in `test_voice_fingerprint.py`):
#   * M2 lands an `implicit_relation_distribution` block under (or
#     beside) `relation_distribution`, behind a lazy
#     `importlib.import_module` inside the function and a
#     `--include-implicit` CLI flag that DEFAULTS OFF.
#   * When the dep/model is absent, M1 still runs and the implicit
#     block is simply absent — no crash (the `available`/optional-
#     dep pattern already in the codebase).
#   * M2 tests gate behind `pytest.importorskip("transformers")` so
#     CI without the model stays green and the M1 explicit layer is
#     always exercised.
#   * The capability fragment records the M2 dep under
#     `dependencies.python_optional` only when M2 ships; M1 leaves
#     it empty.


def audit_discourse_moves(text: str) -> dict[str, Any]:
    """Compute per-category densities + move-sequence bigrams +
    move-sequence entropy. Pure function; no I/O.
    """
    n_words = _word_count(text)
    if n_words == 0:
        return {
            "task_surface": TASK_SURFACE,
            "tool": TOOL_NAME,
            "version": SCRIPT_VERSION,
            "available": False,
            "reason": "empty text",
        }

    sentences = _split_sentences(text)

    # Per-category counts (over the whole text — pattern matches
    # across all words, not deduplicated within a sentence).
    category_counts: Counter[str] = Counter()
    for category, patterns in _PATTERNS.items():
        n_matches = 0
        for pattern in patterns:
            n_matches += len(pattern.findall(text))
        if n_matches:
            category_counts[category] = n_matches

    densities = {
        cat: 1000.0 * category_counts.get(cat, 0) / n_words
        for cat in CATEGORIES
    }
    total_marker_density = sum(densities.values())

    # Move sequence: classify each sentence's leading marker (if
    # any). Sentences without a marker label as "_unmarked".
    move_sequence: list[str] = []
    for s in sentences:
        c = classify_sentence(s)
        move_sequence.append(c if c else "_unmarked")

    # Move sequence bigrams: count consecutive (move_i, move_{i+1})
    # across the doc. Bigrams over labels, including _unmarked, so
    # "concession → unmarked → contrast" pairs both transitions.
    bigrams: Counter[tuple[str, str]] = Counter()
    for a, b in zip(move_sequence, move_sequence[1:]):
        bigrams[(a, b)] += 1

    # Move-sequence entropy: how varied is the move pattern? Low
    # entropy means scripted cadence (e.g., concession→reversal→
    # claim repeated). The unmarked-only stretch contributes a
    # single label and therefore low entropy, so we measure two
    # entropies: full (including _unmarked) and marked-only.
    move_counts: Counter[str] = Counter(move_sequence)
    full_entropy = _entropy(dict(move_counts))
    marked_only = {k: v for k, v in move_counts.items() if k != "_unmarked"}
    marked_entropy = _entropy(marked_only)

    # Composite signal: when total marker density is high AND
    # marked-only entropy is low, the prose is scaffolded with a
    # narrow set of move types — the "scripted argumentative
    # cadence" pattern. When density is low or entropy is high,
    # the prose is unscaffolded or freely-varying.
    flagged_signals: list[str] = []
    if total_marker_density >= 30.0:
        flagged_signals.append("high_total_marker_density")
    if marked_entropy <= 1.50 and sum(marked_only.values()) >= 3:
        flagged_signals.append("low_marked_move_entropy")
    if (
        densities.get("concession", 0) >= 5.0
        and densities.get("contrast", 0) >= 5.0
        and densities.get("consequence", 0) >= 3.0
    ):
        flagged_signals.append("dense_concession_contrast_consequence_triad")
    if densities.get("metadiscourse", 0) >= 3.0:
        flagged_signals.append("high_metadiscourse_density")
    if (
        densities.get("hedging", 0) > 4.0
        and densities.get("boosting", 0) > 4.0
    ):
        flagged_signals.append("high_hedging_and_boosting_oscillation")

    n_signals = 5
    compression_fraction = len(flagged_signals) / n_signals
    if compression_fraction < 0.20:
        band = "Lightly scaffolded"
    elif compression_fraction < 0.50:
        band = "Moderately scaffolded"
    else:
        band = "Heavily scaffolded"

    # Additive PDTB explicit-connective relation layer (parallel,
    # independent read over the full text — NOT a re-bucketing of
    # the move sequence above). Descriptive shape, no verdict.
    relation_distribution = audit_explicit_relations(text, n_words)

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "available": True,
        "n_words": n_words,
        "n_sentences": len(sentences),
        "category_counts": dict(category_counts),
        "category_densities_per_1k": densities,
        "total_marker_density_per_1k": total_marker_density,
        "move_sequence": move_sequence,
        "move_sequence_bigrams": {
            f"{a}->{b}": c for (a, b), c in bigrams.most_common()
        },
        "move_sequence_entropy_bits": full_entropy,
        "marked_only_entropy_bits": marked_entropy,
        "relation_distribution": relation_distribution,
        "compression": {
            "band": band,
            "compression_fraction": round(compression_fraction, 3),
            "flagged_signals": flagged_signals,
            "n_flagged": len(flagged_signals),
            "n_signals": n_signals,
        },
    }


# --- Baseline aggregate ----------------------------------------


def audit_baseline_discourse(
    baseline_dir: str,
    *,
    allow_non_prose: bool = False,
    strip_rules: str | Iterable[str] | None = None,
    strip_aggressive: bool = False,
    strip_masking: str | Iterable[str] | None = None,
    target_path: Path | None = None,
    include_filenames: bool = False,
) -> dict[str, Any]:
    """Run the discourse audit across every text file in
    ``baseline_dir``; return aggregate per-category mean+sd plus
    pooled bigram counts.

    1.34.2 hardening (mirrors paragraph_audit / general_imposters
    conventions):
      * ``baseline_dir`` must exist; raises ``FileNotFoundError``.
      * Unreadable / unaudited files surface in ``skipped_files``.
      * When ``target_path`` is supplied, baseline entries whose
        resolved path matches are excluded with a stderr notice.
      * Per-file summaries use anonymized ``baseline_001`` IDs by
        default (filenames often carry private metadata); opt in
        via ``include_filenames=True``.
    """
    base = Path(baseline_dir)
    if not base.is_dir():
        raise FileNotFoundError(
            f"Baseline directory not found or not a directory: "
            f"{baseline_dir}"
        )
    paths = (
        sorted(base.glob("*.txt")) + sorted(base.glob("*.md"))
    )
    paths = [p for p in paths if not p.name.lower().startswith("readme")]

    target_resolved: Path | None = None
    if target_path is not None:
        try:
            target_resolved = Path(target_path).resolve()
        except OSError:
            target_resolved = None

    skipped_files: list[dict[str, str]] = []
    per_file: list[dict[str, Any]] = []
    pooled_density_by_cat: dict[str, list[float]] = {c: [] for c in CATEGORIES}
    pooled_relation_density: dict[str, list[float]] = {
        b: [] for b in RELATION_BUCKETS
    }
    pooled_bigrams: Counter[str] = Counter()
    next_anon_id = 1
    for p in paths:
        if target_resolved is not None:
            try:
                if p.resolve() == target_resolved:
                    sys.stderr.write(
                        f"  excluding {p.name} from discourse "
                        "baseline (matches target path)\n"
                    )
                    continue
            except OSError:
                pass
        try:
            raw = p.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            skipped_files.append({
                "name": p.name if include_filenames else f"file_{len(skipped_files):03d}",
                "reason": f"unreadable: {exc}",
            })
            continue
        cleaned, _ = strip_non_prose(
            raw, strip_rules,
            allow_non_prose=allow_non_prose,
            strip_aggressive=strip_aggressive,
            strip_masking=strip_masking,
        )
        a = audit_discourse_moves(cleaned)
        if not a.get("available"):
            skipped_files.append({
                "name": p.name if include_filenames else f"file_{next_anon_id:03d}",
                "reason": f"audit unavailable: {a.get('reason', 'unknown')}",
            })
            next_anon_id += 1
            continue
        per_file.append({
            "file": (
                p.name if include_filenames
                else f"baseline_{next_anon_id:03d}"
            ),
            "category_densities_per_1k": a["category_densities_per_1k"],
            "marked_only_entropy_bits": a["marked_only_entropy_bits"],
            "total_marker_density_per_1k": a["total_marker_density_per_1k"],
        })
        next_anon_id += 1
        for cat, density in a["category_densities_per_1k"].items():
            pooled_density_by_cat[cat].append(density)
        rel = a.get("relation_distribution", {})
        rel_density = rel.get("density_per_1k", {})
        for bucket in RELATION_BUCKETS:
            pooled_relation_density[bucket].append(
                rel_density.get(bucket, 0.0)
            )
        for bigram_str, count in a["move_sequence_bigrams"].items():
            pooled_bigrams[bigram_str] += count

    def _mean_sd(values: list[float]) -> dict[str, float]:
        if not values:
            return {"mean": 0.0, "sd": 0.0, "n": 0}
        m = sum(values) / len(values)
        if len(values) > 1:
            var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
            sd = math.sqrt(var)
        else:
            sd = 0.0
        return {"mean": m, "sd": sd, "n": len(values)}

    aggregate = {
        cat: _mean_sd(vals)
        for cat, vals in pooled_density_by_cat.items()
    }
    aggregate_relation = {
        bucket: _mean_sd(vals)
        for bucket, vals in pooled_relation_density.items()
    }

    return {
        "n_files": len(per_file),
        "n_skipped": len(skipped_files),
        "skipped_files": skipped_files,
        "per_file_summaries": per_file,
        "aggregate_density_by_category": aggregate,
        "aggregate_relation_density_by_bucket": aggregate_relation,
        "pooled_bigrams": dict(pooled_bigrams),
        "include_filenames": include_filenames,
    }


def compare_to_baseline(
    target: dict[str, Any],
    baseline_block: dict[str, Any],
) -> dict[str, Any]:
    if not target.get("available"):
        return {"available": False, "reason": "target unavailable"}
    if baseline_block.get("n_files", 0) == 0:
        return {"available": False, "reason": "baseline empty"}
    z_scores: dict[str, float | None] = {}
    agg = baseline_block["aggregate_density_by_category"]
    target_dens = target["category_densities_per_1k"]
    for cat in CATEGORIES:
        bucket = agg.get(cat, {})
        sd = bucket.get("sd", 0.0)
        if sd <= 0 or bucket.get("n", 0) < 2:
            z_scores[cat] = None
            continue
        z = (target_dens.get(cat, 0.0) - bucket["mean"]) / sd
        z_scores[cat] = z

    # PDTB relation-bucket z-scores (parallel, same guard). These
    # measure distance from the OPERATOR'S OWN corpus (a self-
    # baseline), not a population norm and not a verdict threshold.
    relation_z: dict[str, float | None] = {}
    agg_rel = baseline_block.get("aggregate_relation_density_by_bucket", {})
    target_rel = target.get("relation_distribution", {}).get(
        "density_per_1k", {}
    )
    for rb in RELATION_BUCKETS:
        bucket = agg_rel.get(rb, {})
        sd = bucket.get("sd", 0.0)
        if sd <= 0 or bucket.get("n", 0) < 2:
            relation_z[rb] = None
            continue
        relation_z[rb] = (target_rel.get(rb, 0.0) - bucket["mean"]) / sd

    return {
        "available": True,
        "category_density_z_scores": z_scores,
        "relation_density_z_scores": relation_z,
    }


# --- Markdown rendering ----------------------------------------


def _claim_license(audit: dict[str, Any]) -> ClaimLicense:
    licenses = (
        "Discourse-marker typology and move-sequence pattern of "
        "the input: per-category marker densities (contrast, "
        "concession, consequence, elaboration, exemplification, "
        "sequencing, reframing, epistemic stance, boosting, "
        "hedging, self-correction, metadiscourse) and move-"
        "sequence bigram counts. Surfaces *what kind* of "
        "scaffolding the writer uses, not just *how much*."
    )
    does_not_license = (
        "An AI-provenance verdict. Heavy scaffolding is "
        "characteristic of legal/policy memos, academic prose, "
        "AI-edited drafts, and well-scaffolded human essayists "
        "alike. The differential diagnosis of cause is the "
        "confounder audit's job (which consumes this output as "
        "evidence). Nor does the audit license claims about "
        "which moves are 'good' or 'bad' — the typology is "
        "descriptive."
    )
    additional_caveats = [
        "Marker patterns are case-insensitive English regexes. "
        "Idiomatic markers (e.g. \"the better question is\") "
        "are pattern-matched literally; metaphorical or unusual "
        "wordings will be missed.",
        "First-match wins for sentence classification: a "
        "sentence with multiple markers gets typed by the "
        "earliest one. Move-sequence bigrams capture the "
        "between-sentence pattern regardless.",
        "Heuristic thresholds (band call) are calibration-"
        "pending; treat the band as a cue, not a verdict.",
    ]

    # PDTB explicit-connective relation layer (additive). Only
    # extend the license when the relation block is present (it
    # always is on an available audit, but guard defensively).
    if "relation_distribution" in audit:
        licenses += (
            " When explicit connectives are present, the writer's "
            "EXPLICIT PDTB top-level discourse-relation mix "
            "(Comparison / Contingency / Expansion / Temporal) as a "
            "descriptive distribution — per-1k densities, fractions, "
            "and relation entropy."
        )
        does_not_license += (
            " It also does not license any claim about IMPLICIT "
            "discourse relations. The relation layer is an explicit-"
            "connective PROXY: it maps only explicitly-signalled "
            "connectives to their PDTB majority top-level class. It "
            "does not extract argument spans, does not disambiguate "
            "polysemous connectives per-occurrence, and does not see "
            "the (larger) implicit-relation layer that the trained "
            "classifier of arXiv:2307.03378 targets. A relation "
            "distribution is characteristic of register and genre "
            "(legal/policy prose is Contingency-dense; narrative is "
            "Temporal-dense) and licenses no inference about "
            "authorship or argument quality."
        )
        additional_caveats.extend([
            "Explicit-connective proxy only; implicit (unsignalled) "
            "relations are invisible to this M1 layer (they are the "
            "subject of the gated model, arXiv:2307.03378).",
            "Polysemous connectives (while, since, as, …) are each "
            "assigned a single majority PDTB class; "
            "`ambiguous_connective_fraction` reports their share, so "
            "a high value flags low confidence in the relation mix.",
            "The relation distribution is uncalibrated "
            "(`calibration_status: \"uncalibrated\"`); baseline "
            "relation z-scores, when present, measure distance from "
            "the supplied corpus (a self-baseline), not a population "
            "norm and not a verdict threshold.",
        ])

    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=licenses,
        does_not_license=does_not_license,
        comparison_set={
            "n_words": audit.get("n_words"),
            "n_sentences": audit.get("n_sentences"),
            "band": audit.get("compression", {}).get("band"),
        },
        additional_caveats=additional_caveats,
    )
    # B.3: state-routed caveats when --ai-status was passed.
    return with_state_caveats(
        lic, target_ai_status=audit.get("ai_status"),
    )


def _claim_license_block(audit: dict[str, Any]) -> str:
    return _claim_license(audit).render_block().rstrip()


_RESULTS_KEYS = (
    "category_counts", "category_densities_per_1k",
    "total_marker_density_per_1k", "move_sequence",
    "move_sequence_bigrams", "move_sequence_entropy_bits",
    "marked_only_entropy_bits", "relation_distribution",
    "compression",
)


def build_audit_payload(
    audit: dict[str, Any],
    *,
    target_path: Path | str,
    baseline_block: dict[str, Any] | None,
    baseline_comparison: dict[str, Any] | None,
) -> dict[str, Any]:
    """Wrap the audit dict in the schema_version 1.0 envelope.

    Per ``internal/SPEC_output_schema_unification.md`` §1. Metadata
    (task_surface, tool, version, available) is peeled out; signals
    listed in ``_RESULTS_KEYS`` flow into ``results``; preprocessing
    rides under ``target``; baseline metadata under ``baseline``.
    """
    available = bool(audit.get("available", True))
    n_words = int(audit.get("n_words", 0) or 0)
    n_sentences = int(audit.get("n_sentences", 0) or 0)
    target_extra: dict[str, Any] = {"sentences": n_sentences}
    if "preprocessing" in audit:
        target_extra["preprocessing"] = audit["preprocessing"]

    results: dict[str, Any] = {}
    if available:
        for k in _RESULTS_KEYS:
            if k in audit:
                results[k] = audit[k]
        if baseline_comparison is not None:
            results["baseline_comparison"] = baseline_comparison

    baseline_meta: dict[str, Any] | None = None
    if baseline_block is not None:
        baseline_meta = build_baseline_metadata(
            n_files=int(baseline_block.get("n_files", 0) or 0),
            words=int(baseline_block.get("n_words", 0) or 0),
            extra={
                k: v for k, v in baseline_block.items()
                if k not in {"n_files", "n_words"}
            } or None,
        )

    warnings: list[str] = []
    if not available and "reason" in audit:
        warnings.append(audit["reason"])

    lic = _claim_license(audit) if available else None
    ai_status = audit.get("ai_status")

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=n_words,
        baseline=baseline_meta,
        results=results,
        claim_license=lic,
        available=available,
        warnings=warnings,
        ai_status=ai_status,
        target_extra=target_extra or None,
    )


def render_report(
    audit: dict[str, Any],
    baseline_comparison: dict[str, Any] | None = None,
) -> str:
    if not audit.get("available"):
        return (
            "# Discourse move signature\n\n"
            f"_Unavailable: {audit.get('reason', 'unknown')}._\n"
        )
    c = audit["compression"]
    lines: list[str] = [
        "# Discourse move signature",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Words:** {audit['n_words']:,}  "
        f"**Sentences:** {audit['n_sentences']}",
        "",
        f"**Band:** {c['band']}  "
        f"(compression fraction {c['compression_fraction']:.2f}; "
        f"{c['n_flagged']}/{c['n_signals']} signals fired)",
        "",
        f"**Total marker density:** "
        f"{audit['total_marker_density_per_1k']:.1f} per 1,000 words  "
        f"**Marked-only move entropy:** "
        f"{audit['marked_only_entropy_bits']:.2f} bits",
        "",
        "## Per-category densities",
        "",
        "| category | count | density / 1k words |",
        "|---|---:|---:|",
    ]
    densities = audit["category_densities_per_1k"]
    counts = audit.get("category_counts", {})
    for cat in CATEGORIES:
        lines.append(
            f"| {cat} | {counts.get(cat, 0)} | "
            f"{densities.get(cat, 0.0):.2f} |"
        )
    lines.append("")

    if c["flagged_signals"]:
        lines.append("## Flagged signals")
        lines.append("")
        for sig in c["flagged_signals"]:
            lines.append(f"- `{sig}`")
        lines.append("")

    rel = audit.get("relation_distribution")
    if rel is not None:
        lines.append("## Explicit discourse-relation profile")
        lines.append("")
        n_exp = rel.get("n_explicit_connectives", 0)
        if n_exp == 0:
            lines.append(
                "_No explicit PDTB connectives matched — the writer "
                "signals discourse relations implicitly (or this is a "
                "short / non-argumentative passage). Explicit-only "
                "proxy; implicit relations are out of scope._"
            )
            lines.append("")
        else:
            lines.append(
                f"**Explicit connectives:** {n_exp}  "
                f"**Relation entropy:** "
                f"{rel.get('relation_entropy_bits', 0.0):.2f} / "
                f"{rel.get('relation_entropy_max_bits', 0.0):.2f} bits  "
                f"**Ambiguous-connective share:** "
                f"{rel.get('ambiguous_connective_fraction', 0.0):.2f}  "
                f"(`calibration_status: "
                f"{rel.get('calibration_status', 'uncalibrated')}`)"
            )
            lines.append("")
            lines.append("| relation | count | density / 1k | fraction |")
            lines.append("|---|---:|---:|---:|")
            counts = rel.get("counts", {})
            dens = rel.get("density_per_1k", {})
            fracs = rel.get("fractions", {})
            for b in rel.get("buckets", list(RELATION_BUCKETS)):
                lines.append(
                    f"| {b} | {counts.get(b, 0)} | "
                    f"{dens.get(b, 0.0):.2f} | "
                    f"{fracs.get(b, 0.0):.3f} |"
                )
            lines.append("")

    bigrams = audit.get("move_sequence_bigrams", {})
    if bigrams:
        # Top 10 most-frequent bigrams.
        top = list(bigrams.items())[:10]
        lines.append("## Top move-sequence bigrams")
        lines.append("")
        lines.append("| bigram | count |")
        lines.append("|---|---:|")
        for bg, c_count in top:
            lines.append(f"| `{bg}` | {c_count} |")
        lines.append("")

    if baseline_comparison and baseline_comparison.get("available"):
        lines.append("## Baseline comparison")
        lines.append("")
        lines.append("| category | z-score |")
        lines.append("|---|---:|")
        zs = baseline_comparison["category_density_z_scores"]
        for cat in CATEGORIES:
            z = zs.get(cat)
            z_str = f"{z:+.2f}" if isinstance(z, (int, float)) else "n/a"
            lines.append(f"| {cat} | {z_str} |")
        lines.append("")

        rel_zs = baseline_comparison.get("relation_density_z_scores")
        if rel_zs:
            lines.append("### Relation-bucket z-scores")
            lines.append("")
            lines.append(
                "_Distance from the supplied corpus (self-baseline), "
                "not a population norm or a verdict threshold._"
            )
            lines.append("")
            lines.append("| relation | z-score |")
            lines.append("|---|---:|")
            for b in RELATION_BUCKETS:
                z = rel_zs.get(b)
                z_str = f"{z:+.2f}" if isinstance(z, (int, float)) else "n/a"
                lines.append(f"| {b} | {z_str} |")
            lines.append("")

    lines.append(_claim_license_block(audit))
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- CLI -------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="discourse_move_signature.py",
        description=(
            "Typed discourse marker + move-sequence audit. "
            "Provides differentiating evidence for the confounder "
            "audit's differential diagnosis (legal/policy memo "
            "style vs. AI smoothing vs. professional copyediting)."
        ),
    )
    p.add_argument("input", help="Path to .txt or .md target file.")
    p.add_argument("--baseline-dir", help="Optional baseline directory.")
    p.add_argument("--json", action="store_true", help="Emit JSON.")
    p.add_argument("--out", help="Write output to this path.")
    p.add_argument("--allow-non-prose", action="store_true")
    p.add_argument("--strip-rules", help="Comma-separated strip rules.")
    p.add_argument("--strip-aggressive", action="store_true")
    p.add_argument(
        "--strip-masking",
        help="Optional masking profile (prose_body_only, etc.).",
    )
    p.add_argument(
        "--include-baseline-filenames", action="store_true",
        help=(
            "Include raw baseline filenames in `per_file_summaries` "
            "(privacy default: anonymized as `baseline_001`)."
        ),
    )
    # B.3 (v1.47.0+): authorship-state routing for the ClaimLicense.
    p.add_argument(
        "--ai-status",
        default=None,
        help=(
            "Manifest ai_status value for the target text. When "
            "supplied, the ClaimLicense block gains state-specific "
            "caveats per SPEC_authorship_states.md §9.2."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    target_path = Path(args.input).expanduser()
    if not target_path.is_file():
        sys.stderr.write(f"Input not found: {target_path}\n")
        return 2
    raw = target_path.read_text(encoding="utf-8", errors="ignore")
    cleaned, prep_meta = strip_non_prose(
        raw, args.strip_rules,
        allow_non_prose=args.allow_non_prose,
        strip_aggressive=args.strip_aggressive,
        strip_masking=args.strip_masking,
    )
    audit = audit_discourse_moves(cleaned)
    audit["preprocessing"] = prep_meta
    # B.3: propagate --ai-status into the audit dict for the
    # claim-license block.
    if args.ai_status:
        audit["ai_status"] = args.ai_status

    baseline_comparison: dict[str, Any] | None = None
    if args.baseline_dir:
        try:
            block = audit_baseline_discourse(
                args.baseline_dir,
                allow_non_prose=args.allow_non_prose,
                strip_rules=args.strip_rules,
                strip_aggressive=args.strip_aggressive,
                strip_masking=args.strip_masking,
                target_path=target_path,
                include_filenames=args.include_baseline_filenames,
            )
        except FileNotFoundError as exc:
            sys.stderr.write(f"  baseline error: {exc}\n")
            return 2
        audit["baseline_block"] = block
        if block.get("n_files", 0) == 0:
            sys.stderr.write(
                f"  baseline at {args.baseline_dir} produced 0 "
                "usable files; baseline comparison skipped.\n"
            )
        baseline_comparison = compare_to_baseline(audit, block)
        audit["baseline_comparison"] = baseline_comparison

    if args.json:
        payload = build_audit_payload(
            audit,
            target_path=target_path,
            baseline_block=audit.get("baseline_block"),
            baseline_comparison=baseline_comparison,
        )
        out = json.dumps(payload, indent=2, default=str)
    else:
        out = render_report(audit, baseline_comparison)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
