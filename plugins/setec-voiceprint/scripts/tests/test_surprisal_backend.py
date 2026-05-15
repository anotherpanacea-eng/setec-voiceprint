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
    """Nine core candidates as documented in the 2026-05-15 spec
    revision; if this number changes, the spec's §4.1 candidate
    table and this test should change together."""
    assert len(sb.MODEL_ALIASES) == 9


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
    Returns deterministic synthetic logits per call."""

    def __init__(self, n_positions: int, vocab_size: int, logits=None):
        self.n_positions = n_positions
        self.vocab_size = vocab_size
        self._logits = logits

    def eval(self):
        return self

    def __call__(self, input_ids):
        import torch
        n = input_ids.shape[1]
        if self._logits is not None:
            logits = self._logits
        else:
            # Default: uniform logits across the vocab. Every token
            # has equal probability 1/vocab_size; surprisal is
            # log2(vocab_size) bits per position.
            logits = torch.zeros((1, n, self.vocab_size))
        out = mock.MagicMock()
        out.logits = logits
        return out


class _FakeTokenizer:
    """Returns deterministic token ids for tests."""

    def __init__(self, token_ids: list[int]):
        self.token_ids = token_ids

    def __call__(self, text, return_tensors=None):
        import torch
        return {
            "input_ids": torch.tensor([self.token_ids]),
        }

    def decode(self, token_ids):
        return f"<tok:{token_ids[0]}>"

    @classmethod
    def from_pretrained(cls, *args, **kwargs):
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
