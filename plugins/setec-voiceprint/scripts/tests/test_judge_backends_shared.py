#!/usr/bin/env python3
"""Regression: both judge families resolve to the one shared provider backend.

`narrative_judge` and `argument_judge` previously each carried their own
near-identical `_api_judge_{anthropic,openai,gemini}` adapters; that
duplication let the #193 manifest/_extract_json hardening land in one family
and not the other (the parity gap fixed in #194). The provider plumbing now
lives once in `judge_backends.make_api_judge`. These tests pin that both
families use the single shared object and that no per-family adapter crept
back — mirroring the `tools/r1_bundle.py` dedup's "one shared validator object"
regression. The families' DATA contracts (JudgeResult shapes, prompts, parsing,
JudgeError classes) stay deliberately separate; this only pins the plumbing.
See issue #198.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import argument_judge as aj  # type: ignore
import judge_backends  # type: ignore
import narrative_judge as nj  # type: ignore


def test_both_families_bind_the_one_shared_backend():
    assert nj.judge_backends is judge_backends
    assert aj.judge_backends is judge_backends
    # The shared factory is a single object, not a per-family copy.
    assert nj.judge_backends.make_api_judge is aj.judge_backends.make_api_judge


def test_no_per_family_api_adapter_remains():
    # The duplicated _api_judge_* adapters were removed in favor of the factory.
    for mod in (nj, aj):
        leftover = [n for n in dir(mod) if n.startswith("_api_judge_")]
        assert leftover == [], f"{mod.__name__} still defines {leftover}"


def test_unknown_provider_raises_the_callers_judge_error():
    # The shared factory raises the FAMILY's JudgeError (passed in), preserving
    # each family's error contract rather than introducing a third type.
    try:
        judge_backends.make_api_judge(
            "bogus-provider",
            model="m",
            system_preamble="",
            user_prompt="",
            temperature=0.0,
            max_tokens=16,
            build_user_content=lambda _u, _i: "",
            build_result=lambda *_a: None,
            judge_error=nj.JudgeError,
            extract_json=lambda _t: {},
        )
    except nj.JudgeError as exc:
        assert "unknown api judge provider" in str(exc)
    else:
        raise AssertionError("expected JudgeError for an unknown provider")


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
