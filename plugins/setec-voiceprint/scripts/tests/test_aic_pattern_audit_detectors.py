#!/usr/bin/env python3
"""Functional tests for aic_pattern_audit.py's pattern detectors.

The surface previously had only a schema-shape test (`test_aic_pattern_audit_schema.py`);
the detector LOGIC — the named AIC patterns from `references/source-triage.md` —
was untested. These pin each pure detector against hand-constructed positive and
negative inputs so a regression in the matching logic is caught (not just the
envelope shape). See issue #197.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import aic_pattern_audit as aic  # type: ignore


# ---- negation hedge (sentence-pair) --------------------------------------

def test_negation_hedge_fires_on_initial_not_then_affirm():
    sentences = ["Not a problem.", "It is an opportunity."]
    hits = aic.detect_negation_hedge_pairs(sentences)
    assert len(hits) == 1
    assert hits[0].pattern == "negation_hedge"
    assert hits[0].sentence_index == 0


def test_negation_hedge_silent_without_initial_not():
    sentences = ["This is fine.", "It works."]
    assert aic.detect_negation_hedge_pairs(sentences) == []


def test_negation_hedge_skips_when_next_also_negates():
    # "Not X." followed by another "Not Y." is still the negation list, not
    # the affirm — must not fire.
    sentences = ["Not a problem.", "Not really either."]
    assert aic.detect_negation_hedge_pairs(sentences) == []


def test_negation_hedge_skips_overlong_discrimination():
    # The initial negation must be a short discrimination (< 25 words).
    long_not = "Not " + " ".join(["word"] * 30) + "."
    sentences = [long_not, "It affirms the claim."]
    assert aic.detect_negation_hedge_pairs(sentences) == []


# ---- disguised correctio (inline) ----------------------------------------

def test_correctio_fires_on_inline_not_but():
    sentences = ["This is not a failure, but a lesson."]
    hits = aic.detect_disguised_correctio(sentences)
    assert len(hits) == 1
    assert hits[0].pattern == "correctio"


def test_correctio_silent_on_plain_sentence():
    assert aic.detect_disguised_correctio(["This is a success."]) == []


# ---- pseudo-aphorism (gnomic frames) -------------------------------------

def test_pseudo_aphorism_fires_on_there_is_a_kind_of_frame():
    sentences = ["There is a kind of courage in every failure."]
    hits = aic.detect_pseudo_aphorism(sentences)
    assert len(hits) == 1
    assert hits[0].pattern == "pseudo_aphorism"


def test_pseudo_aphorism_silent_on_plain_sentence():
    assert aic.detect_pseudo_aphorism(["Cats sleep often."]) == []


# ---- manifesto cadence (anaphoric run) -----------------------------------

def test_manifesto_cadence_fires_on_three_same_head():
    sentences = ["We will fight.", "We will win.", "We will endure."]
    hits = aic.detect_manifesto_cadence(sentences, min_run=3)
    assert len(hits) == 1
    assert hits[0].pattern == "manifesto_cadence"
    assert "run length 3" in hits[0].note


def test_manifesto_cadence_silent_below_min_run():
    # Two matching heads then a break — run of 2 is below the default min_run=3.
    sentences = ["We will fight.", "We will win.", "They lost."]
    assert aic.detect_manifesto_cadence(sentences, min_run=3) == []


# ---- triplet (3-4 item comma list) ---------------------------------------

def test_triplet_fires_on_three_item_list():
    hits = aic.detect_triplets(["We need courage, wisdom, and strength."])
    assert len(hits) == 1
    assert hits[0].pattern == "triplet"


def test_triplet_silent_on_two_item_list():
    # A single "X and Y" has too few comma-separated items to be a triplet.
    assert aic.detect_triplets(["We need courage and strength."]) == []


# ---- all_patterns integration --------------------------------------------

def test_all_patterns_returns_every_family_and_counts_a_known_hit():
    text = "Not a problem. It is an opportunity."
    sentences = ["Not a problem.", "It is an opportunity."]
    results = aic.all_patterns(text, sentences)
    # Every named detector family is present in the result dict...
    for key in ("negation_hedge", "correctio", "pseudo_aphorism",
                "manifesto_cadence", "triplet"):
        assert key in results
    # ...and the planted negation-hedge is counted, with the clean text empty.
    assert results["negation_hedge"].count == 1
    assert results["triplet"].count == 0


if __name__ == "__main__":
    import traceback
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                print(f"FAIL {name}")
                traceback.print_exc()
