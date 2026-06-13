"""Tests for argument_decision_audit — the ArgScope Layer A surface."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argument_decision_audit as ada  # noqa: E402
from argument_judge import build_judge  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # noqa: E402


# ---- registration --------------------------------------------------------
def test_task_surface_registered():
    assert ada.TASK_SURFACE == "argument_decision_audit"
    assert ada.TASK_SURFACE in VALID_TASK_SURFACES


# ---- paragraph splitting -------------------------------------------------
def test_split_paragraphs_drops_blank_runs():
    text = "Para one.\n\n\n Para two. \n\nPara three."
    assert ada.split_paragraphs(text) == ["Para one.", "Para two.", "Para three."]


# ---- arc-signal computation ----------------------------------------------
def test_compute_arc_signals_row_normalized_from_support():
    # roles: thesis, support, support, proposal, support, proposal
    labels = [
        {"role": "thesis", "mode": "argumentation"},
        {"role": "support", "mode": "argumentation"},
        {"role": "support", "mode": "exposition"},
        {"role": "proposal", "mode": "argumentation"},
        {"role": "support", "mode": "argumentation"},
        {"role": "proposal", "mode": "narration"},
    ]
    obs = ada.compute_arc_signals(labels)
    # support transitions (with a labeled successor): s->s, s->proposal, s->proposal
    # denom=3; support->proposal=2 -> 2/3; support->support=1 -> 1/3
    assert obs["support_to_proposal_rate"] == pytest.approx(2 / 3)
    assert obs["support_to_support_rate"] == pytest.approx(1 / 3)
    # argumentation share over 6 labeled modes: 4 argumentation -> 4/6
    assert obs["argumentation_share"] == pytest.approx(4 / 6)
    # first role is thesis -> thesis-opening 1.0
    assert obs["thesis_opening_tendency"] == 1.0


def test_compute_arc_signals_none_when_no_support_chain():
    labels = [{"role": "thesis", "mode": "argumentation"},
              {"role": "proposal", "mode": "argumentation"}]
    obs = ada.compute_arc_signals(labels)
    assert obs["support_to_proposal_rate"] is None
    assert obs["support_to_support_rate"] is None
    assert obs["argumentation_share"] == 1.0
    assert obs["thesis_opening_tendency"] == 1.0


def test_compute_arc_signals_skips_unlabeled_successor():
    labels = [{"role": "support", "mode": None}, {"role": None, "mode": None}]
    obs = ada.compute_arc_signals(labels)
    # the only support has a None successor -> not counted -> rates None
    assert obs["support_to_proposal_rate"] is None
    assert obs["argumentation_share"] is None  # no labeled modes


# ---- contribution math ---------------------------------------------------
def test_contribution_is_one_at_human_mean_zero_at_ai_mean():
    # support_to_proposal: human 0.123, ai 0.294
    at_human = ada.per_signal_contributions({"support_to_proposal_rate": 0.123})
    c = next(x for x in at_human if x.signal_key == "support_to_proposal_rate")
    assert c.contribution == pytest.approx(1.0)
    at_ai = ada.per_signal_contributions({"support_to_proposal_rate": 0.294})
    c = next(x for x in at_ai if x.signal_key == "support_to_proposal_rate")
    assert c.contribution == pytest.approx(0.0)


def test_unanchored_signal_has_no_contribution():
    contribs = ada.per_signal_contributions({"thesis_opening_tendency": 1.0})
    c = next(x for x in contribs if x.signal_key == "thesis_opening_tendency")
    assert c.anchored is False
    assert c.contribution is None
    assert c.direction == "directional"
    assert c.observed_value == 1.0


def test_unavailable_signal_when_observed_none():
    contribs = ada.per_signal_contributions({"argumentation_share": None})
    c = next(x for x in contribs if x.signal_key == "argumentation_share")
    assert c.contribution is None and c.direction == "unavailable"


def test_aggregate_skips_unavailable_and_unanchored():
    # only support_to_support observed -> 1 evaluated signal
    contribs = ada.per_signal_contributions({"support_to_support_rate": 0.525})
    agg = ada.aggregate_score(contribs)
    assert agg["n_signals_evaluated"] == 1
    assert agg["verdict_band"] == "uncalibrated"
    assert agg["score"] == pytest.approx(1.0)  # at human mean


# ---- pre_flag ------------------------------------------------------------
def test_pre_flag_fires_on_collapse_leaning_majority():
    # all three anchored signals at their LLM means -> all AI-leaning -> informative
    obs = {"support_to_proposal_rate": 0.294, "support_to_support_rate": 0.329,
           "argumentation_share": 0.897}
    contribs = ada.per_signal_contributions(obs)
    pf = ada.compute_pre_flag(contribs)
    assert pf["dialectical_clarity_informative"] is True


def test_pre_flag_quiet_when_human_leaning():
    obs = {"support_to_proposal_rate": 0.123, "support_to_support_rate": 0.525,
           "argumentation_share": 0.715}
    contribs = ada.per_signal_contributions(obs)
    pf = ada.compute_pre_flag(contribs)
    assert pf["dialectical_clarity_informative"] is False


# ---- end-to-end with the mock judge --------------------------------------
def _run(tmp_path, text, **kw):
    target = tmp_path / "essay.txt"
    target.write_text(text, encoding="utf-8")
    out_json = tmp_path / "o.json"
    rc = ada.main([str(target), "--judge", "mock", "--out", str(out_json),
                   "--out-md", str(tmp_path / "o.md"), *kw.get("argv", [])])
    return rc, json.loads(out_json.read_text())


def test_end_to_end_mock_envelope(tmp_path):
    text = "\n\n".join(f"Paragraph number {i} makes a point." for i in range(5))
    rc, env = _run(tmp_path, text)
    assert rc == 0
    assert env["schema_version"] == "1.0"
    assert env["task_surface"] == "argument_decision_audit"
    assert env["available"] is True
    r = env["results"]
    assert r["target"]["register_match"] == ["op-ed"]
    assert r["target"]["paragraphs"] == 5
    assert r["aggregate"]["verdict_band"] == "uncalibrated"
    assert r["aggregate"]["thresholds"] == {"low": None, "high": None}
    # 4 derived signals; 3 anchored + 1 directional
    assert len(r["contributions"]) == 4
    assert {b["bundle"] for b in r["bundles"]} == {"B1_structural_arc", "B2_discourse_mode"}
    # mock labels all (support, argumentation) -> judge provenance is mock
    assert r["judge"]["judge_identity"]["kind"] == "mock"
    # B3/B4 reuse present + descriptive (heuristic, not in the aggregate)
    rs = r["reused_signals"]
    assert rs["available"] is True
    assert any(k.startswith("stance.") for k in rs["signals"])
    assert any(k.startswith("agency.") for k in rs["signals"])
    assert any(k.startswith("agd.") for k in rs["signals"])


def test_reused_signals_degrade_gracefully(monkeypatch):
    # A reused-audit failure (schema drift / missing optional dep) must NOT crash
    # the surface — B3/B4 reuse is descriptive context, not load-bearing.
    import argmove_profile
    def boom(_text):
        raise argmove_profile.ContractError("simulated reuse-audit schema drift")
    monkeypatch.setattr(argmove_profile, "argmove_vector", boom)
    out = ada.compute_reused_signals("some argumentative text here")
    assert out["available"] is False
    assert "ContractError" in out["reason"]


def test_reused_signals_real_compute_shape():
    # The real reuse path (stdlib audits) yields the stance/agency/agd vector.
    text = (
        "We should act now, because the evidence is clear. "
        "Although critics disagree, the data obviously supports the plan. "
        "Therefore the council must decide this session."
    )
    out = ada.compute_reused_signals(text)
    assert out["available"] is True
    assert "stance.hedge" in out["signals"]
    assert "agd.discounting_per_1k" in out["signals"]


def test_register_warning_below_floor(tmp_path):
    rc, env = _run(tmp_path, "Short.\n\nToo short.")  # 2 paragraphs, few words
    assert rc == 0
    warns = env["results"]["target"]["register_warnings"]
    assert any("paragraph" in w.lower() for w in warns)
    assert any("words" in w.lower() for w in warns)


def test_missing_target_exits_1(tmp_path):
    rc = ada.main([str(tmp_path / "nope.txt"), "--judge", "mock"])
    assert rc == 1


def test_manifest_judge_round_trip(tmp_path):
    text = "\n\n".join(f"P{i}." for i in range(3))
    target = tmp_path / "e.txt"
    target.write_text(text, encoding="utf-8")
    manifest = tmp_path / "labels.json"
    manifest.write_text(json.dumps({"values": {"paragraphs": [
        {"index": 0, "role": "thesis", "mode": "argumentation"},
        {"index": 1, "role": "support", "mode": "argumentation"},
        {"index": 2, "role": "proposal", "mode": "argumentation"},
    ]}}), encoding="utf-8")
    rc = ada.main([str(target), "--judge", "manifest", "--judge-manifest",
                   str(manifest), "--out", str(tmp_path / "o.json"),
                   "--out-md", str(tmp_path / "o.md")])
    assert rc == 0
