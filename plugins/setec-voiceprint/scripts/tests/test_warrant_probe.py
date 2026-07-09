"""Tests for warrant_probe.py (ArgScope M2) + warrant_judge.py.

Torch-free (mock judge + a monkeypatched build_judge for missing-SDK). The
central contract is the no-verdict POSTURE: coverage, never soundness.
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
import warrant_judge  # type: ignore
import warrant_probe  # type: ignore

SAMPLE = (
    "Everyone already knows the policy works, so anyone who doubts it simply "
    "hasn't been paying attention. Because the experts all agree, there is no "
    "real debate to be had here.\n\n"
    "We face a stark choice: either we adopt this reform in full, or we accept "
    "the total collapse of the system. Therefore the only responsible vote is yes."
)

_FORBIDDEN_EXACT = {"soundness", "verdict", "is_bad", "score", "quality", "unsound",
                    "warrant_score", "coverage_score"}


def _run(tmp_path, text=SAMPLE, *args):
    target = tmp_path / "arg.txt"
    target.write_text(text, encoding="utf-8")
    out = tmp_path / "arg.json"
    argv = [str(target), "--out", str(out), "--out-md", str(tmp_path / "arg.md"), *args]
    if "--judge" not in args:                 # --judge is now REQUIRED; tests opt into the mock stub
        argv += ["--judge", "mock"]
    rc = warrant_probe.main(argv)
    env = json.loads(out.read_text(encoding="utf-8"))
    return rc, env


def _results(env):
    return env.get("results", env)


def test_mock_coverage_shape(tmp_path):
    rc, env = _run(tmp_path)
    assert rc == 0 and env["available"] is True
    r = _results(env)
    claims = r["warrant_coverage"]
    assert len(claims) == 2
    for c in claims:
        assert set(c) == {"claim_span", "paragraph_index", "critical_questions"}
        cqs = c["critical_questions"]
        assert set(cqs) == {"warrant", "backing", "rebuttal"}
        assert all(v in warrant_judge.CQ_STATUSES for v in cqs.values())
    # coverage_summary is a per-question count rollup, not a score
    summ = r["coverage_summary"]
    assert set(summ) == {"warrant", "backing", "rebuttal"}
    assert summ["rebuttal"]["absent"] == 2  # mock pattern: both claims rebuttal=absent
    assert r["calibration_status"] == "uncalibrated"


def test_no_verdict_data_shape(tmp_path):
    _, env = _run(tmp_path)
    r = _results(env)
    for k in r:
        assert k not in _FORBIDDEN_EXACT, f"forbidden results key: {k}"
        assert "soundness" not in k and "score" not in k and "unsound" not in k, k
    assert isinstance(r["coverage_summary"], dict)


def test_claim_license_refuses_verdict(tmp_path):
    _, env = _run(tmp_path)
    dnl = env["claim_license"]["does_not_license"].lower()
    for word in ("unsound", "weak", "bad"):
        assert word in dnl
    assert "coverage gap" in dnl
    assert "uncalibrated" in dnl


def test_own_prompt_fingerprint(tmp_path):
    _, env = _run(tmp_path)
    fp = _results(env)["prompt_fingerprint_sha256"]
    assert fp == warrant_judge.fingerprint_prompt()
    # distinct from BOTH sibling judges (own-prompt discipline across the family)
    assert fp != fallacy_judge.fingerprint_prompt()
    assert fp != argument_judge.fingerprint_prompt()


def test_register_warning_is_soft_not_abstain(tmp_path):
    text = (
        "Cats are nice animals. Dogs are nice animals. Birds are nice animals. "
        "Fish are nice animals. Rabbits are nice animals. Horses are nice animals. "
        "Lizards are nice animals. Turtles are nice animals as well in my view."
    )
    rc, env = _run(tmp_path, text)
    assert rc == 0 and env["available"] is True
    assert _results(env)["register_warnings"]


def test_short_input_bad_input(tmp_path):
    _, env = _run(tmp_path, "too short here")
    assert env["available"] is False
    assert "bad_input" in json.dumps(env)


def test_fingerprint_drift_abstains(tmp_path):
    _, env = _run(tmp_path, SAMPLE, "--expect-fingerprint", "deadbeef")
    assert env["available"] is False
    assert "drift" in json.dumps(env).lower()


def test_stale_manifest_fingerprint_caught_by_drift_gate(tmp_path):
    # recorded-field-without-verifier: a manifest's coverage was produced under ITS prompt, so the drift
    # gate must check the MANIFEST's stored fingerprint, not the current code's. Otherwise a stale
    # manifest passes a current-vs-current comparison and the envelope mislabels its provenance as the
    # current fingerprint. Mirrors argquality's regression guard (Codex #243).
    current = warrant_judge.fingerprint_prompt()
    stale = "stale" + "0" * 59
    assert stale != current
    manifest = tmp_path / "claims.json"
    manifest.write_text(json.dumps({
        "values": {"claims": [
            {"claim_span": "Everyone already knows the policy works",
             "paragraph_index": 0,
             "critical_questions": {"warrant": "present", "backing": "partial",
                                    "rebuttal": "absent"}},
        ]},
        "judge_identity": {"model": "precomputed-x", "prompt_fingerprint_sha256": stale},
    }), encoding="utf-8")
    margs = ["--judge", "manifest", "--judge-manifest", str(manifest)]

    # Expecting the CURRENT code fp must ABSTAIN — the stale manifest was NOT produced under it.
    _, env = _run(tmp_path, SAMPLE, "--expect-fingerprint", current, *margs)
    assert env["available"] is False and "drift" in json.dumps(env).lower()

    # Expecting the manifest's OWN fingerprint passes, and the envelope reports THAT fingerprint.
    _, env = _run(tmp_path, SAMPLE, "--expect-fingerprint", stale, *margs)
    assert env["available"] is True, env
    assert _results(env)["prompt_fingerprint_sha256"] == stale
    assert _results(env)["judge"]["judge_identity"]["prompt_fingerprint_sha256"] == stale


def test_manifest_without_fingerprint_is_not_rebound(tmp_path):
    # A manifest that declares NO prompt fingerprint must NOT be rebound to the current code's
    # fingerprint — the envelope reports None (honest "no provenance"), and any --expect-fingerprint
    # can't be confirmed, so the gate abstains (fail-closed).
    current = warrant_judge.fingerprint_prompt()
    manifest = tmp_path / "claims.json"
    manifest.write_text(json.dumps({
        "values": {"claims": [
            {"claim_span": "Everyone already knows the policy works",
             "paragraph_index": 0,
             "critical_questions": {"warrant": "present", "backing": "partial",
                                    "rebuttal": "absent"}},
        ]},
        "judge_identity": {"model": "precomputed-x"},   # NO prompt_fingerprint_sha256
    }), encoding="utf-8")
    margs = ["--judge", "manifest", "--judge-manifest", str(manifest)]

    # No --expect-fingerprint: succeeds, but the envelope must NOT claim current_fp (no false rebind).
    _, env = _run(tmp_path, SAMPLE, *margs)
    assert env["available"] is True, env
    assert _results(env)["prompt_fingerprint_sha256"] is None
    assert _results(env)["prompt_fingerprint_sha256"] != current

    # With --expect-fingerprint set, a fingerprint-less manifest can't be confirmed -> abstain.
    _, env = _run(tmp_path, SAMPLE, "--expect-fingerprint", current, *margs)
    assert env["available"] is False and "drift" in json.dumps(env).lower()


def test_missing_sdk_is_missing_dependency(tmp_path, monkeypatch):
    def _raise(*a, **k):
        raise warrant_probe.JudgeError(
            "openai backend requires the `openai` SDK; pip install first."
        )
    monkeypatch.setattr(warrant_probe, "build_judge", _raise)
    _, env = _run(tmp_path, SAMPLE, "--judge", "openai", "--judge-model", "x")
    assert env["available"] is False
    assert "missing_dependency" in json.dumps(env)


def test_normalize_claims_drops_malformed_status_and_hallucinated():
    paras = ["paragraph zero has a real claim and a second real claim stated plainly here"]
    full = {"warrant": "present", "backing": "absent", "rebuttal": "absent"}
    kept = warrant_judge.normalize_claims(
        [
            {"claim_span": "real claim", "paragraph_index": 0,                       # #230 malformed status
             "critical_questions": {"warrant": "present", "backing": "BOGUS", "rebuttal": "absent"}},
            {"claim_span": "real claim", "paragraph_index": 0,                       # #230 missing rebuttal
             "critical_questions": {"warrant": "present", "backing": "absent"}},
            {"claim_span": "  ", "paragraph_index": 0, "critical_questions": full},  # empty span
            {"claim_span": "x", "paragraph_index": 9, "critical_questions": full},   # out of range
            {"claim_span": "a claim the judge never quoted", "paragraph_index": 0,   # #229 hallucinated
             "critical_questions": full},
            {"claim_span": "second real claim", "paragraph_index": 0,                # valid -> kept
             "critical_questions": {"warrant": "present", "backing": "partial", "rebuttal": "absent"}},
        ],
        paras,
    )
    # the malformed-status claims are DROPPED (not coerced to a fabricated 'absent' gap); only the
    # all-valid claim survives.
    assert len(kept) == 1
    assert kept[0]["claim_span"] == "second real claim"
    assert kept[0]["critical_questions"] == {"warrant": "present", "backing": "partial",
                                             "rebuttal": "absent"}


def test_mock_judge_deterministic():
    j = warrant_judge.build_judge("mock")
    a = j(["one two three four", "five six seven eight"]).values["claims"]
    b = j(["one two three four", "five six seven eight"]).values["claims"]
    assert a == b and len(a) == 2


def test_judge_is_required(tmp_path):
    # #229 (mirrored): no --judge default — a bare run must NOT fall back to the fabricating mock.
    target = tmp_path / "arg.txt"; target.write_text(SAMPLE, encoding="utf-8")
    try:
        warrant_probe.main([str(target), "--out", str(tmp_path / "o.json")])   # no --judge
        raise AssertionError("expected SystemExit (--judge required)")
    except SystemExit as e:
        assert e.code == 2


def test_invalid_utf8_target_is_bad_input(tmp_path):
    # self-audit (the #225/#226 lesson): invalid UTF-8 must be bad_input, not a traceback.
    target = tmp_path / "bad.txt"; target.write_bytes(b"\xff\xfe not utf-8 \x80\x81")
    out = tmp_path / "o.json"
    rc = warrant_probe.main([str(target), "--judge", "mock", "--out", str(out)])
    env = json.loads(out.read_text(encoding="utf-8"))
    assert env["available"] is False and "bad_input" in json.dumps(env)


# ----------------- agent_host host-delegated judge (spec 35 follow-on) ----------
import contextlib  # noqa: E402
import os  # noqa: E402


@contextlib.contextmanager
def _host_transport(fn):
    """Inject a STUB host transport (no live host, no key) — the spec-35 seam."""
    jb = warrant_judge.judge_backends
    prev = jb._HOST_JUDGE_OVERRIDE
    saved = {k: os.environ.pop(k, None) for k in ("SETEC_HOST_JUDGE", "SETEC_HOST_JUDGE_CMD")}
    jb._HOST_JUDGE_OVERRIDE = fn
    try:
        yield
    finally:
        jb._HOST_JUDGE_OVERRIDE = prev
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_agent_host_build_judge_resolves_without_model():
    assert "agent_host" in warrant_judge.judge_backends.PROVIDERS
    with _host_transport(lambda req: json.dumps({"claims": []})):
        j = warrant_judge.build_judge("agent_host", model=None)
        assert callable(j)
        res = j(SAMPLE.split("\n\n"))
    ident = res.judge_identity
    assert ident["kind"] == "agent_host"
    assert ident["model"] == "host-resolved"
    assert ident["host"] == "agent_host" and ident["delegated"] is True
    with pytest.raises(warrant_judge.JudgeError):
        warrant_judge.build_judge("anthropic", model=None)  # gate unchanged


def test_agent_host_in_audit_choices_default_unchanged():
    parser = warrant_probe.build_arg_parser()
    judge_action = next(a for a in parser._actions if "--judge" in a.option_strings)
    assert "agent_host" in judge_action.choices
    assert judge_action.required is True and judge_action.default is None


def test_agent_host_end_to_end_via_stub(tmp_path):
    with _host_transport(lambda req: json.dumps({"claims": []})):
        rc, env = _run(tmp_path, SAMPLE, "--judge", "agent_host")
    assert rc == 0 and env["available"] is True
    r = _results(env)
    assert r["judge"]["judge_identity"]["kind"] == "agent_host"
    assert r["calibration_status"] == "uncalibrated"
    cl = env["claim_license"]
    caveat = [c for c in cl["additional_caveats"]
              if "`agent_host`" in c and "HOST runtime" in c]
    assert caveat, "expected the agent_host caveat"
    assert "NON-DETERMINISTIC" in caveat[0]
    assert "specs/35-host-delegated-judge.md" in caveat[0]
    assert cl["comparison_set"]["judge_host"] == "agent_host"


def test_mock_run_has_no_agent_host_caveat_and_null_host(tmp_path):
    _, env = _run(tmp_path)  # default mock
    cl = env["claim_license"]
    assert not [c for c in cl["additional_caveats"] if "HOST runtime" in c]
    assert cl["comparison_set"].get("judge_host") is None
