#!/usr/bin/env python3
"""Regression tests for embedding_backend.py.

The module is a thin wrapper around sentence-transformers; tests
pin the wrapper's contract without loading real model weights:

  * Alias resolution: `mxbai`, `gemma`, `minilm` map to the right
    HuggingFace identifiers.
  * Reverse alias detection: passing a full id matching a known
    alias surfaces the alias in the identifier block.
  * Lazy load: instantiation does not load the model; `.encode()`
    does.
  * Missing-package failure: when sentence-transformers is not
    importable, `.encode()` raises `EmbeddingBackendError` with an
    install hint, not a silent fallback.
  * Identifier block: returns the shape PROVENANCE consumers expect.
  * Empty input: `.encode([])` returns an empty numpy array without
    loading the model.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import embedding_backend as eb  # type: ignore


# --------------- Alias resolution -------------------------------


def test_aliases_resolve_to_full_huggingface_ids():
    assert eb.MODEL_ALIASES["mxbai"] == "mixedbread-ai/mxbai-embed-large-v1"
    assert eb.MODEL_ALIASES["gemma"] == "google/embeddinggemma-300m"
    assert eb.MODEL_ALIASES["harrier"] == "microsoft/harrier-oss-v1-270m"
    assert "MiniLM" in eb.MODEL_ALIASES["minilm"]


def test_harrier_alias_resolves_to_full_id():
    """Harrier-OSS-v1-270m (Microsoft, MIT, released 2026-03-30) is
    one of the five §6.4 fixture-test candidates per the
    embedding-model-choice spec revision 4. Added in v1.45.0 as a
    follow-up to that spec revision."""
    b = eb.EmbeddingBackend(model_id="harrier")
    assert b.model_id == "microsoft/harrier-oss-v1-270m"
    assert b._alias == "harrier"


def test_construction_with_harrier_full_id_finds_alias():
    """Reverse lookup: a full Harrier HF id should report the
    `harrier` alias in the identifier block. Lets PROVENANCE
    consumers group runs by alias even when the user passed the
    full id."""
    b = eb.EmbeddingBackend(model_id="microsoft/harrier-oss-v1-270m")
    assert b.model_id == "microsoft/harrier-oss-v1-270m"
    assert b._alias == "harrier"


def test_construction_with_alias_resolves_to_full_id():
    b = eb.EmbeddingBackend(model_id="mxbai")
    assert b.model_id == "mixedbread-ai/mxbai-embed-large-v1"
    assert b._alias == "mxbai"


def test_construction_with_full_id_finds_known_alias():
    b = eb.EmbeddingBackend(model_id="google/embeddinggemma-300m")
    assert b.model_id == "google/embeddinggemma-300m"
    assert b._alias == "gemma"


def test_construction_with_unknown_id_passes_through():
    b = eb.EmbeddingBackend(model_id="my-org/my-model")
    assert b.model_id == "my-org/my-model"
    assert b._alias is None


# --------------- resolve_model_arg ------------------------------


def test_resolve_model_arg_none_returns_default():
    assert eb.resolve_model_arg(None) == eb.DEFAULT_MODEL


def test_resolve_model_arg_passes_through_known_alias():
    assert eb.resolve_model_arg("mxbai") == "mxbai"


def test_resolve_model_arg_passes_through_full_id():
    assert eb.resolve_model_arg("my-org/my-model") == "my-org/my-model"


# --------------- Lazy load --------------------------------------


def test_construction_does_not_load_model():
    """Instantiating an EmbeddingBackend must not trigger a model
    download or load. This matters for `--help`, for argparse
    failures, and for any caller that constructs a backend defensively
    and may never actually encode."""
    b = eb.EmbeddingBackend(model_id="mxbai")
    assert b._model is None


# --------------- Missing-package handling -----------------------


def test_encode_raises_when_sentence_transformers_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    """When sentence-transformers is not importable, `.encode()`
    must raise `EmbeddingBackendError` with a helpful message — not
    a silent fallback to TF-IDF or to zeros. Callers that want
    fallback behavior own that decision."""
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _no_sentence_transformers(name, *args, **kwargs):
        if name == "sentence_transformers":
            raise ImportError("simulated: sentence-transformers not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _no_sentence_transformers)
    b = eb.EmbeddingBackend(model_id="mxbai")
    with pytest.raises(eb.EmbeddingBackendError) as exc:
        b.encode(["sample text"])
    assert "sentence-transformers" in str(exc.value)
    assert "pip install" in str(exc.value)


def test_encode_raises_when_model_load_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    """Model-load failures (network timeout, unknown id) bubble up
    as EmbeddingBackendError so the caller sees a typed failure."""
    fake_st = mock.MagicMock()
    fake_st.SentenceTransformer.side_effect = RuntimeError("simulated load failure")
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    b = eb.EmbeddingBackend(model_id="not-a-real-model")
    # Reset state to force a load attempt.
    b._model = None
    with pytest.raises(eb.EmbeddingBackendError) as exc:
        b.encode(["sample"])
    assert "Failed to load embedding model" in str(exc.value)
    assert "simulated load failure" in str(exc.value)


# --------------- Empty input ------------------------------------


def test_encode_empty_returns_empty_array_without_loading():
    """Encoding an empty list should not trigger model load — this
    is the cheap-out path for callers that may have nothing to
    encode (e.g., a text that produced zero windows)."""
    b = eb.EmbeddingBackend(model_id="mxbai")
    out = b.encode([])
    assert out.shape == (0, 0)
    # Model still not loaded.
    assert b._model is None


# --------------- Encoded output via stub -------------------------


def test_encode_passes_kwargs_through_to_sentence_transformers(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify the wrapper passes the right kwargs through to
    SentenceTransformer.encode (no progress bar, the right batch
    size, normalize flag, convert to numpy)."""
    import numpy as np
    captured = {}

    class _FakeModel:
        def encode(self, texts, **kwargs):
            captured.update({"texts": texts, "kwargs": kwargs})
            return np.zeros((len(texts), 4), dtype="float32")

    fake_st = mock.MagicMock()
    fake_st.SentenceTransformer.return_value = _FakeModel()
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)
    b = eb.EmbeddingBackend(model_id="mxbai")
    out = b.encode(["a", "b"], batch_size=64, normalize=True)
    assert out.shape == (2, 4)
    assert captured["texts"] == ["a", "b"]
    assert captured["kwargs"]["batch_size"] == 64
    assert captured["kwargs"]["normalize_embeddings"] is True
    assert captured["kwargs"]["show_progress_bar"] is False
    assert captured["kwargs"]["convert_to_numpy"] is True


# --------------- Identifier block -------------------------------


def test_identifier_block_shape():
    b = eb.EmbeddingBackend(
        model_id="mxbai", revision="sha-abc123", deterministic=True,
    )
    out = b.identifier_block()
    assert out["id"] == "mixedbread-ai/mxbai-embed-large-v1"
    assert out["revision"] == "sha-abc123"
    assert out["alias"] == "mxbai"
    assert out["deterministic_mode"] is True
    assert out["method"] == "sentence-transformers"


def test_identifier_block_unknown_id_reports_none_alias():
    b = eb.EmbeddingBackend(model_id="my-org/my-model")
    out = b.identifier_block()
    assert out["alias"] is None
    assert out["id"] == "my-org/my-model"


# ---------- Reviewer P2 (2026-05-14 retroactive audit) ----------


class TestEncodeRuntimeErrorWrapping:
    """Reviewer P2 from the retroactive R12 audit: ``encode()``
    wrapped load failures but not runtime ``model.encode()``
    failures. A bare RuntimeError / IndexError / MemoryError from
    sentence-transformers escaped, and
    ``semantic_trajectory_audit.main()`` only catches
    ``EmbeddingBackendError`` → CLI traceback instead of the
    documented clean-error path. Same shape as the
    ``audit_surprisal`` P2 fix from PR #30."""

    def _make_backend_with_stub_model(self, raises):
        """Build a backend whose internal ``_model`` is a stub
        that raises the prescribed exception class on ``encode``.
        Bypasses ``_load`` so we don't need sentence-transformers
        installed for the test."""
        backend = eb.EmbeddingBackend(model_id="stub/test-model")

        class _StubModel:
            def encode(self, texts, **kwargs):
                raise raises("simulated sentence-transformers failure")

        backend._model = _StubModel()
        return backend

    def test_runtime_error_is_wrapped(self):
        """A bare RuntimeError from model.encode (the
        sentence-transformers OOM / device-error shape) must be
        wrapped as EmbeddingBackendError so callers'
        ``except EmbeddingBackendError`` blocks fire."""
        backend = self._make_backend_with_stub_model(RuntimeError)
        with pytest.raises(eb.EmbeddingBackendError) as excinfo:
            backend.encode(["some text"])
        msg = str(excinfo.value)
        assert "encode failed" in msg
        assert "RuntimeError" in msg
        # Diagnostic mentions the common causes.
        assert "context window" in msg or "memory" in msg

    def test_index_error_is_wrapped(self):
        """IndexError (tokenizer-shape surprise) also wraps."""
        backend = self._make_backend_with_stub_model(IndexError)
        with pytest.raises(eb.EmbeddingBackendError) as excinfo:
            backend.encode(["some text"])
        assert "IndexError" in str(excinfo.value)

    def test_memory_error_is_wrapped(self):
        backend = self._make_backend_with_stub_model(MemoryError)
        with pytest.raises(eb.EmbeddingBackendError) as excinfo:
            backend.encode(["some text"])
        assert "MemoryError" in str(excinfo.value)

    def test_value_error_is_wrapped(self):
        """ValueError catches sentence-transformers' input-shape
        complaints (e.g., empty string in a batch with strict mode)."""
        backend = self._make_backend_with_stub_model(ValueError)
        with pytest.raises(eb.EmbeddingBackendError) as excinfo:
            backend.encode(["some text"])
        assert "ValueError" in str(excinfo.value)

    def test_oserror_is_wrapped(self):
        """OSError covers device-level failures (CUDA driver
        errors surface as OSError on some platforms)."""
        backend = self._make_backend_with_stub_model(OSError)
        with pytest.raises(eb.EmbeddingBackendError) as excinfo:
            backend.encode(["some text"])
        assert "OSError" in str(excinfo.value)

    def test_typed_backend_error_passes_through(self):
        """``EmbeddingBackendError`` raised from inside encode (or
        re-raised from ``_load`` having been called inside
        encode's call chain) must NOT be re-wrapped. Callers that
        distinguish load-vs-runtime failures see the original
        typed exception verbatim."""
        backend = eb.EmbeddingBackend(model_id="stub/test")

        class _AlreadyTypedFailureModel:
            def encode(self, texts, **kwargs):
                raise eb.EmbeddingBackendError("inner typed failure")

        backend._model = _AlreadyTypedFailureModel()
        with pytest.raises(eb.EmbeddingBackendError) as excinfo:
            backend.encode(["x"])
        # Original message preserved (NOT wrapped with "encode failed").
        assert str(excinfo.value) == "inner typed failure"

    def test_empty_texts_does_not_trigger_wrapping(self):
        """The empty-list short-circuit must still return the
        empty ndarray without going through the encode path
        (otherwise we'd risk wrapping a non-failure)."""
        backend = eb.EmbeddingBackend(model_id="stub/test")
        # No _model set; the empty-list path should not call _load.
        result = backend.encode([])
        assert result.shape == (0, 0)
