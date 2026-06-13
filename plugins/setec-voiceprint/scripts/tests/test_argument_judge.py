"""Tests for argument_judge — the per-paragraph role/mode labeler plumbing."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argument_judge as j  # noqa: E402
from argument_feature_schema import MODE_OPTIONS, ROLE_OPTIONS  # noqa: E402


# ---- mock judge ----------------------------------------------------------
def test_mock_judge_is_deterministic_and_full_length():
    mock = j.build_judge("mock")
    paras = ["a", "b", "c", "d"]
    r1 = mock(paras)
    r2 = mock(paras)
    assert r1.values == r2.values
    seq = r1.values["paragraphs"]
    assert len(seq) == 4
    # default role_index=1 (support), mode_index=0 (argumentation)
    assert all(p["role"] == "support" and p["mode"] == "argumentation" for p in seq)
    assert r1.judge_identity["kind"] == "mock"


def test_mock_judge_respects_option_indices():
    mock = j.build_judge("mock", mock_role_index=0, mock_mode_index=1)
    seq = mock(["x"]).values["paragraphs"]
    assert seq[0]["role"] == ROLE_OPTIONS[0] == "thesis"
    assert seq[0]["mode"] == MODE_OPTIONS[1] == "exposition"


# ---- validate_labels -----------------------------------------------------
def test_validate_good_sequence_no_warnings():
    vals = {"paragraphs": [
        {"index": 0, "role": "thesis", "mode": "argumentation"},
        {"index": 1, "role": "support", "mode": "exposition"},
        {"index": 2, "role": "proposal", "mode": "argumentation"},
    ]}
    cleaned, warns = j.validate_labels(vals, n_paragraphs=3)
    assert warns == []
    assert cleaned == [
        {"role": "thesis", "mode": "argumentation"},
        {"role": "support", "mode": "exposition"},
        {"role": "proposal", "mode": "argumentation"},
    ]


def test_validate_bad_role_and_mode_nulled_with_warnings():
    vals = {"paragraphs": [{"index": 0, "role": "BOGUS", "mode": "alsobad"}]}
    cleaned, warns = j.validate_labels(vals, n_paragraphs=1)
    assert cleaned[0] == {"role": None, "mode": None}
    assert any("role 'BOGUS'" in w for w in warns)
    assert any("mode 'alsobad'" in w for w in warns)


def test_validate_missing_paragraphs_flagged():
    vals = {"paragraphs": [{"index": 0, "role": "thesis", "mode": "argumentation"}]}
    cleaned, warns = j.validate_labels(vals, n_paragraphs=3)
    assert cleaned[1] == {"role": None, "mode": None}
    assert cleaned[2] == {"role": None, "mode": None}
    assert any("missing indices" in w for w in warns)


def test_validate_non_list_all_null():
    cleaned, warns = j.validate_labels({"paragraphs": "nope"}, n_paragraphs=2)
    assert cleaned == [{"role": None, "mode": None}, {"role": None, "mode": None}]
    assert any("missing a 'paragraphs' list" in w for w in warns)


def test_validate_out_of_order_indices_align():
    vals = {"paragraphs": [
        {"index": 2, "role": "proposal", "mode": "argumentation"},
        {"index": 0, "role": "thesis", "mode": "argumentation"},
        {"index": 1, "role": "support", "mode": "argumentation"},
    ]}
    cleaned, warns = j.validate_labels(vals, n_paragraphs=3)
    assert warns == []
    assert cleaned[0]["role"] == "thesis"
    assert cleaned[2]["role"] == "proposal"


def test_validate_duplicate_index_keeps_first():
    vals = {"paragraphs": [
        {"index": 0, "role": "thesis", "mode": "argumentation"},
        {"index": 0, "role": "support", "mode": "exposition"},
    ]}
    cleaned, warns = j.validate_labels(vals, n_paragraphs=1)
    assert cleaned[0]["role"] == "thesis"
    assert any("duplicate label" in w for w in warns)


def test_validate_extra_entries_flagged():
    vals = {"paragraphs": [
        {"index": 0, "role": "thesis", "mode": "argumentation"},
        {"index": 1, "role": "support", "mode": "argumentation"},
    ]}
    cleaned, warns = j.validate_labels(vals, n_paragraphs=1)
    assert any("extras ignored" in w for w in warns)


# ---- manifest backend ----------------------------------------------------
def test_manifest_judge_round_trip(tmp_path):
    manifest = tmp_path / "labels.json"
    manifest.write_text(json.dumps({
        "values": {"paragraphs": [
            {"index": 0, "role": "thesis", "mode": "argumentation", "confidence": 0.9},
            {"index": 1, "role": "support", "mode": "exposition"},
        ]},
        "judge_identity": {"model": "gpt-X", "prompt_version": "v1"},
    }), encoding="utf-8")
    judge = j.build_judge("manifest", manifest_path=manifest)
    res = judge(["p0", "p1"])
    assert res.values["paragraphs"][0]["role"] == "thesis"
    assert res.judge_identity["kind"] == "manifest"
    assert res.judge_identity["model"] == "gpt-X"
    assert res.per_paragraph_confidence[0] == 0.9


def test_manifest_missing_paragraphs_raises(tmp_path):
    manifest = tmp_path / "bad.json"
    manifest.write_text(json.dumps({"values": {}}), encoding="utf-8")
    with pytest.raises(j.JudgeError):
        j.build_judge("manifest", manifest_path=manifest)


def test_manifest_bad_paths_all_raise_judge_error(tmp_path):
    # Every manifest failure mode must surface as JudgeError (-> clean exit 2),
    # never a raw traceback.
    with pytest.raises(j.JudgeError):
        j.build_judge("manifest", manifest_path=tmp_path / "nope.json")  # missing
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all", encoding="utf-8")
    with pytest.raises(j.JudgeError):
        j.build_judge("manifest", manifest_path=bad)  # invalid JSON
    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(j.JudgeError):
        j.build_judge("manifest", manifest_path=arr)  # array top level


def test_manifest_non_dict_judge_identity_does_not_crash(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({
        "values": {"paragraphs": [{"index": 0, "role": "thesis", "mode": "argumentation"}]},
        "judge_identity": "gpt-X",  # malformed (string, not dict)
    }), encoding="utf-8")
    res = j.build_judge("manifest", manifest_path=manifest)(["p0"])  # must not raise
    assert res.judge_identity["model"] is None


def test_extract_json_rejects_non_object():
    # A bare array (a likely model output for per-paragraph labels) is a clean
    # ValueError -> JudgeError via the API backends, not an AttributeError.
    with pytest.raises(ValueError):
        j._extract_json('[{"index": 0, "role": "thesis"}]')


def test_confidences_keep_first_matches_label_policy():
    # validate_labels keeps the FIRST entry per index; confidences must too.
    assert j._confidences([{"index": 0, "confidence": 0.9},
                           {"index": 0, "confidence": 0.1}], 1) == [0.9]


def test_bool_index_and_confidence_rejected():
    # bool is an int subclass — must not be accepted as an index or a confidence.
    cleaned, _ = j.validate_labels(
        {"paragraphs": [{"index": True, "role": "thesis", "mode": "argumentation"}]},
        n_paragraphs=2)
    assert cleaned == [{"role": None, "mode": None}, {"role": None, "mode": None}]
    assert j._confidences([{"index": 0, "confidence": True}], 1) == [None]


# ---- factory errors ------------------------------------------------------
def test_factory_unknown_kind_raises():
    with pytest.raises(j.JudgeError):
        j.build_judge("banana")


def test_factory_manifest_requires_path():
    with pytest.raises(j.JudgeError):
        j.build_judge("manifest")


def test_factory_api_requires_model():
    for kind in ("anthropic", "openai", "gemini"):
        with pytest.raises(j.JudgeError):
            j.build_judge(kind)  # no --judge-model


# ---- prompt + fingerprint ------------------------------------------------
def test_prompt_covers_all_roles_and_modes():
    prompt = j.render_prompt()
    assert all(r in prompt for r in ROLE_OPTIONS)
    assert all(m in prompt for m in MODE_OPTIONS)


def test_fingerprint_is_stable_sha256():
    fp1 = j.fingerprint_prompt()
    fp2 = j.fingerprint_prompt()
    assert fp1 == fp2 and len(fp1) == 64
