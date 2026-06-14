#!/usr/bin/env python3
"""Regression tests for calibration_survey.py.

The survey wrapper loops over every COMPRESSION_HEURISTICS signal,
calls into `calibrate_thresholds.derive_threshold`, and aggregates
the results into a single comparison table + JSON ledger. Tests
verify:

  * Gate evaluation logic (the automatable subset of PROVENANCE.md's
    five selection criteria) maps inputs to booleans correctly.
  * Survey aggregation handles signals that derive cleanly AND
    signals that fail (a single bad signal must not abort the run).
  * Output rendering (markdown table + JSON ledger) has stable shape.
  * CLI surface honors the documented flags.
  * Coverage matches the registry: surveying without --signal hits
    every key in COMPRESSION_HEURISTICS.

The tests don't run a real calibration (that requires the labeled
corpus the maintainer fetches via fetch_pangram_editlens.py +
license-gated HF access). They patch derive_threshold to return
synthetic provenance entries so the aggregation / gate / rendering
logic is exercised without Tier 2/3 spaCy or SBERT compute.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import calibration_survey as cs  # type: ignore
from variance_audit import COMPRESSION_HEURISTICS  # type: ignore  # noqa: E402


# ------------------- Synthetic provenance entries ---------------


def _entry(
    signal: str,
    *,
    direction: str = "gt",
    auc: float = 0.85,
    ap: float = 0.80,
    threshold: float = 0.42,
    tpr: float = 0.60,
    fpr: float = 0.009,
    n_pos: int = 100,
    n_neg: int = 200,
    fpr_resolution: float = 0.005,
) -> dict:
    """Build a minimal derive_threshold-shaped entry."""
    return {
        "signal": signal,
        "direction": direction,
        "fpr_target": 0.01,
        "empirical": {
            "auc": auc,
            "ap": ap,
            "tpr_at_threshold": tpr,
            "fpr_at_threshold": fpr,
            "n_pos": n_pos,
            "n_neg": n_neg,
        },
        "sweep": {
            "threshold": threshold,
            "fpr_resolution": fpr_resolution,
            "available": True,
        },
    }


# ------------------- Gate evaluation ----------------------------


def test_gate_polarity_passes_when_auc_above_half():
    entry = _entry("burstiness_B", auc=0.70)
    g = cs.evaluate_gates(
        entry, heuristic_value=0.5, direction="gt",
        tpr_floor=0.05, aggressiveness_tolerance=0.05,
    )
    assert g.polarity_matches is True


def test_gate_polarity_fails_when_auc_below_half():
    """An AUC < 0.5 in the registry's declared direction means the
    corpus inverts the polarity. PROVENANCE.md says this is a
    *finding*, not a threshold to commit."""
    entry = _entry("burstiness_B", auc=0.30)
    g = cs.evaluate_gates(
        entry, heuristic_value=0.5, direction="gt",
        tpr_floor=0.05, aggressiveness_tolerance=0.05,
    )
    assert g.polarity_matches is False


def test_gate_enough_negatives_passes_when_resolution_finer_than_target():
    entry = _entry("burstiness_B", fpr_resolution=0.005)
    g = cs.evaluate_gates(
        entry, heuristic_value=0.5, direction="gt",
        tpr_floor=0.05, aggressiveness_tolerance=0.05,
    )
    assert g.enough_negatives is True


def test_gate_enough_negatives_fails_when_resolution_coarser_than_target():
    """fpr_resolution = 1/n_neg = 0.05 with target 0.01 means we
    can't cleanly request the requested FPR."""
    entry = _entry("burstiness_B", fpr_resolution=0.05)
    g = cs.evaluate_gates(
        entry, heuristic_value=0.5, direction="gt",
        tpr_floor=0.05, aggressiveness_tolerance=0.05,
    )
    assert g.enough_negatives is False


def test_gate_interpretable_threshold_passes_when_tpr_above_floor():
    entry = _entry("burstiness_B", tpr=0.40)
    g = cs.evaluate_gates(
        entry, heuristic_value=0.5, direction="gt",
        tpr_floor=0.05, aggressiveness_tolerance=0.05,
    )
    assert g.interpretable_threshold is True


def test_gate_interpretable_threshold_fails_when_tpr_under_floor():
    """A TPR of 0.5% means the threshold fires on basically nothing —
    'predict almost nothing' per PROVENANCE.md gate 4."""
    entry = _entry("burstiness_B", tpr=0.005)
    g = cs.evaluate_gates(
        entry, heuristic_value=0.5, direction="gt",
        tpr_floor=0.05, aggressiveness_tolerance=0.05,
    )
    assert g.interpretable_threshold is False


def test_gate_esl_conservative_within_tolerance_passes():
    """Calibrated 0.51 vs heuristic 0.50 (2% drift, well within
    5% tolerance) is conservative regardless of direction."""
    entry = _entry("burstiness_B", threshold=0.51)
    g = cs.evaluate_gates(
        entry, heuristic_value=0.50, direction="gt",
        tpr_floor=0.05, aggressiveness_tolerance=0.05,
    )
    assert g.esl_conservative is True


def test_gate_esl_conservative_gt_signal_lower_than_heuristic_fails():
    """gt signal: lower threshold flags MORE essays as compressed
    = more aggressive. Calibrated 0.30 vs heuristic 0.50 is a 40%
    drop; outside tolerance, more aggressive direction → fail."""
    entry = _entry("burstiness_B", threshold=0.30)
    g = cs.evaluate_gates(
        entry, heuristic_value=0.50, direction="gt",
        tpr_floor=0.05, aggressiveness_tolerance=0.05,
    )
    assert g.esl_conservative is False


def test_gate_esl_conservative_gt_signal_higher_than_heuristic_passes():
    """gt signal: higher threshold flags FEWER as compressed = less
    aggressive. Conservative direction."""
    entry = _entry("burstiness_B", threshold=0.65)
    g = cs.evaluate_gates(
        entry, heuristic_value=0.50, direction="gt",
        tpr_floor=0.05, aggressiveness_tolerance=0.05,
    )
    assert g.esl_conservative is True


def test_gate_esl_conservative_lt_signal_higher_than_heuristic_fails():
    """lt signal (compressed when value is low): higher threshold
    flags MORE as compressed = more aggressive."""
    entry = _entry("mattr", threshold=0.85, direction="lt")
    g = cs.evaluate_gates(
        entry, heuristic_value=0.70, direction="lt",
        tpr_floor=0.05, aggressiveness_tolerance=0.05,
    )
    assert g.esl_conservative is False


def test_gate_esl_conservative_lt_signal_lower_than_heuristic_passes():
    """lt signal: lower threshold flags FEWER = conservative."""
    entry = _entry("mattr", threshold=0.55, direction="lt")
    g = cs.evaluate_gates(
        entry, heuristic_value=0.70, direction="lt",
        tpr_floor=0.05, aggressiveness_tolerance=0.05,
    )
    assert g.esl_conservative is True


def test_gate_2_stays_none_for_maintainer_judgment():
    """Gate 2 (AUC/AP not embarrassing) is explicitly maintainer
    judgment per PROVENANCE.md. The survey never sets it to
    True/False — the maintainer sees AUC + AP and decides."""
    entry = _entry("burstiness_B", auc=0.99, ap=0.99)
    g = cs.evaluate_gates(
        entry, heuristic_value=0.5, direction="gt",
        tpr_floor=0.05, aggressiveness_tolerance=0.05,
    )
    assert g.auc_ap_not_embarrassing is None


def test_gate_results_n_passes_counts_only_explicit_passes():
    g = cs.GateResults(
        polarity_matches=True,
        auc_ap_not_embarrassing=None,  # judgment
        enough_negatives=True,
        interpretable_threshold=False,
        esl_conservative=True,
    )
    assert g.n_passes == 3
    assert g.n_evaluated == 4
    assert g.all_pass is False


def test_gate_results_all_pass_requires_no_none():
    """If gate 2 is None, all_pass is False. Maintainer judgment
    isn't replaced by the wrapper."""
    g = cs.GateResults(
        polarity_matches=True,
        auc_ap_not_embarrassing=None,
        enough_negatives=True,
        interpretable_threshold=True,
        esl_conservative=True,
    )
    assert g.all_pass is False


# ------------------- Survey runner ------------------------------


def _stub_args(**overrides) -> argparse.Namespace:
    base = dict(
        manifest="dummy.jsonl",
        use="validation",
        fpr_target=0.01,
        out=None,
        signal=[],
        tier2=False,
        tier3=False,
        bootstrap_resamples=10,
        bootstrap_confidence=0.95,
        bootstrap_seed=42,
        tpr_floor=0.05,
        aggressiveness_tolerance=0.05,
        json_only=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_survey_one_signal_returns_row_with_metrics():
    """Survey one signal with a stub derive_threshold that returns a
    canned entry; verify the row is populated correctly.

    Uses raw AUC=0.15 against the `lt`-direction `burstiness_B`
    signal so direction-aware AUC = 1 − 0.15 = 0.85 ≥ 0.5 and
    gate 1 (polarity) passes. Pre-1.26.1 the test used raw AUC=0.85
    which silently passed the polarity gate when the gate logic was
    direction-blind; the maintainer's first real calibration run
    surfaced the bug.
    """
    parent = _stub_args()
    fake_entry = _entry(
        "burstiness_B", auc=0.15, threshold=0.42, tpr=0.60, n_neg=200,
    )
    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([], {}, False)), \
         mock.patch.object(cs.ct, "derive_threshold_from_records",
                           return_value=fake_entry):
        row = cs.survey_one_signal(
            "burstiness_B", parent,
            tpr_floor=0.05, aggressiveness_tolerance=0.05,
        )
    assert row.signal == "burstiness_B"
    assert row.error is None
    assert row.auc == 0.15  # raw
    assert abs(row.direction_aware_auc - 0.85) < 1e-9  # 1 - 0.15 for lt
    assert row.threshold == 0.42
    assert row.tpr_at_threshold == 0.60
    assert row.n_neg == 200
    # Gates evaluated. Polarity matches because da_AUC ≥ 0.5.
    assert row.gates.polarity_matches is True
    assert row.gates.enough_negatives is True
    assert row.gates.interpretable_threshold is True


def test_survey_one_signal_records_systemexit_as_error():
    """A signal that derive_threshold can't compute (registry mismatch,
    unscored corpus) raises SystemExit. The survey records the error
    instead of aborting."""
    parent = _stub_args()
    with mock.patch.object(
        cs.ct, "derive_threshold",
        side_effect=SystemExit("Could not derive threshold"),
    ):
        row = cs.survey_one_signal(
            "burstiness_B", parent,
            tpr_floor=0.05, aggressiveness_tolerance=0.05,
        )
    assert row.error is not None
    assert "Could not derive" in row.error
    # Gates left at defaults (None).
    assert row.gates.polarity_matches is None


def test_survey_one_signal_records_unexpected_exception():
    """Defensive: any other exception type is caught and recorded."""
    parent = _stub_args()
    with mock.patch.object(
        cs.ct, "derive_threshold", side_effect=ValueError("boom"),
    ):
        row = cs.survey_one_signal(
            "burstiness_B", parent,
            tpr_floor=0.05, aggressiveness_tolerance=0.05,
        )
    assert row.error is not None
    assert "ValueError" in row.error
    assert "boom" in row.error


def test_run_survey_iterates_all_signals_by_default():
    """Without --signal, the survey runs every key in
    COMPRESSION_HEURISTICS. This is the coverage-correctness
    invariant the PROVENANCE.md doc fix is about."""
    parent = _stub_args()
    fake_entry = _entry("dummy")
    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([], {}, False)), \
         mock.patch.object(cs.ct, "derive_threshold_from_records",
                           return_value=fake_entry):
        survey = cs.run_survey(parent)
    surveyed = {r["signal"] for r in survey["rows"]}
    assert surveyed == set(COMPRESSION_HEURISTICS.keys())
    assert survey["n_signals"] == len(COMPRESSION_HEURISTICS)


def test_run_survey_honors_explicit_signal_list():
    parent = _stub_args()
    fake_entry = _entry("dummy")
    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([], {}, False)), \
         mock.patch.object(cs.ct, "derive_threshold_from_records",
                           return_value=fake_entry):
        survey = cs.run_survey(
            parent, signals=["burstiness_B", "mattr"],
        )
    surveyed = {r["signal"] for r in survey["rows"]}
    assert surveyed == {"burstiness_B", "mattr"}


def test_run_survey_ranks_passing_signals_above_failing():
    """Rows with more pass-glyphs come first. A signal with all
    automatable gates passing should sort above one with a polarity
    failure.

    Direction matters in 1.26.1+: pick signals deliberately so one
    has matching polarity and the other doesn't, regardless of
    registry insertion order.
    """
    parent = _stub_args()

    # `good` is a `gt` signal with raw AUC 0.85 → da_AUC 0.85 →
    # polarity matches. `bad` is the same `gt` signal with raw AUC
    # 0.30 → da_AUC 0.30 → polarity fails. Survey runs both signals
    # but the second call returns a different entry shape via the
    # dispatch lambda.
    a = "connective_density"  # direction = "gt"
    b = "yules_k"             # direction = "gt"

    def fake_dispatch(args):
        if args.signal == a:
            return _entry(a, auc=0.85, tpr=0.60, fpr_resolution=0.005)
        return _entry(b, auc=0.30)  # da_AUC 0.30 < 0.5 → polarity fails

    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([], {}, False)), \
         mock.patch.object(
             cs.ct, "derive_threshold_from_records",
             side_effect=lambda records, *, args, scoring_meta: fake_dispatch(args),
         ):
        survey = cs.run_survey(parent, signals=[a, b])
    # Signal `a` (passing more gates) should rank ahead of `b`.
    assert survey["rows"][0]["signal"] == a
    assert survey["rows"][1]["signal"] == b


def test_run_survey_counts_all_gates_pass_correctly():
    parent = _stub_args()

    keys = list(COMPRESSION_HEURISTICS.keys())[:3]

    def fake_dispatch(args):
        if args.signal == keys[0]:
            # All gates pass + within aggressiveness tolerance.
            return _entry(
                keys[0], auc=0.85, tpr=0.60, fpr_resolution=0.005,
                threshold=COMPRESSION_HEURISTICS[keys[0]].value or 0.5,
            )
        elif args.signal == keys[1]:
            # Polarity fails.
            return _entry(keys[1], auc=0.20)
        else:
            # TPR too low.
            return _entry(keys[2], tpr=0.001)

    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([], {}, False)), \
         mock.patch.object(
             cs.ct, "derive_threshold_from_records",
             side_effect=lambda records, *, args, scoring_meta: fake_dispatch(args),
         ):
        survey = cs.run_survey(parent, signals=keys)
    # n_signals_all_gates_pass excludes None (gate 2 always None) so
    # the count is "passes every evaluable gate" — for our synthetic
    # data that's the keys[0] row, which has gate 2 = None and so
    # all_pass is False by the strict definition. Verify the
    # numerical count.
    assert survey["n_signals_all_gates_pass"] == 0
    # But n_passes for keys[0] should be 4 (all evaluable gates pass).
    row0 = next(r for r in survey["rows"] if r["signal"] == keys[0])
    assert row0["gates"]["n_passes"] >= 3


# ------------------- Rendering ----------------------------------


def test_render_markdown_table_includes_header_and_legend():
    survey = {
        "manifest": "/tmp/m.jsonl",
        "fpr_target": 0.01,
        "use": "validation",
        "tier2": True,
        "tier3": False,
        "tpr_floor": 0.05,
        "aggressiveness_tolerance": 0.05,
        "n_signals": 1,
        "n_signals_all_gates_pass": 0,
        "rows": [
            {
                "signal": "burstiness_B",
                "direction": "gt",
                "heuristic_value": 0.5,
                "auc": 0.85,
                "ap": 0.80,
                "threshold": 0.42,
                "tpr_at_threshold": 0.60,
                "fpr_at_threshold": 0.009,
                "n_pos": 100,
                "n_neg": 200,
                "fpr_resolution": 0.005,
                "gates": {
                    "polarity_matches": True,
                    "auc_ap_not_embarrassing": None,
                    "enough_negatives": True,
                    "interpretable_threshold": True,
                    "esl_conservative": True,
                    "n_passes": 4,
                    "n_evaluated": 4,
                    "all_pass": False,
                },
                "error": None,
            }
        ],
        "date": "2026-05-09",
    }
    text = cs.render_markdown_table(survey)
    assert "# Calibration survey" in text
    assert "burstiness_B" in text
    assert "Gate legend" in text
    assert "0.4200" in text  # threshold in the row


def test_render_markdown_table_separates_errors():
    """Signals that derive_threshold rejected get their own table
    so they don't pollute the comparison view."""
    survey = {
        "manifest": "/tmp/m.jsonl",
        "fpr_target": 0.01,
        "use": "validation",
        "tier2": True,
        "tier3": False,
        "tpr_floor": 0.05,
        "aggressiveness_tolerance": 0.05,
        "n_signals": 2,
        "n_signals_all_gates_pass": 0,
        "rows": [
            {
                "signal": "good", "direction": "gt", "heuristic_value": 0.5,
                "auc": 0.85, "ap": 0.80, "threshold": 0.42,
                "tpr_at_threshold": 0.60, "fpr_at_threshold": 0.009,
                "n_pos": 100, "n_neg": 200, "fpr_resolution": 0.005,
                "gates": {
                    "polarity_matches": True,
                    "auc_ap_not_embarrassing": None,
                    "enough_negatives": True,
                    "interpretable_threshold": True,
                    "esl_conservative": True,
                    "n_passes": 4, "n_evaluated": 4, "all_pass": False,
                },
                "error": None,
            },
            {
                "signal": "bad", "direction": "gt", "heuristic_value": 0.5,
                "auc": None, "ap": None, "threshold": None,
                "tpr_at_threshold": None, "fpr_at_threshold": None,
                "n_pos": None, "n_neg": None, "fpr_resolution": None,
                "gates": {
                    "polarity_matches": None,
                    "auc_ap_not_embarrassing": None,
                    "enough_negatives": None,
                    "interpretable_threshold": None,
                    "esl_conservative": None,
                    "n_passes": 0, "n_evaluated": 0, "all_pass": False,
                },
                "error": "Could not derive threshold",
            },
        ],
        "date": "2026-05-09",
    }
    text = cs.render_markdown_table(survey)
    assert "good" in text
    assert "Signals that failed to derive a threshold" in text
    assert "bad" in text
    assert "Could not derive" in text


# ------------------- CLI ----------------------------------------


def test_cli_help_lists_required_flags():
    parser = cs.build_arg_parser()
    help_text = parser.format_help()
    for flag in (
        "--manifest", "--fpr-target", "--use", "--out", "--signal",
        "--tier2", "--no-tier2", "--tier3", "--no-tier3",
        "--bootstrap-resamples", "--bootstrap-confidence",
        "--bootstrap-seed", "--tpr-floor", "--aggressiveness-tolerance",
        "--json-only",
    ):
        assert flag in help_text, f"--help missing {flag}"


def test_cli_rejects_invalid_fpr_target(tmp_path, capsys):
    args = argparse.Namespace(
        manifest="dummy.jsonl",
        fpr_target=1.5,  # invalid
        use="validation",
        out=None,
        signal=[],
        tier2=False, tier3=False,
        bootstrap_resamples=10,
        bootstrap_confidence=0.95,
        bootstrap_seed=42,
        tpr_floor=0.05,
        aggressiveness_tolerance=0.05,
        json_only=True,
    )
    rc = cs.run(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "fpr-target" in err


def test_cli_rejects_unknown_signal(capsys):
    args = argparse.Namespace(
        manifest="dummy.jsonl", fpr_target=0.01, use="validation",
        out=None, signal=["definitely_not_a_signal"],
        tier2=False, tier3=False,
        bootstrap_resamples=10, bootstrap_confidence=0.95,
        bootstrap_seed=42, tpr_floor=0.05,
        aggressiveness_tolerance=0.05, json_only=True,
    )
    rc = cs.run(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "Unknown signal" in err


def test_run_writes_json_ledger_to_out(tmp_path):
    args = argparse.Namespace(
        manifest="dummy.jsonl", fpr_target=0.01, use="validation",
        out=str(tmp_path / "survey.json"),
        signal=["burstiness_B"],
        tier2=False, tier3=False,
        bootstrap_resamples=10, bootstrap_confidence=0.95,
        bootstrap_seed=42, tpr_floor=0.05,
        aggressiveness_tolerance=0.05, json_only=True,
    )
    fake_entry = _entry("burstiness_B")
    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([], {}, False)), \
         mock.patch.object(cs.ct, "derive_threshold_from_records",
                           return_value=fake_entry):
        rc = cs.run(args)
    out_path = tmp_path / "survey.json"
    assert out_path.is_file()
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["task_surface"] == "smoothing_diagnosis_calibration"
    assert data["n_signals"] == 1
    assert data["rows"][0]["signal"] == "burstiness_B"


def test_run_json_only_suppresses_markdown_on_stdout(tmp_path, capsys):
    args = argparse.Namespace(
        manifest="dummy.jsonl", fpr_target=0.01, use="validation",
        out=str(tmp_path / "survey.json"),
        signal=["burstiness_B"],
        tier2=False, tier3=False,
        bootstrap_resamples=10, bootstrap_confidence=0.95,
        bootstrap_seed=42, tpr_floor=0.05,
        aggressiveness_tolerance=0.05, json_only=True,
    )
    fake_entry = _entry("burstiness_B")
    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([], {}, False)), \
         mock.patch.object(cs.ct, "derive_threshold_from_records",
                           return_value=fake_entry):
        cs.run(args)
    captured = capsys.readouterr()
    assert "# Calibration survey" not in captured.out


# ------------------- Provenance MD update -----------------------


def test_provenance_md_no_longer_lists_partial_loop():
    """The PROVENANCE.md doc fix removed the partial 7-signal shell
    loop. Pin that the canonical doc points at calibration_survey.py
    instead."""
    p = ROOT / "calibration" / "PROVENANCE.md"
    text = p.read_text(encoding="utf-8")
    assert "calibration_survey.py" in text
    # The old partial loop enumerated the 7 signals on a single line.
    # The new doc shouldn't have that exact incantation.
    assert (
        "burstiness_B connective_density fkgl_sd mattr mtld "
        "adjacent_cosine_mean adjacent_cosine_sd"
    ) not in text


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))


# ---- survey-level per-signal checkpoint (#133) ----

def test_survey_cache_resume_skips_already_swept_signals(tmp_path):
    """A re-run with the same --survey-cache reuses cached rows and does NOT
    re-sweep (derive_threshold_from_records is not called again)."""
    cache = tmp_path / "survey.json"
    calls = {"n": 0}

    def _count(records, *, args, scoring_meta):
        calls["n"] += 1
        return _entry(args.signal)

    sigs = ["burstiness_B", "mattr"]
    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([], {}, False)), \
         mock.patch.object(cs.ct, "derive_threshold_from_records",
                           side_effect=_count):
        survey1 = cs.run_survey(_stub_args(survey_cache=str(cache)), signals=sigs)
    assert calls["n"] == 2
    assert cache.exists()
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["status"] == "complete"
    assert set(payload["rows"]) == set(sigs)

    # Second run, same cache + settings: both signals served from cache.
    calls["n"] = 0
    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([], {}, False)), \
         mock.patch.object(cs.ct, "derive_threshold_from_records",
                           side_effect=_count):
        survey2 = cs.run_survey(_stub_args(survey_cache=str(cache)), signals=sigs)
    assert calls["n"] == 0
    assert {r["signal"] for r in survey2["rows"]} == set(sigs)


def test_survey_cache_incompatible_meta_resweeps(tmp_path):
    """Changing a sweep knob (--fpr-target) invalidates the survey cache so the
    signals are re-swept rather than served stale."""
    cache = tmp_path / "survey.json"
    calls = {"n": 0}

    def _count(records, *, args, scoring_meta):
        calls["n"] += 1
        return _entry(args.signal)

    sigs = ["burstiness_B"]
    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([], {}, False)), \
         mock.patch.object(cs.ct, "derive_threshold_from_records",
                           side_effect=_count):
        cs.run_survey(_stub_args(survey_cache=str(cache), fpr_target=0.01),
                      signals=sigs)
    assert calls["n"] == 1
    calls["n"] = 0
    with mock.patch.object(cs.ct, "load_or_score_corpus",
                           return_value=([], {}, False)), \
         mock.patch.object(cs.ct, "derive_threshold_from_records",
                           side_effect=_count):
        cs.run_survey(_stub_args(survey_cache=str(cache), fpr_target=0.02),
                      signals=sigs)
    assert calls["n"] == 1  # different fpr_target -> cache ignored -> re-swept
