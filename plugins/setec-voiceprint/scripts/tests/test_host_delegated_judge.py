#!/usr/bin/env python3
"""spec 35 — host-delegated (`agent_host`) judge backend.

The judge tier can run key-free by delegating the judgment to the HOST agent
runtime's model (Claude Code / Codex / Gemini Antigravity) via a registered
transport, instead of an API call. These tests pin the M1 seam against a STUB
transport (no live host, no key), mirroring how the API providers are CI-tested
without hitting a real SDK. See specs/35-host-delegated-judge.md.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import argument_judge as aj  # type: ignore
import judge_backends  # type: ignore
import narrative_judge as nj  # type: ignore

# This module must be importable by name so a SETEC_HOST_JUDGE='module:function'
# entrypoint can resolve a helper defined here (mirrors how a host registers one).
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
_THIS_MODULE = Path(__file__).stem  # "test_host_delegated_judge"


def canned_host_judgment(request: dict) -> str:
    """Module-level entrypoint helper resolvable via SETEC_HOST_JUDGE. Returns a
    schema-valid judgment JSON regardless of the request (the M1 stub contract)."""
    return json.dumps({"label": "resolved-via-entrypoint"})


@contextlib.contextmanager
def host_transport(fn):
    """Inject a stub host transport for the duration of the block, and clear any
    env-based resolution so the override is the only path."""
    prev = judge_backends._HOST_JUDGE_OVERRIDE
    saved_env = {k: os.environ.pop(k, None) for k in ("SETEC_HOST_JUDGE", "SETEC_HOST_JUDGE_CMD")}
    judge_backends._HOST_JUDGE_OVERRIDE = fn
    try:
        yield
    finally:
        judge_backends._HOST_JUDGE_OVERRIDE = prev
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v


def _make(provider, transport_or_none, *, build_result, extract_json=json.loads, model="host-resolved"):
    return judge_backends.make_api_judge(
        provider,
        model=model,
        system_preamble="sys",
        user_prompt="prompt",
        temperature=0.0,
        max_tokens=16,
        build_user_content=lambda _u, i: str(i),
        build_result=build_result,
        judge_error=nj.JudgeError,
        extract_json=extract_json,
    )


# 1. provider registered in the single source of truth
def test_agent_host_in_providers():
    assert "agent_host" in judge_backends.PROVIDERS


# 2. end-to-end via stub (no host, no key): build + run returns build_result output
def test_agent_host_runs_via_stub():
    with host_transport(lambda req: json.dumps({"label": "ok"})):
        judge = _make("agent_host", None, build_result=lambda payload, raw, identity, ji: payload)
        out = judge("the-judge-input")
    assert out == {"label": "ok"}


# 3. identity / provenance recorded: kind=agent_host, delegated, host, model
def test_identity_records_delegation():
    with host_transport(lambda req: json.dumps({})):
        judge = _make("agent_host", None, build_result=lambda payload, raw, identity, ji: identity)
        ident = judge("x")
    assert ident["kind"] == "agent_host"
    assert ident["delegated"] is True
    assert "host" in ident
    assert ident["model"] == "host-resolved"


# 3b. the transport actually receives the request the host model needs
def test_transport_receives_request_fields():
    seen = {}

    def _t(req):
        seen.update(req)
        return json.dumps({})

    with host_transport(_t):
        _make("agent_host", None, build_result=lambda *a: None)("the-input")
    assert seen["system"] == "sys"
    assert seen["content"] == "the-input"
    assert "no_verdict" in seen  # the no-verdict instruction is carried to the host


# 4. unresolved transport -> graceful JudgeError with a registration hint
def test_unresolved_transport_raises_judge_error():
    prev = judge_backends._HOST_JUDGE_OVERRIDE
    saved = {k: os.environ.pop(k, None) for k in ("SETEC_HOST_JUDGE", "SETEC_HOST_JUDGE_CMD")}
    judge_backends._HOST_JUDGE_OVERRIDE = None
    try:
        _make("agent_host", None, build_result=lambda *a: None)
        raise AssertionError("expected JudgeError for an unresolved host transport")
    except nj.JudgeError as exc:
        assert "host transport" in str(exc)
    finally:
        judge_backends._HOST_JUDGE_OVERRIDE = prev
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


# 5. non-JSON host body -> wrapped like the API providers
def test_non_json_host_body_wrapped():
    with host_transport(lambda req: "not json at all"):
        judge = _make("agent_host", None, build_result=lambda *a: None)
        try:
            judge("x")
            raise AssertionError("expected JudgeError for a non-JSON host body")
        except nj.JudgeError as exc:
            assert "agent_host judge returned non-JSON" in str(exc)


# 6. both families' build_judge accept agent_host WITHOUT --judge-model (review [P2]·5)
def test_families_build_agent_host_without_model():
    with host_transport(lambda req: json.dumps({})):
        for mod in (aj, nj):
            judge = mod.build_judge("agent_host", model=None)
            assert callable(judge), f"{mod.__name__}.build_judge('agent_host') should resolve"
        # and the non-host providers still REQUIRE a model (gate unchanged)
        for mod in (aj, nj):
            try:
                mod.build_judge("anthropic", model=None)
                raise AssertionError(f"{mod.__name__} anthropic should require --judge-model")
            except mod.JudgeError as exc:
                assert "requires --judge-model" in str(exc)


# 7. unknown kind still rejected (the widened gate didn't open everything)
def test_unknown_kind_still_rejected():
    for mod in (aj, nj):
        try:
            mod.build_judge("bogus", model="m")
            raise AssertionError(f"{mod.__name__} should reject an unknown kind")
        except mod.JudgeError as exc:
            assert "unknown judge kind" in str(exc)


# 8. import stays stdlib (no host SDK / transport pulled at import)
def test_import_stays_stdlib():
    # importing judge_backends must not require a host transport or any SDK.
    assert hasattr(judge_backends, "_resolve_host_judge")
    assert judge_backends._HOST_JUDGE_OVERRIDE is None  # default: nothing registered


# --- Codex P1 (judge_backends.py:277): record the ACTUAL delegated model, and make
# the placeholder fail the disjointness firewall CLOSED ---------------------------


# 9. structured provenance: when the host REPORTS a concrete model, it overrides the
# "host-resolved" placeholder in judge_identity.model (so a consumer can read it).
def test_host_reported_model_overrides_placeholder():
    def _structured(req):
        return {"text": json.dumps({}), "model": "claude-opus-4-8", "revision": "2026-01"}

    with host_transport(_structured):
        judge = _make("agent_host", None, build_result=lambda payload, raw, identity, ji: identity)
        ident = judge("x")
    assert ident["kind"] == "agent_host"
    # the placeholder must NOT survive once the host names its model
    assert ident["model"] == "claude-opus-4-8"
    assert ident.get("model_revision") == "2026-01"
    # and the payload text is still extracted from the structured response
    out = None
    with host_transport(_structured):
        out = _make("agent_host", None, build_result=lambda payload, *a: payload)("x")
    assert out == {}


# 9b. SETEC_HOST_MODEL env carries the host-reported concrete identity when the
# transport returns bare text (no structured model field).
def test_host_model_env_overrides_placeholder():
    prev = judge_backends._HOST_JUDGE_OVERRIDE
    saved_env = {
        k: os.environ.pop(k, None)
        for k in ("SETEC_HOST_JUDGE", "SETEC_HOST_JUDGE_CMD", "SETEC_HOST_MODEL")
    }
    judge_backends._HOST_JUDGE_OVERRIDE = lambda req: json.dumps({})
    os.environ["SETEC_HOST_MODEL"] = "gpt-5.4"
    try:
        judge = _make("agent_host", None, build_result=lambda payload, raw, identity, ji: identity)
        ident = judge("x")
        assert ident["model"] == "gpt-5.4"
    finally:
        judge_backends._HOST_JUDGE_OVERRIDE = prev
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)


# 10. FAIL-CLOSED: the bare "host-resolved" placeholder is an UNKNOWN concrete model
# and MUST NOT satisfy the judge!=generator disjointness firewall.
def test_placeholder_fails_disjointness_closed():
    placeholder_identity = {"kind": "agent_host", "model": "host-resolved", "host": "claude-code"}
    try:
        judge_backends.assert_judge_generator_disjoint(placeholder_identity, "any-generator")
        raise AssertionError("placeholder must NOT pass a disjointness-required check")
    except judge_backends.JudgeDisjointnessError as exc:
        assert "host-resolved" in str(exc) or "concrete" in str(exc)
    # the predicate agrees: a placeholder is not a concrete identity
    assert judge_backends.judge_identity_is_concrete(placeholder_identity) is False


# 10b. a SAME-MODEL host (concrete judge identity == generator model) is DETECTABLE
# from the emitted judge_identity and REFUSED by the disjointness check.
def test_same_model_host_refused():
    same = {"kind": "agent_host", "model": "claude-opus-4-8", "host": "claude-code"}
    try:
        judge_backends.assert_judge_generator_disjoint(same, "claude-opus-4-8")
        raise AssertionError("a judge model == generator model must be refused")
    except judge_backends.JudgeDisjointnessError as exc:
        assert "claude-opus-4-8" in str(exc)
    # a genuinely disjoint concrete identity passes and returns the concrete model
    disjoint = {"kind": "agent_host", "model": "claude-opus-4-8", "host": "claude-code"}
    assert (
        judge_backends.assert_judge_generator_disjoint(disjoint, "gpt-5.4")
        == "claude-opus-4-8"
    )
    assert judge_backends.judge_identity_is_concrete(disjoint) is True


# 10c. FAIL-CLOSED on a MISSING generator identity: disjointness cannot be PROVEN
# without the other identity, so a concrete judge paired with a None / blank /
# whitespace-only generator MUST be refused (symmetric to the judge-side check),
# never silently returned as if it were disjoint (Codex P1 round-2).
def test_missing_generator_fails_disjointness_closed():
    concrete_judge = {"kind": "openai", "model": "gpt-5.4"}
    # None generator: identity unknown, disjointness unprovable -> REFUSE
    try:
        judge_backends.assert_judge_generator_disjoint(concrete_judge, None)
        raise AssertionError("a None generator must NOT pass a disjointness-required check")
    except judge_backends.JudgeDisjointnessError as exc:
        assert "generator" in str(exc)
    # whitespace-only generator: a non-concrete identity -> REFUSE (same sentinel logic)
    for blank in ("", "   ", "\t", "\n"):
        try:
            judge_backends.assert_judge_generator_disjoint(concrete_judge, blank)
            raise AssertionError(f"a blank generator {blank!r} must be refused")
        except judge_backends.JudgeDisjointnessError as exc:
            assert "generator" in str(exc)
    # a genuinely distinct concrete generator still PASSES and returns the judge model
    assert (
        judge_backends.assert_judge_generator_disjoint(concrete_judge, "claude-opus-4-8")
        == "gpt-5.4"
    )


# --- RESOLVED transport: the two real resolution paths, not just the override --------
#     (round-round #35: prior tests only exercised the unresolved branch + the in-process
#      override; these pin the SETEC_HOST_JUDGE entrypoint and SETEC_HOST_JUDGE_CMD subprocess
#      transports actually resolving and round-tripping a judgment, plus the new timeout wrap.)


def _clear_host_env():
    """Snapshot + clear the host-resolution env so a test controls the path.
    Returns the saved mapping for restore in teardown (mirrors host_transport)."""
    return {
        k: os.environ.pop(k, None)
        for k in ("SETEC_HOST_JUDGE", "SETEC_HOST_JUDGE_CMD", "SETEC_HOST_JUDGE_TIMEOUT")
    }


def _restore_host_env(prev_override, saved):
    judge_backends._HOST_JUDGE_OVERRIDE = prev_override
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


# 11. entrypoint transport: SETEC_HOST_JUDGE='module:function' resolves to a real helper,
#     and a judgment round-trips through it (no override, no key).
def test_entrypoint_transport_resolves_and_round_trips():
    prev = judge_backends._HOST_JUDGE_OVERRIDE
    saved = _clear_host_env()
    judge_backends._HOST_JUDGE_OVERRIDE = None
    os.environ["SETEC_HOST_JUDGE"] = f"{_THIS_MODULE}:canned_host_judgment"
    try:
        transport = judge_backends._resolve_host_judge(nj.JudgeError)
        assert json.loads(transport({}))["label"] == "resolved-via-entrypoint"
        judge = _make("agent_host", None, build_result=lambda payload, raw, identity, ji: payload)
        assert judge("x") == {"label": "resolved-via-entrypoint"}
    finally:
        _restore_host_env(prev, saved)


# 12. subprocess transport: SETEC_HOST_JUDGE_CMD (a real stdlib command) resolves and round-trips.
def test_subprocess_transport_resolves_and_round_trips():
    prev = judge_backends._HOST_JUDGE_OVERRIDE
    saved = _clear_host_env()
    judge_backends._HOST_JUDGE_OVERRIDE = None
    os.environ["SETEC_HOST_JUDGE_CMD"] = (
        sys.executable
        + " -c 'import sys, json; sys.stdin.read(); "
        + "print(json.dumps({\"label\": \"resolved-via-cmd\"}))'"
    )
    try:
        transport = judge_backends._resolve_host_judge(nj.JudgeError)
        assert json.loads(transport({"content": "x"}))["label"] == "resolved-via-cmd"
        judge = _make("agent_host", None, build_result=lambda payload, raw, identity, ji: payload)
        assert judge("x") == {"label": "resolved-via-cmd"}
    finally:
        _restore_host_env(prev, saved)


# 13. subprocess timeout: a hung host command surfaces as a JudgeError (family error),
#     never a bare subprocess.TimeoutExpired traceback (spec 35 [35a]).
def test_subprocess_transport_timeout_surfaces_as_judge_error():
    import subprocess

    prev = judge_backends._HOST_JUDGE_OVERRIDE
    saved = _clear_host_env()
    judge_backends._HOST_JUDGE_OVERRIDE = None
    os.environ["SETEC_HOST_JUDGE_CMD"] = f"{sys.executable} -c 'pass'"
    real_run = subprocess.run

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="stub-host-cmd", timeout=kwargs.get("timeout"))

    subprocess.run = _raise_timeout
    try:
        transport = judge_backends._resolve_host_judge(nj.JudgeError)
        try:
            transport({"content": "x"})
            raise AssertionError("expected JudgeError when the host command times out")
        except nj.JudgeError as exc:
            assert "timed out" in str(exc)
    finally:
        subprocess.run = real_run
        _restore_host_env(prev, saved)


if __name__ == "__main__":
    import traceback
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    sys.exit(1 if failed else 0)
