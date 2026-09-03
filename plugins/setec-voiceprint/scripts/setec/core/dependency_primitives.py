"""Parser-only dependency-distance primitives shared by audit surfaces.

This L1 module deliberately owns only the spaCy parser seam and dependency
distance math.  Keep embedding, classifier, and corpus dependencies out so a
parser-only capability does not pay the monolithic ``variance_audit`` import
cost.
"""

from __future__ import annotations

import statistics
from typing import Any

try:
    import spacy  # type: ignore

    try:
        _NLP = spacy.load("en_core_web_sm")
        HAS_SPACY = True
    except Exception:
        HAS_SPACY = False
        _NLP = None
except ImportError:
    HAS_SPACY = False
    _NLP = None


def mdd_stats(text: str, *, nlp: Any | None = None) -> dict[str, Any] | None:
    """Return per-sentence mean dependency-distance statistics.

    ``nlp`` is injectable so compatibility callers can preserve their existing
    parser seam while sharing the implementation.  With no explicit parser,
    the module's parser is used and an unavailable parser returns ``None``.
    """
    parser = _NLP if nlp is None else nlp
    if parser is None:
        return None

    doc = parser(text)
    per_sentence = []
    for sent in doc.sents:
        toks = [token for token in sent if not token.is_space]
        if len(toks) < 2:
            continue
        distances = []
        for token in toks:
            if token.dep_ == "ROOT" or token.head is token:
                continue
            distances.append(abs(token.i - token.head.i))
        if distances:
            per_sentence.append(sum(distances) / len(distances))

    if len(per_sentence) < 2:
        return {
            "n_sentences": len(per_sentence),
            "mean": per_sentence[0] if per_sentence else 0.0,
            "sd": 0.0,
        }
    return {
        "n_sentences": len(per_sentence),
        "mean": statistics.mean(per_sentence),
        "sd": statistics.stdev(per_sentence),
    }
