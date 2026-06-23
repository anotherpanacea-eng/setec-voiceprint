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

__all__ = ["PROVIDERS", "make_api_judge"]

# `agent_host` delegates the judgment to the HOST agent runtime's model (Claude Code /
# Codex / Gemini Antigravity) via a host-registered transport — no API key. See
# specs/35-host-delegated-judge.md. NOTE: this tuple is the single source of truth read
# by voice_verifier.py; the audit families (argument/narrative) read it too (M1).
PROVIDERS = ("anthropic", "openai", "gemini", "agent_host")

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
            text = resp if isinstance(resp, str) else str(resp)
            return text, {"delegated": True, "host": host_id}

        return host_judge, call, read

    raise judge_error(f"unknown api judge provider: {provider!r}")
