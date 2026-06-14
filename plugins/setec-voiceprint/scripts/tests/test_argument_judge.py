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
    # the B5 arc fields default to None when the entry omits them (additive).
    assert [{"role": c["role"], "mode": c["mode"]} for c in cleaned] == [
        {"role": "thesis", "mode": "argumentation"},
        {"role": "support", "mode": "exposition"},
        {"role": "proposal", "mode": "argumentation"},
    ]
    assert all(
        c["guard_strength"] is None and c["claim_ref"] is None
        and c["objection_strength"] is None
        for c in cleaned
    )


_NULL_LABEL = {
    "role": None, "mode": None, "guard_strength": None,
    "claim_ref": None, "objection_strength": None,
}


def test_validate_bad_role_and_mode_nulled_with_warnings():
    vals = {"paragraphs": [{"index": 0, "role": "BOGUS", "mode": "alsobad"}]}
    cleaned, warns = j.validate_labels(vals, n_paragraphs=1)
    assert cleaned[0] == _NULL_LABEL
    assert any("role 'BOGUS'" in w for w in warns)
    assert any("mode 'alsobad'" in w for w in warns)


def test_validate_missing_paragraphs_flagged():
    vals = {"paragraphs": [{"index": 0, "role": "thesis", "mode": "argumentation"}]}
    cleaned, warns = j.validate_labels(vals, n_paragraphs=3)
    assert cleaned[1] == _NULL_LABEL
    assert cleaned[2] == _NULL_LABEL
    assert any("missing indices" in w for w in warns)


def test_validate_non_list_all_null():
    cleaned, warns = j.validate_labels({"paragraphs": "nope"}, n_paragraphs=2)
    assert cleaned == [_NULL_LABEL, _NULL_LABEL]
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


# ---- B5 arc fields (guard_strength / claim_ref / objection_strength) ------
def test_mock_judge_emits_deterministic_b5_fields():
    seq = j.build_judge("mock")(["a", "b", "c"]).values
    paras = seq["paragraphs"]
    # guard drops strong (para 0) -> weak (later); a single shared claim_ref;
    # no objection role so objection_strength + doc-level field are null.
    assert paras[0]["guard_strength"] == "strong"
    assert paras[1]["guard_strength"] == "weak"
    assert all(p["claim_ref"] == "c0" for p in paras)
    assert all(p["objection_strength"] is None for p in paras)
    assert seq["strongest_internal_objection_engaged"] is None


def test_validate_b5_fields_good_values():
    vals = {"paragraphs": [
        {"index": 0, "role": "thesis", "mode": "argumentation",
         "guard_strength": "strong", "claim_ref": "c1"},
        {"index": 1, "role": "counterclaim", "mode": "argumentation",
         "guard_strength": "none", "claim_ref": "c1", "objection_strength": "weak"},
    ]}
    cleaned, warns = j.validate_labels(vals, n_paragraphs=2)
    assert warns == []
    assert cleaned[0]["guard_strength"] == "strong" and cleaned[0]["claim_ref"] == "c1"
    assert cleaned[1]["objection_strength"] == "weak"


def test_validate_b5_out_of_vocab_nulled_with_warning():
    vals = {"paragraphs": [{
        "index": 0, "role": "rebuttal", "mode": "argumentation",
        "guard_strength": "ultra", "objection_strength": "meh", "claim_ref": "  ",
    }]}
    cleaned, warns = j.validate_labels(vals, n_paragraphs=1)
    assert cleaned[0]["guard_strength"] is None
    assert cleaned[0]["objection_strength"] is None
    assert cleaned[0]["claim_ref"] is None  # whitespace-only is not a real id
    assert any("guard_strength 'ultra'" in w for w in warns)
    assert any("objection_strength 'meh'" in w for w in warns)
    assert any("claim_ref" in w for w in warns)


def test_validate_b5_explicit_null_is_legal():
    vals = {"paragraphs": [{
        "index": 0, "role": "support", "mode": "argumentation",
        "guard_strength": None, "claim_ref": None, "objection_strength": None,
    }]}
    cleaned, warns = j.validate_labels(vals, n_paragraphs=1)
    assert warns == []
    assert cleaned[0]["guard_strength"] is None


def test_validate_b5_missing_fields_tolerated_legacy_manifest():
    # A pre-extension manifest entry has no B5 keys — must load with the fields
    # defaulting to None and NO warnings (back-compat).
    vals = {"paragraphs": [{"index": 0, "role": "thesis", "mode": "argumentation"}]}
    cleaned, warns = j.validate_labels(vals, n_paragraphs=1)
    assert warns == []
    assert cleaned[0]["guard_strength"] is None
    assert cleaned[0]["claim_ref"] is None
    assert cleaned[0]["objection_strength"] is None


def test_validate_doc_level_field():
    assert j.validate_doc_level({"strongest_internal_objection_engaged": True}) == (True, [])
    assert j.validate_doc_level({"strongest_internal_objection_engaged": False}) == (False, [])
    assert j.validate_doc_level({"strongest_internal_objection_engaged": None}) == (None, [])
    # missing -> None, no warning (legacy manifest)
    assert j.validate_doc_level({}) == (None, [])
    # non-bool (incl. int 1, since bool is an int subclass) -> None + warning
    v, w = j.validate_doc_level({"strongest_internal_objection_engaged": 1})
    assert v is None and any("not a boolean" in m for m in w)


def test_to_dict_carries_doc_level_field():
    res = j.build_judge("mock")(["a", "b"])
    d = res.to_dict()
    assert "strongest_internal_objection_engaged" in d["values"]


def test_prompt_introduces_b5_fields():
    prompt = j.render_prompt()
    for token in ("guard_strength", "claim_ref", "objection_strength",
                  "strongest_internal_objection_engaged"):
        assert token in prompt
    # the null-on-uncertainty discipline must be in the preamble or prompt.
    assert "null" in prompt.lower()


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
    assert cleaned == [_NULL_LABEL, _NULL_LABEL]
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
