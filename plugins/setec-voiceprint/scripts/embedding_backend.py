#!/usr/bin/env python3
"""embedding_backend.py — pluggable embedding model wrapper for SETEC.

Wraps `sentence-transformers` behind a thin abstraction so SETEC's
voice-coherence and semantic-trajectory tools can swap embedding
models without touching call sites. The 2026-05-11
`internal/SPEC_embedding_model_choice.md` decision registers two
co-primary candidates — `mxbai-embed-large-v1` and
`EmbeddingGemma-300M` — and a ranked fallback list. This module
honors that decision via a small alias table while accepting any
HuggingFace model identifier passed verbatim.

Design goals:

  * **Minimal surface area.** Three public symbols: the
    `EmbeddingBackend` dataclass, the `MODEL_ALIASES` table, and
    `resolve_model_arg`. Tools call `.encode(texts)` and read
    `.identifier_block()` for PROVENANCE output.
  * **Lazy load.** Models load on the first `encode` call rather
    than at construction so a CLI that takes ``--help`` doesn't
    download a 600 MB model just to print usage.
  * **Honest failure.** Missing `sentence-transformers` raises a
    clear `EmbeddingBackendError` rather than silently degrading to
    TF-IDF or returning zeros. Callers that want fallback behavior
    own that decision explicitly.
  * **Deterministic mode by default.** Per `SPEC_embedding_model_
    choice.md` §4.3, batch-size determinism is a load-bearing
    property of any signal SETEC asserts about embedding-derived
    quantities. The wrapper sets ``torch.use_deterministic_
    algorithms(True, warn_only=True)`` on first load when torch is
    importable. Callers running batch-size sensitivity checks
    should still verify cross-batch agreement empirically.

The module does NOT manage:

  * Multi-GPU placement (single-device only).
  * Batching policy (passes ``batch_size`` straight through to
    sentence-transformers).
  * Disk caching of embeddings (consumers own that — see
    `semantic_trajectory_audit.py` for an example).
  * Records-cache redistribution (per the "Stylometry to the
    people" policy in `scripts/calibration/PROVENANCE.md`, SETEC
    does not ship per-row embedding caches across releases).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Candidate aliases per `internal/SPEC_embedding_model_choice.md` §6.1
# (revision 4: no-priority posture; the §6.4 fixture test is the
# load-bearing decision on which candidate is the user's CLI
# default). Tools that accept a `--model` argument resolve any of
# these aliases or any full HuggingFace model id passed verbatim.
#
# - `mxbai`, `gemma`, `harrier`: three of the five §6.4 candidates
#   with stable HuggingFace identifiers. `bge-large` and `qwen3-0.6b`
#   are also §6.4 candidates but ship as full IDs (no alias) until
#   the fixture test runs on the user's register mix and identifies
#   which set of aliases is worth keeping load-bearing.
# - `minilm`: the small / fast fallback shipped with the existing
#   `variance_audit.py` Tier-3 adjacent-cosine signal. Predates the
#   §6.4 candidate set; kept for back-compat.
MODEL_ALIASES: dict[str, str] = {
    "mxbai": "mixedbread-ai/mxbai-embed-large-v1",
    "gemma": "google/embeddinggemma-300m",
    "harrier": "microsoft/harrier-oss-v1-270m",
    "minilm": "sentence-transformers/all-MiniLM-L6-v2",
}

# Default when no `--model` is passed. mxbai is the CLI default for
# users who haven't run the §6.4 fixture suite; users who have can
# pass whichever co-primary wins on their register mix.
DEFAULT_MODEL: str = "mxbai"


class EmbeddingBackendError(RuntimeError):
    """Raised when the embedding backend cannot be loaded or used.

    The framework uses a typed exception so callers can catch
    embedding-specific failures separately from generic runtime
    errors — e.g., to fall back gracefully in audits where embedding
    coverage is optional, or to report cleanly when the user is
    missing calibration-tier dependencies.
    """


@dataclass
class EmbeddingBackend:
    """Pluggable wrapper around a sentence-transformers model.

    ``model_id`` accepts either a `MODEL_ALIASES` key (e.g.,
    ``"mxbai"``) or a full HuggingFace identifier (e.g.,
    ``"mixedbread-ai/mxbai-embed-large-v1"``). Aliases are resolved
    in ``__post_init__`` so callers always see the full id in
    ``self.model_id`` and in the PROVENANCE block.

    ``revision`` pins a specific HuggingFace commit SHA. PROVENANCE
    discipline requires that every load-bearing audit record the
    revision; tools that don't pin a revision get a ``revision: null``
    field in their PROVENANCE block and a warning in their
    claim-license that the result is not reproducible across
    upstream model updates.
    """

    model_id: str
    revision: str | None = None
    deterministic: bool = True
    _model: Any = field(default=None, repr=False, init=False, compare=False)
    _alias: str | None = field(default=None, repr=False, init=False, compare=False)

    def __post_init__(self) -> None:
        # Resolve alias → full id once at construction.
        if self.model_id in MODEL_ALIASES:
            self._alias = self.model_id
            self.model_id = MODEL_ALIASES[self.model_id]
        else:
            # Reverse-lookup so `identifier_block()` can report
            # which alias the full id corresponds to, when one
            # exists. Useful for downstream consumers that want to
            # group "mxbai" runs together even when the user passed
            # the full id.
            self._alias = next(
                (alias for alias, full in MODEL_ALIASES.items()
                 if full == self.model_id),
                None,
            )

    def _load(self) -> Any:
        """Load the sentence-transformers model on demand.

        Cached on ``self._model`` so subsequent encode calls reuse
        the loaded weights. The first call pays the download +
        weight-load cost (2-3 GB for mxbai over a fresh cache,
        seconds when cached). Raises ``EmbeddingBackendError`` on
        any failure — missing package, unknown model id, network
        timeout — with a message naming the failure mode so the
        caller can surface it cleanly.
        """
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:
            raise EmbeddingBackendError(
                "sentence-transformers is not installed. "
                "Install with: pip install sentence-transformers "
                "(part of the optional calibration / R12 tier; see "
                "the setup skill for tier-by-tier install guidance)."
            ) from exc
        try:
            kwargs: dict[str, Any] = {}
            if self.revision:
                kwargs["revision"] = self.revision
            self._model = SentenceTransformer(self.model_id, **kwargs)
        except Exception as exc:
            raise EmbeddingBackendError(
                f"Failed to load embedding model {self.model_id!r}"
                + (f" at revision {self.revision!r}" if self.revision else "")
                + f": {type(exc).__name__}: {exc}"
            ) from exc
        if self.deterministic:
            # Best-effort. Failures here don't block — the wrapper
            # surfaces them in the identifier block instead.
            try:
                import torch  # type: ignore
                torch.use_deterministic_algorithms(True, warn_only=True)
            except Exception:  # noqa: BLE001 — torch may not be installed
                pass
        return self._model

    def encode(
        self,
        texts: list[str],
        *,
        batch_size: int = 32,
        normalize: bool = False,
    ) -> Any:
        """Encode a list of texts into a numpy array of vectors.

        Returns a 2D numpy array of shape ``(len(texts),
        embedding_dim)``. Float32 by default per sentence-
        transformers' convention. When ``normalize=True`` the
        wrapper asks the model to L2-normalize so downstream cosine
        computation can use dot products directly; consumers that
        want raw vectors (e.g., for trajectory drift slopes that
        care about magnitude as well as direction) pass
        ``normalize=False``.

        Reviewer P2 (2026-05-14 retroactive audit): the prior
        version wrapped load failures (``_load``) but NOT runtime
        failures from ``model.encode``. A normal runtime exception
        from sentence-transformers — context-window overflow,
        out-of-memory on the device, tokenizer surprise on a
        pathological input — escapes as a bare ``RuntimeError`` /
        ``IndexError`` / ``MemoryError``. Callers like
        ``semantic_trajectory_audit.main()`` only catch
        ``EmbeddingBackendError``, so the CLI tracebacks instead of
        the documented clean-error path. The same fix shape was
        applied to ``surprisal_audit.audit_surprisal`` in PR #30;
        this is the embedding-side analogue.
        """
        if not texts:
            import numpy as np  # type: ignore
            return np.empty((0, 0), dtype="float32")
        model = self._load()
        try:
            return model.encode(
                texts,
                show_progress_bar=False,
                batch_size=batch_size,
                convert_to_numpy=True,
                normalize_embeddings=normalize,
            )
        except EmbeddingBackendError:
            # Already typed; pass through unchanged so callers
            # that distinguish load-vs-runtime still see the
            # typed error.
            raise
        except (
            MemoryError, RuntimeError, IndexError, ValueError,
            OSError,
        ) as exc:
            raise EmbeddingBackendError(
                f"sentence-transformers encode failed "
                f"({type(exc).__name__}: {exc}). Common causes: "
                f"a text in the batch exceeded the model's context "
                f"window, the device ran out of memory, or the "
                f"tokenizer produced an unexpected shape. Consider "
                f"chunking long inputs before calling encode."
            ) from exc

    def identifier_block(self) -> dict[str, Any]:
        """Provenance block consumers paste into their JSON output.

        Mirrors the shape `internal/SPEC_embedding_model_choice.md`
        §6.6 requires for the calibration ledger: model id, revision
        SHA, alias (when known), deterministic-mode flag, and the
        method name. Tools that want richer provenance (e.g., torch
        version, ROCm patch) extend this dict in their own output;
        the wrapper deliberately doesn't probe those because they're
        deployment context, not embedding-backend state.
        """
        return {
            "id": self.model_id,
            "revision": self.revision,
            "alias": self._alias,
            "deterministic_mode": self.deterministic,
            "method": "sentence-transformers",
        }


def resolve_model_arg(arg: str | None) -> str:
    """Normalise a `--model` CLI argument to an alias or full id.

    Returns the original string if it's already in ``MODEL_ALIASES``
    or looks like a HuggingFace identifier (contains ``/``). Returns
    ``DEFAULT_MODEL`` for ``None``. This lets CLI argument parsers
    take either form: ``--model mxbai`` or
    ``--model mixedbread-ai/mxbai-embed-large-v1``.
    """
    if arg is None:
        return DEFAULT_MODEL
    return arg
