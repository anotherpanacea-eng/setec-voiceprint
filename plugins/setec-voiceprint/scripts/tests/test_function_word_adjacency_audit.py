#!/usr/bin/env python3
"""Tests for function_word_adjacency_audit.py (spec 32) — the function-word adjacency NETWORK.

M1 = stdlib + numpy, NO model, runs unconditionally in CI (no skipif). Covers the spec-32
contract: the graph descriptors, the edge-total tie to the run-segmentation primitive (NOT the
truncated function_bigrams public field — review P1), the no-verdict recursive-walk posture guard +
the band.score REMOVAL (review P2), the hardened networkx-disjointness guard + concrete band floor
(review P3), the anti-Goodhart import disjointness, and graceful abstention."""

from __future__ import annotations

import io
import json
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import function_word_adjacency_audit as fwan  # type: ignore  # noqa: E402
import function_word_grammar_audit as ga  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES, validate_results_bounds  # type: ignore  # noqa: E402

# A text with MANY (> 20) distinct function-word bigrams so the grammar audit's
# .most_common(20) truncation would DIVERGE from the true transition total — the
# exact condition that makes the P1 tie load-bearing.
_LONG_RUNS = ("it would have been to the one of those who could not have been "
              "in the same way as if it were of all that we can do for them. "
              "and yet there is to be no more than what we would have had if "
              "only they had been able to do so with us and for him and her. ") * 4


def _envelope(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = fwan.main(argv)
    return rc, json.loads(out.getvalue())


# --- surface registration ---------------------------------------------------

def test_surface_registered():
    assert fwan.TASK_SURFACE == "voice_coherence" and "voice_coherence" in VALID_TASK_SURFACES


# --- AC-1 deterministic -----------------------------------------------------

def test_deterministic():
    assert fwan.audit_function_word_adjacency(_LONG_RUNS) == \
        fwan.audit_function_word_adjacency(_LONG_RUNS)


# --- AC-2 envelope shape / finite leaves ------------------------------------

def test_envelope_shape_and_bounds(tmp_path):
    t = tmp_path / "t.txt"; t.write_text(_LONG_RUNS)
    rc, env = _envelope([str(t), "--json"])
    assert rc == 0 and env["available"] is True
    assert env["schema_version"] == "1.0"
    # 12-key success contract (no R3 error keys).
    assert "reason" not in env and "reason_category" not in env
    validate_results_bounds(env["results"])   # all numeric leaves finite


# --- AC-3 + AC-4 no-verdict recursive walk + no band.score (POSTURE GUARDS) --

_FORBIDDEN_KEYS = {
    "is_ai", "is_human", "verdict", "selected", "selection",
    "threshold", "decision", "class", "prediction",
}


def _walk_keys(obj, path=""):
    """Yield (path, key) for every dict key, recursively."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield (path, k)
            yield from _walk_keys(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, it in enumerate(obj):
            yield from _walk_keys(it, f"{path}[{i}]")


def test_no_verdict_recursive_walk():
    r = fwan.audit_function_word_adjacency(_LONG_RUNS)
    bad = [f"{p}/{k}" for (p, k) in _walk_keys(r) if k in _FORBIDDEN_KEYS]
    assert bad == [], bad
    # `label` is allowed ONLY as band.label (a structure-concentration phrase),
    # never anywhere else.
    label_paths = [(p, k) for (p, k) in _walk_keys(r) if k == "label"]
    assert label_paths == [("/band", "label")], label_paths


def test_no_band_score_scalar():
    # Review P2: the bare, formula-less band.score is REMOVED. No `score` key
    # exists anywhere in results.
    r = fwan.audit_function_word_adjacency(_LONG_RUNS)
    score_paths = [f"{p}/{k}" for (p, k) in _walk_keys(r) if k == "score"]
    assert score_paths == [], score_paths
    # The band carries label + NAMED flagged_signals + calibration_status only.
    band = r["band"]
    assert set(band["flagged_signals"]).issubset(set(fwan._BAND_SIGNAL_NAMES))
    assert band["calibration_status"]["n_calibrated"] == 0  # all-provisional at M1


def test_band_label_in_fixed_vocabulary():
    # On the floor-clearing text the band is offered with a structure-concentration label.
    r = fwan.audit_function_word_adjacency(_LONG_RUNS)
    assert r["band"]["label"] in {
        "diffuse structure", "typical structure", "concentrated structure",
        "insufficient structure",
    }


# --- AC-5 claim-license refuses verdict + names the classifier --------------

def test_claim_license_refuses_verdict(tmp_path):
    t = tmp_path / "t.txt"; t.write_text(_LONG_RUNS)
    _, env = _envelope([str(t), "--json"])
    lic = env["claim_license"]
    assert lic is not None
    dnl = lic["does_not_license"].lower()
    assert "authorship" in dnl or "ai/human" in dnl
    assert "classifier" in dnl              # arXiv:1406.4469 classifier NOT reproduced
    assert "1406.4469" in " ".join(lic["references"])


# --- AC-6 edge-total tie to the RUN SEGMENTATION (review P1) -----------------

def test_edge_total_ties_to_run_segmentation_not_truncated_field():
    runs = fwan.function_word_runs(_LONG_RUNS)
    # (a) tie to the run-segmentation primitive directly.
    seg_total = sum(len(run) - 1 for run in runs if len(run) >= 2)
    r = fwan.audit_function_word_adjacency(_LONG_RUNS)
    assert r["graph"]["total_transitions"] == seg_total
    # (b) tie to the FULL recomputed bigram Counter over those runs.
    full = fwan._bigram_counts(runs)
    assert r["graph"]["total_transitions"] == sum(full.values())
    # (c) prove the truncated public field would DIVERGE (so the tie is not
    #     trivially anchored to a toy <=20-bigram text).
    assert len(full) > 20, "fixture must have >20 distinct bigrams for P1 to bite"
    pub = ga.audit_function_word_grammar(_LONG_RUNS)["function_bigrams"]  # top-20 view
    assert sum(pub.values()) < r["graph"]["total_transitions"]


def test_runs_use_len_ge_2_rule():
    # Isolated function words between content words carry zero edges.
    assert fwan.function_word_runs("the cat the dog the house the tree") == []
    # A genuine run of >= 2 function words is kept.
    assert fwan.function_word_runs("it would have been the one") == [
        ["it", "would", "have", "been", "the", "one"]
    ]


# --- AC-7 graph-descriptor pins ---------------------------------------------

def test_pagerank_sums_to_one():
    import numpy as np
    runs = fwan.function_word_runs(_LONG_RUNS)
    counts = fwan._bigram_counts(runs)
    nodes = sorted({w for pair in counts for w in pair})
    idx = {w: i for i, w in enumerate(nodes)}
    M = np.zeros((len(nodes), len(nodes)))
    for (a, b), c in counts.items():
        M[idx[a], idx[b]] = c
    pr = fwan._pagerank(M, fwan.DEFAULT_PAGERANK_DAMPING)
    assert abs(float(pr.sum()) - 1.0) < 1e-6


def test_n_active_nodes_counts_distinct_participating_function_words():
    runs = fwan.function_word_runs(_LONG_RUNS)
    counts = fwan._bigram_counts(runs)
    distinct = {w for pair in counts for w in pair}
    r = fwan.audit_function_word_adjacency(_LONG_RUNS)
    assert r["graph"]["n_active_nodes"] == len(distinct)
    assert r["graph"]["n_possible_nodes"] == 135


def test_hub_dominated_more_concentrated_than_flat():
    hub = ("of the to the in the on the at the by the from the with the as the "
           "of the to the in the on the at the by the from the with the as the")
    flat = ("of to in on at by from with as of to in on at by from with as "
            "and but or so for nor yet and but or so for nor yet")
    rh = fwan.audit_function_word_adjacency(hub)
    rf = fwan.audit_function_word_adjacency(flat)
    assert rh["centrality"]["pagerank_top1_share"] > rf["centrality"]["pagerank_top1_share"]
    assert rh["centrality"]["pagerank_gini"] > rf["centrality"]["pagerank_gini"]


def test_two_cycles_and_self_loops_exact():
    # 'of the ... the of' yields a reciprocated of<->the pair -> exactly one 2-cycle.
    tc = fwan.audit_function_word_adjacency(
        "of the of the the of the of and so to be it is of the the of")
    assert tc["motifs"]["two_cycles"] == 1
    # 'that that' is a self-loop.
    sl = fwan.audit_function_word_adjacency(
        "that that and so it is to be or not to be that that")
    assert sl["motifs"]["self_loops"] == 1


# --- AC-8 anti-Goodhart import disjointness ---------------------------------

def test_no_held_out_detector_imports():
    src = (SCRIPTS / "function_word_adjacency_audit.py").read_text(encoding="utf-8")
    # FWAN must not import the held-out detector / selection paths.
    for forbidden in ("voice_distance", "surface_disagreement_resolver",
                      "fast_detect_curvature", "binoculars"):
        assert not re.search(rf"\bimport\s+{forbidden}\b", src), forbidden
        assert not re.search(rf"\bfrom\s+{forbidden}\b", src), forbidden


# --- AC-9 stdlib / NO networkx (review P3 — hardened) -----------------------

def test_networkx_not_imported_by_fwan():
    # Robust to networkx being importable in the env (3.6.1) and to a co-import
    # pulling it: snapshot sys.modules, fresh-import FWAN, assert the import
    # added no 'networkx*' key.
    import importlib
    before = set(sys.modules)
    if "function_word_adjacency_audit" in sys.modules:
        importlib.reload(sys.modules["function_word_adjacency_audit"])
    else:  # pragma: no cover
        importlib.import_module("function_word_adjacency_audit")
    added = set(sys.modules) - before
    assert not any(m == "networkx" or m.startswith("networkx.") for m in added), added


def test_source_has_no_networkx_import():
    src = (SCRIPTS / "function_word_adjacency_audit.py").read_text(encoding="utf-8")
    assert "import networkx" not in src and "from networkx" not in src


def test_networkx_absent_from_requirements():
    # The Tier-1 stdlib contract lives at the dependency manifest: networkx is in
    # NO requirements*.txt.
    for req in REPO_ROOT.glob("requirements*.txt"):
        body = req.read_text(encoding="utf-8")
        assert "networkx" not in body, req


# --- AC-10 graceful abstention ----------------------------------------------

def test_missing_target_bad_input(tmp_path):
    rc, env = _envelope([str(tmp_path / "nope.txt"), "--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input" and rc == 3


def test_zero_transitions_bad_input(tmp_path):
    t = tmp_path / "t.txt"; t.write_text("cat dog house tree mountain river ocean")
    rc, env = _envelope([str(t), "--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input" and rc == 3


# --- AC-11 length-confound visibility ---------------------------------------

def test_length_confounders_present():
    r = fwan.audit_function_word_adjacency(_LONG_RUNS)
    assert "n_active_nodes" in r["graph"] and "total_transitions" in r["graph"]


# --- AC-12 band floor (review P3) -------------------------------------------

def test_band_suppressed_below_floor():
    # A short text below the transition floor -> band suppressed, values still emitted.
    short = "it would have been the one of those"
    r = fwan.audit_function_word_adjacency(short)
    assert r["graph"]["total_transitions"] < fwan.BAND_TRANSITION_FLOOR
    assert r["band"]["band_offered"] is False
    assert r["band"]["label"] == "insufficient structure"
    assert r["band"]["flagged_signals"] == []
    # raw values still present
    assert "global_bits" in r["transition_entropy"]


def test_band_offered_above_floor():
    r = fwan.audit_function_word_adjacency(_LONG_RUNS)
    assert r["graph"]["total_transitions"] >= fwan.BAND_TRANSITION_FLOOR
    assert r["band"]["band_offered"] is True
    assert r["band"]["label"] != "insufficient structure"


# --- saturated / degenerate math (pre-flight mode 6) ------------------------

def test_single_repeated_bigram_is_finite():
    # Saturated: one bigram repeated -> a 2-node graph, all finite, band suppressed.
    r = fwan.audit_function_word_adjacency("of the " * 3)
    validate_results_bounds(r)
    assert r["graph"]["n_active_nodes"] == 2
    assert r["centrality"]["pagerank_gini"] >= 0.0


def test_gini_empty_and_singleton():
    import numpy as np
    assert fwan._gini(np.zeros(0)) == 0.0
    assert fwan._gini(np.array([0.0])) == 0.0
    assert fwan._gini(np.array([5.0])) == 0.0
