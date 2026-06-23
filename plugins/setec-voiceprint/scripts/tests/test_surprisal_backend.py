#!/usr/bin/env python3
"""Regression tests for surprisal_backend.py.

The module is a thin wrapper around transformers causal LMs; tests
pin the wrapper's contract without loading real model weights:

  * Alias resolution: the nine §4.1 core candidates per
    `SPEC_surprisal_model_choice.md` (revised 2026-05-15) map to the
    right HuggingFace identifiers.
  * Reverse alias detection: passing a full id matching a known
    alias surfaces the alias in the identifier block.
  * Deprecation gate: the removed `phi3_mini` alias raises
    `SurprisalBackendError` with migration guidance, rather than
    silently passing through as a bogus HF id.
  * Lazy load: instantiation does not load the model; `.score_text()`
    does.
  * Missing-package failure: when transformers is not importable,
    `.score_text()` raises `SurprisalBackendError` with an install
    hint, not a silent fallback.
  * Surprisal math: a stub-model fixture verifies the teacher-
    forcing + log-softmax + bits-conversion pipeline produces
    expected values.
  * Identifier block: returns the PROVENANCE-consumer shape.
  * Empty / single-token input: returns empty series without
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

import surprisal_backend as sb  # type: ignore

# Math tests need torch for tensor ops. Skip when torch isn't
# installed (the surprisal stack is opt-in Tier-4; CI environments
# without torch should still pass the wrapper-contract tests).
try:
    import torch  # type: ignore  # noqa: F401
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

_skip_no_torch = pytest.mark.skipif(
    not _HAS_TORCH,
    reason="torch not installed; skipping surprisal-math tests",
)


@pytest.fixture(autouse=True)
def _no_mps_autodetect(monkeypatch):
    """Make device auto-detect resolve to CPU on Apple-Silicon (MPS) hosts so
    these mocked-model math tests run identically to CI (Linux, CPU).

    ``SurprisalBackend._select_device`` picks MPS when
    ``torch.backends.mps.is_available()`` is true, but the fake model's logits
    aren't allocated on MPS, so the forward pass raises ``RuntimeError:
    Placeholder storage has not been allocated on MPS device!`` — the tests
    passed in CI (no MPS) but failed on a developer Mac. Disabling ONLY the MPS
    branch (never CUDA) keeps every device-precedence test intact: those set an
    explicit ``device`` / env var or mock ``torch.cuda.is_available`` — all
    higher precedence than the MPS branch — and the auto-detect contract test
    still exercises the real auto-detect path, which now lands on CPU."""
    if not _HAS_TORCH:
        return
    import torch  # type: ignore
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and hasattr(mps, "is_available"):
        monkeypatch.setattr(mps, "is_available", lambda: False)


# --------------- Alias resolution -------------------------------


def test_aliases_resolve_to_full_huggingface_ids():
    """All nine §4.1 core candidates per SPEC_surprisal_model_choice.md
    (revised 2026-05-15) must be in the alias table, each pointing at
    the canonical HF identifier."""
    assert sb.MODEL_ALIASES["gpt2"] == "openai-community/gpt2"
    assert sb.MODEL_ALIASES["llama32_1b"] == "meta-llama/Llama-3.2-1B"
    assert sb.MODEL_ALIASES["llama32_3b"] == "meta-llama/Llama-3.2-3B"
    assert sb.MODEL_ALIASES["olmo2_1b"] == "allenai/OLMo-2-0425-1B"
    assert sb.MODEL_ALIASES["openelm_1b"] == "apple/OpenELM-1_1B"
    assert sb.MODEL_ALIASES["qwen25_1_5b"] == "Qwen/Qwen2.5-1.5B"
    assert sb.MODEL_ALIASES["qwen3_1_7b"] == "Qwen/Qwen3-1.7B-Base"
    assert sb.MODEL_ALIASES["smollm2_1_7b"] == "HuggingFaceTB/SmolLM2-1.7B"
    assert "TinyLlama" in sb.MODEL_ALIASES["tinyllama"]


def test_alias_table_size():
    """Ten core candidates: the nine from the 2026-05-15 spec revision
    plus gpt2_medium, added in the Phase-B frontier scan. If this number
    changes, the spec's §4.1 candidate table and this test should change
    together."""
    assert len(sb.MODEL_ALIASES) == 10


def test_phi3_mini_removed_from_alias_table():
    """The 2026-05-15 spec revision dropped Phi-3 Mini per §3.7
    (base-only posture). The alias must not appear in the active
    table; pinning it raises an error per the deprecation gate test
    below."""
    assert "phi3_mini" not in sb.MODEL_ALIASES


def test_default_model_is_in_alias_table():
    """The CLI default must be one of the known aliases so that
    `resolve_model_arg(None)` produces a value that the
    `SurprisalBackend` constructor can resolve."""
    assert sb.DEFAULT_MODEL in sb.MODEL_ALIASES


def test_construction_with_alias_resolves_to_full_id():
    b = sb.SurprisalBackend(model_id="tinyllama")
    assert "TinyLlama" in b.model_id
    assert b._alias == "tinyllama"


def test_construction_with_full_id_finds_known_alias():
    """Reverse lookup: passing a full id should surface the
    matching alias for PROVENANCE-consumer grouping."""
    b = sb.SurprisalBackend(model_id="openai-community/gpt2")
    assert b.model_id == "openai-community/gpt2"
    assert b._alias == "gpt2"


def test_construction_with_unknown_id_passes_through():
    """An unknown HF identifier passes through unchanged; the alias
    is None so PROVENANCE consumers know there's no canonical alias."""
    b = sb.SurprisalBackend(model_id="my-org/my-causal-lm")
    assert b.model_id == "my-org/my-causal-lm"
    assert b._alias is None


# --------------- Deprecation gate (phi3_mini, 2026-05-15) -------


def test_phi3_mini_alias_raises_deprecation_error():
    """Pinning the removed `phi3_mini` alias raises
    `SurprisalBackendError` at construction time with the migration
    guidance message body, rather than silently passing through and
    failing later with a confusing HF-id-not-found error."""
    with pytest.raises(sb.SurprisalBackendError) as exc:
        sb.SurprisalBackend(model_id="phi3_mini")
    msg = str(exc.value)
    # Names the alias that was removed.
    assert "phi3_mini" in msg
    # Names the 2026-05-15 date so operators can find the spec revision.
    assert "2026-05-15" in msg
    # Names at least one of the migration paths (HF id pass-through,
    # Qwen 3 4B Base replacement, or core-set fallback).
    assert (
        "Qwen3-4B-Base" in msg
        or "microsoft/Phi-3-mini-4k-instruct" in msg
    )


def test_phi3_mini_full_huggingface_id_still_passes_through():
    """The deprecation gate only fires on the alias key. Operators
    who pass the full HF id directly still get a backend (the
    instruct-tuned model itself is still on HF; this is the documented
    migration path for operators with legacy calibrations)."""
    b = sb.SurprisalBackend(model_id="microsoft/Phi-3-mini-4k-instruct")
    assert b.model_id == "microsoft/Phi-3-mini-4k-instruct"
    assert b._alias is None  # No alias for this id post-2026-05-15.


def test_deprecated_aliases_table_is_populated():
    """The deprecation gate reads from `DEPRECATED_ALIASES`; the
    table must contain at least `phi3_mini` so the gate has a message
    to render."""
    assert "phi3_mini" in sb.DEPRECATED_ALIASES
    assert "base" in sb.DEPRECATED_ALIASES["phi3_mini"].lower()


# --------------- resolve_model_arg ------------------------------


def test_resolve_model_arg_none_returns_default():
    assert sb.resolve_model_arg(None) == sb.DEFAULT_MODEL


def test_resolve_model_arg_passes_through_known_alias():
    assert sb.resolve_model_arg("gpt2") == "gpt2"


def test_resolve_model_arg_passes_through_full_id():
    assert sb.resolve_model_arg("my-org/my-causal-lm") == "my-org/my-causal-lm"


# --------------- Lazy load --------------------------------------


def test_construction_does_not_load_model():
    """Instantiating a SurprisalBackend must not trigger a model
    download or load. Matters more than for embeddings: the heaviest
    candidate in the post-2026-05-15 set (Llama 3.2 3B ~6 GB; Qwen 3
    4B Base from the optional comparators is ~8 GB) shouldn't download
    just to print `--help`."""
    b = sb.SurprisalBackend(model_id="tinyllama")
    assert b._model is None
    assert b._tokenizer is None


# --------------- Missing-package handling -----------------------


def test_score_text_raises_when_transformers_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    """When transformers is not importable, `.score_text()` must
    raise `SurprisalBackendError` with a helpful install hint —
    not a silent fallback to zeros or to a different model."""
    real_import = (
        __builtins__["__import__"] if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )

    def _no_transformers(name, *args, **kwargs):
        if name == "transformers":
            raise ImportError("simulated: transformers not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _no_transformers)
    b = sb.SurprisalBackend(model_id="tinyllama")
    with pytest.raises(sb.SurprisalBackendError) as exc:
        b.score_text("test text for scoring")
    assert "transformers" in str(exc.value)
    assert "pip install" in str(exc.value)


def test_score_text_raises_when_model_load_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    """Model-load failures (network timeout, unknown id) bubble up
    as SurprisalBackendError so callers see a typed failure."""
    fake_transformers = mock.MagicMock()
    fake_transformers.AutoTokenizer.from_pretrained.side_effect = (
        RuntimeError("simulated load failure")
    )
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    b = sb.SurprisalBackend(model_id="not-a-real-model")
    b._model = None
    b._tokenizer = None
    with pytest.raises(sb.SurprisalBackendError) as exc:
        b.score_text("test text")
    assert "Failed to load causal LM" in str(exc.value)
    assert "simulated load failure" in str(exc.value)


# --------------- Dtype resolution (1.93.0) ----------------------


def test_dtype_default_is_auto():
    """Constructing without an explicit dtype yields ``"auto"``, the
    sentinel that resolves to bf16/fp16/fp32 at load time based on
    hardware. Pins the default so operators get the perf win without
    needing to pass --surprisal-dtype on every invocation."""
    b = sb.SurprisalBackend(model_id="tinyllama")
    assert b.dtype == "auto"


def test_dtype_invalid_raises_at_construction():
    """``__post_init__`` validates dtype against VALID_DTYPES so the
    typed failure happens at construction (cheap) rather than after
    the model finishes downloading (expensive)."""
    with pytest.raises(sb.SurprisalBackendError) as exc:
        sb.SurprisalBackend(model_id="tinyllama", dtype="float32")
    assert "Invalid surprisal-backend dtype" in str(exc.value)
    assert "'float32'" in str(exc.value)


def test_identifier_block_records_dtype_request_pre_load():
    """Before the model loads, identifier_block surfaces the dtype the
    operator requested (``dtype_requested``) but ``dtype_loaded`` is
    None — the resolved label is unknown until _resolve_dtype runs
    against actual hardware."""
    b = sb.SurprisalBackend(model_id="tinyllama", dtype="bf16")
    block = b.identifier_block()
    assert block["dtype_requested"] == "bf16"
    assert block["dtype_loaded"] is None


@_skip_no_torch
def test_resolve_dtype_auto_on_no_cuda_is_fp32():
    """Auto resolution on a CPU-only host returns fp32 — bf16/fp16
    inference is slower than fp32 on CPU, so the auto path defaults
    to the fast option for the available hardware."""
    import torch  # type: ignore
    dtype, label = sb._resolve_dtype(
        "auto", cuda_available=False, bf16_supported=False,
    )
    assert dtype == torch.float32
    assert label == "fp32"


@_skip_no_torch
def test_resolve_dtype_auto_on_bf16_cuda_is_bf16():
    """Auto on Ampere+/Hopper/Ada (bf16-supported cuda) returns bf16
    — the load-bearing perf path. Pins that a properly equipped host
    gets the ~1.7-2x throughput win without operator action."""
    import torch  # type: ignore
    dtype, label = sb._resolve_dtype(
        "auto", cuda_available=True, bf16_supported=True,
    )
    assert dtype == torch.bfloat16
    assert label == "bf16"


@_skip_no_torch
def test_resolve_dtype_auto_on_pre_ampere_cuda_is_fp16():
    """Auto on a cuda device that does NOT support bf16 (V100, T4)
    falls back to fp16 rather than fp32. bf16 on these devices
    triggers slow kernels; fp16 is the correct fast path. The
    log_softmax upcast in score_text / score_texts keeps numerical
    safety regardless of dtype."""
    import torch  # type: ignore
    dtype, label = sb._resolve_dtype(
        "auto", cuda_available=True, bf16_supported=False,
    )
    assert dtype == torch.float16
    assert label == "fp16"


@_skip_no_torch
def test_resolve_dtype_explicit_overrides_auto():
    """Explicit dtype strings always win over auto resolution. An
    operator can force fp32 on a bf16-capable host (e.g., to
    reproduce a pre-1.93 calibration bit-exactly) or force bf16 on
    no-cuda (e.g., to test serialization roundtrips)."""
    import torch  # type: ignore
    # fp32 forced on bf16-capable cuda.
    dtype, label = sb._resolve_dtype(
        "fp32", cuda_available=True, bf16_supported=True,
    )
    assert dtype == torch.float32 and label == "fp32"
    # bf16 forced with no cuda.
    dtype, label = sb._resolve_dtype(
        "bf16", cuda_available=False, bf16_supported=False,
    )
    assert dtype == torch.bfloat16 and label == "bf16"
    # fp16 forced.
    dtype, label = sb._resolve_dtype(
        "fp16", cuda_available=True, bf16_supported=True,
    )
    assert dtype == torch.float16 and label == "fp16"


@_skip_no_torch
def test_resolve_dtype_invalid_raises():
    """``_resolve_dtype`` rejects unknown strings even though the
    dataclass already validates at construction — defensive double-
    check for direct callers (tests, ad-hoc scripts) bypassing the
    SurprisalBackend constructor."""
    with pytest.raises(sb.SurprisalBackendError) as exc:
        sb._resolve_dtype("float16")  # wrong spelling
    assert "Invalid" in str(exc.value)


@_skip_no_torch
def test_load_passes_torch_dtype_to_from_pretrained(
    monkeypatch: pytest.MonkeyPatch,
):
    """The model load path forwards the resolved torch dtype to
    AutoModelForCausalLM.from_pretrained. Pins the wiring so a
    refactor that drops the kwarg silently regressing to fp32 will
    trip this test."""
    import torch  # type: ignore
    captured: dict = {}
    fake_transformers = mock.MagicMock()
    fake_model = mock.MagicMock()
    fake_model.to.return_value = fake_model
    fake_tokenizer = mock.MagicMock()
    fake_tokenizer.pad_token = None
    fake_tokenizer.eos_token = "<eos>"

    def _capture_model_call(model_id, **kwargs):
        captured["model_id"] = model_id
        captured["kwargs"] = kwargs
        return fake_model

    fake_transformers.AutoTokenizer.from_pretrained.return_value = (
        fake_tokenizer
    )
    fake_transformers.AutoModelForCausalLM.from_pretrained.side_effect = (
        _capture_model_call
    )
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)

    # Force the dtype path explicitly so the test doesn't depend on
    # the host machine's cuda availability.
    b = sb.SurprisalBackend(model_id="tinyllama", dtype="bf16")
    b._load()
    assert captured["kwargs"].get("torch_dtype") == torch.bfloat16
    assert b._resolved_dtype_label == "bf16"


@_skip_no_torch
def test_identifier_block_records_resolved_dtype_after_load(
    monkeypatch: pytest.MonkeyPatch,
):
    """After ``_load`` resolves dtype, identifier_block surfaces the
    resolved label in ``dtype_loaded``. Audit consumers reading the
    PROVENANCE block see ``"bf16"`` / ``"fp16"`` / ``"fp32"`` —
    never ``"auto"`` (the sentinel is consumed at load time)."""
    fake_transformers = mock.MagicMock()
    fake_transformers.AutoTokenizer.from_pretrained.return_value = (
        mock.MagicMock(pad_token=None, eos_token="<eos>")
    )
    fake_model = mock.MagicMock()
    fake_model.to.return_value = fake_model
    fake_transformers.AutoModelForCausalLM.from_pretrained.return_value = (
        fake_model
    )
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)

    b = sb.SurprisalBackend(model_id="tinyllama", dtype="fp32")
    b._load()
    block = b.identifier_block()
    assert block["dtype_requested"] == "fp32"
    assert block["dtype_loaded"] == "fp32"


# --------------- Device override --------------------------------


def _fake_transformers_with_model():
    """A transformers stand-in whose model records ``.to(device)``
    without loading real weights, for device-resolution tests."""
    fake = mock.MagicMock()
    fake.AutoTokenizer.from_pretrained.return_value = mock.MagicMock(
        pad_token=None, eos_token="<eos>",
    )
    fake_model = mock.MagicMock()
    fake_model.to.return_value = fake_model
    fake.AutoModelForCausalLM.from_pretrained.return_value = fake_model
    return fake


def test_default_device_is_none():
    """Default device is ``None`` so ``_load`` auto-detects
    (cuda > mps > cpu). Mirrors EmbeddingBackend's contract."""
    b = sb.SurprisalBackend(model_id="tinyllama")
    assert b.device is None


def test_device_field_is_stored():
    """An explicit device string is retained on the dataclass."""
    b = sb.SurprisalBackend(model_id="tinyllama", device="cuda:1")
    assert b.device == "cuda:1"


@_skip_no_torch
def test_device_field_overrides_autodetect(
    monkeypatch: pytest.MonkeyPatch,
):
    """An explicit ``device`` wins over the auto-detect. Uses the
    always-present ``meta`` device (no GPU required) so a pass proves
    the override fired rather than the cpu fallback."""
    monkeypatch.delenv("SETEC_SURPRISAL_DEVICE", raising=False)
    monkeypatch.setitem(
        sys.modules, "transformers", _fake_transformers_with_model(),
    )
    b = sb.SurprisalBackend(model_id="tinyllama", dtype="fp32", device="meta")
    b._load()
    assert str(b._device) == "meta"


@_skip_no_torch
def test_env_var_device_used_when_field_unset(
    monkeypatch: pytest.MonkeyPatch,
):
    """``SETEC_SURPRISAL_DEVICE`` is honored when no ``device`` field
    is set — the operator surface for the override until a
    ``--surprisal-device`` CLI flag is wired through."""
    monkeypatch.setenv("SETEC_SURPRISAL_DEVICE", "meta")
    monkeypatch.setitem(
        sys.modules, "transformers", _fake_transformers_with_model(),
    )
    b = sb.SurprisalBackend(model_id="tinyllama", dtype="fp32")
    b._load()
    assert str(b._device) == "meta"


@_skip_no_torch
def test_device_field_beats_env_var(
    monkeypatch: pytest.MonkeyPatch,
):
    """When both are set, the explicit ``device`` field takes
    precedence over the env var."""
    monkeypatch.setenv("SETEC_SURPRISAL_DEVICE", "cpu")
    monkeypatch.setitem(
        sys.modules, "transformers", _fake_transformers_with_model(),
    )
    b = sb.SurprisalBackend(model_id="tinyllama", dtype="fp32", device="meta")
    b._load()
    assert str(b._device) == "meta"


@_skip_no_torch
def test_cpu_override_on_cuda_host_loads_fp32(
    monkeypatch: pytest.MonkeyPatch,
):
    """[P2 regression] A cpu device override on a cuda host must yield
    fp32 under ``auto`` dtype. The target device is resolved *before*
    dtype selection, so the bf16/fp16 a bare ``torch.cuda.is_available()``
    probe would pick is never loaded-then-moved-to-CPU."""
    import torch  # type: ignore

    monkeypatch.delenv("SETEC_SURPRISAL_DEVICE", raising=False)
    # Simulate an Ampere+ CUDA host.
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(
        torch.cuda, "is_bf16_supported", lambda: True, raising=False,
    )
    fake = _fake_transformers_with_model()
    monkeypatch.setitem(sys.modules, "transformers", fake)

    b = sb.SurprisalBackend(model_id="tinyllama", dtype="auto", device="cpu")
    b._load()

    assert b._resolved_dtype_label == "fp32"
    kwargs = fake.AutoModelForCausalLM.from_pretrained.call_args.kwargs
    assert kwargs["torch_dtype"] == torch.float32
    assert str(b._device) == "cpu"


@_skip_no_torch
def test_auto_dtype_on_cuda_host_without_override_still_bf16(
    monkeypatch: pytest.MonkeyPatch,
):
    """Control for the fix above: with no device override, ``auto`` on a
    bf16-capable cuda host still loads bf16 — the device-first resolution
    only redirects explicit cpu/mps overrides, not the normal cuda path."""
    import torch  # type: ignore

    monkeypatch.delenv("SETEC_SURPRISAL_DEVICE", raising=False)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(
        torch.cuda, "is_bf16_supported", lambda: True, raising=False,
    )
    fake = _fake_transformers_with_model()
    monkeypatch.setitem(sys.modules, "transformers", fake)

    b = sb.SurprisalBackend(model_id="tinyllama", dtype="auto")
    b._load()

    assert b._resolved_dtype_label == "bf16"
    assert str(b._device) == "cuda"


# --------------- Empty / single-token input ---------------------


def test_score_text_empty_returns_empty_list():
    """Empty input bypasses model load; returns empty series."""
    b = sb.SurprisalBackend(model_id="tinyllama")
    out = b.score_text("")
    assert out == []
    assert b._model is None


def test_score_text_whitespace_only_returns_empty_list():
    b = sb.SurprisalBackend(model_id="tinyllama")
    assert b.score_text("   \n\t  ") == []


def test_score_text_empty_with_top_k_returns_empty_tuple():
    """When return_top_k > 0, empty input returns a (list, list) of
    empty lists rather than a bare empty list."""
    b = sb.SurprisalBackend(model_id="tinyllama")
    out = b.score_text("", return_top_k=10)
    assert out == ([], [])


# --------------- Surprisal math via stub -----------------------


class _FakeCausalLM:
    """Stand-in for `AutoModelForCausalLM.from_pretrained()` output.
    Returns deterministic synthetic logits per call.

    Accepts ``attention_mask`` as a keyword argument to match the
    batched-scoring path's call signature; the fake ignores the
    mask (uniform-logits default doesn't depend on attention)."""

    def __init__(
        self, n_positions: int, vocab_size: int, logits=None,
        max_position_embeddings: int | None = None,
    ):
        self.n_positions = n_positions
        self.vocab_size = vocab_size
        self._logits = logits
        # 1.96.0+ over-context chunking reads
        # ``model.config.max_position_embeddings`` to decide chunk
        # size. Default to the same n_positions value (so existing
        # tests where input length < n_positions take the single-
        # chunk path); override to a smaller value to exercise the
        # multi-chunk path.
        from types import SimpleNamespace
        self.config = SimpleNamespace(
            max_position_embeddings=(
                max_position_embeddings
                if max_position_embeddings is not None
                else n_positions
            ),
        )

    def eval(self):
        return self

    def to(self, device):  # noqa: ARG002
        return self

    def __call__(self, input_ids, attention_mask=None):  # noqa: ARG002
        import torch
        batch_size = input_ids.shape[0]
        n = input_ids.shape[1]
        if self._logits is not None:
            logits = self._logits
        else:
            # Default: uniform logits across the vocab. Every token
            # has equal probability 1/vocab_size; surprisal is
            # log2(vocab_size) bits per position.
            logits = torch.zeros((batch_size, n, self.vocab_size))
        out = mock.MagicMock()
        out.logits = logits
        return out


class _FakeTokenizer:
    """Returns deterministic token ids for tests.

    Accepts the ``padding`` / ``truncation`` keyword arguments used by
    the batched-scoring path. When called on a list of strings, returns
    a stacked tensor padded to the longest member with attention_mask
    flagging real-vs-pad positions."""

    pad_token = "<pad>"
    eos_token = "<eos>"

    def __init__(self, token_ids: list[int], pad_id: int = 99):
        self.token_ids = token_ids
        self.pad_id = pad_id

    def __call__(
        self,
        text,
        return_tensors=None,  # noqa: ARG002
        padding=False,  # noqa: ARG002
        truncation=False,  # noqa: ARG002
    ):
        import torch
        if isinstance(text, str):
            return {
                "input_ids": torch.tensor([self.token_ids]),
                "attention_mask": torch.ones((1, len(self.token_ids)), dtype=torch.long),
            }
        # Batched path: list of strings. Pad to the longest member.
        per_text = [self.token_ids for _ in text]
        max_len = max(len(ids) for ids in per_text)
        padded = [
            ids + [self.pad_id] * (max_len - len(ids))
            for ids in per_text
        ]
        attention = [
            [1] * len(ids) + [0] * (max_len - len(ids))
            for ids in per_text
        ]
        return {
            "input_ids": torch.tensor(padded),
            "attention_mask": torch.tensor(attention, dtype=torch.long),
        }

    def decode(self, token_ids):
        return f"<tok:{token_ids[0]}>"

    @classmethod
    def from_pretrained(cls, *args, **kwargs):  # noqa: ARG003
        return cls([0, 1, 2, 3, 4])


@_skip_no_torch
def test_score_text_uniform_logits_produce_expected_surprisal(
    monkeypatch: pytest.MonkeyPatch,
):
    """With uniform logits over a vocab of size V, every token's
    surprisal is log2(V) bits. The surprisal series for an N-token
    input has N-1 entries, all equal to log2(V)."""
    import math
    vocab_size = 8  # log2(8) = 3.0 bits per position
    fake = mock.MagicMock()
    fake.AutoTokenizer.from_pretrained.return_value = _FakeTokenizer(
        [0, 1, 2, 3, 4]
    )
    fake.AutoModelForCausalLM.from_pretrained.return_value = _FakeCausalLM(
        n_positions=5, vocab_size=vocab_size,
    )
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    series = b.score_text("five tokens here total xx")
    # 5 input tokens → 4 surprisal positions
    assert len(series) == 4
    expected = math.log2(vocab_size)
    for s in series:
        assert abs(s - expected) < 1e-5


@_skip_no_torch
def test_score_text_returns_top_k_when_requested(
    monkeypatch: pytest.MonkeyPatch,
):
    """The top_k diagnostic returns the k most-surprising tokens
    with position and decoded text. With uniform logits the choice
    is arbitrary but the shape contract must hold."""
    vocab_size = 8
    fake = mock.MagicMock()
    fake.AutoTokenizer.from_pretrained.return_value = _FakeTokenizer(
        [0, 1, 2, 3, 4]
    )
    fake.AutoModelForCausalLM.from_pretrained.return_value = _FakeCausalLM(
        n_positions=5, vocab_size=vocab_size,
    )
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    result = b.score_text("five tokens here total xx", return_top_k=3)
    assert isinstance(result, tuple)
    series, top_k = result
    assert len(series) == 4
    assert len(top_k) == 3
    for item in top_k:
        assert "position" in item
        assert "token_id" in item
        assert "token_text" in item
        assert "surprisal_bits" in item
        assert item["position"] >= 1


@_skip_no_torch
def test_score_text_single_token_returns_empty_series(
    monkeypatch: pytest.MonkeyPatch,
):
    """A one-token input has no position to predict — surprisal
    requires a context. The series must be empty."""
    fake = mock.MagicMock()
    fake.AutoTokenizer.from_pretrained.return_value = _FakeTokenizer(
        [42]
    )
    fake.AutoModelForCausalLM.from_pretrained.return_value = _FakeCausalLM(
        n_positions=1, vocab_size=8,
    )
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    series = b.score_text("hi")
    assert series == []


# --------------- Over-context chunking (originally PR #97) ------


@_skip_no_torch
def test_score_text_chunks_over_context_input(
    monkeypatch: pytest.MonkeyPatch,
):
    """When ``input_ids.shape[1] > model.config.max_position_embeddings``,
    score_text slices the input into non-overlapping chunks and
    concatenates the per-chunk surprisal series. Without chunking,
    feeding the full sequence to a model whose positional embedding
    table is smaller than the input indexes out of range — clean
    IndexError on native-CUDA Linux, indefinite GPU hang on
    WSL+ROCm (PR #97's original failure mode).

    For a 10-token input on a model with max_position_embeddings=4,
    we expect 3 chunks (lengths 4, 4, 2) with surprisal series of
    lengths 3, 3, 1 — total 7 positions, vs. the 9 a single forward
    pass would produce. The 2 lost positions are the first-position-
    of-chunk forfeitures (chunks 2 and 3; chunk 1's first position
    is the same as the input's first position, no loss there).
    """
    fake = mock.MagicMock()
    # Tokenize to 10 tokens → over-context for a 4-position model. Every id is
    # < vocab_size (8) so the next-token gather stays in bounds — a real
    # tokenizer never emits an id >= the model's vocab.
    fake.AutoTokenizer.from_pretrained.return_value = _FakeTokenizer(
        [0, 1, 2, 3, 4, 5, 6, 7, 0, 1]
    )
    fake.AutoModelForCausalLM.from_pretrained.return_value = _FakeCausalLM(
        n_positions=4, vocab_size=8,
        max_position_embeddings=4,
    )
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    series = b.score_text("ten tokens here")
    # 3 chunks: [0,1,2,3] (series len 3), [4,5,6,7] (series len 3),
    # [8,9] (series len 1). Total concatenated series length = 7.
    assert len(series) == 7
    # Uniform-logits fake → every position is log2(8) = 3 bits.
    import math
    expected = math.log2(8)
    for v in series:
        assert abs(v - expected) < 1e-5


@_skip_no_torch
def test_score_text_short_input_unchanged_under_chunking(
    monkeypatch: pytest.MonkeyPatch,
):
    """When ``input_ids.shape[1] <= max_position_embeddings``, the
    chunking path collapses to a single forward pass with no
    boundary forfeitures. Pins that the over-context fix doesn't
    regress the common short-input case."""
    fake = mock.MagicMock()
    # Tokenize to 5 ids; max_position_embeddings=8 → single chunk.
    fake.AutoTokenizer.from_pretrained.return_value = _FakeTokenizer(
        [0, 1, 2, 3, 4]
    )
    fake.AutoModelForCausalLM.from_pretrained.return_value = _FakeCausalLM(
        n_positions=5, vocab_size=8,
        max_position_embeddings=8,
    )
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    series = b.score_text("five tokens")
    # Single chunk: series length = n - 1 = 4.
    assert len(series) == 4


@_skip_no_torch
def test_score_text_chunking_falls_back_to_1024_when_config_lacks_max_position(
    monkeypatch: pytest.MonkeyPatch,
):
    """When the model's config carries none of
    ``max_position_embeddings`` / ``n_positions`` / ``n_ctx``
    (some HuggingFace configs use yet a different attribute name
    we don't catch), the chunker falls back to 1024 — a safe value
    that fits all the candidate models in the framework's alias
    table. Pins the fallback so a config edge case doesn't crash
    or silently produce a 1-chunk over-context call."""
    from types import SimpleNamespace
    fake = mock.MagicMock()
    fake.AutoTokenizer.from_pretrained.return_value = _FakeTokenizer(
        [0, 1, 2]
    )
    fake_model = _FakeCausalLM(n_positions=2048, vocab_size=8)
    # Strip the config attributes the chunker probes for.
    fake_model.config = SimpleNamespace()
    fake.AutoModelForCausalLM.from_pretrained.return_value = fake_model
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    # 3-token input is well under the 1024 fallback → single chunk,
    # series length 2.
    series = b.score_text("three")
    assert len(series) == 2


@_skip_no_torch
def test_score_text_top_k_positions_index_into_concatenated_series(
    monkeypatch: pytest.MonkeyPatch,
):
    """When chunking is active, top-k position indices are 1-indexed
    into the *concatenated* surprisal series (not into the original
    input_ids). Pins the documented behavior: the position number
    won't correspond directly to token offsets in the original
    sequence when chunking is in play."""
    import torch
    # Build per-position logits so each chunk has a unique surprisal
    # maximum that we can identify by position.
    vocab_size = 8
    # 8 tokens total; max_position_embeddings = 4 → 2 chunks.
    n = 8
    logits = torch.zeros((1, n, vocab_size))
    # Make position 3 (chunk 1's last) and position 7 (chunk 2's
    # last) carry highly negative logits for the actually-next token
    # so their gathered surprisals are large. We don't need exact
    # values — just non-uniform — so the top-k diagnostic surfaces
    # specific positions.

    fake_lm = _FakeCausalLM(
        n_positions=n, vocab_size=vocab_size,
        max_position_embeddings=4,
    )
    fake = mock.MagicMock()
    fake.AutoTokenizer.from_pretrained.return_value = _FakeTokenizer(
        list(range(n)),
    )
    fake.AutoModelForCausalLM.from_pretrained.return_value = fake_lm
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    series, top_k = b.score_text("eight tokens", return_top_k=3)
    # 2 chunks of len 4 → concatenated series len = (4-1) + (4-1) = 6.
    assert len(series) == 6
    # All returned positions are 1..len(series).
    for entry in top_k:
        assert 1 <= entry["position"] <= len(series)


# --------------- Batched scoring --------------------------------


@_skip_no_torch
def test_score_texts_returns_one_series_per_input(
    monkeypatch: pytest.MonkeyPatch,
):
    """``score_texts`` returns a list of surprisal series, one per
    input text, in input order, each non-empty input producing a
    series of length len(tokens) - 1."""
    import math
    vocab_size = 8
    fake = mock.MagicMock()
    fake.AutoTokenizer.from_pretrained.return_value = _FakeTokenizer(
        [0, 1, 2, 3, 4]
    )
    fake.AutoModelForCausalLM.from_pretrained.return_value = _FakeCausalLM(
        n_positions=5, vocab_size=vocab_size,
    )
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    results = b.score_texts(
        ["one", "two", "three"], batch_size=2,
    )
    assert len(results) == 3
    expected = math.log2(vocab_size)
    for series in results:
        assert len(series) == 4
        for s in series:
            assert abs(s - expected) < 1e-5


@_skip_no_torch
def test_score_texts_handles_empty_strings_without_loading(
    monkeypatch: pytest.MonkeyPatch,
):
    """Empty / whitespace-only inputs return empty series at their
    position in the result, without consuming forward-pass time
    inside the batch."""
    fake = mock.MagicMock()
    fake.AutoTokenizer.from_pretrained.return_value = _FakeTokenizer(
        [0, 1, 2, 3, 4]
    )
    fake.AutoModelForCausalLM.from_pretrained.return_value = _FakeCausalLM(
        n_positions=5, vocab_size=8,
    )
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    results = b.score_texts(["", "non-empty", "   \n\t  ", "another"])
    assert results[0] == []
    assert results[2] == []
    assert len(results[1]) == 4
    assert len(results[3]) == 4


@_skip_no_torch
def test_score_texts_empty_list_returns_empty_list():
    """An empty input list returns an empty result without touching
    the model. Construction-time guard before lazy-load."""
    b = sb.SurprisalBackend(model_id="tinyllama")
    assert b.score_texts([]) == []


@_skip_no_torch
def test_score_texts_raises_on_over_context_batch(
    monkeypatch: pytest.MonkeyPatch,
):
    """Preflight guard: when the padded batch length exceeds the
    model's ``max_position_embeddings``, ``score_texts`` raises
    ``SurprisalBackendError`` *before* the forward pass. Without
    this guard, feeding an over-context batch to the model on
    WSL+ROCm hangs the GPU kernel indefinitely (no Python
    exception → calibration loop's batch-failure latch never
    flips → operator never sees the warning, run wedges until
    the host reaps the WSL VM).

    The calibration loop catches the typed error, flips
    ``batched_surprisal_disabled = True``, and falls through to
    the per-entry ``score_text`` path which chunks over-context
    inputs safely. This test pins the typed-error contract; the
    fallback wiring is exercised in
    ``test_calibration_batched_surprisal``.
    """
    fake = mock.MagicMock()
    # 10-token texts on a 4-position model → padded batch length 10 > 4.
    fake.AutoTokenizer.from_pretrained.return_value = _FakeTokenizer(
        list(range(10))
    )
    fake.AutoModelForCausalLM.from_pretrained.return_value = _FakeCausalLM(
        n_positions=4, vocab_size=8,
        max_position_embeddings=4,
    )
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    with pytest.raises(sb.SurprisalBackendError) as excinfo:
        b.score_texts(["one", "two", "three"])
    # The message should name the over-context dimension so the
    # operator can correlate with the failing model and lower
    # --surprisal-batch-size (or re-run with batch-size 1, which
    # bypasses the batched path entirely).
    assert "max_position_embeddings" in str(excinfo.value)


@_skip_no_torch
def test_score_texts_short_batch_unchanged_under_preflight(
    monkeypatch: pytest.MonkeyPatch,
):
    """When the padded batch length is within
    ``max_position_embeddings``, the preflight is a no-op and
    batched scoring proceeds normally. Pins that the over-context
    guard doesn't regress the common in-context-batch path."""
    fake = mock.MagicMock()
    fake.AutoTokenizer.from_pretrained.return_value = _FakeTokenizer(
        [0, 1, 2, 3]
    )
    fake.AutoModelForCausalLM.from_pretrained.return_value = _FakeCausalLM(
        n_positions=4, vocab_size=8,
        max_position_embeddings=8,
    )
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    results = b.score_texts(["a", "b"])
    # Both texts tokenize to 4 ids → 3-element series each.
    assert len(results) == 2
    assert all(len(s) == 3 for s in results)


@_skip_no_torch
def test_score_texts_matches_score_text_for_each_input(
    monkeypatch: pytest.MonkeyPatch,
):
    """The batched path must produce the same per-text series as
    the single-text path within FP32 tolerance. This is the
    load-bearing 'batch-size determinism' property from
    SPEC_surprisal_signal.md §3.4 — at uniform logits the equality
    is exact; on a real model padded vs un-padded forward passes
    can differ by ~1e-5 but the test uses the deterministic fake
    so the equality holds tightly."""
    vocab_size = 8
    fake = mock.MagicMock()
    fake.AutoTokenizer.from_pretrained.return_value = _FakeTokenizer(
        [0, 1, 2, 3, 4]
    )
    fake.AutoModelForCausalLM.from_pretrained.return_value = _FakeCausalLM(
        n_positions=5, vocab_size=vocab_size,
    )
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    serial_series = b.score_text("hello world")
    batched_series = b.score_texts(["hello world"])
    assert len(batched_series) == 1
    assert len(batched_series[0]) == len(serial_series)
    for s_batched, s_serial in zip(batched_series[0], serial_series):
        assert abs(s_batched - s_serial) < 1e-5


class _CountingFakeCausalLM(_FakeCausalLM):
    """Variant of ``_FakeCausalLM`` that counts ``__call__``
    invocations at the class level. Necessary because Python
    resolves special methods on the class, not the instance —
    a per-instance ``fake_model.__call__ = wrapper`` assignment
    does NOT intercept ``fake_model(...)`` invocations."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.call_count = 0

    def __call__(self, input_ids, attention_mask=None):
        self.call_count += 1
        return super().__call__(input_ids, attention_mask=attention_mask)


@_skip_no_torch
def test_score_texts_respects_batch_size(
    monkeypatch: pytest.MonkeyPatch,
):
    """Asking for batch_size=2 across 5 inputs produces 3 forward
    passes (2 + 2 + 1). Uses a counting subclass with a class-level
    ``__call__`` override (per-instance assignment doesn't intercept
    invocation because Python resolves special methods on the class)
    so the assertion really pins the number of forward passes."""
    fake = mock.MagicMock()
    fake.AutoTokenizer.from_pretrained.return_value = _FakeTokenizer(
        [0, 1, 2, 3, 4]
    )
    fake_model = _CountingFakeCausalLM(n_positions=5, vocab_size=8)
    fake.AutoModelForCausalLM.from_pretrained.return_value = fake_model
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    results = b.score_texts(["a", "b", "c", "d", "e"], batch_size=2)
    assert len(results) == 5
    # Five non-empty inputs at batch_size=2 → ceil(5/2) = 3 batches.
    assert fake_model.call_count == 3


class _LeftPaddingFakeTokenizer(_FakeTokenizer):
    """Variant of ``_FakeTokenizer`` that pads on the LEFT side
    (the HF default for some causal-LM tokenizers in generation
    contexts). Pins the regression that batched scoring must not
    leak pad-position context into the surprisal of the first
    real token of a shorter row.

    The fake also exposes a writable ``padding_side`` attribute
    so ``_load()`` can flip it back to ``'right'``; subsequent
    calls observe the override."""

    padding_side: str = "left"

    def __call__(
        self,
        text,
        return_tensors=None,  # noqa: ARG002
        padding=False,  # noqa: ARG002
        truncation=False,  # noqa: ARG002
    ):
        import torch
        if isinstance(text, str):
            return {
                "input_ids": torch.tensor([self.token_ids]),
                "attention_mask": torch.ones((1, len(self.token_ids)), dtype=torch.long),
            }
        per_text = [self.token_ids for _ in text]
        max_len = max(len(ids) for ids in per_text)
        if self.padding_side == "left":
            padded = [
                [self.pad_id] * (max_len - len(ids)) + ids
                for ids in per_text
            ]
            attention = [
                [0] * (max_len - len(ids)) + [1] * len(ids)
                for ids in per_text
            ]
        else:  # 'right'
            padded = [
                ids + [self.pad_id] * (max_len - len(ids))
                for ids in per_text
            ]
            attention = [
                [1] * len(ids) + [0] * (max_len - len(ids))
                for ids in per_text
            ]
        return {
            "input_ids": torch.tensor(padded),
            "attention_mask": torch.tensor(attention, dtype=torch.long),
        }


@_skip_no_torch
def test_load_forces_right_padding_when_tokenizer_defaults_left(
    monkeypatch: pytest.MonkeyPatch,
):
    """Some HF causal-LM tokenizers default to ``padding_side =
    'left'`` for generation. The batched-scoring path assumes pad
    tokens live on the right edge of each row (so the valid-mask
    convention filters target-side pads cleanly). ``_load()``
    must flip the override before any batched tokenization."""
    fake = mock.MagicMock()
    tokenizer = _LeftPaddingFakeTokenizer([0, 1, 2, 3, 4])
    assert tokenizer.padding_side == "left"  # precondition
    fake.AutoTokenizer.from_pretrained.return_value = tokenizer
    fake.AutoModelForCausalLM.from_pretrained.return_value = _FakeCausalLM(
        n_positions=5, vocab_size=8,
    )
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    b.score_text("hello")  # drives lazy-load
    assert tokenizer.padding_side == "right"


@_skip_no_torch
def test_score_texts_correct_under_left_padding_tokenizer(
    monkeypatch: pytest.MonkeyPatch,
):
    """Even if a hypothetical tokenizer somehow ignored
    ``padding_side = 'right'`` and produced left-padded batches,
    the valid-mask convention
    ``attention_mask[:, :-1] & attention_mask[:, 1:]`` must still
    keep the per-text series equivalent to the single-text path.
    Defense in depth — covers the case where a custom tokenizer
    or a HF revision quietly re-pins left padding after load."""
    import math
    vocab_size = 8

    # A tokenizer whose ``padding_side`` is locked to 'left' and
    # ignores the ``_load`` override. Models the worst-case
    # custom-tokenizer regression.
    class _StubbornLeftPaddingFakeTokenizer(_LeftPaddingFakeTokenizer):
        @property
        def padding_side(self):
            return "left"

        @padding_side.setter
        def padding_side(self, value):  # noqa: ARG002
            pass  # Silently refuse to flip.

    fake = mock.MagicMock()
    fake.AutoTokenizer.from_pretrained.return_value = (
        _StubbornLeftPaddingFakeTokenizer([0, 1, 2, 3, 4])
    )
    fake.AutoModelForCausalLM.from_pretrained.return_value = _FakeCausalLM(
        n_positions=5, vocab_size=vocab_size,
    )
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    results = b.score_texts(["one", "two", "three"], batch_size=3)
    expected = math.log2(vocab_size)
    # Each input tokenises to 5 tokens; series length should be 4
    # (len(tokens) - 1) regardless of padding side, and every
    # value should equal log2(vocab_size) since the fake produces
    # uniform logits independent of context.
    for series in results:
        assert len(series) == 4
        for s in series:
            assert abs(s - expected) < 1e-5


@_skip_no_torch
def test_load_raises_on_real_device_placement_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    """Narrow-down regression for the silent-CPU-fallback issue.
    A model whose ``.to()`` raises something other than
    ``AttributeError`` (the stub-model signal) must surface as
    ``SurprisalBackendError`` rather than silently leaving the
    backend on CPU. Models the OOM / driver-mismatch /
    unsupported-dtype-on-MPS path that previously got swallowed
    by the broad ``except Exception``."""

    class _RaisingFakeCausalLM(_FakeCausalLM):
        def to(self, device):  # noqa: ARG002
            raise RuntimeError("CUDA out of memory (simulated)")

    fake = mock.MagicMock()
    fake.AutoTokenizer.from_pretrained.return_value = _FakeTokenizer(
        [0, 1, 2, 3, 4]
    )
    fake.AutoModelForCausalLM.from_pretrained.return_value = (
        _RaisingFakeCausalLM(n_positions=5, vocab_size=8)
    )
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    with pytest.raises(sb.SurprisalBackendError) as excinfo:
        b.score_text("hello")
    # Error message must name the original RuntimeError so the
    # operator can diagnose without re-running with debug flags.
    assert "CUDA out of memory" in str(excinfo.value)
    assert "RuntimeError" in str(excinfo.value)


@_skip_no_torch
def test_score_texts_assigns_device_when_torch_supports_it(
    monkeypatch: pytest.MonkeyPatch,
):
    """The device-auto-detect in ``_load`` should set ``_device`` to
    a torch.device after a successful load even when the fake model
    silently ignores ``.to()``. This pins the regression that
    motivated the patch: the v1.59.x backend left tensors on CPU
    even when CUDA was available."""
    fake = mock.MagicMock()
    fake.AutoTokenizer.from_pretrained.return_value = _FakeTokenizer(
        [0, 1, 2, 3, 4]
    )
    fake.AutoModelForCausalLM.from_pretrained.return_value = _FakeCausalLM(
        n_positions=5, vocab_size=8,
    )
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    # Drive lazy-load via a single-text scoring call.
    b.score_text("hello")
    # On a CI host without CUDA / MPS this lands on CPU; on a GPU
    # host it lands on cuda or mps. Either way the field must no
    # longer be None — the bug the patch fixes is that it was None.
    assert b._device is not None


# --------------- Length-sorted batching (1.91.0+) ---------------


class _ShapeRecordingFakeTokenizer(_FakeTokenizer):
    """Variant of ``_FakeTokenizer`` that emits token_ids whose
    length matches the input text's character count divided by 5
    (a rough proxy for real tokenizer behavior). Lets length-sorted
    batching tests reason about which texts end up in which chunk."""

    def __call__(
        self,
        text,
        return_tensors=None,  # noqa: ARG002
        padding=False,  # noqa: ARG002
        truncation=False,  # noqa: ARG002
    ):
        import torch
        if isinstance(text, str):
            n = max(2, len(text) // 5)
            ids = list(range(n))
            return {
                "input_ids": torch.tensor([ids]),
                "attention_mask": torch.ones((1, n), dtype=torch.long),
            }
        per_text = [list(range(max(2, len(t) // 5))) for t in text]
        max_len = max(len(ids) for ids in per_text)
        padded = [
            ids + [self.pad_id] * (max_len - len(ids))
            for ids in per_text
        ]
        attention = [
            [1] * len(ids) + [0] * (max_len - len(ids))
            for ids in per_text
        ]
        return {
            "input_ids": torch.tensor(padded),
            "attention_mask": torch.tensor(attention, dtype=torch.long),
        }


class _ShapeRecordingFakeCausalLM(_FakeCausalLM):
    """``_FakeCausalLM`` that records the shape of every forward
    pass's input_ids tensor. Lets length-sorted batching tests pin
    that consecutive forward passes have homogeneous lengths within
    each chunk (which is the throughput win the sort delivers)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.forward_pass_shapes: list[tuple[int, int]] = []

    def __call__(self, input_ids, attention_mask=None):
        self.forward_pass_shapes.append(
            (input_ids.shape[0], input_ids.shape[1])
        )
        return super().__call__(
            input_ids, attention_mask=attention_mask,
        )


@_skip_no_torch
def test_score_texts_preserves_input_order_after_length_sort(
    monkeypatch: pytest.MonkeyPatch,
):
    """Heterogeneous-length inputs are processed in length-sorted
    order internally, but the returned list must align with the
    input order — results[i] corresponds to texts[i]. Pin the
    contract: a long-short-long-short interleaved input must produce
    a result list whose series lengths follow the input lengths,
    not the sorted order."""
    fake = mock.MagicMock()
    fake.AutoTokenizer.from_pretrained.return_value = (
        _ShapeRecordingFakeTokenizer([0, 1, 2, 3, 4])
    )
    fake.AutoModelForCausalLM.from_pretrained.return_value = (
        # vocab must exceed the largest id the shape-recording tokenizer emits
        # (ids = range(len(text)//5)) — a real tokenizer never emits id >= vocab.
        _ShapeRecordingFakeCausalLM(n_positions=200, vocab_size=256)
    )
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    # Interleaved short / long inputs. Series length is roughly
    # len(text) // 5 - 1 under the shape-recording fake.
    inputs = ["short", "x" * 100, "med length text", "y" * 50]
    results = b.score_texts(inputs, batch_size=4)
    assert len(results) == 4
    # results[0] is the short input → short series
    # results[1] is the long input → long series
    # results[2] is the medium input → medium series
    # results[3] is the second long input → medium-long series
    assert len(results[0]) < len(results[2]) < len(results[3]) < len(results[1])


@_skip_no_torch
def test_score_texts_groups_length_similar_texts_in_forward_passes(
    monkeypatch: pytest.MonkeyPatch,
):
    """With length-sort enabled, consecutive forward passes should
    contain length-similar texts so the padded sequence length per
    pass is close to the longest member rather than the longest
    overall. Pin via a shape-recording fake model: after sorting,
    the shortest-text batch's padded length must be strictly less
    than the longest-text batch's padded length when the input has
    a long tail."""
    fake = mock.MagicMock()
    fake.AutoTokenizer.from_pretrained.return_value = (
        _ShapeRecordingFakeTokenizer([0, 1, 2, 3, 4])
    )
    shape_model = _ShapeRecordingFakeCausalLM(
        # vocab must exceed the largest tokenizer id (ids = range(len(text)//5)).
        n_positions=200, vocab_size=256,
    )
    fake.AutoModelForCausalLM.from_pretrained.return_value = shape_model
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    # 8 inputs: 4 short (~25 chars), 4 long (~250 chars). At
    # batch_size=4 the sort should produce two chunks: one all-
    # short, one all-long. The shape-recording fake exposes the
    # (batch, seq_len) of each forward pass; the short chunk's
    # seq_len must be smaller than the long chunk's.
    short_texts = ["short text " + str(i) for i in range(4)]
    long_texts = ["long " * 50 + str(i) for i in range(4)]
    inputs = []
    # Interleave so the un-sorted batching would have mixed
    # lengths and a single chunk's seq_len pinned to the longest.
    for s, l in zip(short_texts, long_texts):
        inputs.append(s)
        inputs.append(l)
    b.score_texts(inputs, batch_size=4)
    # 8 inputs at batch_size=4 → 2 forward passes.
    assert len(shape_model.forward_pass_shapes) == 2
    seq_lens = sorted(shape[1] for shape in shape_model.forward_pass_shapes)
    # The length-sorted batches must have strictly different
    # seq_lens; without length-sort, both batches would be padded
    # to the global max and seq_lens would be equal.
    assert seq_lens[0] < seq_lens[1], (
        f"Expected length-sorted batches to have different padded "
        f"seq_lens; got {seq_lens}. The sort is not separating "
        "short from long inputs across forward passes."
    )


@_skip_no_torch
def test_score_texts_homogeneous_lengths_no_op_under_sort(
    monkeypatch: pytest.MonkeyPatch,
):
    """When all inputs are the same length, the stable length-sort
    leaves them in input order. Pin that the no-op case isn't
    accidentally reordered (which would matter for determinism
    audits of the calibration pipeline)."""
    fake = mock.MagicMock()
    fake.AutoTokenizer.from_pretrained.return_value = (
        _ShapeRecordingFakeTokenizer([0, 1, 2, 3, 4])
    )
    fake.AutoModelForCausalLM.from_pretrained.return_value = (
        # vocab must exceed the largest id the shape-recording tokenizer emits
        # (ids = range(len(text)//5)) — a real tokenizer never emits id >= vocab.
        _ShapeRecordingFakeCausalLM(n_positions=200, vocab_size=256)
    )
    monkeypatch.setitem(sys.modules, "transformers", fake)
    b = sb.SurprisalBackend(model_id="tinyllama")
    # 6 inputs of identical length; with stable sort the input
    # order is preserved. Verify by comparing against the single-
    # text path's output for each input.
    inputs = [f"item-{i:02d}" for i in range(6)]
    batched = b.score_texts(inputs, batch_size=3)
    serial = [b.score_text(t) for t in inputs]
    assert len(batched) == 6
    for i in range(6):
        assert len(batched[i]) == len(serial[i])
        for a, e in zip(batched[i], serial[i]):
            assert abs(a - e) < 1e-5


# --------------- Identifier block -------------------------------


def test_identifier_block_shape():
    b = sb.SurprisalBackend(
        model_id="tinyllama", revision="sha-abc123", deterministic=True,
    )
    out = b.identifier_block()
    assert "TinyLlama" in out["id"]
    assert out["revision"] == "sha-abc123"
    assert out["alias"] == "tinyllama"
    assert out["deterministic_mode"] is True
    assert out["method"] == "transformers-causal-lm"


def test_identifier_block_unknown_id_reports_none_alias():
    b = sb.SurprisalBackend(model_id="my-org/my-causal-lm")
    out = b.identifier_block()
    assert out["alias"] is None
    assert out["id"] == "my-org/my-causal-lm"
