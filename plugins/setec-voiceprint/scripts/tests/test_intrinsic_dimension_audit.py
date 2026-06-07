#!/usr/bin/env python3
"""Tests for intrinsic_dimension_audit.py — the clean-room PHD surface.

Every test uses a DETERMINISTIC STUB embedder. No embedding model is ever
loaded or downloaded: the real ``embedding_backend`` / ``sentence-transformers``
path is exercised only behind ``main()`` (not touched here). The PHD estimator
itself (scipy MST H0-persistence + log-log scaling fit) is real and runs on the
stub's point cloud.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np  # type: ignore
import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import intrinsic_dimension_audit as ida  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402
from claim_license import TASK_SURFACE_LABELS  # type: ignore  # noqa: E402


# ----------------------------------------------------------------------
# Deterministic stub embedder. Maps each text unit to a point in R^D by
# seeding a per-unit RNG from a hash of the text. Same text in -> same
# vector out, with NO model load. The intrinsic geometry is whatever this
# stub produces; the test asserts on stability / shape / discipline, not on
# any "true" dimension of real prose.
# ----------------------------------------------------------------------

def _stub_embed(dim: int = 16):
    def embed(texts: list[str]) -> np.ndarray:
        vecs = []
        for i, t in enumerate(texts):
            # Stable per-unit seed: deterministic function of (index, text).
            seed = (abs(hash(t)) % (2**31)) ^ (i * 2654435761 & 0x7FFFFFFF)
            rng = np.random.default_rng(seed % (2**32))
            vecs.append(rng.standard_normal(dim))
        return np.asarray(vecs, dtype="float64")

    return embed


def _make_text(n_sentences: int) -> str:
    # Distinct sentences so split_units yields n_sentences embedding units.
    return " ".join(f"This is sentence number {i} in the cloud." for i in range(n_sentences))


# ----------------------------------------------------------------------
# Named tests from the spec test contract.
# ----------------------------------------------------------------------

def test_surface_registered():
    """The new surface is registered in BOTH enums and on the script."""
    assert ida.TASK_SURFACE == "intrinsic_dimension"
    assert "intrinsic_dimension" in VALID_TASK_SURFACES
    assert "intrinsic_dimension" in TASK_SURFACE_LABELS


def test_phd_deterministic_with_seed():
    """Stub embedder -> the PHD estimate is stable across repeated runs.

    Determinism is the contract: identical input + identical seed -> bit-for-bit
    identical PHD. No model load anywhere.
    """
    text = _make_text(260)
    embed = _stub_embed()

    r1 = ida.audit(text, embed=embed, embedding_model_id="stub/deterministic")
    r2 = ida.audit(text, embed=embed, embedding_model_id="stub/deterministic")

    assert r1["phd"] is not None
    assert r1["phd"] == r2["phd"]
    assert r1["fit"]["slope"] == r2["fit"]["slope"]
    # The estimate_phd entry point is independently deterministic too.
    pts = embed(ida.split_units(text))
    e1 = ida.estimate_phd(pts, seed=ida.DEFAULT_SEED)
    e2 = ida.estimate_phd(pts, seed=ida.DEFAULT_SEED)
    assert e1["phd"] == e2["phd"]
    assert e1["phd"] is not None and e1["phd"] > 0.0


def test_no_default_threshold_no_band():
    """Uncalibrated by default: results carry NO band / verdict / threshold."""
    text = _make_text(260)
    results = ida.audit(text, embed=_stub_embed(), embedding_model_id="stub/x")
    for forbidden in ("band", "verdict", "threshold", "operating_point", "label"):
        assert forbidden not in results
    assert "uncalibrated_no_threshold_no_band" in results["caveats"]


def test_claim_license_refuses_verdict():
    """The claim license refuses an AI/human verdict absent operator thresholds,
    and licenses only the geometric PHD measurement under a named model."""
    results = ida.audit(_make_text(260), embed=_stub_embed(), embedding_model_id="stub/x")
    lic = ida._claim_license(results)
    assert lic.task_surface == "intrinsic_dimension"
    dn = lic.does_not_license.lower()
    assert "verdict" in dn
    assert "ai" in dn and "human" in dn
    assert "threshold" in dn
    licenses = lic.licenses.lower()
    assert "phd" in licenses or "intrinsic" in licenses
    assert "not a verdict" in licenses or "measurement" in licenses
    # No shipped operating point.
    assert lic.fpr_target is None
    rendered = lic.render_block()
    assert "Does NOT report" in rendered


def test_short_text_instability_warned():
    """Short text (few embedding units) raises the short-text instability
    caveat; a long-enough cloud does not."""
    short = _make_text(8)
    short_results = ida.audit(short, embed=_stub_embed(), embedding_model_id="stub/x")
    assert any("short_text" in c for c in short_results["caveats"])
    assert short_results["n_points"] < ida.MIN_STABLE_POINTS

    long = _make_text(ida.MIN_STABLE_POINTS + 40)
    long_results = ida.audit(long, embed=_stub_embed(), embedding_model_id="stub/x")
    assert long_results["n_points"] >= ida.MIN_STABLE_POINTS
    assert not any("short_text" in c for c in long_results["caveats"])


def test_envelope_shape():
    """compose_envelope produces a valid schema-1.0 envelope on the new surface."""
    text = _make_text(260)
    results = ida.audit(text, embed=_stub_embed(), embedding_model_id="stub/deterministic")
    envelope = ida.compose_envelope(
        target_path="sample.txt",
        target_words=ida.count_words(text),
        results=results,
    )
    assert envelope["schema_version"] == "1.0"
    assert envelope["task_surface"] == "intrinsic_dimension"
    assert envelope["tool"] == "intrinsic_dimension_audit"
    assert envelope["available"] is True
    assert envelope["claim_license"] is not None
    assert envelope["claim_license"]["task_surface"] == "intrinsic_dimension"
    # results payload carries the scalar, point count, and model id; no band.
    r = envelope["results"]
    assert "phd" in r
    assert "n_points" in r
    assert r["embedding_model"]["id"] == "stub/deterministic"
    assert "band" not in r and "verdict" not in r
    # markdown renderer is robust on a real envelope.
    md = ida.render_markdown(envelope)
    assert "Intrinsic-Dimension Audit" in md
    assert "PHD" in md
