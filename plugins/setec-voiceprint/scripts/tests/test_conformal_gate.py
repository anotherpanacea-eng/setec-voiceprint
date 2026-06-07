#!/usr/bin/env python3
"""Tests for conformal_gate.py — split-conformal abstention gate."""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import conformal_gate as cg  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402


def test_task_surface_is_validation():
    assert cg.TASK_SURFACE == "validation"
    assert cg.TASK_SURFACE in VALID_TASK_SURFACES


def test_pvalue_formula():
    # cal=[1..5], score=3, higher_is_nonconforming: #{cal>=3}=3 => (1+3)/6
    p = cg.conformal_p([1, 2, 3, 4, 5], 3, direction="higher_is_nonconforming")
    assert abs(p - 4 / 6) < 1e-9


def test_coverage_guarantee_empirical():
    # Over many seeded draws where target is exchangeable with calibration,
    # the empirical rejection rate at alpha must be <= alpha (+ sampling slack).
    rng = random.Random(123)
    alpha = 0.2
    n = 50
    trials = 3000
    rejects = 0
    for _ in range(trials):
        cal = [rng.gauss(0, 1) for _ in range(n)]
        target = rng.gauss(0, 1)
        p = cg.conformal_p(cal, target, direction="two_sided")
        if p <= alpha:
            rejects += 1
    rate = rejects / trials
    assert rate <= alpha + 0.03


def test_in_reference_when_typical():
    cal = list(range(1, 101))
    res = cg.gate_one_class(cal, 50, alpha=0.1,
                            direction="higher_is_nonconforming",
                            reference_label="reference")
    assert res["p_value"] > 0.1
    assert res["in_reference_set"] is True
    assert res["prediction_set"] == ["reference"]


def test_out_of_reference_when_extreme():
    cal = list(range(1, 101))
    res = cg.gate_one_class(cal, 1000, alpha=0.1,
                            direction="higher_is_nonconforming",
                            reference_label="reference")
    assert res["p_value"] <= 0.1
    assert res["in_reference_set"] is False
    assert res["prediction_set"] == []


def test_direction_lower():
    cal = list(range(1, 11))
    higher = cg.gate_one_class(cal, 0, alpha=0.1,
                               direction="higher_is_nonconforming",
                               reference_label="reference")
    lower = cg.gate_one_class(cal, 0, alpha=0.1,
                              direction="lower_is_nonconforming",
                              reference_label="reference")
    assert higher["in_reference_set"] is True   # 0 below all => conforming when higher=nc
    assert lower["in_reference_set"] is False    # 0 is extreme-low => nonconforming


def test_two_class_both_and_empty():
    ref = list(range(1, 11))
    pos = list(range(5, 15))
    both = cg.gate_two_class(ref, pos, 0, alpha=0.1,
                             direction="higher_is_nonconforming",
                             reference_label="reference", positive_label="positive")
    assert set(both["prediction_set"]) == {"reference", "positive"}
    empty = cg.gate_two_class(ref, pos, 10_000, alpha=0.1,
                              direction="higher_is_nonconforming",
                              reference_label="reference", positive_label="positive")
    assert empty["prediction_set"] == []


def test_alpha_monotonicity():
    cal = list(range(1, 101))
    small = cg.gate_one_class(cal, 90, alpha=0.05,
                              direction="higher_is_nonconforming",
                              reference_label="reference")
    big = cg.gate_one_class(cal, 90, alpha=0.5,
                            direction="higher_is_nonconforming",
                            reference_label="reference")
    assert len(big["prediction_set"]) <= len(small["prediction_set"])


def test_claim_license_refuses_ai_verdict():
    dn = cg._claim_license().does_not_license.lower()
    assert "ai" in dn and "verdict" in dn and "exchangeab" in dn


def test_empty_calibration_unavailable(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("\n", encoding="utf-8")
    out = tmp_path / "o.json"
    assert cg.main(["--calibration", str(f), "--score", "1.0", "--json",
                    "--out", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert payload["available"] is False


def test_malformed_json_list_clean_exit(tmp_path):
    # A JSON list with a non-numeric entry must yield a clean exit 2, not a traceback.
    f = tmp_path / "bad.txt"
    f.write_text('[1, 2, "x"]', encoding="utf-8")
    assert cg.main(["--calibration", str(f), "--score", "1.0", "--json"]) == 2


def test_malformed_line_clean_exit(tmp_path):
    f = tmp_path / "bad.txt"
    f.write_text("1.0\nnot_a_number\n3.0", encoding="utf-8")
    assert cg.main(["--calibration", str(f), "--score", "1.0", "--json"]) == 2


def test_json_list_and_newline_parse_equal(tmp_path):
    j = tmp_path / "a.json"
    j.write_text("[1, 2, 3, 4, 5]", encoding="utf-8")
    n = tmp_path / "b.txt"
    n.write_text("1\n2\n3\n4\n5\n", encoding="utf-8")
    assert cg.load_scores(j) == cg.load_scores(n) == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_deterministic():
    cal = [1.0, 2.0, 3.0, 4.0, 5.0]
    a = cg.gate_one_class(cal, 2.5, alpha=0.1,
                          direction="higher_is_nonconforming",
                          reference_label="reference")
    b = cg.gate_one_class(cal, 2.5, alpha=0.1,
                          direction="higher_is_nonconforming",
                          reference_label="reference")
    assert a == b


def _exit_code(args):
    """Run main, normalizing an argparse SystemExit to its exit code.

    Some invalid values (e.g. '-inf') are rejected by argparse before main's
    own validation runs; both paths must reject with code 2.
    """
    try:
        return cg.main(args)
    except SystemExit as exc:
        return exc.code


def test_invalid_alpha_rejected(tmp_path):
    f = tmp_path / "cal.txt"
    f.write_text("\n".join(str(x) for x in range(1, 51)), encoding="utf-8")
    for bad in ("2.0", "0", "1", "-0.1", "nan", "inf"):
        assert _exit_code(["--calibration", str(f), "--score", "25",
                           "--alpha", bad, "--json"]) == 2


def test_non_finite_score_rejected(tmp_path):
    f = tmp_path / "cal.txt"
    f.write_text("\n".join(str(x) for x in range(1, 51)), encoding="utf-8")
    for bad in ("nan", "inf", "-inf"):
        assert _exit_code(["--calibration", str(f), "--score", bad, "--json"]) == 2


def test_cli_one_class_envelope(tmp_path):
    f = tmp_path / "cal.txt"
    f.write_text("\n".join(str(x) for x in range(1, 51)), encoding="utf-8")
    out = tmp_path / "o.json"
    assert cg.main(["--calibration", str(f), "--score", "25", "--json",
                    "--out", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert payload["task_surface"] == "validation"
    assert payload["results"]["mode"] == "one_class"
