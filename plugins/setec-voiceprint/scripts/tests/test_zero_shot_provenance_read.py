#!/usr/bin/env python3
"""Tests for zero_shot_provenance_read (vendored arXiv:2611.26216 prompts, no verdict).

Protects: the prompt sent is the vendored paper text (an external contract the read's meaning
depends on), parsing of the paper's plain-text answer format (including a reproduced bug where the
last evidence item swallowed the reasoning section), the quote check against the target, the
self-assessment refusal, refusal/format-drift handling, and the Anthropic path sending the
system/user split unchanged."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import types
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from setec.surfaces import zero_shot_provenance_read as zsp  # type: ignore

TARGET = (
    "The lighthouse keeper had not spoken to anyone for eleven days when the supply boat came "
    "round the point. He watched it from the gallery, counting the strokes of the oars, and found "
    "that he had forgotten how a voice sounded across water. The boatman shouted something about "
    "the weather and the price of paraffin, and the keeper answered in a croak that surprised them "
    "both. Later, unpacking the tins on the kitchen table, he caught himself rehearsing the next "
    "conversation, which would not come for another fortnight.\n"
)

IDEAS_PROSE_ANSWER = """PART 1: IDEAS

KEY IDEAS:
1. IDEA: An isolated keeper loses the habit of speech. LEANS: human STRENGTH: medium QUOTE: "had forgotten how a voice sounded across water" WHY: a particular, unexplained detail.
2. IDEA: The ending withholds a moral. LEANS: Human STRENGTH: Low QUOTE: “rehearsing the next conversation”
QUOTE: "the price of paraffin" WHY: no theme is spelled out.

REASONING (IDEAS):
The ideas are concrete.

DECISIVE FACTOR(S) (IDEAS):
No stated moral.

ideas_ai_share: 0.2
ideas_prediction: human-leaning

PART 2: PROSE

KEY OBSERVATIONS:
1. OBSERVATION: A tidy close. LEANS: ai STRENGTH: low QUOTE: "a croak that surprised them both [...] tins on the kitchen table" QUOTE: "words the text never uses" WHY: neat final beat.

REASONING (PROSE):
Mostly plain.

DECISIVE FACTOR(S) (PROSE):
Unshowy rhythm.

prose_ai_share: 0.65
prose_prediction: ai-leaning
"""

WHICH_MODEL_ANSWER = """KEY OBSERVATIONS:
1. OBSERVATION: A quiet ending. POINTS TO: Claude Opus 5.5 STRENGTH: medium QUOTE: "which would not come for another fortnight" WHY: ends quietly.

REASONING:
Quiet ending.

DECISIVE FACTOR(S):
The close.

confidence: 0.4
model: Anthropic, Claude Opus, Claude Opus 5.5
"""

# sha256 of the two wired templates as published upstream (github.com/denizbt/unmasking_authors,
# prompts/ at 75f4f48). An external contract: the read means "the paper's prompt" only while the
# vendored bytes are the paper's.
UPSTREAM_SHA256 = {
    "RQ1/joint.txt": "5b1a207238cbf887ba9baef4cc2dab24d61b704ef3646dd5d481c25ec26c9d93",
    "RQ2/holistic.txt": "236e039463bfa700ae576bd66aaf813b8bfc2faab08a5dd274f5d98b16a7836d",
}


def _run(tmp_path: Path, answer: str | None, *extra: str) -> tuple[int, dict]:
    target = tmp_path / "target.txt"
    target.write_text(TARGET, encoding="utf-8")
    args = [str(target), "--json", *extra]
    if answer is not None:
        resp = tmp_path / "answer.txt"
        resp.write_text(answer, encoding="utf-8")
        args += ["--response", str(resp)]
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = zsp.main(args)
    return code, json.loads(buf.getvalue())


@pytest.mark.parametrize("task", sorted(zsp.TASKS))
def test_wired_templates_are_the_upstream_bytes(task):
    rel = zsp.TASKS[task]
    raw = (zsp.prompts_root() / rel).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == UPSTREAM_SHA256[rel]
    assert zsp.render_prompt(task, "x")["sha256"] == UPSTREAM_SHA256[rel]


def test_rendered_prompt_is_the_vendored_paper_text():
    msgs = zsp.render_prompt("ideas-prose", "BODY OF TEXT")
    assert msgs["system"].startswith("TASK: Judge two separate things")
    assert "===== " not in msgs["system"] + msgs["user"]
    assert msgs["user"] == "<text>\nBODY OF TEXT\n</text>"
    which = zsp.render_prompt("which-model", "BODY OF TEXT")
    assert which["user"] == "BODY OF TEXT"
    assert which["system"].startswith("TASK: Judge which AI language model wrote")


def test_vendored_license_travels_with_prompts():
    lic = (zsp.prompts_root() / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in lic and "Deniz BT" in lic


def test_ideas_prose_answer_is_parsed_without_a_verdict(tmp_path):
    code, env = _run(tmp_path, IDEAS_PROSE_ANSWER, "--reader-model", "gpt-6.1-sol",
                     "--generator-lab", "anthropic")
    assert code == 0 and env["available"] is True
    r = env["results"]
    assert (r["ideas"]["ai_share"], r["ideas"]["reader_label"]) == (0.2, "human-leaning")
    assert (r["prose"]["ai_share"], r["prose"]["reader_label"]) == (0.65, "ai-leaning")
    assert r["reader"]["lab"] == "openai"
    assert r["prompt"]["template"] == "RQ1/joint.txt"
    second = r["ideas"]["evidence"][1]
    assert second["leans"] == "human" and second["strength"] == "low"
    assert second["quotes"] == ["rehearsing the next conversation", "the price of paraffin"]
    # Reproduced bug: the last item's WHY must not absorb the reasoning section after it.
    assert second["why"] == "no theme is spelled out."
    assert r["ideas"]["reasoning"] == "The ideas are concrete."
    assert r["ideas"]["decisive_factors"] == "No stated moral."
    assert "verdict" not in json.dumps(r).lower()
    assert env["claim_license"]["does_not_license"]


def test_quote_check_flags_quotes_missing_from_target(tmp_path):
    _, env = _run(tmp_path, IDEAS_PROSE_ANSWER, "--reader-model", "gpt-6.1-sol")
    qc = env["results"]["quote_check"]
    assert (qc["n_quotes"], qc["n_found"]) == (5, 4)
    obs = env["results"]["prose"]["evidence"][0]
    assert obs["quotes_found"] == [True, False]  # ellipsis fragments found; invented one not
    assert any("do not occur in the target" in w for w in env["warnings"])


def test_which_model_names_lab(tmp_path):
    code, env = _run(tmp_path, WHICH_MODEL_ANSWER, "--task", "which-model",
                     "--reader-model", "gemini-4-argon", "--generator-lab", "openai")
    assert code == 0
    r = env["results"]
    assert r["lab_named"] == "anthropic" and r["confidence"] == 0.4
    assert r["reader"]["lab"] == "google"
    assert r["quote_check"]["found_rate"] == 1.0


@pytest.mark.parametrize("reader_model, extra", [
    ("claude-fable-5-2", []),         # same lab as the generator
    ("mystery-model", []),            # lab unknown: cannot be shown to differ
    ("mystery-model", ["--reader-lab", "anthropic"]),
])
def test_self_assessment_is_refused(tmp_path, reader_model, extra):
    code, env = _run(tmp_path, IDEAS_PROSE_ANSWER, "--reader-model", reader_model,
                     "--generator-lab", "Anthropic", *extra)
    assert code == 3
    assert env["available"] is False and env["reason_category"] == "policy_refused"


def test_named_reader_lab_from_another_family_is_allowed(tmp_path):
    code, _ = _run(tmp_path, IDEAS_PROSE_ANSWER, "--reader-model", "mystery-model",
                   "--reader-lab", "openai", "--generator-lab", "anthropic")
    assert code == 0


@pytest.mark.parametrize("answer", [
    "I can't help identify who wrote this.",
    IDEAS_PROSE_ANSWER.replace("prose_ai_share: 0.65", "prose_ai_share: 1.4"),
    IDEAS_PROSE_ANSWER.replace("ideas_prediction: human-leaning", "ideas_prediction: probably"),
])
def test_refusal_or_format_drift_is_unavailable(tmp_path, answer):
    code, env = _run(tmp_path, answer, "--reader-model", "gpt-6.1-sol")
    assert code == 3
    assert env["available"] is False and env["reason_category"] == "bad_input"


def test_too_short_target_is_refused(tmp_path):
    target = tmp_path / "t.txt"
    target.write_text("Too short to read.", encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = zsp.main([str(target), "--json", "--response", str(target)])
    assert code == 3 and json.loads(buf.getvalue())["reason_category"] == "text_too_short"


def test_anthropic_reader_gets_the_paper_prompt_as_plain_text(tmp_path, monkeypatch):
    sent = {}

    class _Messages:
        def create(self, **kw):
            sent.update(kw)
            block = types.SimpleNamespace(type="text", text=IDEAS_PROSE_ANSWER)
            return types.SimpleNamespace(content=[block], stop_reason="end_turn")

    fake = types.ModuleType("anthropic")
    fake.Anthropic = lambda: types.SimpleNamespace(messages=_Messages())
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.delenv("setec_hermetic_backend", raising=False)

    code, env = _run(tmp_path, None, "--reader", "anthropic", "--reader-model",
                     "claude-fable-5-2", "--generator-lab", "openai")
    assert code == 0
    expected = zsp.render_prompt("ideas-prose", TARGET)
    assert sent["system"] == expected["system"]
    assert sent["messages"] == [{"role": "user", "content": expected["user"]}]
    assert env["results"]["reader"] == {"kind": "anthropic", "model": "claude-fable-5-2",
                                        "lab": "anthropic"}
    assert env["results"]["prose"]["ai_share"] == 0.65


def test_api_reader_needs_a_model(tmp_path):
    code, env = _run(tmp_path, None, "--reader", "anthropic")
    assert code == 3 and env["reason_category"] == "bad_input"
