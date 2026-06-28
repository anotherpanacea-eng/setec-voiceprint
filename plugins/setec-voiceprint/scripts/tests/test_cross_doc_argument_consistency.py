#!/usr/bin/env python3
"""Tests for cross_doc_argument_consistency — the cross-document argument-consistency
surface, its MECHANICAL no-verdict firewall, and the legitimate-variation filter.

The firewall is the defensibility of this capability, so it is tested adversarially:
an artifact carrying ANY forbidden verdict/character/score key must make
``assert_no_verdict`` RAISE -> ``policy_refused``. Filter-integrity is mechanical:
a ``defended_*`` (or ``genuine``) tension with an empty rationale is a BUILD ERROR.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_ROOT))

import cross_doc_argument_consistency as cdac  # noqa: E402
import cross_doc_consistency_judge as cjudge  # noqa: E402
import cross_doc_consistency_schema as schema  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "argument_consistency"


# --------------------------------------------------------------------------
# (a) Adversarial firewall: assert_no_verdict RAISES on EACH forbidden key.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("forbidden_key", sorted(cdac.FORBIDDEN_RESULT_KEYS))
def test_assert_no_verdict_raises_on_each_forbidden_key(forbidden_key):
    """An artifact carrying ANY forbidden verdict key (at any depth) raises."""
    poisoned = {
        "tensions": [],
        "summary": {},
        "nested": {"deeper": {forbidden_key: "smuggled"}},
    }
    with pytest.raises(cdac.ConsistencyVerdictError):
        cdac.assert_no_verdict(poisoned)


@pytest.mark.parametrize("forbidden_key", sorted(cdac.FORBIDDEN_RESULT_KEYS))
def test_assert_no_verdict_raises_on_forbidden_value(forbidden_key):
    """A forbidden key rendered as a string VALUE (not a key) also raises."""
    poisoned = {"results": {"some_label": forbidden_key}}
    with pytest.raises(cdac.ConsistencyVerdictError):
        cdac.assert_no_verdict(poisoned)


def test_assert_no_verdict_raises_on_substring_key():
    """A KEY containing a forbidden substring (e.g. a per-author 'hypocrisy_index')
    raises even though the exact key is not in the frozenset."""
    with pytest.raises(cdac.ConsistencyVerdictError):
        cdac.assert_no_verdict({"hypocrisy_index": 0.9})
    with pytest.raises(cdac.ConsistencyVerdictError):
        cdac.assert_no_verdict({"per_author_consistency_score": 0.4})


def test_assert_no_verdict_raises_on_out_of_whitelist_severity():
    """A smuggled non-whitelist severity (an author judgment dressed as an ordinal)
    raises — the severity vocabulary is whitelist-enforced."""
    with pytest.raises(cdac.ConsistencyVerdictError):
        cdac.assert_no_verdict({"tensions": [{"severity": "Damning"}]})


def test_assert_no_verdict_raises_on_non_tension_relation():
    """A 'consistent'/'incomparable' row must never reach the ledger; a relation
    value outside the tension set raises."""
    with pytest.raises(cdac.ConsistencyVerdictError):
        cdac.assert_no_verdict({"tensions": [{"relation": "consistent"}]})


def test_assert_no_verdict_passes_clean_results():
    """The happy-path results (including honest does_not_license prose that names
    'hypocrisy' in a VALUE) passes — the substring walk is KEY-ONLY."""
    clean = {
        "tensions": [{
            "relation": "tension", "severity": "Notable",
            "legitimate_variation": "genuine",
            "rationale": "a real tension",
            "does_not_license": "this is NOT a finding of hypocrisy or bad faith",
        }],
        "summary": {"n_tensions": 1},
        "does_not_license": "consistency is a property of a text pair, not the author's honesty",
    }
    cdac.assert_no_verdict(clean)  # must not raise


def test_main_routes_verdict_error_to_policy_refused(monkeypatch, tmp_path):
    """End-to-end: if compose_envelope raises ConsistencyVerdictError, main()
    emits an available:false / policy_refused envelope and exits 3."""
    # Monkeypatch compose_results to smuggle a forbidden key past the builders.
    real = cdac.compose_results

    def poisoned(*args, **kwargs):
        r = real(*args, **kwargs)
        r["author_verdict"] = "hypocrite"  # forbidden key
        return r

    monkeypatch.setattr(cdac, "compose_results", poisoned)
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    (pool_dir / "p.txt").write_text((FIXTURES / "pool_genuine.txt").read_text(), encoding="utf-8")
    out = tmp_path / "out.json"
    rc = cdac.main([
        "--focal", str(FIXTURES / "focal.txt"),
        "--reference-dir", str(pool_dir), "--judge", "mock", "--out", str(out),
    ])
    assert rc == 3
    env = json.loads(out.read_text())
    assert env["available"] is False
    assert env["reason_category"] == "policy_refused"


# --------------------------------------------------------------------------
# (b) Legitimate-variation: a time-evolved position is defended_time.
# --------------------------------------------------------------------------

def test_defended_time_fixture(tmp_path):
    """The carbon-tax pair (focal pro vs a dated/hindsight-marked 2014 reversal)
    is reported defended_time, not genuine."""
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    (pool_dir / "old.txt").write_text(
        (FIXTURES / "pool_defended_time.txt").read_text(), encoding="utf-8")
    env = cdac.compose_envelope(
        FIXTURES / "focal.txt",
        cdac._load_reference_dir(pool_dir),
        judge_kind="mock",
    )
    assert env["available"] is True
    tensions = env["results"]["tensions"]
    carbon = [t for t in tensions if t["topic_ref"] == "t_carbon_tax"]
    assert len(carbon) == 1
    assert carbon[0]["legitimate_variation"] == "defended_time"
    assert carbon[0]["rationale"].strip()  # non-empty rationale required


def test_legitimate_variation_precedence_retraction_beats_time():
    """DEFENSE_ORDER precedence: a blob carrying BOTH a retraction marker and a
    temporal marker fires retraction (it comes first), not time."""
    blob = "I retract this claim. Since then the evidence has shifted, in 2018 especially."
    lv, rationale = cdac.classify_legitimate_variation(blob)
    assert lv == "defended_retraction"
    assert "retraction" in rationale


def test_legitimate_variation_genuine_when_no_marker():
    """No defense marker -> genuine, with a non-empty 'why genuine' rationale."""
    blob = "Public transit must be subsidized. Public transit must not be subsidized."
    lv, rationale = cdac.classify_legitimate_variation(blob)
    assert lv == "genuine"
    assert rationale.strip()


@pytest.mark.parametrize("marker,expected", [
    ("in the case of rural districts only", "defended_scope"),
    ("for specialists, as I told the committee", "defended_audience"),
    ("strictly speaking, in the formal brief", "defended_genre"),
])
def test_legitimate_variation_each_defense_fires(marker, expected):
    lv, _ = cdac.classify_legitimate_variation(marker)
    assert lv == expected


# --------------------------------------------------------------------------
# (c) One genuine + one defended tension end-to-end on the MOCK judge.
# --------------------------------------------------------------------------

def test_end_to_end_one_genuine_one_defended(tmp_path):
    """Focal vs a pool of {genuine-reversal, time-defended-reversal} yields exactly
    one genuine and one defended_time tension, both present in the ledger."""
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    for name in ("pool_genuine.txt", "pool_defended_time.txt"):
        (pool_dir / name).write_text((FIXTURES / name).read_text(), encoding="utf-8")
    env = cdac.compose_envelope(
        FIXTURES / "focal.txt", cdac._load_reference_dir(pool_dir), judge_kind="mock",
    )
    assert env["available"] is True
    r = env["results"]
    assert r["summary"]["n_tensions"] == 2
    assert r["summary"]["n_genuine"] == 1
    assert r["summary"]["n_defended"] == 1
    lvs = {t["topic_ref"]: t["legitimate_variation"] for t in r["tensions"]}
    assert lvs["t_transit_subsidy"] == "genuine"
    assert lvs["t_carbon_tax"] == "defended_time"
    # No top-level score anywhere; calibration heuristic.
    assert "consistency_score" not in r
    assert "author_score" not in r
    assert r["calibration_status"] == "heuristic"
    assert env["claim_license"] is not None


def test_end_to_end_envelope_passes_firewall(tmp_path):
    """The full happy-path envelope's results pass assert_no_verdict (no smuggled
    verdict key in the real output)."""
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    (pool_dir / "g.txt").write_text((FIXTURES / "pool_genuine.txt").read_text(), encoding="utf-8")
    env = cdac.compose_envelope(
        FIXTURES / "focal.txt", cdac._load_reference_dir(pool_dir), judge_kind="mock",
    )
    cdac.assert_no_verdict(env["results"])  # must not raise
    # And the docs-not-license names hypocrisy in a VALUE without tripping the guard.
    assert "hypocrisy" not in env["results"]  # not a key
    assert "honest" in env["results"]["does_not_license"].lower()


# --------------------------------------------------------------------------
# (d) Empty-rationale-defended = BUILD ERROR (mechanical filter-integrity).
# --------------------------------------------------------------------------

def _row(**over):
    base = {
        "loci": [
            {"doc": "a.txt", "start_char": 0, "end_char": 5, "quote": "alpha"},
            {"doc": "b.txt", "start_char": 0, "end_char": 4, "quote": "beta"},
        ],
        "topic_ref": "t1",
        "relation": "tension",
        "legitimate_variation": "defended_time",
        "rationale": "a real defense rationale",
        "severity": "Notable",
        "resolution_class": "name the scope",
    }
    base.update(over)
    return base


def test_empty_rationale_defended_is_build_error():
    """A defended_* row whose rationale is empty must RAISE (SchemaError), never
    pass silently — this is the mechanical filter-integrity guarantee."""
    with pytest.raises(schema.SchemaError):
        schema.validate_tension_row(_row(rationale=""))
    with pytest.raises(schema.SchemaError):
        schema.validate_tension_row(_row(rationale="   "))


def test_empty_rationale_genuine_is_also_build_error():
    with pytest.raises(schema.SchemaError):
        schema.validate_tension_row(_row(legitimate_variation="genuine", rationale=""))


def test_validate_results_raises_on_unjustified_defended_row():
    """The surface-level validate_results raises if ANY tension row is
    under-justified — an unfiltered/unjustified tension is a build error."""
    results = {
        "tensions": [_row(rationale="")],
        "summary": {},
        "assumptions": {},
        "does_not_license": "x",
    }
    with pytest.raises(schema.SchemaError):
        schema.validate_results(results)


def test_validate_tension_row_rejects_non_tension_relation():
    with pytest.raises(schema.SchemaError):
        schema.validate_tension_row(_row(relation="consistent"))


def test_validate_tension_row_rejects_out_of_whitelist_severity():
    with pytest.raises(schema.SchemaError):
        schema.validate_tension_row(_row(severity="Damning"))


def test_validate_tension_row_rejects_bad_legitimate_variation():
    with pytest.raises(schema.SchemaError):
        schema.validate_tension_row(_row(legitimate_variation="defended_vibes"))


def test_validate_tension_row_accepts_well_formed_row():
    schema.validate_tension_row(_row())  # must not raise


def test_validate_tension_row_requires_two_loci():
    with pytest.raises(schema.SchemaError):
        schema.validate_tension_row(_row(loci=[{"doc": "a", "start_char": 0, "end_char": 1, "quote": "x"}]))


# --------------------------------------------------------------------------
# Judge contract: mock determinism + extraction.
# --------------------------------------------------------------------------

def test_mock_judge_deterministic_and_marker_driven():
    mock = cjudge.build_judge("mock")
    text = "Lead. [[topic=t1 type=claim stance=for]] The claim sentence here."
    r1 = mock("docA", text)
    r2 = mock("docA", text)
    assert [c.locus() for c in r1.commitments] == [c.locus() for c in r2.commitments]
    assert len(r1.commitments) == 1
    c = r1.commitments[0]
    assert c.topic_ref == "t1" and c.ctype == "claim" and c.stance == "for"
    # the quoted span is verbatim and offset-accurate
    assert text[c.start_char:c.end_char] == c.quote
    assert r1.judge_identity["kind"] == "mock"


def test_detect_tension_polarity_rules():
    def mk(stance, topic="t1"):
        return cjudge.Commitment("d", topic, "claim", "s", 0, 1, "q", stance)
    assert cjudge.detect_tension(mk("for"), mk("against")) == "direct_conflict"
    assert cjudge.detect_tension(mk("for"), mk("for")) == "consistent"
    assert cjudge.detect_tension(mk("for"), mk(None)) == "tension"
    assert cjudge.detect_tension(mk("for"), mk("against", topic="t2")) == "incomparable"


def test_validate_commitments_drops_bad_entries():
    payload = {"commitments": [
        {"topic_ref": "t1", "type": "claim", "statement": "s", "start_char": 0,
         "end_char": 3, "quote": "abc", "stance": "for"},
        {"topic_ref": "t1", "type": "BOGUS", "statement": "s", "start_char": 0,
         "end_char": 3, "quote": "abc"},  # bad type -> dropped
        {"topic_ref": "t1", "type": "claim", "statement": "s", "start_char": 0,
         "end_char": 99, "quote": "abc"},  # span out of range -> dropped
    ]}
    cleaned, warns = cjudge.validate_commitments(payload, doc="d", text_len=10)
    assert len(cleaned) == 1
    assert len(warns) == 2


def test_api_judge_requires_model():
    with pytest.raises(cjudge.JudgeError):
        cjudge.build_judge("anthropic")  # no model -> fail loud


def test_manifest_judge_round_trip(tmp_path):
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({
        "docs": {"d.txt": {"commitments": [
            {"topic_ref": "t1", "type": "claim", "statement": "s", "start_char": 0,
             "end_char": 3, "quote": "abc", "stance": "for"},
        ]}},
        "judge_identity": {"model": "gpt-X"},
    }), encoding="utf-8")
    judge = cjudge.build_judge("manifest", manifest_path=manifest)
    r = judge("d.txt", "abc defghij")
    assert len(r.commitments) == 1
    assert r.judge_identity["model"] == "gpt-X"


# --------------------------------------------------------------------------
# Intake: self-exclusion + min-docs.
# --------------------------------------------------------------------------

def test_self_exclusion_drops_focal_copy_in_pool(tmp_path):
    """A pool entry that is a content-copy of the focal is self-excluded; with no
    other usable doc, the surface abstains (bad_input)."""
    focal = FIXTURES / "focal.txt"
    pool = [("dup", focal.read_text(), None)]  # inline copy of focal
    env = cdac.compose_envelope(focal, pool, judge_kind="mock")
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"


def test_below_floor_focal_abstains(tmp_path):
    focal = tmp_path / "tiny.txt"
    focal.write_text("Too short.", encoding="utf-8")
    pool = [("p", (FIXTURES / "pool_genuine.txt").read_text(), None)]
    env = cdac.compose_envelope(focal, pool, judge_kind="mock")
    assert env["available"] is False
    assert env["reason_category"] == "text_too_short"
