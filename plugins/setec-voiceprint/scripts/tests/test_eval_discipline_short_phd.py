#!/usr/bin/env python3
"""Tests for Short-PHD stabilization (spec 28, PR D).

Deterministic STUB embedder only — no model loaded. Root: Short-PHD
(arXiv:2504.02873). Asserts the auto-route on short text, bit-for-bit
long-text preservation, the degenerate None contract, determinism, and the
absence of any band/threshold/verdict key.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np  # type: ignore

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import intrinsic_dimension_audit as ida  # type: ignore  # noqa: E402


def _stub(dim: int = 16):
    def embed(texts: list[str]) -> np.ndarray:
        vecs = []
        for i, t in enumerate(texts):
            seed = (abs(hash(t)) % (2**31)) ^ (i * 2654435761 & 0x7FFFFFFF)
            rng = np.random.default_rng(seed % (2**32))
            vecs.append(rng.standard_normal(dim))
        return np.asarray(vecs, dtype="float64")
    return embed


def _text(n: int) -> str:
    return " ".join(f"This is sentence number {i} in the cloud." for i in range(n))


# ---- Acceptance #7: Short-PHD stabilization ---------------------------------


def test_short_text_auto_routes_to_short_phd():
    text = _text(60)  # < MIN_STABLE_POINTS (200)
    r = ida.audit(text, embed=_stub(), embedding_model_id="stub",
                  short_text_mode="auto")
    assert r["n_points"] < ida.MIN_STABLE_POINTS
    stab = r["fit"].get("phd_stability")
    assert stab is not None
    assert {"median", "iqr", "n_fits_valid", "per_fit_phd"}.issubset(stab)
    # The short-text caveat is KEPT.
    assert "short_text_phd_estimate_unstable" in r["caveats"]


def test_long_text_auto_is_bit_for_bit_single_fit():
    text = _text(260)  # >= MIN_STABLE_POINTS
    r_auto = ida.audit(text, embed=_stub(), embedding_model_id="stub",
                       short_text_mode="auto")
    r_never = ida.audit(text, embed=_stub(), embedding_model_id="stub",
                        short_text_mode="never")
    assert r_auto == r_never
    assert "phd_stability" not in r_auto["fit"]


def test_short_phd_keeps_uncalibrated_no_band_no_verdict():
    text = _text(60)
    r = ida.audit(text, embed=_stub(), embedding_model_id="stub",
                  short_text_mode="auto")
    for forbidden in ("band", "threshold", "verdict", "is_ai", "is_human"):
        assert forbidden not in r
    assert "uncalibrated_no_threshold_no_band" in r["caveats"]


def test_degenerate_cloud_returns_none_phd():
    # Too few points to fit any scaling law.
    def tiny(texts):
        return np.asarray([[float(i), 0.0] for i in range(3)])
    r = ida.audit("a. b. c.", embed=tiny, embedding_model_id="stub",
                  short_text_mode="always")
    assert r["phd"] is None
    assert "phd_estimate_unavailable_degenerate_or_too_small" in r["caveats"]
    assert r["fit"]["phd_stability"]["n_fits_valid"] == 0


# ---- Acceptance #8: Short-PHD determinism -----------------------------------


def test_short_phd_deterministic_given_seed():
    text = _text(60)
    r1 = ida.audit(text, embed=_stub(), embedding_model_id="stub",
                   short_text_mode="auto", seed=99)
    r2 = ida.audit(text, embed=_stub(), embedding_model_id="stub",
                   short_text_mode="auto", seed=99)
    assert r1["fit"]["phd_stability"] == r2["fit"]["phd_stability"]
    assert r1["phd"] == r2["phd"]


def test_estimate_phd_short_direct_determinism():
    rng = np.random.default_rng(7)
    points = rng.standard_normal((60, 16))
    a = ida.estimate_phd_short(points, seed=5)
    b = ida.estimate_phd_short(points, seed=5)
    assert a["phd_stability"] == b["phd_stability"]
    assert a["phd"] == b["phd"]


def test_always_mode_forces_short_phd_on_long_text():
    text = _text(260)
    r = ida.audit(text, embed=_stub(), embedding_model_id="stub",
                  short_text_mode="always")
    assert "phd_stability" in r["fit"]


def test_invalid_short_text_mode_rejected():
    text = _text(60)
    import pytest
    with pytest.raises(ValueError):
        ida.audit(text, embed=_stub(), embedding_model_id="stub",
                  short_text_mode="bogus")
