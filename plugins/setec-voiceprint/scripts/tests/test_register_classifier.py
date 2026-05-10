#!/usr/bin/env python3
"""Regression tests for register_classifier.py (Release 1).

Phase-1 trustworthiness layer. The classifier is intentionally
heuristic — its primary value is honest claim-licensing
(register-mismatch warnings), not classification accuracy. Tests
check the behavior contract:

  * Clear-case classifications hit the right register.
  * Confidence is in [0, 1] and tracks how clearly the input
    matches.
  * `secondary` lists nearby candidates within a 0.10 band.
  * Short-text refusal: below `min_words` returns
    primary='unknown' with a warning.
  * `hint` provides a small score nudge.
  * `register_match()` returns the right strength label across
    strong / moderate / weak / mismatch cases.
  * `register_match()` handles unknown-target and empty-baseline
    edge cases.

Synthetic corpus uses obvious genre signals; this is a smoke
contract on the heuristics, not an evaluation harness.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

from register_classifier import (  # type: ignore
    KNOWN_REGISTERS,
    classify_register,
    register_match,
    render_register_match_block,
)


# ---------- Fixtures ----------


_FICTION_PARAGRAPH = (
    "She walked down the corridor and looked at the photograph. "
    '"You knew, didn\'t you?" she said. He thought about it for '
    "a long moment. He remembered the night, the cold light, the "
    'way she had stood at the window. "I knew," he said. She '
    "watched him for a long moment. The room felt smaller. She "
    "wanted to tell him everything but the words would not come. "
    "He believed her. He had always believed her, even when he "
    "should not have. The clock on the mantel struck the hour. "
    "Outside, the snow had begun to fall again. "
)

_LEGAL_PARAGRAPH = (
    "Pursuant to 42 U.S.C. § 1983, the plaintiff shall be "
    "entitled to relief. The Court held in Smith v. Jones, 123 "
    "F.3d 456, that the statute applies broadly. Under Pub. L. "
    "No. 116-25, agencies shall provide the requested information. "
    "Notwithstanding the foregoing, the aforementioned obligations "
    "shall not apply to entities exempt under § 504(c). Fed. R. "
    "Civ. P. 12 governs the procedure. The hereinafter-mentioned "
    "parties shall comply with the requirements set forth in 18 "
    "U.S.C. § 922. Whereas the Committee has determined that "
    "compliance is essential, the Department shall report quarterly. "
)

_BLOG_PARAGRAPH = (
    "I started writing this essay because I could not stop "
    "thinking about my grandmother. She had a way of telling "
    "stories that made the ordinary feel mythic. We would sit at "
    "the kitchen table for hours and she would tell me about the "
    "war, about the boats, about the night her father did not come "
    "home. I have tried to write like her my whole life. I do not "
    "know if I can. My voice is different, flatter, more cautious, "
    "more academic. I think that is what happens to writers who go "
    "to graduate school. We trade the kitchen table for the seminar "
    "room and the seminar room never quite gives it back. "
)

_ACADEMIC_PARAGRAPH = (
    "We argue in this paper that the standard model of voter "
    "rationality is incomplete (Smith, 2020). As shown in section "
    "3, the empirical evidence (Jones and Davies, 2019) suggests a "
    "different mechanism. This paper proposes an alternative "
    "framework. We demonstrate that the alternative framework "
    "(Brown, 2021) better fits the data presented in section 4. "
    "As argued earlier (Lee, 2018), prior work has missed this. "
    "We conclude that the alternative is more parsimonious. "
)


def _scale(text: str, target_words: int = 250) -> str:
    """Repeat text to reach a target word count for the classifier's
    minimum-length floor (default 100)."""
    cur = len(text.split())
    times = max(2, target_words // max(1, cur) + 1)
    return (text + " ") * times


# ---------- classify_register ----------


class TestClassifyRegister:
    def test_fiction_classified_as_fiction(self):
        r = classify_register(_scale(_FICTION_PARAGRAPH))
        assert r["primary"] in {
            "literary_fiction",
            "commercial_fiction",
            "literary_horror",
        }
        assert r["confidence"] > 0.5

    def test_legal_classified_as_legal_or_policy(self):
        r = classify_register(_scale(_LEGAL_PARAGRAPH))
        assert r["primary"] in {"legal_memo", "policy_memo"}
        assert r["confidence"] > 0.5

    def test_blog_classified_as_essay(self):
        r = classify_register(_scale(_BLOG_PARAGRAPH))
        assert r["primary"] in {
            "blog_essay",
            "personal_essay",
            "newsletter",
        }
        assert r["confidence"] > 0.5

    def test_academic_classified_as_academic(self):
        r = classify_register(_scale(_ACADEMIC_PARAGRAPH))
        assert r["primary"] in {
            "academic_philosophy",
            "academic_general",
        }
        assert r["confidence"] > 0.5

    def test_short_text_returns_unknown(self):
        r = classify_register("Short text fragment.")
        assert r["primary"] == "unknown"
        assert r["confidence"] == 0.0
        assert r["warning"]

    def test_confidence_in_unit_interval(self):
        r = classify_register(_scale(_BLOG_PARAGRAPH))
        assert 0.0 <= r["confidence"] <= 1.0
        for s in r["scores"].values():
            assert 0.0 <= s <= 1.0

    def test_secondary_within_threshold(self):
        r = classify_register(_scale(_BLOG_PARAGRAPH))
        # Secondary candidates are within 0.10 of primary score.
        primary = r["confidence"]
        for secondary in r["secondary"]:
            assert (primary - r["scores"][secondary]) < 0.10

    def test_evidence_carries_features(self):
        r = classify_register(_scale(_FICTION_PARAGRAPH))
        e = r["evidence"]
        assert "n_words" in e
        assert "dialogue_ratio" in e
        assert "first_person_per_1k" in e

    def test_hint_nudges_score(self):
        text = _scale(_BLOG_PARAGRAPH)
        no_hint = classify_register(text)
        with_hint = classify_register(text, hint="newsletter")
        # The hinted register's score should be slightly higher.
        assert (
            with_hint["scores"]["newsletter"]
            >= no_hint["scores"]["newsletter"]
        )

    def test_classification_returns_known_register_or_unknown(self):
        """Whatever it returns is in the canonical taxonomy. The
        classifier doesn't commit to behavior on edge-case gibberish
        beyond returning a register name (potentially with low
        confidence) — the contract is the taxonomy, not the floor."""
        text = _scale("Words. More words. Words. Words. ")
        r = classify_register(text)
        assert r["primary"] in KNOWN_REGISTERS


# ---------- register_match ----------


class TestRegisterMatch:
    def test_strong_match(self):
        baseline = ["blog_essay"] * 9 + ["personal_essay"]
        m = register_match("blog_essay", baseline)
        assert m["strength"] == "strong"

    def test_moderate_match(self):
        baseline = ["blog_essay", "blog_essay", "blog_essay", "personal_essay"]
        m = register_match("blog_essay", baseline)
        assert m["strength"] == "moderate"

    def test_weak_match(self):
        baseline = (
            ["blog_essay"] * 2
            + ["personal_essay"] * 4
            + ["literary_fiction"] * 4
        )
        m = register_match("blog_essay", baseline)
        assert m["strength"] == "weak"

    def test_mismatch(self):
        baseline = ["legal_memo"] * 3 + ["policy_memo"] * 3
        m = register_match("blog_essay", baseline)
        assert m["strength"] == "mismatch"
        assert "legal_memo" in m["rationale"]

    def test_unknown_target(self):
        m = register_match("unknown", ["blog_essay", "personal_essay"])
        assert m["strength"] == "weak"
        assert "unknown" in m["rationale"].lower()

    def test_none_target(self):
        m = register_match(None, ["blog_essay"])
        assert m["strength"] == "weak"

    def test_empty_baseline(self):
        m = register_match("blog_essay", [])
        assert m["strength"] == "mismatch"
        assert "no registered" in m["rationale"].lower()

    def test_baseline_distribution_recorded(self):
        m = register_match(
            "blog_essay",
            ["blog_essay", "blog_essay", "personal_essay", None],
        )
        assert m["baseline_distribution"]["blog_essay"] == 2
        assert m["baseline_distribution"]["personal_essay"] == 1
        assert m["baseline_distribution"]["unknown"] == 1


class TestRenderRegisterMatchBlock:
    def test_renders_strength_and_rationale(self):
        m = register_match("blog_essay", ["blog_essay"] * 5)
        block = render_register_match_block(m)
        assert "**Register match:**" in block
        assert "strong" in block

    def test_mismatch_renders(self):
        m = register_match("blog_essay", ["legal_memo"] * 5)
        block = render_register_match_block(m)
        assert "mismatch" in block


# ---------- Known registers taxonomy ----------


class TestTaxonomy:
    def test_canonical_registers_present(self):
        for required in (
            "blog_essay", "personal_essay",
            "literary_fiction", "academic_philosophy",
            "legal_memo", "testimony_policy",
            "unknown",
        ):
            assert required in KNOWN_REGISTERS


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
