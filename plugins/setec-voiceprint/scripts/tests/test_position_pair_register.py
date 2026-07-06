#!/usr/bin/env python3
"""Tests for position_pair_register.py — the position_pair_register surface
(stance-consistency PR 1, producer). SPEC: setec-scratch/apo-stance-consistency/
SPEC.md (v4 as amended by v5).

The F11 producer fixture matrix. Every test is model-free and deterministic (the
mock/manifest backends only). The posture-critical gates — F4 (Q-string) and F3
(runtime banned-key walk) — are tested hardest.

Mock marker format (a CI scaffold; a live judge reads raw prose):
    [[pair=<id> side=<a|b> q=<question...>]] <the passage text.>
Two markers sharing a `pair` id (one side=a, one side=b) form one pair, labeled
with their shared `q`. `q=` must be the LAST key in the marker (it swallows the
remainder, so it may contain spaces and a '?').
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
_REPO_ROOT = _SCRIPTS.parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import position_pair_register as s  # type: ignore  # noqa: E402
import position_pair_register_judge as j  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402
from claim_license import TASK_SURFACE_LABELS  # type: ignore  # noqa: E402


# ----------------------------------------------------------------------
# Helpers: build a mock envelope from marker-annotated text.
# ----------------------------------------------------------------------

def _mock_envelope(text: str, *, cap_per_question=12, cap_per_work=60):
    jr = j.build_judge("mock")(text)
    results, warnings = s.build_results(
        jr,
        text_len=len(text),
        cap_per_question=cap_per_question,
        cap_per_work=cap_per_work,
        prompt_fingerprint=j.fingerprint_prompt(),
    )
    words = len(text.split())
    return s.compose_envelope(
        target_path="work.txt", target_words=words, results=results,
        warnings=warnings or None,
    )


# Two clean same-question pairs, in document order.
TWO_PAIRS = (
    "The essay opens with a survey of the field. "
    "[[pair=p1 side=a q=What is the author's position on market regulation?]] "
    "Markets work best when left entirely free. "
    "[[pair=p1 side=b q=What is the author's position on market regulation?]] "
    "Some regulation is plainly necessary to prevent abuse. "
    "[[pair=p2 side=a q=How should the transition be funded?]] "
    "The transition can be funded through existing revenue. "
    "[[pair=p2 side=b q=How should the transition be funded?]] "
    "A new levy would be required to fund the transition. "
    "The essay closes here."
)


# ----------------------------------------------------------------------
# Surface registration (mirrors compression :86-95).
# ----------------------------------------------------------------------

def test_surface_registered():
    assert s.TASK_SURFACE == "position_pair_register"
    assert "position_pair_register" in VALID_TASK_SURFACES
    assert "position_pair_register" in TASK_SURFACE_LABELS


def test_surface_fragment_file_is_source_of_truth():
    frag = _SCRIPTS / "claim_license_surfaces" / "position_pair_register.txt"
    assert frag.exists()


# ----------------------------------------------------------------------
# (1) positive: two valid pairs, document order, well-formed loci.
# ----------------------------------------------------------------------

def test_positive_two_pairs_document_order_and_loci():
    env = _mock_envelope(TWO_PAIRS)
    assert env["available"] is True
    assert env["task_surface"] == "position_pair_register"
    assert env["schema_version"] == "1.0"
    pairs = env["results"]["pairs"]
    assert len(pairs) == 2
    # Ascending document order by passage-A start offset.
    a_starts = [p["a"]["start_char"] for p in pairs]
    assert a_starts == sorted(a_starts)
    # Each pair carries a question + two well-formed loci (doc/start/end/quote).
    for p in pairs:
        assert set(p.keys()) == {"question", "a", "b"}
        for side in ("a", "b"):
            locus = p[side]
            assert set(locus.keys()) == {"doc", "start_char", "end_char", "quote"}
            assert locus["doc"] == s.DOC_LABEL
            assert isinstance(locus["start_char"], int)
            assert isinstance(locus["end_char"], int)
            assert locus["start_char"] <= locus["end_char"]
            # The quote is a VERBATIM substring at the claimed offsets.
            assert TWO_PAIRS[locus["start_char"]:locus["end_char"]] == locus["quote"]
    assert env["results"]["pairs_refused_q_gate"] == 0
    assert env["results"]["pairs_dropped_cap"] == 0
    assert env["results"]["calibration_status"] == "uncalibrated"


# ----------------------------------------------------------------------
# (2) F3 runtime banned-key gate — RAISES; + #298-shaped recursive walk test.
# ----------------------------------------------------------------------

def test_injected_relation_key_raises_at_compose():
    """A banned RELATION key injected into a crafted results dict makes
    compose_envelope / _assert_no_banned_keys RAISE before the envelope escapes."""
    good = _mock_envelope(TWO_PAIRS)
    poisoned = dict(good["results"])
    # Inject a `stance` key on the first pair (the exact channel the sibling judge
    # emits and this surface strips).
    poisoned_pairs = [dict(p) for p in poisoned["pairs"]]
    poisoned_pairs[0]["stance"] = "for"
    poisoned["pairs"] = poisoned_pairs
    with pytest.raises(s.BannedKeyError):
        s.compose_envelope(
            target_path="work.txt", target_words=50, results=poisoned,
        )


def test_injected_relation_key_at_top_level_raises():
    """A relation key anywhere in the envelope (not just under pairs) is caught by
    the whole-envelope relation walk."""
    good = _mock_envelope(TWO_PAIRS)
    poisoned = dict(good["results"])
    poisoned["conflict_count"] = 2  # relation key at the results top level
    with pytest.raises(s.BannedKeyError):
        s.compose_envelope(
            target_path="work.txt", target_words=50, results=poisoned,
        )


def test_generic_verdict_key_in_pairs_raises():
    """A generic verdict key (label/score/verdict/...) inside results.pairs is
    caught by the payload-scoped walk, even though the same key is legitimate in
    envelope metadata."""
    good = _mock_envelope(TWO_PAIRS)
    poisoned = dict(good["results"])
    poisoned_pairs = [dict(p) for p in poisoned["pairs"]]
    poisoned_pairs[0]["score"] = 0.9
    poisoned["pairs"] = poisoned_pairs
    with pytest.raises(s.BannedKeyError):
        s.compose_envelope(
            target_path="work.txt", target_words=50, results=poisoned,
        )


def test_envelope_carries_no_verdict_keys_recursive():
    """The #298-shaped recursive walk over the REAL mock envelope: no relation /
    verdict key anywhere. (Cite PR #298's test for the walk SHAPE; the stance set
    is net-new.)"""
    env = _mock_envelope(TWO_PAIRS)
    banned = (
        "contradiction", "contradicts", "opposes", "opposition", "conflict",
        "conflicting", "tension", "stance", "polarity", "agreement",
        "disagreement", "inconsistent", "inconsistency", "verdict",
        "relation",
    )

    def walk(obj, prefix=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                path = f"{prefix}.{k}" if prefix else str(k)
                low = str(k).lower()
                for b in banned:
                    assert b not in low, f"forbidden key {b!r} at {path}"
                walk(v, path)
        elif isinstance(obj, (list, tuple)):
            for i, item in enumerate(obj):
                walk(item, f"{prefix}[{i}]")

    walk(env)


# ----------------------------------------------------------------------
# (3) banned-vocab Q → pair REFUSED + warned + counted.
# ----------------------------------------------------------------------

def test_banned_vocab_question_is_refused():
    text = (
        "Intro. "
        "[[pair=p1 side=a q=How does chapter two conflict with chapter nine?]] "
        "Chapter two argues for X. "
        "[[pair=p1 side=b q=How does chapter two conflict with chapter nine?]] "
        "Chapter nine argues against X. "
        "End."
    )
    env = _mock_envelope(text)
    assert env["results"]["pairs"] == []
    assert env["results"]["pairs_refused_q_gate"] == 1
    reasons = env["results"]["pairs_refused_q_gate_reasons"]
    assert len(reasons) == 1
    assert "conflict" in reasons[0]["reason"]
    assert any("Q-gate" in w for w in env["warnings"])


# ----------------------------------------------------------------------
# (4) non-interrogative Q → REFUSED.
# ----------------------------------------------------------------------

def test_non_interrogative_question_is_refused():
    text = (
        "Intro. "
        "[[pair=p1 side=a q=The relationship between X and Y.]] "
        "Passage about X. "
        "[[pair=p1 side=b q=The relationship between X and Y.]] "
        "Passage about Y. "
        "End."
    )
    env = _mock_envelope(text)
    assert env["results"]["pairs"] == []
    assert env["results"]["pairs_refused_q_gate"] == 1
    assert "interrogative" in env["results"]["pairs_refused_q_gate_reasons"][0]["reason"]


def test_loaded_question_passes_syntax_gate():
    """The interrogative-form gate is SYNTAX ONLY — a loaded/presuppositional Q
    passes both checks (the human terminus, not the form gate, is the guarantee)."""
    assert s._gate_question("Why does the author abandon X in chapter nine?") is None


# ----------------------------------------------------------------------
# (6) over-cap → first-by-locus survive, dropped counted + loci logged.
# ----------------------------------------------------------------------

def test_over_cap_keeps_document_order_and_discloses():
    # Three pairs on the SAME question; cap_per_question=1 keeps the first by order.
    text = (
        "Intro. "
        "[[pair=p1 side=a q=What is the author's view on X?]] First A. "
        "[[pair=p1 side=b q=What is the author's view on X?]] First B. "
        "[[pair=p2 side=a q=What is the author's view on X?]] Second A. "
        "[[pair=p2 side=b q=What is the author's view on X?]] Second B. "
        "[[pair=p3 side=a q=What is the author's view on X?]] Third A. "
        "[[pair=p3 side=b q=What is the author's view on X?]] Third B. "
        "End."
    )
    env = _mock_envelope(text, cap_per_question=1)
    pairs = env["results"]["pairs"]
    assert len(pairs) == 1
    assert env["results"]["pairs_dropped_cap"] == 2
    dropped = env["results"]["pairs_dropped_cap_loci"]
    assert len(dropped) == 2
    # The survivor is the FIRST by document order (lowest a.start_char).
    survivor_start = pairs[0]["a"]["start_char"]
    dropped_starts = [d["a"]["start_char"] for d in dropped]
    assert all(survivor_start < ds for ds in dropped_starts)
    assert any("cap" in w for w in env["warnings"])


# ----------------------------------------------------------------------
# (8) claim-license round-trip (four F10 refusals) + calibration_status.
# ----------------------------------------------------------------------

def test_claim_license_four_refusals_round_trip():
    env = _mock_envelope(TWO_PAIRS)
    dnl = env["claim_license"]["does_not_license"].lower()
    # (a) NOT that the passages ARE in conflict.
    assert "conflict" in dnl and ("no relation" in dnl or "asserts no relation" in dnl)
    # (b) NOT which passage is correct.
    assert "which passage is correct" in dnl or "which passage is right" in dnl
    # (c) NOT exhaustive — absence of a pair is not consistency.
    assert "not exhaustive" in dnl or "absence of a pair" in dnl
    # (d) NOT fiction / narrator.
    assert "fiction" in dnl
    # The syntax-only honesty downgrade is stated.
    assert "syntax only" in dnl or "syntax-only" in dnl
    # calibration_status carried in results.
    assert env["results"]["calibration_status"] == "uncalibrated"


def test_claim_license_cites_both_arxiv_ids():
    env = _mock_envelope(TWO_PAIRS)
    refs = " ".join(env["claim_license"]["references"])
    assert "2311.09182" in refs
    assert "2603.23848" in refs


# ----------------------------------------------------------------------
# (7) paraphrased/hallucinated quote → dropped by the judge's span validator.
# ----------------------------------------------------------------------

def test_manifest_out_of_range_span_is_dropped():
    """A manifest pair whose span is out of range is dropped by validate_pairs
    (skip-and-warn), so a fabricated locus never reaches the human as evidence."""
    text = "A short document with some words in it for the span check."
    payload = {
        "pairs": [
            {
                "question": "What is the point?",
                "a": {"start_char": 0, "end_char": 5, "quote": "A sho"},
                # end_char beyond text_len → dropped.
                "b": {"start_char": 0, "end_char": 99999, "quote": "nope"},
            }
        ]
    }
    pairs, warns = j.validate_pairs(payload, text=text)
    assert pairs == []
    assert any("out of range" in w for w in warns)


def test_manifest_in_range_fabricated_quote_is_dropped():
    """THE Codex P1 repro: a pair whose span is IN RANGE but whose quote is text
    the judge INVENTED (present nowhere in the document) is dropped — a fabricated
    quote must never reach the human as 'verbatim evidence'."""
    text = "A short document with some words in it for the span check."
    payload = {
        "pairs": [
            {
                "question": "What is the point?",
                # Side a is honest (verbatim).
                "a": {"start_char": 0, "end_char": 5, "quote": "A sho"},
                # Side b: span in range, but the quote is fabricated — nowhere in text.
                "b": {"start_char": 6, "end_char": 20,
                      "quote": "wholly invented sentence"},
            }
        ]
    }
    pairs, warns = j.validate_pairs(payload, text=text)
    assert pairs == []
    assert any(
        ("verbatim" in w and "fabricated" in w) for w in warns
    ), warns


def test_manifest_wrong_offsets_are_retightened():
    """A pair with a CORRECT quote but WRONG offsets (off by a few chars) is KEPT,
    with its span RE-TIGHTENED so text[start:end] == quote — the quote is real
    document text, just mis-located; we correct the pointer rather than drop it."""
    text = "A short document with some words in it for the span check."
    a_quote = "A short"
    b_quote = "some words"
    a_true = text.index(a_quote)
    b_true = text.index(b_quote)
    payload = {
        "pairs": [
            {
                "question": "What is the point?",
                # Offsets deliberately off by a few chars; quotes are verbatim.
                "a": {"start_char": a_true + 2, "end_char": a_true + 2 + len(a_quote),
                      "quote": a_quote},
                "b": {"start_char": b_true - 1, "end_char": b_true - 1 + len(b_quote),
                      "quote": b_quote},
            }
        ]
    }
    pairs, warns = j.validate_pairs(payload, text=text)
    assert len(pairs) == 1
    p = pairs[0]
    # Re-tightened: the emitted offsets now index the verbatim quote.
    assert text[p.a_start_char:p.a_end_char] == p.a_quote == a_quote
    assert p.a_start_char == a_true
    assert text[p.b_start_char:p.b_end_char] == p.b_quote == b_quote
    assert p.b_start_char == b_true


def test_retighten_binds_overlapping_occurrence_not_later_duplicate():
    """THE Codex round-2 P2 repro: the quote appears TWICE; the claimed start falls
    a few chars INSIDE the intended (earlier) occurrence. An at-or-after search
    skips the overlapping intended occurrence and binds to the later duplicate —
    nearest-occurrence binding must snap BACK to the overlapping one."""
    dup = "the levy funds the transition entirely."
    text = (
        "Opening remarks. First claim: " + dup +
        " Later, restated verbatim for emphasis: " + dup + " Closing."
    )
    first = text.index(dup)
    second = text.index(dup, first + 1)
    assert first < second
    payload = {
        "pairs": [
            {
                "question": "How should the transition be funded?",
                # Claimed start is 3 chars INSIDE the first occurrence (overlap,
                # begins before the claimed start) — must bind to `first`, not `second`.
                "a": {"start_char": first + 3, "end_char": first + 3 + len(dup),
                      "quote": dup},
                "b": {"start_char": 0, "end_char": len("Opening remarks."),
                      "quote": "Opening remarks."},
            }
        ]
    }
    pairs, warns = j.validate_pairs(payload, text=text)
    assert len(pairs) == 1
    assert pairs[0].a_start_char == first, (pairs[0].a_start_char, first, second)
    assert text[pairs[0].a_start_char:pairs[0].a_end_char] == dup


def test_pair_collapsing_onto_same_passage_is_dropped():
    """The degenerate-pair guard: if both sides resolve to the SAME span (duplicate
    quotes collapsing onto one passage), the pair asserts nothing and is dropped —
    a pair must point at two distinct passages."""
    text = "Alpha statement here. Filler between the two. Omega statement ends."
    q = "Alpha statement here."
    true_at = text.index(q)
    payload = {
        "pairs": [
            {
                "question": "What is stated?",
                # Both sides carry the same quote with slightly-off offsets — both
                # re-tighten to the identical span.
                "a": {"start_char": true_at, "end_char": true_at + len(q), "quote": q},
                "b": {"start_char": true_at + 2, "end_char": true_at + 2 + len(q),
                      "quote": q},
            }
        ]
    }
    pairs, warns = j.validate_pairs(payload, text=text)
    assert pairs == []
    assert any("same passage" in w for w in warns), warns


# ----------------------------------------------------------------------
# CLI happy path + error envelopes.
# ----------------------------------------------------------------------

def _run(argv, tmp_path):
    out_path = tmp_path / "env.json"
    rc = s.main(argv + ["--json", "--out", str(out_path)])
    env = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else None
    return rc, env


def test_cli_happy_path_manifest(tmp_path):
    target = tmp_path / "work.txt"
    target.write_text(
        "Intro passage. Markets work best when left free. "
        "Some regulation is necessary. Closing passage.",
        encoding="utf-8",
    )
    manifest = tmp_path / "pairs.json"
    # Offsets into the target text above.
    body = target.read_text(encoding="utf-8")
    a0 = body.index("Markets")
    a1 = a0 + len("Markets work best when left free.")
    b0 = body.index("Some regulation")
    b1 = b0 + len("Some regulation is necessary.")
    manifest.write_text(json.dumps({
        "pairs": [
            {
                "question": "What is the author's position on regulation?",
                "a": {"start_char": a0, "end_char": a1, "quote": body[a0:a1]},
                "b": {"start_char": b0, "end_char": b1, "quote": body[b0:b1]},
            }
        ],
        "judge_identity": {"model": "test-fixture"},
    }), encoding="utf-8")
    rc, env = _run([str(target), "--judge", "manifest",
                    "--judge-manifest", str(manifest)], tmp_path)
    assert rc == 0
    assert env["available"] is True
    assert len(env["results"]["pairs"]) == 1
    assert env["results"]["pairs"][0]["question"].endswith("?")


def test_cli_missing_target_is_bad_input(tmp_path):
    rc, env = _run([str(tmp_path / "nope.txt"), "--judge", "mock"], tmp_path)
    assert rc == 3
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"


def test_cli_manifest_missing_manifest_path_fails_loud(tmp_path):
    target = tmp_path / "work.txt"
    target.write_text("Some words here for the target.", encoding="utf-8")
    # --judge manifest with no --judge-manifest → JudgeError → parser.error (exit 2).
    with pytest.raises(SystemExit) as ei:
        s.main([str(target), "--judge", "manifest"])
    assert ei.value.code != 0


def test_cli_markdown_default(tmp_path):
    target = tmp_path / "work.txt"
    target.write_text(TWO_PAIRS, encoding="utf-8")
    out = tmp_path / "report.md"
    rc = s.main([str(target), "--judge", "mock", "--out", str(out)])
    assert rc == 0
    md = out.read_text(encoding="utf-8")
    assert "Position-Pair Register" in md
    assert "asserts NO relation" in md
    assert "Claim license" in md


# ----------------------------------------------------------------------
# Registration + drift + golden.
# ----------------------------------------------------------------------

def test_capability_entry_and_golden_present():
    tools_dir = _REPO_ROOT / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    import check_capabilities_drift as drift  # type: ignore

    report = drift.check_drift()
    assert report.passed, (
        "capabilities drift detected:\n"
        + "\n".join(v.render() for v in report.violations)
    )
    manifest = drift.load_manifest(drift.DEFAULT_MANIFEST)
    entry = next(
        (e for e in manifest["entries"] if e.get("id") == "position_pair_register"),
        None,
    )
    assert entry is not None, "position_pair_register missing from capabilities.d"
    assert entry["surface"] == "position_pair_register"
    # Adopted as an apodictic consumer surface (stance PR 2 ripple): the R1
    # normalized-entrypoint bundle is now present so the surface can be vendored
    # under a pinned tag + drift gate.
    assert entry["consumers"] == ["apodictic"]
    assert entry["min_setec_version"] == "1.121.0"
    assert entry["json_delivery"] == "stdout"
    assert entry["family"] == "argument-consistency"
    assert entry["compute"]["tier"] == "api_llm"
    assert entry["dependencies"]["python"] == []

    golden = _HERE / "_golden_capabilities" / "position_pair_register.json"
    assert golden.exists()
    assert json.loads(golden.read_text(encoding="utf-8")) == entry


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
