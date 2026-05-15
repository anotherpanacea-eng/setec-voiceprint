#!/usr/bin/env python3
"""Regression tests for embeddings.py.

Tests the contract without forcing a spaCy vectors model to be
installed in CI environments. Most tests use mocking; a few are
gated on the vectors model being available (skip otherwise).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import embeddings as e  # type: ignore


def _check_vectors_model_available() -> bool:
    """Return True if a spaCy vectors model is installable."""
    try:
        import spacy  # type: ignore
        for name in ("en_core_web_md", "en_core_web_lg"):
            try:
                spacy.load(name)
                return True
            except OSError:
                continue
        return False
    except ImportError:
        return False


_HAS_VECTORS = _check_vectors_model_available()
_skip_no_vectors = pytest.mark.skipif(
    not _HAS_VECTORS,
    reason="No spaCy vectors model installed; install en_core_web_md to run",
)


@pytest.fixture(autouse=True)
def clear_nlp_cache():
    """Reset the spaCy-load lru_cache between tests."""
    e._get_nlp.cache_clear()
    yield
    e._get_nlp.cache_clear()


# --------------- Missing-model error path -----------------------


def test_missing_vectors_model_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch,
):
    """When neither md nor lg is installed, `EmbeddingsBackendError`
    fires with operator-facing install guidance."""
    fake_spacy = mock.MagicMock()
    fake_spacy.load.side_effect = OSError(
        "[E050] Can't find model"
    )
    monkeypatch.setitem(sys.modules, "spacy", fake_spacy)
    with pytest.raises(e.EmbeddingsBackendError) as exc:
        e._get_nlp()
    msg = str(exc.value)
    assert "en_core_web_md" in msg
    assert "spacy download" in msg


def test_missing_spacy_raises_typed_error(monkeypatch: pytest.MonkeyPatch):
    """When spaCy itself is unimportable, the typed error fires."""
    real_import = (
        __builtins__["__import__"] if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )

    def _no_spacy(name, *args, **kwargs):
        if name == "spacy":
            raise ImportError("simulated: spacy not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _no_spacy)
    with pytest.raises(e.EmbeddingsBackendError) as exc:
        e._get_nlp()
    assert "spaCy is not installed" in str(exc.value)


# --------------- Vector lookups with mocked spaCy ---------------


def _make_fake_nlp_with_vectors():
    """Construct a fake spaCy pipeline whose vocab returns vector-
    bearing Lexeme stand-ins for a few known words."""
    import numpy as np

    # Vectors are chosen so:
    # - machinery <-> grief: low cosine (~0.1)
    # - heavy <-> burden: high cosine (~0.85)
    # - xyzzy: no vector
    vectors = {
        "machinery": np.array([1.0, 0.0, 0.0]),
        "grief": np.array([0.1, 1.0, 0.0]),  # cos with machinery ≈ 0.1
        "heavy": np.array([1.0, 1.0, 0.0]),
        "burden": np.array([1.0, 0.5, 0.0]),  # cos with heavy ≈ 0.95
    }

    class _Lex:
        def __init__(self, vec):
            self.vector = vec
            self.has_vector = vec is not None
            self.vector_norm = float(np.linalg.norm(vec)) if vec is not None else 0.0

    class _Vocab:
        def __getitem__(self, word):
            return _Lex(vectors.get(word.lower()))

    fake_nlp = mock.MagicMock()
    fake_nlp.vocab = _Vocab()
    fake_nlp.meta = {
        "name": "fake_test_model",
        "version": "0.0.0",
        "spacy_version": "test",
        "vectors": {"vectors": len(vectors)},
    }
    return fake_nlp


@pytest.fixture
def with_fake_nlp(monkeypatch):
    """Patch `_get_nlp` to return a fake pipeline with known vectors."""
    fake_nlp = _make_fake_nlp_with_vectors()

    def _fake_get():
        return fake_nlp

    monkeypatch.setattr(e, "_get_nlp", _fake_get)


def test_vector_returns_array_for_known_word(with_fake_nlp):
    v = e.vector("machinery")
    assert v is not None
    import numpy as np
    assert isinstance(v, np.ndarray)


def test_vector_returns_none_for_unknown_word(with_fake_nlp):
    assert e.vector("xyzzy_unknown") is None


def test_vector_is_case_insensitive(with_fake_nlp):
    """Vector lookup matches regardless of case."""
    a = e.vector("MACHINERY")
    b = e.vector("machinery")
    assert a is not None
    assert (a == b).all()  # numpy array equality


def test_cosine_similarity_basic_pair(with_fake_nlp):
    """machinery <-> grief should produce low cosine (~0.1)."""
    sim = e.cosine_similarity("machinery", "grief")
    assert sim is not None
    assert 0.0 < sim < 0.3  # low similarity → AIC-8 image-conjunction candidate


def test_cosine_similarity_idiom_pair(with_fake_nlp):
    """heavy <-> burden should produce high cosine (>0.85)."""
    sim = e.cosine_similarity("heavy", "burden")
    assert sim is not None
    assert sim > 0.85  # high similarity → conventional collocation


def test_cosine_similarity_returns_none_when_oov(with_fake_nlp):
    assert e.cosine_similarity("machinery", "xyzzy") is None
    assert e.cosine_similarity("xyzzy", "grief") is None


def test_cosine_similarity_is_symmetric(with_fake_nlp):
    a = e.cosine_similarity("machinery", "grief")
    b = e.cosine_similarity("grief", "machinery")
    assert a == pytest.approx(b)


def test_has_vector_distinguishes_known_unknown(with_fake_nlp):
    assert e.has_vector("machinery") is True
    assert e.has_vector("xyzzy_unknown") is False


def test_l2_distance_basic_pair(with_fake_nlp):
    """L2 distance returns a float for known pairs."""
    d = e.l2_distance("machinery", "grief")
    assert d is not None
    assert d > 0  # different vectors → non-zero distance


def test_l2_distance_oov_returns_none(with_fake_nlp):
    assert e.l2_distance("machinery", "xyzzy") is None


def test_model_identifier_block_shape(with_fake_nlp):
    block = e.model_identifier()
    assert block["model"] == "fake_test_model"
    assert block["method"] == "spacy-glove-vectors"
    assert "version" in block


# --------------- Integration (requires real spaCy vectors model) ---


@_skip_no_vectors
def test_real_md_loads_and_provides_vectors():
    """If en_core_web_md is installed, the real model should load
    and provide vectors for common English words."""
    nlp = e._get_nlp()
    assert nlp is not None
    # Common English word should have a vector.
    assert e.has_vector("table") is True
    sim = e.cosine_similarity("happy", "sad")
    assert sim is not None
    # Antonyms still cluster in similar semantic neighborhoods (this
    # is the GloVe distributional-hypothesis story); expect moderate-
    # to-high cosine.
    assert sim > 0.3
