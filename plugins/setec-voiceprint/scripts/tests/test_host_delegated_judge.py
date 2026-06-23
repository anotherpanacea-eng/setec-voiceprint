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
