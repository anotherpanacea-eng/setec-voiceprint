#!/usr/bin/env python3
"""Regression tests for paragraph_parser.py.

Pins paragraph-boundary detection, sentence splitting, position-
within-paragraph annotation, and the convenience helpers used by
AIC-9 kicker-density detection.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import paragraph_parser as p  # type: ignore


# Canonical fixture: 4 paragraphs of varying size used across
# several tests. Paragraph 2 has a single sentence (boundary case);
# paragraph 3 has two; paragraphs 0 and 1 have three each.
_FIXTURE = """First paragraph opens. It has three sentences. The third one closes it.

Second paragraph. One more sentence. And another.

Third paragraph is single-sentence.

Fourth paragraph. Has two sentences."""


# --------------- Paragraph splitting ---------------------------


def test_split_paragraphs_basic():
    """Blank-line-separated paragraphs split cleanly."""
    paragraphs = p.split_paragraphs(_FIXTURE)
    assert len(paragraphs) == 4
    assert paragraphs[0].startswith("First paragraph")
    assert paragraphs[2] == "Third paragraph is single-sentence."


def test_split_paragraphs_empty_input():
    assert p.split_paragraphs("") == []


def test_split_paragraphs_whitespace_only():
    assert p.split_paragraphs("   \n\t  \n\n   ") == []


def test_split_paragraphs_handles_multiple_blank_lines():
    """3+ consecutive newlines collapse to a single paragraph boundary."""
    text = "First.\n\n\n\nSecond.\n\n\nThird."
    paragraphs = p.split_paragraphs(text)
    assert len(paragraphs) == 3


def test_split_paragraphs_strips_whitespace():
    """Leading/trailing whitespace stripped from each paragraph."""
    text = "  First paragraph.  \n\n  Second.  "
    paragraphs = p.split_paragraphs(text)
    assert paragraphs == ["First paragraph.", "Second."]


# --------------- Sentence splitting ----------------------------


def test_split_sentences_basic():
    para = "First sentence. Second sentence! Third sentence?"
    sentences = p.split_sentences(para)
    assert len(sentences) == 3


def test_split_sentences_handles_quoted_followups():
    """A period followed by an opening quote starts a new sentence."""
    para = 'She said it was over. "Believe me," he replied.'
    sentences = p.split_sentences(para)
    assert len(sentences) == 2


def test_split_sentences_empty_input():
    assert p.split_sentences("") == []
    assert p.split_sentences("   ") == []


def test_split_sentences_single_sentence():
    assert p.split_sentences("Just one sentence.") == ["Just one sentence."]


# --------------- parse_document position annotation ------------


def test_parse_document_basic_fixture():
    """Each sentence gets the correct paragraph/position annotation."""
    positions = p.parse_document(_FIXTURE)
    assert len(positions) == 9
    # Paragraph 0 has 3 sentences
    assert positions[0].paragraph_index == 0
    assert positions[0].position_in_paragraph == 0
    assert positions[0].paragraph_size == 3
    assert positions[0].is_paragraph_initial is True
    assert positions[0].is_paragraph_final is False
    # Last sentence of paragraph 0
    assert positions[2].paragraph_index == 0
    assert positions[2].position_in_paragraph == 2
    assert positions[2].is_paragraph_final is True


def test_parse_document_single_sentence_paragraph():
    """A 1-sentence paragraph: both initial AND final."""
    positions = p.parse_document(_FIXTURE)
    # Paragraph 2 is "Third paragraph is single-sentence."
    pos = [s for s in positions if s.paragraph_index == 2]
    assert len(pos) == 1
    assert pos[0].is_paragraph_initial is True
    assert pos[0].is_paragraph_final is True
    assert pos[0].paragraph_size == 1


def test_parse_document_empty_input():
    assert p.parse_document("") == []


def test_parse_document_accepts_external_sentences():
    """Caller-supplied per-paragraph sentence lists bypass the regex."""
    external = [
        ["Alpha.", "Beta.", "Gamma."],
        ["Delta."],
    ]
    positions = p.parse_document("ignored", sentences_per_paragraph=external)
    assert len(positions) == 4
    assert positions[0].text == "Alpha."
    assert positions[2].is_paragraph_final is True  # Gamma
    assert positions[3].is_paragraph_initial is True  # Delta
    assert positions[3].is_paragraph_final is True


# --------------- paragraph_final_sentences ----------------------


def test_paragraph_final_sentences_count():
    """One per paragraph in the fixture: 4 paragraphs → 4 finals."""
    finals = p.paragraph_final_sentences(_FIXTURE)
    assert len(finals) == 4


def test_paragraph_final_sentences_have_correct_flag():
    finals = p.paragraph_final_sentences(_FIXTURE)
    for s in finals:
        assert s.is_paragraph_final is True


def test_paragraph_final_sentences_text():
    """The actual closing sentences are surfaced correctly."""
    finals = p.paragraph_final_sentences(_FIXTURE)
    assert finals[0].text == "The third one closes it."
    assert finals[1].text == "And another."
    assert finals[2].text == "Third paragraph is single-sentence."
    assert finals[3].text == "Has two sentences."


# --------------- paragraph_count + paragraph_stats --------------


def test_paragraph_count_fixture():
    assert p.paragraph_count(_FIXTURE) == 4
    assert p.paragraph_count("") == 0


def test_paragraph_stats_fixture():
    """Stats reflect the 4-paragraph / 9-sentence fixture shape."""
    stats = p.paragraph_stats(_FIXTURE)
    assert stats["paragraph_count"] == 4
    assert stats["sentence_count"] == 9
    assert stats["mean_sentences_per_paragraph"] == 2.25
    assert stats["min_sentences_per_paragraph"] == 1
    assert stats["max_sentences_per_paragraph"] == 3
    assert stats["single_sentence_paragraph_count"] == 1


def test_paragraph_stats_empty():
    """Empty input returns a fully-zeroed stats block, no errors."""
    stats = p.paragraph_stats("")
    assert stats["paragraph_count"] == 0
    assert stats["sentence_count"] == 0
    assert stats["mean_sentences_per_paragraph"] == 0.0


# --------------- SentencePosition dataclass --------------------


def test_sentence_position_is_frozen():
    """Position records are immutable to prevent accidental mutation
    while walking the position-annotated stream."""
    positions = p.parse_document("One sentence.")
    pos = positions[0]
    with pytest.raises((AttributeError, TypeError)):
        pos.text = "mutated"
