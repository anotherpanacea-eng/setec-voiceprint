"""Final L1 owners for shared text-analysis primitives.

This module stays import-safe: it owns data and pure primitives without
loading model stacks, accessing corpora, or emitting surface envelopes.
`PRIMITIVES` below names the one owner of each shared primitive.
"""

from __future__ import annotations

import re


# Top function words (Mosteller-Wallace + extensions).
FUNCTION_WORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an",
    "and", "any", "are", "as", "at", "be", "because", "been", "before",
    "being", "below", "between", "both", "but", "by", "could", "did", "do",
    "does", "doing", "down", "during", "each", "few", "for", "from",
    "further", "had", "has", "have", "having", "he", "her", "here", "hers",
    "herself", "him", "himself", "his", "how", "i", "if", "in", "into", "is",
    "it", "its", "itself", "just", "me", "might", "mine", "more", "most",
    "must", "my", "myself", "no", "nor", "not", "now", "of", "off", "on",
    "once", "one", "only", "or", "other", "ought", "our", "ours", "ourselves",
    "out", "over", "own", "same", "shall", "she", "should", "so", "some",
    "such", "than", "that", "the", "their", "theirs", "them", "themselves",
    "then", "there", "these", "they", "this", "those", "through", "to", "too",
    "under", "until", "up", "upon", "us", "very", "was", "we", "were", "what",
    "when", "where", "which", "while", "who", "whom", "whose", "why", "will",
    "with", "would", "yet", "you", "your", "yours", "yourself", "yourselves",
}

# Dialogue-specific function words used for per-character distributions.
# This intentionally differs from variance_audit's broader 135-word set.
DIALOGUE_FUNCTION_WORDS = {
    "a", "about", "after", "again", "all", "am", "an", "and", "any",
    "are", "as", "at", "be", "because", "been", "but", "by", "can",
    "could", "did", "do", "does", "for", "from", "had", "has", "have",
    "he", "her", "here", "him", "his", "how", "i", "if", "in", "into",
    "is", "it", "its", "just", "me", "more", "my", "no", "not", "now",
    "of", "off", "on", "one", "or", "our", "out", "over", "she",
    "should", "so", "some", "than", "that", "the", "their", "them",
    "then", "there", "these", "they", "this", "to", "too", "up", "us",
    "very", "was", "we", "were", "what", "when", "where", "which",
    "who", "why", "will", "with", "would", "yes", "you", "your",
}


_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])|\n{2,}")


def split_sentences_punkt(text: str) -> list[str]:
    from nltk.tokenize import sent_tokenize  # type: ignore
    return [s.strip() for s in sent_tokenize(text) if s.strip()]


def split_sentences_regex(text: str) -> list[str]:
    parts = _SENT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def _normws(s: str) -> str:
    """Whitespace-normalized form for a tolerant verbatim-containment check."""
    return " ".join(s.split())


_WORD_RE = re.compile(r"[A-Za-z']+")


def count_words_alpha(text: str) -> int:
    return len(_WORD_RE.findall(text))


# Enthymeme text units retain their distinct alphanumeric and sentence rules.
_WORD_ALNUM_RE = re.compile(r"[A-Za-z0-9']+")

_ENTHYMEME_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

_ENTHYMEME_STOPWORDS = frozenset((
    "a", "an", "the", "and", "or", "but", "if", "then", "so", "of", "to", "in",
    "on", "at", "by", "for", "with", "as", "is", "are", "was", "were", "be",
    "been", "being", "it", "its", "this", "that", "these", "those", "we", "you",
    "they", "he", "she", "i", "not", "no", "do", "does", "did", "have", "has",
    "had", "will", "would", "can", "could", "should", "may", "might", "must",
    "from", "into", "than", "such", "which", "who", "what", "there", "their",
    "them", "our", "us", "all", "any", "more", "most", "some", "very", "also",
))


def count_words_alnum(text: str) -> int:
    return len(_WORD_ALNUM_RE.findall(text))


def split_sentences_enthymeme(paragraph: str) -> list[str]:
    """Deterministic stdlib sentence split within a paragraph (not a parser)."""
    raw = _ENTHYMEME_SENT_SPLIT_RE.split(paragraph.strip())
    return [s.strip() for s in raw if s.strip()]


def content_tokens_enthymeme(text: str) -> set[str]:
    """Stopword-filtered lowercase content tokens — the set the tautology guard compares."""
    return {t for t in _WORD_ALNUM_RE.findall(text.lower()) if t not in _ENTHYMEME_STOPWORDS}


def split_paragraphs_blanklines(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n", text.strip())
    return [p.strip() for p in parts if p.strip()]


_WORD_UNICODE_HYPHEN_RE = re.compile(r"\b\w[\w'-]*\b", re.UNICODE)


def count_words_unicode_hyphen(text: str) -> int:
    return len(_WORD_UNICODE_HYPHEN_RE.findall(text))


WORD_RE = re.compile(r"[A-Za-z']+")


def word_tokens_alpha(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(text)]


_WORD_UNICODE_RE = re.compile(r"\b\w+\b")


def count_words_unicode(text: str) -> int:
    return len(_WORD_UNICODE_RE.findall(text))


# The one owner module of each shared text primitive. Each is importable from
# here; those owned elsewhere load lazily on first use, so importing this
# module stays light. Behavior is pinned by
# references/textprims/characterization.json, and
# tools/gen_textprims_inventory.py refuses new copies of these definitions.
from types import MappingProxyType as _MappingProxyType

PRIMITIVES = _MappingProxyType({
    "FUNCTION_WORDS": "setec.core.textprims",
    "DIALOGUE_FUNCTION_WORDS": "setec.core.textprims",
    "split_sentences_punkt": "setec.core.textprims",
    "split_sentences_regex": "setec.core.textprims",
    "_normws": "setec.core.textprims",
    "count_words_alpha": "setec.core.textprims",
    "count_words_alnum": "setec.core.textprims",
    "count_words_unicode_hyphen": "setec.core.textprims",
    "word_tokens_alpha": "setec.core.textprims",
    "count_words_unicode": "setec.core.textprims",
    "split_sentences_enthymeme": "setec.core.textprims",
    "content_tokens_enthymeme": "setec.core.textprims",
    "tokenize": "setec.core.passage_tokenizer_v1",
    "_tokens": "setec.core.verbatim_cover",
    "_content_fingerprint": "setec.core.verbatim_cover",
    "split_paragraphs_blanklines": "setec.core.textprims",
    "split_paragraphs": "setec.core.paragraph_parser",
    "split_sentences": "setec.core.paragraph_parser",
    "_analysis": "setec.preflight.common",
})


def __getattr__(name):
    owner = PRIMITIVES.get(name)
    if owner is None or owner == __name__:
        raise AttributeError(name)
    import importlib
    return getattr(importlib.import_module(owner), name)
