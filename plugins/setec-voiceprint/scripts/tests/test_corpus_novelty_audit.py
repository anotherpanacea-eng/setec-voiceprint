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


def test_self_exclusion_drops_inline_content_copy(tmp_path):
    """Third sibling of the cross_doc_novelty_profile / originality_audit Codex P1: the per-doc
    self-exclusion loop dropped the exact index and PATH-duplicates, but NOT an inline-`text`
    manifest row (resolved_path None) carrying a CONTENT copy of document i sitting at a different
    index. Document i then reconstructs itself from its own inline copy, trivially collapsing its
    novelty to 0.0 and violating the surface contract ('a doc never reconstructs itself').

    Here doc 'a' (a file) has an inline content-copy 'a_inline' (text None) at a different index,
    plus two genuinely-disjoint docs. With the content-fingerprint guard, the inline copy is dropped
    when scoring 'a': 'a' does NOT reconstruct itself (originality stays 1.0 against the disjoint
    rest), and dropped_self counts the content drop. PRE-FIX: 'a' reconstructs itself -> 0.0."""
    a = tmp_path / "a.txt"
    a.write_text(_DOC)
    b = tmp_path / "b.txt"
    b.write_text(_DISJOINT)
    c = tmp_path / "c.txt"
    c.write_text("a third entirely separate line about mountains rivers valleys and open skies now")
    man = tmp_path / "m.jsonl"
    man.write_text("\n".join(json.dumps(r) for r in [
        {"id": "a", "text_path": "a.txt"},
        {"id": "a_inline", "text": _DOC},        # inline CONTENT copy of 'a', resolved_path None
        {"id": "b", "text_path": "b.txt"},
        {"id": "c", "text_path": "c.txt"},
    ]) + "\n")
    rc, env = _envelope(["--manifest", str(man), "--min-ngram", "8", "--json"])
    assert rc == 0
    by_id = {row["id"]: row for row in env["results"]["per_document"]}
    # 'a' must NOT reconstruct itself from its inline copy: novelty is not trivially collapsed.
    assert by_id["a"]["originality"] == pytest.approx(1.0)
    # the content drop is reflected in the honesty count (inclusive of content-dropped duplicates).
    assert env["results"]["assumptions"]["dropped_self"] >= 1


def test_self_exclusion_inline_copy_whitespace_case_variant(tmp_path):
    """The content fingerprint normalizes whitespace/case (normalize_for_char_ngrams), so an inline
    copy of doc 'a' that differs only by trivial whitespace/case is STILL recognized as a copy of
    'a' and dropped when scoring 'a' — 'a' does not reconstruct itself via a near-identical twin."""
    a = tmp_path / "a.txt"
    a.write_text(_DOC)
    b = tmp_path / "b.txt"
    b.write_text(_DISJOINT)
    c = tmp_path / "c.txt"
    c.write_text("a third entirely separate line about mountains rivers valleys and open skies now")
    variant = "  " + _DOC.upper().replace(" ", "   ") + "  "  # same normalized content as _DOC
    man = tmp_path / "m.jsonl"
    man.write_text("\n".join(json.dumps(r) for r in [
        {"id": "a", "text_path": "a.txt"},
        {"id": "a_variant", "text": variant},    # whitespace/case variant of 'a', resolved_path None
        {"id": "b", "text_path": "b.txt"},
        {"id": "c", "text_path": "c.txt"},
    ]) + "\n")
    rc, env = _envelope(["--manifest", str(man), "--min-ngram", "8", "--json"])
    assert rc == 0
    by_id = {row["id"]: row for row in env["results"]["per_document"]}
    assert by_id["a"]["originality"] == pytest.approx(1.0)
    assert env["results"]["assumptions"]["dropped_self"] >= 1


def test_fully_reconstructible_corpus_zero_novelty(tmp_path):
    # Previously used three EXACT copies of _DOC; with content self-exclusion an exact copy of a doc
    # is dropped when scoring that doc (a doc no longer reconstructs itself from its own copy), so the
    # exact-copy corpus now scores 1.0 (correct). Mirror originality_audit's superset fix: doc 'a' is
    # the base; 'b' and 'c' are SUPERSETS of 'a' (a's content + a distinct tail each) — distinct
    # content (not self-excluded) that fully covers 'a'. So 'a' is fully reconstructible from b/c ->
    # originality 0.0 (the "reconstructible -> zero novelty" numeric pin, without an exact self-copy).
    d = _corpus(tmp_path, [
        ("a", _DOC),
        ("b", _DOC + " plus a distinct closing tail unique to b alone here"),
        ("c", _DOC + " followed by another separate tail unique to c entirely"),
    ])
    _, env = _envelope(["--corpus-dir", str(d), "--min-ngram", "8", "--json"])
    r = env["results"]
    by_id = {row["id"]: row for row in r["per_document"]}
    # 'a' is fully covered by its supersets -> zero novelty (reconstructible from the corpus).
    assert by_id["a.txt"]["originality"] == pytest.approx(0.0)
    assert r["novelty_distribution"]["min"] == pytest.approx(0.0)
    # at least one ordered pair reconstructs the other above the share threshold (a covered by b/c).
    assert r["mutual_reconstructibility"]["count"] >= 1


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
    # 'b' is a SUPERSET of 'a' (a's content + a distinct tail) so 'a' is fully reconstructible from b
    # (originality 0.0) WITHOUT 'b' being an exact self-copy of 'a' (which would now be self-excluded);
    # 'c' is disjoint (originality 1.0). The descriptive object is a spread, not a single number.
    d = _corpus(tmp_path, [
        ("a", _DOC),
        ("b", _DOC + " plus a distinct closing tail unique to b alone here"),
        ("c", _DISJOINT),
    ])
    _, env = _envelope(["--corpus-dir", str(d), "--min-ngram", "8", "--json"])
    dist = env["results"]["novelty_distribution"]
    assert dist["min"] < dist["max"]  # the descriptive object: a spread, not a single number


# --- glass-box --------------------------------------------------------------

def test_top_source_is_longest_span_source(tmp_path):
    # 'b' is a SUPERSET of 'a' (a's content + a distinct tail): 'a' is reconstructible from b without
    # b being an exact self-copy (which content self-exclusion would now drop). top_source names the
    # longest-span source (b), not None.
    d = _corpus(tmp_path, [
        ("a", _DOC),
        ("b", _DOC + " plus a distinct closing tail unique to b alone here"),
        ("c", _DISJOINT),
    ])
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
