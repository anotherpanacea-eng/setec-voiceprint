#!/usr/bin/env python3
"""paragraph_parser.py — paragraph-aware document segmentation.

Splits prose into paragraphs and tracks per-sentence position within
its paragraph. Foundation infrastructure for AIC-9 kicker-density
detection (which scores paragraph-final sentences) and any future
audit that needs paragraph boundaries.

Other framework scripts (variance_audit, repetition_audit, etc.)
currently treat documents as flat token streams and ignore
paragraph structure. This module fills the gap; the AIC-9 detector
imports it directly, and audits that want to add paragraph-aware
features can extend the structures here.

Design:

  * **Paragraph boundary = blank line**. The conventional Markdown
    / prose definition. A run of `\\n\\n` (or more) separates
    paragraphs. Single newlines inside a paragraph are kept as
    soft-wraps. This is the simplest definition that works for
    operator-prepared manuscripts; documents with paragraph
    boundaries marked differently (XML, single-newline-separated)
    can convert first.
  * **Sentence tokenization** uses a regex sentence-splitter
    (period/question/exclamation + lookahead), not spaCy. Reasons:
    (a) the AIC-9 kicker-shape classifier has its own light NLP
    that doesn't need full parsing; (b) this module is foundation,
    not parse-time; (c) downstream callers that want spaCy-parsed
    sentences can pass them in via the `sentences` argument
    directly. The default tokenizer is deliberately cheap.
  * **Position-in-paragraph** is exposed three ways per sentence:
    `paragraph_index` (0-indexed), `position_in_paragraph` (0-indexed
    among sentences in that paragraph), and `is_paragraph_final`
    (boolean). AIC-9 reads `is_paragraph_final` to identify kicker
    candidates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# Blank-line separator. Three or more newlines collapse to one
# paragraph boundary (operator-prepared documents sometimes have
# extra spacing).
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")

# Sentence-end splitter: a sentence-final period / question /
# exclamation, followed by whitespace, followed by an uppercase
# letter or a quotation mark. Conservative; doesn't try to handle
# every edge case (abbreviations, embedded quotes, ellipsis). The
# AIC-9 kicker classifier handles edge cases at the sentence level.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'A-Z])")


@dataclass(frozen=True)
class SentencePosition:
    """A single sentence with its position context.

    `text`: the sentence text, stripped.
    `paragraph_index`: which paragraph it belongs to (0-indexed).
    `position_in_paragraph`: sentence index within the paragraph.
    `paragraph_size`: total sentences in this paragraph.
    `is_paragraph_initial`: is this the first sentence of its
        paragraph?
    `is_paragraph_final`: is this the last sentence of its
        paragraph? (The signal AIC-9 reads.)
    """

    text: str
    paragraph_index: int
    position_in_paragraph: int
    paragraph_size: int
    is_paragraph_initial: bool
    is_paragraph_final: bool


def split_paragraphs(text: str) -> list[str]:
    """Split ``text`` into paragraph strings.

    Paragraphs are runs of text separated by blank lines. Trailing
    and leading whitespace is stripped from each paragraph.
    Returns an empty list for empty / whitespace-only input.
    """
    if not text or not text.strip():
        return []
    paragraphs = _PARAGRAPH_SPLIT.split(text.strip())
    return [p.strip() for p in paragraphs if p.strip()]


def split_sentences(paragraph: str) -> list[str]:
    """Split a single paragraph into sentence strings.

    Conservative regex-based splitter. Operators who need precise
    sentence boundaries (e.g., for handling abbreviations or
    quoted sentences) should pre-parse and pass sentence strings
    to ``annotate_sentences`` directly.
    """
    if not paragraph or not paragraph.strip():
        return []
    sentences = _SENTENCE_END.split(paragraph.strip())
    return [s.strip() for s in sentences if s.strip()]


def parse_document(
    text: str,
    *,
    sentences_per_paragraph: Optional[list[list[str]]] = None,
) -> list[SentencePosition]:
    """Return a flat list of ``SentencePosition`` covering all sentences.

    Default behavior: split ``text`` into paragraphs (blank-line),
    then each paragraph into sentences (regex), and return position-
    annotated sentence records.

    To use externally-tokenized sentences (e.g., from spaCy), pass
    `sentences_per_paragraph` as a list of lists: outer index is the
    paragraph; inner list is the paragraph's sentences in order.
    The function then skips its built-in tokenization and uses the
    provided structure. Useful for AIC-9 callers who already have
    a spaCy pipeline running.
    """
    if sentences_per_paragraph is not None:
        para_sentences = sentences_per_paragraph
    else:
        paragraphs = split_paragraphs(text)
        para_sentences = [split_sentences(p) for p in paragraphs]

    out: list[SentencePosition] = []
    for p_idx, sents in enumerate(para_sentences):
        n = len(sents)
        if n == 0:
            continue
        for s_idx, s in enumerate(sents):
            out.append(SentencePosition(
                text=s,
                paragraph_index=p_idx,
                position_in_paragraph=s_idx,
                paragraph_size=n,
                is_paragraph_initial=(s_idx == 0),
                is_paragraph_final=(s_idx == n - 1),
            ))
    return out


def paragraph_final_sentences(text: str) -> list[SentencePosition]:
    """Return only the sentences that end a paragraph.

    Convenience for the AIC-9 kicker-density detector, which scores
    these sentences against the kicker-shape classifier.
    """
    return [s for s in parse_document(text) if s.is_paragraph_final]


def paragraph_count(text: str) -> int:
    """Return the number of paragraphs in ``text``.

    Convenience for density normalization
    (`kicker_density = kicker_count / paragraph_count`).
    """
    return len(split_paragraphs(text))


def paragraph_stats(text: str) -> dict[str, float]:
    """Return summary statistics for the document's paragraph structure.

    Exposes:

      * `paragraph_count`: total paragraphs.
      * `sentence_count`: total sentences across all paragraphs.
      * `mean_sentences_per_paragraph`: average paragraph length.
      * `min_sentences_per_paragraph`, `max_sentences_per_paragraph`:
        range diagnostics.
      * `single_sentence_paragraph_count`: number of paragraphs
        that contain exactly one sentence. AIC-9 weighting may
        treat these differently from multi-sentence paragraphs.
    """
    paragraphs = split_paragraphs(text)
    if not paragraphs:
        return {
            "paragraph_count": 0,
            "sentence_count": 0,
            "mean_sentences_per_paragraph": 0.0,
            "min_sentences_per_paragraph": 0,
            "max_sentences_per_paragraph": 0,
            "single_sentence_paragraph_count": 0,
        }
    sentences_per_para = [len(split_sentences(p)) for p in paragraphs]
    total = sum(sentences_per_para)
    return {
        "paragraph_count": len(paragraphs),
        "sentence_count": total,
        "mean_sentences_per_paragraph": total / len(paragraphs),
        "min_sentences_per_paragraph": min(sentences_per_para),
        "max_sentences_per_paragraph": max(sentences_per_para),
        "single_sentence_paragraph_count": sum(
            1 for n in sentences_per_para if n == 1
        ),
    }
