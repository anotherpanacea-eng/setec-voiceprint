#!/usr/bin/env python3
"""Tests for triage_agreement.py — framework-vs-human triage agreement."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import triage_agreement as ta  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402


def _pairs(framework, human):
    return list(zip(framework, human))


def test_task_surface_is_validation():
    assert ta.TASK_SURFACE == "validation"
    assert ta.TASK_SURFACE in VALID_TASK_SURFACES


def test_perfect_agreement_kappa_one():
    pairs = _pairs(["a", "b"] * 10, ["a", "b"] * 10)
    res = ta.analyze(pairs, n_dropped=0, n_boot=0, seed=0)
    assert res["percent_agreement"] == 1.0
    assert res["cohens_kappa"] == 1.0


def test_chance_agreement_kappa_near_zero():
    # framework all 'a'; human balanced => p_e high, kappa ~ 0
    framework = ["a"] * 20
    human = (["a", "b"] * 10)
    res = ta.analyze(_pairs(framework, human), n_dropped=0, n_boot=0, seed=0)
    assert abs(res["cohens_kappa"]) < 0.2


def test_kappa_paradox_pabak_reported():
    # 18 agree on 'a', 1 agree on 'b', 1 disagree => high agreement, skewed prevalence
    framework = ["a"] * 18 + ["b", "a"]
    human = ["a"] * 18 + ["b", "b"]
    res = ta.analyze(_pairs(framework, human), n_dropped=0, n_boot=0, seed=0)
    assert res["percent_agreement"] >= 0.9
    # PABAK (driven by raw agreement) exceeds the prevalence-deflated kappa.
    assert res["pabak"] > res["cohens_kappa"]


def test_confusion_and_marginals():
    pairs = _pairs(["a", "a", "b", "b"], ["a", "b", "b", "b"])
    res = ta.analyze(pairs, n_dropped=0, n_boot=0, seed=0)
    assert res["confusion"]["a"]["a"] == 1
    assert res["confusion"]["a"]["b"] == 1
    assert res["confusion"]["b"]["b"] == 2
    assert res["marginals"]["framework"]["a"] == 2
    assert res["marginals"]["human"]["b"] == 3


def test_bootstrap_ci_brackets_kappa():
    pairs = _pairs(["a", "b"] * 15, ["a", "b", "b"] * 10)
    res = ta.analyze(pairs, n_dropped=0, n_boot=500, seed=7)
    ci = res["kappa_ci95"]
    assert ci is not None and ci[0] <= res["cohens_kappa"] <= ci[1]
    # deterministic
    res2 = ta.analyze(pairs, n_dropped=0, n_boot=500, seed=7)
    assert res2["kappa_ci95"] == ci


def test_multicategory():
    pairs = _pairs(["a", "b", "c"] * 5, ["a", "c", "c"] * 5)
    res = ta.analyze(pairs, n_dropped=0, n_boot=0, seed=0)
    assert set(res["categories"]) == {"a", "b", "c"}


def test_dropped_rows_counted(tmp_path):
    f = tmp_path / "labels.jsonl"
    rows = [{"framework": "a", "human": "a"}] * 12 + [{"framework": "a"}]
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    pairs, dropped = ta.load_pairs(f, framework_key="framework",
                                   human_key="human", fmt="jsonl")
    assert dropped == 1
    assert len(pairs) == 12


def test_csv_input(tmp_path):
    f = tmp_path / "labels.csv"
    f.write_text("framework,human\n" + "\n".join(["a,a"] * 11), encoding="utf-8")
    pairs, dropped = ta.load_pairs(f, framework_key="framework",
                                   human_key="human", fmt="csv")
    assert len(pairs) == 11 and dropped == 0


def test_too_few_items_unavailable(tmp_path):
    f = tmp_path / "few.jsonl"
    f.write_text("\n".join(json.dumps({"framework": "a", "human": "a"})
                           for _ in range(5)), encoding="utf-8")
    assert ta.main([str(f), "--json", "--out", str(tmp_path / "o.json")]) == 0
    payload = json.loads((tmp_path / "o.json").read_text())
    assert payload["available"] is False


def test_claim_license_refuses_ground_truth():
    dn = ta._claim_license().does_not_license.lower()
    assert "correct" in dn or "ground truth" in dn


def test_deterministic():
    pairs = _pairs(["a", "b"] * 12, ["a", "a", "b"] * 8)
    a = ta.analyze(pairs, n_dropped=0, n_boot=300, seed=3)
    b = ta.analyze(pairs, n_dropped=0, n_boot=300, seed=3)
    assert a == b
