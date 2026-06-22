#!/usr/bin/env python3
"""Tests for the Voight-Kampff benchmark harness.

Covers AC-1..AC-20 from the spec ``voight-kampff-benchmark-harness``:
adapter (manifest validity, label mapping, JSONL+CSV, NOTICE/no-vendor),
runner (binoculars via injected score_fn, stdlib stand-in, skip-on-error,
orientation), scorer (TIRA-parity metrics + bootstrap CI), the
anti-Goodhart invariants (no writes, no fitter import, one-way labels,
report block), and the report/posture/docs surface.

All model-free: binoculars runs via its injected ``score_fn`` hook;
nothing here loads a model.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
_CALIB = _SCRIPTS / "calibration"
for _p in (_SCRIPTS, _CALIB):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pan_metrics as pm  # noqa: E402
import pan_voight_kampff_to_manifest as adapter  # noqa: E402
import pan_voight_kampff_benchmark as bench  # noqa: E402
from manifest_validator import validate_manifest  # noqa: E402
from validation_harness import fallback_roc_auc  # noqa: E402

FIXTURE = _SCRIPTS / "test_data" / "pan_voight_kampff_fixture"


# ============================================================
# Helpers
# ============================================================


def _build_manifest(tmp_path: Path, *, pan_dir: Path | None = None) -> Path:
    pan_dir = pan_dir or FIXTURE
    manifest = tmp_path / "manifest.jsonl"
    text_dir = tmp_path / "text"
    rc = adapter.main([
        "--pan-dir", str(pan_dir),
        "--split", "validation",
        "--manifest", str(manifest),
        "--text-dir", str(text_dir),
    ])
    assert rc == 0
    return manifest


class StubBackend:
    def __init__(self, model_id: str):
        self.model_id = model_id
        self.revision = None

    def identifier_block(self):
        return {"id": self.model_id}


def _det_score_fn(backend, text):
    """Deterministic surprisal proxy (no model): scorer/observer series
    differ by a constant so the ratio is well-defined and model-free."""
    n = max(len(text), 1)
    base = sum(1 for c in text if c.isalpha()) / n
    bump = 0.5 if backend.model_id == "scorer" else 1.0
    return [base + bump] * 120


def _binoculars_kwargs(**over):
    kw = {
        "score_fn": _det_score_fn,
        "scorer_backend": StubBackend("scorer"),
        "observer_backend": StubBackend("observer"),
    }
    kw.update(over)
    return kw


def _run(manifest: Path, detectors: str, **over) -> dict:
    args = argparse.Namespace(
        manifest=str(manifest),
        detectors=detectors,
        split="validation",
        operating_point=over.pop("operating_point", False),
        n_resamples=over.pop("n_resamples", 200),
        confidence_level=0.95,
        seed=7,
        per_instance=over.pop("per_instance", None),
        _binoculars_kwargs=over.pop("_binoculars_kwargs", _binoculars_kwargs()),
        _standin_kwargs=over.pop("_standin_kwargs", {}),
    )
    return bench.run_benchmark(args)


# ============================================================
# Adapter (AC-1..AC-4)
# ============================================================


def test_ac1_adapter_manifest_validates_clean(tmp_path):
    manifest = _build_manifest(tmp_path)
    result = validate_manifest(manifest)
    assert result["n_errors"] == 0, result["issues"]


def test_ac2_label_mapping_and_fields(tmp_path):
    manifest = _build_manifest(tmp_path)
    entries = [json.loads(l) for l in manifest.read_text().splitlines() if l.strip()]
    by_id = {e["source_id"]: e for e in entries}
    # vk0001 is human (label 0) -> pre_ai_human; vk0002 machine -> ai_generated
    assert by_id["vk0001"]["ai_status"] == "pre_ai_human"
    assert by_id["vk0002"]["ai_status"] == "ai_generated"
    for e in entries:
        assert e["use"] == ["validation"]  # LIST, not scalar (P1)
        assert isinstance(e["use"], list)
        assert e["source"] == "pan25_voight_kampff"
        assert e["privacy"] == "shareable"


def test_ac3_jsonl_and_csv_both_ingested(tmp_path):
    manifest = _build_manifest(tmp_path)
    entries = [json.loads(l) for l in manifest.read_text().splitlines() if l.strip()]
    source_files = {e["notes"]["source_file"] for e in entries}
    # The fixture has a JSONL instance file AND an inline-label CSV; both
    # must contribute entries (the CSV exercises the BOM/utf-8-sig path).
    assert "instances.jsonl" in source_files
    assert "instances_inline.csv" in source_files
    # 8 JSONL + 4 CSV = 12.
    assert len(entries) == 12


def test_ac4_notice_written_and_no_vendored_pan_text(tmp_path):
    manifest = _build_manifest(tmp_path)
    notice = tmp_path / "text" / "NOTICE.md"
    assert notice.is_file()
    body = notice.read_text()
    assert "redistribut" in body.lower()
    assert "14962653" in body
    # The repo tree contains ONLY the synthetic fixture, never real PAN
    # data: assert the fixture is self-describing as synthetic.
    assert (FIXTURE / "README.md").is_file()
    assert "NOT real PAN data" in (FIXTURE / "README.md").read_text()


def test_adapter_csv_label_inverts_for_is_human_key():
    # _normalize_label flips sense for an is_human field.
    assert adapter._normalize_label(True, key="is_human") == 0
    assert adapter._normalize_label(False, key="is_human") == 1
    assert adapter._normalize_label(1, key="label") == 1
    assert adapter._normalize_label("human", key="label") == 0
    # one-hot [human, machine]
    assert adapter._normalize_label([1, 0]) == 0
    assert adapter._normalize_label([0, 1]) == 1
    assert adapter._normalize_label([1, 1]) is None  # not a clean one-hot


# ============================================================
# Runner (AC-5..AC-8)
# ============================================================


def test_ac5_binoculars_runs_via_injected_score_fn(tmp_path):
    manifest = _build_manifest(tmp_path)
    report = _run(manifest, "binoculars_audit")
    d = report["detectors"][0]
    assert d["detector"] == "binoculars_audit"
    assert d["task_surface"] == "binoculars_discrimination"
    assert d["score_name"] == "perplexity_ratio"
    assert d["n_scored"] == 12  # all instances scored, no model load


def test_ac6_standin_runs_with_zero_model_loads(tmp_path):
    manifest = _build_manifest(tmp_path)
    # No binoculars backend supplied at all -> pure stdlib path.
    report = _run(manifest, "length_ratio_standin", _binoculars_kwargs={})
    d = report["detectors"][0]
    assert d["detector"] == "length_ratio_standin"
    assert d["n_scored"] == 12


def test_ac7_error_envelope_instance_is_skipped(tmp_path):
    manifest = _build_manifest(tmp_path)
    # Backend unavailable (no scorer/observer wired) -> every instance
    # returns available=False and is skipped, not crashed.
    report = _run(manifest, "binoculars_audit", _binoculars_kwargs={})
    d = report["detectors"][0]
    assert d["n_scored"] == 0
    assert d["n_skipped"] == 12
    assert sum(d["skipped_reasons"].values()) == 12
    assert "binoculars_backend_unavailable" in d["skipped_reasons"]


def test_ac8_orientation_recorded_and_flips_with_polarity(tmp_path):
    manifest = _build_manifest(tmp_path)
    entries = bench.load_manifest_entries(str(manifest))

    # binoculars declares lower_is_ai; the runner records that orientation.
    reg = bench.build_detector_registry(
        ["binoculars_audit"], binoculars_kwargs=_binoculars_kwargs()
    )
    run = bench.run_detector_over_manifest(
        "binoculars_audit", reg["binoculars_audit"], entries
    )
    assert run["orientation_applied"] == "lower_is_ai"
    # A lower_is_ai detector sign-flips raw -> oriented.
    for row in run["rows"]:
        assert row["oriented_score"] == -row["raw_score"]

    # The stand-in declares higher_is_ai -> no flip.
    reg2 = bench.build_detector_registry(["length_ratio_standin"])
    run2 = bench.run_detector_over_manifest(
        "length_ratio_standin", reg2["length_ratio_standin"], entries
    )
    assert run2["orientation_applied"] == "higher_is_ai"
    for row in run2["rows"]:
        assert row["oriented_score"] == row["raw_score"]


def test_reversed_threshold_band_is_rejected():
    # Codex P1: a REVERSED band (threshold_low > threshold_high) silently collapsed the
    # [low, high] indeterminate zone to empty and inverted the human/ai classification —
    # corrupt benchmark results rather than a failure. Both scorer factories now reject it
    # at construction time (fail loud, not a runtime misclassification).
    with pytest.raises(ValueError, match="reversed band"):
        bench.make_length_ratio_standin_scorer(threshold_low=0.80, threshold_high=0.20)
    with pytest.raises(ValueError, match="reversed band"):
        bench.make_binoculars_scorer(threshold_low=0.80, threshold_high=0.20)
    # A correctly-ordered band, a degenerate low == high, and a one-sided band all
    # construct fine (the last stays the None-guarded "uncalibrated" case).
    bench.make_length_ratio_standin_scorer(threshold_low=0.20, threshold_high=0.80)
    bench.make_length_ratio_standin_scorer(threshold_low=0.50, threshold_high=0.50)
    bench.make_length_ratio_standin_scorer(threshold_low=None, threshold_high=0.80)


def test_nonfinite_threshold_band_is_rejected():
    # Codex #267 round-2: NaN/infinite thresholds bypassed the low>high ordering guard (every
    # comparison with NaN is False) and then corrupted every band decision. They must be rejected
    # for being non-finite — including a reversed band hidden behind a NaN bound.
    nan, inf = float("nan"), float("inf")
    for low, high in [(nan, 0.8), (0.2, nan), (inf, 0.8), (0.2, -inf), (nan, 0.2)]:
        with pytest.raises(ValueError, match="finite"):
            bench.make_length_ratio_standin_scorer(threshold_low=low, threshold_high=high)
        with pytest.raises(ValueError, match="finite"):
            bench.make_binoculars_scorer(threshold_low=low, threshold_high=high)
    # a finite band still constructs (one bound NaN-free on each side)
    bench.make_length_ratio_standin_scorer(threshold_low=0.20, threshold_high=0.80)


# ============================================================
# Scorer (AC-9..AC-11)
# ============================================================


def test_ac9_roc_auc_matches_validation_harness():
    labels = [1, 0, 1, 0, 1, 0]
    scores = [0.9, 0.1, 0.8, 0.2, 0.7, 0.3]
    assert pm.roc_auc(labels, scores) == fallback_roc_auc(labels, scores)


def test_ac10_metrics_match_hand_computed_values():
    # Hand-computed against the TIRA reference conventions.
    # labels (gold): 1,0,1,0 ; preds: 0.9,0.1,0.5(abstain),0.6
    labels = [1, 0, 1, 0]
    preds = [0.9, 0.1, 0.5, 0.6]
    # c@1: answered = idx 0,1,3. correct: 0 (0.9>0.5 & gold1 ok),
    #   1 (0.1<0.5 & gold0 ok), 3 (0.6>0.5 but gold0 -> wrong). nc=2.
    #   nu=1, n=4 -> (1/4)*(2 + 1*2/4) = (1/4)*2.5 = 0.625
    assert pm.c_at_1(labels, preds) == pytest.approx(0.625)
    # f1: tp = idx0 (pred>0.5,gold1)=1 ; fp = idx3 =1 ; fn = idx2 (0.5 not
    #   >0.5, gold1)=1. precision=1/2, recall=1/2 -> f1=0.5
    assert pm.f1(labels, preds) == pytest.approx(0.5)
    # f05u: n_tp=1, n_fn=0 (only pred<0.5 with gold1; idx2 is ==0.5 not
    #   <0.5), n_fp=1, n_u=1. denom=1.25*1 + 0.25*(0+1) + 1 = 2.5 ->
    #   1.25/2.5 = 0.5
    assert pm.f05u(labels, preds) == pytest.approx(0.5)
    # brier complement: clip preds; loss=mean((p-y)^2)
    #   = mean((0.9-1)^2,(0.1-0)^2,(0.5-1)^2,(0.6-0)^2)
    #   = mean(0.01,0.01,0.25,0.36)=0.1575 -> 1-0.1575=0.8425
    assert pm.brier(labels, preds) == pytest.approx(0.8425)
    # pan_mean over the five (roc_auc computed on these probs too)
    vals = {
        "roc_auc": pm.roc_auc(labels, preds),
        "brier": pm.brier(labels, preds),
        "c_at_1": pm.c_at_1(labels, preds),
        "f1": pm.f1(labels, preds),
        "f05u": pm.f05u(labels, preds),
    }
    assert pm.pan_mean(vals) == pytest.approx(
        sum(v or 0.0 for v in vals.values()) / 5
    )


def test_ac10_f1_none_on_no_positives():
    # No predicted positives and no gold positives -> PAN's zero_division
    # nan path -> None.
    assert pm.f1([0, 0], [0.2, 0.1]) is None
    # f05u denom 0 -> None
    assert pm.f05u([0, 0], [0.2, 0.1]) is None


def test_ac11_bootstrap_ci_present_and_skips_single_class():
    labels = [1, 0, 1, 0, 1, 0, 1, 0]
    scores = [0.9, 0.1, 0.8, 0.2, 0.7, 0.3, 0.6, 0.4]
    cell = pm.score_metric_with_ci(
        "roc_auc", labels, scores,
        n_resamples=200, confidence_level=0.95, seed=1,
    )
    assert cell["value"] is not None
    assert cell["ci_low"] is not None and cell["ci_high"] is not None
    import math
    assert math.isfinite(cell["ci_low"]) and math.isfinite(cell["ci_high"])

    # Degenerate one-class input: metric undefined -> value None, CI absent
    # with a reason (graceful, no crash).
    degenerate = pm.score_metric_with_ci(
        "roc_auc", [1, 1, 1], [0.5, 0.6, 0.7],
        n_resamples=200, confidence_level=0.95, seed=1,
    )
    assert degenerate["value"] is None
    assert degenerate["ci_low"] is None
    assert degenerate.get("ci_reason")


# ============================================================
# Anti-Goodhart (AC-12..AC-15) — load-bearing
# ============================================================


# Paths a tuning/calibration loop would write; the harness must touch NONE.
_FORBIDDEN_WRITE_TARGETS = (
    _CALIB / "thresholds_calibrated.json",
    _SCRIPTS.parent / "capabilities.d",
    _SCRIPTS / "claim_license_surfaces",
)


def _snapshot(paths):
    snap = {}
    for p in paths:
        if p.is_file():
            snap[p] = p.read_bytes()
        elif p.is_dir():
            snap[p] = sorted(q.name for q in p.iterdir())
        else:
            snap[p] = None
    return snap


def test_ac12_harness_writes_only_report_and_sidecars(tmp_path):
    manifest = _build_manifest(tmp_path)
    before = _snapshot(_FORBIDDEN_WRITE_TARGETS)
    # Full run (assemble + per-instance sink populated in-memory).
    report = _run(manifest, "binoculars_audit,length_ratio_standin",
                  per_instance="sink")
    assert report["report_kind"] == "voight_kampff_benchmark"
    after = _snapshot(_FORBIDDEN_WRITE_TARGETS)
    assert before == after, (
        "harness mutated a threshold/registry/claim-license artifact"
    )


def test_ac13_no_fitter_import_in_harness_modules():
    # Static check: none of the harness module sources import a
    # threshold-/calibration-FITTING symbol. Reading a calibrated
    # threshold is allowed; importing a fitter is not.
    forbidden = ("calibrate_thresholds", "train_edit_magnitude")
    for mod in (
        _CALIB / "pan_metrics.py",
        _CALIB / "pan_voight_kampff_to_manifest.py",
        _CALIB / "pan_voight_kampff_benchmark.py",
    ):
        src = mod.read_text()
        for sym in forbidden:
            assert f"import {sym}" not in src, f"{mod.name} imports {sym}"
            assert f"from {sym}" not in src, f"{mod.name} imports {sym}"


def test_ac14_labels_flow_one_way_report_is_terminal(tmp_path):
    # The report is a plain dict with no callback/return that re-enters
    # detector config. Assert there is no operating-point / selection /
    # calibration key produced FROM the metrics.
    manifest = _build_manifest(tmp_path)
    report = _run(manifest, "binoculars_audit")
    # The only operating_point block is descriptive and threshold=None
    # (never derived from labels).
    op = report["detectors"][0]["operating_point"]
    assert op["threshold"] is None
    assert op["source"] in ("none", "operator_supplied", "detector_calibrated")
    # No "best_detector" / "selected" / "fitted_threshold" key anywhere.
    blob = json.dumps(report)
    for banned in ("best_detector", "fitted_threshold", "selected_operating_point"):
        assert banned not in blob


def test_ac15_report_carries_anti_goodhart_block(tmp_path):
    manifest = _build_manifest(tmp_path)
    report = _run(manifest, "length_ratio_standin", _binoculars_kwargs={})
    ag = report["anti_goodhart"]
    assert ag["role"] == "external_held_out_validation"
    assert ag["is_tuning_target"] is False
    assert ag["is_calibration_target"] is False
    assert ag["is_selection_target"] is False
    assert "external validation only" in ag["statement"]


def test_probability_transform_is_label_free_and_declared(tmp_path):
    # D7: the Brier probability transform reads only scores, never labels.
    # Permuting labels must not change the probabilities the transform
    # produces (it never sees them).
    scores = [0.1, 0.9, 0.5, 0.3, 0.7]
    p1 = bench.oriented_score_to_probability(scores)
    p2 = bench.oriented_score_to_probability(list(scores))
    assert p1 == p2
    # Degenerate all-equal -> constant 0.5 (no fitting).
    assert bench.oriented_score_to_probability([0.4, 0.4, 0.4]) == [0.5, 0.5, 0.5]
    # The transform is recorded in the report.
    manifest = _build_manifest(tmp_path)
    report = _run(manifest, "length_ratio_standin", _binoculars_kwargs={})
    assert "min_max" in report["detectors"][0]["probability_transform"]


# ============================================================
# Report / posture (AC-16..AC-18)
# ============================================================


def test_ac16_report_shape(tmp_path):
    manifest = _build_manifest(tmp_path)
    report = _run(manifest, "binoculars_audit")
    assert report["report_schema_version"] == "1.0"
    assert report["report_kind"] == "voight_kampff_benchmark"
    assert report["dataset"]["zenodo_record"] == "14962653"
    assert isinstance(report["detectors"], list) and report["detectors"]
    baselines = {b["baseline"] for b in report["official_baselines"]}
    assert baselines == {"tf_idf_svm", "ppmd", "binoculars"}
    assert "anti_goodhart" in report
    assert report["harness_version"] == bench.HARNESS_VERSION
    assert "cmd" in report["reproduce"]


def test_ac17_thresholded_null_without_operating_point(tmp_path):
    manifest = _build_manifest(tmp_path)
    report = _run(manifest, "binoculars_audit")  # operating_point=False
    m = report["detectors"][0]["metrics"]
    for k in ("c_at_1", "f1", "f05u"):
        assert m[k]["value"] is None
        assert m[k]["reason"] == "no_operating_point_without_fitting_to_pan"
    # roc_auc is still reported.
    assert m["roc_auc"]["value"] is not None


def test_ac17_thresholded_present_with_operating_point(tmp_path):
    manifest = _build_manifest(tmp_path)
    # Give the stand-in a two-threshold band so its bands answer/abstain.
    report = _run(
        manifest, "length_ratio_standin",
        operating_point=True,
        _binoculars_kwargs={},
        _standin_kwargs={"threshold_low": 0.78, "threshold_high": 0.80},
    )
    d = report["detectors"][0]
    m = d["metrics"]
    # At least one thresholded metric is now non-null.
    assert any(m[k]["value"] is not None for k in ("c_at_1", "f1", "f05u"))
    # Operator-supplied thresholds reached the scorer -> honest provenance.
    assert d["operating_point"]["source"] == "operator_supplied"
    assert d["operating_point"]["in_force"] is True


# ============================================================
# Operating-point provenance honesty (findings 1 & 2) — driven
# through the REAL main([...]) CLI, not the injection attrs.
# ============================================================


def test_operating_point_flag_no_thresholds_is_not_operator_supplied(tmp_path):
    """--operating-point with NO reachable threshold (the bare flag, the
    only state the old CLI could reach) must NOT stamp 'operator_supplied'
    and must NOT fabricate 0.0 thresholded cells — it stays source 'none'
    with the thresholded cells null. Drives the real main() entrypoint."""
    manifest = _build_manifest(tmp_path)
    out = tmp_path / "report.json"
    rc = bench.main([
        "--manifest", str(manifest),
        "--detectors", "length_ratio_standin",
        "--operating-point",  # bare: no --threshold-low/--threshold-high
        "--out", str(out),
        "--n-resamples", "0",
    ])
    assert rc == 0
    report = json.loads(out.read_text())
    d = report["detectors"][0]
    op = d["operating_point"]
    # The core bug: provenance must be honest, never fabricated.
    assert op["source"] == "none"
    assert op["in_force"] is False
    # Thresholded cells stay null (not fabricated 0.0), with the distinct
    # "flag passed but no reachable threshold" reason.
    for k in ("c_at_1", "f1", "f05u"):
        assert d["metrics"][k]["value"] is None
        assert d["metrics"][k]["reason"] == (
            "operating_point_requested_but_no_reachable_threshold"
        )


def test_operating_point_cli_thresholds_are_operator_supplied(tmp_path):
    """--operating-point WITH --threshold-low/--threshold-high feeds the
    detector's band from the CLI and records source 'operator_supplied'
    with real thresholded cells — the flag now does what its help text
    promises. Drives the real main() entrypoint."""
    manifest = _build_manifest(tmp_path)
    out = tmp_path / "report.json"
    rc = bench.main([
        "--manifest", str(manifest),
        "--detectors", "length_ratio_standin",
        "--operating-point",
        "--threshold-low", "0.78",
        "--threshold-high", "0.80",
        "--out", str(out),
        "--n-resamples", "0",
    ])
    assert rc == 0
    report = json.loads(out.read_text())
    d = report["detectors"][0]
    op = d["operating_point"]
    assert op["source"] == "operator_supplied"
    assert op["in_force"] is True
    assert any(
        d["metrics"][k]["value"] is not None for k in ("c_at_1", "f1", "f05u")
    )


def test_default_run_pan_mean_is_partial_not_deflated(tmp_path):
    """On the DEFAULT (no operating point) path, pan_mean must NOT be a
    zero-deflated scalar that invites a wrong side-by-side against PAN's
    published five-metric means — it is null with a partial marker
    (finding 3). Drives the real main() entrypoint."""
    manifest = _build_manifest(tmp_path)
    out = tmp_path / "report.json"
    md = tmp_path / "report.md"
    rc = bench.main([
        "--manifest", str(manifest),
        "--detectors", "length_ratio_standin",
        "--out", str(out),
        "--markdown", str(md),
        "--n-resamples", "0",
    ])
    assert rc == 0
    report = json.loads(out.read_text())
    pm_cell = report["detectors"][0]["metrics"]["pan_mean"]
    assert pm_cell["value"] is None
    assert pm_cell["partial"] is True
    assert pm_cell["n_metrics_present"] == 2  # roc_auc + brier only
    assert pm_cell["reason"] == "partial_suite_no_operating_point"
    # The markdown render must not surface a concrete pan_mean number.
    md_text = md.read_text()
    assert "partial" in md_text


def test_pan_mean_cell_partial_and_full():
    """pan_metrics.pan_mean_cell: full suite -> real mean; any null
    constituent -> null value with partial marker (finding 3)."""
    full = {"roc_auc": 1.0, "brier": 0.9, "c_at_1": 0.8, "f1": 0.7, "f05u": 0.6}
    cell = pm.pan_mean_cell(full)
    assert cell["partial"] is False
    assert cell["n_metrics_present"] == 5
    assert cell["value"] == pytest.approx(pm.pan_mean(full))
    partial = {"roc_auc": 1.0, "brier": 0.9, "c_at_1": None, "f1": None, "f05u": None}
    pcell = pm.pan_mean_cell(partial)
    assert pcell["value"] is None
    assert pcell["partial"] is True
    assert pcell["n_metrics_present"] == 2


def test_ac18_baseline_sourcing(tmp_path):
    manifest = _build_manifest(tmp_path)
    report = _run(manifest, "binoculars_audit")
    by_name = {b["baseline"]: b for b in report["official_baselines"]}
    assert by_name["binoculars"]["source"] == "first_party"
    assert by_name["binoculars"]["maps_to_detector"] == "binoculars_audit"
    assert by_name["tf_idf_svm"]["source"] == "pan_published"
    assert by_name["ppmd"]["source"] == "pan_published"


def test_unknown_detector_rejected(tmp_path):
    manifest = _build_manifest(tmp_path)
    with pytest.raises(SystemExit):
        _run(manifest, "fast_detect_curvature")  # out-of-M1 (model dep)


def test_markdown_renders(tmp_path):
    manifest = _build_manifest(tmp_path)
    report = _run(manifest, "length_ratio_standin", _binoculars_kwargs={})
    md = bench.render_markdown(report)
    assert "Voight-Kampff Benchmark Report" in md
    assert "external validation only" in md
