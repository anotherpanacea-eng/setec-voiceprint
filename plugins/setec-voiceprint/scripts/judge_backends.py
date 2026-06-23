"""judge_backends.py — shared API-judge provider plumbing.

The two pluggable LLM-judge families (`narrative_judge`, `argument_judge`)
keep their OWN data contracts — distinct `JudgeResult` shapes, prompts,
response parsing, and `JudgeError` classes. That decoupling is deliberate
(the families must stay independent). What they previously DUPLICATED, and
what lives here, is the provider plumbing: the lazy SDK import, client
construction, the per-provider request/response shape, response-text
extraction, and the `JudgeError` wrapping.

A family builds an API judge by calling :func:`make_api_judge` with its own
``build_user_content`` (how the prompt + target become the user message),
``build_result`` (how a parsed payload becomes the family's `JudgeResult`),
its `JudgeError` class, and its `extract_json` helper. Behavior is identical
to the hand-written per-family adapters this replaces: the provider *call* is
wrapped as ``"<provider> provider call failed"``, response-text extraction is
left unwrapped (a malformed SDK response surfaces as its native error, as
before), and a non-JSON body is wrapped as ``"<provider> judge returned
non-JSON"``.

Stdlib-only at import (the SDK imports stay lazy, inside the factory).
"""

from __future__ import annotations

import os
from typing import Any, Callable

__all__ = [
    "PROVIDERS",
    "make_api_judge",
    "NON_CONCRETE_JUDGE_MODELS",
    "JudgeDisjointnessError",
    "judge_identity_is_concrete",
    "assert_judge_generator_disjoint",
]

# `agent_host` delegates the judgment to the HOST agent runtime's model (Claude Code /
# Codex / Gemini Antigravity) via a host-registered transport — no API key. See
# specs/35-host-delegated-judge.md. NOTE: this tuple is the single source of truth read
# by voice_verifier.py; the audit families (argument/narrative) read it too (M1).
PROVIDERS = ("anthropic", "openai", "gemini", "agent_host")

# A judge model recorded as one of these sentinels is NOT a concrete model identity —
# nobody named the model the host actually used. The "host-resolved" placeholder
# (judge_backends defaults it when the host doesn't expose an id) is the headline case.
# A non-concrete identity CANNOT satisfy the judge-model != generator-model disjointness
# firewall — it must FAIL CLOSED, never silently pass (Codex P1, spec 35).
NON_CONCRETE_JUDGE_MODELS = frozenset({"host-resolved", "(unspecified)", "unknown", ""})


class JudgeDisjointnessError(RuntimeError):
    """A judge identity cannot be PROVEN disjoint from the generator model.

    Raised fail-closed on a disjointness-required (holdout / selection-critical) path
    either because the judge model is a non-concrete sentinel (so disjointness is
    unprovable) or because the concrete judge model equals the generator model.
    """


def judge_identity_is_concrete(judge_identity: dict | None) -> bool:
    """True iff ``judge_identity`` names a CONCRETE model the firewall can reason about.

    A missing identity, a missing/blank model, or a non-concrete sentinel (e.g. the
    ``host-resolved`` placeholder) is NOT concrete — disjointness from a generator
    cannot be established, so callers on a disjointness-required path must fail closed.
    """
    if not judge_identity:
        return False
    model = judge_identity.get("model")
    if not isinstance(model, str):
        return False
    return model.strip() not in NON_CONCRETE_JUDGE_MODELS


def assert_judge_generator_disjoint(
    judge_identity: dict | None, generator_model: str | None
) -> str:
    """Fail CLOSED unless the judge model is concrete AND differs from the generator.

    The producer-side counterpart to the consumer drift gate spec 35 names: where a
    judge is routed into a HOLDOUT validator or a selection signal, the judge model
    must be provably ``!=`` the generator model. This refuses two ways the firewall
    can be defeated:

    * a non-concrete judge identity (the ``host-resolved`` placeholder, a blank model)
      — disjointness is *unprovable*, so it must not pass; and
    * a concrete judge model equal to ``generator_model`` — the generator grading its
      own homework, the exact circularity the firewall exists to block.

    Returns the concrete judge model on success (so a caller can record what it
    enforced against). Raises :class:`JudgeDisjointnessError` otherwise.
    """
    if not judge_identity_is_concrete(judge_identity):
        recorded = (judge_identity or {}).get("model")
        raise JudgeDisjointnessError(
            "judge model is not a concrete identity "
            f"(recorded as {recorded!r}); a host-delegated judge that does not name "
            "the model it used CANNOT be proven disjoint from the generator — "
            "refusing on a disjointness-required (holdout/selection) path. Have the "
            "host report its model (structured transport response or SETEC_HOST_MODEL)."
        )
    judge_model = judge_identity["model"].strip()  # type: ignore[index]
    if generator_model is not None and judge_model == generator_model.strip():
        raise JudgeDisjointnessError(
            f"judge model {judge_model!r} == generator model — the generator would "
            "grade its own output; refusing on a disjointness-required path."
        )
    return judge_model


# (user_prompt, judge_input) -> user-message string
BuildUserContent = Callable[[str, Any], str]
# (payload, raw_text, judge_identity, judge_input) -> family JudgeResult
BuildResult = Callable[[dict, str, dict], Any]
# raw model text -> parsed object (raises ValueError on a bad / non-object body)
ExtractJson = Callable[[str], dict]

# Host-delegated judge transport: (request_dict) -> judgment JSON text. The host
# runtime registers one; tests inject `_HOST_JUDGE_OVERRIDE`. Resolution order:
# in-process override, `SETEC_HOST_JUDGE="module:function"`, `SETEC_HOST_JUDGE_CMD`.
HostJudge = Callable[[dict], str]
_HOST_JUDGE_OVERRIDE: "HostJudge | None" = None


def _resolve_host_judge(judge_error: type[Exception]) -> "HostJudge":
    """Resolve the host judge transport, or raise ``judge_error`` with a hint."""
    if _HOST_JUDGE_OVERRIDE is not None:
        return _HOST_JUDGE_OVERRIDE
    entry = os.environ.get("SETEC_HOST_JUDGE")
    if entry:
        import importlib
        mod_name, sep, fn_name = entry.partition(":")
        if not sep or not mod_name or not fn_name:
            raise judge_error(
                "SETEC_HOST_JUDGE must be 'module:function' (a callable "
                "request->json-text the host registers)."
            )
        try:
            return getattr(importlib.import_module(mod_name), fn_name)
        except Exception as exc:  # noqa: BLE001
            raise judge_error(f"SETEC_HOST_JUDGE {entry!r} did not resolve: {exc}") from exc
    cmd = os.environ.get("SETEC_HOST_JUDGE_CMD")
    if cmd:
        import json as _json
        import subprocess

        def _cmd_transport(request: dict) -> str:
            proc = subprocess.run(
                cmd, shell=True, input=_json.dumps(request),
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                raise judge_error(
                    f"SETEC_HOST_JUDGE_CMD failed (exit {proc.returncode}): "
                    f"{proc.stderr.strip()[:200]}"
                )
            return proc.stdout

        return _cmd_transport
    raise judge_error(
        "agent_host backend needs a host transport: register one in-process "
        "(judge_backends._HOST_JUDGE_OVERRIDE) or set SETEC_HOST_JUDGE='module:function' "
        "or SETEC_HOST_JUDGE_CMD. In an agent runtime (Claude Code / Codex / Gemini "
        "Antigravity) this is the host's subagent/MCP-sampling adapter — no API key needed."
    )


def make_api_judge(
    provider: str,
    *,
    model: str,
    system_preamble: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    build_user_content: BuildUserContent,
    build_result: BuildResult,
    judge_error: type[Exception],
    extract_json: ExtractJson,
) -> Callable[[Any], Any]:
    """Construct an API-backed judge callable for ``provider``.

    The SDK is imported and the client constructed eagerly here (so a missing
    SDK / bad credentials fail at build time, wrapped as ``judge_error``); the
    returned callable runs one request per call. ``build_result`` is invoked as
    ``build_result(payload, raw_text, judge_identity, judge_input)``.
    """
    client, call, read = _provider_setup(
        provider,
        model=model,
        system_preamble=system_preamble,
        temperature=temperature,
        max_tokens=max_tokens,
        judge_error=judge_error,
    )

    def _run(judge_input: Any) -> Any:
        content = build_user_content(user_prompt, judge_input)
        try:
            response = call(content)
        except Exception as exc:  # noqa: BLE001 — any SDK error is a call failure
            raise judge_error(f"{provider} provider call failed: {exc}") from exc
        text, identity_extras = read(response)
        try:
            payload = extract_json(text)
        except ValueError as exc:
            raise judge_error(f"{provider} judge returned non-JSON: {exc}") from exc
        identity = {"kind": provider, "model": model, **identity_extras}
        return build_result(payload, text, identity, judge_input)

    return _run


def _provider_setup(
    provider: str,
    *,
    model: str,
    system_preamble: str,
    temperature: float,
    max_tokens: int,
    judge_error: type[Exception],
) -> tuple[Any, Callable[[str], Any], Callable[[Any], tuple[str, dict]]]:
    """Return ``(client, call, read)`` for ``provider``.

    ``call(content)`` issues the request; ``read(response)`` returns
    ``(text, identity_extras)``. Raises ``judge_error`` on a missing SDK or a
    failed client construction.
    """
    if provider == "anthropic":
        try:
            import anthropic  # type: ignore
        except ImportError as exc:
            raise judge_error(
                "anthropic backend requires the `anthropic` SDK; "
                "`pip install anthropic` first."
            ) from exc
        try:
            client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from env
        except Exception as exc:  # noqa: BLE001
            raise judge_error(f"anthropic client construction failed: {exc}") from exc

        def call(content: str) -> Any:
            return client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_preamble,
                messages=[{"role": "user", "content": content}],
            )

        def read(msg: Any) -> tuple[str, dict]:
            text = "".join(
                block.text
                for block in msg.content
                if getattr(block, "type", None) == "text"
            )
            return text, {"stop_reason": getattr(msg, "stop_reason", None)}

        return client, call, read

    if provider == "openai":
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:
            raise judge_error(
                "openai backend requires the `openai` SDK; "
                "`pip install openai` first."
            ) from exc
        try:
            client = OpenAI()  # OPENAI_API_KEY from env
        except Exception as exc:  # noqa: BLE001
            raise judge_error(f"openai client construction failed: {exc}") from exc

        def call(content: str) -> Any:
            return client.chat.completions.create(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_preamble},
                    {"role": "user", "content": content},
                ],
            )

        def read(resp: Any) -> tuple[str, dict]:
            text = resp.choices[0].message.content or ""
            return text, {"finish_reason": resp.choices[0].finish_reason}

        return client, call, read

    if provider == "gemini":
        try:
            from google import genai  # type: ignore
        except ImportError as exc:
            raise judge_error(
                "gemini backend requires the `google-genai` SDK; "
                "`pip install google-genai` first."
            ) from exc
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise judge_error(
                "gemini backend requires GOOGLE_API_KEY or "
                "GEMINI_API_KEY in the environment."
            )
        try:
            client = genai.Client(api_key=api_key)
        except Exception as exc:  # noqa: BLE001
            raise judge_error(f"gemini client construction failed: {exc}") from exc

        def call(content: str) -> Any:
            return client.models.generate_content(
                model=model,
                contents=[{"role": "user", "parts": [{"text": f"{system_preamble}\n\n{content}"}]}],
                config={
                    "temperature": temperature,
                    "max_output_tokens": max_tokens,
                    "response_mime_type": "application/json",
                },
            )

        def read(resp: Any) -> tuple[str, dict]:
            return (resp.text or ""), {}

        return client, call, read

    if provider == "agent_host":
        # Delegate to the host runtime's model via a registered transport (no API key).
        # Resolved eagerly here so a missing transport fails at build time (like a missing
        # SDK), wrapped as judge_error. The transport returns the model's JSON text; the
        # rest of the pipeline (extract_json / build_result / non-JSON wrapping) is shared.
        host_judge = _resolve_host_judge(judge_error)
        host_id = os.environ.get("SETEC_HOST", "agent_host")
        # The host may name the concrete model it used (the disjointness firewall needs
        # it). A structured transport response wins; failing that, `SETEC_HOST_MODEL`.
        env_model = os.environ.get("SETEC_HOST_MODEL")
        env_revision = os.environ.get("SETEC_HOST_MODEL_REVISION")

        def call(content: str) -> Any:
            return host_judge({
                "system": system_preamble,
                "content": content,
                "response_format": "json_object",
                "no_verdict": (
                    "Return ONLY a JSON object with the requested fields. Do NOT emit a "
                    "same/different-author or AI/human verdict — this is descriptive, "
                    "no-verdict labeling."
                ),
                "temperature": temperature,
                "max_tokens": max_tokens,
            })

        def read(resp: Any) -> tuple[str, dict]:
            # A transport may return bare judgment text, or a structured envelope
            # {text|content|judgment, model, revision} that NAMES the concrete model
            # the host used. A concrete model overrides the "host-resolved" placeholder
            # (it lands in judge_identity.model — see make_api_judge's identity merge),
            # so a consumer can enforce judge model != generator model. The placeholder
            # stays only when nobody names a concrete model (fail-closed at the gate).
            extras: dict = {"delegated": True, "host": host_id}
            if isinstance(resp, dict):
                text = (
                    resp.get("text")
                    if resp.get("text") is not None
                    else resp.get("content")
                    if resp.get("content") is not None
                    else resp.get("judgment", "")
                )
                text = text if isinstance(text, str) else str(text)
                reported_model = resp.get("model")
                reported_revision = resp.get("revision") or resp.get("model_revision")
            else:
                text = resp if isinstance(resp, str) else str(resp)
                reported_model = None
                reported_revision = None
            model_id = reported_model or env_model
            revision_id = reported_revision or env_revision
            if model_id:
                # overrides make_api_judge's placeholder `model` (extras win the merge)
                extras["model"] = model_id
            if revision_id:
                extras["model_revision"] = revision_id
            return text, extras

        return host_judge, call, read

    raise judge_error(f"unknown api judge provider: {provider!r}")
