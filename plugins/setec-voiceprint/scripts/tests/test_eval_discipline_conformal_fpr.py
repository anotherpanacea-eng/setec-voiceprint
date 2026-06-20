#!/usr/bin/env python3
"""Tests for the conformal FPR-bound mode (spec 28, PR B).

Stdlib only — no model. Root: Multiscaled Conformal Prediction
(arXiv:2505.05084). Asserts the bound holds on the calibration set, is
monotonic, names the reference-class FPR ceiling (not P(AI)), and that the
default one-class/two-class behavior is preserved when --fpr-bound is omitted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import conformal_gate as cg  # type: ignore  # noqa: E402


CAL = [float(x) for x in range(1, 101)]


def _exit_code(args):
    try:
        return cg.main(args)
    except SystemExit as exc:
        return exc.code


# ---- Acceptance #5: conformal FPR bound -------------------------------------


def test_empirical_fpr_within_bound():
    for q in (0.05, 0.1, 0.2, 0.3, 0.5):
        r = cg.threshold_at_fpr_bound(
            CAL, fpr_bound=q, direction="higher_is_nonconforming")
        assert r["available"] is True
        assert r["empirical_reference_fpr_at_threshold"] <= q + 1e-12


def test_ties_do_not_violate_fpr_bound():
    # Codex #242: with the `>= threshold` rule, calibration scores TIED at the threshold are all
    # flagged. An all-tied tail (ten identical scores, q=0.1) used to flag the whole block -> empirical
    # reference FPR 1.0 while claiming <= q. The tie-safe threshold must keep the empirical FPR <= q.
    for direction in cg.FPR_BOUND_DIRECTIONS:
        r = cg.threshold_at_fpr_bound([0.0] * 10, fpr_bound=0.1, direction=direction)
        assert r["available"] is True
        assert r["empirical_reference_fpr_at_threshold"] <= 0.1 + 1e-12, r
        assert r["empirical_reference_fpr_at_threshold"] == 0.0   # all-tied -> flag none (conservative)
    # a partial tie at the boundary is pushed above the tied block, never over the bound
    cal = [1.0] * 5 + [9.0] * 5   # q=0.1 would want to flag 1, but the top is a 5-way tie
    r = cg.threshold_at_fpr_bound(cal, fpr_bound=0.1, direction="higher_is_nonconforming")
    assert r["empirical_reference_fpr_at_threshold"] <= 0.1 + 1e-12, r


def test_threshold_monotonic_in_bound():
    """A larger fpr_bound never raises the threshold (so never lowers TPR)."""
    thresholds = [
        cg.threshold_at_fpr_bound(
            CAL, fpr_bound=q, direction="higher_is_nonconforming")["threshold"]
        for q in (0.05, 0.1, 0.2, 0.3)
    ]
    assert thresholds == sorted(thresholds, reverse=True)


def test_lower_direction_supported():
    r = cg.threshold_at_fpr_bound(
        CAL, fpr_bound=0.1, direction="lower_is_nonconforming")
    assert r["available"] is True
    assert r["empirical_reference_fpr_at_threshold"] <= 0.1 + 1e-12


def test_two_sided_rejected():
    r = cg.threshold_at_fpr_bound(CAL, fpr_bound=0.1, direction="two_sided")
    assert r["available"] is False
    assert "one-tailed" in r["reason"]


def test_no_score_returns_threshold_only():
    r = cg.gate_fpr_bound(
        CAL, None, fpr_bound=0.1, direction="higher_is_nonconforming",
        reference_label="reference")
    assert "threshold" in r
    assert "in_reference_set" not in r


def test_with_score_classifies_target():
    out = cg.gate_fpr_bound(
        CAL, 95.0, fpr_bound=0.1, direction="higher_is_nonconforming",
        reference_label="reference")
    assert out["in_reference_set"] is False  # 95 nc >= threshold
    assert out["prediction_set"] == []
    inside = cg.gate_fpr_bound(
        CAL, 50.0, fpr_bound=0.1, direction="higher_is_nonconforming",
        reference_label="reference")
    assert inside["in_reference_set"] is True
    assert inside["prediction_set"] == ["reference"]


def test_claim_license_names_fpr_ceiling_not_p_ai():
    dn = cg._claim_license().does_not_license.lower()
    assert "false-positive ceiling" in dn
    assert "not p(ai)" in dn
    lic = cg._claim_license().licenses.lower()
    assert "false-positive rate is bounded" in lic


# ---- CLI contract: --score conditionally required ---------------------------


def _write_cal(tmp_path):
    f = tmp_path / "cal.txt"
    f.write_text("\n".join(str(x) for x in range(1, 101)), encoding="utf-8")
    return f


def test_cli_fpr_bound_emits_mode(tmp_path):
    f = _write_cal(tmp_path)
    out = tmp_path / "o.json"
    rc = cg.main(["--calibration", str(f), "--fpr-bound", "0.1", "--json",
                  "--out", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text())
    assert payload["results"]["mode"] == "fpr_bound"
    assert payload["results"]["fpr_bound"] == 0.1
    assert "threshold" in payload["results"]


def test_cli_score_required_without_fpr_bound(tmp_path):
    """The default gate still errors if --score is omitted (preserved)."""
    f = _write_cal(tmp_path)
    assert _exit_code(["--calibration", str(f), "--json"]) == 2


def test_cli_score_optional_with_fpr_bound(tmp_path):
    f = _write_cal(tmp_path)
    assert cg.main(["--calibration", str(f), "--fpr-bound", "0.1", "--json"]) == 0


def test_cli_fpr_bound_two_sided_rejected(tmp_path):
    f = _write_cal(tmp_path)
    assert _exit_code([
        "--calibration", str(f), "--fpr-bound", "0.1",
        "--direction", "two_sided", "--json"]) == 2


def test_cli_invalid_fpr_bound_rejected(tmp_path):
    f = _write_cal(tmp_path)
    for bad in ("0", "1", "2.0", "-0.1", "nan"):
        assert _exit_code([
            "--calibration", str(f), "--fpr-bound", bad, "--json"]) == 2


# ---- Default path preserved (byte-for-byte) ---------------------------------


def test_default_one_class_unchanged_without_fpr_bound(tmp_path):
    f = _write_cal(tmp_path)
    out = tmp_path / "o.json"
    rc = cg.main(["--calibration", str(f), "--score", "25", "--json",
                  "--out", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text())
    assert payload["results"]["mode"] == "one_class"
