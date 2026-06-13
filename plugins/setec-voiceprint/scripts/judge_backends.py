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

PROVIDERS = ("anthropic", "openai", "gemini")

# (user_prompt, judge_input) -> user-message string
BuildUserContent = Callable[[str, Any], str]
# (payload, raw_text, judge_identity, judge_input) -> family JudgeResult
BuildResult = Callable[[dict, str, dict], Any]
# raw model text -> parsed object (raises ValueError on a bad / non-object body)
ExtractJson = Callable[[str], dict]


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

    raise judge_error(f"unknown api judge provider: {provider!r}")
