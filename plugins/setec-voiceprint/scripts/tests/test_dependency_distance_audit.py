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
