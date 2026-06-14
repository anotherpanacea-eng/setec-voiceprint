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
    # 6 derived signals; 3 anchored (B1/B2) + 1 directional (thesis) + 2 B5 flags
    assert len(r["contributions"]) == 6
    assert {b["bundle"] for b in r["bundles"]} == {
        "B1_structural_arc", "B2_discourse_mode", "B5_collapse_dynamics",
    }
    # the B5 bundle is present, unanchored -> never enters a bundle mean
    b5 = next(b for b in r["bundles"] if b["bundle"] == "B5_collapse_dynamics")
    assert b5["n_signals"] == 2 and b5["n_evaluated"] == 0
    assert b5["mean_contribution"] is None
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
    assert out["calibration_status"] == "heuristic"
    assert "stance.hedge" in out["signals"]
    assert "agd.discounting_per_1k" in out["signals"]


def test_fixture_reused_keys_match_argmove_vector():
    # The fixture hand-feeds a canonical reused_signals; pin its key shape to the
    # REAL argmove_vector output so a producer-side rename can't drift silently
    # (the contract-blind-spot guard).
    import json
    from pathlib import Path
    import argmove_profile
    golden = json.loads((
        Path(__file__).resolve().parents[2]
        / "references" / "contract_fixtures" / "argument_decision_audit.json"
    ).read_text(encoding="utf-8"))
    fixture_keys = set(golden["results"]["reused_signals"]["signals"])
    real = argmove_profile.argmove_vector(
        "We should act now because the evidence is clear and obvious. "
        "Although critics disagree, the data supports the plan. Therefore decide."
    )
    real_keys = {k for k in real if k != "_n_words"}
    assert fixture_keys == real_keys, (
        f"fixture reused_signals shape drifted from argmove_vector: "
        f"missing {real_keys - fixture_keys}, extra {fixture_keys - real_keys}"
    )


def test_thesis_opening_none_when_first_paragraph_unlabeled():
    # If para 0 is unlabeled, thesis-opening is unknown (None) — never read off a
    # later paragraph (that would fabricate "opens thesis-first").
    labels = [{"role": None, "mode": None},
              {"role": "thesis", "mode": "argumentation"}]
    obs = ada.compute_arc_signals(labels)
    assert obs["thesis_opening_tendency"] is None


def test_contributions_carry_calibration_status():
    # B1/B2 are literature_anchored; the two B5 arc flags are heuristic.
    contribs = ada.per_signal_contributions({"argumentation_share": 0.8})
    by = {c.signal_key: c for c in contribs}
    for key in ("support_to_proposal_rate", "support_to_support_rate",
                "thesis_opening_tendency", "argumentation_share"):
        assert by[key].calibration_status == "literature_anchored"
    for key in ("disappearing_guard_flag", "discounting_straw_men_flag"):
        assert by[key].calibration_status == "heuristic"
        assert by[key].anchored is False
        assert by[key].contribution is None
        assert by[key].paper_human_mean is None and by[key].paper_ai_mean is None


def test_pre_flag_basis_only_names_converged_signals():
    # support_to_proposal HUMAN-side, the other two AI-side -> informative, but
    # the basis must NOT claim the proposal/AT3 hook (it isn't AI-leaning).
    obs = {"support_to_proposal_rate": 0.123,   # human
           "support_to_support_rate": 0.329,    # ai
           "argumentation_share": 0.897}        # ai
    pf = ada.compute_pre_flag(ada.per_signal_contributions(obs))
    assert pf["dialectical_clarity_informative"] is True
    assert "AT3" not in pf["basis"]
    assert "support_to_proposal_rate" not in pf["basis"]
    # When support_to_proposal IS ai-side, the AT3/DC hook is named.
    obs2 = {"support_to_proposal_rate": 0.294, "support_to_support_rate": 0.329,
            "argumentation_share": 0.715}
    pf2 = ada.compute_pre_flag(ada.per_signal_contributions(obs2))
    assert pf2["dialectical_clarity_informative"] is True
    assert "AT3" in pf2["basis"]


def test_directory_target_exits_1(tmp_path):
    rc = ada.main([str(tmp_path), "--judge", "mock"])  # a directory, not a file
    assert rc == 1


def test_register_warning_below_floor(tmp_path):
    rc, env = _run(tmp_path, "Short.\n\nToo short.")  # 2 paragraphs, few words
    assert rc == 0
    warns = env["results"]["target"]["register_warnings"]
    assert any("paragraph" in w.lower() for w in warns)
    assert any("words" in w.lower() for w in warns)


def test_missing_target_exits_1(tmp_path):
    rc = ada.main([str(tmp_path / "nope.txt"), "--judge", "mock"])
    assert rc == 1


def test_missing_manifest_is_bad_input_not_policy_refusal(tmp_path, capsys):
    # --judge=manifest without --judge-manifest is a setup error (bad input),
    # not a privacy-policy refusal. It must exit 2 AND emit a "usage:" line so
    # setec_run._wrap_script_failure categorizes it as bad_input rather than
    # the bare-exit-2 policy_refused bucket.
    target = tmp_path / "e.txt"
    target.write_text("P0.\n\nP1.\n\nP2.", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        ada.main([str(target), "--judge", "manifest"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "usage:" in err.lower()
    assert "judge construction failed" in err


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


# ---- C0: register-baseline plumbing --------------------------------------
def test_register_op_ed_attaches_means(tmp_path):
    """--register op-ed attaches the seeded register_human_mean (= the paper
    anchors) + per-signal literature_anchored status, records target.register,
    and leaves the band uncalibrated (the C0 seed graduates nothing)."""
    text = "\n\n".join(f"Paragraph number {i} makes a point." for i in range(5))
    rc, env = _run(tmp_path, text, argv=["--register", "op-ed"])
    assert rc == 0
    r = env["results"]
    reg = r["target"]["register"]
    assert reg is not None and reg["genre"] == "op-ed" and reg["calibrated"] is False
    by = {c["signal_key"]: c for c in r["contributions"]}
    # anchored signals carry a register_human_mean equal to the paper mean
    assert by["support_to_proposal_rate"]["register_human_mean"] == 0.123
    assert by["argumentation_share"]["register_human_mean"] == 0.715
    # the four B1/B2 signals stay literature_anchored under the op-ed seed.
    for key in ("support_to_proposal_rate", "support_to_support_rate",
                "thesis_opening_tendency", "argumentation_share"):
        assert by[key]["calibration_status"] == "literature_anchored"
    # the B5 arc flags are pinned heuristic and carry NO register means even when
    # a register is supplied (an unanchored arc_flag is never graduated).
    for key in ("disappearing_guard_flag", "discounting_straw_men_flag"):
        assert by[key]["calibration_status"] == "heuristic"
        assert by[key]["register_human_mean"] is None
        assert by[key]["register_provenance"] is None
    assert all(c["register_ai_mean"] is None for c in r["contributions"])  # no calibrated arm
    assert r["aggregate"]["verdict_band"] == "uncalibrated"


def test_register_unknown_genre_falls_back(tmp_path):
    """An unknown --register genre is a soft fallback: no register block, a
    register_warnings note, and contributions keep null register means."""
    text = "\n\n".join(f"Paragraph number {i} makes a point." for i in range(5))
    rc, env = _run(tmp_path, text, argv=["--register", "made_up_genre"])
    assert rc == 0
    r = env["results"]
    assert r["target"]["register"] is None
    assert any("made_up_genre" in w for w in r["target"]["register_warnings"])
    assert all(c["register_human_mean"] is None for c in r["contributions"])


def test_baseline_dir_requires_register(tmp_path):
    """--baseline-dir without --register is a usage error (exit 2 / bad input)."""
    target = tmp_path / "e.txt"
    target.write_text("\n\n".join(f"P{i} argues." for i in range(4)), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        ada.main([str(target), "--judge", "mock", "--baseline-dir", str(tmp_path),
                  "--out", str(tmp_path / "o.json"), "--out-md", str(tmp_path / "o.md")])
    assert exc.value.code == 2


def test_baseline_dir_overrides_shipped(tmp_path):
    """--baseline-dir prefers an operator-local yaml; an empirically_oriented row
    graduates that signal's calibration_status (the C1/C3 drop-in path)."""
    (tmp_path / "argument_register_baselines.yaml").write_text(
        "argument_register_baselines:\n"
        "  op-ed:\n"
        "    support_to_proposal_rate:\n"
        "      human: { mean: 0.140 }\n"
        "      status: empirically_oriented\n"
        "      provenance: \"local test corpus · pre-2022 · hand-authored\"\n"
        "      provisional: false\n",
        encoding="utf-8",
    )
    text = "\n\n".join(f"Paragraph number {i} makes a point." for i in range(5))
    rc, env = _run(tmp_path, text, argv=["--register", "op-ed", "--baseline-dir", str(tmp_path)])
    assert rc == 0
    r = env["results"]
    assert r["target"]["register"]["source"].startswith(str(tmp_path))
    by = {c["signal_key"]: c for c in r["contributions"]}
    assert by["support_to_proposal_rate"]["register_human_mean"] == 0.140
    assert by["support_to_proposal_rate"]["calibration_status"] == "empirically_oriented"


# ---- B5: collapse-dynamics derivation ------------------------------------
def _lbl(role, mode="argumentation", guard=None, claim=None, obj=None):
    return {"role": role, "mode": mode, "guard_strength": guard,
            "claim_ref": claim, "objection_strength": obj}


def test_disappearing_guard_true_on_downward_transition():
    # same claim guarded strong early, then weak later -> True.
    labels = [
        _lbl("thesis", guard="strong", claim="c1"),
        _lbl("support", guard="moderate", claim="c2"),
        _lbl("support", guard="weak", claim="c1"),
    ]
    out = ada.compute_collapse_dynamics(labels, None)
    assert out["disappearing_guard_flag"] is True


def test_disappearing_guard_false_when_tracked_but_no_drop():
    # claim c1 tracked across 2 paragraphs but guard does not weaken -> False.
    labels = [
        _lbl("thesis", guard="strong", claim="c1"),
        _lbl("support", guard="strong", claim="c1"),
    ]
    out = ada.compute_collapse_dynamics(labels, None)
    assert out["disappearing_guard_flag"] is False


def test_disappearing_guard_none_when_no_claim_spans_two():
    # no claim_ref appears in >=2 paragraphs with guard data -> None (not False).
    labels = [
        _lbl("thesis", guard="strong", claim="c1"),
        _lbl("support", guard="weak", claim="c2"),
    ]
    out = ada.compute_collapse_dynamics(labels, None)
    assert out["disappearing_guard_flag"] is None


def test_disappearing_guard_none_when_no_guard_data():
    labels = [_lbl("thesis", claim="c1"), _lbl("support", claim="c1")]
    out = ada.compute_collapse_dynamics(labels, None)
    assert out["disappearing_guard_flag"] is None


def test_discounting_straw_men_true_weak_objection_strong_ignored():
    labels = [
        _lbl("thesis"),
        _lbl("counterclaim", obj="weak"),
        _lbl("rebuttal", obj="weak"),
    ]
    out = ada.compute_collapse_dynamics(labels, strongest_internal_objection_engaged=False)
    assert out["discounting_straw_men_flag"] is True


def test_discounting_straw_men_false_when_strongest_engaged():
    labels = [_lbl("thesis"), _lbl("counterclaim", obj="strong")]
    out = ada.compute_collapse_dynamics(labels, strongest_internal_objection_engaged=True)
    assert out["discounting_straw_men_flag"] is False


def test_discounting_straw_men_none_when_no_objection_paragraph():
    labels = [_lbl("thesis"), _lbl("support")]
    out = ada.compute_collapse_dynamics(labels, strongest_internal_objection_engaged=False)
    assert out["discounting_straw_men_flag"] is None


def test_discounting_straw_men_none_when_doc_field_unknown():
    # a counterclaim exists but the strongest-objection judgment is null -> None,
    # never a fabricated False.
    labels = [_lbl("thesis"), _lbl("counterclaim", obj="weak")]
    out = ada.compute_collapse_dynamics(labels, strongest_internal_objection_engaged=None)
    assert out["discounting_straw_men_flag"] is None


def test_b5_contributions_shape_anchored_false_contribution_null():
    obs = {"disappearing_guard_flag": True, "discounting_straw_men_flag": None}
    contribs = ada.per_signal_contributions(obs)
    by = {c.signal_key: c for c in contribs}
    dg = by["disappearing_guard_flag"]
    assert dg.anchored is False and dg.contribution is None
    assert dg.calibration_status == "heuristic" and dg.bundle == "B5_collapse_dynamics"
    assert dg.observed_value is True and dg.direction == "directional"
    ds = by["discounting_straw_men_flag"]
    assert ds.observed_value is None and ds.direction == "unavailable"


def test_b5_status_immune_to_register_graduation(tmp_path):
    """A register row keyed to a B5 arc_flag must NOT graduate it above heuristic
    (an unanchored arc_flag has no numeric anchor to calibrate) — the review's
    register-graduation guard."""
    (tmp_path / "argument_register_baselines.yaml").write_text(
        "argument_register_baselines:\n"
        "  op-ed:\n"
        "    disappearing_guard_flag:\n"
        "      human: { mean: 0.300 }\n"
        "      status: empirically_oriented\n"
        "      provenance: \"adversarial register row · should be ignored for B5\"\n"
        "      provisional: false\n",
        encoding="utf-8",
    )
    text = "\n\n".join(f"Paragraph number {i} makes a point." for i in range(5))
    rc, env = _run(tmp_path, text, argv=["--register", "op-ed", "--baseline-dir", str(tmp_path)])
    assert rc == 0
    by = {c["signal_key"]: c for c in env["results"]["contributions"]}
    dg = by["disappearing_guard_flag"]
    assert dg["calibration_status"] == "heuristic"   # pinned, not graduated
    assert dg["register_human_mean"] is None          # register mean not attached
    assert dg["register_provenance"] is None


def test_additive_envelope_aggregate_unchanged_by_b5():
    """The review-binding regression guard: B5 must not perturb the aggregate
    score, the verdict band, or the pre_flag boolean. Compute the B1/B2-only
    contributions (no B5 keys) and the full observed (with B5 keys) and assert
    the score/band/pre_flag are byte-identical."""
    base_obs = {
        "support_to_proposal_rate": 0.294,
        "support_to_support_rate": 0.329,
        "argumentation_share": 0.897,
        "thesis_opening_tendency": 0.0,
    }
    full_obs = dict(base_obs)
    full_obs.update({"disappearing_guard_flag": True, "discounting_straw_men_flag": True})

    base_c = ada.per_signal_contributions(base_obs)
    full_c = ada.per_signal_contributions(full_obs)

    base_agg = ada.aggregate_score(base_c)
    full_agg = ada.aggregate_score(full_c)
    assert base_agg["score"] == full_agg["score"]
    assert base_agg["verdict_band"] == full_agg["verdict_band"]
    # only the total-signal COUNT grows (4 -> 6); the evaluated count is unchanged.
    assert base_agg["n_signals_evaluated"] == full_agg["n_signals_evaluated"]

    base_pf = ada.compute_pre_flag(base_c)
    full_pf = ada.compute_pre_flag(full_c)
    assert base_pf["dialectical_clarity_informative"] == full_pf["dialectical_clarity_informative"]
    assert base_pf["basis"] == full_pf["basis"]


def test_b5_excluded_from_pre_flag_arc_keys():
    # Even with both B5 flags True (AI-leaning), the pre_flag stays driven only by
    # the B1/B2 arc keys — B5 is never in arc_keys.
    obs = {
        "support_to_proposal_rate": 0.123,   # human
        "support_to_support_rate": 0.525,    # human
        "argumentation_share": 0.715,        # human
        "disappearing_guard_flag": True,
        "discounting_straw_men_flag": True,
    }
    pf = ada.compute_pre_flag(ada.per_signal_contributions(obs))
    assert pf["dialectical_clarity_informative"] is False


def test_end_to_end_doc_level_field_in_envelope(tmp_path):
    # the doc-level collapse field lands in results.collapse_dynamics.
    text = "\n\n".join(f"Paragraph number {i} makes a point." for i in range(5))
    rc, env = _run(tmp_path, text)
    assert rc == 0
    cd = env["results"]["collapse_dynamics"]
    assert "strongest_internal_objection_engaged" in cd
    # mock judge emits no objection role -> doc-level field is null.
    assert cd["strongest_internal_objection_engaged"] is None
    # and the mock's strong->weak guard on a shared claim_ref derives True.
    by = {c["signal_key"]: c for c in env["results"]["contributions"]}
    assert by["disappearing_guard_flag"]["observed_value"] is True
    assert by["discounting_straw_men_flag"]["observed_value"] is None
