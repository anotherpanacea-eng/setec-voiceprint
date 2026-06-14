"""Tests for argument_register_baselines — the ArgScope register-baseline loader
(calibration ladder C0). Focus: the honesty discipline (calibration spec §4),
the resolution order, and the shipped op-ed seed."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argument_register_baselines as arb  # noqa: E402


def _write(tmp_path, body: str) -> Path:
    p = tmp_path / arb.YAML_NAME
    p.write_text(body, encoding="utf-8")
    return p


# ---- the shipped seed ----------------------------------------------------
def test_shipped_op_ed_seed_is_literature_anchored():
    rb = arb.load_register("op-ed")
    assert rb is not None and rb.genre == "op-ed"
    assert rb.is_calibrated is False  # no discrimination row in the seed
    sp = rb.signals["support_to_proposal_rate"]
    assert sp.human_mean == 0.123 and sp.ai_mean is None
    assert sp.status == "literature_anchored" and sp.provenance
    # op-ed is the only seeded genre; an unseeded genre returns None
    assert arb.load_register("policy_brief") is None


# ---- resolution order ----------------------------------------------------
def test_baseline_dir_is_preferred(tmp_path):
    _write(tmp_path, (
        "argument_register_baselines:\n"
        "  op-ed:\n"
        "    argumentation_share:\n"
        "      human: { mean: 0.700 }\n"
        "      status: empirically_oriented\n"
        "      provenance: \"local · pre-2022 · hand\"\n"
    ))
    rb = arb.load_register("op-ed", baseline_dir=tmp_path)
    assert rb.source_path.startswith(str(tmp_path))
    assert rb.signals["argumentation_share"].status == "empirically_oriented"


# ---- honesty discipline (§4) --------------------------------------------
def test_above_heuristic_requires_provenance(tmp_path):
    p = _write(tmp_path, (
        "argument_register_baselines:\n"
        "  op-ed:\n"
        "    argumentation_share:\n"
        "      human: { mean: 0.7 }\n"
        "      status: empirically_oriented\n"  # no provenance
    ))
    with pytest.raises(arb.RegisterBaselineError, match="provenance"):
        arb.load_register("op-ed", yaml_path=p)


def test_calibrated_requires_discrimination(tmp_path):
    p = _write(tmp_path, (
        "argument_register_baselines:\n"
        "  op-ed:\n"
        "    argumentation_share:\n"
        "      human: { mean: 0.7 }\n"
        "      ai: { mean: 0.9 }\n"
        "      status: calibrated\n"
        "      provenance: \"labeled corpus\"\n"
    ))
    with pytest.raises(arb.RegisterBaselineError, match="discrimination"):
        arb.load_register("op-ed", yaml_path=p)


def test_unknown_status_rejected(tmp_path):
    p = _write(tmp_path, (
        "argument_register_baselines:\n"
        "  op-ed:\n"
        "    argumentation_share:\n"
        "      human: { mean: 0.7 }\n"
        "      status: register_calibrated\n"  # not a ladder value
        "      provenance: \"x\"\n"
    ))
    with pytest.raises(arb.RegisterBaselineError, match="calibration_status"):
        arb.load_register("op-ed", yaml_path=p)


def test_out_of_range_proportion_rejected(tmp_path):
    p = _write(tmp_path, (
        "argument_register_baselines:\n"
        "  op-ed:\n"
        "    argumentation_share:\n"
        "      human: { mean: 1.5 }\n"
        "      status: heuristic\n"
    ))
    with pytest.raises(arb.RegisterBaselineError, match=r"\[0, 1\]"):
        arb.load_register("op-ed", yaml_path=p)


def test_calibrated_with_discrimination_is_calibrated(tmp_path):
    p = _write(tmp_path, (
        "argument_register_baselines:\n"
        "  op-ed:\n"
        "    argumentation_share:\n"
        "      human: { mean: 0.7 }\n"
        "      ai: { mean: 0.9 }\n"
        "      status: calibrated\n"
        "      provenance: \"labeled corpus · 2021 · human+AI\"\n"
        "discrimination:\n"
        "  op-ed: { da_AUC: 0.82, FPR_at: 0.1, TPR_at: 0.7, validator_run: \"vh-1\", model: \"gpt-x 2026-01\" }\n"
    ))
    rb = arb.load_register("op-ed", yaml_path=p)
    assert rb.is_calibrated is True
    sig = rb.signals["argumentation_share"]
    assert sig.status == "calibrated" and sig.ai_mean == 0.9
