#!/usr/bin/env python3
"""Tests for style_vectorizer.py (spec 30) — the interpretable (glass-box) document vectorizer.

M1 is model-free (stdlib only): every path here runs with spaCy absent OR present, because the
surface forces ``include_spacy=False``. Covers the spec-30 contract: the named-feature envelope
shape + determinism, the glass-box bijection (vector keys == feature_space names), single-mode
FULL inventory (no select_feature_names cap), the load-bearing posture guards (no-verdict
recursive walk, never-selects, R4 bounds incl. the sd==0 -> z=None edge), the anti-Goodhart
held-out-disjoint warning, the stdlib-import guard, and the drop-in surface registration.
"""

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

import style_vectorizer as sv  # type: ignore  # noqa: E402
import stylometry_core as sc  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES, validate_results_bounds  # type: ignore  # noqa: E402
from variance_audit import FUNCTION_WORDS  # type: ignore  # noqa: E402

# Long enough that single-mode emits a rich multi-family vector; distinct enough from the
# baseline below that some axes deviate.
_TEXT = (
    "The cat sat on the mat. The rat that the cat chased ran away quickly down the lane. "
    "She walked to the store and bought bread and milk for the week ahead of the holiday. "
    "It seemed, perhaps, that the weather would hold; she was not entirely sure of it though. "
    "Nevertheless, they pressed on, and the road grew longer with every weary step they took. "
    "He could not have known what waited there, nor would he have turned back if he had. "
    "We are, all of us, walking some road or other toward a place we cannot quite see yet."
)

_BASELINE_DOCS = [
    "This writer favors winding sentences; the semicolon appears often, almost a tic. "
    "The dog slept while the birds sang in the trees above the quiet little house all day. "
    "Perhaps the style is recognizable, perhaps not, but it is consistent across the work.",
    "A second baseline document, shorter in its clauses, plainer in its diction throughout. "
    "It states things directly. It does not hedge. It moves from one fact to the next fact. "
    "The reader is never in doubt about where the sentence is going or what it intends to say.",
    "The third sample mixes the two registers, now winding and now plain, by turns and again. "
    "Sometimes a long, qualified, parenthetical thought (the kind that loops back) appears here; "
    "sometimes a blunt one does not. The variance itself is part of what makes the voice a voice.",
]


def _envelope(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = sv.main(argv)
    return rc, json.loads(out.getvalue())


def _write_target(tmp_path) -> Path:
    t = tmp_path / "target.txt"
    t.write_text(_TEXT, encoding="utf-8")
    return t


def _write_baseline(tmp_path) -> Path:
    d = tmp_path / "baseline"
    d.mkdir()
    for i, body in enumerate(_BASELINE_DOCS, start=1):
        (d / f"doc{i}.txt").write_text(body, encoding="utf-8")
    return d


# --- AC 12: surface registration --------------------------------------------

def test_surface_registered():
    assert sv.TASK_SURFACE == "voice_coherence"
    assert "voice_coherence" in VALID_TASK_SURFACES


# --- AC 1: valid envelope ---------------------------------------------------

def test_envelope_shape(tmp_path):
    t = _write_target(tmp_path)
    rc, env = _envelope([str(t), "--json"])
    assert rc == 0
    assert env["schema_version"] == "1.0"
    assert env["task_surface"] == "voice_coherence"
    assert env["tool"] == "style_vectorizer"
    assert env["available"] is True
    assert env["results"]["mode"] == "single"


# --- AC 2: total_dimensions == len(vector_flat); sorted; determinism --------

def test_total_dimensions_and_sorted(tmp_path):
    t = _write_target(tmp_path)
    _, env = _envelope([str(t), "--json"])
    r = env["results"]
    flat = r["vector_flat"]
    assert r["feature_space"]["total_dimensions"] == len(flat)
    dims = [row["dim"] for row in flat]
    assert dims == sorted(dims)


def test_deterministic(tmp_path):
    t = _write_target(tmp_path)
    _, e1 = _envelope([str(t), "--json"])
    _, e2 = _envelope([str(t), "--json"])
    # byte-identical vector_flat (the load-bearing determinism claim)
    assert json.dumps(e1["results"]["vector_flat"], sort_keys=True) == \
        json.dumps(e2["results"]["vector_flat"], sort_keys=True)


# --- AC 3: glass-box bijection (vector keys <-> feature_space names) ---------

def test_glassbox_bijection(tmp_path):
    t = _write_target(tmp_path)
    _, env = _envelope([str(t), "--json"])
    r = env["results"]
    fams = r["feature_space"]["families"]
    vec = r["vector"]
    assert set(fams.keys()) == set(vec.keys())
    for family, blk in fams.items():
        assert blk["n"] == len(blk["names"])
        assert set(blk["names"]) == set(vec[family].keys())
    # vector_flat dims are exactly the family::name product, sorted
    expected = sorted(
        sv._flat_dim(fam, name)
        for fam, blk in fams.items()
        for name in blk["names"]
    )
    assert [row["dim"] for row in r["vector_flat"]] == expected


# --- AC 5: M1 default = only the six stdlib families ------------------------

def test_m1_only_stdlib_families(tmp_path):
    t = _write_target(tmp_path)
    _, env = _envelope([str(t), "--json"])
    fams = set(env["results"]["feature_space"]["families"].keys())
    expected = {
        "function_words", "char_ngrams_3", "char_ngrams_4", "char_ngrams_5",
        "punctuation", "paragraph_dialogue", "pronoun_modal_negation",
    }
    assert fams == expected
    assert "pos_trigrams" not in fams and "dependency_ngrams" not in fams


# --- AC 6: single-mode FULL function-word inventory (no top-100 cap) --------

def test_single_mode_full_function_word_inventory(tmp_path):
    t = _write_target(tmp_path)
    _, env = _envelope([str(t), "--json"])
    fw = env["results"]["feature_space"]["families"]["function_words"]
    # The full sorted FUNCTION_WORDS inventory, NOT capped to DEFAULT_LIMITS=100.
    assert fw["n"] == len(FUNCTION_WORDS)
    assert fw["n"] > sc.DEFAULT_LIMITS["function_words"]  # would fail if capped to 100
    assert fw["names"] == sorted(FUNCTION_WORDS)


# --- AC 4: baseline_relative mode -------------------------------------------

def test_baseline_relative_mode(tmp_path):
    t = _write_target(tmp_path)
    d = _write_baseline(tmp_path)
    _, env = _envelope([str(t), "--baseline-dir", str(d), "--json"])
    r = env["results"]
    assert r["mode"] == "baseline_relative"
    ref = r["baseline_reference"]
    assert len(ref["per_dimension"]) == r["feature_space"]["total_dimensions"]
    assert ref["calibration_status"] == "provisional"
    # every row's dim is a real axis
    axes = {
        sv._flat_dim(fam, name)
        for fam, blk in r["feature_space"]["families"].items()
        for name in blk["names"]
    }
    assert {row["dim"] for row in ref["per_dimension"]} == axes
    # baseline caps apply in this mode (function_words <= DEFAULT_LIMITS)
    assert r["feature_space"]["families"]["function_words"]["n"] <= sc.DEFAULT_LIMITS["function_words"]


def test_single_mode_omits_baseline_reference(tmp_path):
    t = _write_target(tmp_path)
    _, env = _envelope([str(t), "--json"])
    assert "baseline_reference" not in env["results"]


# --- AC 7: no-verdict recursive walk ----------------------------------------

_VERDICT_KEYS = {
    "is_ai", "is_human", "same_author", "verdict", "label", "prediction",
    "class", "flagged", "selected", "score_overall", "decision",
}
_VERDICT_STRINGS = {"ai", "human", "same author", "different author"}


def _walk(node):
    if isinstance(node, dict):
        for k, v in node.items():
            assert k not in _VERDICT_KEYS, f"verdict key {k!r} present"
            _walk(v)
    elif isinstance(node, list):
        for item in node:
            _walk(item)
    elif isinstance(node, str):
        assert node.strip().lower() not in _VERDICT_STRINGS, f"verdict string {node!r}"


def test_no_verdict_walk_single(tmp_path):
    t = _write_target(tmp_path)
    _, env = _envelope([str(t), "--json"])
    _walk(env["results"])


def test_no_verdict_walk_baseline(tmp_path):
    t = _write_target(tmp_path)
    d = _write_baseline(tmp_path)
    _, env = _envelope([str(t), "--baseline-dir", str(d), "--json"])
    _walk(env["results"])


# --- AC 8: never-selects (no aggregate scalar, no ranking field) ------------

def test_never_selects(tmp_path):
    t = _write_target(tmp_path)
    d = _write_baseline(tmp_path)
    _, env = _envelope([str(t), "--baseline-dir", str(d), "--json"])
    r = env["results"]
    # No top-level aggregate distance/score key (the thing voice_distance has, that this
    # surface deliberately refuses). If any of these appears, the no-scalar guarantee is broken.
    banned = {"overall", "weighted_delta", "distance", "score", "rank", "ranking",
              "argmax", "most_likely", "least_likely", "selection", "winner", "top"}
    assert banned.isdisjoint(set(r.keys()))
    # baseline_reference has per_dimension only — no aggregate roll-up scalar.
    ref_keys = set(r["baseline_reference"].keys())
    assert banned.isdisjoint(ref_keys)
    assert "per_dimension" in ref_keys


# --- AC 9: R4 bounds pass; sd==0 -> z=None (not nan) -------------------------

def test_r4_bounds_pass(tmp_path):
    t = _write_target(tmp_path)
    d = _write_baseline(tmp_path)
    res_single, _, _ = sv.vectorize(_TEXT, target_path=str(t))
    validate_results_bounds(res_single)  # raises on violation
    res_base, _, _ = sv.vectorize(_TEXT, baseline_dir=str(d), target_path=str(t))
    validate_results_bounds(res_base)


def test_sd_zero_yields_none_not_nan():
    # A baseline of ONE document => every per-dimension sd is 0 => z must be None, never nan.
    selected = {"punctuation": ["comma_per_100_words"]}
    target_features = {"punctuation": {"comma_per_100_words": 5.0}}
    baseline_features = [{"features": {"punctuation": {"comma_per_100_words": 3.0}}}]
    ref = sv._baseline_reference(
        target_features, selected, baseline_features,
        k_sd=2.0, n_baseline_files=1, n_baseline_words=100,
    )
    row = ref["per_dimension"][0]
    assert row["baseline_sd"] == 0.0
    assert row["z"] is None          # would be a ZeroDivisionError / nan if mishandled
    assert row["band"] == "within"
    validate_results_bounds(ref)     # finiteness check still passes (None is skipped)


def test_band_above_below_within():
    # A non-degenerate baseline: assert the band edges are computed from mean ± k·sd.
    selected = {"punctuation": ["x"]}
    baseline_features = [
        {"features": {"punctuation": {"x": 1.0}}},
        {"features": {"punctuation": {"x": 3.0}}},
    ]  # mean=2.0, sd=sqrt(2)~1.414 (sample sd)
    # value well above mean + 2*sd
    above = sv._baseline_reference(
        {"punctuation": {"x": 100.0}}, selected, baseline_features,
        k_sd=2.0, n_baseline_files=2, n_baseline_words=10)["per_dimension"][0]
    assert above["band"] == "above" and above["z"] > 0
    # value well below mean - 2*sd
    below = sv._baseline_reference(
        {"punctuation": {"x": -100.0}}, selected, baseline_features,
        k_sd=2.0, n_baseline_files=2, n_baseline_words=10)["per_dimension"][0]
    assert below["band"] == "below" and below["z"] < 0
    # value at the mean
    within = sv._baseline_reference(
        {"punctuation": {"x": 2.0}}, selected, baseline_features,
        k_sd=2.0, n_baseline_files=2, n_baseline_words=10)["per_dimension"][0]
    assert within["band"] == "within"


# --- AC 10: anti-Goodhart / held-out disjoint warning -----------------------

def test_held_out_disjoint_warns(tmp_path):
    d = _write_baseline(tmp_path)
    # Vectorize a baseline MEMBER against the baseline => self-comparison warning.
    member = d / "doc1.txt"
    _, env = _envelope([str(member), "--baseline-dir", str(d), "--json"])
    assert any("member of the baseline" in w for w in env["warnings"])


def test_held_out_disjoint_silent_when_disjoint(tmp_path):
    t = _write_target(tmp_path)  # NOT in the baseline dir
    d = _write_baseline(tmp_path)
    _, env = _envelope([str(t), "--baseline-dir", str(d), "--json"])
    assert not any("member of the baseline" in w for w in env["warnings"])


# --- AC 11: stdlib-import guard (spaCy absent) ------------------------------

def test_stdlib_path_with_spacy_absent(tmp_path, monkeypatch):
    # Even with HAS_SPACY forced False at the stylometry_core layer, M1 produces a complete
    # stdlib vector and available:true (M1 forces include_spacy=False regardless).
    monkeypatch.setattr(sc, "HAS_SPACY", False)
    t = _write_target(tmp_path)
    rc, env = _envelope([str(t), "--json"])
    assert rc == 0 and env["available"] is True
    fams = set(env["results"]["feature_space"]["families"].keys())
    assert "pos_trigrams" not in fams and "dependency_ngrams" not in fams
    assert env["results"]["feature_space"]["total_dimensions"] == len(env["results"]["vector_flat"])


# --- AC 13: claim-license matches envelope + enumerates the four refusals ----

def test_claim_license_refusals(tmp_path):
    t = _write_target(tmp_path)
    _, env = _envelope([str(t), "--json"])
    cl = env["claim_license"]
    assert cl["task_surface"] == "voice_coherence"  # build_output enforces ==
    dnl = cl["does_not_license"].lower()
    assert isinstance(cl["does_not_license"], str)  # one prose string, not a list
    assert "authorship" in dnl or "ai/human" in dnl
    assert "same-author" in dnl or "same author" in dnl
    assert "quality" in dnl or "readability" in dnl
    assert "classifier" in dnl or "training target" in dnl


# --- length floor warnings (single mode) ------------------------------------

def test_short_text_warns(tmp_path):
    t = tmp_path / "short.txt"
    t.write_text("A short little text under five hundred words.", encoding="utf-8")
    _, env = _envelope([str(t), "--json"])
    assert any("500 words" in w for w in env["warnings"])


# --- bad input --------------------------------------------------------------

def test_missing_file_bad_input(tmp_path):
    rc, env = _envelope([str(tmp_path / "nope.txt"), "--json"])
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"
    assert rc == 3
