#!/usr/bin/env python3
"""surprisal_backend.py — pluggable causal-LM wrapper for SETEC.

Wraps `transformers` causal language models behind a thin abstraction
so SETEC's surprisal-based audits (the planned R12+1 surprisal signal
per `internal/SPEC_surprisal_signal.md`) can swap causal LMs without
touching call sites. The `internal/SPEC_surprisal_model_choice.md`
decision registers five candidate LMs (GPT-2 small, Llama 3.2 1B,
Phi-3 Mini, Qwen 2.5 1.5B, TinyLlama 1.1B) with a no-priority
posture — the §5.4 fixture test decides which is the user's CLI
default.

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
# default). Listed alphabetically — the order is not a ranking.
#
# - `gpt2`: OpenAI GPT-2 small (124M, MIT). Archived, no longer
#   changing; old training data may be less contaminated by modern
#   AI-generated web content than the modern LMs in this list.
# - `llama32_1b`: Meta Llama 3.2 1B (1.2B, Llama Community License).
#   Modern training data (contamination concern); custom license
#   propagates to outputs in some jurisdictions.
# - `phi3_mini`: Microsoft Phi-3 Mini 4K Instruct (3.8B, MIT). Upper
#   end of the candidate size range; strongest English performance.
# - `qwen25_1_5b`: Alibaba Qwen 2.5 1.5B (1.5B, Apache 2.0). Only
#   multilingual-capable candidate. Modern training (contamination
#   concern).
# - `tinyllama`: TinyLlama 1.1B-intermediate-step-1431k-3T
#   (1.1B, Apache 2.0). Documented training cutoff (less likely
#   to be contaminated by AI-generated web content); English-only.
MODEL_ALIASES: dict[str, str] = {
    "gpt2": "openai-community/gpt2",
    "llama32_1b": "meta-llama/Llama-3.2-1B",
    "phi3_mini": "microsoft/Phi-3-mini-4k-instruct",
    "qwen25_1_5b": "Qwen/Qwen2.5-1.5B",
    "tinyllama": "TinyLlama/TinyLlama-1.1B-intermediate-step-1431k-3T",
}

# Default when no `--model` is passed. `tinyllama` chosen as the
# documented-training-cutoff + small-footprint default for users
# who haven't run the §5.4 fixture suite. NOT a recommendation
# that tinyllama is best — only that it has the lowest contamination
# concern among the candidates and the smallest footprint. The §5.4
# fixture test on the user's register mix is the load-bearing
# decision.
DEFAULT_MODEL: str = "tinyllama"


class SurprisalBackendError(RuntimeError):
    """Raised when the surprisal backend cannot be loaded or used.

    Typed exception so callers can catch surprisal-specific failures
    separately from generic runtime errors — e.g., to fall back
    gracefully in audits where surprisal coverage is optional, or
    to report cleanly when the user is missing the Tier-4 dependency
    install.
    """


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
    _model: Any = field(default=None, repr=False, init=False, compare=False)
    _tokenizer: Any = field(default=None, repr=False, init=False, compare=False)
    _alias: str | None = field(default=None, repr=False, init=False, compare=False)

    def __post_init__(self) -> None:
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
                "accelerator — ROCm / CUDA / MPS / CPU-only)."
            ) from exc
        try:
            kwargs: dict[str, Any] = {}
            if self.revision:
                kwargs["revision"] = self.revision
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_id, **kwargs,
            )
            self._model = AutoModelForCausalLM.from_pretrained(
                self.model_id, **kwargs,
            )
            # Causal LMs in eval mode — no dropout, no gradient
            # accumulation. Surprisal scoring is inference-only.
            self._model.eval()
        except Exception as exc:
            raise SurprisalBackendError(
                f"Failed to load causal LM {self.model_id!r}"
                + (f" at revision {self.revision!r}" if self.revision else "")
                + f": {type(exc).__name__}: {exc}"
            ) from exc
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
        with torch.no_grad():
            outputs = model(input_ids)
        logits = outputs.logits  # (1, N, vocab)
        # Log-softmax for numerical stability, then negate and convert
        # from nats (natural log) to bits (log base 2). Position i's
        # logits predict token i+1; gather surprisal of the actual
        # next token at each position.
        import math
        log2e = 1.0 / math.log(2.0)
        log_probs_nats = torch.log_softmax(logits[0, :-1, :], dim=-1)
        # next_tokens[i] = the actual token at position i+1 in input_ids
        next_tokens = input_ids[0, 1:]
        surprisals_nats = -log_probs_nats.gather(
            -1, next_tokens.unsqueeze(-1),
        ).squeeze(-1)
        surprisals_bits = (surprisals_nats * log2e).tolist()
        if return_top_k <= 0:
            return surprisals_bits
        # Top-k diagnostic: most-surprising tokens with decoded text.
        token_ids = next_tokens.tolist()
        indexed = sorted(
            range(len(surprisals_bits)),
            key=lambda i: surprisals_bits[i],
            reverse=True,
        )[:return_top_k]
        top_k = [
            {
                "position": i + 1,  # 1-indexed position in input_ids
                "token_id": token_ids[i],
                "token_text": tokenizer.decode([token_ids[i]]),
                "surprisal_bits": surprisals_bits[i],
            }
            for i in indexed
        ]
        return surprisals_bits, top_k

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
