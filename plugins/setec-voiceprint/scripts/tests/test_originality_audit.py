#!/usr/bin/env python3
"""Tests for originality_audit.py (spec 22, M1) — DJ-Search reconstructibility vs a reference pool.

Stdlib, deterministic, no model. Covers the spec-22 test contract: deterministic output, envelope
shape, claim-license-present + refuses-verdict, graceful degradation (empty pool → bad_input),
self-exclusion, the numeric pins (verbatim copy → coverage≈1; disjoint → originality≈1), and the
corpus-dependence/ESL caveat."""

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

import originality_audit as oa  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402

_REF = [("r1", "the quick brown fox jumps over the lazy dog and then the cat ran away"),
        ("r2", "wholly unrelated lines about distant galaxies and cosmic background radiation")]


def _envelope(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = oa.main(argv)
    return rc, json.loads(out.getvalue())


def _files(tmp_path, ref=_REF, target="the quick brown fox jumps over the lazy dog and then the cat WALKED off"):
    rdir = tmp_path / "ref"; rdir.mkdir()
    for name, text in ref:
        (rdir / f"{name}.txt").write_text(text)
    tgt = tmp_path / "target.txt"; tgt.write_text(target)
    return rdir, tgt


# --- method (audit_originality) -------------------------------------------

def test_deterministic():
    a = oa.audit_originality("alpha beta gamma delta epsilon zeta eta theta iota", _REF, min_ngram=4)
    b = oa.audit_originality("alpha beta gamma delta epsilon zeta eta theta iota", _REF, min_ngram=4)
    assert a == b


def test_verbatim_copy_full_coverage():
    # a target that IS one reference doc -> coverage ~1.0, originality ~0.0
    r = oa.audit_originality(_REF[0][1], _REF, min_ngram=8)
    assert r["coverage"] == pytest.approx(1.0) and r["originality"] == pytest.approx(0.0)
    assert r["attribution"][0]["source"] == "r1"


def test_disjoint_full_originality():
    r = oa.audit_originality("entirely novel sentence sharing no long span with the pool whatsoever indeed truly",
                             _REF, min_ngram=8)
    assert r["originality"] == pytest.approx(1.0) and r["n_matched_spans"] == 0


def test_partial_coverage_counts_only_min_ngram_spans():
    # 13-token shared prefix, min_ngram 8 -> that span counts; min_ngram 20 -> it doesn't
    target = "the quick brown fox jumps over the lazy dog and then the cat WALKED off elsewhere now"
    hit = oa.audit_originality(target, _REF, min_ngram=8)
    miss = oa.audit_originality(target, _REF, min_ngram=20)
    assert hit["n_matched_spans"] == 1 and hit["coverage"] > 0.0
    assert miss["n_matched_spans"] == 0 and miss["originality"] == pytest.approx(1.0)
    assert hit["longest_match_tokens"] >= 13


def test_empty_target_raises():
    with pytest.raises(ValueError):
        oa.audit_originality("   !!!   ", _REF)


def test_empty_reference_raises():
    with pytest.raises(ValueError):
        oa.audit_originality("some real words here", [("r", "   ")])


def test_caveat_present():
    r = oa.audit_originality("alpha beta gamma delta epsilon zeta eta theta", _REF, min_ngram=4)
    a = r["assumptions"]
    assert "less reconstructible" in a["orientation"] and "NOT 'more human'" in a["orientation"]
    assert "register" in a["corpus_dependence"] and "ESL" in a["corpus_dependence"]


# --- envelope / CLI --------------------------------------------------------

def test_surface_registered():
    assert "set_level_diversity" in VALID_TASK_SURFACES


def test_envelope_shape(tmp_path):
    rdir, tgt = _files(tmp_path)
    rc, env = _envelope(["--target", str(tgt), "--reference-dir", str(rdir), "--json"])
    assert rc == 0 and env["available"] is True
    assert env["task_surface"] == "set_level_diversity" and env["tool"] == "originality_audit"
    assert {"coverage", "originality", "longest_match_tokens", "n_matched_spans",
            "attribution", "min_ngram"} <= set(env["results"])


def test_claim_license_present_and_refuses_verdict(tmp_path):
    rdir, tgt = _files(tmp_path)
    _, env = _envelope(["--target", str(tgt), "--reference-dir", str(rdir), "--json"])
    cl = env["claim_license"]
    assert cl["task_surface"] == "set_level_diversity" and cl["licenses"]
    dnl = cl["does_not_license"].lower()
    assert "ai/human" in dnl and "not 'ai'" in dnl and "plagiarism" in dnl
    # no verdict/label field smuggled into results
    assert not any(k in env["results"] for k in ("verdict", "label", "is_ai", "decision"))


def test_empty_pool_bad_input(tmp_path):
    empty = tmp_path / "empty"; empty.mkdir()
    tgt = tmp_path / "t.txt"; tgt.write_text("some words to audit here please")
    rc, env = _envelope(["--target", str(tgt), "--reference-dir", str(empty), "--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input" and rc == 3


def test_self_exclusion(tmp_path):
    # target sits INSIDE its own reference dir -> dropped, not reconstructed from itself
    rdir, _ = _files(tmp_path)
    tgt = rdir / "r1.txt"            # the target IS a reference file
    rc, env = _envelope(["--target", str(tgt), "--reference-dir", str(rdir), "--json"])
    assert rc == 0
    # without self-exclusion this would be coverage 1.0; the only other doc shares no long span
    assert env["results"]["originality"] == pytest.approx(1.0)
    assert any("self-exclusion" in w for w in (env.get("warnings") or []))


def test_short_target_warns(tmp_path):
    rdir, _ = _files(tmp_path)
    tgt = tmp_path / "short.txt"; tgt.write_text("only three words")
    rc, env = _envelope(["--target", str(tgt), "--reference-dir", str(rdir),
                         "--min-ngram", "8", "--json"])
    assert rc == 0 and env["results"]["originality"] == pytest.approx(1.0)
    assert any("min_ngram" in w for w in (env.get("warnings") or []))


def test_manifest_reference(tmp_path):
    doc = tmp_path / "a.txt"; doc.write_text(_REF[0][1])
    man = tmp_path / "m.jsonl"
    man.write_text(json.dumps({"id": "a", "text_path": "a.txt"}) + "\n"
                   + json.dumps({"id": "b", "text": "inline reference text not shared"}) + "\n")
    tgt = tmp_path / "t.txt"; tgt.write_text(_REF[0][1])     # verbatim of doc a
    _, env = _envelope(["--target", str(tgt), "--manifest", str(man), "--json"])
    assert env["available"] is True and env["results"]["coverage"] == pytest.approx(1.0)


# --- #225 P2 regressions ---------------------------------------------------

def test_missing_reference_dir_is_bad_input(tmp_path):
    """A missing --reference-dir / --manifest must return a bad_input envelope, not traceback."""
    tgt = tmp_path / "t.txt"; tgt.write_text("alpha beta gamma delta epsilon")
    rc, env = _envelope(["--target", str(tgt),
                         "--reference-dir", str(tmp_path / "does_not_exist"), "--json"])
    assert env["available"] is False
    assert "bad_input" in json.dumps(env)
    assert rc == 3


def test_missing_manifest_is_bad_input(tmp_path):
    tgt = tmp_path / "t.txt"; tgt.write_text("alpha beta gamma delta epsilon")
    rc, env = _envelope(["--target", str(tgt),
                         "--manifest", str(tmp_path / "nope.jsonl"), "--json"])
    assert env["available"] is False and "bad_input" in json.dumps(env)


def test_span_cap_is_surfaced_not_hidden(tmp_path):
    """The per-span cap must be surfaced: when hit, longest_match_tokens is a lower bound and
    longest_match_capped is True (raising --max-span recovers the exact value)."""
    long_ref = " ".join(f"w{i}" for i in range(40))
    rdir = tmp_path / "ref"; rdir.mkdir(); (rdir / "r.txt").write_text(long_ref)
    tgt = tmp_path / "t.txt"; tgt.write_text(long_ref)            # full verbatim of the reference
    # cap below the true 40-token span -> capped True, longest == cap
    rc, env = _envelope(["--target", str(tgt), "--reference-dir", str(rdir),
                         "--min-ngram", "3", "--max-span", "10", "--json"])
    r = env["results"]
    assert r["max_span_cap"] == 10
    assert r["longest_match_tokens"] == 10 and r["longest_match_capped"] is True
    assert "span_cap" in r["assumptions"]
    # raising the cap recovers the exact longest span and clears the flag
    _, env2 = _envelope(["--target", str(tgt), "--reference-dir", str(rdir),
                         "--min-ngram", "3", "--max-span", "256", "--json"])
    assert env2["results"]["longest_match_tokens"] == 40
    assert env2["results"]["longest_match_capped"] is False


def test_max_span_below_min_ngram_rejected(tmp_path):
    rdir, tgt = _files(tmp_path)
    # validation exits 2 before emitting any JSON — call main() directly (not _envelope)
    rc = oa.main(["--target", str(tgt), "--reference-dir", str(rdir),
                  "--min-ngram", "5", "--max-span", "3", "--json"])
    assert rc == 2


# --- #225 P2 round-2: invalid UTF-8 + non-object JSONL rows -----------------

def test_invalid_utf8_target_is_bad_input(tmp_path):
    tgt = tmp_path / "bad.txt"; tgt.write_bytes(b"\xff\xfe not utf-8 \x80\x81")
    rdir = tmp_path / "ref"; rdir.mkdir(); (rdir / "r.txt").write_text("some reference text here")
    rc, env = _envelope(["--target", str(tgt), "--reference-dir", str(rdir), "--json"])
    assert env["available"] is False and "bad_input" in json.dumps(env)


def test_invalid_utf8_manifest_is_bad_input(tmp_path):
    tgt = tmp_path / "t.txt"; tgt.write_text("alpha beta gamma delta epsilon")
    man = tmp_path / "bad.jsonl"; man.write_bytes(b"\xff\xfe\x00 garbage bytes")
    rc, env = _envelope(["--target", str(tgt), "--manifest", str(man), "--json"])
    assert env["available"] is False and "bad_input" in json.dumps(env)


def test_non_object_jsonl_rows_skipped_not_traceback(tmp_path):
    tgt = tmp_path / "t.txt"; tgt.write_text("alpha beta gamma delta epsilon zeta")
    man = tmp_path / "m.jsonl"
    man.write_text('[1,2,3]\n42\n"a bare string"\n'
                   + json.dumps({"id": "a", "text": "alpha beta gamma delta epsilon zeta"}) + "\n")
    rc, env = _envelope(["--target", str(tgt), "--manifest", str(man), "--json"])
    assert env["available"] is True                       # the non-object rows are skipped, not fatal
    assert env["results"]["n_reference_docs"] == 1        # only the one valid object row is used
