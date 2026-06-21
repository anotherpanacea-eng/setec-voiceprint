#!/usr/bin/env python3
"""Tests for the eval-discipline topic-leakage split + Simpson check (spec 28).

All on PRE-SCORED synthetic records — no model loaded, stdlib only. Roots:
Topic Confusion Task (arXiv:2104.08530), HITS (arXiv:2407.19164),
Log-Likelihood & Simpson's Paradox (arXiv:2605.06294).
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validation_harness as vh  # type: ignore  # noqa: E402
import manifest_validator as mv  # type: ignore  # noqa: E402


def _rec(topic, label, score, **extra):
    r = {
        "topic": topic,
        "label": label,
        "score": score,
        "usable_for_metrics": True,
    }
    r.update(extra)
    return r


# ---- Acceptance #1: topic field + manifest ----------------------------------


def test_topic_is_a_known_field():
    assert "topic" in mv.KNOWN_FIELDS


def _validate(entry, tmp_path):
    """Run validate_entry with a real (existing) target path so the only
    findings are about the schema fields under test, not a missing file."""
    target = tmp_path / "x.txt"
    target.write_text("hello world\n", encoding="utf-8")
    entry = dict(entry)
    entry["path"] = "x.txt"
    return mv.validate_entry(
        entry,
        lineno=1,
        manifest_path=tmp_path / "manifest.jsonl",
        seen_ids=set(),
        seen_paths={},
    )


def test_topic_and_topic_match_coexist_and_are_distinct(tmp_path):
    """A record with both `topic` and `topic_match` validates (no error) and
    `topic` does not trip an unknown-field warning."""
    entry = {
        "id": "e1",
        "ai_status": "ai_generated",
        "use": ["validation"],
        "topic": "monetary_policy",
        "topic_match": "high",
    }
    issues = _validate(entry, tmp_path)
    errs = [i for i in issues if i.severity == "error"]
    assert errs == [], [i.message for i in errs]
    # `topic` is a KNOWN field -> no unknown-field warning for it.
    topic_unknown = [
        i for i in issues
        if i.field == "topic" and "Unknown field" in (i.message or "")
    ]
    assert topic_unknown == []


def test_topic_survives_from_entry_to_scored_record():
    """The scored-record shaper must carry `topic` through (else the split
    buckets everything 'unknown'). Distinct from `topic_match`."""
    entry = {
        "id": "e1",
        "ai_status": "ai_generated",
        "topic": "sports",
        "topic_match": "low",
        "_resolved_path": __file__,  # real readable file
        "_lineno": 1,
    }
    rec = vh.score_smoothing_entry(
        entry,
        positive_statuses={"ai_generated"},
        negative_statuses={"pre_ai_human"},
    )
    assert rec["topic"] == "sports"
    assert rec["topic_match"] == "low"


def test_topic_unknown_value_is_not_an_error(tmp_path):
    """topic is open-set (free text, like a tag): an odd value is fine."""
    entry = {
        "id": "e1",
        "ai_status": "pre_ai_human",
        "use": ["validation"],
        "topic": "some_brand_new_topic_label_42",
    }
    issues = _validate(entry, tmp_path)
    errs = [i for i in issues if i.severity == "error" and i.field == "topic"]
    assert errs == []
    warns = [i for i in issues if i.field == "topic"]
    assert warns == []  # open-set: not even a warning on an odd value


# ---- Acceptance #2: topic-disjoint split ------------------------------------


def test_topic_disjoint_split_no_topic_on_both_sides():
    recs = [_rec(t, i % 2, float(i)) for i, t in enumerate(
        ["a", "b", "c", "d", "a", "b", "c", "d"])]
    split = vh.topic_disjoint_split(recs, seed=0)
    assert split["available"] is True
    assert set(split["test_topics"]).isdisjoint(set(split["train_topics"]))
    assert split["topics_disjoint"] is True


def test_topic_disjoint_split_deterministic_under_seed():
    recs = [_rec(t, i % 2, float(i)) for i, t in enumerate(
        ["a", "b", "c", "d", "e", "f"])]
    s1 = vh.topic_disjoint_split(recs, seed=7)
    s2 = vh.topic_disjoint_split(recs, seed=7)
    assert s1["test_topics"] == s2["test_topics"]
    assert s1["train_topics"] == s2["train_topics"]


def test_topic_disjoint_split_reports_balance_and_topics():
    recs = [_rec("a", 1, 1.0), _rec("a", 0, 0.0), _rec("b", 1, 1.0), _rec("b", 0, 0.0)]
    split = vh.topic_disjoint_split(recs, seed=0)
    assert "test_balance" in split and "train_balance" in split
    assert set(split["test_topics"]) | set(split["train_topics"]) == {"a", "b"}


def test_topic_disjoint_split_unavailable_with_one_topic():
    recs = [_rec("only", i % 2, float(i)) for i in range(6)]
    split = vh.topic_disjoint_split(recs, seed=0)
    assert split["available"] is False
    assert "at least two distinct" in split["reason"]
    assert split["n_distinct_topics"] == 1


# ---- Acceptance #3: leakage diagnostic + AUC gap ----------------------------


def _leaked_corpus():
    """Score purely topic-determined; AI-fraction rises with the topic band.
    Pooled AUC separates (via topic), within-topic AUC ~0.5."""
    recs = []
    for ti, topic in enumerate(["t0", "t1", "t2", "t3"]):
        band = float(ti * 10)
        n_pos, n_neg = ti + 1, 4 - ti + 1
        recs += [_rec(topic, 1, band) for _ in range(n_pos)]
        recs += [_rec(topic, 0, band) for _ in range(n_neg)]
    return recs


def _independent_corpus():
    """Within each topic the score separates label identically; topic is
    independent of label."""
    recs = []
    for topic in ["a", "b", "c"]:
        recs += [_rec(topic, 1, 1.0) for _ in range(5)]
        recs += [_rec(topic, 0, 0.0) for _ in range(5)]
    return recs


def test_leakage_diagnostic_positive_gap_when_topic_correlates():
    d = vh.topic_leakage_diagnostic(_leaked_corpus(), seed=3, resamples=400)
    assert d["available"] is True
    assert d["pooled_roc_auc"] > d["split_roc_auc"]
    assert d["auc_gap"] > 0.0
    assert d["auc_gap_ci"]["available"] is True


def test_leakage_diagnostic_zero_gap_when_independent():
    d = vh.topic_leakage_diagnostic(_independent_corpus(), seed=3, resamples=400)
    assert d["available"] is True
    assert abs(d["auc_gap"]) < 1e-9


def test_leakage_diagnostic_emits_no_verdict_string():
    d = vh.topic_leakage_diagnostic(_leaked_corpus(), seed=3, resamples=100)
    # Shape: two AUCs, a gap, a confound caveat. No verdict / corrected number.
    assert set(["pooled_roc_auc", "split_roc_auc", "auc_gap", "caveat"]).issubset(d)
    assert "corrected" not in str(d).lower() or "no corrected" in d["caveat"].lower()
    blob = (d["caveat"]).lower()
    for forbidden in ("this text is ai", "reliably detect", "ai-detectable"):
        assert forbidden not in blob


def test_leakage_diagnostic_per_topic_balance_present():
    d = vh.topic_leakage_diagnostic(_leaked_corpus(), seed=3, resamples=50)
    assert set(d["topic_class_balance"].keys()) == {"t0", "t1", "t2", "t3"}
    for bal in d["topic_class_balance"].values():
        assert {"n", "n_positive", "n_negative"}.issubset(bal)


def test_leakage_diagnostic_unavailable_single_topic():
    recs = [_rec("only", i % 2, float(i)) for i in range(6)]
    d = vh.topic_leakage_diagnostic(recs, seed=0, resamples=50)
    assert d["available"] is False
    assert d["auc_gap"] is None


# ---- Acceptance #6: Simpson inversion check ---------------------------------


def _simpson_inverting_corpus():
    """Within each stratum negatives score higher than positives (AUC<0.5),
    but stratum bands + class imbalance flip the pooled rank > 0.5."""
    recs = []
    recs += [_rec("x", 0, 1.0, register="A") for _ in range(8)]
    recs += [_rec("x", 1, 0.0, register="A") for _ in range(3)]
    recs += [_rec("y", 1, 10.0, register="B") for _ in range(8)]
    recs += [_rec("y", 0, 11.0, register="B") for _ in range(3)]
    return recs


def test_simpson_inversion_refuses_pooled():
    sc = vh.simpson_inversion_check(
        _simpson_inverting_corpus(), strata_field="register",
        seed=1, resamples=200)
    assert sc["pooled_roc_auc"] > 0.5
    assert sc["pooled_ranking_refused"] is True
    assert "Simpson" in sc["message"]
    # per-stratum AUCs present.
    assert all("roc_auc" in b for b in sc["per_stratum"].values())


def test_simpson_no_inversion_when_consistent():
    recs = []
    for reg in ("A", "B"):
        recs += [_rec("t", 1, 1.0, register=reg) for _ in range(5)]
        recs += [_rec("t", 0, 0.0, register=reg) for _ in range(5)]
    sc = vh.simpson_inversion_check(
        recs, strata_field="register", seed=1, resamples=100)
    assert sc["pooled_ranking_refused"] is False


def test_simpson_emits_no_corrected_aggregate():
    sc = vh.simpson_inversion_check(
        _simpson_inverting_corpus(), strata_field="register",
        seed=1, resamples=50)
    # Refuse-don't-correct: no "corrected_auc" key in either case.
    assert "corrected_auc" not in sc
    assert "corrected_aggregate" not in sc


def test_simpson_strata_field_must_be_supported():
    sc = vh.simpson_inversion_check(
        [_rec("t", 1, 1.0)], strata_field="not_a_field",
        seed=1, resamples=10)
    assert "unsupported" in sc["reason"]
