#!/usr/bin/env python3
"""surprisal_backend.py — pluggable causal-LM wrapper for SETEC.

Wraps `transformers` causal language models behind a thin abstraction
so SETEC's surprisal-based audits (the planned R12+1 surprisal signal
per `internal/SPEC_surprisal_signal.md`) can swap causal LMs without
touching call sites. The `internal/SPEC_surprisal_model_choice.md`
decision registers nine core candidate LMs (GPT-2 small, Llama 3.2 1B,
Llama 3.2 3B, OLMo 2 1B, OpenELM 1.1B, Qwen 2.5 1.5B, Qwen 3 1.7B Base,
SmolLM2 1.7B, TinyLlama 1.1B) with a no-priority posture; the §5.4
fixture test decides which is the user's CLI default, subject to the
constraint that the operational default must come from the pre-mid-2024
training-cutoff bucket. Revised 2026-05-15 per
`SPEC_surprisal_model_choice_UPDATE_2026-05-15.md`; the original
five-candidate set is documented in the spec's 2026-05-11 decision-log
entry. Phi-3 Mini was dropped in the 2026-05-15 revision per the
spec's §3.7 base-only posture.

Design goals (mirror `embedding_backend.py`):

  * **Minimal surface area.** Three public symbols: the
    `SurprisalBackend` dataclass, the `MODEL_ALIASES` table, and
    `resolve_model_arg`. Tools call `.score_text(text)` and read
    `.identifier_block()` for PROVENANCE output.
  * **Lazy load.** The causal LM loads on first `score_text` call,
    not at construction. Matters more here than for embeddings —
    Phi-3 Mini is ~7.6 GB and shouldn't download just to print
    `--help`.
  * **Honest failure.** Missing `transformers` raises a clear
    `SurprisalBackendError` rather than silently degrading. Callers
    that want fallback behavior own that decision explicitly.
  * **Deterministic mode by default.** Per
    `SPEC_surprisal_signal.md` §3.4, batch-size determinism is a
    load-bearing property. The wrapper sets
    ``torch.use_deterministic_algorithms(True, warn_only=True)`` on
    first load.
  * **Teacher-forcing.** Per `SPEC_surprisal_signal.md` §2.1, the
    surprisal series is computed via teacher-forcing (full sequence
    in, logits at every position out, single forward pass). This is
    N× cheaper than per-position autoregressive scoring.

The module does NOT manage:

  * Multi-GPU placement (single-device only; the calibration host
    runs one GPU per the spec).
  * Chunking documents that exceed the model's context window —
    callers do that. See `SPEC_surprisal_signal.md` §3.3 for the
    chunking contract.
  * Records-cache redistribution. Per the "Stylometry to the
    people" policy, per-document surprisal series stay local.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Candidate aliases per `internal/SPEC_surprisal_model_choice.md` §4.1
# (no priority designated; §5.4 fixture test decides the user's CLI
# default). Revised 2026-05-15 per the verification pass landing in
# `SPEC_surprisal_model_choice_UPDATE_2026-05-15.md`. Listed
# alphabetically by alias key; the order is not a ranking. The
# bracketed tag at the end of each line is the training-cutoff
# bucket per spec §3.8 (used by §5.4's per-bucket reporting).
#
# - `gpt2`: OpenAI GPT-2 small (124M, MIT). Archived 2019, no longer
#   changing; oldest training data in the set (2017 cutoff) and
#   diagnostically the contamination-cleanest comparator. [pre-mid-2024]
# - `llama32_1b`: Meta Llama 3.2 1B (1.23B, Llama 3.2 Community
#   License). Custom license has redistribution + acceptable-use
#   clauses. December 2023 training cutoff. [pre-mid-2024]
# - `llama32_3b`: Meta Llama 3.2 3B (3.21B, Llama 3.2 Community
#   License). Same family, same training data, same architecture as
#   `llama32_1b` with more capacity. Within-family parameter scan.
#   [pre-mid-2024]
# - `olmo2_1b`: AI2 OLMo 2 1B (1B, Apache 2.0). Openly-published
#   training corpus (OLMo-mix-1124, ~4T tokens, downloadable);
#   documented December 2023 cutoff. The only candidate where
#   PROVENANCE can audit the input corpus directly. [pre-mid-2024]
# - `openelm_1b`: Apple OpenELM 1.1B (1.1B, apple-amlr). Documented
#   pre-mid-2024 training corpus (RefinedWeb + Pile + RedPajama +
#   Dolma v1.6, ~1.8T tokens). Apple Sample Code License is
#   permissive but not OSI-certified. [pre-mid-2024]
# - `qwen25_1_5b`: Alibaba Qwen 2.5 1.5B (1.54B, Apache 2.0).
#   Multilingual (29 languages). Training cutoff not documented;
#   presumed mid-2024 effective window. [boundary]
# - `qwen3_1_7b`: Alibaba Qwen 3 1.7B Base (1.7B, Apache 2.0).
#   Same-family successor to `qwen25_1_5b` with broader multilingual
#   coverage (119 languages). Training cutoff not documented; release
#   date (May 14 2025) implies post-mid-2024. [post-mid-2024]
# - `smollm2_1_7b`: HuggingFace SmolLM2 1.7B (1.7B, Apache 2.0).
#   English-only. Effective training cutoff bounded to April-June
#   2024 via FineWeb-Edu source snapshot dates available at SmolLM2
#   training time. [boundary]
# - `tinyllama`: TinyLlama 1.1B-intermediate-step-1431k-3T (1.1B,
#   Apache 2.0). Documented training cutoff (mid-2023); English-only;
#   smallest-footprint pre-cutoff candidate. [pre-mid-2024]
#
# Dropped 2026-05-15 (was in the original five): `phi3_mini`
# (Microsoft Phi-3 Mini 4K Instruct). Instruction-tuned; Microsoft
# confirmed no base variant is planned across the Phi family; violates
# spec §3.7 base-only posture. Operators who pinned `phi3_mini` get a
# typed `SurprisalBackendError` with migration guidance pointing at
# (a) the full HF id route for ad-hoc use, and (b) `qwen3_4b_base` as
# the recommended Apache-2.0 upper-bound base replacement (available
# via the full HF id route `Qwen/Qwen3-4B-Base`; not aliased because
# it is an optional comparator per spec §4.1, not a core-set member).
MODEL_ALIASES: dict[str, str] = {
    "gpt2": "openai-community/gpt2",
    "llama32_1b": "meta-llama/Llama-3.2-1B",
    "llama32_3b": "meta-llama/Llama-3.2-3B",
    "olmo2_1b": "allenai/OLMo-2-0425-1B",
    "openelm_1b": "apple/OpenELM-1_1B",
    "qwen25_1_5b": "Qwen/Qwen2.5-1.5B",
    "qwen3_1_7b": "Qwen/Qwen3-1.7B-Base",
    "smollm2_1_7b": "HuggingFaceTB/SmolLM2-1.7B",
    "tinyllama": "TinyLlama/TinyLlama-1.1B-intermediate-step-1431k-3T",
}

# Aliases removed in 2026-05-15. Kept in a separate dict so the
# constructor can recognise an operator's pinned legacy alias and
# raise `SurprisalBackendError` with migration guidance, rather than
# passing the alias through as a bogus HF id and producing a confusing
# weight-download error. Value is the migration message body.
DEPRECATED_ALIASES: dict[str, str] = {
    "phi3_mini": (
        "Microsoft Phi-3 Mini was dropped from the candidate set in "
        "2026-05-15. It is instruction-tuned, and Microsoft has "
        "confirmed no base variant of any Phi family member will be "
        "published. Per `SPEC_surprisal_model_choice.md` §3.7, only "
        "pretrained base models are eligible candidates. Migration "
        "options: (a) pass the full HF id directly via "
        "`--surprisal-model microsoft/Phi-3-mini-4k-instruct` if you "
        "have a calibration that requires the original model "
        "(deprecated, instruct-tuned); (b) use the recommended "
        "Apache-2.0 upper-bound base replacement "
        "`--surprisal-model Qwen/Qwen3-4B-Base`; (c) drop to one of "
        "the core-set aliases (gpt2 / llama32_1b / llama32_3b / "
        "olmo2_1b / openelm_1b / qwen25_1_5b / qwen3_1_7b / "
        "smollm2_1_7b / tinyllama)."
    ),
}

# Default when no `--model` is passed. `tinyllama` chosen as the
# documented-training-cutoff + small-footprint default for users
# who haven't run the §5.4 fixture suite. NOT a recommendation
# that tinyllama is best — only that it has the lowest contamination
# concern among the candidates and the smallest footprint, and that
# it satisfies the §5.4 decision-rule constraint of "operational
# CLI default must come from the pre-mid-2024 bucket." The §5.4
# fixture test on the user's register mix is the load-bearing
# decision; this default is the conservative pick in its absence.
DEFAULT_MODEL: str = "tinyllama"


class SurprisalBackendError(RuntimeError):
    """Raised when the surprisal backend cannot be loaded or used.

    Typed exception so callers can catch surprisal-specific failures
    separately from generic runtime errors — e.g., to fall back
    gracefully in audits where surprisal coverage is optional, or
    to report cleanly when the user is missing the Tier-4 dependency
    install.
    """


# ----------------------------------------------------------------- Dtype

VALID_DTYPES: tuple[str, ...] = ("auto", "fp32", "fp16", "bf16")


def _resolve_dtype(
    requested: str,
    *,
    cuda_available: bool | None = None,
    bf16_supported: bool | None = None,
) -> tuple[Any, str]:
    """Resolve a user-facing dtype string to a (torch.dtype, label).

    ``auto`` picks bf16 on cuda devices that support it (Ampere /
    Hopper / Ada), fp16 on older cuda where bf16 forward passes fall
    back to slow kernels (V100 / T4), and fp32 elsewhere (CPU / MPS;
    bf16/fp16 inference is not a throughput win on those backends).

    The probe defaults to live ``torch.cuda`` queries but accepts
    overrides for testability — the resolution logic is pure-Python
    and can be exercised without torch installed by passing the two
    booleans directly.

    Returns ``(torch_dtype, canonical_label)``. The label is one of
    ``"fp32" / "fp16" / "bf16"`` — never ``"auto"`` (the auto sentinel
    is consumed at resolution time so the provenance block records
    what the backend actually loaded, not the user's request).
    """
    if requested not in VALID_DTYPES:
        raise SurprisalBackendError(
            f"Invalid surprisal-backend dtype {requested!r}; "
            f"must be one of {VALID_DTYPES}."
        )
    try:
        import torch  # type: ignore
    except ImportError as exc:
        raise SurprisalBackendError(
            "torch not installed; cannot resolve surprisal-backend "
            "dtype. Install with: pip install -r "
            "requirements-surprisal.txt"
        ) from exc
    if cuda_available is None:
        cuda_available = bool(torch.cuda.is_available())
    if bf16_supported is None:
        bf16_supported = bool(
            cuda_available
            and hasattr(torch.cuda, "is_bf16_supported")
            and torch.cuda.is_bf16_supported()
        )
    if requested == "auto":
        if cuda_available and bf16_supported:
            return torch.bfloat16, "bf16"
        if cuda_available:
            return torch.float16, "fp16"
        return torch.float32, "fp32"
    if requested == "bf16":
        return torch.bfloat16, "bf16"
    if requested == "fp16":
        return torch.float16, "fp16"
    return torch.float32, "fp32"


@dataclass
class SurprisalBackend:
    """Pluggable wrapper around a transformers causal language model.

    ``model_id`` accepts either a `MODEL_ALIASES` key (e.g.,
    ``"tinyllama"``) or a full HuggingFace identifier (e.g.,
    ``"TinyLlama/TinyLlama-1.1B-intermediate-step-1431k-3T"``).
    Aliases are resolved in ``__post_init__`` so callers always see
    the full id in ``self.model_id`` and in the PROVENANCE block.

    ``revision`` pins a specific HuggingFace commit SHA. PROVENANCE
    discipline requires that every load-bearing audit record the
    revision; tools that don't pin a revision get a ``revision: null``
    field in their PROVENANCE block.
    """

    model_id: str
    revision: str | None = None
    deterministic: bool = True
    dtype: str = "auto"
    _model: Any = field(default=None, repr=False, init=False, compare=False)
    _tokenizer: Any = field(default=None, repr=False, init=False, compare=False)
    _alias: str | None = field(default=None, repr=False, init=False, compare=False)
    _device: Any = field(default=None, repr=False, init=False, compare=False)
    _resolved_dtype_label: str | None = field(
        default=None, repr=False, init=False, compare=False,
    )

    def __post_init__(self) -> None:
        # Deprecation gate (added 2026-05-15). Operators who pinned a
        # removed alias get a typed error with migration guidance
        # rather than a downstream HF-id-not-found failure. The full
        # HF id route still works for operators who need the underlying
        # model for ad-hoc comparison; only the alias indirection is
        # gone.
        if self.model_id in DEPRECATED_ALIASES:
            raise SurprisalBackendError(
                f"Alias {self.model_id!r} was removed in 2026-05-15. "
                + DEPRECATED_ALIASES[self.model_id]
            )
        if self.dtype not in VALID_DTYPES:
            raise SurprisalBackendError(
                f"Invalid surprisal-backend dtype {self.dtype!r}; "
                f"must be one of {VALID_DTYPES}."
            )
        # Resolve alias → full id once at construction.
        if self.model_id in MODEL_ALIASES:
            self._alias = self.model_id
            self.model_id = MODEL_ALIASES[self.model_id]
        else:
            # Reverse lookup for identifier_block() reporting.
            self._alias = next(
                (alias for alias, full in MODEL_ALIASES.items()
                 if full == self.model_id),
                None,
            )

    def _load(self) -> tuple[Any, Any]:
        """Load the causal LM + tokenizer on demand.

        Cached on ``self._model`` and ``self._tokenizer``. The first
        call pays the weight-load cost (500 MB to 7.6 GB depending
        on candidate); subsequent calls reuse. Raises
        ``SurprisalBackendError`` on any failure with a message
        naming the failure mode.
        """
        if self._model is not None and self._tokenizer is not None:
            return self._model, self._tokenizer
        try:
            from transformers import (  # type: ignore
                AutoModelForCausalLM, AutoTokenizer,
            )
        except ImportError as exc:
            raise SurprisalBackendError(
                "transformers is not installed. "
                "Install with: pip install -r requirements-surprisal.txt "
                "(opt-in Tier-4 / surprisal dependency layer; the file "
                "documents how to pick the right torch wheel for your "
                "accelerator — ROCm / CUDA / MPS / CPU-only). For the "
                "full decision tree, per-path install steps, smoke "
                "test, and fallback ladder see "
                "scripts/calibration/RUNBOOK_tier4_install.md."
            ) from exc
        try:
            kwargs: dict[str, Any] = {}
            if self.revision:
                kwargs["revision"] = self.revision
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_id, **kwargs,
            )
            # Resolve dtype + load model in the resolved precision. ``auto``
            # picks bf16 on supporting cuda (Ampere+), fp16 on older cuda
            # (V100/T4), fp32 on CPU/MPS. The resolved label is recorded
            # so identifier_block surfaces the loaded precision rather
            # than the user's request — operators reading audit JSON see
            # ``dtype: bf16`` not ``dtype: auto``.
            resolved_torch_dtype, self._resolved_dtype_label = _resolve_dtype(
                self.dtype,
            )
            model_kwargs = dict(kwargs)
            model_kwargs["torch_dtype"] = resolved_torch_dtype
            self._model = AutoModelForCausalLM.from_pretrained(
                self.model_id, **model_kwargs,
            )
            # Causal LMs in eval mode — no dropout, no gradient
            # accumulation. Surprisal scoring is inference-only.
            self._model.eval()
        except SurprisalBackendError:
            raise
        except Exception as exc:
            raise SurprisalBackendError(
                f"Failed to load causal LM {self.model_id!r}"
                + (f" at revision {self.revision!r}" if self.revision else "")
                + f" (dtype={self.dtype!r}): "
                + f"{type(exc).__name__}: {exc}"
            ) from exc
        # Device placement. The prior v1.59.x backend called
        # ``model(input_ids)`` without moving the model or inputs to
        # an accelerator, so a CUDA / MPS / ROCm host that had the
        # right torch wheel still ran the forward pass on CPU. That
        # turns an H100 rental into a glorified CPU at ~30-80 tok/s
        # and is the load-bearing reason Tier-4 calibration was
        # multi-day on rented GPUs. The auto-detect prefers CUDA
        # (which the ROCm wheel also shims) over MPS over CPU. A
        # caller that wants a specific device can override by
        # assigning ``backend._device`` after construction; the
        # batched-scoring path reads ``self._device`` rather than
        # re-detecting.
        #
        # Failure-mode discipline: a missing torch install or a
        # stub model that doesn't implement ``.to()`` is a soft
        # case (test fakes, CI hosts without the Tier-4 dependency
        # layer) — leave ``_device = None`` and let the scoring path
        # skip the device move. But a real placement error (OOM,
        # driver mismatch, unsupported-dtype-on-MPS, etc.) is the
        # exact thing this patch exists to surface, because the
        # silent CPU fallback that those errors used to produce is
        # the multi-day-rented-GPU bug. Surface real placement
        # failures as ``SurprisalBackendError``.
        try:
            import torch  # type: ignore
        except ImportError:
            self._device = None
        else:
            if torch.cuda.is_available():
                self._device = torch.device("cuda")
            elif (
                hasattr(torch.backends, "mps")
                and torch.backends.mps.is_available()
            ):
                self._device = torch.device("mps")
            else:
                self._device = torch.device("cpu")
            try:
                self._model = self._model.to(self._device)
            except AttributeError:
                # Stub model (test fake) without ``.to()``. The
                # scoring path will skip the device move and the
                # forward pass runs on whatever device the stub
                # implements internally.
                self._device = None
            except Exception as exc:  # noqa: BLE001
                raise SurprisalBackendError(
                    f"Failed to move model {self.model_id!r} to "
                    f"{self._device}: {type(exc).__name__}: {exc}. "
                    "This usually indicates an OOM, driver mismatch, "
                    "or unsupported-op placement error. The backend "
                    "refuses to silently fall back to CPU here — a "
                    "CPU-only Tier-4 run on RAID-scale data is a "
                    "multi-day operation on rented GPU hours, which "
                    "is the exact failure mode auto-device-placement "
                    "is meant to prevent."
                ) from exc
        # Pad token. Many causal LMs (GPT-2, OLMo, OpenELM, etc.) ship
        # without a pad_token set, which breaks batched tokenization
        # with ``padding=True``. Standard practice is to alias
        # ``pad_token`` to ``eos_token``; the attention_mask the
        # tokenizer emits keeps the forward pass numerically
        # equivalent to the unpadded case for every non-pad position.
        # Also force right padding: ``score_texts`` assumes pad tokens
        # live on the right edge of each row so the valid-mask
        # convention (``attention_mask[:, :-1] & attention_mask[:, 1:]``)
        # filters out positions predicted from pad-context. Some HF
        # causal-LM tokenizers default to left padding for generation;
        # we override defensively here so the batched-scoring contract
        # holds regardless of the tokenizer's preset.
        try:
            if (
                getattr(self._tokenizer, "pad_token", None) is None
                and getattr(self._tokenizer, "eos_token", None) is not None
            ):
                self._tokenizer.pad_token = self._tokenizer.eos_token
            if hasattr(self._tokenizer, "padding_side"):
                self._tokenizer.padding_side = "right"
        except Exception:  # noqa: BLE001
            pass
        if self.deterministic:
            try:
                import torch  # type: ignore
                torch.use_deterministic_algorithms(True, warn_only=True)
            except Exception:  # noqa: BLE001
                pass
        return self._model, self._tokenizer

    def score_text(
        self, text: str, *, return_top_k: int = 0,
    ) -> list[float] | tuple[list[float], list[dict[str, Any]]]:
        """Compute the per-token surprisal series for ``text``.

        Returns a list of floats in **bits** (log base 2), one per
        token position 2 through N (position 1 has no left context).
        Series length is ``len(tokens) - 1``.

        When ``return_top_k > 0``, returns a tuple
        ``(series, top_k_tokens)`` where ``top_k_tokens`` is a list
        of the k most-surprising tokens with their positions and
        decoded text — a reader-facing diagnostic.

        The math (per `SPEC_surprisal_signal.md` §2.1):

          1. Tokenize text → tokens.
          2. Run the model on the full sequence (teacher-forcing) →
             logits at every position.
          3. For each position i from 1 to N-1, compute
             ``-log_2(softmax(logits[i])[tokens[i+1]])``. That's the
             surprisal of the (i+1)-th token given the prefix.

        Empty / single-token inputs return an empty series.
        """
        if not text.strip():
            return [] if return_top_k == 0 else ([], [])
        model, tokenizer = self._load()
        import torch  # type: ignore
        encoded = tokenizer(text, return_tensors="pt")
        input_ids = encoded["input_ids"]
        if input_ids.shape[1] < 2:
            return [] if return_top_k == 0 else ([], [])
        if self._device is not None:
            input_ids = input_ids.to(self._device)

        # Over-context chunking (originally PR #97, 2026-05-18).
        # Feeding ``model(input_ids)`` with
        # ``input_ids.shape[1] > model.config.max_position_embeddings``
        # indexes the positional embedding table out of range. On
        # native-CUDA Linux you get a clean ``IndexError``. On
        # WSL+ROCm the GPU kernel spins indefinitely — no Python
        # exception, no progress, 100% utilization — and after a few
        # minutes a Windows host event reaps the WSL VM. Four
        # reproducible host bounces traced to this path on 2026-05-18.
        #
        # Fix: slice ``input_ids`` into non-overlapping chunks of
        # ``max_len`` tokens, score each chunk independently, and
        # concatenate the per-token surprisal series. Each chunk
        # forfeits its first position (no left context), so a fully
        # chunked sequence loses ``num_chunks`` positions out of N
        # rather than just 1 — a negligible artifact at MAGE/RAID
        # scale for distributional signals (mean, sd, acf). A future
        # refinement could prepend a warm-up window from the prior
        # chunk's tail and discard those positions from the scored
        # series, but that's only worth doing if the boundary bias
        # measurably affects a downstream gate.
        cfg = model.config
        max_len = (
            getattr(cfg, "max_position_embeddings", None)
            or getattr(cfg, "n_positions", None)
            or getattr(cfg, "n_ctx", None)
            or 1024
        )

        # Log-softmax for numerical stability, then negate and convert
        # from nats (natural log) to bits (log base 2). Position i's
        # logits predict token i+1; gather surprisal of the actual
        # next token at each position.
        #
        # Upcast logits to fp32 before log_softmax. For bf16 this is a
        # no-op precision-wise (bf16 has fp32-like exponent range, so
        # log_softmax is well-behaved in bf16 too). For fp16 the LM
        # head can produce logits that overflow softmax normalisation
        # — upcasting prevents that. The cost is a single dtype cast
        # per forward pass; the surprisal series is the load-bearing
        # output and computing it in fp32 keeps the framework's
        # numerical contract stable across dtype choices.
        import math
        log2e = 1.0 / math.log(2.0)
        all_surprisals_bits: list[float] = []
        all_next_token_ids: list[int] = []

        total_len = input_ids.shape[1]
        chunk_starts = (
            [0] if total_len <= max_len
            else list(range(0, total_len, max_len))
        )
        for start in chunk_starts:
            end = min(start + max_len, total_len)
            chunk_ids = input_ids[:, start:end]
            if chunk_ids.shape[1] < 2:
                continue
            with torch.no_grad():
                outputs = model(chunk_ids)
            logits = outputs.logits  # (1, n_chunk, vocab)
            log_probs_nats = torch.log_softmax(
                logits[0, :-1, :].float(), dim=-1,
            )
            # next_tokens[i] = the actual token at position i+1 in chunk_ids
            next_tokens = chunk_ids[0, 1:]
            surprisals_nats = -log_probs_nats.gather(
                -1, next_tokens.unsqueeze(-1),
            ).squeeze(-1)
            all_surprisals_bits.extend(
                (surprisals_nats * log2e).tolist()
            )
            all_next_token_ids.extend(next_tokens.tolist())

        if return_top_k <= 0:
            return all_surprisals_bits
        # Top-k diagnostic: most-surprising tokens with decoded text.
        # Positions are 1-indexed within the concatenated chunk series.
        # For inputs that were chunked, the position number won't
        # correspond directly to token offsets in the original
        # input_ids — each chunk boundary skips a position (the
        # chunk's position 0, which has no left context).
        indexed = sorted(
            range(len(all_surprisals_bits)),
            key=lambda i: all_surprisals_bits[i],
            reverse=True,
        )[:return_top_k]
        top_k = [
            {
                "position": i + 1,
                "token_id": all_next_token_ids[i],
                "token_text": tokenizer.decode([all_next_token_ids[i]]),
                "surprisal_bits": all_surprisals_bits[i],
            }
            for i in indexed
        ]
        return all_surprisals_bits, top_k

    def score_texts(
        self,
        texts: list[str],
        *,
        batch_size: int = 8,
    ) -> list[list[float]]:
        """Batched per-token surprisal scoring for a list of texts.

        Returns one bits-valued surprisal series per input text, in
        input order, with the same shape contract as ``score_text``
        (series length = ``len(tokens) - 1`` for non-empty inputs,
        empty list for empty / single-token inputs).

        Designed for the calibration-survey hot loop, which calls
        surprisal scoring N times per shard. At batch_size=1 this
        is equivalent to looping ``score_text``; at batch_size=8
        on a CUDA H100 the forward-pass throughput is roughly
        5–10× higher, which is what makes RAID-scale Tier-4
        calibration tractable on a single rented GPU hour.

        Padding semantics: texts are right-padded to the longest
        member of each chunk with the tokenizer's pad token (aliased
        to ``eos_token`` in ``_load`` when not otherwise set). The
        attention_mask passed to the model ensures non-pad positions
        attend only to non-pad left context, so per-position
        surprisals are numerically equivalent to the un-padded
        single-text path within FP32 tolerance. Padded positions
        are masked out of the returned series — the result length
        for each text matches its un-padded token count minus one.

        ``batch_size`` is the per-chunk batch size; larger values
        improve GPU utilisation but raise VRAM peak. The default
        of 8 is conservative for 1–2B-param candidates on a 24 GB
        L4. Bump to 16 or 32 on an A100/H100.

        Length-sorting (1.91.0+): texts are processed in ascending
        length order so length-similar texts batch together and
        padding waste collapses. On heterogeneous-length corpora
        (MAGE / RAID rows have a long tail) this recovers ~20-40%
        throughput beyond the naive same-order batching. Output
        order is preserved — ``results[i]`` always corresponds to
        ``texts[i]``. Stable sort: same-length texts retain their
        input order, so the empty / uniform-length cases are
        bit-exact no-ops vs. the un-sorted path.
        """
        if not texts:
            return []
        model, tokenizer = self._load()
        import torch  # type: ignore
        import math
        log2e = 1.0 / math.log(2.0)
        results: list[list[float]] = [[] for _ in texts]
        # Length-sort: stable ordering by character count (cheap
        # proxy for token count; correlation is >0.95 on natural
        # prose). The sort is O(N log N) and pays back many-fold
        # whenever the input length distribution has a long tail.
        # Python's ``sorted`` is stable, so determinism is
        # preserved for ties and for already-sorted inputs.
        ordered_indices = sorted(
            range(len(texts)),
            key=lambda i: len(texts[i]) if texts[i] else 0,
        )
        # Process in chunks of batch_size in length-sorted order.
        # Empty / whitespace-only texts get an empty series without
        # touching the model; non-empty texts in the same chunk go
        # through the batched forward pass together. ``chunk_indices``
        # stores the ORIGINAL position in ``texts`` so the final
        # write-back to ``results`` lands at the right slot.
        for chunk_start in range(0, len(ordered_indices), batch_size):
            chunk_indices: list[int] = []
            chunk_texts: list[str] = []
            for orig_idx in ordered_indices[
                chunk_start:chunk_start + batch_size
            ]:
                if texts[orig_idx].strip():
                    chunk_indices.append(orig_idx)
                    chunk_texts.append(texts[orig_idx])
            if not chunk_texts:
                continue
            encoded = tokenizer(
                chunk_texts,
                return_tensors="pt",
                padding=True,
                truncation=False,
            )
            input_ids = encoded["input_ids"]
            attention_mask = encoded.get("attention_mask")
            if attention_mask is None:
                # Fallback when the tokenizer doesn't return a mask
                # (some stubs in tests don't). Treat every position
                # as real; safe when chunk_texts has length 1 or all
                # entries tokenise to the same length.
                attention_mask = torch.ones_like(input_ids)
            if self._device is not None:
                input_ids = input_ids.to(self._device)
                attention_mask = attention_mask.to(self._device)
            with torch.no_grad():
                outputs = model(
                    input_ids,
                    attention_mask=attention_mask,
                )
            logits = outputs.logits  # (B, N, vocab)
            # Predicted-next-token surprisal: position i's logits
            # predict token i+1. Slice off the last position from
            # logits and the first position from input_ids to align.
            # Upcast logits to fp32 before log_softmax (see score_text
            # for the rationale: fp16 overflow safety + bf16 no-op).
            log_probs_nats = torch.log_softmax(
                logits[:, :-1, :].float(), dim=-1,
            )
            next_tokens = input_ids[:, 1:]  # (B, N-1)
            surprisals_nats = -log_probs_nats.gather(
                -1, next_tokens.unsqueeze(-1),
            ).squeeze(-1)  # (B, N-1)
            surprisals_bits = (surprisals_nats * log2e).cpu()
            # Valid positions require BOTH the context position
            # (i, predicting i+1) AND the target position (i+1) to
            # be real tokens. The naive ``attention_mask[:, 1:]``
            # mask filters target-side pads but admits the case
            # where the target is real but the left context is a
            # pad — which happens under left-padded tokenizers
            # (where shorter rows look like ``[PAD, PAD, A, B, C]``
            # and the position predicting ``A`` reads pad-context).
            # The paired AND is correct for both padding sides; we
            # also force ``padding_side = 'right'`` in ``_load`` for
            # defense in depth.
            valid_mask = (
                attention_mask[:, :-1].bool() & attention_mask[:, 1:].bool()
            ).cpu()
            for k, original_idx in enumerate(chunk_indices):
                if input_ids.shape[1] < 2:
                    # Single-token (or empty) row after tokenisation;
                    # no surprisal position. Leave the placeholder
                    # empty list in ``results``.
                    continue
                series = surprisals_bits[k][valid_mask[k]].tolist()
                results[original_idx] = series
        return results

    def identifier_block(self) -> dict[str, Any]:
        """Provenance block consumers paste into their JSON output.

        Mirrors `embedding_backend.EmbeddingBackend.identifier_block()`
        for shape consistency — readers parsing per-audit PROVENANCE
        see the same fields whether the backend is an embedding
        model or a causal LM.
        """
        return {
            "id": self.model_id,
            "revision": self.revision,
            "alias": self._alias,
            "deterministic_mode": self.deterministic,
            "method": "transformers-causal-lm",
            "dtype_requested": self.dtype,
            "dtype_loaded": self._resolved_dtype_label,
        }


def resolve_model_arg(arg: str | None) -> str:
    """Normalise a `--model` CLI argument to an alias or full id.

    Returns the original string if it's already in ``MODEL_ALIASES``
    or looks like a HuggingFace identifier. Returns ``DEFAULT_MODEL``
    for ``None``. Lets CLI argument parsers take either form:
    ``--model tinyllama`` or
    ``--model TinyLlama/TinyLlama-1.1B-intermediate-step-1431k-3T``.
    """
    if arg is None:
        return DEFAULT_MODEL
    return arg
