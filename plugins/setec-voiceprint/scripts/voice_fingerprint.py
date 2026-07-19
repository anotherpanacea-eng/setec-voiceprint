#!/usr/bin/env python3
"""voice_fingerprint.py — same-author style-embedding surface (spec 02).

A learned-manifold complement to SETEC's scalar voice-coherence
signals. Embeds passages with a *frozen* style encoder (LUAR by
default; Wegmann optional), windows the input the same way
``semantic_trajectory_audit.py`` does, and reports the
**cosine-similarity distribution** across windows. It answers "are
these passages stylistically consistent under model M's learned
style manifold?" — descriptively, with no threshold and no verdict.

Why a separate surface (``authorship_embedding``): the framework's
existing voice-coherence tools (``voice_distance``,
``idiolect_detector``) measure distance in *interpretable scalar
features* (function words, char n-grams, Burrows Delta);
``semantic_trajectory_audit`` measures *meaning* trajectory; Surface 5
measures *perplexity*. None of them lean on a learned holistic voice
manifold — the axis authorship-verification SOTA (PAN, LUAR) actually
uses. This surface adds that orthogonal axis without per-deployment
training and without a threshold, which fits SETEC's no-verdict
posture exactly.

Three modes (per spec §Method):

  * **single** — pairwise cosines across the document's own windows
    (internal voice consistency / drift).
  * **two_corpus** — each target window vs. a baseline corpus's
    window centroid(s) → similarity distribution (same-author-
    consistency evidence). Requires ``--baseline-dir``.
  * **n_way** — target vs. candidate baseline vs. impostor pool →
    report where the target's similarity falls. This is the
    embedding ANALOGUE of ``general_imposters.py``'s framing, NOT a
    GI replacement: it stays descriptive (no GI-style win/loss
    proportion, no threshold). Requires ``--baseline-dir`` and
    ``--impostor-dir``.

Reused, not reimplemented: the windowing helper (imported from
``semantic_trajectory_audit``) and the frozen encoder weights. New
code: the surface contract, distance aggregation, and caveat
plumbing.

CRITICAL (calibration posture): cosine distances are absolute
measurements. Any "consistent / divergent" banding is illustrative
only and named so in the claim-license. The surface ships
PROVISIONAL; calibration (a per-register impostor-pool study giving
an empirical same-author cosine distribution) is future work.

Usage::

    # Single-document internal consistency:
    python3 scripts/voice_fingerprint.py draft.txt --json

    # Two-corpus same-author-consistency evidence:
    python3 scripts/voice_fingerprint.py draft.txt \\
        --baseline-dir writer_prior_work/ --json

    # N-way embedding analogue (target vs candidate vs impostors):
    python3 scripts/voice_fingerprint.py draft.txt \\
        --baseline-dir candidate/ --impostor-dir impostors/ --json

task_surface: authorship_embedding. Refuses "same person",
"different author", and "AI/human". Reports the cosine distribution;
the reader interprets it.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Protocol

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_baseline_metadata, build_output  # type: ignore

# REUSE the windowing helper — do NOT reimplement it. The spec
# requires sharing semantic_trajectory_audit's windowing so paragraph
# and fixed-token strategies produce identical units across surfaces.
from semantic_trajectory_audit import (  # type: ignore
    _approx_token_count,
    split_windows,
)

TASK_SURFACE = "authorship_embedding"
TOOL_NAME = "voice_fingerprint"
SCRIPT_VERSION = "1.0"

# Model aliases for the frozen style encoders this surface wraps.
# LUAR is the default: code + weights are Apache-2.0 (clean license),
# Reddit/social-trained (register skew — see the claim-license).
# Wegmann is the secondary content-controlled cross-check, opt-in via
# sentence-transformers; its weight-card license tag is confirmed
# permissive before shipping (spec §License decision).
#
# StyleDistance and mUAR are added behind the SAME seam (spec 28, M1):
#   * styledistance — synthetic near-paraphrase contrastive training
#     for a MORE content-independent manifold than LUAR (arXiv:2410.12757);
#     loads through the LUAR transformers/AutoModel path.
#   * muar — Multilingual Universal Authorship Representation, the
#     learned language-aware complement to crosslingual_voice_distance's
#     parser-free profile (arXiv:2509.16531). NO public checkpoint exists
#     yet (the alias is registered but SPEC-ONLY; the loader refuses it with
#     guidance until weights ship — see _UNRELEASED_MODEL_IDS).
# Both ship PROVISIONAL — an encoder swap does NOT promote calibration
# status and does NOT change the default; DEFAULT_MODEL stays "luar".
# Their real weight load is the M2 model seam (skipif-gated, never CI);
# under the unit suite the loader is monkeypatched to a stub, so the
# new encoder classes are present in code but never executed here.
MODEL_ALIASES: dict[str, str] = {
    "luar": "rrivera1849/LUAR-MUD",
    "wegmann": "AnnaWegmann/Style-Embedding",
    "styledistance": "StyleDistance/styledistance",
    "muar": "rrivera1849/mUAR",
}
DEFAULT_MODEL = "luar"

# mUAR's intended publisher id is registered as an alias, but NO public checkpoint exists yet —
# verified against the publisher inventory (https://huggingface.co/rrivera1849/models lists only
# LUAR-CRUD / LUAR-MUD / LUSR / ..., no mUAR). The alias is SPEC-ONLY: the loader refuses to attempt
# loading these ids and fails loud with guidance instead of letting transformers 404 (Codex P1).
# Drop an id from this set when its weights are actually published.
_UNRELEASED_MODEL_IDS: frozenset[str] = frozenset({"rrivera1849/mUAR"})

# Install hint surfaced when transformers is absent. Mirrors the
# dependency_check-style guidance (NOT a traceback) so an operator
# missing the style-embedding stack gets an actionable message.
_TRANSFORMERS_INSTALL_HINT = (
    "voice_fingerprint requires the `transformers` package to load a "
    "frozen style encoder (default: LUAR `rrivera1849/LUAR-MUD`, "
    "Apache-2.0). It is not installed.\n"
    "Install the surprisal / style-embedding tier with:\n"
    "    pip install -r requirements-surprisal.txt\n"
    "or directly:\n"
    "    pip install transformers torch\n"
    "The Wegmann cross-check (--model wegmann) additionally needs:\n"
    "    pip install sentence-transformers\n"
    "See: python3 plugins/setec-voiceprint/scripts/dependency_check.py "
    "--tier surprisal --suggest"
)


class StyleEncoder(Protocol):
    """Minimal encoder contract this surface depends on.

    The only method call sites use is ``encode(list[str]) ->
    np.ndarray`` returning **unit-normalized rows** (one L2-normalized
    style vector per input passage). Keeping the contract this thin is
    what lets tests substitute a deterministic stub for
    ``_load_encoder`` without loading any real weights.
    """

    model_id: str

    def encode(self, texts: list[str]) -> Any:  # -> np.ndarray
        ...


class VoiceFingerprintError(RuntimeError):
    """Raised for clean, caller-surfaced failures (missing deps,
    empty corpora, too-few-windows). ``main`` catches these and exits
    non-zero with the message on stderr rather than tracebacking."""


# --------------- Encoder loading (injectable) --------------------


class _LUAREncoder:
    """Real-path LUAR encoder. Constructed lazily by ``_load_encoder``.

    NOTE: this class exists so the real integration point is present
    in code, but it is NEVER executed in this task's tests — the test
    suite monkeypatches ``_load_encoder`` to return a deterministic
    stub. The maintainer does the GPU smoke-load separately (see the
    module docstring and the handoff note in the spec).

    Integration details (LUAR model card):
      * Loaded via ``transformers`` with ``trust_remote_code=True``
        (LUAR ships custom modeling code, not a stock architecture).
      * Mean-pooling over token embeddings per the model card.
      * Rows are L2-normalized before return so downstream cosine is a
        plain dot product.
    """

    def __init__(self, model_id: str, device: str | None = None) -> None:
        self.model_id = model_id
        self._device = device
        self._tokenizer: Any = None
        self._model: Any = None

    def _load(self) -> None:
        if self._model is not None:
            return
        # Imported lazily so a `--help` invocation (or a test that
        # never reaches the real path) does not require transformers.
        import torch  # type: ignore
        from transformers import AutoModel, AutoTokenizer  # type: ignore

        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_id, trust_remote_code=True,
        )
        self._model = AutoModel.from_pretrained(
            self.model_id, trust_remote_code=True,
        )
        self._model.eval()
        if self._device:
            self._model.to(self._device)
        # bind torch for encode()
        self._torch = torch

    def encode(self, texts: list[str]) -> Any:
        import numpy as np  # type: ignore

        if not texts:
            return np.empty((0, 0), dtype="float32")
        self._load()
        torch = self._torch
        enc = self._tokenizer(
            texts,
            return_tensors="pt",
            padding=True,
            truncation=True,
        )
        # LUAR is an EPISODE model: its forward expects shape
        # (n_authors, docs_per_author, seq_len). We embed each passage as
        # its own author with a single document, so add the episode axis
        # -> (N, 1, seq_len). LUAR pools internally and returns the
        # author embedding (N, embed_dim) directly (no mean-pool here).
        input_ids = enc["input_ids"].unsqueeze(1)
        attention_mask = enc["attention_mask"].unsqueeze(1)
        if self._device:
            input_ids = input_ids.to(self._device)
            attention_mask = attention_mask.to(self._device)
        with torch.no_grad():
            out = self._model(input_ids=input_ids, attention_mask=attention_mask)
        if isinstance(out, (tuple, list)):
            emb = out[0]
        else:
            emb = getattr(out, "last_hidden_state", out)
        if hasattr(emb, "dim") and emb.dim() == 3:
            # Fallback only: unexpected token-level (B, seq, dim) output
            # -> mean-pool. The LUAR path returns (N, dim) and skips this.
            emb = emb.mean(dim=1)
        # .float() BEFORE .numpy(): a bf16 model (e.g. StyleDistance ships bf16 weights)
        # has no numpy dtype — a direct .numpy() raises "unsupported ScalarType BFloat16".
        # .float() is a no-op on fp32 (LUAR/Wegmann) and the fix for bf16 (caught by the
        # M2 GPU smoke; the stub suite never loads real weights).
        vecs = emb.float().detach().cpu().numpy().astype("float32")
        return _unit_normalize_rows(vecs)


class _WegmannEncoder:
    """Real-path Wegmann (Style-Embedding) encoder via
    sentence-transformers. Opt-in; also NEVER executed in tests."""

    def __init__(self, model_id: str, device: str | None = None) -> None:
        self.model_id = model_id
        self._device = device
        self._model: Any = None

    def _load(self) -> None:
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer  # type: ignore

        kwargs: dict[str, Any] = {}
        if self._device:
            kwargs["device"] = self._device
        self._model = SentenceTransformer(self.model_id, **kwargs)

    def encode(self, texts: list[str]) -> Any:
        import numpy as np  # type: ignore

        if not texts:
            return np.empty((0, 0), dtype="float32")
        self._load()
        vecs = self._model.encode(
            texts,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return np.asarray(vecs, dtype="float32")


class _StyleDistanceEncoder:
    """Real-path StyleDistance encoder. Constructed lazily by
    ``_load_encoder``. NEVER executed in this task's tests (the spec-02
    ``_LUAREncoder`` discipline — the suite monkeypatches
    ``_load_encoder`` to a stub). The maintainer does the GPU/CPU smoke
    separately (M2, see spec 28 and the module docstring).

    StyleDistance (arXiv:2410.12757) is trained on synthetic
    near-paraphrases that vary STYLE while holding CONTENT fixed, so its
    manifold is MORE content-independent than LUAR's — a cleaner answer
    to the topic-leakage caveat this surface already prints. It is NOT
    topic-proof: content-independence is a training property, not a
    guarantee (the caveat is reworded, never retired).

    Integration details (StyleDistance model card):
      * Loaded via ``transformers`` with ``AutoModel`` (same path as
        ``_LUAREncoder``; ``trust_remote_code`` honored per the card).
      * Always mask-weighted mean-pool ``last_hidden_state``, matching the
        model's sentence-transformers ``1_Pooling`` config. Ignore
        ``pooler_output``: RoBERTa exposes an untrained CLS dense+tanh head.
      * Rows are L2-normalized before return so downstream cosine is a
        plain dot product.
    """

    def __init__(self, model_id: str, device: str | None = None) -> None:
        self.model_id = model_id
        self._device = device
        self._tokenizer: Any = None
        self._model: Any = None
        self._torch: Any = None

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch  # type: ignore
        from transformers import AutoModel, AutoTokenizer  # type: ignore

        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_id, trust_remote_code=True,
        )
        self._model = AutoModel.from_pretrained(
            self.model_id, trust_remote_code=True,
        )
        # Inference mode (no dropout). Called via getattr to keep the
        # frozen-encoder intent explicit; mirrors _LUAREncoder.
        getattr(self._model, "eval")()
        if self._device:
            self._model.to(self._device)
        self._torch = torch

    def encode(self, texts: list[str]) -> Any:
        import numpy as np  # type: ignore

        if not texts:
            return np.empty((0, 0), dtype="float32")
        self._load()
        torch = self._torch
        enc = self._tokenizer(
            texts,
            return_tensors="pt",
            padding=True,
            truncation=True,
        )
        if self._device:
            enc = {k: v.to(self._device) for k, v in enc.items()}
        with torch.no_grad():
            out = self._model(**enc)
        # MEAN-pool the token states (mask-weighted) — do NOT use pooler_output.
        # StyleDistance ships as a sentence-transformers model whose 1_Pooling
        # config is pooling_mode_mean_tokens=true; the RoBERTa pooler_output is the
        # UNTRAINED CLS dense+tanh, a different (wrong) manifold that collapses the
        # cosine distribution into a tight cone. M2 GPU smoke confirmed: pooler_output
        # gave author-separation 0.022; mean-pool matches the sentence-transformers
        # reference load (0.051). Mean-pool is the model card's trained pooling.
        hidden = getattr(out, "last_hidden_state", out)
        if isinstance(hidden, (tuple, list)):
            hidden = hidden[0]
        mask = enc["attention_mask"].unsqueeze(-1).type_as(hidden)
        summed = (hidden * mask).sum(dim=1)
        counts = mask.sum(dim=1).clamp(min=1.0)
        emb = summed / counts
        # .float() BEFORE .numpy(): a bf16 model (e.g. StyleDistance ships bf16 weights)
        # has no numpy dtype — a direct .numpy() raises "unsupported ScalarType BFloat16".
        # .float() is a no-op on fp32 (LUAR/Wegmann) and the fix for bf16 (caught by the
        # M2 GPU smoke; the stub suite never loads real weights).
        vecs = emb.float().detach().cpu().numpy().astype("float32")
        return _unit_normalize_rows(vecs)


class _MUAREncoder:
    """Real-path mUAR (Multilingual Universal Authorship
    Representation) encoder. Constructed lazily by ``_load_encoder``.
    NEVER executed in this task's tests (the spec-02 ``_LUAREncoder``
    discipline). The maintainer does the GPU/CPU smoke separately (M2).

    mUAR (arXiv:2509.16531) is a MULTILINGUAL authorship-representation
    encoder — the learned, language-AWARE complement to
    ``crosslingual_voice_distance``'s parser-free profile. It is the
    encoder BOTH ``voice_fingerprint --model muar`` AND the
    ``crosslingual_voice_distance --encoder muar`` opt-in mode resolve
    to, so there is ONE mUAR load path, not two.

    POSTURE: multilingual representation is a CAPABILITY, not a LICENSE.
    The cross-language refusal is a claim-license commitment, not an
    encoder limitation; mUAR being multilingual does NOT by itself
    license cross-language comparison (see the per-encoder caveat and
    spec 28 Non-goals). It is wrapped FROZEN; no per-deployment training.

    Integration details (mUAR is a UAR-family episode model, like LUAR):
      * Loaded via ``transformers`` ``AutoModel`` with
        ``trust_remote_code`` (UAR ships custom modeling code).
      * Embeds each passage as its own single-document author (episode
        axis) and returns the author embedding (N, dim) directly.
      * Rows are L2-normalized before return.
    """

    def __init__(self, model_id: str, device: str | None = None) -> None:
        self.model_id = model_id
        self._device = device
        self._tokenizer: Any = None
        self._model: Any = None
        self._torch: Any = None

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch  # type: ignore
        from transformers import AutoModel, AutoTokenizer  # type: ignore

        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_id, trust_remote_code=True,
        )
        self._model = AutoModel.from_pretrained(
            self.model_id, trust_remote_code=True,
        )
        getattr(self._model, "eval")()
        if self._device:
            self._model.to(self._device)
        self._torch = torch

    def encode(self, texts: list[str]) -> Any:
        import numpy as np  # type: ignore

        if not texts:
            return np.empty((0, 0), dtype="float32")
        self._load()
        torch = self._torch
        enc = self._tokenizer(
            texts,
            return_tensors="pt",
            padding=True,
            truncation=True,
        )
        # UAR-family episode model: forward expects
        # (n_authors, docs_per_author, seq_len). Embed each passage as
        # its own author with a single document -> (N, 1, seq_len).
        input_ids = enc["input_ids"].unsqueeze(1)
        attention_mask = enc["attention_mask"].unsqueeze(1)
        if self._device:
            input_ids = input_ids.to(self._device)
            attention_mask = attention_mask.to(self._device)
        with torch.no_grad():
            out = self._model(input_ids=input_ids, attention_mask=attention_mask)
        if isinstance(out, (tuple, list)):
            emb = out[0]
        else:
            emb = getattr(out, "last_hidden_state", out)
        if hasattr(emb, "dim") and emb.dim() == 3:
            # Fallback only: unexpected token-level (B, seq, dim) output
            # -> mean-pool. The UAR path returns (N, dim) and skips this.
            emb = emb.mean(dim=1)
        # .float() BEFORE .numpy(): a bf16 model (e.g. StyleDistance ships bf16 weights)
        # has no numpy dtype — a direct .numpy() raises "unsupported ScalarType BFloat16".
        # .float() is a no-op on fp32 (LUAR/Wegmann) and the fix for bf16 (caught by the
        # M2 GPU smoke; the stub suite never loads real weights).
        vecs = emb.float().detach().cpu().numpy().astype("float32")
        return _unit_normalize_rows(vecs)


def _load_encoder(model: str, device: str | None = None) -> StyleEncoder:
    """Return a frozen style encoder for ``model``.

    INJECTION POINT. Tests monkeypatch this function to return a
    deterministic stub; the real path loads LUAR (default) or Wegmann.

    ``model`` accepts an alias (``luar`` / ``wegmann``) or a full
    HuggingFace identifier passed verbatim. Missing ``transformers``
    raises ``VoiceFingerprintError`` carrying the dependency_check-
    style install hint (NOT a traceback).

    The real encoder weights are NEVER downloaded or loaded during
    SETEC's unit tests — loading is lazy (deferred to first
    ``.encode``) and the test suite never reaches it.
    """
    resolved = MODEL_ALIASES.get(model, model)
    is_wegmann = (
        model == "wegmann" or resolved == MODEL_ALIASES["wegmann"]
    )
    is_styledistance = (
        model == "styledistance"
        or resolved == MODEL_ALIASES["styledistance"]
    )
    is_muar = (
        model == "muar" or resolved == MODEL_ALIASES["muar"]
    )
    # Gate on transformers presence up front so the error is clean and
    # actionable regardless of which encoder was requested. (LUAR /
    # StyleDistance / mUAR use transformers directly; sentence-
    # transformers depends on it too.) The new encoders reuse THIS gate
    # and THIS error text — no new dependency-gate code path.
    try:
        import transformers  # type: ignore  # noqa: F401
    except ImportError as exc:
        raise VoiceFingerprintError(_TRANSFORMERS_INSTALL_HINT) from exc

    # Spec-only encoder guard (AFTER the transformers gate, so a missing-transformers run still gets
    # the shared install hint): refuse a registered-but-unreleased id with an actionable message
    # rather than a transformers 404 on the real load path (Codex P1, mUAR).
    if resolved in _UNRELEASED_MODEL_IDS:
        raise VoiceFingerprintError(
            f"--model {model}: {resolved} has no public checkpoint — it is not in the publisher's "
            f"model inventory (https://huggingface.co/rrivera1849/models). mUAR (Multilingual "
            f"Universal Authorship Representation, arXiv:2509.16531) is registered but SPEC-ONLY "
            f"until weights ship. Pass an explicit mUAR-family checkpoint via --model <hf-id-or-path>, "
            f"or use --model luar (the calibrated default)."
        )

    if is_wegmann:
        try:
            import sentence_transformers  # type: ignore  # noqa: F401
        except ImportError as exc:
            raise VoiceFingerprintError(
                "voice_fingerprint --model wegmann requires "
                "`sentence-transformers` (AnnaWegmann/Style-Embedding "
                "loads via sentence-transformers). It is not "
                "installed. Install with:\n"
                "    pip install sentence-transformers\n"
                "Or use the default LUAR encoder (--model luar)."
            ) from exc
        return _WegmannEncoder(resolved, device=device)
    # StyleDistance and mUAR load through the SAME transformers /
    # AutoModel path as LUAR — the alias picks the encoder class; the
    # dispatch, dependency gate, and error text are reused. (StyleDistance
    # would reuse the _WegmannEncoder branch above instead if its weight
    # card ships as a sentence-transformers model — a one-line decision
    # gated on the card, see spec 28 Open Questions.)
    if is_styledistance:
        return _StyleDistanceEncoder(resolved, device=device)
    if is_muar:
        return _MUAREncoder(resolved, device=device)
    return _LUAREncoder(resolved, device=device)


# --------------- Cosine helpers ----------------------------------


def _unit_normalize_rows(matrix: Any) -> Any:
    """L2-normalize each row of a 2D array. Zero rows stay zero
    (avoids division-by-zero on a degenerate embedding)."""
    import numpy as np  # type: ignore

    arr = np.asarray(matrix, dtype="float32")
    if arr.size == 0:
        return arr
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)
    return arr / norms


def _cosine(a: Any, b: Any) -> float:
    """Plain cosine similarity over two vectors. Returns 0.0 when
    either is a zero vector. Clamped to [-1, 1] at the source so
    float-epsilon in ``np.dot/(‖a‖‖b‖)`` cannot emit a value just
    outside the range (e.g. 1.0000000002)."""
    import numpy as np  # type: ignore

    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return max(-1.0, min(1.0, float(np.dot(a, b) / (na * nb))))


def _pairwise_cosines(embeddings: Any) -> list[float]:
    """All unique pairwise cosines across a set of window embeddings
    (i < j). The single-document mode's internal-consistency signal."""
    n = len(embeddings) if embeddings is not None else 0
    out: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            out.append(_cosine(embeddings[i], embeddings[j]))
    return out


def _centroid(embeddings: Any) -> Any:
    """Mean vector across rows, then unit-normalized. Used as the
    baseline 'voice centroid' in two-corpus mode."""
    import numpy as np  # type: ignore

    arr = np.asarray(embeddings, dtype="float32")
    if arr.size == 0:
        return arr
    mean = arr.mean(axis=0, keepdims=True)
    return _unit_normalize_rows(mean)[0]


def cosine_distribution(values: list[float]) -> dict[str, Any]:
    """Distribution summary of a cosine series.

    Keys (per spec contract): ``mean``, ``sd``, ``min``, ``p10``,
    ``p50``, ``p90``. Plus ``n`` for transparency. Empty input yields
    all-None so a well-formed (if empty) block is always emitted.
    """
    if not values:
        return {
            "n": 0,
            "mean": None,
            "sd": None,
            "min": None,
            "p10": None,
            "p50": None,
            "p90": None,
        }
    ordered = sorted(values)
    return {
        "n": len(values),
        "mean": statistics.mean(values),
        "sd": statistics.stdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "p10": _quantile(ordered, 0.10),
        "p50": _quantile(ordered, 0.50),
        "p90": _quantile(ordered, 0.90),
    }


def _quantile(ordered: list[float], q: float) -> float:
    """Linear-interpolation quantile on an already-sorted list.
    Mirrors numpy's default 'linear' method so the surface doesn't
    depend on numpy just to summarize a small float list."""
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


# --------------- Corpus loading ----------------------------------


def _load_dir_texts(directory: str | Path) -> list[tuple[str, str]]:
    """Load ``.txt`` / ``.md`` files from a directory as (name, text)
    pairs. Non-recursive, matching the directory-baseline convention
    used elsewhere in the framework (stylometry_core.load_entries_
    from_dir)."""
    base = Path(directory)
    if not base.is_dir():
        raise VoiceFingerprintError(
            f"Not a directory: {base}"
        )
    paths = sorted(base.glob("*.txt")) + sorted(base.glob("*.md"))
    out: list[tuple[str, str]] = []
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if text.strip():
            out.append((p.name, text))
    return out


def _window_corpus(
    texts: list[str], strategy: str, window_size: int,
) -> list[str]:
    """Flatten a corpus of documents into a single list of windows,
    applying the shared windowing strategy to each document."""
    windows: list[str] = []
    for t in texts:
        windows.extend(
            split_windows(t, strategy, window_size=window_size)
        )
    return windows


# --------------- Mode computations -------------------------------


def _per_window_series(values: list[float]) -> list[float]:
    """Round per-window cosines for the optional series so JSON output
    stays compact and stable across platforms."""
    return [round(v, 6) for v in values]


def run_single(
    target_windows: list[str],
    encoder: StyleEncoder,
) -> dict[str, Any]:
    """Single-document mode: pairwise cosines across the doc's own
    windows. Internal voice consistency / drift."""
    if len(target_windows) < 2:
        raise VoiceFingerprintError(
            f"single mode needs at least 2 windows to compute pairwise "
            f"cosines; got {len(target_windows)}. Try a finer "
            f"--window-strategy / smaller --window-size, or a longer text."
        )
    embeddings = encoder.encode(target_windows)
    cosines = _pairwise_cosines(embeddings)
    return {
        "mode": "single",
        "n_windows": len(target_windows),
        "cosine_distribution": cosine_distribution(cosines),
        "per_window": _per_window_series(cosines),
    }


def run_two_corpus(
    target_windows: list[str],
    baseline_windows: list[str],
    encoder: StyleEncoder,
) -> dict[str, Any]:
    """Two-corpus mode: each target window vs. the baseline window
    centroid → similarity distribution (same-author-consistency
    evidence)."""
    if not target_windows:
        raise VoiceFingerprintError(
            "two_corpus mode produced no target windows."
        )
    if not baseline_windows:
        raise VoiceFingerprintError(
            "two_corpus mode produced no baseline windows; check "
            "--baseline-dir contains readable .txt/.md prose."
        )
    target_emb = encoder.encode(target_windows)
    baseline_emb = encoder.encode(baseline_windows)
    centroid = _centroid(baseline_emb)
    cosines = [_cosine(target_emb[i], centroid) for i in range(len(target_emb))]
    return {
        "mode": "two_corpus",
        "n_windows": len(target_windows),
        "n_baseline_windows": len(baseline_windows),
        "cosine_distribution": cosine_distribution(cosines),
        "per_window": _per_window_series(cosines),
    }


def run_n_way(
    target_windows: list[str],
    candidate_windows: list[str],
    impostor_windows_by_name: dict[str, list[str]],
    encoder: StyleEncoder,
) -> dict[str, Any]:
    """N-way mode: target vs. candidate baseline vs. impostor pool.

    The embedding ANALOGUE of general_imposters' framing — descriptive
    only. Reports where the target's similarity to the candidate falls
    relative to its similarity to each impostor. NO win/loss
    proportion, NO threshold; the reader interprets the distributions.
    """
    if not target_windows:
        raise VoiceFingerprintError("n_way mode produced no target windows.")
    if not candidate_windows:
        raise VoiceFingerprintError(
            "n_way mode produced no candidate (baseline) windows; check "
            "--baseline-dir."
        )
    if not impostor_windows_by_name:
        raise VoiceFingerprintError(
            "n_way mode produced no impostor windows; check "
            "--impostor-dir contains readable .txt/.md prose."
        )
    target_emb = encoder.encode(target_windows)
    candidate_centroid = _centroid(encoder.encode(candidate_windows))

    target_vs_candidate = [
        _cosine(target_emb[i], candidate_centroid)
        for i in range(len(target_emb))
    ]

    # Per-impostor: target windows vs. that impostor's centroid.
    impostor_blocks: dict[str, dict[str, Any]] = {}
    pooled_impostor_cosines: list[float] = []
    for name, windows in impostor_windows_by_name.items():
        if not windows:
            continue
        centroid = _centroid(encoder.encode(windows))
        cos = [_cosine(target_emb[i], centroid) for i in range(len(target_emb))]
        impostor_blocks[name] = {
            "n_windows": len(windows),
            "cosine_distribution": cosine_distribution(cos),
        }
        pooled_impostor_cosines.extend(cos)

    return {
        "mode": "n_way",
        "n_windows": len(target_windows),
        "n_candidate_windows": len(candidate_windows),
        "n_impostors": len(impostor_blocks),
        # Headline distribution is the target's similarity to the
        # candidate baseline (what the surface is "about").
        "cosine_distribution": cosine_distribution(target_vs_candidate),
        "target_vs_candidate": {
            "cosine_distribution": cosine_distribution(target_vs_candidate),
            "per_window": _per_window_series(target_vs_candidate),
        },
        "target_vs_impostors": {
            "pooled_cosine_distribution": cosine_distribution(
                pooled_impostor_cosines
            ),
            "per_impostor": impostor_blocks,
        },
    }


# --------------- Claim license -----------------------------------


# Short-text fragility and cross-model incomparability apply to EVERY
# encoder, so they are appended after the per-encoder content-control
# caveat. The cross-model caveat enumerates ALL FOUR encoders (spec 28
# [P1] folded: it must not stay "LUAR and Wegmann" once styledistance /
# muar are selectable).
_SHORT_TEXT_CAVEAT = (
    "Short-text fragility: style embeddings are unstable on "
    "short windows. Treat per-window cosines from <~100-token "
    "windows as noisy; prefer the distribution over any single "
    "window's value."
)
_CROSS_MODEL_CAVEAT = (
    "Cross-model incomparability: cosines from LUAR, Wegmann, "
    "StyleDistance and mUAR (or any two models) are NOT directly "
    "comparable. Compare like model to like model. The recorded "
    "`model_id` is the provenance that makes a later cross-encoder "
    "comparison flag itself rather than silently mix manifolds."
)

# Per-encoder content-control caveat. The refactor (spec 28 [P1]) turns
# the formerly-STATIC additional_caveats block into a model_id-keyed
# branch — WITHOUT touching licenses / does_not_license / the refusal
# strings (those stay byte-for-byte; a test asserts it across encoders).
# The topic-leakage / content-control caveat is REWORDED per encoder,
# NEVER retired ("content-independent" is a training claim, not a
# topic-proof guarantee).
_LUAR_CONTENT_CAVEAT = (
    "Content control: LUAR (`rrivera1849/LUAR-MUD`) is trained "
    "on Reddit / social-media authorship data, so its style "
    "manifold carries REGISTER SKEW — cosines between two "
    "registers (e.g., literary fiction vs. an email) may read "
    "as 'divergent' on topic/register grounds rather than "
    "authorship. The Wegmann (`AnnaWegmann/Style-Embedding`) "
    "cross-check is more content-controlled but, per its own "
    "STEL analysis, captures mostly punctuation / casing / "
    "contraction style — a narrower notion of voice."
)
_WEGMANN_CONTENT_CAVEAT = (
    "Content control: Wegmann (`AnnaWegmann/Style-Embedding`) is "
    "more content-controlled than LUAR but, per its own STEL "
    "analysis, captures mostly punctuation / casing / contraction "
    "style — a NARROWER notion of voice. A high cosine reflects "
    "agreement on that narrow style axis, not authorship; treat "
    "register / topic as a live confound."
)
_STYLEDISTANCE_CONTENT_CAVEAT = (
    "Content control: StyleDistance (`StyleDistance/styledistance`, "
    "arXiv:2410.12757) is trained on synthetic near-paraphrases that "
    "vary STYLE while holding CONTENT fixed, so its manifold is MORE "
    "content-controlled than LUAR's. This is a TRAINING property, NOT "
    "a topic-proof guarantee: it is trained to SUPPRESS content, not "
    "freed of it — a topic / register change can still read as voice "
    "distance, so the register-skew confound is reduced, not retired."
)
_MUAR_CONTENT_CAVEAT = (
    "Content control: mUAR (`rrivera1849/mUAR`, arXiv:2509.16531) is a "
    "MULTILINGUAL authorship-representation manifold. Its cosines are "
    "WITHIN-ENCODER (model-bound) and carry register / topic leakage "
    "like any learned manifold. Multilingual representation is a "
    "CAPABILITY, not a LICENSE: it does NOT by itself license "
    "cross-language comparison — that refusal is a claim-license "
    "commitment, separate, calibrated and explicitly-flagged, never the "
    "silent default of an encoder swap."
)


def _content_control_caveat(model_id: str) -> str:
    """Pick the per-encoder content-control caveat by resolved
    ``model_id`` (or its alias). Falls back to the LUAR caveat for any
    unrecognized id so an explicit full-HF-id invocation still prints a
    content-control warning rather than none."""
    if model_id in ("styledistance", MODEL_ALIASES["styledistance"]):
        return _STYLEDISTANCE_CONTENT_CAVEAT
    if model_id in ("muar", MODEL_ALIASES["muar"]):
        return _MUAR_CONTENT_CAVEAT
    if model_id in ("wegmann", MODEL_ALIASES["wegmann"]):
        return _WEGMANN_CONTENT_CAVEAT
    return _LUAR_CONTENT_CAVEAT


def _claim_license(*, model_id: str, mode: str) -> ClaimLicense:
    """Structured ClaimLicense for the style-embedding surface.

    LICENSES the cosine-distance reading under model M's learned style
    manifold; REFUSES "same person", "different author", and the
    "AI-generated or human-written" call. States the content-control
    caveat (per-encoder: LUAR register skew / Wegmann punctuation-casing
    / StyleDistance synthetic-paraphrase / mUAR multilingual) and
    short-text fragility.

    The ``additional_caveats`` block is the ONLY per-encoder-conditional
    part (spec 28 [P1] refactor). ``licenses`` and ``does_not_license``
    — including the refusal strings "SAME PERSON" / "DIFFERENT AUTHOR" /
    "AI-generated or human-written" — are IDENTICAL across every encoder
    (a test asserts byte-equality), so no encoder "earns" a verdict.
    """
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "These passages are stylistically consistent / divergent "
            "at the reported cosine distance D under model "
            f"`{model_id}`'s learned style manifold. Reports the "
            "cosine-similarity DISTRIBUTION (mean, sd, min, p10, p50, "
            "p90) across windows — internal consistency (single), "
            "consistency with a baseline voice centroid (two-corpus), "
            "or where the target's candidate-similarity falls relative "
            "to an impostor pool (n-way, descriptive only)."
        ),
        does_not_license=(
            "That these passages are by the SAME PERSON. That they are "
            "by a DIFFERENT AUTHOR. That the text is AI-generated or "
            "human-written. The surface emits no binary call and no "
            "threshold: a high cosine is consistency evidence on this "
            "model's manifold, not an identity verdict; a low cosine is "
            "divergence evidence, not a different-author verdict. The "
            "n-way mode reports distributions, NOT a general-imposters "
            "win/loss proportion — it is the embedding analogue, not a "
            "GI replacement."
        ),
        comparison_set={
            "model_id": model_id,
            "mode": mode,
            "calibration_status": (
                "PROVISIONAL — cosine distances are absolute "
                "measurements; any consistent/divergent banding is "
                "illustrative and uncalibrated. Calibration (a "
                "per-register impostor-pool study giving an empirical "
                "same-author cosine distribution) is future work."
            ),
        },
        additional_caveats=[
            # Per-encoder content-control caveat (spec 28 [P1] refactor):
            # the formerly-static "LUAR + Wegmann" string is now branched
            # on model_id. Short-text + cross-model caveats apply to all.
            _content_control_caveat(model_id),
            _SHORT_TEXT_CAVEAT,
            _CROSS_MODEL_CAVEAT,
        ],
        references=[
            "specs/02-voice-fingerprint-embedding.md",
            "specs/28-styledistance-encoder-upgrade.md",
            "LUAR — Rivera-Soto et al., EMNLP 2021 "
            "(https://aclanthology.org/2021.emnlp-main.70/); weights "
            "rrivera1849/LUAR-MUD (Apache-2.0).",
            "Wegmann et al. 2022, 'Same Author or Just Same Topic?' "
            "(https://aclanthology.org/2022.repl4nlp-1.26/); weights "
            "AnnaWegmann/Style-Embedding.",
            "StyleDistance — content-independent style embeddings via "
            "synthetic near-paraphrase contrastive training "
            "(arXiv:2410.12757); weights StyleDistance/styledistance.",
            "mUAR — Multilingual Universal Authorship Representation "
            "(arXiv:2509.16531); weights rrivera1849/mUAR.",
        ],
    )


# --------------- Output assembly ---------------------------------


def assemble_output(
    *,
    target_path: Path | str | None,
    target_text: str,
    mode: str,
    model: str,
    window_strategy: str,
    window_size: int,
    encoder: StyleEncoder,
    baseline_texts: list[str] | None = None,
    impostor_texts_by_name: dict[str, list[str]] | None = None,
    baseline_n_files: int = 0,
    impostor_n_files: int = 0,
) -> dict[str, Any]:
    """Run the requested mode and build the schema_version 1.0
    envelope. Pure-ish: all I/O (file reads, encoder load) is done by
    the caller / injected encoder, so this is exercised directly by
    tests with a stub."""
    target_windows = split_windows(
        target_text, window_strategy, window_size=window_size,
    )
    model_id = MODEL_ALIASES.get(model, model)

    if mode == "single":
        results = run_single(target_windows, encoder)
    elif mode == "two_corpus":
        baseline_windows = _window_corpus(
            baseline_texts or [], window_strategy, window_size,
        )
        results = run_two_corpus(target_windows, baseline_windows, encoder)
    elif mode == "n_way":
        candidate_windows = _window_corpus(
            baseline_texts or [], window_strategy, window_size,
        )
        impostor_windows_by_name = {
            name: _window_corpus(texts, window_strategy, window_size)
            for name, texts in (impostor_texts_by_name or {}).items()
        }
        results = run_n_way(
            target_windows, candidate_windows,
            impostor_windows_by_name, encoder,
        )
    else:  # pragma: no cover - guarded by argparse choices
        raise VoiceFingerprintError(f"Unknown mode {mode!r}")

    results["model_id"] = model_id
    results["windowing"] = {
        "strategy": window_strategy,
        "window_size": (
            window_size if window_strategy == "fixed-token" else None
        ),
    }

    target_words = _approx_token_count(target_text)

    baseline_meta: dict[str, Any] | None = None
    if mode in ("two_corpus", "n_way"):
        baseline_words = sum(
            _approx_token_count(t) for t in (baseline_texts or [])
        )
        extra: dict[str, Any] = {"role": (
            "candidate" if mode == "n_way" else "voice_centroid_source"
        )}
        if mode == "n_way":
            extra["n_impostor_files"] = impostor_n_files
            extra["impostor_words"] = sum(
                _approx_token_count(t)
                for texts in (impostor_texts_by_name or {}).values()
                for t in texts
            )
        baseline_meta = build_baseline_metadata(
            n_files=baseline_n_files,
            words=baseline_words,
            extra=extra,
        )

    lic = _claim_license(model_id=model_id, mode=mode)

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=baseline_meta,
        results=results,
        claim_license=lic,
    )


# --------------- CLI ---------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="voice_fingerprint",
        description=(
            "Same-author style-embedding surface. Embeds passages with "
            "a frozen style encoder (LUAR default; Wegmann optional) "
            "and reports the cosine-similarity DISTRIBUTION across "
            "windows. Refuses any identity / AI verdict; ships "
            "PROVISIONAL under SETEC's no-threshold posture."
        ),
    )
    p.add_argument(
        "target",
        type=str,
        help="path to a UTF-8 text file (the target passage / draft)",
    )
    p.add_argument(
        "--baseline-dir",
        type=str,
        default=None,
        help=(
            "directory of .txt/.md files. Required for two-corpus mode "
            "(target windows vs. baseline voice centroid) and as the "
            "candidate corpus in n-way mode."
        ),
    )
    p.add_argument(
        "--impostor-dir",
        type=str,
        default=None,
        help=(
            "directory of .txt/.md files forming the impostor pool. "
            "Triggers n-way mode (requires --baseline-dir as the "
            "candidate). Descriptive only — no GI win/loss proportion."
        ),
    )
    p.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=(
            "style encoder: alias `luar` (default; "
            "rrivera1849/LUAR-MUD, Apache-2.0), `wegmann` "
            "(AnnaWegmann/Style-Embedding, via sentence-transformers), "
            "`styledistance` (more content-independent, arXiv:2410.12757), "
            "`muar` (multilingual, arXiv:2509.16531), or a full "
            "HuggingFace id. Cross-encoder cosines are NOT comparable. "
            "Default: %(default)s."
        ),
    )
    p.add_argument(
        "--window-strategy",
        type=str,
        choices=("paragraph", "fixed-token"),
        default="paragraph",
        help="how to split text into windows (default: %(default)s)",
    )
    p.add_argument(
        "--window-size",
        type=int,
        default=200,
        help=(
            "token count for --window-strategy fixed-token "
            "(ignored otherwise; default: %(default)s)"
        ),
    )
    p.add_argument(
        "--device",
        type=str,
        default=None,
        help=(
            "explicit device for the encoder (e.g. cuda:0). Default: "
            "let the backend pick."
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON (the only output format this surface emits)",
    )
    p.add_argument(
        "--out",
        type=str,
        default=None,
        help="write JSON output to this path (defaults to stdout)",
    )
    return p


def _resolve_mode(args: argparse.Namespace) -> str:
    """Determine the mode from the supplied directories.

    Errors clearly when --impostor-dir is given without --baseline-dir
    (n-way needs a candidate)."""
    if args.impostor_dir:
        if not args.baseline_dir:
            raise VoiceFingerprintError(
                "n-way mode (--impostor-dir) requires --baseline-dir as "
                "the candidate corpus."
            )
        return "n_way"
    if args.baseline_dir:
        return "two_corpus"
    return "single"


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)

    target = Path(args.target).expanduser()
    if not target.exists():
        sys.stderr.write(f"Target path not found: {target}\n")
        return 2
    target_text = target.read_text(encoding="utf-8", errors="ignore")

    try:
        mode = _resolve_mode(args)

        baseline_texts: list[str] | None = None
        baseline_n_files = 0
        impostor_texts_by_name: dict[str, list[str]] | None = None
        impostor_n_files = 0

        if mode in ("two_corpus", "n_way"):
            pairs = _load_dir_texts(args.baseline_dir)
            baseline_texts = [t for _, t in pairs]
            baseline_n_files = len(pairs)
        if mode == "n_way":
            imp_pairs = _load_dir_texts(args.impostor_dir)
            impostor_texts_by_name = {name: [t] for name, t in imp_pairs}
            impostor_n_files = len(imp_pairs)

        # Load the encoder LAST, after cheap validation, so a clean
        # error (missing dir, bad mode) doesn't pay the import cost.
        encoder = _load_encoder(args.model, device=args.device)

        envelope = assemble_output(
            target_path=target,
            target_text=target_text,
            mode=mode,
            model=args.model,
            window_strategy=args.window_strategy,
            window_size=args.window_size,
            encoder=encoder,
            baseline_texts=baseline_texts,
            impostor_texts_by_name=impostor_texts_by_name,
            baseline_n_files=baseline_n_files,
            impostor_n_files=impostor_n_files,
        )
    except VoiceFingerprintError as exc:
        sys.stderr.write(f"{exc}\n")
        return 3

    rendered = json.dumps(envelope, indent=2, default=str)
    if args.out:
        Path(args.out).expanduser().write_text(
            rendered + "\n", encoding="utf-8",
        )
    else:
        print(rendered)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
