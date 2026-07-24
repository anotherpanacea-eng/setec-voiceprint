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
import inspect
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import manifest_validator  # type: ignore
import register_classifier as rc  # type: ignore
from register_classifier import (  # type: ignore
    CANONICAL_REGISTER_TO_FAMILY,
    KNOWN_REGISTERS,
    LEGACY_REGISTER_TO_FAMILY,
    REGISTER_FAMILIES,
    REGISTER_TAXONOMY,
    classify_register,
    register_match,
    render_register_match_block,
    resolve_family,
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

_FAMILY_FIXTURES = {
    "formal_legal_policy": (
        "Pursuant to 42 U.S.C. § 1983, the agency shall provide relief "
        "notwithstanding the foregoing. Smith v. Jones applies. "
    ),
    "formal_first_person": (
        "Dear Senator Smith, I ask the Committee to consider 42 U.S.C. § 1983. "
        "We believe this policy matters to us. "
    ),
    "academic": (
        "We argue in this paper that evidence supports the theory (Smith, 2020). "
        "As shown in section 3, this study demonstrates the result. "
    ),
    "journalism": (
        "The agency reported the proposal on Monday. The department announced "
        "the schedule. According to the committee, residents noted the change.\n\n"
    ),
    "narrative_fiction": (
        'She walked into the room and looked away. "You knew," he said. '
        "She remembered what she had seen and felt the cold. "
    ),
    "first_person_essay": (
        "I remember my childhood and I think about our family. My experience "
        "shapes how I write about the ordinary world around me. "
    ),
    "promotional": (
        "Buy today! Discover your best work! You can unlock your future. "
        "Join now and save! "
    ),
    "short_social": (
        "I wonder why you left?\n\nDo you remember me?\n\n"
        "I think we should ask why?\n\n"
    ),
}


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
        assert r["primary"] == "narrative_fiction"
        assert r["confidence"] > 0.5

    def test_legal_classified_as_legal_or_policy(self):
        r = classify_register(_scale(_LEGAL_PARAGRAPH))
        assert r["primary"] == "formal_legal_policy"
        assert r["confidence"] > 0.5

    def test_blog_classified_as_essay(self):
        r = classify_register(_scale(_BLOG_PARAGRAPH))
        assert r["primary"] == "first_person_essay"
        assert r["confidence"] > 0.5

    def test_academic_classified_as_academic(self):
        r = classify_register(_scale(_ACADEMIC_PARAGRAPH))
        assert r["primary"] == "academic"
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
        with_hint = classify_register(text, hint="personal")
        # The hinted register's score should be slightly higher.
        assert (
            with_hint["scores"]["first_person_essay"]
            >= no_hint["scores"]["first_person_essay"]
        )

    def test_hint_family_and_canonical_are_equivalent(self):
        text = _scale(_BLOG_PARAGRAPH)
        assert (
            classify_register(text, hint="personal")["scores"]
            == classify_register(text, hint="first_person_essay")["scores"]
        )

    def test_unknown_hint_warns_and_does_not_change_scores(self):
        text = _scale(_BLOG_PARAGRAPH)
        plain = classify_register(text)
        warned = classify_register(text, hint="not-a-register")
        assert warned["scores"] == plain["scores"]
        assert "not-a-register" in warned["warning"]

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
        baseline = ["personal"] * 10
        m = register_match("first_person_essay", baseline)
        assert m["strength"] == "strong"
        assert m["target_family"] == "first_person_essay"
        assert m["baseline_distribution"] == {"personal": 10}
        assert m["baseline_family_distribution"] == {"first_person_essay": 10}
        canonical = register_match("personal", baseline)
        assert canonical["strength"] == "strong"
        assert canonical["target_family"] == "first_person_essay"
        assert canonical["baseline_distribution"] == {"personal": 10}
        assert canonical["baseline_family_distribution"] == {
            "first_person_essay": 10,
        }

    def test_moderate_match(self):
        baseline = ["personal"] * 2 + ["literary_fiction"] * 2
        m = register_match("personal", baseline)
        assert m["strength"] == "moderate"

    def test_weak_match(self):
        baseline = (
            ["personal"] * 2
            + ["academic_philosophy"] * 4
            + ["literary_fiction"] * 4
        )
        m = register_match("personal", baseline)
        assert m["strength"] == "weak"

    def test_mismatch(self):
        baseline = ["legal_brief"] * 3 + ["policy_brief"] * 3
        m = register_match("personal", baseline)
        assert m["strength"] == "mismatch"
        assert "formal_legal_policy" in m["rationale"]

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
            "personal",
            ["personal", "personal", "blog_essay", None],
        )
        assert m["baseline_distribution"]["personal"] == 2
        assert m["baseline_distribution"]["blog_essay"] == 1
        assert m["baseline_distribution"]["unknown"] == 1

    def test_family_collapse_is_disclosed(self):
        m = register_match(
            "formal_legal_policy",
            ["legal_brief", "grant_proposal"],
        )
        assert m["strength"] == "strong"
        assert "does not distinguish document types" in m["rationale"]


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
    def test_canonical_mapping_is_exact_and_total(self):
        expected = {
            "literary_fiction": "narrative_fiction",
            "literary_horror": "narrative_fiction",
            "blog_essay": "first_person_essay",
            "personal": "first_person_essay",
            "academic_philosophy": "academic",
            "scholarly_article": "academic",
            "testimony_policy": "formal_first_person",
            "expert_affidavit": "formal_first_person",
            "policy_brief": "formal_legal_policy",
            "legal_brief": "formal_legal_policy",
            "regulatory_comment": "formal_legal_policy",
            "grant_proposal": "formal_legal_policy",
            "policy_advocacy": "formal_legal_policy",
            "professional_letter": "formal_first_person",
            "teaching": "academic",
        }
        assert CANONICAL_REGISTER_TO_FAMILY == expected
        assert set(CANONICAL_REGISTER_TO_FAMILY) == manifest_validator.ALLOWED_REGISTER

    def test_legacy_mapping_is_exact(self):
        assert LEGACY_REGISTER_TO_FAMILY == {
            "personal_essay": "first_person_essay",
            "commercial_fiction": "narrative_fiction",
            "academic_general": "academic",
            "legal_memo": "formal_legal_policy",
            "policy_memo": "formal_legal_policy",
            "newsletter": "first_person_essay",
            "marketing": "promotional",
            "report_prose": "journalism",
            "social_thread": "short_social",
            "email": "formal_first_person",
        }

    def test_domains_and_scorers_are_coherent(self):
        families = set(REGISTER_FAMILIES)
        canonical = set(CANONICAL_REGISTER_TO_FAMILY)
        legacy = set(LEGACY_REGISTER_TO_FAMILY)
        assert KNOWN_REGISTERS == REGISTER_FAMILIES
        assert families.isdisjoint(canonical)
        assert families.isdisjoint(legacy)
        assert canonical.isdisjoint(legacy)
        assert set(CANONICAL_REGISTER_TO_FAMILY.values()) <= families - {"unknown"}
        assert set(LEGACY_REGISTER_TO_FAMILY.values()) <= families - {"unknown"}
        assert set(rc._SCORERS) == families - {"unknown"}
        assert len(set(rc._SCORERS.values())) == len(rc._SCORERS)

    def test_resolver_accepts_both_vocabularies(self):
        assert resolve_family("first_person_essay") == "first_person_essay"
        assert resolve_family("personal") == "first_person_essay"
        assert resolve_family("personal_essay") == "first_person_essay"
        assert resolve_family("not-a-register") == "unknown"

    def test_public_results_always_carry_taxonomy(self):
        for result in (
            classify_register("short"),
            classify_register(_scale(_BLOG_PARAGRAPH)),
            register_match("personal", []),
            register_match("personal", ["personal"]),
        ):
            assert result["taxonomy"] == REGISTER_TAXONOMY

    def test_public_result_shapes_are_additive_and_exact(self):
        classification = classify_register(_scale(_BLOG_PARAGRAPH))
        assert set(classification) == {
            "primary", "confidence", "secondary", "scores", "evidence",
            "warning", "taxonomy",
        }
        match = register_match("personal", ["personal"])
        assert set(match) == {
            "strength", "rationale", "target", "baseline_distribution",
            "taxonomy", "target_family", "baseline_family_distribution",
        }

    @pytest.mark.parametrize("family,text", _FAMILY_FIXTURES.items())
    def test_every_scorer_backed_family_is_reachable(self, family, text):
        assert classify_register(_scale(text))["primary"] == family

    @pytest.mark.parametrize("legacy,family", LEGACY_REGISTER_TO_FAMILY.items())
    def test_legacy_inputs_resolve_but_are_never_emitted(self, legacy, family):
        hinted = classify_register(
            _scale(_FAMILY_FIXTURES[family]),
            hint=legacy,
        )
        assert legacy not in hinted["scores"]
        assert legacy not in hinted["secondary"]
        assert hinted["primary"] != legacy
        matched = register_match(legacy, [legacy] * 3)
        assert matched["strength"] == "strong"
        assert matched["target_family"] == family
        assert legacy not in matched["baseline_family_distribution"]

    def test_all_weak_scores_refuse(self, monkeypatch):
        def weak_a(_features):
            return 0.1

        def weak_b(_features):
            return 0.05

        monkeypatch.setattr(rc, "_SCORERS", {"weak_a": weak_a, "weak_b": weak_b})
        result = classify_register(_scale("word "))
        assert result["primary"] == "unknown"
        assert result["confidence"] == 0.1
        assert result["secondary"] == []
        assert result["taxonomy"] == REGISTER_TAXONOMY

    def test_scorers_are_key_agnostic_and_report_exact_values(self, monkeypatch):
        features = rc._features(_scale(_BLOG_PARAGRAPH))
        monkeypatch.setattr(rc, "_features", lambda _text: features)
        result = classify_register("ignored", min_words=1)
        for scorer in rc._SCORERS.values():
            assert len(inspect.signature(scorer).parameters) == 1
        assert result["scores"] == {
            family: round(scorer(features), 4)
            for family, scorer in rc._SCORERS.items()
        }

    def test_exact_top_tie_refuses_without_order_bias(self, monkeypatch):
        def alpha(_features):
            return 0.5000

        def beta(_features):
            return 0.5000

        monkeypatch.setattr(rc, "_SCORERS", {"alpha": alpha, "beta": beta})
        tied = classify_register(_scale("word "), hint="invalid")
        assert tied["primary"] == "unknown"
        assert tied["confidence"] == 0.5
        assert tied["secondary"] == ["alpha", "beta"]
        assert tied["warning"].index("invalid") < tied["warning"].index("tie")

        def beta_lower(_features):
            return 0.4999

        monkeypatch.setattr(rc, "_SCORERS", {"alpha": alpha, "beta": beta_lower})
        untied = classify_register(_scale("word "))
        assert untied["primary"] == "alpha"

    def test_no_verdict_keys_and_posture(self):
        forbidden = {"verdict", "is_ai", "is_human", "label", "same_author"}
        for result in (
            classify_register(_scale(_BLOG_PARAGRAPH)),
            register_match("personal", ["personal"]),
        ):
            assert forbidden.isdisjoint(result)
        assert "prompt to ask register match questions" in (rc.__doc__ or "")

    def test_runtime_import_does_not_import_manifest_validator(self):
        code = (
            "import sys; import register_classifier; "
            "raise SystemExit('manifest_validator' in sys.modules)"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            check=False,
        )
        assert completed.returncode == 0


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
