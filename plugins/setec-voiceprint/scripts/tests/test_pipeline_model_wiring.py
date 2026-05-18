#!/usr/bin/env python3
"""Tests for the 1.80.0 pipeline-wiring PR: pluggable embedding
model (Tier 3) and Tier 4 surprisal-model threading through the
sharded calibration scoring pipeline.

Two wiring gaps closed here, tested together because they ride the
same plumbing change:

  * **W1** — ``embedding_backend.py`` shipped a pluggable Tier 3
    embedding wrapper with 4 aliases (mxbai, gemma, harrier, minilm)
    but ``variance_audit.adjacent_sentence_cosine`` was hardcoded to
    ``sentence-transformers (all-MiniLM-L6-v2)``. ``shard_runner
    shard`` accepted ``--embedding-model`` and recorded it in
    ``state.json`` but no scorer read it.
  * **W2** — Tier 4 (surprisal) worked in standalone
    ``variance_audit.py --tier4`` but no calibration-pipeline entry
    point — ``calibration_survey.py``, ``shard_runner shard``,
    ``validation_harness.score_smoothing_entry`` — accepted ``--tier4``
    or ``--surprisal-model``. MAGE / RAID Tier 4 calibration would
    have needed a parallel ad-hoc scoring loop.

This test module pins both fixes by walking up the chain:

  1. ``variance_audit.adjacent_sentence_cosine`` honors the new
     ``embedding_model`` kwarg and the legacy (no kwarg) path still
     produces the pre-1.80 method string for back-compat.
  2. ``variance_audit.audit_text`` threads ``embedding_model`` /
     ``surprisal_model`` / ``do_tier4`` down to the leaf functions.
  3. ``validation_harness.score_smoothing_entry`` accepts and forwards
     the new kwargs.
  4. ``calibrate_thresholds.cache_is_compatible`` invalidates the
     cache on any of the new fields mismatching.
  5. ``shard_runner shard`` accepts ``--tier4`` /
     ``--surprisal-model`` / ``--surprisal-revision`` and writes them
     to ``state.json["task_params"]``.
  6. ``task_surfaces._score_shard_calibration_survey`` reads the new
     fields from ``task_params`` and forwards them to ``DEFAULT_SCORER``.

The tests deliberately avoid actually loading any embedding or
surprisal model — that would download GB of weights and slow the
suite to a crawl. Model behavior is exercised in the dedicated
backend modules' own test suites (``test_embedding_backend.py``,
``test_surprisal_backend.py``); this module's job is to pin the
wiring contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "calibration") not in sys.path:
    sys.path.insert(0, str(ROOT / "calibration"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# --------------- W1: variance_audit Tier 3 embedding ----------


def test_variance_audit_cli_exposes_embedding_model_flag():
    """``--embedding-model`` and ``--embedding-revision`` are wired
    on the variance_audit CLI. Default is None (legacy MiniLM path).
    Spawn variance_audit as a subprocess since its ``main()`` reads
    ``sys.argv`` and there's no in-process arg-injection hook.
    """
    import subprocess
    result = subprocess.run(
        [sys.executable, str(ROOT / "variance_audit.py"), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert "--embedding-model" in result.stdout
    assert "--embedding-revision" in result.stdout


def test_adjacent_sentence_cosine_legacy_path_unchanged():
    """Without ``embedding_model``, falls through to the legacy
    MiniLM hardcode → TF-IDF chain. The method string preserves the
    pre-1.80 'sentence-transformers (all-MiniLM-L6-v2)' literal so
    pre-1.80 cached surveys remain bit-comparable."""
    import variance_audit as va  # type: ignore

    sentences = ["A short sentence.", "Another short sentence."]
    # We don't actually need ST loaded; the call should at minimum
    # return None or a dict whose method names MiniLM (when ST
    # works) or tfidf-cosine (sklearn fallback). The contract under
    # test: the method string does NOT name mxbai/gemma/harrier
    # since we didn't pass embedding_model.
    result = va.adjacent_sentence_cosine(sentences)
    if result is None:
        # ST and sklearn both missing — acceptable for the back-compat
        # contract (legacy path may fall through to None on a fresh
        # CI machine without optional deps).
        return
    method = result.get("method", "")
    assert "mxbai" not in method
    assert "gemma" not in method
    assert "harrier" not in method
    # Either MiniLM (ST present) or tfidf-cosine (sklearn fallback).
    assert "MiniLM" in method or "tfidf" in method


try:
    import numpy as _np  # type: ignore  # noqa: F401
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

_skip_no_numpy = pytest.mark.skipif(
    not _HAS_NUMPY,
    reason=(
        "numpy not installed; adjacent_sentence_cosine's new-path "
        "and audit_text Tier-3 spy tests need numpy for the per-pair "
        "dot-product math the function does after the spy/stub returns"
    ),
)


@_skip_no_numpy
def test_adjacent_sentence_cosine_new_path_uses_embedding_backend(monkeypatch):
    """When ``embedding_model="mxbai"`` is passed, the new path
    delegates to ``embedding_backend.EmbeddingBackend``. We stub
    the backend to avoid loading a real model and verify the method
    string echoes the resolved model id + the cache returns the
    same backend across calls."""
    import variance_audit as va  # type: ignore

    # Stub backend that returns a fixed embedding array.
    class _StubBackend:
        def __init__(self, model_id, revision=None, deterministic=True):
            self.model_id = model_id
            self.revision = revision

        def encode(self, texts):
            import numpy as np  # type: ignore
            # Deterministic per-text vectors so cosine is computable
            # but not all 1.0 (which would make sd undefined).
            return np.array(
                [[float(i + 1), float(i + 2), float(i + 3)] for i in range(len(texts))],
                dtype="float32",
            )

        def identifier_block(self):
            return {
                "id": self.model_id,
                "revision": self.revision,
                "alias": "mxbai" if "mxbai" in self.model_id else None,
                "deterministic_mode": True,
                "method": "sentence-transformers",
            }

    # Reset the per-(alias, revision) cache so this test sees a fresh
    # construction, not a leftover from another test.
    va._EMBEDDING_BACKENDS_CACHE.clear()

    # Patch _get_embedding_backend to return the stub regardless of
    # whether embedding_backend is actually importable on this machine.
    real_get = va._get_embedding_backend

    def _stub_get(model_alias, revision=None):
        if model_alias is None:
            return None
        key = (model_alias, revision or "")
        cached = va._EMBEDDING_BACKENDS_CACHE.get(key)
        if cached is not None:
            return cached
        # Build a stub with the "resolved" id matching what the
        # real resolver would produce for the mxbai alias.
        backend = _StubBackend(model_id="mixedbread-ai/mxbai-embed-large-v1")
        va._EMBEDDING_BACKENDS_CACHE[key] = backend
        return backend

    monkeypatch.setattr(va, "_get_embedding_backend", _stub_get)

    sentences = ["Sentence one.", "Sentence two.", "Sentence three."]
    result = va.adjacent_sentence_cosine(
        sentences, embedding_model="mxbai",
    )
    assert result is not None
    assert result["embedding_model"] == "mixedbread-ai/mxbai-embed-large-v1"
    assert result["embedding_alias"] == "mxbai"
    assert "mxbai" in result["method"]
    assert result["n_pairs"] == 2  # 3 sentences → 2 adjacent pairs

    # Second call should hit the cache (no new construction).
    result2 = va.adjacent_sentence_cosine(
        sentences, embedding_model="mxbai",
    )
    assert result2 is not None
    # Cache key is (alias, revision); both calls used the same key.
    assert len(va._EMBEDDING_BACKENDS_CACHE) == 1


def test_audit_text_threads_embedding_model_through(monkeypatch):
    """``audit_text(..., embedding_model="X")`` propagates ``X``
    into the adjacent_cosine call. We spy via monkeypatch.

    The Tier-3 path inside ``audit_text`` is gated on
    ``HAS_ST or HAS_SKLEARN``; we patch ``HAS_ST=True`` so the gate
    opens regardless of host deps. The spy is a complete replacement
    for ``adjacent_sentence_cosine``, so it never reaches the real
    function's internal ``import numpy``; the gate-patch is enough.
    """
    import variance_audit as va  # type: ignore

    captured: dict[str, Any] = {}
    real_fn = va.adjacent_sentence_cosine

    def _spy(sentences, *, embedding_model=None, embedding_revision=None):
        captured["embedding_model"] = embedding_model
        captured["embedding_revision"] = embedding_revision
        # Return a minimal valid dict so the rest of audit_text proceeds.
        return {
            "method": "stub", "n_pairs": 1, "mean": 0.5, "sd": 0.0,
            "min": 0.5, "max": 0.5,
        }

    monkeypatch.setattr(va, "HAS_ST", True)
    with mock.patch.object(va, "adjacent_sentence_cosine", _spy):
        va.audit_text(
            "Sentence one. " * 60,  # enough words to pass the 50-word floor
            do_tier2=False,
            do_tier3=True,
            embedding_model="mxbai",
            embedding_revision="abc123",
        )

    assert captured["embedding_model"] == "mxbai"
    assert captured["embedding_revision"] == "abc123"


# --------------- W2: variance_audit Tier 4 surprisal ----------


def test_audit_text_threads_surprisal_model_through():
    """``audit_text(..., do_tier4=True, surprisal_model="X")`` forwards
    ``X`` into _tier4_surprisal_block."""
    import variance_audit as va  # type: ignore

    captured: dict[str, Any] = {}

    def _spy_tier4(text, *, score_fn=None, backend=None,
                   surprisal_model=None, surprisal_revision=None,
                   surprisal_dtype="auto",
                   sliding_window=False, window_size=200, stride=100,
                   top_k=20):
        captured["surprisal_model"] = surprisal_model
        captured["surprisal_revision"] = surprisal_revision
        captured["surprisal_dtype"] = surprisal_dtype
        return {"available": True, "surprisal": {"mean": 0.0, "sd": 0.0}}

    with mock.patch.object(va, "_tier4_surprisal_block", _spy_tier4):
        va.audit_text(
            "Sentence one. " * 60,
            do_tier2=False,
            do_tier3=False,
            do_tier4=True,
            surprisal_model="gpt2",
            surprisal_revision="def456",
        )

    assert captured["surprisal_model"] == "gpt2"
    assert captured["surprisal_revision"] == "def456"


def test_audit_text_threads_surprisal_dtype_through():
    """``audit_text(..., do_tier4=True, surprisal_dtype="bf16")``
    forwards the dtype into _tier4_surprisal_block. Pins the per-
    entry fallback path: when the batched backend has latched off
    and the loop falls back to per-row audit_text calls, the
    operator's --surprisal-dtype is honored, not silently dropped
    to the SurprisalBackend default of "auto"."""
    import variance_audit as va  # type: ignore

    captured: dict[str, Any] = {}

    def _spy_tier4(text, *, score_fn=None, backend=None,
                   surprisal_model=None, surprisal_revision=None,
                   surprisal_dtype="auto",
                   sliding_window=False, window_size=200, stride=100,
                   top_k=20):
        captured["surprisal_dtype"] = surprisal_dtype
        return {"available": True, "surprisal": {"mean": 0.0, "sd": 0.0}}

    with mock.patch.object(va, "_tier4_surprisal_block", _spy_tier4):
        va.audit_text(
            "Sentence one. " * 60,
            do_tier2=False,
            do_tier3=False,
            do_tier4=True,
            surprisal_model="gpt2",
            surprisal_dtype="bf16",
        )

    assert captured["surprisal_dtype"] == "bf16"


def test_get_surprisal_backend_caches_by_dtype():
    """``_get_surprisal_backend`` returns separate instances for
    different dtypes on the same (model, revision). Without this,
    a per-entry fallback that asked for bf16 could silently get
    back an fp32 backend cached from an earlier call. The cache
    key explicitly includes dtype so the two coexist."""
    import variance_audit as va  # type: ignore

    captured_constructions: list[dict] = []

    class _FakeSurprisalBackend:
        def __init__(self, model_id, revision=None, dtype="auto"):
            self.model_id = model_id
            self.revision = revision
            self.dtype = dtype
            captured_constructions.append({
                "model_id": model_id,
                "revision": revision,
                "dtype": dtype,
            })

    def _fake_resolve(arg):
        return arg if arg else "tinyllama"

    # Reset cache for hermetic per-test state.
    va._SURPRISAL_BACKENDS_CACHE.clear()

    fake_module = mock.MagicMock()
    fake_module.SurprisalBackend = _FakeSurprisalBackend
    fake_module.resolve_model_arg = _fake_resolve

    with mock.patch.dict(
        sys.modules, {"surprisal_backend": fake_module},
    ):
        b_fp32_a = va._get_surprisal_backend("tinyllama", None, "fp32")
        b_fp32_b = va._get_surprisal_backend("tinyllama", None, "fp32")
        b_bf16 = va._get_surprisal_backend("tinyllama", None, "bf16")
        b_auto = va._get_surprisal_backend("tinyllama", None, "auto")

    # Same dtype → same instance (cached).
    assert b_fp32_a is b_fp32_b
    # Different dtypes → different instances.
    assert b_fp32_a is not b_bf16
    assert b_fp32_a is not b_auto
    assert b_bf16 is not b_auto
    # Three constructions total (fp32 once, bf16 once, auto once).
    assert len(captured_constructions) == 3
    constructed_dtypes = sorted(c["dtype"] for c in captured_constructions)
    assert constructed_dtypes == ["auto", "bf16", "fp32"]


# --------------- W1 + W2: validation_harness threading ----------


def test_score_smoothing_entry_accepts_new_kwargs():
    """``score_smoothing_entry`` accepts the 1.80.0 kwargs and
    threads them into ``audit_text``."""
    import validation_harness as vh  # type: ignore

    captured: dict[str, Any] = {}

    def _spy_audit(text, **kwargs):
        captured.update(kwargs)
        return {
            "summary": {"n_words": 100, "n_words_original": 100,
                        "n_sentences": 5, "reliable": True,
                        "preprocessing_applied": False},
            "preprocessing": {"applied": False},
            "tier1": {},
        }

    # Patch validation_harness's bound name so the import-time binding
    # at the top of validation_harness.py is replaced.
    with mock.patch.object(vh, "audit_text", _spy_audit), \
         mock.patch("pathlib.Path.read_text", return_value="word " * 200):
        vh.score_smoothing_entry(
            {"id": "x", "path": "/tmp/x.txt", "_resolved_path": "/tmp/x.txt",
             "ai_status": "human"},
            positive_statuses={"ai_generated"},
            negative_statuses={"human"},
            do_tier2=False,
            do_tier3=True,
            do_tier4=True,
            embedding_model="mxbai",
            embedding_revision="abc",
            surprisal_model="gpt2",
            surprisal_revision="def",
        )

    assert captured.get("do_tier4") is True
    assert captured.get("embedding_model") == "mxbai"
    assert captured.get("embedding_revision") == "abc"
    assert captured.get("surprisal_model") == "gpt2"
    assert captured.get("surprisal_revision") == "def"


def test_score_smoothing_entry_threads_surprisal_dtype_through():
    """``score_smoothing_entry(..., surprisal_dtype="bf16")`` forwards
    the dtype to ``audit_text``. Pins the per-entry path: when
    score_corpus has fallen back from the batched-Tier-4 path to per-
    row scoring, the dtype the operator chose still reaches the
    underlying SurprisalBackend construction."""
    import validation_harness as vh  # type: ignore

    captured: dict[str, Any] = {}

    def _spy_audit(text, **kwargs):
        captured.update(kwargs)
        return {
            "summary": {"n_words": 100, "n_words_original": 100,
                        "n_sentences": 5, "reliable": True,
                        "preprocessing_applied": False},
            "preprocessing": {"applied": False},
            "tier1": {},
        }

    with mock.patch.object(vh, "audit_text", _spy_audit), \
         mock.patch("pathlib.Path.read_text", return_value="word " * 200):
        vh.score_smoothing_entry(
            {"id": "x", "path": "/tmp/x.txt", "_resolved_path": "/tmp/x.txt",
             "ai_status": "human"},
            positive_statuses={"ai_generated"},
            negative_statuses={"human"},
            do_tier2=False,
            do_tier3=False,
            do_tier4=True,
            surprisal_model="gpt2",
            surprisal_dtype="bf16",
        )

    assert captured.get("surprisal_dtype") == "bf16"


def test_score_smoothing_entry_default_surprisal_dtype_is_auto():
    """Default kwarg preserves pre-1.93 caller behavior: callers
    that don't pass surprisal_dtype get the "auto" default routed
    through to audit_text."""
    import validation_harness as vh  # type: ignore

    captured: dict[str, Any] = {}

    def _spy_audit(text, **kwargs):
        captured.update(kwargs)
        return {
            "summary": {"n_words": 100, "n_words_original": 100,
                        "n_sentences": 5, "reliable": True,
                        "preprocessing_applied": False},
            "preprocessing": {"applied": False},
            "tier1": {},
        }

    with mock.patch.object(vh, "audit_text", _spy_audit), \
         mock.patch("pathlib.Path.read_text", return_value="word " * 200):
        vh.score_smoothing_entry(
            {"id": "x", "path": "/tmp/x.txt", "_resolved_path": "/tmp/x.txt",
             "ai_status": "human"},
            positive_statuses={"ai_generated"},
            negative_statuses={"human"},
            do_tier4=True,
            surprisal_model="gpt2",
        )

    assert captured.get("surprisal_dtype") == "auto"


# --------------- W1 + W2: calibrate_thresholds cache compat ----------


def test_cache_compat_invalidates_on_embedding_model_change():
    """``cache_is_compatible`` refuses a cache whose embedding_model
    differs from the current args."""
    import calibrate_thresholds as ct  # type: ignore

    cache_meta = {
        "manifest_sha256": "abc",
        "corpus_text_fingerprint": "def",
        "use": "validation",
        "do_tier2": True,
        "do_tier3": True,
        "do_tier4": False,
        "embedding_model": "mxbai",
        "embedding_revision": None,
        "surprisal_model": None,
        "surprisal_revision": None,
        "scorer_version": ct.SCORER_CACHE_VERSION,
    }
    args = argparse.Namespace(
        manifest="x", use="validation", tier2=True, tier3=True,
        tier4=False,
        embedding_model="gemma",  # different from cached
        embedding_revision=None,
        surprisal_model=None, surprisal_revision=None,
        max_entries=None,
    )
    ok, reason = ct.cache_is_compatible(
        cache_meta, args, manifest_sha256="abc",
        corpus_text_fingerprint="def",
    )
    assert ok is False
    assert "embedding_model" in reason


def test_cache_compat_invalidates_on_tier4_change():
    """Toggling tier4 invalidates the cache."""
    import calibrate_thresholds as ct  # type: ignore

    cache_meta = {
        "manifest_sha256": "abc",
        "corpus_text_fingerprint": "def",
        "use": "validation",
        "do_tier2": True, "do_tier3": False, "do_tier4": False,
        "embedding_model": None, "embedding_revision": None,
        "surprisal_model": None, "surprisal_revision": None,
        "scorer_version": ct.SCORER_CACHE_VERSION,
    }
    args = argparse.Namespace(
        manifest="x", use="validation", tier2=True, tier3=False,
        tier4=True,  # toggled on
        embedding_model=None, embedding_revision=None,
        surprisal_model=None, surprisal_revision=None,
        max_entries=None,
    )
    ok, reason = ct.cache_is_compatible(
        cache_meta, args, manifest_sha256="abc",
        corpus_text_fingerprint="def",
    )
    assert ok is False
    assert "tier4" in reason


def test_cache_compat_invalidates_on_surprisal_model_change():
    """Changing surprisal_model invalidates the cache."""
    import calibrate_thresholds as ct  # type: ignore

    cache_meta = {
        "manifest_sha256": "abc",
        "corpus_text_fingerprint": "def",
        "use": "validation",
        "do_tier2": True, "do_tier3": False, "do_tier4": True,
        "embedding_model": None, "embedding_revision": None,
        "surprisal_model": "tinyllama", "surprisal_revision": None,
        "surprisal_dtype": "auto",
        "scorer_version": ct.SCORER_CACHE_VERSION,
    }
    args = argparse.Namespace(
        manifest="x", use="validation", tier2=True, tier3=False,
        tier4=True,
        embedding_model=None, embedding_revision=None,
        surprisal_model="gpt2",  # different
        surprisal_revision=None,
        surprisal_dtype="auto",
        max_entries=None,
    )
    ok, reason = ct.cache_is_compatible(
        cache_meta, args, manifest_sha256="abc",
        corpus_text_fingerprint="def",
    )
    assert ok is False
    assert "surprisal_model" in reason


def test_cache_compat_invalidates_on_surprisal_dtype_change():
    """Changing surprisal_dtype invalidates a Tier-4 cache. Without
    this check, a cache scored at fp32 would silently reuse on a
    later bf16 bake-off run — the per-token surprisal series differs
    at the ~0.1 bit/token level under different dtypes, which leaks
    into the framework's signal-level statistics."""
    import calibrate_thresholds as ct  # type: ignore

    cache_meta = {
        "manifest_sha256": "abc",
        "corpus_text_fingerprint": "def",
        "use": "validation",
        "do_tier2": True, "do_tier3": False, "do_tier4": True,
        "embedding_model": None, "embedding_revision": None,
        "surprisal_model": "tinyllama", "surprisal_revision": None,
        "surprisal_dtype": "fp32",
        "scorer_version": ct.SCORER_CACHE_VERSION,
    }
    args = argparse.Namespace(
        manifest="x", use="validation", tier2=True, tier3=False,
        tier4=True,
        embedding_model=None, embedding_revision=None,
        surprisal_model="tinyllama", surprisal_revision=None,
        surprisal_dtype="bf16",  # different
        max_entries=None,
    )
    ok, reason = ct.cache_is_compatible(
        cache_meta, args, manifest_sha256="abc",
        corpus_text_fingerprint="def",
    )
    assert ok is False
    assert "surprisal_dtype" in reason


def test_cache_compat_invalidates_on_missing_dtype_for_tier4_cache():
    """A Tier-4 cache that predates 1.93.0 dtype tracking lacks the
    ``surprisal_dtype`` field. Prefer re-scoring over treating the
    missing field as "auto" — the operator might have been on fp32-
    only CPU when the cache was scored but is now on a bf16 cuda
    host, and reusing the old series would silently mix dtypes."""
    import calibrate_thresholds as ct  # type: ignore

    cache_meta = {
        "manifest_sha256": "abc",
        "corpus_text_fingerprint": "def",
        "use": "validation",
        "do_tier2": True, "do_tier3": False, "do_tier4": True,
        "embedding_model": None, "embedding_revision": None,
        "surprisal_model": "tinyllama", "surprisal_revision": None,
        # No "surprisal_dtype" key — pre-1.93 Tier-4 cache.
        "scorer_version": ct.SCORER_CACHE_VERSION,
    }
    args = argparse.Namespace(
        manifest="x", use="validation", tier2=True, tier3=False,
        tier4=True,
        embedding_model=None, embedding_revision=None,
        surprisal_model="tinyllama", surprisal_revision=None,
        surprisal_dtype="auto",
        max_entries=None,
    )
    ok, reason = ct.cache_is_compatible(
        cache_meta, args, manifest_sha256="abc",
        corpus_text_fingerprint="def",
    )
    assert ok is False
    assert "surprisal_dtype" in reason
    assert "pre-1.93" in reason


def test_cache_compat_missing_dtype_is_ok_when_tier4_off():
    """When Tier 4 is off no surprisal scoring happened, so the
    missing ``surprisal_dtype`` field on a pre-1.93 cache is
    irrelevant. The cache stays compatible for the non-Tier-4
    bake-off / threshold-fitting paths."""
    import calibrate_thresholds as ct  # type: ignore

    cache_meta = {
        "manifest_sha256": "abc",
        "corpus_text_fingerprint": "def",
        "use": "validation",
        "do_tier2": True, "do_tier3": True, "do_tier4": False,
        "embedding_model": None, "embedding_revision": None,
        "surprisal_model": None, "surprisal_revision": None,
        # No surprisal_dtype, no Tier 4 — fine.
        "scorer_version": ct.SCORER_CACHE_VERSION,
    }
    args = argparse.Namespace(
        manifest="x", use="validation", tier2=True, tier3=True,
        tier4=False,
        embedding_model=None, embedding_revision=None,
        surprisal_model=None, surprisal_revision=None,
        surprisal_dtype="auto",
        max_entries=None,
    )
    ok, reason = ct.cache_is_compatible(
        cache_meta, args, manifest_sha256="abc",
        corpus_text_fingerprint="def",
    )
    assert ok is True, f"Expected compatible cache; got reason: {reason!r}"


def test_cache_compat_back_compat_with_pre_1_80_caches():
    """A pre-1.80 cache lacks the new fields; treated as
    ``None`` / ``False`` defaults. Compatibility holds when the
    current args also have ``None`` / ``False`` for these fields
    (the unchanged-default operator)."""
    import calibrate_thresholds as ct  # type: ignore

    # No do_tier4, no model fields at all.
    cache_meta = {
        "manifest_sha256": "abc",
        "corpus_text_fingerprint": "def",
        "use": "validation",
        "do_tier2": True, "do_tier3": True,
        "scorer_version": ct.SCORER_CACHE_VERSION,
    }
    args = argparse.Namespace(
        manifest="x", use="validation", tier2=True, tier3=True,
        # No tier4, no model fields. The getattr-with-default
        # pattern in cache_is_compatible should treat both sides
        # as equivalent.
        max_entries=None,
    )
    ok, reason = ct.cache_is_compatible(
        cache_meta, args, manifest_sha256="abc",
        corpus_text_fingerprint="def",
    )
    assert ok is True, f"pre-1.80 cache should be compatible; got: {reason}"


# --------------- W1 + W2: partial-cache resume preserves model fields ----------


def test_interim_flush_meta_carries_new_model_fields_so_resume_works(
    tmp_path, monkeypatch,
):
    """**Codex P2 on PR #77**: the in-progress checkpoint metadata
    written every ``flush_every`` entries must include the same five
    new fields (do_tier4, embedding_model, embedding_revision,
    surprisal_model, surprisal_revision) that the final scoring_meta
    write emits. Otherwise: a long --tier4 / --embedding-model run
    that crashes mid-loop, then is resumed, will read its own partial
    cache, fail cache_is_compatible (the partial's interim_meta lacks
    the new keys → mismatch against the current args' real model
    settings), and silently re-score from scratch.

    Pins by:
      1. Running a partial scoring loop with non-default
         embedding_model + tier4 + surprisal_model that triggers at
         least one flush_every checkpoint.
      2. Reading the partial cache from disk and asserting the 5 new
         fields are present and match the args.
      3. Re-invoking ``load_or_score_corpus`` with the same args and
         asserting the resume path engages (not re-score from scratch).
    """
    import calibrate_thresholds as ct  # type: ignore
    from test_incremental_corpus_scoring import (  # type: ignore
        _write_real_manifest, _make_args, _patch_scoring,
    )

    manifest = _write_real_manifest(tmp_path, n_entries=5)
    cache = tmp_path / "cache.json"
    args = _make_args(
        manifest,
        records_cache=str(cache),
        records_cache_flush_every=2,
    )
    # Add the 1.80 fields to the Namespace. The args helper produces
    # a pre-1.80 namespace; without these our test pretends to be a
    # legacy run and the bug doesn't show up.
    args.tier4 = True
    args.embedding_model = "mxbai"
    args.embedding_revision = "test-revision-sha"
    args.surprisal_model = "gpt2"
    args.surprisal_revision = "another-sha"

    # Build a stub scorer that returns minimal records and don't
    # actually load any models. The test is about metadata
    # propagation, not Tier 4 numerics.
    with _patch_scoring({}):
        records, _meta, _hit = ct.load_or_score_corpus(
            args, cache_path=cache,
        )
    assert len(records) == 5

    # Now flip the on-disk cache from complete to in_progress with
    # only 3 records (simulates a crash partway through) and verify
    # the cache_meta has the model fields. We do this by re-running
    # against a partial we craft ourselves — the same shape the
    # in-loop flush_every checkpoint produces.
    payload = json.loads(cache.read_text(encoding="utf-8"))
    cached_meta = payload.get("scoring_meta") or {}
    assert cached_meta.get("do_tier4") is True, (
        "completed scoring_meta is missing do_tier4 -- a regression "
        "in scoring_meta would mask this whole P2"
    )
    assert cached_meta.get("embedding_model") == "mxbai"
    assert cached_meta.get("surprisal_model") == "gpt2"

    # The real bug surface: the in-progress flush writes an
    # interim_meta that historically lacked these fields. Spy on
    # _save_score_cache to capture EVERY meta payload it writes,
    # then verify the in_progress ones carry the 5 fields just like
    # the complete one.
    flushed_metas: list[dict[str, Any]] = []
    real_save = ct._save_score_cache

    def _spy(path, scoring_meta, records, status):
        flushed_metas.append({"status": status, "meta": scoring_meta})
        return real_save(path, scoring_meta, records, status)

    monkeypatch.setattr(ct, "_save_score_cache", _spy)

    # Wipe the cache so we re-score from scratch and trigger the
    # in_progress flush path again.
    cache.unlink()

    with _patch_scoring({}):
        ct.load_or_score_corpus(args, cache_path=cache)

    in_progress_metas = [m for m in flushed_metas if m["status"] == "in_progress"]
    assert in_progress_metas, (
        "expected at least one in-progress flush at flush_every=2 / "
        "n=5; got " + str([m['status'] for m in flushed_metas])
    )

    # THE KEY ASSERTION: every in-progress meta carries the 5 new
    # fields. A regression here means an interrupted bake-off run
    # loses ALL its scoring work on resume.
    for entry in in_progress_metas:
        meta = entry["meta"]
        assert meta.get("do_tier4") is True, (
            "in-progress flush meta missing do_tier4; resume would "
            f"reject. Saw meta keys: {sorted(meta.keys())}"
        )
        assert meta.get("embedding_model") == "mxbai", (
            "in-progress flush meta missing embedding_model; resume "
            f"would reject. Saw embedding_model={meta.get('embedding_model')!r}"
        )
        assert meta.get("embedding_revision") == "test-revision-sha"
        assert meta.get("surprisal_model") == "gpt2"
        assert meta.get("surprisal_revision") == "another-sha"


def test_partial_cache_resume_round_trip_with_non_default_models(
    tmp_path,
):
    """End-to-end version of the codex P2 fix: write a partial cache
    that has the 5 new fields, re-invoke ``load_or_score_corpus``
    with matching args, assert the resume path engages (only the
    remaining entries are scored, not all of them)."""
    import calibrate_thresholds as ct  # type: ignore
    from test_incremental_corpus_scoring import (  # type: ignore
        _write_real_manifest, _make_args, _patch_scoring,
    )

    manifest = _write_real_manifest(tmp_path, n_entries=5)
    cache = tmp_path / "cache.json"
    args = _make_args(manifest, records_cache=str(cache))
    args.tier4 = True
    args.embedding_model = "mxbai"
    args.embedding_revision = None
    args.surprisal_model = "gpt2"
    args.surprisal_revision = None

    # First: full scoring run so we have a real scoring_meta to use as
    # the partial's prior-meta. (Otherwise we'd be hand-constructing
    # the meta and the test would just verify what we wrote.)
    counter = {"calls": 0}
    with _patch_scoring(counter):
        records, scoring_meta, _ = ct.load_or_score_corpus(
            args, cache_path=cache,
        )
    assert counter["calls"] == 5
    # Sanity: completed scoring_meta carries the 5 new fields. (If
    # this fails, the W1/W2 scoring_meta wiring regressed; the P2
    # test below wouldn't be meaningful.)
    assert scoring_meta.get("embedding_model") == "mxbai"

    # Truncate to first 3 and flip to in_progress. This is the shape
    # the in-loop flush would write IF the P2 fix is in place. Without
    # the fix, the meta would be missing the 5 new keys and the
    # resume below would fail compatibility check.
    partial = {
        "status": "in_progress",
        "scoring_meta": scoring_meta,
        "records": records[:3],
    }
    cache.write_text(json.dumps(partial, default=str))

    # Re-invoke with the same args. Resume should fire (2 fresh calls,
    # not 5).
    counter2 = {"calls": 0}
    with _patch_scoring(counter2):
        records2, _meta2, hit = ct.load_or_score_corpus(
            args, cache_path=cache,
        )
    assert hit is False, "partial cache must not register as a hit"
    assert counter2["calls"] == 2, (
        f"resume should have skipped 3 already-scored entries and "
        f"only re-scored the remaining 2; got {counter2['calls']} "
        f"fresh calls. If this fails, the in-loop interim_meta is "
        f"likely missing the new tier4/model fields (codex P2 on PR #77)."
    )
    assert len(records2) == 5


# --------------- shard_runner CLI surface ----------


def test_shard_runner_shard_parser_accepts_new_flags():
    """``shard_runner shard --tier4 --surprisal-model gpt2
    --embedding-model mxbai`` parses cleanly."""
    import shard_runner as sr  # type: ignore

    parser = sr.build_arg_parser()
    args = parser.parse_args([
        "--base-dir", "/tmp",
        "shard",
        "--source-manifest", "/tmp/x.jsonl",
        "--run-id", "r1",
        "--tier4",
        "--surprisal-model", "gpt2",
        "--surprisal-revision", "sha",
        "--embedding-model", "mxbai",
        "--embedding-revision", "embsha",
    ])
    assert args.tier4 is True
    assert args.surprisal_model == "gpt2"
    assert args.surprisal_revision == "sha"
    assert args.embedding_model == "mxbai"
    assert args.embedding_revision == "embsha"


def test_shard_runner_shard_defaults_preserve_back_compat():
    """Without any new flags, defaults match pre-1.80 behavior:
    tier4 off, model aliases None."""
    import shard_runner as sr  # type: ignore

    parser = sr.build_arg_parser()
    args = parser.parse_args([
        "--base-dir", "/tmp",
        "shard",
        "--source-manifest", "/tmp/x.jsonl",
        "--run-id", "r1",
    ])
    assert args.tier4 is False
    assert args.surprisal_model is None
    assert args.surprisal_revision is None
    assert args.embedding_model is None
    assert args.embedding_revision is None


# --------------- task_surfaces threading ----------


def test_score_shard_calibration_survey_threads_new_params_to_scorer():
    """``_score_shard_calibration_survey`` reads tier4 / model fields
    from ``task_params`` and passes them as kwargs to DEFAULT_SCORER.
    This is the single place the new pipeline-wired Tier 4 / pluggable
    embedding configuration reaches the actual scoring code; if this
    threading breaks, sharded runs silently fall back to Tier 1-3 with
    legacy MiniLM."""
    import shard_runner as sr  # type: ignore
    import task_surfaces as ts  # type: ignore

    captured: dict[str, Any] = {}

    def _stub_scorer(shard_manifest_path, **kwargs):
        captured.update(kwargs)
        return {"records": [], "meta": {}, "cache_hit": False}

    with mock.patch.object(sr, "DEFAULT_SCORER", _stub_scorer):
        surface = ts.get_task("calibration_survey")
        surface.score_shard(
            shard_manifest_path=Path("/tmp/x.jsonl"),
            cache_path=Path("/tmp/x_cache.json"),
            sigterm_event=None,
            flush_every=10,
            task_params={
                "fpr_target": 0.01, "tier1": True,
                "tier2": True, "tier3": True,
                "tier4": True,
                "embedding_model": "mxbai",
                "embedding_revision": "embsha",
                "surprisal_model": "gpt2",
                "surprisal_revision": "surpsha",
            },
            run_context={"use": "validation", "run_id": "r1"},
        )

    assert captured.get("tier4") is True
    assert captured.get("embedding_model") == "mxbai"
    assert captured.get("embedding_revision") == "embsha"
    assert captured.get("surprisal_model") == "gpt2"
    assert captured.get("surprisal_revision") == "surpsha"


def test_score_shard_calibration_survey_falls_back_to_run_context_for_embedding():
    """Pre-1.80 partial wiring stored embedding_model in state.json's
    TOP-level field (not under task_params). The task surface honors
    that path via the two-arg ``task_params.get(..., run_context.get(...))``
    fallback for embedding_model + embedding_revision so older
    state.json files keep producing the configured embedding model."""
    import shard_runner as sr  # type: ignore
    import task_surfaces as ts  # type: ignore

    captured: dict[str, Any] = {}

    def _stub_scorer(shard_manifest_path, **kwargs):
        captured.update(kwargs)
        return {"records": [], "meta": {}, "cache_hit": False}

    with mock.patch.object(sr, "DEFAULT_SCORER", _stub_scorer):
        surface = ts.get_task("calibration_survey")
        surface.score_shard(
            shard_manifest_path=Path("/tmp/x.jsonl"),
            cache_path=Path("/tmp/x_cache.json"),
            sigterm_event=None,
            flush_every=10,
            task_params={
                "fpr_target": 0.01, "tier1": True,
                "tier2": True, "tier3": True,
                # task_params has NO embedding_model — simulates a
                # pre-1.80 state.json that only stored it top-level.
            },
            run_context={
                "use": "validation", "run_id": "r1",
                "embedding_model": "gemma",  # legacy top-level
                "embedding_revision": "legsha",
            },
        )

    assert captured.get("embedding_model") == "gemma"
    assert captured.get("embedding_revision") == "legsha"


def test_score_shard_task_params_wins_over_run_context_for_embedding():
    """When both task_params and run_context have embedding_model,
    task_params (1.80+) wins over run_context (pre-1.80 legacy)."""
    import shard_runner as sr  # type: ignore
    import task_surfaces as ts  # type: ignore

    captured: dict[str, Any] = {}

    def _stub_scorer(shard_manifest_path, **kwargs):
        captured.update(kwargs)
        return {"records": [], "meta": {}, "cache_hit": False}

    with mock.patch.object(sr, "DEFAULT_SCORER", _stub_scorer):
        surface = ts.get_task("calibration_survey")
        surface.score_shard(
            shard_manifest_path=Path("/tmp/x.jsonl"),
            cache_path=Path("/tmp/x_cache.json"),
            sigterm_event=None,
            flush_every=10,
            task_params={
                "fpr_target": 0.01, "tier1": True,
                "tier2": True, "tier3": True,
                "embedding_model": "mxbai",  # 1.80+ wins
            },
            run_context={
                "use": "validation", "run_id": "r1",
                "embedding_model": "gemma",  # legacy loses
            },
        )

    assert captured.get("embedding_model") == "mxbai"


# --------------- 1.81.0: standalone-CLI surface ----------


def test_calibration_survey_cli_exposes_tier4_and_model_flags():
    """1.81.0+: ``calibration_survey.py`` must expose the same
    ``--tier4`` / ``--surprisal-model`` / ``--embedding-model`` flags
    as ``shard_runner shard`` so a 5K-subsample bake-off invocation
    against the standalone CLI can exercise the 1.80.0 wiring. Before
    1.81.0 the scoring path read these via ``getattr`` defaults; only
    the sharded path populated them on the args Namespace, so single-
    process bake-off runs against ``calibration_survey.py`` couldn't
    actually swap embedding models or enable Tier 4."""
    import subprocess
    result = subprocess.run(
        [sys.executable, str(ROOT / "calibration" / "calibration_survey.py"),
         "--help"],
        capture_output=True, text=True, timeout=30,
    )
    out = result.stdout
    assert "--tier4" in out, (
        "calibration_survey.py must expose --tier4 (1.81.0+)"
    )
    assert "--no-tier4" in out
    assert "--surprisal-model" in out
    assert "--surprisal-revision" in out
    assert "--embedding-model" in out
    assert "--embedding-revision" in out


def test_calibrate_thresholds_cli_exposes_tier4_and_model_flags():
    """1.81.0+: same flags on ``calibrate_thresholds.py``. The
    single-signal threshold-derivation CLI is the same shape as
    ``calibration_survey.py`` for these flags; both call into
    ``score_corpus`` which reads them via ``getattr``."""
    import subprocess
    result = subprocess.run(
        [sys.executable, str(ROOT / "calibration" / "calibrate_thresholds.py"),
         "--help"],
        capture_output=True, text=True, timeout=30,
    )
    out = result.stdout
    assert "--tier4" in out, (
        "calibrate_thresholds.py must expose --tier4 (1.81.0+)"
    )
    assert "--surprisal-model" in out
    assert "--surprisal-revision" in out
    assert "--embedding-model" in out
    assert "--embedding-revision" in out


def test_calibration_survey_parser_defaults_preserve_back_compat():
    """The new flags default to off / None on calibration_survey so
    operators who don't pass them get exactly the pre-1.81 behavior."""
    import calibration_survey as cs  # type: ignore

    parser = cs.build_arg_parser()
    args = parser.parse_args([
        "--manifest", "x.jsonl", "--fpr-target", "0.01",
    ])
    assert args.tier4 is False
    assert args.surprisal_model is None
    assert args.surprisal_revision is None
    assert args.embedding_model is None
    assert args.embedding_revision is None


def test_calibration_survey_parser_accepts_explicit_values():
    """Passing the new flags populates the args Namespace cleanly so
    score_corpus's ``getattr(args, ..., default)`` calls see real
    values, not defaults."""
    import calibration_survey as cs  # type: ignore

    parser = cs.build_arg_parser()
    args = parser.parse_args([
        "--manifest", "x.jsonl", "--fpr-target", "0.01",
        "--tier4",
        "--surprisal-model", "gpt2",
        "--surprisal-revision", "abc123",
        "--embedding-model", "mxbai",
        "--embedding-revision", "def456",
    ])
    assert args.tier4 is True
    assert args.surprisal_model == "gpt2"
    assert args.surprisal_revision == "abc123"
    assert args.embedding_model == "mxbai"
    assert args.embedding_revision == "def456"


def test_calibration_survey_build_inner_args_forwards_new_fields():
    """**Codex P2 on PR #78**: calibration_survey's parser accepts
    the new flags, but ``_build_inner_args`` constructs a fresh
    Namespace for the scoring path. Before the fix, that inner
    Namespace dropped the 5 new fields (no ``tier4``, no
    ``embedding_model``, no ``surprisal_model`` / etc.) so
    ``score_corpus`` saw ``None`` / ``False`` defaults via its own
    ``getattr`` fallbacks -- the standalone CLI silently fell back
    to Tier 1+2+3 with legacy MiniLM regardless of what flags the
    operator passed.

    Pins by parsing args with explicit non-default values, building
    the inner Namespace, and asserting each new field landed on it.
    Direct regression guard for the wiring gap.
    """
    import calibration_survey as cs  # type: ignore

    parser = cs.build_arg_parser()
    parent_args = parser.parse_args([
        "--manifest", "x.jsonl", "--fpr-target", "0.01",
        "--tier4",
        "--surprisal-model", "gpt2",
        "--surprisal-revision", "abc123",
        "--embedding-model", "mxbai",
        "--embedding-revision", "def456",
    ])
    inner = cs._build_inner_args(parent_args, "burstiness_B")

    assert inner.tier4 is True, (
        "inner Namespace must carry tier4 from parent_args; otherwise "
        "score_corpus's getattr falls back to False and the standalone "
        "CLI silently scores Tier 1+2+3 only"
    )
    assert inner.embedding_model == "mxbai", (
        "inner Namespace must carry embedding_model from parent_args; "
        "otherwise score_corpus's getattr falls back to None and the "
        "standalone CLI silently uses legacy MiniLM"
    )
    assert inner.embedding_revision == "def456"
    assert inner.surprisal_model == "gpt2"
    assert inner.surprisal_revision == "abc123"


def test_calibration_survey_build_inner_args_defaults_when_parent_lacks_fields():
    """Back-compat: any pre-1.81 test fixture or programmatic caller
    that hand-constructs a parent_args without the new flags must
    still produce a working inner Namespace (no AttributeError) with
    the same default values score_corpus would have used."""
    import calibration_survey as cs  # type: ignore

    # Hand-construct a pre-1.81 parent_args -- no tier4, no model
    # fields. The fix uses getattr with safe defaults, so this should
    # not raise.
    parent_args = argparse.Namespace(
        manifest="x.jsonl",
        use="validation",
        fpr_target=0.01,
        bootstrap_resamples=2000,
        bootstrap_confidence=0.95,
        bootstrap_seed=42,
        tier2=True,
        tier3=True,
        # No tier4 / embedding_model / etc.
    )
    inner = cs._build_inner_args(parent_args, "burstiness_B")
    assert inner.tier4 is False
    assert inner.embedding_model is None
    assert inner.embedding_revision is None
    assert inner.surprisal_model is None
    assert inner.surprisal_revision is None


def test_calibration_survey_inner_args_reach_score_corpus(monkeypatch, tmp_path):
    """End-to-end: parse the standalone CLI's flags, run the same
    code path the CLI invokes (build inner args + call
    load_or_score_corpus), and assert the score_smoothing_entry the
    pipeline calls actually sees the operator-specified model values.

    This is the assertion codex pointed at: the parser tests prove
    the flags PARSE, but only this test proves the parsed values
    REACH THE SCORE-ONCE PATH. Spies on score_smoothing_entry to
    capture what kwargs it receives across the full args→inner→
    score_corpus→score_smoothing_entry chain."""
    import calibration_survey as cs  # type: ignore
    import calibrate_thresholds as ct  # type: ignore
    import validation_harness as vh  # type: ignore

    # Build a tiny synthetic manifest with one entry pointing at a
    # real text file (load_or_score_corpus needs to be able to read
    # the entry, even if the scorer is stubbed).
    txt = tmp_path / "essay.txt"
    txt.write_text("word " * 200, encoding="utf-8")
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps({
        "id": "essay_0", "path": str(txt),
        "register": "essay", "ai_status": "human",
        "language_status": "en", "word_count": 200,
        "use": ["validation"],
    }) + "\n", encoding="utf-8")

    parser = cs.build_arg_parser()
    parent_args = parser.parse_args([
        "--manifest", str(manifest),
        "--fpr-target", "0.01",
        "--tier4",
        "--surprisal-model", "gpt2",
        "--embedding-model", "mxbai",
    ])

    # Spy on score_smoothing_entry (the leaf the chain reaches) to
    # capture per-entry kwargs. Patch where calibrate_thresholds
    # imported it so the call site uses our spy.
    captured: dict[str, Any] = {}

    def _spy_score(entry, **kwargs):
        captured.update(kwargs)
        return {
            "id": entry.get("id"), "score": 0.5, "label": 0,
            "score_name": "compression_fraction",
            "usable_for_metrics": True,
            "per_signal_scores": {"burstiness_B": 0.5},
            "raw_word_count": 200, "observed_word_count": 200,
            "length_bucket": "short",
        }

    monkeypatch.setattr(ct, "score_smoothing_entry", _spy_score)

    inner = cs._build_inner_args(parent_args, "burstiness_B")
    # load_or_score_corpus is the exact entry point calibration_
    # survey.py main() uses. Call it the same way.
    cache_path = tmp_path / "cache.json"
    ct.load_or_score_corpus(inner, cache_path=cache_path, refresh=False)

    # The captured kwargs from score_smoothing_entry confirm the
    # values flowed parent_args → inner → score_corpus → score_
    # smoothing_entry. If _build_inner_args drops them, these
    # assertions fail.
    assert captured.get("do_tier4") is True, (
        f"do_tier4 didn't reach score_smoothing_entry; got "
        f"{captured.get('do_tier4')!r}. The wiring gap codex caught "
        f"on PR #78 has regressed."
    )
    assert captured.get("embedding_model") == "mxbai", (
        f"embedding_model didn't reach score_smoothing_entry; got "
        f"{captured.get('embedding_model')!r}"
    )
    assert captured.get("surprisal_model") == "gpt2"


