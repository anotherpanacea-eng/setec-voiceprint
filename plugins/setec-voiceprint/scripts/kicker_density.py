#!/usr/bin/env python3
"""kicker_density.py — AIC-9 (Closure Inflation) detector.

Identifies sentences with **kicker shape** — short, declarative,
generalizable, sentence-final-period — and computes the proportion
of paragraphs that end with one. AI-smoothed essayistic prose
elevates this rate because the default assistant register has
learned that "good paragraphs end with quotable summaries." Human
writers ration kickers; aphoristic essayists (Borges, Bacon, La
Rochefoucauld) deploy them as genre.

Per `internal/SPEC_aic_8_9_implementation.md` Steps 4-5. JSON
output schema matches the spec's §5 block so the registry update
in PR #4 can register `aic_8_9.kicker_density` cleanly.

The kicker-shape classifier is heuristic (regex + optional spaCy
POS filter). Per the spec: "regex first, ML later if needed." The
classifier reports a confidence score alongside the boolean
verdict for callers who want to tune their own thresholds.

CLI usage::

    # Default: stdout JSON
    python3 scripts/kicker_density.py path/to/draft.md

    # Override word limit (default 15):
    python3 scripts/kicker_density.py path/to/draft.md --word-limit 12

    # Compare against an explicit baseline (PR #4 ships register-
    # typical defaults via baselines/register_typical.yaml):
    python3 scripts/kicker_density.py path/to/draft.md --baseline 0.10

    # Write JSON to file:
    python3 scripts/kicker_density.py path/to/draft.md --out audit.json

Limitations:

  * Sentence boundary detection is regex-based (via
    ``paragraph_parser.split_sentences``). Operators with abbreviation-
    heavy prose may want to pre-tokenize via spaCy and pass via
    ``classify_with_pretokenized`` instead.
  * Proper-noun detection prefers spaCy POS tagging; falls back to
    a mid-sentence-capitalization heuristic when spaCy is
    unavailable. Both have known failure modes (spaCy mistags rare
    proper nouns; the regex flags title-cased common nouns).
  * The shipped band (suggested operator alarm at kicker_density >
    0.30 against a register-typical 0.05-0.10 baseline) is
    provisional. The §5.4 calibration corpus that anchors the
    threshold ships as roadmap work; the detector emits the raw
    density so operators can apply their own thresholds.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

# Make sibling imports work whether invoked as a script or imported.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import paragraph_parser  # type: ignore


# Defaults per spec §4 (configurable via CLI / function args).
DEFAULT_WORD_LIMIT = 15

# Sentence-final punctuation that disqualifies a sentence from
# being kicker-shaped. Periods only; questions, exclamations, and
# ellipses are explicit rhetorical moves, not the "performing
# landing" pattern the AIC-9 family flags.
_DISQUALIFYING_FINALS = frozenset({"?", "!", "…", '"', "'"})

# Digit detector: any decimal digit, including those embedded in
# strings like "1980s" or "section 4.3."
_DIGIT_RE = re.compile(r"\d")

# Common capitalized non-proper-noun tokens to allow through the
# regex proper-noun heuristic. The first word of a sentence is
# always capitalized; we skip position 0 entirely. Standalone "I"
# (and contractions) are allowed because they're first-person
# pronouns, not proper nouns.
_CAP_ALLOWLIST = frozenset({"I", "I'm", "I've", "I'll", "I'd"})

# Capitalized-word detector: a token starting with a Unicode upper
# letter, including hyphenated compounds and apostrophe-containing
# tokens.
_CAPITALIZED_TOKEN_RE = re.compile(r"[A-Z][A-Za-z'’\-]*")

# Word tokenizer for kicker length-counting. Excludes punctuation
# from the count.
_WORD_RE = re.compile(r"\b[\w']+\b")


@dataclass(frozen=True)
class KickerClassification:
    """Result of running the kicker-shape classifier on one sentence.

    `is_kicker`: the boolean verdict.
    `confidence`: a [0, 1] score combining how many of the
        sentence-shape conditions held. Operators tuning their own
        thresholds can use this; the boolean uses the spec's
        default cutoff (all conditions must hold).
    `reasons`: list of human-readable explanations for the verdict.
        For positive verdicts, lists the passing conditions; for
        negative verdicts, lists the failing ones. Useful for
        per-sentence revision packets.
    """

    is_kicker: bool
    confidence: float
    reasons: list[str] = field(default_factory=list)


def _word_count(sentence: str) -> int:
    """Count word-class tokens in a sentence (punctuation excluded)."""
    return len(_WORD_RE.findall(sentence))


def _has_disqualifying_final(sentence: str) -> Optional[str]:
    """Return the disqualifying final character if any, else None.

    A kicker ends with `.` (sentence-final period). Questions,
    exclamations, ellipses, and quote-final sentences are
    disqualified because they signal explicit rhetorical moves
    rather than the AIC-9 landing pattern.
    """
    stripped = sentence.rstrip()
    if not stripped:
        return ""  # empty signals disqualification
    last = stripped[-1]
    if last == ".":
        return None
    return last


def _has_digit(sentence: str) -> bool:
    return bool(_DIGIT_RE.search(sentence))


def _has_proper_noun_via_regex(sentence: str) -> bool:
    """Heuristic: capitalized mid-sentence tokens not in the allowlist.

    The first word of every sentence is capitalized; we skip it.
    Tokens like ``I``, ``I'm``, ``I've`` are kept in
    `_CAP_ALLOWLIST`. Anything else mid-sentence that starts with a
    capital is treated as a proper noun.

    Known false-positive: title-case sentences (e.g., section
    headers used as the closing sentence) flag every word as a
    proper noun. The spaCy POS check, when available, is sharper.
    """
    # Get all word tokens with positions; check tokens past position 0.
    tokens = _WORD_RE.findall(sentence)
    if len(tokens) <= 1:
        return False
    for tok in tokens[1:]:
        if tok in _CAP_ALLOWLIST:
            continue
        # Token starts with capital and isn't fully uppercase
        # (acronym handling: ALLCAPS one-word references like
        # "USA" are still flagged — that's correct for a kicker).
        if _CAPITALIZED_TOKEN_RE.match(tok):
            return True
    return False


def _has_proper_noun_via_spacy(sentence: str, nlp: Any) -> bool:
    """spaCy-backed proper-noun check.

    Tags the sentence and returns True if any token is tagged
    PROPN, OR if any named entity is detected (NER catches some
    proper nouns the small model's POS tagger misses; the union
    is strictly more permissive than either check alone).

    Requires a spaCy pipeline. Any model with POS + NER works
    (`en_core_web_sm` is enough; `_md` / `_lg` give better
    accuracy on rare proper nouns).
    """
    doc = nlp(sentence)
    if any(tok.pos_ == "PROPN" for tok in doc):
        return True
    return any(ent.label_ in {"PERSON", "ORG", "GPE", "LOC", "FAC",
                              "WORK_OF_ART", "EVENT", "NORP", "PRODUCT"}
               for ent in doc.ents)


def is_kicker_shape(
    sentence: str,
    *,
    word_limit: int = DEFAULT_WORD_LIMIT,
    nlp: Optional[Any] = None,
) -> KickerClassification:
    """Classify a single sentence as kicker-shaped or not.

    Conditions (all must hold for ``is_kicker=True``):

      1. Word count <= ``word_limit`` (default 15).
      2. Ends with sentence-final period (not ?, !, …, or quote).
      3. No digits anywhere in the sentence.
      4. No proper nouns mid-sentence. spaCy-based PROPN check if
         ``nlp`` is provided; otherwise a regex capitalization
         heuristic.

    ``confidence`` is the proportion of conditions that hold,
    independent of the boolean. ``reasons`` lists the passing or
    failing conditions for human-readable revision feedback.
    """
    if not sentence or not sentence.strip():
        return KickerClassification(
            is_kicker=False,
            confidence=0.0,
            reasons=["empty sentence"],
        )

    passes: list[str] = []
    fails: list[str] = []

    # Condition 1: word count.
    wc = _word_count(sentence)
    if wc <= word_limit and wc > 0:
        passes.append(f"word_count={wc} <= {word_limit}")
    else:
        fails.append(f"word_count={wc} > {word_limit}")

    # Condition 2: sentence-final period.
    disqual = _has_disqualifying_final(sentence)
    if disqual is None:
        passes.append("sentence_final_period")
    else:
        fails.append(f"non_period_final={disqual!r}")

    # Condition 3: no digits.
    if _has_digit(sentence):
        fails.append("contains_digit")
    else:
        passes.append("no_digits")

    # Condition 4: no proper nouns.
    if nlp is not None:
        has_propn = _has_proper_noun_via_spacy(sentence, nlp)
        if has_propn:
            fails.append("contains_propn (spacy)")
        else:
            passes.append("no_propn (spacy)")
    else:
        has_propn = _has_proper_noun_via_regex(sentence)
        if has_propn:
            fails.append("contains_capitalized_mid_sentence (regex)")
        else:
            passes.append("no_propn (regex)")

    n_conditions = 4
    confidence = len(passes) / n_conditions
    is_kicker = (len(fails) == 0)
    reasons = passes if is_kicker else fails
    return KickerClassification(
        is_kicker=is_kicker,
        confidence=confidence,
        reasons=reasons,
    )


def _spacing_variance(kicker_positions: list[int]) -> float:
    """Standard deviation of inter-kicker paragraph distances.

    ``kicker_positions`` is the sorted list of paragraph indices
    that contain kickers. Returns 0.0 when fewer than 2 kickers
    (no inter-kicker distance to measure); otherwise the sample
    SD of the consecutive-distance series.

    Diagnostic intent: high variance means kickers cluster (a few
    aphoristic passages punctuating prose that mostly doesn't
    perform landing). Low variance means kickers are evenly
    distributed (every paragraph performs landing). Per spec
    §AIC-9: distributed kickers are more diagnostic than clustered
    ones.
    """
    if len(kicker_positions) < 2:
        return 0.0
    distances = [
        kicker_positions[i + 1] - kicker_positions[i]
        for i in range(len(kicker_positions) - 1)
    ]
    if len(distances) < 2:
        return 0.0
    return float(statistics.stdev(distances))


def kicker_density(
    text: str,
    *,
    word_limit: int = DEFAULT_WORD_LIMIT,
    nlp: Optional[Any] = None,
    baseline_value: Optional[float] = None,
    baseline_source: Optional[str] = None,
) -> dict[str, Any]:
    """Compute AIC-9 kicker density and the surrounding JSON block.

    Returns a dict matching the spec's §5 output schema:

    .. code-block:: json

        {
          "signal_path": "aic_8_9.kicker_density",
          "family": "aic-9-closure-inflation",
          "value": 0.47,
          "spacing_variance": 0.32,
          "polarity": "↑",
          "status": "provisional",
          "baseline_comparison": {
            "baseline_source": "register_typical_essay",
            "baseline_value": 0.10,
            "elevation_factor": 4.7
          },
          "task_surface": "smoothing_diagnosis",
          "claim_license": "voice_diagnostic"
        }

    ``baseline_value`` and ``baseline_source`` are optional; when
    absent, the returned dict omits the ``baseline_comparison`` key
    (the operator can supply baselines after the fact). PR #4
    wires `baselines/register_typical.yaml` as the default source.

    The detection also returns per-paragraph diagnostics under the
    ``paragraphs`` key — useful for revision packets that need to
    show *which* paragraph endings triggered the flag.
    """
    positions = paragraph_parser.parse_document(text)
    total_paragraphs = max({s.paragraph_index for s in positions}, default=-1) + 1
    if total_paragraphs == 0:
        block: dict[str, Any] = {
            "signal_path": "aic_8_9.kicker_density",
            "family": "aic-9-closure-inflation",
            "value": 0.0,
            "spacing_variance": 0.0,
            "polarity": "↑",
            "status": "provisional",
            "task_surface": "smoothing_diagnosis",
            "claim_license": "voice_diagnostic",
            "paragraphs": [],
            "diagnostics": {
                "total_paragraphs": 0,
                "kicker_count": 0,
                "word_limit": word_limit,
                "proper_noun_detection": "spacy" if nlp is not None else "regex",
            },
        }
        return block

    paragraph_results: list[dict[str, Any]] = []
    kicker_paragraph_indices: list[int] = []
    finals = [s for s in positions if s.is_paragraph_final]
    for s in finals:
        cls = is_kicker_shape(s.text, word_limit=word_limit, nlp=nlp)
        paragraph_results.append({
            "paragraph_index": s.paragraph_index,
            "final_sentence": s.text,
            "is_kicker": cls.is_kicker,
            "confidence": cls.confidence,
            "reasons": list(cls.reasons),
        })
        if cls.is_kicker:
            kicker_paragraph_indices.append(s.paragraph_index)

    kicker_count = len(kicker_paragraph_indices)
    density = kicker_count / total_paragraphs
    spacing = _spacing_variance(kicker_paragraph_indices)

    block = {
        "signal_path": "aic_8_9.kicker_density",
        "family": "aic-9-closure-inflation",
        "value": density,
        "spacing_variance": spacing,
        "polarity": "↑",
        "status": "provisional",
        "task_surface": "smoothing_diagnosis",
        "claim_license": "voice_diagnostic",
        "paragraphs": paragraph_results,
        "diagnostics": {
            "total_paragraphs": total_paragraphs,
            "kicker_count": kicker_count,
            "word_limit": word_limit,
            "proper_noun_detection": "spacy" if nlp is not None else "regex",
        },
    }

    if baseline_value is not None:
        block["baseline_comparison"] = {
            "baseline_source": baseline_source or "operator-supplied",
            "baseline_value": baseline_value,
            "elevation_factor": (
                density / baseline_value if baseline_value > 0 else None
            ),
        }
    return block


# ---------- CLI ------------------------------------------------------


def _load_spacy_or_none(force_regex: bool = False) -> Optional[Any]:
    """Load `en_core_web_sm` for the PROPN check; return None if absent.

    Kicker-density does not require word vectors (`_md`/`_lg`); the
    `_sm` model is sufficient. Operators who already use the
    framework's Tier 2 audits have `_sm` installed; operators who
    don't get the regex fallback automatically.

    ``force_regex=True`` skips the load entirely; useful for tests
    that want to lock in the regex code path.
    """
    if force_regex:
        return None
    try:
        import spacy  # type: ignore
        return spacy.load("en_core_web_sm")
    except (ImportError, OSError):
        return None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "AIC-9 (Closure Inflation) kicker-density detector. "
            "Computes the proportion of paragraphs whose final "
            "sentence is kicker-shaped (short, declarative, "
            "generalizable, sentence-final period). "
            "Per `internal/SPEC_aic_8_9_implementation.md`."
        ),
    )
    parser.add_argument(
        "input", type=Path,
        help="Path to a text or Markdown file to audit.",
    )
    parser.add_argument(
        "--word-limit", type=int, default=DEFAULT_WORD_LIMIT,
        help=(
            "Maximum word count for kicker-shape sentences "
            "(default: 15). Configurable per the spec §4."
        ),
    )
    parser.add_argument(
        "--baseline", type=float, default=None, metavar="VALUE",
        help=(
            "Compare detected density against this baseline "
            "(optional). PR #4 ships register-typical defaults via "
            "baselines/register_typical.yaml; for now, pass an "
            "explicit value. Elevation factor = density / baseline "
            "appears in the JSON when provided."
        ),
    )
    parser.add_argument(
        "--baseline-source", type=str, default="operator-supplied",
        metavar="LABEL",
        help=(
            "Human-readable label for the baseline source (e.g., "
            "'register_typical_essay', 'personal_pre_ai'). Appears "
            "in the JSON output. Default: 'operator-supplied'."
        ),
    )
    parser.add_argument(
        "--force-regex", action="store_true",
        help=(
            "Skip spaCy load; force the regex proper-noun heuristic. "
            "Useful for reproducible test runs."
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
    nlp = _load_spacy_or_none(force_regex=args.force_regex)

    block = kicker_density(
        text,
        word_limit=args.word_limit,
        nlp=nlp,
        baseline_value=args.baseline,
        baseline_source=args.baseline_source if args.baseline else None,
    )

    output = json.dumps(block, indent=2)
    if args.out is None:
        print(output)
    else:
        args.out.write_text(output + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
