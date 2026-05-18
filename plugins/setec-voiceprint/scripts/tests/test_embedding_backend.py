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


# ============================================================
# Dtype + device awareness (mirrors PR #93 / #88 on the
# surprisal side; this is the embedding-side analogue).
# ============================================================

try:
    import torch as _torch  # type: ignore
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

_skip_no_torch = pytest.mark.skipif(
    not _HAS_TORCH,
    reason="torch not installed; dtype resolution requires torch",
)


# ---------- Construction-time validation (pure-Python) ----------


def test_default_dtype_is_auto():
    """Default dtype is ``"auto"`` so an operator who doesn't set
    ``--embedding-dtype`` gets bf16 on Hopper, fp16 on Ampere-down,
    fp32 on CPU. Pinning this means a future patch that changes the
    default trips the test rather than silently inverting the
    auto-resolution contract."""
    b = eb.EmbeddingBackend(model_id="mxbai")
    assert b.dtype == "auto"


def test_default_device_is_none():
    """Default device is ``None`` so sentence-transformers' built-in
    auto-device logic picks cuda > mps > cpu. Operators on multi-
    GPU hosts override per call via ``device="cuda:1"``."""
    b = eb.EmbeddingBackend(model_id="mxbai")
    assert b.device is None


def test_invalid_dtype_raises_at_construction():
    """Construction-time validation: a typo on the dtype string
    fails fast with a typed error and a useful message naming the
    valid set, rather than escaping at first-encode as a sentence-
    transformers internal stack trace. Same shape as the
    surprisal-side contract from PR #93."""
    with pytest.raises(eb.EmbeddingBackendError) as excinfo:
        eb.EmbeddingBackend(model_id="mxbai", dtype="fp64")
    assert "fp64" in str(excinfo.value)
    assert "auto" in str(excinfo.value)  # the message lists valid values


def test_valid_dtypes_all_accepted():
    """All four documented dtype strings accept at construction.
    Pins the validation logic doesn't reject something the CLI
    accepts; the surprisal-side test has the same shape."""
    for dt in ("auto", "fp32", "fp16", "bf16"):
        b = eb.EmbeddingBackend(model_id="mxbai", dtype=dt)
        assert b.dtype == dt


def test_pre_load_identifier_block_surfaces_requested_but_not_loaded():
    """Before ``_load`` has run, ``dtype_loaded`` and
    ``device_loaded`` are ``None`` — the model hasn't been built
    yet, so there's no resolved state. ``dtype_requested`` and
    ``device_requested`` reflect operator intent immediately.
    Distinguishes the pre-load / post-load contract."""
    b = eb.EmbeddingBackend(
        model_id="mxbai", dtype="bf16", device="cuda:1",
    )
    block = b.identifier_block()
    assert block["dtype_requested"] == "bf16"
    assert block["dtype_loaded"] is None
    assert block["device_requested"] == "cuda:1"
    assert block["device_loaded"] is None


# ---------- _resolve_dtype probe outcomes (torch-gated) ----------


@_skip_no_torch
def test_resolve_dtype_auto_no_cuda_returns_fp32():
    """On a CPU-only host (or one with cuda available but no
    bf16 support and no fp16 demand-path), auto resolves to fp32.
    Embedding inference in bf16 on CPU is not faster (and often
    slower due to BF16-on-CPU emulation), so fp32 is the right
    default."""
    import torch
    out, label = eb._resolve_dtype("auto", cuda_available=False)
    assert label == "fp32"
    assert out == torch.float32


@_skip_no_torch
def test_resolve_dtype_auto_bf16_cuda_returns_bf16():
    """On bf16-supporting cuda (Ampere / Hopper / Ada), auto picks
    bf16 — the throughput win is real and there's no precision
    cliff on embedding models (the cosine similarities are stable
    in bf16 to within 1e-3)."""
    import torch
    out, label = eb._resolve_dtype(
        "auto", cuda_available=True, bf16_supported=True,
    )
    assert label == "bf16"
    assert out == torch.bfloat16


@_skip_no_torch
def test_resolve_dtype_auto_pre_ampere_cuda_returns_fp16():
    """On pre-Ampere cuda (V100 / T4), bf16 falls back to slow
    emulation kernels. auto picks fp16 instead — same dtype
    behaviour as the surprisal-side resolution from PR #93."""
    import torch
    out, label = eb._resolve_dtype(
        "auto", cuda_available=True, bf16_supported=False,
    )
    assert label == "fp16"
    assert out == torch.float16


@_skip_no_torch
def test_resolve_dtype_explicit_bf16_overrides_auto_resolution():
    """An explicit ``bf16`` request is honored even on hardware
    where auto would have picked something else. Lets operators
    pin a dtype for reproducibility across heterogeneous hosts."""
    import torch
    out, label = eb._resolve_dtype(
        "bf16", cuda_available=False, bf16_supported=False,
    )
    assert label == "bf16"
    assert out == torch.bfloat16


@_skip_no_torch
def test_resolve_dtype_explicit_fp16_overrides_auto_resolution():
    """Same contract for fp16."""
    import torch
    out, label = eb._resolve_dtype(
        "fp16", cuda_available=True, bf16_supported=True,
    )
    assert label == "fp16"
    assert out == torch.float16


@_skip_no_torch
def test_resolve_dtype_explicit_fp32_overrides_auto_resolution():
    """Same contract for fp32 — operators who want guaranteed
    precision regardless of hardware can pin fp32."""
    import torch
    out, label = eb._resolve_dtype(
        "fp32", cuda_available=True, bf16_supported=True,
    )
    assert label == "fp32"
    assert out == torch.float32


@_skip_no_torch
def test_resolve_dtype_rejects_unknown_string():
    """The free function does its own validation so direct callers
    (not just the dataclass) also fail-fast."""
    with pytest.raises(eb.EmbeddingBackendError) as excinfo:
        eb._resolve_dtype("fp64")
    assert "fp64" in str(excinfo.value)


# ---------- _load passes dtype + device into ST (stubbed) ----------


@_skip_no_torch
def test_load_passes_torch_dtype_via_model_kwargs(
    monkeypatch: pytest.MonkeyPatch,
):
    """The wrapper threads the resolved torch_dtype into
    ``SentenceTransformer(model_kwargs={"torch_dtype": ...})``.
    Without this, sentence-transformers loads in its fp32 default
    regardless of what the operator requested."""
    import torch
    captured: dict = {}

    class _FakeST:
        def __init__(self, model_id, **kwargs):
            captured["model_id"] = model_id
            captured["kwargs"] = kwargs

        def parameters(self):
            # Return a single zero-tensor as the model's sole
            # parameter so ``next(model.parameters()).device``
            # works for the device-probe path.
            yield torch.zeros(1)

    fake_module = mock.MagicMock()
    fake_module.SentenceTransformer = _FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    b = eb.EmbeddingBackend(model_id="mxbai", dtype="bf16")
    b._load()
    assert "model_kwargs" in captured["kwargs"]
    assert captured["kwargs"]["model_kwargs"] == {"torch_dtype": torch.bfloat16}


@_skip_no_torch
def test_load_passes_device_when_set(
    monkeypatch: pytest.MonkeyPatch,
):
    """When ``device`` is set, it's threaded into the ST
    constructor. When unset, ST's auto-device logic owns the
    placement (no ``device=`` kwarg passed)."""
    import torch
    captured: dict = {}

    class _FakeST:
        def __init__(self, model_id, **kwargs):
            captured["kwargs"] = kwargs

        def parameters(self):
            yield torch.zeros(1)

    fake_module = mock.MagicMock()
    fake_module.SentenceTransformer = _FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    b = eb.EmbeddingBackend(model_id="mxbai", device="cuda:1")
    b._load()
    assert captured["kwargs"].get("device") == "cuda:1"


@_skip_no_torch
def test_load_omits_device_kwarg_when_unset(
    monkeypatch: pytest.MonkeyPatch,
):
    """No ``device`` kwarg passed to ST when caller didn't ask for
    one — pins that we don't force ST off its auto-device logic
    (which already picks cuda > mps > cpu correctly)."""
    import torch
    captured: dict = {}

    class _FakeST:
        def __init__(self, model_id, **kwargs):
            captured["kwargs"] = kwargs

        def parameters(self):
            yield torch.zeros(1)

    fake_module = mock.MagicMock()
    fake_module.SentenceTransformer = _FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    b = eb.EmbeddingBackend(model_id="mxbai")
    b._load()
    assert "device" not in captured["kwargs"]


@_skip_no_torch
def test_load_records_resolved_dtype_label(
    monkeypatch: pytest.MonkeyPatch,
):
    """After ``_load`` runs, ``_resolved_dtype_label`` surfaces in
    ``identifier_block`` so provenance consumers see what the
    backend actually loaded (vs. the ``auto`` sentinel the
    operator might have passed)."""
    import torch

    class _FakeST:
        def __init__(self, *a, **kw):
            pass

        def parameters(self):
            yield torch.zeros(1)

    fake_module = mock.MagicMock()
    fake_module.SentenceTransformer = _FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    b = eb.EmbeddingBackend(model_id="mxbai", dtype="fp16")
    b._load()
    block = b.identifier_block()
    # Operator's ``fp16`` request is honored — no auto-resolution
    # interfering with explicit choice.
    assert block["dtype_requested"] == "fp16"
    assert block["dtype_loaded"] == "fp16"


@_skip_no_torch
def test_load_records_resolved_device_from_loaded_model(
    monkeypatch: pytest.MonkeyPatch,
):
    """``device_loaded`` reflects where the model's parameters
    actually landed — captured from ``next(model.parameters()).
    device``. Means an operator who didn't set ``--embedding-
    device`` still gets a useful audit field (e.g.,
    ``cuda:0`` on a single-GPU cloud host)."""
    import torch

    class _FakeST:
        def __init__(self, *a, **kw):
            pass

        def parameters(self):
            # Pretend the model landed on cuda:0.
            t = torch.zeros(1)
            # Tensor.device is a property on the real torch object;
            # we can't easily fake "cuda:0" without a real GPU, but
            # the device-probe path just str()s the device. For CPU
            # testing the captured value is "cpu" — what matters is
            # that the field is non-None after load.
            yield t

    fake_module = mock.MagicMock()
    fake_module.SentenceTransformer = _FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    b = eb.EmbeddingBackend(model_id="mxbai")
    b._load()
    block = b.identifier_block()
    # On a CPU-only test harness the captured device is "cpu";
    # the contract is that the field is non-None after load and
    # carries a string form usable by audit consumers.
    assert block["device_loaded"] is not None
    assert isinstance(block["device_loaded"], str)


@_skip_no_torch
def test_load_error_message_includes_dtype_context(
    monkeypatch: pytest.MonkeyPatch,
):
    """When ``_load`` fails (e.g., HF Hub timeout, model id typo),
    the wrapped error message names the dtype that was requested.
    Operators debugging an OOM at fp32 see ``dtype='fp32'`` in
    the failure message and know to retry with bf16/fp16."""
    fake_module = mock.MagicMock()
    fake_module.SentenceTransformer.side_effect = RuntimeError(
        "fake load failure"
    )
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    b = eb.EmbeddingBackend(model_id="my-org/never-exists", dtype="bf16")
    with pytest.raises(eb.EmbeddingBackendError) as excinfo:
        b._load()
    # Either the requested dtype or the resolved label surfaces in
    # the message — both name the dtype context the operator needs.
    msg = str(excinfo.value)
    assert "bf16" in msg


@_skip_no_torch
def test_load_falls_back_to_caller_device_when_model_has_no_parameters(
    monkeypatch: pytest.MonkeyPatch,
):
    """Some stub models in tests have no ``parameters()`` iterator.
    The device-probe path catches ``StopIteration`` /
    ``AttributeError`` and falls back to ``self.device``. Pins the
    graceful-degradation contract so test-stub use doesn't break
    the identifier block."""

    class _StubST:
        def __init__(self, *a, **kw):
            pass
        # No parameters method, no parameters iterator.

    fake_module = mock.MagicMock()
    fake_module.SentenceTransformer = _StubST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    b = eb.EmbeddingBackend(model_id="mxbai", device="cuda:7")
    b._load()
    # Without a parameters iterator, the resolved device falls back
    # to whatever the caller declared.
    block = b.identifier_block()
    assert block["device_loaded"] == "cuda:7"
