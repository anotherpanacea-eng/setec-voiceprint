#!/usr/bin/env python3
"""Tests for argument_certainty_calibration — the per-claim certainty-calibration
surface, its MECHANICAL no-verdict firewall, the deterministic certainty lexicon,
and the evidence-gated legitimate-strong-claim filter.

The firewall is the entire defensibility of this capability, so it is tested
adversarially: an artifact carrying ANY forbidden certainty-verdict key must make
``assert_no_verdict`` RAISE -> ``policy_refused``. Filter-integrity is mechanical:
an ``overclaim`` (or any ``defended_*``) row with an empty rationale is a BUILD
ERROR, and a FABRICATED ``defended_elsewhere`` supporting locus is a BUILD ERROR
(it does not validate against the document).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_ROOT))

import argument_certainty_calibration as acc  # noqa: E402
import argument_certainty_judge as cjudge  # noqa: E402
import argument_certainty_calibration_schema as schema  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "argument_calibration"


# --------------------------------------------------------------------------
# (a) Adversarial firewall: assert_no_verdict RAISES on EACH forbidden key.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("forbidden_key", sorted(acc.FORBIDDEN_RESULT_KEYS))
def test_assert_no_verdict_raises_on_each_forbidden_key(forbidden_key):
    """An artifact carrying ANY forbidden certainty-verdict key (at any depth)
    raises -> policy_refused."""
    poisoned = {
        "claims": [],
        "summary": {},
        "nested": {"deeper": {forbidden_key: "smuggled"}},
    }
    with pytest.raises(acc.CalibrationVerdictError):
        acc.assert_no_verdict(poisoned)


@pytest.mark.parametrize("forbidden_key", sorted(acc.FORBIDDEN_RESULT_KEYS))
def test_assert_no_verdict_raises_on_forbidden_value(forbidden_key):
    """A forbidden key rendered as a string VALUE (not a key) also raises."""
    poisoned = {"results": {"some_label": forbidden_key}}
    with pytest.raises(acc.CalibrationVerdictError):
        acc.assert_no_verdict(poisoned)


def test_assert_no_verdict_raises_on_substring_key():
    """A KEY containing a forbidden certainty-verdict substring (e.g. a per-author
    'overconfidence_index') raises even though the exact key is not in the
    frozenset."""
    with pytest.raises(acc.CalibrationVerdictError):
        acc.assert_no_verdict({"overconfidence_index": 0.9})
    with pytest.raises(acc.CalibrationVerdictError):
        acc.assert_no_verdict({"author_calibration_score": 0.4})
    with pytest.raises(acc.CalibrationVerdictError):
        acc.assert_no_verdict({"arrogance_flag": True})


def test_assert_no_verdict_passes_on_honest_envelope():
    """The surface's own honest does_not_license / rationale prose — which
    legitimately contains 'overclaim' and may say 'the author stipulated this' —
    must NOT raise (the substrings are certainty-scoped, not the authorship set,
    and the substring walk is KEY-ONLY)."""
    clean = {
        "claims": [
            {
                "alignment": "overclaim",
                "rationale": "high certainty is NOT arrogance; this is a mismatch, "
                             "not a judgment of the author",
                "defense": "none",
            }
        ],
        "does_not_license": acc._DOES_NOT_LICENSE,
    }
    acc.assert_no_verdict(clean)  # does not raise


# --------------------------------------------------------------------------
# (b) The deterministic certainty lexicon (the M1 substrate).
# --------------------------------------------------------------------------

def test_certainty_booster_is_assertive():
    assert acc.classify_certainty("This clearly proves the point.") == "assertive"


def test_certainty_hedge_is_tentative():
    assert acc.classify_certainty("This might perhaps lower rents.") == "tentative"


def test_certainty_mixed_is_measured():
    # booster + hedge in the same claim -> measured
    assert acc.classify_certainty("This clearly might be the case.") == "measured"


def test_certainty_bare_assertion_is_assertive():
    # No marker at all: a flat unmarked assertion expresses high certainty.
    assert acc.classify_certainty("The discount rate is three percent.") == "assertive"


def test_certainty_no_bare_may_false_positive():
    """'may' must NOT be a bare hedge token (the month / proper-noun false
    positive the spec calls out). A sentence with 'May' as a month is a bare
    assertion, not tentative."""
    assert acc.classify_certainty("The report was filed in May of that year.") == "assertive"
    # but the multi-word 'may suggest' IS a hedge
    assert acc.classify_certainty("The data may suggest a small effect.") == "tentative"


# --------------------------------------------------------------------------
# (c) Alignment pairing.
# --------------------------------------------------------------------------

def test_alignment_overclaim():
    assert acc.classify_alignment("assertive", "none") == "overclaim"


def test_alignment_underclaim():
    assert acc.classify_alignment("tentative", "substantiated") == "underclaim"


def test_alignment_aligned():
    assert acc.classify_alignment("assertive", "substantiated") == "aligned"
    assert acc.classify_alignment("measured", "gestured") == "aligned"


# --------------------------------------------------------------------------
# (d) End-to-end on the mock judge — the worked fixture.
# --------------------------------------------------------------------------

def _run_mock(target: Path, support_loci_path: str | None = None,
              length_floor_words: int = 10) -> dict:
    return acc.compose_envelope(
        target, judge_kind="mock", support_loci_path=support_loci_path,
        length_floor_words=length_floor_words,
    )


def test_end_to_end_worked_fixture_on_mock():
    # The worked fixture clears the real default length floor (50 words).
    env = _run_mock(FIXTURES / "worked_example.txt", length_floor_words=50)
    assert env["available"] is True
    assert env["task_surface"] == "argument_calibration"
    results = env["results"]
    assert results["calibration_status"] == "heuristic"
    # No top-level score key anywhere.
    assert "overconfidence_score" not in json.dumps(results)
    by_topic = {r["topic_ref"]: r for r in results["claims"]}

    # 1. clearly/without question/every + support=none -> overclaim (no defense).
    assert by_topic["t_min_wage"]["certainty"] == "assertive"
    assert by_topic["t_min_wage"]["alignment"] == "overclaim"
    assert by_topic["t_min_wage"]["defense"] == "none"
    assert by_topic["t_min_wage"]["rationale"].strip()  # non-empty
    assert by_topic["t_min_wage"]["resolution_class"] == "hedge_to_match"

    # 2. 'seems possible'/'might' (hedge) + support=substantiated -> underclaim.
    assert by_topic["t_housing"]["certainty"] == "tentative"
    assert by_topic["t_housing"]["alignment"] == "underclaim"

    # 3. bare assertion + support=substantiated -> aligned.
    assert by_topic["t_transit"]["certainty"] == "assertive"
    assert by_topic["t_transit"]["alignment"] == "aligned"

    # 4. 'for the sake of argument, assume ...' + support=none -> defended_stipulated
    #    (re-labeled aligned, NOT overclaim).
    assert by_topic["t_premise"]["defense"] == "defended_stipulated"
    assert by_topic["t_premise"]["alignment"] == "aligned"
    assert by_topic["t_premise"]["resolution_class"] == "mark_stipulation"
    assert by_topic["t_premise"]["rationale"].strip()

    # 5. obviously/always/every + support=none -> overclaim.
    assert by_topic["t_carbon"]["alignment"] == "overclaim"

    # Summary counts are coherent.
    summ = results["summary"]
    assert summ["n_claims"] == 5
    assert summ["n_overclaim"] == 2
    assert summ["n_underclaim"] == 1


def test_flat_assertive_none_is_overclaim(tmp_path):
    doc = tmp_path / "flat.txt"
    doc.write_text(
        "[[claim support=none topic=t1]] The policy obviously fails, always, in every "
        "single case, and no one can deny it on the present record.\n"
        "[[claim support=none topic=t2]] The second measure undeniably collapses the "
        "market without question across the board, full stop, every time it is tried.\n",
        encoding="utf-8",
    )
    env = _run_mock(doc)
    results = env["results"]
    assert all(r["alignment"] == "overclaim" for r in results["claims"])
    assert all(r["defense"] == "none" for r in results["claims"])
    assert all(r["rationale"].strip() for r in results["claims"])


def test_tentative_substantiated_is_underclaim(tmp_path):
    doc = tmp_path / "under.txt"
    doc.write_text(
        "[[claim support=substantiated topic=t1]] It might perhaps be that the program "
        "helps, and the randomized trial in the appendix reports a clear, measured gain.\n"
        "[[claim support=substantiated topic=t2]] Arguably the second effect could hold, "
        "and three independent replications cited above each confirm the same direction.\n",
        encoding="utf-8",
    )
    env = _run_mock(doc)
    results = env["results"]
    assert all(r["certainty"] == "tentative" for r in results["claims"])
    assert all(r["alignment"] == "underclaim" for r in results["claims"])


# --------------------------------------------------------------------------
# (e) defended_stipulated — an assertive×none claim with a stipulation marker.
# --------------------------------------------------------------------------

def test_defended_stipulated_is_not_overclaim(tmp_path):
    doc = tmp_path / "stip.txt"
    doc.write_text(
        "[[claim support=none topic=t1]] Assume for now that the discount rate is fixed "
        "at three percent for the entire horizon under consideration here.\n",
        encoding="utf-8",
    )
    env = _run_mock(doc)
    row = env["results"]["claims"][0]
    # Bare assertion (no hedge/booster) -> assertive; support=none would be
    # overclaim, but the stipulation marker fires defended_stipulated -> aligned.
    assert row["certainty"] == "assertive"
    assert row["defense"] == "defended_stipulated"
    assert row["alignment"] == "aligned"
    assert row["rationale"].strip()


# --------------------------------------------------------------------------
# (f) defended_elsewhere — a REAL in-document supporting locus validates;
#     a FABRICATED one FAILS validation -> build error.
# --------------------------------------------------------------------------

def _overclaim_doc_with_support(tmp_path) -> tuple[Path, str, dict]:
    """A doc with an assertive×none overclaim on topic t_x, plus a real
    supporting paragraph elsewhere whose exact offsets we compute from the text.
    Returns (path, text, {valid_locus, fabricated_locus})."""
    support_sentence = "Three randomized trials in the appendix each found a measurable drop in rents."
    text = (
        "[[claim support=none topic=t_x]] Zoning reform clearly works, without question.\n\n"
        + support_sentence + "\n"
    )
    doc = tmp_path / "elsewhere.txt"
    doc.write_text(text, encoding="utf-8")
    start = text.index(support_sentence)
    end = start + len(support_sentence)
    valid_locus = {"start_char": start, "end_char": end, "quote": support_sentence}
    # A fabricated locus: same offsets, but a quote that does NOT match the text.
    fabricated_locus = {
        "start_char": start, "end_char": end,
        "quote": "A totally different sentence that is not actually in the document at all.",
    }
    return doc, text, {"valid": valid_locus, "fabricated": fabricated_locus}


def test_defended_elsewhere_real_locus_validates(tmp_path):
    doc, _text, loci = _overclaim_doc_with_support(tmp_path)
    sidecar = tmp_path / "support_loci.json"
    sidecar.write_text(
        json.dumps({"support_loci": {"t_x": [loci["valid"]]}}), encoding="utf-8"
    )
    env = _run_mock(doc, support_loci_path=str(sidecar))
    row = next(r for r in env["results"]["claims"] if r["topic_ref"] == "t_x")
    assert row["defense"] == "defended_elsewhere"
    assert row["alignment"] == "aligned"
    assert row["resolution_class"] == "surface_support_elsewhere"
    assert row["rationale"].strip()


def test_defended_elsewhere_fabricated_locus_is_build_error(tmp_path):
    doc, _text, loci = _overclaim_doc_with_support(tmp_path)
    sidecar = tmp_path / "support_loci_bad.json"
    sidecar.write_text(
        json.dumps({"support_loci": {"t_x": [loci["fabricated"]]}}), encoding="utf-8"
    )
    # The fabricated cross-reference must FAIL validation -> CalibrationLocusError.
    with pytest.raises(acc.CalibrationLocusError):
        acc.compose_envelope(doc, judge_kind="mock", support_loci_path=str(sidecar),
                             length_floor_words=10)


def test_validate_support_locus_directly():
    text = "alpha beta gamma delta"
    good = {"start_char": 6, "end_char": 10, "quote": "beta"}
    assert acc._validate_support_locus(text, good) == "beta"
    bad = {"start_char": 6, "end_char": 10, "quote": "ZETA"}
    with pytest.raises(acc.CalibrationLocusError):
        acc._validate_support_locus(text, bad)


# --------------------------------------------------------------------------
# (g) Schema filter-integrity: empty-rationale overclaim/defended_* is a build error;
#     M2-only defenses never fire on the M1 path.
# --------------------------------------------------------------------------

def _row(**over) -> dict:
    base = {
        "loci": {"start_char": 0, "end_char": 3, "quote": "abc"},
        "certainty": "assertive",
        "support": "none",
        "alignment": "overclaim",
        "defense": "none",
        "rationale": "non-empty rationale",
        "resolution_class": "hedge_to_match",
    }
    base.update(over)
    return base


def test_empty_rationale_overclaim_is_build_error():
    with pytest.raises(schema.SchemaError):
        schema.validate_claim_row(_row(rationale=""))


def test_empty_rationale_defended_is_build_error():
    with pytest.raises(schema.SchemaError):
        schema.validate_claim_row(
            _row(alignment="aligned", defense="defended_stipulated",
                 resolution_class="mark_stipulation", rationale="")
        )


def test_aligned_row_allows_empty_rationale():
    # An aligned, undefended row need not carry a rationale.
    schema.validate_claim_row(
        _row(alignment="aligned", defense="none",
             resolution_class="none", certainty="measured",
             support="gestured", rationale="")
    )


def test_m2_only_defense_rejected_on_m1_path():
    with pytest.raises(schema.SchemaError):
        schema.validate_claim_row(
            _row(alignment="aligned", defense="defended_analytic",
                 resolution_class="none", rationale="x"),
            m1=True,
        )
    # but allowed when m1=False (M2 path)
    schema.validate_claim_row(
        _row(alignment="aligned", defense="defended_analytic",
             resolution_class="none", rationale="x"),
        m1=False,
    )


def test_out_of_whitelist_enum_is_build_error():
    with pytest.raises(schema.SchemaError):
        schema.validate_claim_row(_row(certainty="overconfident"))
    with pytest.raises(schema.SchemaError):
        schema.validate_claim_row(_row(alignment="unsound"))


# --------------------------------------------------------------------------
# (h) Offset-exact claim spans: a fabricated CLAIM span is dropped (not trusted).
# --------------------------------------------------------------------------

def test_fabricated_claim_span_is_dropped():
    text = "Real sentence here for the record about policy."
    bad_claim = cjudge.Claim(
        topic_ref="t", statement="x", start_char=0, end_char=4,
        quote="WRONG",  # text[0:4] == "Real" != "WRONG"
        support="none",
    )
    rows = acc.build_claim_rows(text, [bad_claim], {})
    assert rows == []  # dropped, never trusted


def test_mock_judge_spans_are_offset_exact():
    text = (FIXTURES / "worked_example.txt").read_text(encoding="utf-8")
    claims = cjudge._mock_extract(text)
    assert claims  # extracted something
    for c in claims:
        assert text[c.start_char:c.end_char] == c.quote
