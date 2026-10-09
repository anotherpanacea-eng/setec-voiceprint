"""Final L1 owners for shared text-analysis primitives.

This module stays import-safe: it owns data and pure primitives without
loading model stacks, accessing corpora, or emitting surface envelopes.
Registry identities and characterization rows are added only in their
separate R2 cohorts.
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


# Closed rows bind final defining source and referenced pattern declaration.
from types import MappingProxyType as _MappingProxyType

SENTENCE_SPLITTERS = _MappingProxyType({
    'split_sentences_punkt': _MappingProxyType({'id': 'sentence_splitter-0a069702b2f6-v1',
     'family': 'sentence_splitter',
     'implementation_ref': 'plugins/setec-voiceprint/scripts/setec/core/textprims.py:split_sentences_punkt',
     'pattern_sha256': None,
     'case_policy': 'preserve',
     'unicode_normalization': 'none',
     'allowed_backends': ('nltk',),
     'behavior_sha256': '0a069702b2f6cdacfa4fd44828ea65fe79edca38f589dfa8facc4a8ec49989da'}),
    'split_sentences_regex': _MappingProxyType({'id': 'sentence_splitter-bace7d2ce7a5-v1',
     'family': 'sentence_splitter',
     'implementation_ref': 'plugins/setec-voiceprint/scripts/setec/core/textprims.py:split_sentences_regex',
     'pattern_sha256': '560a69ee13a6d8414aa14c1c90f7395985cc4169eeb232767a161518850206f6',
     'case_policy': 'preserve',
     'unicode_normalization': 'none',
     'allowed_backends': (),
     'behavior_sha256': 'bace7d2ce7a5a225448442f1d7fa355b718bb18f398ac27267cc58c8a03554f5'}),
})
def __getattr__(name):
    if name == "tokenize":
        from setec.core.passage_tokenizer_v1 import tokenize
        return tokenize
    raise AttributeError(name)


TOKENIZERS = _MappingProxyType({
    "tokenize": _MappingProxyType({'id': 'tokenizer-d6e53cf12864-v1',
 'family': 'tokenizer',
 'implementation_ref': 'plugins/setec-voiceprint/scripts/setec/core/passage_tokenizer_v1.py:tokenize',
 'pattern_sha256': '13df86429c6498c2cbffe6dacad99dae69824f69775a23514bd053a8a1633aed',
 'case_policy': 'lower',
 'unicode_normalization': 'frozen_table',
 'allowed_backends': (),
 'behavior_sha256': 'd6e53cf12864e702a89ed5c3d0d2f1a5b56c5dacafe627385244cfbb9ed10c02'}),
})
PARAGRAPH_SPLITTERS = _MappingProxyType({})
FUNCTION_WORD_SETS = _MappingProxyType({
    'FUNCTION_WORDS': _MappingProxyType({'id': 'function_words-297455e23b54-v1',
 'family': 'function_words',
 'implementation_ref': 'plugins/setec-voiceprint/scripts/setec/core/textprims.py:FUNCTION_WORDS',
 'pattern_sha256': '36049cc02a8d65add587f8d6c96029627c173bc86a94c886476bf6a23a8ce29c',
 'case_policy': 'preserve',
 'unicode_normalization': 'none',
 'allowed_backends': (),
 'behavior_sha256': '297455e23b5447903d9fe9d62c6f5e47f9c7820103aefcbbd48fe3cd09d2dacc'}),
    'DIALOGUE_FUNCTION_WORDS': _MappingProxyType({'id': 'function_words-80dee76c8121-v1',
 'family': 'function_words',
 'implementation_ref': 'plugins/setec-voiceprint/scripts/setec/core/textprims.py:DIALOGUE_FUNCTION_WORDS',
 'pattern_sha256': 'c71678be291fdf2b2df6ee74db20ecf0860acd981ea08ce99334e421200107fa',
 'case_policy': 'preserve',
 'unicode_normalization': 'none',
 'allowed_backends': (),
 'behavior_sha256': '80dee76c81212de0ddbb226b2536eb0f2db21b4834c3f89afd8331c8fd473ff5'}),
})
QUANTILES = _MappingProxyType({})
FINGERPRINTS = _MappingProxyType({})
PREPROCESSORS = _MappingProxyType({})
