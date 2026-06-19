#!/usr/bin/env python3
"""Tests for the T-Detect Student-t tail normalization (spec 25) in fast_detect_curvature.py.

Opt-in `--tail student-t` adds the T-Detect SCORE `curvature_t` (the deliverable — the statistic the
paper exposes); NO p-value is emitted (a t-survival of a rescaled-Gaussian z would be an unsupported
transform). The default gaussian output is unchanged. All torch-free (stub `score_fn`). The exact
formula, the default-preservation, the no-p-value caveat, the unknown-tail guard, and the t_df<=2
guards (CLI + direct caller) are pinned."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fast_detect_curvature as fd  # type: ignore  # noqa: E402

_STUDENT_KEYS = ("curvature_t", "tail", "t_df")


def _stub(actual_lp, n=60):
    # each position: mean(sampled) = -2, var = 1 ; curvature_score > 0 iff actual_lp > -2
    return lambda model, text, *, n_samples, seed: [(actual_lp, [-3.0, -1.0, -3.0, -1.0])
                                                    for _ in range(n)]


# --- default-preserving ----------------------------------------------------

def test_gaussian_default_has_no_student_keys():
    g = fd.audit("x", score_fn=_stub(-1.5))                 # default tail=gaussian
    assert not any(k in g for k in _STUDENT_KEYS)


def test_gaussian_claim_license_unchanged():
    g = fd.audit("x", score_fn=_stub(-1.5))
    env = fd.compose_envelope(target_path=None, target_words=10, results=g)
    assert env["claim_license"]["does_not_license"] == fd.DEFAULT_DOES_NOT_LICENSE  # byte-identical


# --- the formula + the deliverable -----------------------------------------

def test_exact_formula():
    t = fd.audit("x", score_fn=_stub(-1.5), tail="student-t", t_df=5)
    d = t["actual_log_prob_sum_nats"] - t["reference_mean_sum_nats"]
    v = t["reference_variance_sum_nats2"]
    assert t["curvature_t"] == pytest.approx(d / math.sqrt((5 / 3) * v))
    assert t["curvature_t"] == pytest.approx(t["curvature_score"] / math.sqrt(5 / 3))
    assert t["t_df"] == 5 and t["tail"] == "student-t"


def test_curvature_t_is_the_deliverable_and_no_p_value():
    # The deliverable is the SCORE curvature_t (the statistic the paper exposes), a global rescale
    # of the Gaussian curvature_score. NO p_value_t is emitted (#228 P1: an unsupported transform).
    t = fd.audit("x", score_fn=_stub(-1.5), tail="student-t", t_df=5)
    assert t["curvature_t"] == pytest.approx(t["curvature_score"] / math.sqrt(5 / 3))
    assert "p_value_t" not in t                       # the unsupported transform is gone


def test_curvature_t_monotonic_in_curvature_score():
    lo = fd.audit("x", score_fn=_stub(-1.8), tail="student-t", t_df=5)   # smaller curvature
    hi = fd.audit("x", score_fn=_stub(-1.2), tail="student-t", t_df=5)   # larger curvature
    assert hi["curvature_score"] > lo["curvature_score"]
    assert hi["curvature_t"] > lo["curvature_t"]                          # monotone rescale


def test_df_robustness_runs_3_to_7():
    for nu in (3, 4, 5, 6, 7):
        t = fd.audit("x", score_fn=_stub(-1.5), tail="student-t", t_df=nu)
        assert t["t_df"] == nu and math.isfinite(t["curvature_t"])
        assert "p_value_t" not in t


# --- posture / claim license -----------------------------------------------

def test_student_t_caveat_and_no_verdict():
    t = fd.audit("x", score_fn=_stub(-1.5), tail="student-t", t_df=5)
    env = fd.compose_envelope(target_path=None, target_words=10, results=t)
    dnl = env["claim_license"]["does_not_license"]
    # caveat leads with curvature_t as the deliverable + states no p-value (unsupported transform)
    assert "curvature_t" in dnl and "deliverable" in dnl
    assert "p-value" in dnl.lower() and "UNSUPPORTED" in dnl
    assert "p_value_t" not in dnl                      # we never name an emitted p-value
    assert not any(k in t for k in ("verdict", "is_ai", "label", "decision", "p_value_t"))


# --- nu guard: CLI (before backend) AND direct caller of audit() ------------

def test_t_df_guard_rejects_le_2(capsys):
    rc = fd.main(["/nonexistent/target.txt", "--tail", "student-t", "--t-df", "2"])
    assert rc == 2
    assert "must be > 2" in capsys.readouterr().err


def test_audit_direct_caller_t_df_le_2_raises():
    # P2 (#228): a direct caller of audit() bypasses the CLI guard — audit() must fail loud
    # rather than divide by zero / take sqrt of a negative at nu<=2.
    for nu in (2, 1, 0, -1):
        with pytest.raises(ValueError, match="t_df must be > 2"):
            fd.audit("x", score_fn=_stub(-1.5), tail="student-t", t_df=nu)


def test_audit_unknown_tail_raises():
    # P2 (#228): a direct caller passing an unknown tail must fail loud, NOT be silently treated
    # as gaussian (the CLI restricts tail via choices=; audit() backstops programmatic callers).
    for bad in ("foo", "gauss", "studentt", "", "Gaussian"):
        with pytest.raises(ValueError, match="unknown tail"):
            fd.audit("x", score_fn=_stub(-1.5), tail=bad)


def test_t_df_guard_not_triggered_in_gaussian(tmp_path):
    # gaussian mode never validates t_df (default path); a bad --t-df with gaussian is ignored
    rc = fd.main([str(tmp_path / "nope.txt"), "--t-df", "2"])
    assert rc == 1                                          # falls through to "target not found"
