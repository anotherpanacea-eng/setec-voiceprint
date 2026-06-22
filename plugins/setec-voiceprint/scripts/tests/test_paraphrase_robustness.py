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


def test_unregistered_detector_fails_loud():
    # The silent-inversion guard must be TOTAL, not limited to the 8 pinned
    # names. The spec lists `lrr` as an optional M2 detector column (§6,
    # 'machine lower'); it is deliberately NOT in DETECTOR_DIRECTION yet. An
    # operator scoring it (or any future detector) before pinning its sign
    # must hit a loud failure, never a silently un-oriented (inverted) AUC.
    assert "lrr" not in pr.DETECTOR_DIRECTION

    # _orient itself raises (the lowest-level chokepoint).
    with pytest.raises(ValueError, match="silent-inversion guard"):
        pr._orient("lrr", [0.1, 0.2])

    # ...so every scoring path that routes through it raises too.
    with pytest.raises(ValueError, match="silent-inversion guard"):
        pr.oriented_auc("lrr", [0.1, 0.2, 0.3], [0.8, 0.9, 1.0])
    with pytest.raises(ValueError, match="silent-inversion guard"):
        pr.tpr_at_fpr_budgets("lrr", [0.1, 0.2], [0.8, 0.9])

    # The orchestration boundary names ALL unregistered detectors up front.
    with pytest.raises(ValueError, match="no registered sign"):
        pr.run_report(
            paraphraser=_RecordingParaphraser(),
            scorer=_ConstantScorer(),
            detectors=["lrr"],
            machine_texts=["m one", "m two"],
            human_texts=["h a", "h b", "h c"],
            rungs=1,
        )

    # And the injected-scores entry point inherits the guard.
    bad_payload = {
        "paraphraser_label": "proxy_stdlib",
        "detectors": ["lrr"],
        "n_rungs": 1,
        "machine_texts": ["m one"],
        "human_texts": ["h a", "h b"],
        "scores": {
            "lrr": {
                "machine": [[0.1], [0.2]],
                # human is the fixed reference class: identical across rungs (so this
                # fixture exercises the SIGN guard, not the new fixed-reference guard).
                "human": [[0.8, 0.9], [0.8, 0.9]],
            }
        },
    }
    with pytest.raises(ValueError, match="no registered sign"):
        pr.run_from_injected_scores(bad_payload)


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


def test_injected_report_names_the_declared_paraphraser_not_the_stdlib_proxy():
    # Codex #261 round-2: the injected path hardcoded the stdlib proxy's label, so every injected
    # curve was reported as "proxy_stdlib" regardless of the real attack (e.g. DIPPER). The report
    # must name the payload-declared paraphraser, both top-level and per-rung.
    payload = _injected_payload_two_rungs()
    payload["paraphraser_label"] = "dipper_xxl"
    results = pr.run_from_injected_scores(payload)
    assert results["paraphraser_label"] == "dipper_xxl"
    assert results["per_rung"] and all(r["paraphraser_label"] == "dipper_xxl" for r in results["per_rung"])
    # An injected payload with no declared paraphraser is refused — injected curves may not travel
    # unbound from the attack that generated them.
    for bad_label in (None, "", "   "):
        p = _injected_payload_two_rungs()
        if bad_label is None:
            p.pop("paraphraser_label", None)
        else:
            p["paraphraser_label"] = bad_label
        with pytest.raises(ValueError, match="paraphraser_label"):
            pr.run_from_injected_scores(p)


def test_injected_path_emits_no_proxy_corruption_warnings():
    # Codex #261 round-3: the report is labeled the REAL attack, but run_report used to paraphrase
    # each window with the stdlib PROXY and emit its "collapsed" corruption warnings — bound to
    # unrelated proxy text yet attached to a report labeled e.g. "dipper_xxl". The injected path
    # applies NO proxy (the attack ran externally; the scorer ignores text), so it must emit no
    # paraphrase/corruption warning.
    payload = _injected_payload_two_rungs()
    n = len(payload["machine_texts"])
    # whitespace-heavy windows the stdlib proxy WOULD collapse (its space-jitter halves them) —
    # pre-fix this produced "paraphrase collapsed" warnings on the injected path.
    payload["machine_texts"] = ["word" + " " * 60 + "word"] * n
    payload["paraphraser_label"] = "dipper_xxl"
    results = pr.run_from_injected_scores(payload)
    assert results["paraphraser_label"] == "dipper_xxl"
    assert not any("collapsed" in w or "paraphrase" in w for w in results.get("_warnings", []))


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


# --------------------------- rung-count guard --------------------------- #


def test_injected_n_rungs_zero_refuses_cleanly_not_indexerror():
    """n_rungs:0 must be refused with a clear ValueError, NOT an uncaught
    IndexError deep in scoring.

    Before the fix: n_rungs:0 satisfied the per-side count check
    (expected_lists == 1), but the rung-1 attack loop (the max(1, rungs)
    clamp) then looked up a 2nd injected list and _InjectedScorer.score did
    ``det[which][1]`` on a length-1 list, raising
    ``IndexError: list index out of range`` — the exact failure the shape
    validator promised to prevent. There is no valid n_rungs:0 payload: the
    validator demanded 1 list/side while the clamped runtime demanded 2.
    """
    payload = {
        "paraphraser_label": "proxy_stdlib",
        "detectors": ["yules_k"],
        "n_rungs": 0,
        "machine_texts": ["m a", "m b"],
        "human_texts": ["h a", "h b"],
        "scores": {
            # Exactly one list per side — the count the validator demands for
            # n_rungs:0 (expected_lists == 1). This is the pre-fix repro that
            # slipped the guard and then crashed with IndexError on rung 1.
            "yules_k": {"machine": [[1.0, 2.0]], "human": [[0.0, 0.0]]},
        },
    }
    with pytest.raises(ValueError, match=r"n_rungs must be >= 1"):
        pr.run_from_injected_scores(payload)


def test_run_report_rungs_zero_no_phantom_rung():
    """run_report(rungs=0) with a REAL scorer must refuse, not silently run a
    phantom 'rung 1' and mislabel the result as n_rungs:1.

    Before the fix the loop ``range(1, max(1, int(rungs)) + 1)`` and the
    ``"n_rungs": max(1, int(rungs))`` label both clamped 0 up to 1, fabricating
    an unrequested rung and an inverted label. Now zero rungs is rejected at
    the orchestration boundary (covers the M2/real-scorer path, not just the
    injected entry point).
    """
    with pytest.raises(ValueError, match=r"n_rungs must be >= 1"):
        pr.run_report(
            paraphraser=_RecordingParaphraser(),
            scorer=_ConstantScorer(),
            detectors=["yules_k"],
            machine_texts=["m one", "m two"],
            human_texts=["h a", "h b", "h c"],
            rungs=0,
        )


def test_negative_n_rungs_refused_with_clear_message():
    """Negative n_rungs was already rejected (expected_lists clamped to 0), but
    now it fails up front with the same clear rung-count message rather than a
    downstream shape error — single, well-named refusal for the whole class."""
    payload = {
        "paraphraser_label": "proxy_stdlib",
        "detectors": ["yules_k"],
        "n_rungs": -1,
        "machine_texts": ["m a", "m b"],
        "human_texts": ["h a", "h b"],
        "scores": {"yules_k": {"machine": [], "human": []}},
    }
    with pytest.raises(ValueError, match=r"n_rungs must be >= 1"):
        pr.run_from_injected_scores(payload)


def test_n_rungs_one_still_runs_and_labels_correctly():
    """Guardrail: the smallest valid input (n_rungs:1) must still run end to
    end and report n_rungs:1 (no off-by-one from dropping the clamp)."""
    payload = {
        "paraphraser_label": "proxy_stdlib",
        "detectors": ["yules_k"],
        "n_rungs": 1,
        "machine_texts": ["m one window", "m two window"],
        "human_texts": ["h a", "h b", "h c"],
        "scores": {
            "yules_k": {
                "machine": [[1.0, 2.0], [1.5, 2.5]],
                "human": [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            },
        },
    }
    results = pr.run_from_injected_scores(payload)
    assert results["n_rungs"] == 1
    assert len(results["per_rung"]) == 1
    assert results["per_rung"][0]["rung"] == 1


# ------------------ score-count vs corpus-size guard ------------------ #


def test_injected_score_count_must_match_machine_corpus_size():
    """The Codex round-10 P1 repro: a payload may declare a large machine
    corpus but ship a single score per machine rung. Before the fix the rung
    LIST count check passed (n_rungs + 1 lists) and the report copied
    ``n_machine_windows = len(machine_texts)`` straight from the corpus — so it
    advertised n_machine_windows:100 while AUC/TPR were computed from ONE
    observation. The reported window count and the windows the metrics consumed
    must never diverge: refuse on a count/corpus-size mismatch.
    """
    payload = {
        "paraphraser_label": "proxy_stdlib",
        "detectors": ["yules_k"],
        "n_rungs": 1,
        # 100 declared machine windows...
        "machine_texts": [f"m window {i}" for i in range(100)],
        "human_texts": ["h a", "h b", "h c"],
        "scores": {
            "yules_k": {
                # ...but one machine score per rung. Correct LIST count
                # (n_rungs + 1 == 2), wrong INNER length (1, not 100).
                "machine": [[1.0], [1.5]],
                "human": [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
            },
        },
    }
    with pytest.raises(ValueError, match=r"one per machine corpus window"):
        pr.run_from_injected_scores(payload)


def test_injected_score_count_must_match_human_corpus_size():
    """Sibling of the machine-side guard: a human rung list shorter than
    len(human_texts) must also be refused (n_human_windows would over-report).
    """
    payload = {
        "paraphraser_label": "proxy_stdlib",
        "detectors": ["yules_k"],
        "n_rungs": 1,
        "machine_texts": ["m one", "m two"],
        "human_texts": ["h a", "h b", "h c", "h d"],
        "scores": {
            "yules_k": {
                "machine": [[1.0, 2.0], [1.5, 2.5]],
                # 4 declared human windows, 1 score per rung.
                "human": [[0.0], [0.0]],
            },
        },
    }
    with pytest.raises(ValueError, match=r"one per human corpus window"):
        pr.run_from_injected_scores(payload)


def test_injected_human_scores_must_be_fixed_across_rungs():
    """Codex P1: the human windows are the FIXED reference class — never paraphrased —
    so their injected scores must be IDENTICAL across every rung. A human list that
    drifts rung-to-rung would let a reported AUC/TPR degradation come from moving the
    supposedly fixed reference class instead of from the paraphrase attack on the
    machine side. The machine side MAY change per rung (it is attacked); the human side
    may not. Refuse a drifting human reference loudly."""
    payload = {
        "paraphraser_label": "proxy_stdlib",
        "detectors": ["yules_k"],
        "n_rungs": 1,
        "machine_texts": ["m one", "m two"],
        "human_texts": ["h a", "h b"],
        "scores": {
            "yules_k": {
                "machine": [[1.0, 2.0], [1.5, 2.5]],   # machine MAY change per rung (attacked)
                "human": [[0.3, 0.4], [0.3, 0.5]],     # human MUST NOT — rung 1 differs from rung 0
            },
        },
    }
    with pytest.raises(ValueError, match="fixed reference class"):
        pr.run_from_injected_scores(payload)
    # the same payload with a STABLE human reference passes the guard (it then proceeds
    # into scoring); flip rung 1 back to match rung 0 and the fixed-reference error is gone.
    payload["scores"]["yules_k"]["human"] = [[0.3, 0.4], [0.3, 0.4]]
    pr.run_from_injected_scores(payload)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_injected_non_finite_score_refused(bad):
    """Non-finite score entries (NaN / +Inf / -Inf) must be refused before the
    AUC/TPR math silently propagates them into the ranking."""
    payload = {
        "paraphraser_label": "proxy_stdlib",
        "detectors": ["yules_k"],
        "n_rungs": 1,
        "machine_texts": ["m one", "m two"],
        "human_texts": ["h a", "h b"],
        "scores": {
            "yules_k": {
                "machine": [[1.0, bad], [1.5, 2.5]],
                "human": [[0.0, 0.0], [0.0, 0.0]],
            },
        },
    }
    with pytest.raises(ValueError, match=r"must be finite"):
        pr.run_from_injected_scores(payload)


@pytest.mark.parametrize("bad", ["1.0", None, True])
def test_injected_non_numeric_score_refused(bad):
    """Non-numeric entries — including a stray bool, which is an int subclass
    and would otherwise masquerade as 1.0/0.0 — must be refused as scores."""
    payload = {
        "paraphraser_label": "proxy_stdlib",
        "detectors": ["yules_k"],
        "n_rungs": 1,
        "machine_texts": ["m one", "m two"],
        "human_texts": ["h a", "h b"],
        "scores": {
            "yules_k": {
                "machine": [[1.0, bad], [1.5, 2.5]],
                "human": [[0.0, 0.0], [0.0, 0.0]],
            },
        },
    }
    with pytest.raises(ValueError, match=r"must be a finite number"):
        pr.run_from_injected_scores(payload)


def test_injected_matching_score_counts_run_and_report_true_window_counts():
    """Guardrail: a payload whose inner score lists match the corpus sizes
    runs end to end and reports n_machine_windows / n_human_windows equal to
    the number of scores the metrics actually consumed."""
    payload = {
        "paraphraser_label": "proxy_stdlib",
        "detectors": ["yules_k"],
        "n_rungs": 1,
        "machine_texts": ["m one", "m two", "m three"],
        "human_texts": ["h a", "h b"],
        "scores": {
            "yules_k": {
                "machine": [[1.0, 2.0, 3.0], [1.5, 2.5, 3.5]],
                "human": [[0.0, 0.0], [0.0, 0.0]],
            },
        },
    }
    results = pr.run_from_injected_scores(payload)
    assert results["n_machine_windows"] == 3
    assert results["n_human_windows"] == 2
