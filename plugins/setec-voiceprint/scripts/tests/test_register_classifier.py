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
import hashlib
import json
import random
import re
import struct
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
    REGISTER_REFUSAL_REASONS,
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


_CLASSIFICATION_KEYS = {
    "primary", "confidence", "secondary", "scores", "evidence",
    "warning", "taxonomy", "refusal_reason",
}


def _assert_refusal_contract(result):
    assert set(result) == _CLASSIFICATION_KEYS
    reason = result["refusal_reason"]
    assert reason is None or reason in REGISTER_REFUSAL_REASONS
    assert (result["primary"] == "unknown") == (
        reason in REGISTER_REFUSAL_REASONS
    )
    if result["primary"] != "unknown":
        assert reason is None


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
        r = classify_register("")
        assert r["primary"] == "unknown"
        assert r["confidence"] == 0.0
        assert r["secondary"] == []
        assert r["scores"] == {}
        assert r["evidence"] == {"n_words": 0, "n_chars": 0}
        assert r["warning"] == (
            "Text has 0 words; register classification requires at least 100. "
            "Returning 'unknown'."
        )
        assert r["taxonomy"] == REGISTER_TAXONOMY
        assert r["refusal_reason"] == "short_text"
        _assert_refusal_contract(r)

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

    @pytest.mark.parametrize(("text", "expected_sha256"), (
        (_BLOG_PARAGRAPH,
         "0d88ef3534f706447d3fb7fdad5d790f1076d51c82dd8a6715eee72383093317"),
        (_FICTION_PARAGRAPH,
         "d7c4e59a085862b1af4cdb45f487df134601329d312a3556f0853bfda9bfb76b"),
    ))
    def test_existing_seven_key_results_are_frozen(self, text, expected_sha256):
        result = classify_register(_scale(text))
        assert result.pop("refusal_reason") is None
        frozen = json.dumps(
            result, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        assert hashlib.sha256(frozen).hexdigest() == expected_sha256

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
            "message.imessage": "short_social",
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
        assert resolve_family("message.imessage") == "short_social"
        assert resolve_family("not-a-register") == "unknown"

    def test_public_results_always_carry_taxonomy(self):
        for result in (
            classify_register("short"),
            classify_register(_scale(_BLOG_PARAGRAPH)),
            register_match("personal", []),
            register_match("personal", ["personal"]),
        ):
            assert result["taxonomy"] == REGISTER_TAXONOMY

    def test_refusal_reasons_are_exact_public_tuple(self):
        assert REGISTER_REFUSAL_REASONS == (
            "short_text", "all_weak", "exact_top_tie",
        )
        assert isinstance(REGISTER_REFUSAL_REASONS, tuple)
        assert "REGISTER_REFUSAL_REASONS" in rc.__all__

    def test_public_result_shapes_are_additive_and_exact(self):
        early = classify_register("short")
        final = classify_register(_scale(_BLOG_PARAGRAPH))
        assert set(early) == _CLASSIFICATION_KEYS
        assert set(final) == _CLASSIFICATION_KEYS
        match = register_match("personal", ["personal"])
        assert set(match) == {
            "strength", "rationale", "target", "baseline_distribution",
            "taxonomy", "target_family", "baseline_family_distribution",
        }

    @pytest.mark.parametrize("family,text", _FAMILY_FIXTURES.items())
    def test_every_scorer_backed_family_is_reachable(self, family, text):
        result = classify_register(_scale(text))
        assert result["primary"] == family
        assert result["refusal_reason"] is None

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
        assert result["scores"] == {"weak_a": 0.1, "weak_b": 0.05}
        assert result["warning"] is None
        assert result["taxonomy"] == REGISTER_TAXONOMY
        assert result["refusal_reason"] == "all_weak"
        _assert_refusal_contract(result)

    def test_short_text_refuses_before_any_scorer_runs(self, monkeypatch):
        def must_not_run(_features):
            raise AssertionError("short text must not be scored")

        monkeypatch.setattr(rc, "_SCORERS", {"unexpected": must_not_run})
        result = classify_register("short")
        assert result["primary"] == "unknown"
        assert result["confidence"] == 0.0
        assert result["scores"] == {}
        assert result["secondary"] == []
        assert result["refusal_reason"] == "short_text"

    def test_scorer_error_propagates_without_a_fabricated_reason(self, monkeypatch):
        def broken_scorer(_features):
            raise RuntimeError("synthetic scorer failure")

        monkeypatch.setattr(rc, "_SCORERS", {"broken": broken_scorer})
        with pytest.raises(RuntimeError, match="synthetic scorer failure"):
            classify_register(_scale("word "))

    def test_rounding_and_hint_order_at_threshold(self, monkeypatch):
        monkeypatch.setattr(rc, "_SCORERS", {
            "alpha": lambda _features: 0.29996,
            "beta": lambda _features: 0.1,
        })
        at_floor = classify_register(_scale("word "))
        assert at_floor["primary"] == "alpha"
        assert at_floor["scores"]["alpha"] == 0.3
        assert at_floor["confidence"] == 0.3
        assert at_floor["refusal_reason"] is None

        monkeypatch.setattr(rc, "_SCORERS", {
            "first_person_essay": lambda _features: 0.24995,
            "beta": lambda _features: 0.1,
        })
        raised_by_hint = classify_register(
            _scale("word "), hint="first_person_essay",
        )
        assert raised_by_hint["primary"] == "first_person_essay"
        assert raised_by_hint["scores"]["first_person_essay"] == 0.3
        assert raised_by_hint["confidence"] == 0.3
        assert raised_by_hint["refusal_reason"] is None
        _assert_refusal_contract(at_floor)
        _assert_refusal_contract(raised_by_hint)

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
        assert tied["refusal_reason"] == "exact_top_tie"

        def beta_lower(_features):
            return 0.4999

        monkeypatch.setattr(rc, "_SCORERS", {"alpha": alpha, "beta": beta_lower})
        untied = classify_register(_scale("word "))
        assert untied["primary"] == "alpha"
        assert untied["refusal_reason"] is None

    def test_rounding_and_hint_can_create_exact_top_ties(self, monkeypatch):
        monkeypatch.setattr(rc, "_SCORERS", {
            "alpha": lambda _features: 0.30004,
            "beta": lambda _features: 0.3,
        })
        rounded_tie = classify_register(_scale("word "))
        assert rounded_tie["scores"] == {"alpha": 0.3, "beta": 0.3}
        assert rounded_tie["primary"] == "unknown"
        assert rounded_tie["secondary"] == ["alpha", "beta"]
        assert rounded_tie["refusal_reason"] == "exact_top_tie"

        monkeypatch.setattr(rc, "_SCORERS", {
            "alpha": lambda _features: 0.35,
            "first_person_essay": lambda _features: 0.29996,
            "beta": lambda _features: 0.1,
        })
        hint_tie = classify_register(
            _scale("word "), hint="first_person_essay",
        )
        assert hint_tie["scores"] == {
            "alpha": 0.35, "first_person_essay": 0.35, "beta": 0.1,
        }
        assert hint_tie["primary"] == "unknown"
        assert hint_tie["secondary"] == ["alpha", "first_person_essay"]
        assert hint_tie["refusal_reason"] == "exact_top_tie"
        _assert_refusal_contract(rounded_tie)
        _assert_refusal_contract(hint_tie)

    def test_mutated_warning_production_cannot_change_refusal_reason(
        self, monkeypatch,
    ):
        monkeypatch.setattr(
            rc, "_unrecognized_hint_warning",
            lambda hint: f"MUTATED HINT {hint}",
        )
        monkeypatch.setattr(
            rc, "_short_text_warning",
            lambda n_words, min_words: f"MUTATED SHORT {n_words}/{min_words}",
        )
        monkeypatch.setattr(
            rc, "_exact_top_tie_warning",
            lambda tied: "MUTATED TIE " + "|".join(tied),
        )

        monkeypatch.setattr(rc, "_SCORERS", {
            "alpha": lambda _features: 0.5,
            "beta": lambda _features: 0.1,
        })
        successful = classify_register(
            _scale("word "), hint="not-a-register",
        )
        assert successful["warning"] == "MUTATED HINT not-a-register"
        assert successful["refusal_reason"] is None

        monkeypatch.setattr(rc, "_SCORERS", {
            "alpha": lambda _features: 0.2,
            "beta": lambda _features: 0.1,
        })
        refused = classify_register(_scale("word "), hint="not-a-register")
        assert refused["warning"] == "MUTATED HINT not-a-register"
        assert refused["refusal_reason"] == "all_weak"

        monkeypatch.setattr(rc, "_SCORERS", {
            "alpha": lambda _features: 0.5,
            "beta": lambda _features: 0.5,
        })
        tied = classify_register(_scale("word "), hint="not-a-register")
        assert tied["warning"] == (
            "MUTATED HINT not-a-register; MUTATED TIE alpha|beta"
        )
        assert tied["refusal_reason"] == "exact_top_tie"

        short = classify_register("short", hint="not-a-register")
        assert short["warning"] == (
            "MUTATED HINT not-a-register; MUTATED SHORT 1/100"
        )
        assert short["refusal_reason"] == "short_text"

        for result in (successful, refused, tied, short):
            _assert_refusal_contract(result)

    @pytest.mark.parametrize("seed", (0, 37, 76, 7301))
    def test_seeded_scorer_tables_obey_biconditional(self, monkeypatch, seed):
        rng = random.Random(seed)
        scores = {
            f"family_{index}": rng.randrange(0, 10001) / 10000
            for index in range(5)
        }
        monkeypatch.setattr(
            rc,
            "_SCORERS",
            {family: (lambda _features, value=value: value)
             for family, value in scores.items()},
        )
        for hint in (None, "unknown", "not-a-register"):
            _assert_refusal_contract(
                classify_register(_scale("word "), hint=hint),
            )

    def test_refusal_biconditional_full_input_matrix(self, monkeypatch):
        results = [
            classify_register(""),
            classify_register(" \t\n  "),
            classify_register("word " * 99),
            classify_register("short", hint="personal"),
            classify_register("short", hint="not-a-register"),
        ]
        matrices = (
            ({"alpha": 0.1, "beta": 0.05}, (None, "not-a-register")),
            ({"alpha": 0.29996, "beta": 0.1}, (None, "not-a-register")),
            ({"alpha": 0.3, "beta": 0.3}, (None, "not-a-register")),
            ({"alpha": 0.3001, "beta": 0.3}, (None, "not-a-register")),
            ({"first_person_essay": 0.24995, "beta": 0.1},
             ("personal", "not-a-register")),
        )
        for scores, hints in matrices:
            monkeypatch.setattr(
                rc,
                "_SCORERS",
                {family: (lambda _features, value=value: value)
                 for family, value in scores.items()},
            )
            for hint in hints:
                results.append(classify_register(_scale("word "), hint=hint))
        for result in results:
            _assert_refusal_contract(result)

    def test_refusal_contract_receipt_vector(self):
        contract = {
            "field": "refusal_reason",
            "null_when": "scored_family",
            "reasons": list(REGISTER_REFUSAL_REASONS),
            "taxonomy": REGISTER_TAXONOMY,
        }
        payload = json.dumps(
            contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        preimage = (
            b"setec-register-classifier-refusal-contract-v1\n"
            + struct.pack(">Q", len(payload)) + payload
        )
        assert len(payload) == 140
        assert hashlib.sha256(preimage).hexdigest() == (
            "f2255796634c1e1f2269029cc25afede25f4c033576b5dfba31f160c975a40c5"
        )

    def test_recursive_no_verdict_privacy_walk(self, monkeypatch):
        values = [classify_register("short")]
        monkeypatch.setattr(rc, "_SCORERS", {
            "alpha": lambda _features: 0.2,
            "beta": lambda _features: 0.1,
        })
        values.append(
            classify_register(_scale("word "), hint="not-a-register"),
        )
        monkeypatch.setattr(rc, "_SCORERS", {
            "alpha": lambda _features: 0.5,
            "beta": lambda _features: 0.5,
        })
        values.append(classify_register(_scale("word ")))
        monkeypatch.setattr(rc, "_SCORERS", {
            "alpha": lambda _features: 0.5001,
            "beta": lambda _features: 0.5,
        })
        values.extend((
            classify_register(_scale("word ")),
            REGISTER_REFUSAL_REASONS,
        ))
        forbidden = {
            "verdict", "label", "author", "identity", "ai", "human",
            "quality", "source", "path", "manifest", "corpus",
            "is_ai", "is_human", "same_author",
        }

        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield from walk(key)
                    yield from walk(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    yield from walk(child)
            elif isinstance(value, str):
                yield from re.findall(r"[a-z_]+", value.lower())

        assert forbidden.isdisjoint(set(walk(values)))
        assert "prompt to ask register match questions" in (rc.__doc__ or "")

    @pytest.mark.parametrize(("args", "kwargs", "error"), (
        ((None,), {}, TypeError),
        ((_scale("word "),), {"hint": 7}, AttributeError),
        ((_scale("word "),), {"min_words": None}, TypeError),
    ))
    def test_existing_input_type_errors_propagate(self, args, kwargs, error):
        with pytest.raises(error):
            classify_register(*args, **kwargs)

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


def test_capability_fragment_reason_list_matches_the_code_constant():
    """The published capability registry must not advertise a stale reason set.

    `REGISTER_REFUSAL_REASONS` and the `refusal_reasons` list in the capability
    fragment (plus its golden mirror) are three independent copies of one
    vocabulary. `check_capabilities_drift.py` validates manifest<->source
    structurally and does not compare this field's CONTENTS, so without this
    test a fourth reason could be added to the tuple and its emitting branch,
    pass every classifier test and the drift gate, and leave the registry
    silently advertising the old three-value vocabulary to consumers.
    """
    yaml = pytest.importorskip("yaml")
    root = Path(__file__).resolve().parents[2]
    fragment = root / "capabilities.d" / "register_classifier.yaml"
    golden = Path(__file__).resolve().parent / "_golden_capabilities" / "register_classifier.json"
    assert fragment.exists(), fragment

    declared = yaml.safe_load(fragment.read_text(encoding="utf-8"))
    listed = _find_refusal_reasons(declared)
    assert listed is not None, f"no refusal_reasons key in {fragment}"
    assert tuple(listed) == rc.REGISTER_REFUSAL_REASONS, (
        "capabilities.d/register_classifier.yaml refusal_reasons "
        f"{tuple(listed)!r} != REGISTER_REFUSAL_REASONS "
        f"{rc.REGISTER_REFUSAL_REASONS!r}"
    )

    if golden.exists():
        golden_entry = json.loads(golden.read_text(encoding="utf-8"))
        found = _find_refusal_reasons(golden_entry)
        assert found is not None, f"no refusal_reasons key in {golden}"
        assert tuple(found) == rc.REGISTER_REFUSAL_REASONS, (
            f"golden {golden.name} refusal_reasons {tuple(found)!r} != "
            f"REGISTER_REFUSAL_REASONS {rc.REGISTER_REFUSAL_REASONS!r}"
        )


def _find_refusal_reasons(node):
    """Locate the `refusal_reasons` list anywhere in a nested golden payload."""
    if isinstance(node, dict):
        if "refusal_reasons" in node:
            return node["refusal_reasons"]
        for value in node.values():
            found = _find_refusal_reasons(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_refusal_reasons(value)
            if found is not None:
                return found
    return None


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
