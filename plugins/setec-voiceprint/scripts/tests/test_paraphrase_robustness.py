#!/usr/bin/env python3
"""Tests for paraphrase_robustness.py (spec 33, M1) — model-free, CI-runnable.

Covers every M1 test the spec names plus the REVIEW guards: the WMW-U AUC
computation, Δ-AUC, FPR/TPR operating points, the human-never-paraphrased
invariant, the banned-aggregate-scalar walk, the rung-0 scoring-divergence
gate, the proxy-changes-text check, the sign/direction pin (the silent-
inversion guard), the empty/tie edge case, and the corruption guard.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import sys

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
CALIB_DIR = SCRIPTS_DIR / "calibration"
for p in (str(SCRIPTS_DIR), str(CALIB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import paraphrase_robustness as pr  # type: ignore  # noqa: E402


# ----------------------------- helpers ----------------------------- #


class _RecordingParaphraser:
    """Records every text it is asked to paraphrase (to assert human windows
    are never passed). Returns the text marked, so it differs but does not
    collapse length."""

    label = "recording_stub"

    def __init__(self) -> None:
        self.seen: list[str] = []

    def paraphrase(self, text: str, *, rung: int) -> str:
        self.seen.append(text)
        return text + " (p)"


class _ConstantScorer:
    """Returns a fixed score per text regardless of detector, by length —
    deterministic and model-free. Used where the exact score is not the
    point (e.g. the human-never-paraphrased call-count test)."""

    def score(self, detector: str, texts: list[str]) -> list[float]:
        return [float(len(t)) for t in texts]


# --------------------------- AUC / math --------------------------- #


def test_auc_computation():
    # 'higher' detector: machine label-1 strictly above human -> AUC 1.0.
    machine = [10.0, 11.0, 12.0]
    human = [1.0, 2.0, 3.0, 4.0, 5.0]
    auc = pr.oriented_auc("fast_detect_curvature", machine, human)
    assert auc == pytest.approx(1.0)
    # Strictly below -> AUC 0.0 (a 'higher' detector reading the wrong way).
    auc_lo = pr.oriented_auc("fast_detect_curvature", [0.0, 0.0], [9.0, 9.0, 9.0])
    assert auc_lo == pytest.approx(0.0)


def test_auc_orientation_lower_detector():
    # binoculars_v2 is 'lower' (machine has the LOWER ratio). Machine scores
    # numerically BELOW human must orient to AUC 1.0 (discriminative), not 0.0.
    machine = [0.1, 0.2, 0.3]
    human = [0.8, 0.9, 1.0]
    auc = pr.oriented_auc("binoculars_v2", machine, human)
    assert auc == pytest.approx(1.0)


def test_delta_auc():
    payload = _injected_payload_two_rungs()
    results = pr.run_from_injected_scores(payload)
    base = results["rung_0"]["binoculars_v2"]
    r1 = results["per_rung"][0]["per_detector"]["binoculars_v2"]
    assert r1["delta_auc"] == pytest.approx(r1["auc"] - base)


def test_fpr_tpr_at_operating_points():
    # Machine cleanly separable above human at a threshold with 0 false pos.
    machine = [5.0, 6.0, 7.0, 8.0]
    human = [0.0, 1.0, 2.0, 3.0]
    tpr = pr.tpr_at_fpr_budgets("fast_detect_curvature", machine, human)
    assert tpr["tpr_at_fpr05"] == pytest.approx(1.0)
    assert tpr["tpr_at_fpr10"] == pytest.approx(1.0)


def test_empty_and_tie_inputs():
    # Empty / single-class -> None (never a spurious 0.5).
    assert pr.oriented_auc("yules_k", [], [1.0, 2.0]) is None
    assert pr.oriented_auc("yules_k", [1.0], []) is None
    # All-tied across classes -> WMW-U = 0.5 exactly (a defined tie, not None);
    # the contract is: no crash, ties handled by average ranks.
    tied = pr.oriented_auc("yules_k", [3.0, 3.0], [3.0, 3.0])
    assert tied == pytest.approx(0.5)
    tpr = pr.tpr_at_fpr_budgets("yules_k", [], [1.0])
    assert tpr["tpr_at_fpr05"] is None


# ----------------------- posture / structure ----------------------- #


def test_human_windows_not_paraphrased():
    para = _RecordingParaphraser()
    scorer = _ConstantScorer()
    pr.run_report(
        paraphraser=para,
        scorer=scorer,
        detectors=["yules_k"],
        machine_texts=["machine one", "machine two"],
        human_texts=["human alpha", "human beta", "human gamma"],
        rungs=2,
    )
    # The paraphraser must never have been handed a human window.
    for seen in para.seen:
        assert "human" not in seen, f"human window paraphrased: {seen!r}"
    assert para.seen, "paraphraser was never called on machine windows"


def test_no_aggregate_score():
    payload = _injected_payload_two_rungs()
    results = pr.run_from_injected_scores(payload)
    envelope = pr.build_envelope(results)
    parsed = json.loads(json.dumps(envelope, default=str))

    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert k not in pr.BANNED_AGGREGATE_KEYS, (
                    f"banned aggregate key present: {k}"
                )
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(parsed)
    # The legitimate per-cell descriptive keys ARE present (not banned).
    cell = parsed["results"]["per_rung"][0]["per_detector"]["binoculars_v2"]
    assert "auc" in cell and "delta_auc" in cell and "tpr_at_fpr05" in cell


def test_rung0_auc_consistency():
    # The scoring-divergence gate: the runner's rung-0 AUC must equal the AUC
    # computed directly from the same injected rung-0 scores.
    payload = _injected_payload_two_rungs()
    results = pr.run_from_injected_scores(payload)
    for det in payload["detectors"]:
        direct = pr.oriented_auc(
            det,
            payload["scores"][det]["machine"][0],
            payload["scores"][det]["human"][0],
        )
        assert results["rung_0"][det] == pytest.approx(direct), (
            f"rung-0 divergence for {det}"
        )


def test_proxy_attack_changes_text():
    para = pr.StdlibProxyParaphraser()
    original = "The very big house is good and new"
    attacked = para.paraphrase(original, rung=1)
    assert attacked != original
    # And it is deterministic.
    assert para.paraphrase(original, rung=1) == attacked


def test_sign_direction_pinned():
    # The silent-inversion guard: pin every detector's machine-vs-human sign.
    expected = {
        "binoculars_v2": "lower",
        "fast_detect_curvature": "higher",
        "surprisal_mean": "lower",
        "surprisal_sd": "lower",
        "surprisal_acf_lag1": "higher",
        "yules_k": "higher",
        "burstiness_B": "lower",
        "mtld": "lower",
    }
    assert pr.DETECTOR_DIRECTION == expected
    # And the orientation actually negates a 'lower' detector and not a
    # 'higher' one.
    assert pr._orient("binoculars_v2", [1.0, 2.0]) == [-1.0, -2.0]
    assert pr._orient("yules_k", [1.0, 2.0]) == [1.0, 2.0]


def test_corruption_guard_skips_short_paraphrase():
    class _Collapser:
        label = "collapser"

        def paraphrase(self, text: str, *, rung: int) -> str:
            return "x"  # collapses every window

    scorer = _ConstantScorer()
    results = pr.run_report(
        paraphraser=_Collapser(),
        scorer=scorer,
        detectors=["yules_k"],
        machine_texts=["a reasonably long machine window here"],
        human_texts=["a reasonably long human window here", "another human one"],
        rungs=1,
    )
    assert any("collapsed" in w for w in results["_warnings"]), (
        "corruption guard did not warn on a collapsed paraphrase"
    )


# --------------------------- envelope ----------------------------- #


def test_envelope_is_valid_and_validation_surface():
    payload = _injected_payload_two_rungs()
    results = pr.run_from_injected_scores(payload)
    envelope = pr.build_envelope(results)
    assert envelope["task_surface"] == "validation"
    assert envelope["tool"] == "paraphrase_robustness"
    assert envelope["available"] is True
    assert envelope["claim_license"]["task_surface"] == "validation"
    # The license refuses the robustness claim.
    dnl = envelope["claim_license"]["does_not_license"].lower()
    assert "robust to paraphrase" in dnl


# --------------------------- fixtures ----------------------------- #


def _injected_payload_two_rungs() -> dict:
    """Two REAL detectors, two rungs. ``binoculars_v2`` is a registered
    'lower' detector (machine numerically below human) so the fixture
    exercises orientation; ``fast_detect_curvature`` is a registered
    'higher' detector. Using registered names means the fixture never
    mutates the module-global DETECTOR_DIRECTION (no test-order pollution)."""
    return {
        "paraphraser_label": "proxy_stdlib",
        "detectors": ["binoculars_v2", "fast_detect_curvature"],
        "n_rungs": 2,
        "machine_texts": ["m one window", "m two window", "m three window"],
        "human_texts": ["h a", "h b", "h c", "h d"],
        "scores": {
            "binoculars_v2": {
                # rung0: machine well below human -> AUC 1.0 after orient.
                "machine": [[0.1, 0.2, 0.3], [0.5, 0.6, 0.7], [0.9, 1.0, 1.1]],
                "human": [[0.8, 0.9, 1.0, 1.1]] * 3,
            },
            "fast_detect_curvature": {
                "machine": [[9.0, 9.5, 10.0], [5.0, 5.5, 6.0], [1.0, 1.5, 2.0]],
                "human": [[1.0, 2.0, 3.0, 4.0]] * 3,
            },
        },
    }
