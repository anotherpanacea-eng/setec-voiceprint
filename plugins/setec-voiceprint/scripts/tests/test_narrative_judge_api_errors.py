#!/usr/bin/env python3
"""PR #128 review (P2): provider API errors must escape as JudgeError.

The reference API adapters in narrative_judge.py previously only
wrapped non-JSON parse failures as JudgeError. Any SDK-level
exception from `client.messages.create()` (anthropic),
`client.chat.completions.create()` (openai), or
`client.models.generate_content()` (gemini) propagated as a raw
traceback past `main()`'s error-handling block.

Tests here plant fake-SDK modules into `sys.modules` so the
backend factories pick them up via their lazy `import`. Each fake
client raises an exception when called; the test asserts the
exception is repackaged as JudgeError with a clear message.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_fake(module_name: str, module: types.ModuleType):
    sys.modules[module_name] = module


def _uninstall_fake(module_name: str):
    sys.modules.pop(module_name, None)


def test_anthropic_provider_error_wrapped_as_judge_error():
    fake = types.ModuleType("anthropic")

    class FakeMessages:
        def create(self, **_kwargs):
            raise RuntimeError("simulated anthropic 5xx")

    class FakeClient:
        def __init__(self, *a, **kw):
            self.messages = FakeMessages()

    fake.Anthropic = FakeClient
    _install_fake("anthropic", fake)
    try:
        import narrative_judge as nj  # type: ignore
        judge = nj.build_judge(
            "anthropic", model="claude-test",
        )
        try:
            judge("story")
        except nj.JudgeError as exc:
            assert "anthropic provider call failed" in str(exc)
            assert "simulated anthropic 5xx" in str(exc)
        else:
            raise AssertionError(
                "expected JudgeError; provider exception escaped"
            )
    finally:
        _uninstall_fake("anthropic")


def test_openai_provider_error_wrapped_as_judge_error():
    fake = types.ModuleType("openai")

    class FakeCompletions:
        def create(self, **_kwargs):
            raise RuntimeError("simulated openai timeout")

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    class FakeClient:
        def __init__(self, *a, **kw):
            self.chat = FakeChat()

    fake.OpenAI = FakeClient
    _install_fake("openai", fake)
    try:
        import importlib
        import narrative_judge as nj  # type: ignore
        importlib.reload(nj)  # re-pick fake module
        judge = nj.build_judge(
            "openai", model="gpt-test",
        )
        try:
            judge("story")
        except nj.JudgeError as exc:
            assert "openai provider call failed" in str(exc)
            assert "simulated openai timeout" in str(exc)
        else:
            raise AssertionError(
                "expected JudgeError; provider exception escaped"
            )
    finally:
        _uninstall_fake("openai")


def test_gemini_provider_error_wrapped_as_judge_error():
    fake_genai = types.ModuleType("genai")

    class FakeModels:
        def generate_content(self, **_kwargs):
            raise RuntimeError("simulated gemini 429")

    class FakeClient:
        def __init__(self, *a, **kw):
            self.models = FakeModels()

    fake_genai.Client = FakeClient
    fake_google = types.ModuleType("google")
    fake_google.genai = fake_genai
    _install_fake("google", fake_google)
    _install_fake("google.genai", fake_genai)
    # Gemini backend needs GOOGLE_API_KEY in env
    import os
    prior = os.environ.get("GOOGLE_API_KEY")
    os.environ["GOOGLE_API_KEY"] = "test-key"
    try:
        import importlib
        import narrative_judge as nj  # type: ignore
        importlib.reload(nj)
        judge = nj.build_judge(
            "gemini", model="gemini-test",
        )
        try:
            judge("story")
        except nj.JudgeError as exc:
            assert "gemini provider call failed" in str(exc)
            assert "simulated gemini 429" in str(exc)
        else:
            raise AssertionError(
                "expected JudgeError; provider exception escaped"
            )
    finally:
        _uninstall_fake("google.genai")
        _uninstall_fake("google")
        if prior is None:
            os.environ.pop("GOOGLE_API_KEY", None)
        else:
            os.environ["GOOGLE_API_KEY"] = prior


if __name__ == "__main__":
    import traceback
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception:
                print(f"FAIL {name}")
                traceback.print_exc()
