#!/usr/bin/env python3
"""embeddings.py — spaCy-backed word-vector helpers.

Thin wrapper around spaCy's GloVe-derived 300d word vectors,
exposed as ``vector(word)`` and ``cosine_similarity(a, b)`` for the
AIC-8 image-conjunction detector. Reuses the framework's existing
spaCy dependency rather than introducing a new embeddings stack
(Word2Vec, GloVe binary files, BERT).

**Model requirement**: spaCy's vector-bearing models. The framework
ships with the small model (`en_core_web_sm`) as a runtime default,
but that model **has no word vectors** — it's POS-tagging + parsing
only. The AIC-8 family requires `en_core_web_md` (~50 MB, 300d
GloVe vectors) or `en_core_web_lg` (~700 MB, more coverage). The
loader prefers `_md` and falls back to `_lg`; both work
identically for cosine-similarity callers.

Install once via:

    python -m spacy download en_core_web_md

Design notes:

  * **Lazy load, cached.** The model loads on first call to
    ``_get_nlp()``. Subsequent calls reuse. Module-level
    initialization would force the import-cost on every audit, even
    those that don't need word vectors.
  * **Missing-model failure is loud.** Raising
    ``EmbeddingsBackendError`` with the install hint, rather than
    silently degrading to TF-IDF or skipping, prevents AIC-8
    audits from running on zero-information vectors.
  * **None for out-of-vocab.** spaCy returns a zero-vector for
    unknown words; we return ``None`` so callers don't accidentally
    compute cosine similarity against the origin (which is 0/0 →
    NaN or undefined).
  * **Cosine math is explicit.** spaCy provides ``.similarity()``
    on Lexeme objects, but the documented behavior is "cosine if
    both have vectors, 0 otherwise" with no None-signal. We
    compute the cosine directly so the failure modes are explicit
    and inspectable.

The selected embedding model is documented per-audit in PROVENANCE
via ``identifier_block()``. Future revisions of this module may
re-evaluate against the field's better models (BERT contextual
embeddings, newer GloVe families, sentence-transformers); the
roadmap carries a tickler for the 2026-H2 re-evaluation.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Optional

# Preferred order: md (cheap, sufficient), then lg (more coverage).
# Both ship 300d GloVe-derived vectors. sm is rejected explicitly:
# its `has_vector` returns False for every word and would silently
# produce zero-similarity outputs.
_PREFERRED_MODELS: tuple[str, ...] = ("en_core_web_md", "en_core_web_lg")


class EmbeddingsBackendError(RuntimeError):
    """Raised when no usable spaCy vectors model is available.

    Typed exception so callers in the AIC-8 stack can distinguish
    "no model installed" from generic runtime errors and surface
    actionable install guidance.
    """


@lru_cache(maxsize=1)
def _get_nlp() -> Any:
    """Return a loaded spaCy pipeline with word vectors.

    Tries `en_core_web_md` first, then `en_core_web_lg`. Raises
    `EmbeddingsBackendError` with install guidance if neither is
    installed. Caches the loaded pipeline at module level so
    subsequent calls don't re-pay the import cost.
    """
    try:
        import spacy  # type: ignore
    except ImportError as exc:
        raise EmbeddingsBackendError(
            "spaCy is not installed. Install with: "
            "pip install -r plugins/setec-voiceprint/requirements.txt"
        ) from exc
    last_err: Optional[Exception] = None
    for model_name in _PREFERRED_MODELS:
        try:
            return spacy.load(model_name)
        except OSError as exc:
            last_err = exc
            continue
    raise EmbeddingsBackendError(
        "No spaCy vectors model installed. AIC-8 requires "
        "`en_core_web_md` (preferred, ~50 MB) or `en_core_web_lg` "
        "(~700 MB). Install with: "
        "python -m spacy download en_core_web_md. "
        f"Last load error: {type(last_err).__name__}: {last_err}"
    )


def vector(word: str) -> Optional[Any]:
    """Return the spaCy vector for ``word``, or ``None`` if unknown.

    Case-insensitive lookup (the underlying spaCy vocab is
    lowercase-normalized). Returns ``None`` for out-of-vocab words
    rather than spaCy's default zero-vector, so callers can
    distinguish "no vector" from "vector at origin."
    """
    nlp = _get_nlp()
    lex = nlp.vocab[word.lower()]
    if not lex.has_vector:
        return None
    return lex.vector


def cosine_similarity(word_a: str, word_b: str) -> Optional[float]:
    """Cosine similarity in [-1, 1] between the two words' vectors.

    Returns ``None`` if either word is out-of-vocab. In practice
    English content-word similarities under spaCy's `_md` model
    cluster in `[0, 1]` (negative cosines are rare for natural
    English vocabulary). Idiomatic collocations score high
    (`heavy/burden` ≈ 0.5+); semantically distant pairs score low
    (`grammar/desire` ≈ 0.1-0.2). The AIC-8 image-conjunction
    detector uses this as the second filter in its compound
    diagnostic: low similarity + high concreteness gap = image
    conjunction.
    """
    va = vector(word_a)
    vb = vector(word_b)
    if va is None or vb is None:
        return None
    # Numpy is a transitive dep of spaCy; no need to import-guard.
    import numpy as np  # type: ignore
    norm_a = float(np.linalg.norm(va))
    norm_b = float(np.linalg.norm(vb))
    if norm_a == 0.0 or norm_b == 0.0:
        return None
    dot = float(np.dot(va, vb))
    return dot / (norm_a * norm_b)


def has_vector(word: str) -> bool:
    """Return True if ``word`` has a vector in the loaded model.

    Convenience for callers who need to filter a token list to
    those with computable vectors before pairwise similarity work.
    Does not raise on out-of-vocab — only raises if no model is
    installed at all.
    """
    return vector(word) is not None


def model_identifier() -> dict[str, str]:
    """Return a PROVENANCE-consumer identifier block for the model.

    Names the spaCy model in use, the spaCy version, and the
    framework's intent ("transformers-causal-lm"-style key for
    consistency with `surprisal_backend.identifier_block()`).
    Callers paste this into per-audit PROVENANCE output so
    downstream readers see which embedding model produced the
    cosine-similarity numbers.
    """
    nlp = _get_nlp()
    return {
        "model": nlp.meta.get("name", "unknown"),
        "version": nlp.meta.get("version", "unknown"),
        "spacy_version": nlp.meta.get("spacy_version", "unknown"),
        "vectors_size": str(nlp.meta.get("vectors", {}).get("vectors", "unknown")),
        "method": "spacy-glove-vectors",
    }


# --- Convenience: euclidean / l2 distance for callers that prefer ---


def l2_distance(word_a: str, word_b: str) -> Optional[float]:
    """Euclidean (L2) distance between two words' vectors.

    Less commonly used than cosine for word-similarity work, but
    surfaced here for diagnostics. Returns ``None`` for OOV words.
    """
    va = vector(word_a)
    vb = vector(word_b)
    if va is None or vb is None:
        return None
    import numpy as np  # type: ignore
    return float(math.sqrt(float(np.sum((va - vb) ** 2))))
