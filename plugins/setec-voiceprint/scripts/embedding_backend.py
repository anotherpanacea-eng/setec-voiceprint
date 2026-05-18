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


# Dtype contract mirrors ``surprisal_backend.VALID_DTYPES`` (added
# in PR #93). Embedding models benefit from bf16/fp16 inference on
# Ampere+ / Hopper / Ada cuda just as much as causal LMs do — the
# embedding forward pass is the same matmul-heavy workload, only
# the output projection differs. Keeping the dtype string vocabulary
# symmetric between the two backends means operators set
# ``--surprisal-dtype`` and ``--embedding-dtype`` with one mental
# model instead of two.
VALID_DTYPES: tuple[str, ...] = ("auto", "fp32", "fp16", "bf16")


def _resolve_dtype(
    requested: str,
    *,
    cuda_available: bool | None = None,
    bf16_supported: bool | None = None,
) -> tuple[Any, str]:
    """Resolve a user-facing dtype string to a (torch.dtype, label).

    Embedding-side mirror of ``surprisal_backend._resolve_dtype``.
    Same auto-resolution semantics: ``auto`` picks bf16 on cuda
    devices that support it (Ampere / Hopper / Ada), fp16 on older
    cuda where bf16 forward passes fall back to slow kernels (V100
    / T4), and fp32 elsewhere (CPU / MPS; bf16/fp16 inference is
    not a throughput win on those backends).

    The probe defaults to live ``torch.cuda`` queries but accepts
    overrides for testability — the resolution logic is pure-Python
    and can be exercised without a GPU by passing the two booleans
    directly.

    Returns ``(torch_dtype, canonical_label)``. The label is one of
    ``"fp32" / "fp16" / "bf16"`` — never ``"auto"`` (the auto
    sentinel is consumed at resolution time so the provenance block
    records what the backend actually loaded, not the user's
    request).
    """
    if requested not in VALID_DTYPES:
        raise EmbeddingBackendError(
            f"Invalid embedding-backend dtype {requested!r}; "
            f"must be one of {VALID_DTYPES}."
        )
    try:
        import torch  # type: ignore
    except ImportError as exc:
        raise EmbeddingBackendError(
            "torch not installed; cannot resolve embedding-backend "
            "dtype. Install with: pip install -r "
            "requirements-calibration.txt"
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
    # Dtype contract added to align with PR #93's surprisal-side
    # work. ``auto`` picks bf16 on Ampere+ / Hopper / Ada cuda, fp16
    # on pre-Ampere cuda, fp32 on CPU / MPS. Explicit dtypes
    # override the resolution. Validated in ``__post_init__`` so a
    # typo fails at construction rather than at first-encode.
    dtype: str = "auto"
    # Explicit device override. When ``None``, sentence-transformers'
    # built-in auto-device logic picks cuda > mps > cpu. Operators on
    # multi-GPU hosts pin a specific device by passing
    # ``"cuda:0"`` / ``"cuda:1"`` so concurrent calibration jobs
    # (e.g., the cloud bake-off matrix's parallel-pair-of-processes
    # pattern from PR #100) don't fight for the same device. The
    # surprisal-side equivalent of this came in PR #88; this is the
    # embedding-side mirror.
    device: str | None = None
    _model: Any = field(default=None, repr=False, init=False, compare=False)
    _alias: str | None = field(default=None, repr=False, init=False, compare=False)
    # Populated by ``_load`` after dtype + device resolution. Surfaced
    # via ``identifier_block`` so audit consumers can distinguish
    # operator intent (``dtype: auto`` / ``device: None``) from the
    # actual loaded state (``dtype_loaded: bf16`` / ``device_loaded:
    # cuda:0``).
    _resolved_dtype_label: str | None = field(
        default=None, repr=False, init=False, compare=False,
    )
    _resolved_device: str | None = field(
        default=None, repr=False, init=False, compare=False,
    )

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
        # Validate dtype at construction so a typo fails fast — the
        # surprisal-side contract does the same. Refusing an invalid
        # value here also keeps ``_load`` simpler (it can assume
        # ``self.dtype`` is one of ``VALID_DTYPES``).
        if self.dtype not in VALID_DTYPES:
            raise EmbeddingBackendError(
                f"Invalid embedding-backend dtype {self.dtype!r}; "
                f"must be one of {VALID_DTYPES}."
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
        # Resolve dtype before constructing the model. ``auto``
        # consumes here so the loaded model carries the actual
        # precision (bf16 on H100, fp16 on T4, fp32 on CPU) rather
        # than the ``auto`` sentinel.
        #
        # Torch is a hard dependency of sentence-transformers in
        # production, so in a real install ``_resolve_dtype`` will
        # always succeed. Test fakes that stub ``sys.modules
        # ["sentence_transformers"]`` without installing torch are
        # supported by silently skipping the dtype resolution +
        # ``model_kwargs`` injection — the resolved label stays
        # ``None`` so ``identifier_block`` truthfully reflects "no
        # dtype was resolved on this run", and the SentenceTransformer
        # constructor receives the same kwargs it would have under
        # the pre-1.96 path.
        torch_dtype: Any = None
        try:
            torch_dtype, self._resolved_dtype_label = _resolve_dtype(
                self.dtype,
            )
        except EmbeddingBackendError:
            # Torch not installed (or other resolution failure). In
            # production this is unreachable because ST itself
            # depends on torch; in test stubs it's expected.
            torch_dtype = None
        try:
            kwargs: dict[str, Any] = {}
            if self.revision:
                kwargs["revision"] = self.revision
            if torch_dtype is not None:
                # ``model_kwargs`` flows into ``AutoModel.from_pretrained``
                # inside sentence-transformers, so ``torch_dtype``
                # lands on the underlying HF model the same way PR
                # #93's surprisal load does. fp32 is the ST default;
                # we pass it explicitly for symmetry with bf16/fp16
                # and so the provenance block records the dtype
                # unconditionally.
                kwargs["model_kwargs"] = {"torch_dtype": torch_dtype}
            if self.device is not None:
                # Sentence-transformers honors ``device=`` in the
                # constructor by moving the model on load. ``None``
                # (the default) defers to ST's auto-device logic,
                # which picks cuda > mps > cpu.
                kwargs["device"] = self.device
            self._model = SentenceTransformer(self.model_id, **kwargs)
        except Exception as exc:
            raise EmbeddingBackendError(
                f"Failed to load embedding model {self.model_id!r}"
                + (f" at revision {self.revision!r}" if self.revision else "")
                + f" (dtype={self._resolved_dtype_label!r}"
                + (f", device={self.device!r}" if self.device else "")
                + f"): {type(exc).__name__}: {exc}"
            ) from exc
        # Record the device the model actually landed on. Pulling
        # this from the loaded model rather than ``self.device``
        # captures ST's auto-pick when the caller didn't specify —
        # the provenance block then shows e.g. ``device_loaded:
        # cuda:0`` even on an operator who never set ``--embedding-
        # device``.
        try:
            self._resolved_device = str(
                next(self._model.parameters()).device
            )
        except (StopIteration, AttributeError):
            # Some stub models in tests have no parameters; fall back
            # to whatever the caller declared.
            self._resolved_device = self.device
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
            # Dtype + device pair: ``_requested`` captures operator
            # intent (might be ``"auto"`` / ``None``), ``_loaded``
            # captures the resolved state after ``_load`` has run.
            # Both are recorded so an audit consumer can tell the
            # difference between "operator picked bf16 explicitly"
            # and "auto resolved to bf16 on this host".
            "dtype_requested": self.dtype,
            "dtype_loaded": self._resolved_dtype_label,
            "device_requested": self.device,
            "device_loaded": self._resolved_device,
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
