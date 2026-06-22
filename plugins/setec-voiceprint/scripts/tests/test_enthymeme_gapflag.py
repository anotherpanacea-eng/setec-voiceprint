#!/usr/bin/env python3
"""Tests for enthymeme_gapflag.py (spec 32, M1) — model-free structural enthymeme LOCATION flags.

Stdlib, deterministic, no model, no judge. Covers the spec-32 acceptance criteria (AC-1..AC-14):
stdlib-only import, determinism, the three structural conditions (marked jump / stated-warrant /
tautology guard), terminal-assertion shape, never-authors, the no-verdict recursive walk, the
descriptive (non-gate) band, never-selects (document order, no rank/severity), the length floor +
soft register caveats, and the claim-license refusing authorship + a completeness verdict."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import enthymeme_gapflag as eg  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402

# Forbidden keys (AC-8): no decision/verdict/selection scalar may appear in results.
_FORBIDDEN_KEYS = frozenset({
    "verdict", "soundness", "unsound", "incomplete", "quality", "pass", "fail",
    "score", "decision", "label", "is_ai", "is_human", "flagged_overall",
    "rank", "severity", "confidence", "prediction", "classification",
})
# Forbidden generated-premise keys (AC-7): M1 never authors the missing premise.
_FORBIDDEN_PREMISE_KEYS = frozenset({
    "reconstructed_premise", "suggested_premise", "filled_premise",
    "missing_premise", "premise_text", "generated_premise",
})


def _envelope(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = eg.main(argv)
    return rc, json.loads(out.getvalue())


def _walk_keys(obj):
    """Yield every dict key (recursively) in a results payload."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


# AC-3 / AC-6 fixtures
_MARKED_JUMP = ("Crime rose in the district. Unemployment also rose. "
                "Therefore the mayor should resign immediately.")
# A >= HARD_MIN_WORDS variant for the CLI/envelope-level tests (the short one above is for the
# detector-level unit tests, which have no length floor).
_MARKED_JUMP_LONG = (
    "Crime rose sharply across the entire district over the past eighteen months. "
    "Unemployment also rose during the very same stretch of difficult years. "
    "Therefore the sitting mayor should resign from office immediately and without delay."
)
_STATED_WARRANT = ("Inflation accelerated last quarter. Because interest rates rose sharply, "
                   "prices therefore climbed across the entire board.")
_TAUTOLOGY = "The system is fundamentally fair. Therefore, the system is fundamentally fair."
_TERMINAL = ("The committee reviewed every single proposal in detail. They consulted outside "
             "experts and weighed the costs. The third option suits the growing city.")


# --- AC-1 stdlib only ------------------------------------------------------

def test_import_pulls_no_model():
    """AC-1: importing the module must not pull transformers / torch / spacy / judge_backends."""
    script = (
        "import sys; import enthymeme_gapflag; "
        "assert 'transformers' not in sys.modules, 'transformers imported'; "
        "assert 'torch' not in sys.modules, 'torch imported'; "
        "assert 'spacy' not in sys.modules, 'spacy imported'; "
        "assert 'judge_backends' not in sys.modules, 'judge_backends imported'; "
        "print('clean')"
    )
    proc = subprocess.run([sys.executable, "-c", script], cwd=str(SCRIPTS),
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "clean" in proc.stdout


# --- AC-2 deterministic ----------------------------------------------------

def test_deterministic():
    a = eg.detect_enthymemes(_MARKED_JUMP)
    b = eg.detect_enthymemes(_MARKED_JUMP)
    assert a == b


def test_envelope_deterministic_modulo_envelope(tmp_path):
    tgt = tmp_path / "t.txt"; tgt.write_text(_MARKED_JUMP_LONG)
    _, e1 = _envelope(["--target", str(tgt), "--json"])
    _, e2 = _envelope(["--target", str(tgt), "--json"])
    assert e1["results"] == e2["results"]


# --- AC-3 flags the jump ---------------------------------------------------

def test_flags_marked_jump_no_warrant():
    r = eg.detect_enthymemes(_MARKED_JUMP)
    assert r["n_flags"] == 1
    f = r["enthymeme_gap_flags"][0]
    assert f["candidate_type"] == "suppressed_premise"
    assert f["jump_evidence"]["conclusion_marker"] == "therefore"
    assert f["jump_evidence"]["warrant_bridge_present"] is False
    # grounds are the two preceding sentences (indices 0, 1)
    assert f["jump_evidence"]["ground_window_sentence_indices"] == [0, 1]
    assert f["sentence_index"] == 2


# --- AC-4 does NOT flag a stated warrant -----------------------------------

def test_does_not_flag_stated_warrant():
    r = eg.detect_enthymemes(_STATED_WARRANT)
    assert r["n_flags"] == 0
    assert r["marker_tally"]["warrant_bridges"] >= 1


# --- AC-5 does NOT flag a tautological echo --------------------------------

def test_does_not_flag_tautology():
    r = eg.detect_enthymemes(_TAUTOLOGY)
    assert r["n_flags"] == 0  # content-overlap ceiling suppresses the restatement


def test_warrant_lexicon_excludes_bare_for_as():
    """Regression: bare 'for'/'as' (non-inferential prepositions) must NOT count as a warrant
    bridge — otherwise 'toxic chemicals for years' would silently suppress a real flag."""
    text = ("The plant dumped chemicals for years. The river died. "
            "Therefore the plant must close.")
    r = eg.detect_enthymemes(text)
    assert r["n_flags"] == 1
    assert r["marker_tally"]["warrant_bridges"] == 0


# --- AC-6 terminal-assertion shape -----------------------------------------

def test_terminal_assertion_flagged():
    r = eg.detect_enthymemes(_TERMINAL)
    flags = r["enthymeme_gap_flags"]
    assert any(f["jump_evidence"]["conclusion_marker"] == "terminal-assertion" for f in flags)
    assert r["marker_tally"]["terminal_assertions"] >= 1


def test_no_include_terminal_suppresses_terminal_flags():
    on = eg.detect_enthymemes(_TERMINAL, include_terminal=True)
    off = eg.detect_enthymemes(_TERMINAL, include_terminal=False)
    assert any(f["jump_evidence"]["conclusion_marker"] == "terminal-assertion"
               for f in on["enthymeme_gap_flags"])
    assert all(f["jump_evidence"]["conclusion_marker"] != "terminal-assertion"
               for f in off["enthymeme_gap_flags"])
    assert off["marker_tally"]["terminal_assertions"] == 0


# --- AC-7 never authors ----------------------------------------------------

def test_never_authors_no_premise_key(tmp_path):
    tgt = tmp_path / "t.txt"; tgt.write_text(_MARKED_JUMP_LONG)
    _, env = _envelope(["--target", str(tgt), "--json"])
    keys = set(_walk_keys(env["results"]))
    assert not (keys & _FORBIDDEN_PREMISE_KEYS), keys & _FORBIDDEN_PREMISE_KEYS
    # And no flag carries any generated-text-shaped field.
    for f in env["results"]["enthymeme_gap_flags"]:
        assert set(f) == {"candidate_type", "sentence_index", "paragraph_index",
                          "span_text", "jump_evidence"}


# --- AC-8 no-verdict recursive walk ----------------------------------------

def test_no_verdict_recursive_walk(tmp_path):
    tgt = tmp_path / "t.txt"; tgt.write_text(_MARKED_JUMP_LONG)
    _, env = _envelope(["--target", str(tgt), "--json"])
    keys = list(_walk_keys(env["results"]))
    bad = {k for k in keys if k in _FORBIDDEN_KEYS or k.endswith("_score")}
    assert not bad, f"forbidden keys leaked into results: {bad}"
    assert env["results"]["calibration_status"] == "uncalibrated"
    assert env["results"]["gap_density"]["calibration_status"] == "uncalibrated"


# --- AC-9 band is descriptive, not a gate ----------------------------------

def test_band_is_descriptive_not_a_gate(tmp_path):
    tgt = tmp_path / "t.txt"; tgt.write_text(_MARKED_JUMP_LONG)
    _, env = _envelope(["--target", str(tgt), "--json"])
    gd = env["results"]["gap_density"]
    assert gd["band"] in eg.BAND_LABELS
    assert set(gd["band_edges"]) == {"low", "high"}
    # The band must not be turned into a boolean pass/fail anywhere.
    keys = set(_walk_keys(env["results"]))
    assert "flagged_overall" not in keys and "decision" not in keys


def test_band_thresholds_map_to_labels():
    assert eg._gap_density_band(0.0) == "sparse"
    assert eg._gap_density_band(0.10) == "sparse"
    assert eg._gap_density_band(0.40) == "typical"
    assert eg._gap_density_band(0.90) == "dense"


# --- AC-10 never-selects ---------------------------------------------------

def test_flags_in_document_order_no_rank():
    # several marked conclusions across paragraphs
    text = ("Sales fell. Costs rose. Therefore the firm is failing.\n\n"
            "Morale dropped. Turnover spiked. Thus the policy backfired.")
    r = eg.detect_enthymemes(text)
    idxs = [f["sentence_index"] for f in r["enthymeme_gap_flags"]]
    assert idxs == sorted(idxs)
    for f in r["enthymeme_gap_flags"]:
        assert "rank" not in f and "severity" not in f and "confidence" not in f
        assert "rank" not in f["jump_evidence"] and "severity" not in f["jump_evidence"]


# --- AC-11 length floor + soft register caveat -----------------------------

def test_hard_min_words_bad_input(tmp_path):
    tgt = tmp_path / "t.txt"; tgt.write_text("Too short to scan.")
    rc, env = _envelope(["--target", str(tgt), "--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input" and rc == 3


def test_short_passage_soft_caveat_not_abstain(tmp_path):
    # >= HARD_MIN_WORDS but < MIN_WORDS -> runs, with a soft caveat (no abstain)
    tgt = tmp_path / "t.txt"; tgt.write_text(_MARKED_JUMP_LONG)
    rc, env = _envelope(["--target", str(tgt), "--json"])
    assert rc == 0 and env["available"] is True
    assert any("short" in w for w in env["results"]["register_warnings"])


def test_no_markers_soft_caveat(tmp_path):
    # One flat sentence per paragraph: no conclusion marker, no warrant bridge, and no
    # terminal-assertion (a lone sentence in its paragraph has no prior ground), so the tally is
    # all-zero. Several paragraphs clear the HARD_MIN_WORDS floor without introducing structure.
    text = ("The garden was quiet that warm afternoon in the village.\n\n"
            "A cat slept on the broad stone wall by the gate.\n\n"
            "Somewhere a kettle whistled softly in a kitchen nearby.")
    tgt = tmp_path / "t.txt"; tgt.write_text(text)
    rc, env = _envelope(["--target", str(tgt), "--json"])
    assert rc == 0 and env["available"] is True
    assert env["results"]["marker_tally"] == {
        "conclusion_markers": 0, "terminal_assertions": 0, "warrant_bridges": 0}
    assert any("inferential markers" in w for w in env["results"]["register_warnings"])


# --- AC-12 claim license refuses authorship + verdict ----------------------

def test_claim_license_refuses_authorship_and_verdict(tmp_path):
    tgt = tmp_path / "t.txt"; tgt.write_text(_MARKED_JUMP_LONG)
    _, env = _envelope(["--target", str(tgt), "--json"])
    cl = env["claim_license"]
    assert cl["task_surface"] == "argument_pattern_scan"
    dnl = cl["does_not_license"].lower()
    # (a) refuses authoring/filling the missing premise
    assert "author" in dnl and "premise" in dnl
    # (b) refuses any completeness / soundness / incomplete determination
    assert "incomplete" in dnl and "soundness" in dnl
    assert "uncalibrated" in dnl


def test_surface_registered():
    assert "argument_pattern_scan" in VALID_TASK_SURFACES


# --- envelope shape --------------------------------------------------------

def test_envelope_shape(tmp_path):
    tgt = tmp_path / "t.txt"; tgt.write_text(_MARKED_JUMP_LONG)
    rc, env = _envelope(["--target", str(tgt), "--json"])
    assert rc == 0 and env["available"] is True
    assert env["task_surface"] == "argument_pattern_scan" and env["tool"] == "enthymeme_gapflag"
    r = env["results"]
    assert r["method_version"] == eg.METHOD_VERSION
    assert r["marker_version"] == eg.MARKER_VERSION
    assert {"enthymeme_gap_flags", "gap_density", "marker_tally", "n_flags",
            "n_sentences", "n_paragraphs"} <= set(r)


def test_invalid_utf8_target_is_bad_input(tmp_path):
    tgt = tmp_path / "bad.txt"; tgt.write_bytes(b"\xff\xfe not utf-8 \x80\x81")
    rc, env = _envelope(["--target", str(tgt), "--json"])
    assert env["available"] is False and "bad_input" in json.dumps(env)


def test_bad_overlap_ceiling_rejected(tmp_path):
    tgt = tmp_path / "t.txt"; tgt.write_text(_MARKED_JUMP)
    rc = eg.main(["--target", str(tgt), "--content-overlap-ceiling", "1.5", "--json"])
    assert rc == 2


def test_negative_ground_window_rejected(tmp_path):
    tgt = tmp_path / "t.txt"; tgt.write_text(_MARKED_JUMP)
    rc = eg.main(["--target", str(tgt), "--ground-window", "-1", "--json"])
    assert rc == 2


def test_marker_word_boundary_no_false_so():
    """'so' as a conclusion marker must be word-boundaried — 'soak'/'sole' do not match."""
    assert eg._find_conclusion_marker("He soaked the sole of his shoe.") is None
    assert eg._find_conclusion_marker("So the conclusion stands.") == "so"


def test_ground_window_zero_yields_empty_window():
    """Regression (mode-6): ground_window=0 must give an EMPTY ground window — `list[-0:]` is
    the whole list in Python, so a naive slice would silently use every prior sentence."""
    text = "Crime rose sharply. Unemployment rose too. Therefore the mayor must resign now."
    r0 = eg.detect_enthymemes(text, ground_window=0)
    assert r0["n_flags"] == 1
    assert r0["enthymeme_gap_flags"][0]["jump_evidence"]["ground_window_sentence_indices"] == []
    r_default = eg.detect_enthymemes(text)
    assert r_default["enthymeme_gap_flags"][0]["jump_evidence"][
        "ground_window_sentence_indices"] == [0, 1]


def test_empty_text_no_division_by_zero():
    """An input with no segmentable sentences yields zero steps and gap_density 0.0, not a crash."""
    r = eg.detect_enthymemes("...")
    assert r["n_flags"] == 0
    assert r["gap_density"]["value"] == 0.0
    assert r["gap_density"]["n_inferential_steps"] == 0
