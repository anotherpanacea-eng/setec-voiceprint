"""Judge/model-free pins for the spec-78 calibration consumer."""
from __future__ import annotations

import sys
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALIB = ROOT / "calibration"
for path in (str(ROOT), str(CALIB)):
    if path not in sys.path:
        sys.path.insert(0, path)

import narrative_polarity_extension as npe  # type: ignore  # noqa: E402
from storyscope_polarity_contract import FRAME_DOMAINS, framed_file_digest, source_work_sha256  # type: ignore  # noqa: E402


def test_response_class_disjoint_and_total():
    numeric = {k for k, v in npe.RESPONSE_CLASS_BY_SIGNAL_ID.items() if v == "numeric"}
    indicator = {k for k, v in npe.RESPONSE_CLASS_BY_SIGNAL_ID.items() if v == "indicator"}
    assert numeric.isdisjoint(indicator)
    assert numeric | indicator == set(npe.SIGNAL_IDS)
    assert (len(numeric), len(indicator)) == (19, 14)


def test_framed_hashes_use_the_shared_registry_and_domains():
    payload = b"same bytes"
    domains = sorted(FRAME_DOMAINS)
    assert len(domains) == 12
    assert npe.framed_sha256(domains[0], payload) != npe.framed_sha256(domains[1], payload)


def test_numeric_hedges_zero_pooled_sd_is_indeterminate():
    assert npe.hedges_g([1.0, 1.0], [2.0, 2.0], "ai") is None


def test_verdict_precedence_control_blocks_interval_verdict():
    verdict, step = npe.derive_polarity_verdict(
        arm="segment_regime", availability=(1.0, 1.0), availability_floor=.9,
        support=(24, 24), min_support=24, bridge_support=(8, 8), min_bridge=8,
        response_class="numeric", min_class_n=20,
        bridge=((.01, .30), (.01, .02)), ceiling=.1, degenerate=False,
        interval=(.5, .9), threshold=.2,
    )
    assert (verdict, step) == ("bridge_inconclusive", 3)


def test_crossed_bridge_point_and_ucb_is_inconclusive_not_artifact():
    verdict, step = npe.derive_polarity_verdict(
        arm="segment_regime", availability=(1.0, 1.0), availability_floor=.9,
        support=(24, 24), min_support=24, bridge_support=(8, 8), min_bridge=8,
        response_class="numeric", min_class_n=20,
        bridge=((.09, .11), (.01, .02)), ceiling=.1, degenerate=False,
        interval=(.5, .9), threshold=.2,
    )
    assert (verdict, step) == ("bridge_inconclusive", 3)


def test_quantile_overlap_handles_ties_and_more_bins_than_rows():
    rows = [
        {"label": "pre_ai_human", "n_words": 10},
        {"label": "pre_ai_human", "n_words": 10},
        {"label": "ai_generated", "n_words": 10},
        {"label": "ai_generated", "n_words": 10},
    ]
    assert npe._length_overlap(rows, 10) == 1.0
    rows[-1]["n_words"] = 20
    assert npe._length_overlap(rows, 10) == 0.5


def test_eight_field_design_substitution_changes_digest():
    base = {
        "text_id": "t", "label": "pre_ai_human", "role": "primary",
        "source_kind": "segment", "source_work_id": "w",
        "subfloor_bridge_side": None, "content_sha256": "sha256:" + "0" * 64,
        "source_work_sha256": "sha256:" + "1" * 64,
    }
    changed = dict(base, source_work_id="other")
    digest = lambda rows: npe.framed_sha256(
        "setec.voiceprint.spec78.design-projection-json.v1",
        npe.canonical_json(npe._design(rows)),
    )
    assert digest([base]) != digest([changed])


def test_receipt_and_signal_cell_keysets_are_pinned():
    receipt_keys = {
        "schema_version", "date", "arm", "signal_id_set_sha256", "thresholds_sha256",
        "registration_sha256", "derivation_sha256", "manifest_sha256",
        "source_envelopes_sha256", "registration_path", "manifest_path", "class_counts",
        "covered_length_range", "covered_source_work_range", "segmenter", "judge",
        "bridge_read_mode", "floors_applied", "bands_applied", "multiplicity",
        "deferrals", "stated_limits", "per_signal",
    }
    signal_keys = {
        "verdict", "verdict_step", "operator", "units", "transfer_caveat",
        "response_class", "support", "availability_by_class", "separation_saturated",
        "sign_stability", "statistics", "ci", "bridge", "multiplicity",
        "joint_claim_suppressed",
    }
    assert len(receipt_keys) == 23
    assert len(signal_keys) == 15
    # The construction site is intentionally direct: a future key addition must
    # update this test before it can silently expand the receipt contract.
    source = Path(npe.__file__).read_text(encoding="utf-8")
    for key in receipt_keys | signal_keys:
        assert f'"{key}"' in source


def test_verify_rejects_tampered_rederived_receipt(tmp_path, monkeypatch):
    expected = {"derivation_sha256": "sha256:good", "verdict": "polarity_matches"}
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(dict(expected, verdict="polarity_inverted")), encoding="utf-8")
    monkeypatch.setattr(npe, "evaluate", lambda **_: expected)
    with pytest.raises(npe.CalibrationRefusal):
        npe.verify_receipt(receipt=receipt, arm="subfloor", manifest=tmp_path / "m", thresholds=tmp_path / "t", registration=tmp_path / "r", date="2026-07-27")


def test_arm_b_unrelated_truncation_refuses(tmp_path):
    full = tmp_path / "full.txt"
    short = tmp_path / "short.txt"
    full.write_text("one two three four", encoding="utf-8")
    short.write_text("one three", encoding="utf-8")
    def envelope(path: Path, name: str) -> Path:
        out = tmp_path / name
        out.write_text(json.dumps({"target": {"path": str(path)}}), encoding="utf-8")
        return out
    full_env, short_env = envelope(full, "full.json"), envelope(short, "short.json")
    row = lambda side, env, n: {"label": "pre_ai_human", "source_work_id": "w", "role": "bridge", "subfloor_bridge_side": side, "judge": {"source_envelope_path": str(env)}, "n_words": n}
    with pytest.raises(npe.CalibrationRefusal) as exc:
        npe._validate_truncations([row("full", full_env, 4), row("truncated", short_env, 2)], "subfloor")
    assert exc.value.reason == "source_envelope_mismatch"


def test_arm_a_equal_length_source_swap_refuses(tmp_path):
    source_a, source_b = tmp_path / "a.txt", tmp_path / "b.txt"
    source_a.write_text("one two three", encoding="utf-8")
    source_b.write_text("red blue green", encoding="utf-8")
    env = tmp_path / "bridge.json"
    env.write_text(json.dumps({
        "target": {"path": str(source_b)},
        "results": {"judge": {"kind": "manifest", "model": "m", "model_revision": "r", "prompt_version": "p"}},
    }), encoding="utf-8")
    row = {
        "source_work_words": 3, "source_work_sha256": source_work_sha256(source_a.read_text()),
        "source_kind": "whole_work", "n_words": 3, "content_sha256": "sha256:" + "0" * 64,
        "judge": {"source_envelope_path": str(env), "source_envelope_sha256": framed_file_digest("setec.voiceprint.spec78.source-envelope-file.v1", env), "kind": "manifest", "model": "m", "model_revision": "r", "prompt_version": "p"},
    }
    with pytest.raises(npe.CalibrationRefusal) as exc:
        npe._reopen_envelopes([row])
    assert exc.value.reason == "source_envelope_mismatch"


def test_arm_a_false_source_work_word_count_refuses(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("one two three", encoding="utf-8")
    digest = source_work_sha256(source.read_text(encoding="utf-8"))
    env = tmp_path / "bridge.json"
    env.write_text(json.dumps({
        "target": {
            "path": str(source),
            "words": 99_999,
            "source_content_sha256": digest,
        },
        "results": {},
    }), encoding="utf-8")
    row = {
        "source_work_words": 99_999,
        "source_work_sha256": digest,
        "source_kind": "whole_work",
        "n_words": 99_999,
        "content_sha256": "sha256:" + "0" * 64,
        "judge": {
            "source_envelope_path": str(env),
            "source_envelope_sha256": framed_file_digest(
                "setec.voiceprint.spec78.source-envelope-file.v1", env
            ),
            "kind": "manifest",
            "model": "m",
            "model_revision": "r",
            "prompt_version": "p",
        },
    }
    with pytest.raises(npe.CalibrationRefusal) as exc:
        npe._reopen_envelopes([row])
    assert exc.value.reason == "source_envelope_mismatch"


def test_prompt_blindness_scan_hits_signal_names_but_not_eyes():
    assert npe._prompt_names_signal("Use StoryScope narrative analysis.")
    assert npe._prompt_names_signal("Choose protagonist_choice when appropriate.")
    assert not npe._prompt_names_signal("Her eyes adjusted to the dark yesterday.")


def test_segmenter_registration_requires_exact_four_key_identity(tmp_path):
    # This validates the registration carrier directly; CLI supplies these
    # same three identity flags plus the fixed emitter.
    bad = {"emitter": "narrative_decision_long_form", "segmenter_version": npe.SEGMENTER_VERSION}
    with pytest.raises(npe.CalibrationRefusal):
        npe._validate_registration({
            "schema": "narrative-polarity-registration/1", "date": "2026-07-27", "arm": "segment_regime",
            "thresholds_sha256": "x", "work_ids_sha256": "x", "design_sha256": "x", "signal_id_set_sha256": "x",
            "segmenter": bad,
            "judge": {"kind": "manifest", "model": "m", "model_revision": "r", "prompt_version": "p", "context_bound_words": 1},
            "generation_prompts": [],
        }, arm="segment_regime", date="2026-07-27")


def test_not_aggregatable_transfer_caveat_policy_is_exact():
    expected = {
        npe.signal_id_for(f, s) for f, _, s in npe.iter_signals()
        if npe.OPERATOR_TABLE[(f.key, s.option)] == "not_aggregatable"
    }
    assert len(expected) == 12
    assert all(npe.OPERATOR_TABLE[(next(f.key for f, _, s in npe.iter_signals() if npe.signal_id_for(f, s) == sid), npe.SIGNAL_SPECS[sid].option)] == "not_aggregatable" for sid in expected)


def test_real_signal_spec_numeric_encoder_and_response_range():
    sid = next(sid for sid, spec in npe.SIGNAL_SPECS.items() if spec.feature_type == "scale")
    spec = npe.SIGNAL_SPECS[sid]
    assert npe.convert_mean_response(spec, spec.response_options[0]) >= 0
    assert npe._response_range(sid) > 0


def test_malformed_signals_and_judge_refuse_not_typeerror():
    row = {
        "text_id":"x", "label":"pre_ai_human", "role":"primary", "source_kind":"whole_work",
        "source_work_id":"w", "source_work_words":None, "source_work_sha256":None, "n_words":1,
        "content_sha256":"sha256:" + "0" * 64, "subfloor_bridge_side":None,
        "provenance":{"class":"human", "author_id":"a", "publication_year":1, "source_corpus_id":"c", "claim_license_amendment":None, "register_extension":None},
        "segmenter":None, "read_mode":None, "judge":[], "signals":[],
    }
    with pytest.raises(npe.CalibrationRefusal):
        npe._validate_row(row, "subfloor", {"kind":"manifest", "model":"m", "model_revision":"r", "prompt_version":"p", "context_bound_words":1})


def test_null_signal_cells_refuse_before_fingerprint_walk():
    row = {
        "text_id":"x", "label":"pre_ai_human", "role":"primary",
        "source_kind":"whole_work", "source_work_id":"w",
        "source_work_words":None, "source_work_sha256":None, "n_words":1,
        "content_sha256":"sha256:" + "0" * 64,
        "subfloor_bridge_side":None,
        "provenance":{
            "class":"human", "author_id":"a", "publication_year":1,
            "source_corpus_id":"c", "claim_license_amendment":None,
            "register_extension":None,
        },
        "segmenter":None, "read_mode":None,
        "judge":{
            "kind":"manifest", "model":"m", "model_revision":"r",
            "prompt_version":"p", "source_envelope_sha256":"sha256:" + "1" * 64,
            "source_envelope_path":"producer.json",
        },
        "signals":{sid:None for sid in npe.SIGNAL_IDS},
    }
    with pytest.raises(npe.CalibrationRefusal) as exc:
        npe._validate_row(
            row, "subfloor",
            {"kind":"manifest", "model":"m", "model_revision":"r",
             "prompt_version":"p", "context_bound_words":1},
        )
    assert exc.value.reason == "manifest_schema_violation"


def test_corpus_floor_routes_to_step_two_insufficient_support():
    verdict, step = npe.derive_polarity_verdict(
        arm="subfloor", availability=(1, 1), availability_floor=.9, support=(24,24), min_support=24,
        bridge_support=(8,8), min_bridge=8, response_class="numeric", min_class_n=20,
        bridge=((0,0), (0,0)), ceiling=.1, degenerate=False, interval=(.4,.6), threshold=.2,
        corpus_ok=False,
    )
    assert (verdict, step) == ("insufficient_support", 2)


def test_receipt_guard_rejects_per_text_key_and_unlisted_float():
    try:
        npe.assert_no_per_text_disclosure({"per_text": {}})
    except ValueError:
        pass
    else:
        raise AssertionError("per_text key escaped receipt guard")
    try:
        npe.assert_no_per_text_disclosure({"unlisted": 1.2})
    except ValueError:
        pass
    else:
        raise AssertionError("unlisted float escaped receipt guard")
