"""Tests for fallacy_scan.py (ArgScope M1) + fallacy_judge.py.

Torch-free: the LLM judge is exercised only through the deterministic `mock`
backend (and a monkeypatched build_judge for the missing-SDK case). The central
contract is the no-verdict POSTURE — enforced in the data shape, not just prose.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import argument_judge  # type: ignore
import fallacy_judge  # type: ignore
import fallacy_scan  # type: ignore

SAMPLE = (
    "Everyone already knows the policy works, so anyone who doubts it simply "
    "hasn't been paying attention. Because the experts all agree, there is no "
    "real debate to be had here.\n\n"
    "We face a stark choice: either we adopt this reform in full, or we accept "
    "the total collapse of the system. Therefore the only responsible vote is yes."
)

# Keys that would smuggle a verdict into the data shape — the operator's hard line.
_FORBIDDEN_EXACT = {"fallacy_tally", "fallacy_spans", "soundness", "verdict",
                    "is_bad", "score", "quality"}


def _run(tmp_path, text=SAMPLE, *args):
    target = tmp_path / "arg.txt"
    target.write_text(text, encoding="utf-8")
    out = tmp_path / "arg.json"
    argv = [str(target), "--out", str(out), "--out-md", str(tmp_path / "arg.md"), *args]
    if "--judge" not in args:                 # --judge is now REQUIRED; tests opt into the mock stub
        argv += ["--judge", "mock"]
    rc = fallacy_scan.main(argv)
    env = json.loads(out.read_text(encoding="utf-8"))
    return rc, env


def _results(env):
    # success envelope nests results; error envelope is flat-ish — handle both.
    return env.get("results", env)


# ----------------- happy path: shape -------------------------------
def test_mock_flags_shape(tmp_path):
    rc, env = _run(tmp_path)
    assert rc == 0 and env["available"] is True
    r = _results(env)
    flags = r["rhetorical_move_flags"]
    assert len(flags) == 2  # mock flags ¶0 + ¶1
    for f in flags:
        assert set(f) == {"candidate_type", "paragraph_index", "span_text", "reconstruction"}
        assert f["candidate_type"] in fallacy_judge.FALLACY_TYPES
        assert isinstance(f["paragraph_index"], int)
        assert f["span_text"] and f["span_text"] in SAMPLE.split("\n\n")[f["paragraph_index"]]
    assert r["candidate_pattern_tally"] == {"appeal_to_emotion": 1, "false_dilemma": 1}
    assert r["calibration_status"] == "uncalibrated"
    assert r["n_flags"] == 2


# ----------------- the no-verdict DATA-SHAPE guard -----------------
def test_no_verdict_data_shape(tmp_path):
    _, env = _run(tmp_path)
    r = _results(env)
    for k in r:
        assert k not in _FORBIDDEN_EXACT, f"forbidden results key: {k}"
        assert not k.startswith("fallacy"), f"no fallacy_* key allowed: {k}"
        assert "soundness" not in k and "score" not in k, f"verdict-ish key: {k}"
    # the tally is a rollup of candidate flags, never an aggregate/verdict number
    assert isinstance(r["candidate_pattern_tally"], dict)


def test_claim_license_refuses_verdict(tmp_path):
    _, env = _run(tmp_path)
    cl = env["claim_license"]
    dnl = cl["does_not_license"].lower()
    assert "legitimate in context" in dnl
    for word in ("fallacious", "unsound", "weak", "bad"):
        assert word in dnl
    assert "uncalibrated" in dnl


# ----------------- provenance: OWN fingerprint ---------------------
def test_own_prompt_fingerprint(tmp_path):
    _, env = _run(tmp_path)
    r = _results(env)
    fp = r["prompt_fingerprint_sha256"]
    assert fp == fallacy_judge.fingerprint_prompt()
    # MUST differ from argument_judge's role/mode prompt (the P1c fix) — else a
    # drift gate keyed to this surface would silently hash the wrong prompt.
    assert fp != argument_judge.fingerprint_prompt()
    assert r["judge"]["judge_identity"]["kind"] == "mock"


def test_mock_labelled_as_stub(tmp_path):
    _, env = _run(tmp_path)
    assert any("mock" in w and "stub" in w.lower() for w in env.get("warnings", []))


# ----------------- caveats / abstention ----------------------------
def test_register_warning_is_soft_not_abstain(tmp_path):
    # Above the 25-word hard floor but no inferential connectives -> soft
    # register_warnings, still available (NOT a hard register abstain).
    text = (
        "Cats are nice animals. Dogs are nice animals. Birds are nice animals. "
        "Fish are nice animals. Rabbits are nice animals. Horses are nice animals. "
        "Lizards are nice animals. Turtles are nice animals as well in my view."
    )
    rc, env = _run(tmp_path, text)
    assert rc == 0 and env["available"] is True
    assert _results(env)["register_warnings"], "expected a soft register caveat"


def test_short_input_bad_input(tmp_path):
    rc, env = _run(tmp_path, "too short here")
    assert env["available"] is False
    assert json.dumps(env).find("bad_input") != -1


def test_fingerprint_drift_abstains(tmp_path):
    rc, env = _run(tmp_path, SAMPLE, "--expect-fingerprint", "deadbeef")
    assert env["available"] is False
    assert "drift" in json.dumps(env).lower()


def test_missing_sdk_is_missing_dependency(tmp_path, monkeypatch):
    def _raise(*a, **k):
        raise fallacy_scan.JudgeError(
            "anthropic backend requires the `anthropic` SDK; pip install first."
        )
    monkeypatch.setattr(fallacy_scan, "build_judge", _raise)
    rc, env = _run(tmp_path, SAMPLE, "--judge", "anthropic", "--judge-model", "x")
    assert env["available"] is False
    assert "missing_dependency" in json.dumps(env)


# ----------------- judge unit: normalize_flags ---------------------
def test_normalize_flags_drops_invalid_and_hallucinated():
    paras = ["paragraph zero has some words", "paragraph one contains a real span inside it"]
    kept = fallacy_judge.normalize_flags(
        [
            {"candidate_type": "NOPE", "paragraph_index": 1, "span_text": "real span"},        # bad type
            {"candidate_type": "ad_hominem", "paragraph_index": 9, "span_text": "real span"},  # out of range
            {"candidate_type": "ad_hominem", "paragraph_index": 1, "span_text": "  "},          # empty
            {"candidate_type": "ad_hominem", "paragraph_index": 1,
             "span_text": "a span the judge never quoted"},                                     # #229: hallucinated
            {"candidate_type": "ad_hominem", "paragraph_index": 1, "span_text": "real span"},   # valid (substring)
        ],
        paras,
    )
    assert len(kept) == 1
    assert kept[0]["candidate_type"] == "ad_hominem" and kept[0]["paragraph_index"] == 1
    assert kept[0]["span_text"] == "real span"


def test_normalize_flags_tolerates_requoted_whitespace():
    # a real judge may requote with different whitespace — containment is whitespace-normalized,
    # so a genuine (if reflowed) quote survives while a hallucination still does not.
    paras = ["the experts\n  all agree, so there is no debate"]
    kept = fallacy_judge.normalize_flags(
        [{"candidate_type": "ad_populum", "paragraph_index": 0, "span_text": "the experts all agree"}],
        paras,
    )
    assert len(kept) == 1


def test_mock_judge_deterministic():
    j = fallacy_judge.build_judge("mock")
    a = j(["one two three four", "five six seven eight"]).values["flags"]
    b = j(["one two three four", "five six seven eight"]).values["flags"]
    assert a == b
    assert [f["candidate_type"] for f in a] == ["appeal_to_emotion", "false_dilemma"]


def test_judge_is_required(tmp_path):
    # #229: no --judge default. A bare run must NOT silently fall back to the fabricating mock.
    target = tmp_path / "arg.txt"; target.write_text(SAMPLE, encoding="utf-8")
    try:
        fallacy_scan.main([str(target), "--out", str(tmp_path / "o.json")])   # no --judge
        raise AssertionError("expected SystemExit (--judge required)")
    except SystemExit as e:
        assert e.code == 2


def test_invalid_utf8_target_is_bad_input(tmp_path):
    # self-audit (the #225/#226 lesson): invalid UTF-8 must be bad_input, not a traceback.
    target = tmp_path / "bad.txt"; target.write_bytes(b"\xff\xfe not utf-8 \x80\x81")
    out = tmp_path / "o.json"
    rc = fallacy_scan.main([str(target), "--judge", "mock", "--out", str(out)])
    env = json.loads(out.read_text(encoding="utf-8"))
    assert env["available"] is False and "bad_input" in json.dumps(env)
