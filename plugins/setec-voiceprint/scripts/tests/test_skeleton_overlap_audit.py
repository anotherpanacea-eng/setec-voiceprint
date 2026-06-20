#!/usr/bin/env python3
"""Tests for skeleton_overlap_audit.py (spec 28, M1b) — cross-document discourse-skeleton reuse.

Stdlib, deterministic, no model. Covers the spec-28 test contract: deterministic output, envelope
shape, claim-license-present + refuses-verdict (incl. the is_human-augmented no-verdict guard),
no-aggregate-verdict-scalar (G4), set-floor abstention (D3), graceful degradation, the M2 model-lens
fail-loud gate (G7), the numeric pins (same template -> high overlap + cluster; different shapes ->
low), the topic-invariance pin (same skeleton, different vocabulary -> high overlap — the whole point
vs originality_audit), glass-box readability, never-selects (G3), the report-threshold-is-descriptive
pin, and the corpus-dependence caveat."""

from __future__ import annotations

import importlib
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import skeleton_overlap_audit as soa  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402

# Two docs sharing an ORDERED discourse template (sequence / contrast / cause / sequence) with
# MATCHED per-sentence word counts but entirely different content words — the topic-invariance case.
# (Matched lengths keep the length-tercile dimension equal so the skeleton is byte-identical; the
# point of the pin is that the SHAPE matches despite zero shared vocabulary.)
_TPL_A = ("First, we examined the gathered evidence. However, the numbers stubbornly disagreed. "
          "Therefore the team revised everything. Finally, we published the corrected report.")
_TPL_B = ("First, she warmed the morning kitchen. However, the dough stubbornly refused. "
          "Therefore the baker adjusted everything. Finally, she served the finished bread.")
# A structurally different doc — flat declaratives, no discourse markers.
_FLAT = ("The cat slept on the mat. The dog barked at noon. Birds flew overhead all morning. "
         "Nothing else happened that day.")


def _envelope(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = soa.main(argv)
    return rc, json.loads(out.getvalue())


def _corpus(tmp_path, docs):
    d = tmp_path / "corpus"
    d.mkdir()
    for name, text in docs:
        (d / f"{name}.txt").write_text(text)
    return d


# --- determinism + envelope -------------------------------------------------

def test_deterministic(tmp_path):
    d = _corpus(tmp_path, [("a", _TPL_A), ("b", _TPL_B), ("c", _FLAT)])
    _, e1 = _envelope(["--corpus-dir", str(d), "--json"])
    _, e2 = _envelope(["--corpus-dir", str(d), "--json"])
    assert e1["results"] == e2["results"]


def test_surface_registered():
    assert "set_level_diversity" in VALID_TASK_SURFACES


def test_envelope_shape(tmp_path):
    d = _corpus(tmp_path, [("a", _TPL_A), ("b", _TPL_B), ("c", _FLAT)])
    rc, env = _envelope(["--corpus-dir", str(d), "--json"])
    assert rc == 0 and env["available"] is True
    assert env["task_surface"] == "set_level_diversity" and env["tool"] == "skeleton_overlap_audit"
    assert {"skeleton_overlap", "pair_table", "template_clusters", "per_document"} <= set(env["results"])
    assert {"mean_pairwise", "median_pairwise", "max_pairwise"} <= set(env["results"]["skeleton_overlap"])
    assert env["baseline"]["n_docs"] == 3


# --- posture ----------------------------------------------------------------

def test_claim_license_present_and_refuses_verdict(tmp_path):
    d = _corpus(tmp_path, [("a", _TPL_A), ("b", _TPL_B), ("c", _FLAT)])
    _, env = _envelope(["--corpus-dir", str(d), "--json"])
    cl = env["claim_license"]
    assert cl["task_surface"] == "set_level_diversity" and cl["licenses"]
    dnl = cl["does_not_license"].lower()
    assert "ai/human" in dnl and "not 'ai'" in dnl and "plagiarism" in dnl
    assert not any(k in env["results"]
                   for k in ("verdict", "label", "is_ai", "is_human", "decision"))


def test_no_aggregate_verdict_scalar(tmp_path):
    d = _corpus(tmp_path, [("a", _TPL_A), ("b", _TPL_B), ("c", _FLAT)])
    _, env = _envelope(["--corpus-dir", str(d), "--json"])
    assert not any(k in env["results"]
                   for k in ("originality_score", "corpus_originality", "homogeneity_score",
                             "skeleton_score"))


def test_never_selects_and_report_threshold_descriptive(tmp_path):
    d = _corpus(tmp_path, [("a", _TPL_A), ("b", _TPL_B), ("c", _FLAT)])
    _, env = _envelope(["--corpus-dir", str(d), "--json"])
    r = env["results"]
    # no winner/flag field on the pair rows
    for row in r["pair_table"]:
        assert not any(k in row for k in ("flag", "selected", "is_template", "winner", "verdict"))
    # report_threshold groups for display only and is surfaced (findings P3)
    assert r["assumptions"]["report_threshold"] == 0.8
    # template_clusters is a grouping, not a verdict band — its values are doc-id lists
    for grp in r["template_clusters"]:
        assert isinstance(grp, list) and all(isinstance(x, str) for x in grp)


def test_corpus_dependence_caveat(tmp_path):
    d = _corpus(tmp_path, [("a", _TPL_A), ("b", _TPL_B), ("c", _FLAT)])
    _, env = _envelope(["--corpus-dir", str(d), "--json"])
    a = env["results"]["assumptions"]
    assert "register" in a["corpus_dependence"]
    assert "NOT 'AI'" in a["orientation"]
    assert "content words excluded" in a["topic_robust"]


# --- abstention / degradation / model gate ----------------------------------

def test_set_floor_abstention(tmp_path):
    d = _corpus(tmp_path, [("a", _TPL_A), ("b", _TPL_B)])  # 2 docs < default 3
    rc, env = _envelope(["--corpus-dir", str(d), "--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input" and rc == 3


def test_empty_corpus_bad_input(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    rc, env = _envelope(["--corpus-dir", str(d), "--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input" and rc == 3


def test_model_lens_fails_loud_when_absent(tmp_path):
    # G7: --qud-lens model with no client -> missing_dependency, never a silent proxy fallback.
    # Skip only if the (currently nonexistent) client is somehow importable.
    if importlib.util.find_spec("qud_model_client") is not None:  # pragma: no cover
        pytest.skip("qud_model_client present — model lens would run")
    d = _corpus(tmp_path, [("a", _TPL_A), ("b", _TPL_B), ("c", _FLAT)])
    rc, env = _envelope(["--corpus-dir", str(d), "--qud-lens", "model", "--json"])
    assert env["available"] is False and env["reason_category"] == "missing_dependency" and rc == 3
    assert "proxy" in env["reason"].lower()  # names the fallback the operator can use instead


# --- numeric pins -----------------------------------------------------------

def test_same_template_high_overlap_and_clusters(tmp_path):
    d = _corpus(tmp_path, [("a", _TPL_A), ("b", _TPL_B), ("c", _FLAT)])
    _, env = _envelope(["--corpus-dir", str(d), "--json"])
    r = env["results"]
    assert r["skeleton_overlap"]["max_pairwise"] >= 0.8
    # the templated pair tops the pair_table
    top = r["pair_table"][0]
    assert {top["a"], top["b"]} == {"a.txt", "b.txt"}
    # and they fall in one template cluster
    assert any(set(grp) == {"a.txt", "b.txt"} for grp in r["template_clusters"])


def test_topic_invariance(tmp_path):
    # _TPL_A and _TPL_B share NO content words but the SAME discourse skeleton -> high overlap.
    # This is the load-bearing property vs originality_audit (structural, not lexical).
    d = _corpus(tmp_path, [("a", _TPL_A), ("b", _TPL_B), ("c", _FLAT)])
    _, env = _envelope(["--corpus-dir", str(d), "--json"])
    by_pair = {frozenset((p["a"], p["b"])): p for p in
               # rebuild the full pair list deterministically from the table (top_k default covers all)
               env["results"]["pair_table"]}
    ab = by_pair[frozenset(("a.txt", "b.txt"))]
    assert ab["overlap"] >= 0.8
    # the two docs have identical skeleton strings despite different words
    by_id = {row["id"]: row for row in env["results"]["per_document"]}
    assert by_id["a.txt"]["skeleton"] == by_id["b.txt"]["skeleton"]


def test_different_shapes_low_overlap(tmp_path):
    d = _corpus(tmp_path, [("a", _TPL_A), ("b", _TPL_B), ("c", _FLAT)])
    _, env = _envelope(["--corpus-dir", str(d), "--json"])
    by_pair = {frozenset((p["a"], p["b"])): p for p in env["results"]["pair_table"]}
    ac = by_pair[frozenset(("a.txt", "c.txt"))]
    assert ac["overlap"] < 0.8
    # the flat doc is in no cluster with the templated pair
    for grp in env["results"]["template_clusters"]:
        assert "c.txt" not in grp


# --- glass-box --------------------------------------------------------------

def test_skeleton_is_readable_symbol_string(tmp_path):
    d = _corpus(tmp_path, [("a", _TPL_A), ("b", _TPL_B), ("c", _FLAT)])
    _, env = _envelope(["--corpus-dir", str(d), "--json"])
    for row in env["results"]["per_document"]:
        assert isinstance(row["skeleton"], str) and row["skeleton"]
        # each symbol is 3 chars: bucket-code + tercile + terminal
        for sym in row["skeleton"].split():
            assert len(sym) == 3
    # the shared aligned run is human-readable too
    top = env["results"]["pair_table"][0]
    assert isinstance(top["shared_skeleton"], str)


def test_unit_skeleton_helper_topic_robust():
    # direct unit test of the proxy: same skeleton from disjoint vocabulary
    a = soa.skeleton_for(_TPL_A)
    b = soa.skeleton_for(_TPL_B)
    assert a["skeleton"] == b["skeleton"] and a["n_units"] == 4
