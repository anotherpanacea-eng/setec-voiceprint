#!/usr/bin/env python3
"""Tests for rank_turbulence_audit.py (spec 23, M1) — interpretable per-word stylometric divergence.

Stdlib, deterministic. Covers the spec-23 contract: function-words-only default, RTD ∈ [0,1] with
the reconstruction invariant, self-exclusion, bad_input, competition tie-rule, a concrete
α-monotonicity swap, and claim-license refuses-verdict."""

from __future__ import annotations

import io
import json
import sys
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rank_turbulence_audit as rt  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402

# Function-word-rich texts (so the default function-words mode has signal).
_STYLE_A = "the cat and the dog and the bird were in the house and then the sun and the moon " * 6
_STYLE_B = "of the work by which of these with whom of those by that of it with this of such " * 6


def _envelope(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = rt.main(argv)
    return rc, json.loads(out.getvalue())


def _dir(tmp_path, name, docs):
    d = tmp_path / name; d.mkdir()
    for fn, text in docs.items():
        (d / fn).write_text(text)
    return d


# --- method ----------------------------------------------------------------

def test_competition_ranking_1224():
    ranks = rt._competition_ranks(Counter({"a": 5, "b": 3, "c": 3, "d": 1}))
    assert ranks == {"a": 1.0, "b": 2.0, "c": 2.0, "d": 4.0}   # 1,2,2,4


def test_identical_rtd_zero():
    r = rt.audit_rank_turbulence(_STYLE_A, _STYLE_A)
    assert r["rtd"] == pytest.approx(0.0) and r["top_target"] == [] and r["top_baseline"] == []


def test_disjoint_rtd_one():
    r = rt.audit_rank_turbulence("of by with whom", "the and to a", function_words_only=True)
    assert r["rtd"] == pytest.approx(1.0)


def test_partial_overlap_in_unit_interval_and_reconstructs():
    r = rt.audit_rank_turbulence(_STYLE_A, _STYLE_B)
    assert 0.0 < r["rtd"] < 1.0
    assert r["rtd"] == pytest.approx(r["numerator"] / r["n_denom"], abs=1e-5)   # reconstruction (6dp rounding)
    assert r["rtd"] >= 0.0


def test_deterministic():
    a = rt.audit_rank_turbulence(_STYLE_A, _STYLE_B)
    b = rt.audit_rank_turbulence(_STYLE_A, _STYLE_B)
    assert a == b


def test_top_direction():
    # _STYLE_A is "the"-heavy; vs _STYLE_B "the" is over-ranked in the target -> top_target
    r = rt.audit_rank_turbulence(_STYLE_A, _STYLE_B)
    assert any(c["word"] == "the" for c in r["top_target"])


def test_empty_distributions_raise():
    with pytest.raises(ValueError):
        rt.audit_rank_turbulence("zzz qqq xyz", _STYLE_B)        # no function words in target
    with pytest.raises(ValueError):
        rt.audit_rank_turbulence(_STYLE_A, "zzz qqq xyz")


def test_alpha_dials_rare_vs_common():
    # alpha controls rare-vs-common emphasis. A rare word (big rank ratio, high ranks) gains
    # weight RELATIVE to a common word (small ratio, low ranks) as alpha shrinks. Pin it directly
    # on the bare-summand: rare/common contribution ratio is larger at small alpha than at large.
    common_hi, rare_hi = rt._bare(1, 2, 1.0), rt._bare(10, 100, 1.0)
    common_lo, rare_lo = rt._bare(1, 2, 0.05), rt._bare(10, 100, 0.05)
    assert (rare_lo / common_lo) > (rare_hi / common_hi)         # rare word emphasized at small alpha


# --- envelope / CLI --------------------------------------------------------

def test_surface_is_voice_coherence():
    assert rt.TASK_SURFACE == "voice_coherence" and "voice_coherence" in VALID_TASK_SURFACES


def test_default_is_function_words(tmp_path):
    bdir = _dir(tmp_path, "b", {"b.txt": _STYLE_B})
    tgt = tmp_path / "t.txt"; tgt.write_text(_STYLE_A)
    _, env = _envelope(["--target", str(tgt), "--baseline-dir", str(bdir), "--json"])
    assert env["results"]["mode"] == "function_words"
    _, env2 = _envelope(["--target", str(tgt), "--baseline-dir", str(bdir), "--all-words", "--json"])
    assert env2["results"]["mode"] == "all_words"
    assert any("TOPICAL" in w for w in (env2.get("warnings") or []))


def test_envelope_shape_and_claim_license(tmp_path):
    bdir = _dir(tmp_path, "b", {"b.txt": _STYLE_B})
    tgt = tmp_path / "t.txt"; tgt.write_text(_STYLE_A)
    rc, env = _envelope(["--target", str(tgt), "--baseline-dir", str(bdir), "--json"])
    assert rc == 0 and env["available"] is True and env["task_surface"] == "voice_coherence"
    assert {"rtd", "alpha", "mode", "top_target", "top_baseline", "numerator", "n_denom"} <= set(env["results"])
    cl = env["claim_license"]
    dnl = cl["does_not_license"].lower()
    assert "authorship" in dnl and "not 'ai'" in dnl and "verdict" in dnl
    assert not any(k in env["results"] for k in ("verdict", "is_ai", "label", "decision"))


def test_self_exclusion_not_collapsed(tmp_path):
    # baseline dir holds {a copy of the target (style A), and a different-style doc (B)}.
    bdir = _dir(tmp_path, "b", {"copyA.txt": _STYLE_A, "other.txt": _STYLE_B})
    tgt = bdir / "copyA.txt"                                      # target IS a baseline file
    rc, env = _envelope(["--target", str(tgt), "--baseline-dir", str(bdir), "--json"])
    assert rc == 0
    assert env["results"]["assumptions"]["dropped_self"] == 1
    # after dropping the self-copy, baseline = style B -> rtd > 0 (NOT collapsed to ~0 by self-inclusion)
    assert env["results"]["rtd"] > 0.0
    assert any("self-exclusion" in w for w in (env.get("warnings") or []))


def test_empty_baseline_bad_input(tmp_path):
    empty = tmp_path / "empty"; empty.mkdir()
    tgt = tmp_path / "t.txt"; tgt.write_text(_STYLE_A)
    rc, env = _envelope(["--target", str(tgt), "--baseline-dir", str(empty), "--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input" and rc == 3
