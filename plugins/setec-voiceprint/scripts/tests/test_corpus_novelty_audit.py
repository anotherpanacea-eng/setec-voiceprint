#!/usr/bin/env python3
"""Tests for corpus_novelty_audit.py (spec 28, M1a) — the set-wide DJ-Search novelty distribution.

Stdlib, deterministic, no model. Covers the spec-28 test contract: deterministic output, envelope
shape, claim-license-present + refuses-verdict (incl. the is_human-augmented no-verdict guard),
no-aggregate-verdict-scalar (G4), set-floor abstention (D3), graceful degradation, self-exclusion,
the numeric pins (identical -> 0.0; disjoint -> 1.0; mixed -> spread), the mutual_share back-door pin,
never-selects (G3), and the corpus-dependence caveat."""

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

import corpus_novelty_audit as cna  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402

_DOC = "the quick brown fox jumps over the lazy dog and then the cat ran away today happily indeed"
_DISJOINT = "wholly unrelated lines concerning distant galaxies and the cosmic background radiation field now"


def _envelope(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = cna.main(argv)
    return rc, json.loads(out.getvalue())


def _corpus(tmp_path, docs):
    d = tmp_path / "corpus"
    d.mkdir()
    for name, text in docs:
        (d / f"{name}.txt").write_text(text)
    return d


# --- determinism + envelope -------------------------------------------------

def test_deterministic(tmp_path):
    d = _corpus(tmp_path, [("a", _DOC), ("b", _DOC), ("c", _DISJOINT)])
    _, e1 = _envelope(["--corpus-dir", str(d), "--min-ngram", "8", "--json"])
    _, e2 = _envelope(["--corpus-dir", str(d), "--min-ngram", "8", "--json"])
    assert e1["results"] == e2["results"]


def test_surface_registered():
    assert "set_level_diversity" in VALID_TASK_SURFACES


def test_envelope_shape(tmp_path):
    d = _corpus(tmp_path, [("a", _DOC), ("b", _DOC), ("c", _DISJOINT)])
    rc, env = _envelope(["--corpus-dir", str(d), "--min-ngram", "8", "--json"])
    assert rc == 0 and env["available"] is True
    assert env["task_surface"] == "set_level_diversity" and env["tool"] == "corpus_novelty_audit"
    assert {"per_document", "novelty_distribution", "mutual_reconstructibility"} <= set(env["results"])
    dist = env["results"]["novelty_distribution"]
    assert {"min", "p25", "median", "p75", "max", "mean", "sd", "histogram"} <= set(dist)
    # set-level envelope metadata (findings P3)
    assert env["baseline"]["n_docs"] == 3 and env["target"]["words"] > 0


# --- posture ----------------------------------------------------------------

def test_claim_license_present_and_refuses_verdict(tmp_path):
    d = _corpus(tmp_path, [("a", _DOC), ("b", _DOC), ("c", _DISJOINT)])
    _, env = _envelope(["--corpus-dir", str(d), "--json"])
    cl = env["claim_license"]
    assert cl["task_surface"] == "set_level_diversity" and cl["licenses"]
    dnl = cl["does_not_license"].lower()
    assert "ai/human" in dnl and "not 'ai'" in dnl and "plagiarism" in dnl
    # no-verdict guard, scoped to results — augmented with is_human (stronger than the shipped set)
    assert not any(k in env["results"]
                   for k in ("verdict", "label", "is_ai", "is_human", "decision"))


def test_no_aggregate_verdict_scalar(tmp_path):
    # G4 / D1: the headline is a distribution object, never a single adjudicating scalar.
    d = _corpus(tmp_path, [("a", _DOC), ("b", _DOC), ("c", _DISJOINT)])
    _, env = _envelope(["--corpus-dir", str(d), "--json"])
    assert not any(k in env["results"]
                   for k in ("originality_score", "corpus_originality", "homogeneity_score"))


def test_never_selects(tmp_path):
    # G3: no winner/flag field; per_document is a descriptive table, not a pick.
    d = _corpus(tmp_path, [("a", _DOC), ("b", _DOC), ("c", _DISJOINT)])
    _, env = _envelope(["--corpus-dir", str(d), "--json"])
    r = env["results"]
    for row in r["per_document"]:
        assert not any(k in row for k in ("flag", "selected", "is_most_original", "is_outlier",
                                          "winner", "rank"))
    # no boolean census field — only the count/fraction (mutual_share back-door pin, findings P3)
    assert "is_reconstructed" not in json.dumps(r)
    assert r["assumptions"]["mutual_share"] == 0.5


def test_corpus_dependence_caveat(tmp_path):
    d = _corpus(tmp_path, [("a", _DOC), ("b", _DOC), ("c", _DISJOINT)])
    _, env = _envelope(["--corpus-dir", str(d), "--json"])
    a = env["results"]["assumptions"]
    assert "register" in a["corpus_dependence"] and "ESL" in a["corpus_dependence"]
    assert "NOT 'AI'" in a["orientation"]


# --- abstention / degradation -----------------------------------------------

def test_set_floor_abstention(tmp_path):
    d = _corpus(tmp_path, [("a", _DOC), ("b", _DISJOINT)])  # 2 docs < default min-docs 3
    rc, env = _envelope(["--corpus-dir", str(d), "--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input" and rc == 3


def test_empty_corpus_bad_input(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    rc, env = _envelope(["--corpus-dir", str(d), "--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input" and rc == 3


def test_all_empty_texts_bad_input(tmp_path):
    d = _corpus(tmp_path, [("a", "   "), ("b", " !! "), ("c", "  ")])
    rc, env = _envelope(["--corpus-dir", str(d), "--json"])
    # 3 files clears the floor, but no tokens -> bad_input (no NaN in the distribution stats)
    assert env["available"] is False and env["reason_category"] == "bad_input"


def test_empty_files_do_not_pad_the_min_docs_floor(tmp_path):
    # Codex P2: 1 real doc + 2 empty files must NOT clear --min-docs (default 3). The floor is on
    # USABLE documents (those with word tokens), so this is bad_input — not a 1-point "distribution".
    d = _corpus(tmp_path, [("real", _DOC), ("e1", "   "), ("e2", " !! ")])
    rc, env = _envelope(["--corpus-dir", str(d), "--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input" and rc == 3
    assert "usable" in env["reason"]


def test_usable_floor_passes_and_reports_dropped_empties(tmp_path):
    # 3 real docs + 1 empty: clears the usable floor, distribution is over the 3, and the dropped
    # empty is reported (raw vs usable vs dropped surfaced, not silently absorbed).
    d = _corpus(tmp_path, [("a", _DOC), ("b", _DOC), ("c", _DISJOINT), ("e", "   ")])
    rc, env = _envelope(["--corpus-dir", str(d), "--json"])
    assert env["available"] is True and rc == 0
    assert env["results"]["n_documents"] == 3
    assert env["baseline"]["n_docs_loaded"] == 4
    assert env["baseline"]["n_docs_dropped_empty"] == 1
    assert any("empty" in w for w in (env.get("warnings") or []))


def test_missing_corpus_dir_bad_input(tmp_path):
    rc, env = _envelope(["--corpus-dir", str(tmp_path / "nope"), "--json"])
    assert env["available"] is False and "bad_input" in json.dumps(env)


# --- self-exclusion + numeric pins ------------------------------------------

def test_self_exclusion_duplicate_doc(tmp_path):
    # The same text in two files at DIFFERENT paths are distinct docs (each reconstructs the other);
    # a doc duplicated at the SAME resolved path is the self-exclusion case. We force a same-path
    # duplicate via a manifest pointing the same text_path twice.
    doc = tmp_path / "a.txt"
    doc.write_text(_DOC)
    other = tmp_path / "b.txt"
    other.write_text(_DISJOINT)
    third = tmp_path / "c.txt"
    third.write_text("a third entirely separate line about mountains rivers valleys and open skies now")
    man = tmp_path / "m.jsonl"
    man.write_text("\n".join(json.dumps(r) for r in [
        {"id": "a", "text_path": "a.txt"},
        {"id": "a_dup", "text_path": "a.txt"},   # same resolved path as 'a'
        {"id": "b", "text_path": "b.txt"},
        {"id": "c", "text_path": "c.txt"},
    ]) + "\n")
    rc, env = _envelope(["--manifest", str(man), "--json"])
    assert rc == 0
    a = env["results"]["assumptions"]
    assert a["dropped_self"] >= 1
    # 'a' must not be reconstructed by its same-path twin: originality stays high (the other two
    # docs share no long span with it)
    by_id = {row["id"]: row for row in env["results"]["per_document"]}
    assert by_id["a"]["originality"] == pytest.approx(1.0)


def test_identical_corpus_zero_novelty(tmp_path):
    d = _corpus(tmp_path, [("a", _DOC), ("b", _DOC), ("c", _DOC)])
    _, env = _envelope(["--corpus-dir", str(d), "--min-ngram", "8", "--json"])
    r = env["results"]
    assert all(row["originality"] == pytest.approx(0.0) for row in r["per_document"])
    assert r["novelty_distribution"]["median"] == pytest.approx(0.0)
    assert r["mutual_reconstructibility"]["fraction"] == pytest.approx(1.0)


def test_identical_distinct_path_files_are_the_redundancy_signal(tmp_path):
    """Lock the diversity-audit semantics against the reverted round-1 content-fingerprint over-fix.

    N identical-text files at DISTINCT paths are NOT the same logical document — content equality
    between separate corpus entries is precisely the set-level redundancy this surface reports. A
    content-based self-exclusion (Codex round-2 P1) would erase every identical peer, reporting
    originality 1.0 for all three with zero mutual pairs — a maximally duplicated corpus made to look
    maximally novel. Self-exclusion is index/path-only: identical-but-distinct-path docs each fully
    reconstruct the others.

    Expect for three identical files at distinct paths: ZERO novelty for every doc, full
    reconstructibility (mutual fraction 1.0 with NONZERO mutual pairs), and NO peer dropped as a
    self-duplicate (each is a real, distinct reference)."""
    d = _corpus(tmp_path, [("a", _DOC), ("b", _DOC), ("c", _DOC)])
    _, env = _envelope(["--corpus-dir", str(d), "--min-ngram", "8", "--json"])
    r = env["results"]
    # every doc is fully reconstructible from its identical peers -> zero novelty across the board.
    assert all(row["originality"] == pytest.approx(0.0) for row in r["per_document"])
    assert r["novelty_distribution"]["max"] == pytest.approx(0.0)
    # full reconstructibility AND nonzero mutual pairs (the over-fix collapsed both to 0).
    assert r["mutual_reconstructibility"]["fraction"] == pytest.approx(1.0)
    assert r["mutual_reconstructibility"]["count"] >= 1
    # identical distinct-path peers are NOT self-excluded: nothing is dropped as a self-duplicate.
    assert r["assumptions"]["dropped_self"] == 0


def test_disjoint_corpus_full_novelty(tmp_path):
    d = _corpus(tmp_path, [
        ("a", "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi"),
        ("b", "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth here"),
        ("c", "monday tuesday wednesday thursday friday saturday sunday autumn winter spring summer noon"),
    ])
    _, env = _envelope(["--corpus-dir", str(d), "--min-ngram", "8", "--json"])
    r = env["results"]
    assert all(row["originality"] == pytest.approx(1.0) for row in r["per_document"])
    assert r["novelty_distribution"]["mean"] == pytest.approx(1.0)
    assert r["mutual_reconstructibility"]["fraction"] == pytest.approx(0.0)


def test_mixed_corpus_spread(tmp_path):
    d = _corpus(tmp_path, [("a", _DOC), ("b", _DOC), ("c", _DISJOINT)])
    _, env = _envelope(["--corpus-dir", str(d), "--min-ngram", "8", "--json"])
    dist = env["results"]["novelty_distribution"]
    assert dist["min"] < dist["max"]  # the descriptive object: a spread, not a single number


# --- glass-box --------------------------------------------------------------

def test_top_source_is_longest_span_source(tmp_path):
    # a is reconstructible from b (identical); top_source names the longest-span source (b), not None.
    d = _corpus(tmp_path, [("a", _DOC), ("b", _DOC), ("c", _DISJOINT)])
    _, env = _envelope(["--corpus-dir", str(d), "--min-ngram", "8", "--json"])
    by_id = {row["id"]: row for row in env["results"]["per_document"]}
    assert by_id["a.txt"]["top_source"] == "b.txt"
    # a fully-novel doc has no matched span -> top_source is null
    assert by_id["c.txt"]["top_source"] is None


def test_manifest_corpus(tmp_path):
    man = tmp_path / "m.jsonl"
    man.write_text("\n".join(json.dumps(r) for r in [
        {"id": "a", "text": _DOC},
        {"id": "b", "text": _DOC},
        {"id": "c", "text": _DISJOINT},
    ]) + "\n")
    rc, env = _envelope(["--manifest", str(man), "--min-ngram", "8", "--json"])
    assert env["available"] is True and env["results"]["n_documents"] == 3
