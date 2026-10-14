"""setec_hermetic_backend=stub refuses at the judge, embedding and surprisal boundaries.

Each test checks the refusal happens before anything a real run would touch
(provider setup, a cached model, a dependency import), and that the mode off
leaves the existing path alone. The subprocess cases run the real CLIs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

import embedding_backend as eb  # type: ignore
import judge_backends  # type: ignore
import narrative_judge as nj  # type: ignore
import surprisal_backend as sb  # type: ignore

SCRIPTS = Path(__file__).resolve().parents[1]
MODE = "setec_hermetic_backend"


def _make_judge(provider: str):
    return judge_backends.make_api_judge(
        provider, model="synthetic-model", system_preamble="", user_prompt="",
        temperature=0.0, max_tokens=16, build_user_content=lambda _u, _i: "",
        build_result=lambda *_a: None, judge_error=nj.JudgeError,
        extract_json=lambda _t: {},
    )


def _tripwire(*_a, **_k):
    raise AssertionError("tripwire: provider setup reached")


@pytest.mark.parametrize("provider", judge_backends.PROVIDERS)
def test_api_judge_refuses_before_provider_setup(monkeypatch, provider):
    monkeypatch.setattr(judge_backends, "_provider_setup", _tripwire)
    monkeypatch.setenv(MODE, "stub")
    with pytest.raises(nj.JudgeError, match=f"{MODE}=stub: the {provider} API judge"):
        _make_judge(provider)
    monkeypatch.setenv(MODE, "off")
    with pytest.raises(AssertionError, match="tripwire"):
        _make_judge(provider)


class _PoisonModule(types.ModuleType):
    def __getattr__(self, name):
        raise AssertionError(f"tripwire: {self.__name__}.{name} touched")


@pytest.mark.parametrize("cached", [True, False])
def test_embedding_loader_refuses_before_cache_or_import(monkeypatch, cached):
    monkeypatch.setitem(sys.modules, "sentence_transformers", _PoisonModule("sentence_transformers"))
    backend = eb.EmbeddingBackend(model_id="mxbai")
    sentinel = object()
    if cached:
        backend._model = sentinel
    monkeypatch.setenv(MODE, "stub")
    with pytest.raises(eb.EmbeddingBackendError, match=f"{MODE}=stub"):
        backend._load()
    monkeypatch.delenv(MODE)
    if cached:
        assert backend._load() is sentinel


@pytest.mark.parametrize("cached", [True, False])
def test_surprisal_loader_refuses_before_cache_or_import(monkeypatch, cached):
    monkeypatch.setitem(sys.modules, "transformers", _PoisonModule("transformers"))
    backend = sb.SurprisalBackend(model_id="gpt2")
    model, tokenizer = object(), object()
    if cached:
        backend._model, backend._tokenizer = model, tokenizer
    monkeypatch.setenv(MODE, "stub")
    with pytest.raises(sb.SurprisalBackendError, match=f"{MODE}=stub"):
        backend._load()
    monkeypatch.delenv(MODE)
    if cached:
        assert backend._load() == (model, tokenizer)


def _run(tmp_path, *args):
    text = "\n\n".join(
        " ".join(["the", "river", "lamp", "quiet", "morning", "walked"] * 6) + "."
        for _ in range(4))
    (tmp_path / "in.txt").write_text(text, encoding="utf-8")
    env = {**os.environ, MODE: "stub"}
    env.pop("PYTHONPATH", None)
    return subprocess.run([sys.executable, str(SCRIPTS / args[0]), *args[1:]],
                          cwd=tmp_path, env=env, capture_output=True, text=True,
                          timeout=300)


def test_dispatch_returns_an_unavailable_envelope_for_an_api_judge(tmp_path):
    proc = _run(tmp_path, "setec_run.py", "narrative_decision_audit", "in.txt",
                "--judge", "anthropic", "--judge-model", "synthetic-model", "--json")
    assert proc.returncode == 3
    envelope = json.loads(proc.stdout)
    assert envelope["schema_version"] == "1.0" and envelope["tool"] == "setec_run"
    assert envelope["available"] is False and envelope["results"] == {}
    assert envelope["reason_category"] == "bad_input"
    assert f"{MODE}=stub: the anthropic API judge is disabled" in envelope["reason"]


def test_mock_judge_still_runs_under_the_mode(tmp_path):
    proc = _run(tmp_path, "setec_run.py", "narrative_decision_audit", "in.txt",
                "--judge", "mock", "--json")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["available"] is True


@pytest.mark.parametrize("launcher,prefix", [
    ("semantic_trajectory_audit.py", "Embedding backend error:"),
    ("surprisal_audit.py", "Surprisal scoring failed:"),
])
def test_direct_launchers_refuse_with_their_existing_prefix(tmp_path, launcher, prefix):
    proc = _run(tmp_path, launcher, "in.txt", "--json")
    assert proc.returncode == 3
    assert proc.stdout == ""
    assert f"{prefix} {MODE}=stub" in proc.stderr
    assert "Traceback" not in proc.stderr
