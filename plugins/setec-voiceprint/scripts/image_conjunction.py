#!/usr/bin/env python3
"""image_conjunction.py — AIC-8 image-conjunction detector.

Identifies word pairs that conjoin abstract and concrete words at
elevated density relative to a register-typical baseline. The
canonical AI-prose pattern: "the machinery of grief", "constraints
humming", "lowercase love". Per
`internal/SPEC_aic_8_9_implementation.md` Step 6.

The compound diagnostic is critical:

  * **Concreteness gap alone** catches conventional idioms ("heavy
    burden": gap 0.74; "deep meaning": gap 1.53) which are not
    the AIC-8 pattern.
  * **Embedding similarity alone** catches semantically distant
    pairs whether or not they bridge concreteness levels.
  * **The intersection** isolates the deliberate-juxtaposition
    pattern AIC-8 flags: high concreteness gap AND low embedding
    similarity = abstract word paired with concrete word from a
    distant semantic neighborhood.

Default thresholds (configurable) per the spec's starting points:
``T1 = 2.5`` (concreteness gap) and ``T2 = 0.4`` (cosine
similarity). The roadmap calls for empirical calibration against
the four-corpus fixture suite (idiom negatives, AI-image-
conjunction positives, aphoristic essayist negatives, AI-rewrite
positives). Until calibrated, the detector ships
``status: "provisional"`` per the Stylometry-to-the-people policy.

CLI usage::

    # Default thresholds; stdout JSON
    python3 scripts/image_conjunction.py path/to/draft.md

    # Tune thresholds
    python3 scripts/image_conjunction.py path/to/draft.md \\
        --t1 2.0 --t2 0.5

    # Compare against an explicit baseline (PR #4 ships defaults)
    python3 scripts/image_conjunction.py path/to/draft.md \\
        --baseline 5 --baseline-source register_typical_essay

Dependency requirements:

  * spaCy with a parsing-capable model (``en_core_web_sm`` is
    sufficient for dependency parsing).
  * spaCy with a vectors-bearing model (``en_core_web_md`` or
    ``en_core_web_lg``) for the cosine-similarity check. The
    ``embeddings.py`` helper raises a typed error if neither is
    installed.
  * ``data/brysbaert_concreteness.csv`` (ships in-repo) for the
    concreteness lookup.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import concreteness  # type: ignore
import embeddings  # type: ignore
import paragraph_parser  # type: ignore


# Spec defaults. T1 = 2.5 concreteness gap, T2 = 0.4 cosine.
# Both flagged as starting values pending §5.4 calibration in
# `internal/SPEC_aic_8_9_implementation.md`.
DEFAULT_T1_CONCRETENESS_GAP = 2.5
DEFAULT_T2_EMBEDDING_SIMILARITY = 0.4

# Dependency relations that AIC-8 considers for candidate pairs.
# Per spec Step 6: modifier-head, subject-verb, predicate-
# complement, "X of Y" genitive.
_RELATION_AMOD = "amod"           # ADJ → NOUN: "lowercase love"
_RELATION_COMPOUND = "compound"   # NOUN → NOUN: "lowercase love" (spaCy)
_RELATION_NSUBJ_VERB = "nsubj_verb"  # subject → verb: "constraints hum"
_RELATION_ATTR = "attr"           # complement → copula: "love is desire"
_RELATION_PREP_OF = "prep_of"     # "X of Y" genitive: "machinery of grief"


@dataclass(frozen=True)
class ImageConjunction:
    """One detected abstract-concrete word pair.

    ``word_a`` and ``word_b`` are in syntactic order (head-first
    for amod/compound/prep_of; subject-first for nsubj_verb).
    ``abstract_word`` and ``concrete_word`` properties resolve the
    semantic role from the concreteness scores. ``relation`` names
    the syntactic frame that surfaced the pair.

    Position fields (`paragraph_index`, `sentence_position`,
    `is_paragraph_final_sentence`) are used by the density math
    to compute spacing variance and paragraph-end co-occurrence
    (the AIC-9 cross-tie).
    """

    word_a: str
    word_b: str
    concreteness_a: float
    concreteness_b: float
    concreteness_gap: float
    embedding_similarity: float
    relation: str
    paragraph_index: int
    sentence_position: int  # 0-indexed within paragraph
    is_paragraph_final_sentence: bool

    @property
    def abstract_word(self) -> str:
        """The lower-concreteness member of the pair."""
        return self.word_a if self.concreteness_a <= self.concreteness_b else self.word_b

    @property
    def concrete_word(self) -> str:
        """The higher-concreteness member of the pair."""
        return self.word_a if self.concreteness_a > self.concreteness_b else self.word_b


def extract_candidate_pairs(doc: Any) -> Iterator[tuple[str, str, str]]:
    """Yield ``(word_a, word_b, relation)`` from a spaCy doc.

    Walks the parsed document and emits candidate word pairs for
    every dependency relation AIC-8 considers. Lemmas (not surface
    forms) so "humming" matches "hum" in the concreteness lookup.

    Yields pairs even when the words won't pass the compound filter
    (the filter is applied downstream by `is_image_conjunction`).
    Filtering only at the lookup step keeps the dependency-walk
    code path single-pass and inspectable.
    """
    for tok in doc:
        # 1. ADJ → NOUN (amod): "lowercase love"
        if tok.dep_ == _RELATION_AMOD and tok.head.pos_ == "NOUN":
            yield (tok.lemma_.lower(), tok.head.lemma_.lower(), _RELATION_AMOD)
            continue
        # 2. NOUN → NOUN (compound): spaCy parses "lowercase love"
        #    as compound when "lowercase" is tagged NOUN. Catch
        #    both shapes.
        if tok.dep_ == _RELATION_COMPOUND and tok.head.pos_ == "NOUN":
            yield (tok.lemma_.lower(), tok.head.lemma_.lower(), _RELATION_COMPOUND)
            continue
        # 3. nsubj → verb: "constraints hum"
        if tok.dep_ == "nsubj" and tok.head.pos_ in ("VERB", "AUX"):
            # Skip copular verbs (handled via attr below).
            if tok.head.lemma_ in ("be", "become", "seem", "appear"):
                continue
            yield (
                tok.lemma_.lower(), tok.head.lemma_.lower(),
                _RELATION_NSUBJ_VERB,
            )
            continue
        # 4. "X of Y" genitive: prep token with lemma "of", head=
        #    NOUN, child=pobj of another NOUN.
        if (tok.dep_ == "prep" and tok.lemma_ == "of"
                and tok.head.pos_ == "NOUN"):
            for child in tok.children:
                if child.dep_ == "pobj" and child.pos_ == "NOUN":
                    yield (
                        tok.head.lemma_.lower(),
                        child.lemma_.lower(),
                        _RELATION_PREP_OF,
                    )
            continue
        # 5. Predicate complement: "love is desire" — attr token
        #    with head=copular verb, paired with the nsubj of the
        #    same verb.
        if tok.dep_ == "attr" and tok.head.pos_ in ("VERB", "AUX"):
            for sibling in tok.head.children:
                if sibling.dep_ == "nsubj":
                    yield (
                        sibling.lemma_.lower(),
                        tok.lemma_.lower(),
                        _RELATION_ATTR,
                    )
                    break
            continue


def evaluate_pair(
    word_a: str, word_b: str, relation: str,
    *,
    t1: float = DEFAULT_T1_CONCRETENESS_GAP,
    t2: float = DEFAULT_T2_EMBEDDING_SIMILARITY,
    concreteness_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Test a candidate pair against the AIC-8 compound filter.

    Returns a dict with the pair's metadata if it passes (concreteness
    gap >= ``t1`` AND embedding similarity <= ``t2``); returns
    ``None`` if it fails either filter or if either word is
    out-of-vocabulary in Brysbaert or the embedding model.

    The returned dict is the JSON-ready substructure for a single
    image conjunction (without position fields; those are added by
    the caller).
    """
    conc_a = concreteness.get_concreteness(word_a, concreteness_path)
    conc_b = concreteness.get_concreteness(word_b, concreteness_path)
    if conc_a is None or conc_b is None:
        return None
    gap = abs(conc_a - conc_b)
    if gap < t1:
        return None
    sim = embeddings.cosine_similarity(word_a, word_b)
    if sim is None:
        return None
    if sim > t2:
        return None
    return {
        "word_a": word_a,
        "word_b": word_b,
        "concreteness_a": conc_a,
        "concreteness_b": conc_b,
        "concreteness_gap": gap,
        "embedding_similarity": sim,
        "relation": relation,
    }


def _spacing_variance(positions: list[int]) -> float:
    """SD of inter-position distances; 0 when fewer than 3 positions.

    ``positions`` is a sorted list (paragraph or token index).
    Two positions produce only one distance; SD is undefined. The
    function returns 0.0 in that case rather than raising.
    """
    if len(positions) < 3:
        return 0.0
    distances = [
        positions[i + 1] - positions[i]
        for i in range(len(positions) - 1)
    ]
    return float(statistics.stdev(distances))


def image_conjunction_density(
    text: str,
    *,
    nlp: Any,
    t1: float = DEFAULT_T1_CONCRETENESS_GAP,
    t2: float = DEFAULT_T2_EMBEDDING_SIMILARITY,
    concreteness_path: Optional[Path] = None,
    baseline_value: Optional[float] = None,
    baseline_source: Optional[str] = None,
) -> dict[str, Any]:
    """Compute AIC-8 image-conjunction density + JSON-ready block.

    Returns a dict matching the spec's §6 output schema. Density
    is expressed per 1000 tokens. The ``conjunctions`` list carries
    full per-pair metadata (including paragraph position) for
    downstream consumers (the prestige-metaphor detector composes
    on this).

    ``baseline_value`` and ``baseline_source`` are optional; when
    provided, a ``baseline_comparison`` block is included with the
    elevation factor.
    """
    # Split into paragraphs; parse each separately so we can track
    # paragraph_index alongside the dependency walk.
    paragraphs = paragraph_parser.split_paragraphs(text)
    total_paragraphs = len(paragraphs)
    total_tokens = 0
    detected: list[dict[str, Any]] = []
    conjunction_paragraph_indices: list[int] = []
    paragraph_final_co_occurrence_count = 0

    for p_idx, para in enumerate(paragraphs):
        sentences = paragraph_parser.split_sentences(para)
        n_sentences = len(sentences)
        for s_idx, sentence in enumerate(sentences):
            is_final = (s_idx == n_sentences - 1)
            doc = nlp(sentence)
            total_tokens += sum(1 for t in doc if not t.is_punct)
            for word_a, word_b, relation in extract_candidate_pairs(doc):
                pair_info = evaluate_pair(
                    word_a, word_b, relation,
                    t1=t1, t2=t2,
                    concreteness_path=concreteness_path,
                )
                if pair_info is None:
                    continue
                pair_info.update({
                    "paragraph_index": p_idx,
                    "sentence_position": s_idx,
                    "is_paragraph_final_sentence": is_final,
                })
                detected.append(pair_info)
                conjunction_paragraph_indices.append(p_idx)
                if is_final:
                    paragraph_final_co_occurrence_count += 1

    density_per_1k = (
        (len(detected) / total_tokens) * 1000 if total_tokens > 0 else 0.0
    )
    spacing = _spacing_variance(
        sorted(conjunction_paragraph_indices)
    )
    paragraph_final_rate = (
        paragraph_final_co_occurrence_count / len(detected)
        if detected else 0.0
    )

    block: dict[str, Any] = {
        "signal_path": "aic_8_9.image_conjunction_density",
        "family": "aic-8-aesthetic-authority-laundering",
        "value": density_per_1k,
        "spacing_variance": spacing,
        "paragraph_final_co_occurrence_rate": paragraph_final_rate,
        "polarity": "↑",
        "status": "provisional",
        "task_surface": "smoothing_diagnosis",
        "claim_license": "voice_diagnostic",
        "conjunctions": detected,
        "diagnostics": {
            "total_tokens": total_tokens,
            "total_paragraphs": total_paragraphs,
            "conjunction_count": len(detected),
            "threshold_t1_concreteness_gap": t1,
            "threshold_t2_embedding_similarity": t2,
            "concreteness_vocab": concreteness.vocab_size(concreteness_path),
            "embedding_backend": embeddings.model_identifier(),
        },
    }

    if baseline_value is not None:
        block["baseline_comparison"] = {
            "baseline_source": baseline_source or "operator-supplied",
            "baseline_value": baseline_value,
            "elevation_factor": (
                density_per_1k / baseline_value
                if baseline_value > 0 else None
            ),
        }
    return block


# ---------- CLI ------------------------------------------------------


def _load_spacy_with_parsing() -> Any:
    """Load a spaCy pipeline with parsing. Prefers `_md` (has vectors
    too), falls back to `_sm` (vectors are looked up via `embeddings`).

    Returns the pipeline. Raises ``EmbeddingsBackendError`` (the
    same typed exception the embedding-vectors helper raises) when
    no model is installed, so CLI callers can wrap one try/except
    around both load steps and exit cleanly with an actionable
    install message instead of dumping a traceback.
    """
    try:
        import spacy  # type: ignore
    except ImportError as exc:
        raise embeddings.EmbeddingsBackendError(
            "spaCy is not installed. Install with: "
            "pip install -r plugins/setec-voiceprint/requirements.txt"
        ) from exc
    for name in ("en_core_web_md", "en_core_web_lg", "en_core_web_sm"):
        try:
            return spacy.load(name)
        except OSError:
            continue
    raise embeddings.EmbeddingsBackendError(
        "No spaCy model installed. AIC-8 requires "
        "`en_core_web_sm` for parsing AND a vectors-bearing model "
        "(`en_core_web_md` or `_lg`) for the embedding similarity "
        "check. Install via: python -m spacy download en_core_web_md"
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "AIC-8 image-conjunction detector. Flags abstract-"
            "concrete word pairs at elevated density relative to a "
            "register baseline. Per "
            "`internal/SPEC_aic_8_9_implementation.md` Step 6."
        ),
    )
    parser.add_argument(
        "input", type=Path,
        help="Path to a text or Markdown file to audit.",
    )
    parser.add_argument(
        "--t1", type=float, default=DEFAULT_T1_CONCRETENESS_GAP,
        help=(
            "Concreteness gap threshold (default: 2.5). Pairs with "
            "gap below this fail the first filter. Spec starting "
            "value; calibrate locally against fixtures."
        ),
    )
    parser.add_argument(
        "--t2", type=float, default=DEFAULT_T2_EMBEDDING_SIMILARITY,
        help=(
            "Embedding cosine similarity threshold (default: 0.4). "
            "Pairs with similarity above this fail the second "
            "filter (treated as conventional collocations / idioms). "
            "Spec starting value; calibrate locally."
        ),
    )
    parser.add_argument(
        "--baseline", type=float, default=None, metavar="VALUE",
        help=(
            "Compare detected density (per 1000 tokens) against "
            "this baseline (optional). Spec starting points: "
            "5/1000 contemporary essay; 7/1000 literary fiction."
        ),
    )
    parser.add_argument(
        "--baseline-source", type=str, default="operator-supplied",
        metavar="LABEL",
        help=(
            "Human-readable label for the baseline source. "
            "Default: 'operator-supplied'."
        ),
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Write JSON to this path (default: stdout).",
    )
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 1

    text = args.input.read_text(encoding="utf-8")

    # Wrap both the model load AND the audit in a single try/except.
    # `_load_spacy_with_parsing` raises `EmbeddingsBackendError`
    # (typed) when no spaCy model is installed, matching the typed
    # error the audit's embedding-similarity check raises. Both
    # failure modes route through the same actionable-message exit.
    try:
        nlp = _load_spacy_with_parsing()
        block = image_conjunction_density(
            text,
            nlp=nlp, t1=args.t1, t2=args.t2,
            baseline_value=args.baseline,
            baseline_source=(
                args.baseline_source if args.baseline else None
            ),
        )
    except embeddings.EmbeddingsBackendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    output = json.dumps(block, indent=2)
    if args.out is None:
        print(output)
    else:
        args.out.write_text(output + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
