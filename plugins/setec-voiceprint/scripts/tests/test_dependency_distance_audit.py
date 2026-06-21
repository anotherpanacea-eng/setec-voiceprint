#!/usr/bin/env python3
"""Tests for dependency_distance_audit.py (spec 24) — the dependency-distance distribution.

Parser-tier (spaCy): parser-dependent numeric pins skipif(not HAS_SPACY). The degradation +
bad-input + surface-registration paths run without the model. Covers the spec-24 contract: the
distribution + reused mdd_stats scalars, punctuation kept (n_links invariant), the center-embedding
complexity pin, and the claim-license refuses-verdict + length-confound caveat."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import dependency_distance_audit as dd  # type: ignore  # noqa: E402
import variance_audit as va  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402

_needs_parser = pytest.mark.skipif(not va.HAS_SPACY or va._NLP is None,
                                   reason="needs spaCy + en_core_web_sm")

_TEXT = ("The cat sat on the mat. The rat the cat chased ran. "
         "She walked to the store and bought bread and milk for the week.")


def _envelope(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = dd.main(argv)
    return rc, json.loads(out.getvalue())


# --- runs without the model -------------------------------------------------

def test_surface_registered():
    assert dd.TASK_SURFACE == "voice_coherence" and "voice_coherence" in VALID_TASK_SURFACES


def test_missing_parser_abstains(tmp_path, monkeypatch):
    t = tmp_path / "t.txt"; t.write_text(_TEXT)
    monkeypatch.setattr(dd, "HAS_SPACY", False)
    monkeypatch.setattr(dd, "_NLP", None)
    rc, env = _envelope([str(t), "--json"])
    assert env["available"] is False and env["reason_category"] == "missing_dependency" and rc == 3


@_needs_parser
def test_missing_target_bad_input(tmp_path):
    rc, env = _envelope([str(tmp_path / "nope.txt"), "--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input"


# --- method (parser-gated) --------------------------------------------------

@_needs_parser
def test_deterministic():
    assert dd.audit_dependency_distance(_TEXT) == dd.audit_dependency_distance(_TEXT)


@_needs_parser
def test_mdd_scalars_reuse_mdd_stats():
    r = dd.audit_dependency_distance(_TEXT)
    s = va.mdd_stats(_TEXT)
    assert r["mdd_mean"] == pytest.approx(round(s["mean"], 6))    # reused, not re-derived
    assert r["mdd_sd"] == pytest.approx(round(s["sd"], 6))


@_needs_parser
def test_histogram_consistency_and_nlinks_invariant():
    r = dd.audit_dependency_distance(_TEXT)
    assert sum(r["distance_histogram"].values()) == r["n_links"]   # histogram covers all links
    # punctuation kept, ROOT/self excluded -> one root per sentence
    assert r["n_links"] == r["n_tokens"] - r["n_sentences"]
    shares_total = r["adjacent_share"] + sum(
        v / r["n_links"] for k, v in r["distance_histogram"].items() if k != "1")
    assert shares_total == pytest.approx(1.0)


@_needs_parser
def test_center_embedding_higher_mdd_than_flat():
    # EXACT pinned pair (not "any flat sentence"): center-embedded vs a flat 6-word control.
    ce = dd.audit_dependency_distance("The rat the cat chased ran.")
    flat = dd.audit_dependency_distance("The big black dog barked loudly.")
    assert ce["mdd_mean"] > flat["mdd_mean"]


@_needs_parser
def test_no_links_bad_input(tmp_path):
    t = tmp_path / "t.txt"; t.write_text("Hi")             # single token (no punct) -> no links
    rc, env = _envelope([str(t), "--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input"


# --- envelope / claim license (parser-gated) --------------------------------

@_needs_parser
def test_envelope_shape_and_confound_visible(tmp_path):
    t = tmp_path / "t.txt"; t.write_text(_TEXT)
    rc, env = _envelope([str(t), "--json"])
    assert rc == 0 and env["available"] is True and env["task_surface"] == "voice_coherence"
    r = env["results"]
    assert {"distance_histogram", "adjacent_share", "long_range_share", "mdd_mean", "mdd_sd",
            "mean_sentence_length", "n_links"} <= set(r)
    assert r["mean_sentence_length"] > 0                   # length confound surfaced
    assert env["target"]["spacy_available"] is True


@_needs_parser
def test_claim_license_refuses_verdict(tmp_path):
    t = tmp_path / "t.txt"; t.write_text(_TEXT)
    _, env = _envelope([str(t), "--json"])
    dnl = env["claim_license"]["does_not_license"].lower()
    assert "authorship" in dnl and "ai/human" in dnl and "length-controlled" in dnl
    assert not any(k in env["results"] for k in ("verdict", "is_ai", "label", "decision"))


# ===========================================================================
# DDD SHAPE descriptors (results["shape"]) — distribution geometry of the
# pooled per-link distances (arXiv:2211.14620). Distinct from the histogram,
# the shares, and the reused mdd_sd. M1 stdlib, no verdict, no band.
# ===========================================================================

# A longer right-skewed text with a center-embedding so the pooled per-link
# distribution has visible skew/heavy tail (and so n_links >= 3 everywhere).
_SHAPE_TEXT = (
    "The cat sat on the mat. The rat the cat chased ran. "
    "She walked to the store and bought bread and milk for the week. "
    "Although the weather was unusually cold, the children who had gathered near the old "
    "library waited patiently for the bus that would eventually carry them home."
)

# Exact forbidden KEY NAMES for the no-verdict recursive walk. Substring matching
# (e.g. /threshold/ or /score/) would false-positive on the legitimate, untouched
# `long_threshold` results key (finding P2), so we forbid EXACT keys only — the
# same safe approach the shipped refuses-verdict test uses (exact-key membership).
_FORBIDDEN_KEYS = frozenset({
    "is_ai", "is_human", "is_smoothed", "verdict", "label", "class", "classification",
    "decision", "score", "confidence", "rank", "prediction", "flag", "selection",
    "best", "top", "selected",
})


def _walk_keys(obj):
    """Yield every dict key reachable in a nested results payload (lists too)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_keys(item)


# --- AC 5: moment math (parser-FREE; runs unconditionally in CI) ------------

def test_distance_shape_math_parser_free():
    # Pinned right-skewed list; hand-computed population moments.
    xs = [1, 1, 1, 1, 1, 2, 2, 2, 3, 3, 4, 5, 8, 12, 20]
    s = dd._distance_shape(xs)
    n = len(xs)
    mean = sum(xs) / n
    m2 = sum((x - mean) ** 2 for x in xs) / n
    m3 = sum((x - mean) ** 3 for x in xs) / n
    m4 = sum((x - mean) ** 4 for x in xs) / n
    assert s["variance"] == pytest.approx(m2, abs=1e-6)
    assert s["sd"] == pytest.approx(m2 ** 0.5, abs=1e-6)
    assert s["skewness"] == pytest.approx(m3 / (m2 ** 1.5), abs=1e-6)        # Fisher-Pearson g1
    assert s["excess_kurtosis"] == pytest.approx(m4 / (m2 ** 2) - 3.0, abs=1e-6)  # g2 (excess)
    assert s["skewness"] > 0 and s["excess_kurtosis"] > 0                    # right-skew, heavy tail
    # nearest-rank quantiles
    assert s["quantiles"]["p50"] == 2.0
    assert s["quantiles"]["max"] == 20.0 == float(max(xs))
    assert s["n_links"] == n


def test_distance_shape_degenerate_returns_null():
    # sd == 0 (all equal): standardized moments are UNDEFINED -> null, never 0.0
    # (0.0 would falsely imply "symmetric/mesokurtic" — a quiet no-verdict violation).
    eq = dd._distance_shape([5, 5, 5, 5])
    assert eq["skewness"] is None and eq["excess_kurtosis"] is None
    assert eq["variance"] == 0.0 and eq["sd"] == 0.0          # spread stays DEFINED
    assert eq["quantiles"]["max"] == 5.0
    # n_links < 3: the third/fourth standardized moments are undefined for n<3 -> null
    two = dd._distance_shape([1, 2])
    assert two["skewness"] is None and two["excess_kurtosis"] is None
    assert two["variance"] >= 0.0
    one = dd._distance_shape([4])
    assert one["skewness"] is None and one["excess_kurtosis"] is None
    assert one["variance"] == 0.0 and one["quantiles"]["max"] == 4.0
    # empty -> defensive ValueError (the audit caller guarantees >= 1 link)
    with pytest.raises(ValueError):
        dd._distance_shape([])


def test_no_nan_inf_in_shape():
    # No shape leaf (any input) is ever NaN/inf — degenerate cases emit null or a
    # defined number, so validate_results_bounds' unconditional finite check passes.
    import math as _math
    for xs in ([5, 5, 5], [1, 2], [4], [1, 1, 1, 1, 2, 3, 20], list(range(1, 50))):
        for v in _flat_numbers(dd._distance_shape(xs)):
            assert not (_math.isnan(v) or _math.isinf(v))


def _flat_numbers(obj):
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        yield float(obj); return
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _flat_numbers(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _flat_numbers(v)


def test_no_numpy_scipy_import():
    # AC 11: the shape math is pure stdlib. Assert the module pulls in NO
    # numpy/scipy/torch (the only heavy import remains the OPTIONAL spaCy).
    import importlib
    src = (Path(dd.__file__)).read_text(encoding="utf-8")
    for banned in ("import numpy", "import scipy", "import torch",
                   "from numpy", "from scipy", "from torch"):
        assert banned not in src, f"{banned!r} leaked into dependency_distance_audit.py"
    # and it is importable + _distance_shape runs with none of those installed
    importlib.reload(dd)
    assert dd._distance_shape([1, 2, 3, 4, 5])["variance"] >= 0.0


def test_dependency_distance_not_imported_by_detectors():
    # AC 10 (anti-Goodhart held-out disjoint): this DESCRIPTIVE surface must stay
    # disjoint from the held-out detector seam — no voice_distance / discrimination
    # / surface_disagreement_resolver scoring path may import it.
    scripts_dir = Path(dd.__file__).resolve().parent
    detectors = [
        "voice_distance.py", "crosslingual_voice_distance.py",
        "surface_disagreement_resolver.py", "discrimination_evidence.py",
    ]
    for name in detectors:
        f = scripts_dir / name
        if not f.exists():
            continue
        src = f.read_text(encoding="utf-8")
        assert "dependency_distance_audit" not in src, (
            f"{name} imports/references dependency_distance_audit — breaks the "
            f"anti-Goodhart disjointness of this descriptive surface")


# --- AC 1/2/3: envelope additivity + posture (parser-gated) -----------------

@_needs_parser
def test_shape_block_additive_and_present(tmp_path):
    # AC 1: shape is present; every pre-existing spec-24 results key is unchanged.
    t = tmp_path / "t.txt"; t.write_text(_SHAPE_TEXT)
    _, env = _envelope([str(t), "--json"])
    r = env["results"]
    assert "shape" in r
    pre_existing = {"distance_histogram", "adjacent_share", "long_range_share", "mdd_mean",
                    "mdd_sd", "mean_sentence_length", "long_threshold", "n_links", "n_sentences",
                    "n_tokens", "assumptions"}
    assert pre_existing <= set(r)
    assert set(r) == pre_existing | {"shape"}                 # ONLY shape was added
    assert {"variance", "sd", "skewness", "excess_kurtosis", "quantiles", "n_links",
            "assumptions"} == set(r["shape"])


@_needs_parser
def test_results_carries_no_verdict_incl_shape(tmp_path):
    # AC 2/3: recursive walk over the FULL results (incl. shape) finds no verdict /
    # selection key; quantiles are reported values, not a chosen cut. Exact-key
    # match (NOT substring) so the benign `long_threshold` key is not flagged.
    t = tmp_path / "t.txt"; t.write_text(_SHAPE_TEXT)
    _, env = _envelope([str(t), "--json"])
    keys = set(_walk_keys(env["results"]))
    assert "long_threshold" in keys                           # benign key is present...
    offending = keys & _FORBIDDEN_KEYS
    assert offending == set(), f"verdict/selection keys leaked into results: {offending}"
    # claim-license refuses the inferences
    dnl = env["claim_license"]["does_not_license"].lower()
    assert "authorship" in dnl and "ai/human" in dnl and "length-controlled" in dnl
    assert "not a complexity" in dnl and "not an ai signal" in dnl


@_needs_parser
def test_shape_sd_distinct_from_mdd_sd(tmp_path):
    # AC 4 (load-bearing pin): shape.sd is the within-POOL per-link SD, NOT the
    # across-SENTENCE SD of per-sentence means (mdd_sd). On a text whose
    # per-sentence MEAN distances are similar but whose per-link distances are
    # dispersed (a center-embedding packs a long link against many d=1s), the two
    # numbers separate.
    t = tmp_path / "t.txt"; t.write_text(_SHAPE_TEXT)
    _, env = _envelope([str(t), "--json"])
    r = env["results"]
    assert abs(r["shape"]["sd"] - r["mdd_sd"]) > 1e-3         # genuinely different quantities


@_needs_parser
def test_shape_quantiles_match_histogram(tmp_path):
    # AC 6: shape consistency with the pooled distances the histogram covers.
    t = tmp_path / "t.txt"; t.write_text(_SHAPE_TEXT)
    _, env = _envelope([str(t), "--json"])
    r = env["results"]
    sh = r["shape"]
    assert sh["n_links"] == r["n_links"] == sum(r["distance_histogram"].values())
    assert sh["quantiles"]["max"] >= sh["quantiles"]["p99"] >= sh["quantiles"]["p90"] >= \
        sh["quantiles"]["p50"]
    # p50 is the nearest-rank median of the pooled distances (recomputed directly).
    r2 = dd.audit_dependency_distance(_SHAPE_TEXT)
    assert r2["shape"]["quantiles"]["max"] == sh["quantiles"]["max"]   # deterministic


@_needs_parser
def test_shape_right_skew_on_english(tmp_path):
    # AC 7: directional sanity (NOT a calibrated threshold) — the DDD curve of a
    # normal English paragraph is right-skewed and heavy-tailed (arXiv:2211.14620).
    r = dd.audit_dependency_distance(_SHAPE_TEXT)
    sh = r["shape"]
    assert sh["skewness"] is not None and sh["skewness"] > 0
    assert sh["excess_kurtosis"] is not None and sh["excess_kurtosis"] > 0


@_needs_parser
def test_shape_passes_bounds_gate(tmp_path):
    # AC 8: build_output's validate_results_bounds passes on the augmented results
    # (skew/kurtosis keys carry no surprisal/probability token, so they are left
    # unchecked; the only gate is the unconditional NaN/inf check, which null avoids).
    t = tmp_path / "t.txt"; t.write_text(_SHAPE_TEXT)
    rc, env = _envelope([str(t), "--json"])
    assert rc == 0 and env["available"] is True               # build_output ran the gate, no raise
    assert "shape" in env["results"]
