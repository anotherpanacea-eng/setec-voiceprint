#!/usr/bin/env python3
"""Stage A1: prompt extraction from human stories.

Russell et al. 2026 §2: reverse-engineer a writing prompt from each
human story so the 5 AI models generate from the same starting
point. Paper used Gemini 2.5 Flash in June 2025.

Operator-side: any judge backend works; the prompt is the paper's
verbatim ``prompts_display/prompt_generation.md`` (vendored at
``references/storyscope-prompts/prompt_generation.md``).

The script is also the canonical pattern for the other LLM-driven
stages — A2 (story generation), B1 (templating), B2 (comparative
analysis), B3 (feature discovery), B5 (feature assignment) follow
the same shape: load input manifest, render prompt per row, call
judge, append output row, write sidecar with prompt fingerprint +
input manifest SHA-256.

Usage::

    python3 a1_prompt_extraction.py \\
        --human-corpus path/to/human_stories.jsonl \\
        --judge anthropic --judge-model claude-sonnet-4-6 \\
        --output-dir ./run-2026-06-01/a1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPLICATION_DIR = SCRIPT_DIR.parent
SCRIPTS_DIR = REPLICATION_DIR.parent
PLUGIN_DIR = SCRIPTS_DIR.parent
for p in (
    str(SCRIPT_DIR), str(REPLICATION_DIR), str(SCRIPTS_DIR),
):
    if p not in sys.path:
        sys.path.insert(0, p)

from manifest_format import (  # type: ignore  # noqa: E402
    PromptRow, StageSidecar, load_jsonl, sha256_path,
    utc_now, write_jsonl,
)
from narrative_judge import JudgeError  # type: ignore  # noqa: E402

SCRIPT_VERSION = "0.1.0"

PROMPT_FILE = (
    PLUGIN_DIR
    / "references"
    / "storyscope-prompts"
    / "prompt_generation.md"
)


def load_prompt_template() -> str:
    if not PROMPT_FILE.exists():
        raise FileNotFoundError(
            f"Prompt file missing: {PROMPT_FILE}. The paper's "
            "prompt_generation.md is vendored at this path; re-run "
            "from a clean checkout if the file was deleted."
        )
    return PROMPT_FILE.read_text(encoding="utf-8")


def render_prompt(template: str, story_text: str) -> str:
    return template.replace("{batch_text}", story_text)


def fingerprint(template: str) -> str:
    return hashlib.sha256(template.encode("utf-8")).hexdigest()


def build_a1_judge(args):
    """Build a callable that takes story text and returns a prompt.

    Wraps the audit's narrative_judge interface with a prompt-extraction
    specialization: the judge is called with the full
    prompt-template-rendered text and returns the model's raw text
    response (not a JSON object).
    """
    if args.judge == "manifest":
        raise ValueError(
            "The 'manifest' backend is for pre-computed feature "
            "values, not prompt extraction. Use an API backend "
            "(anthropic / openai / gemini) for stage A1."
        )

    if args.judge in ("anthropic", "openai", "gemini"):
        return _build_api_call(args)
    if args.judge == "mock":
        return _build_mock_call()
    raise ValueError(f"unknown judge {args.judge!r}")


def _build_mock_call():
    def _run(_story_text: str) -> str:
        return (
            "Write a short story about a lighthouse keeper at the "
            "edge of the world who hears the building speak."
        )
    return _run


def _build_api_call(args):
    """Lazy-import the SDK only when needed."""
    kind = args.judge
    model = args.judge_model
    if not model:
        raise ValueError(f"--judge-model is required for {kind}")

    if kind == "anthropic":
        try:
            import anthropic  # type: ignore
        except ImportError as exc:
            raise JudgeError(
                "anthropic SDK missing; `pip install anthropic`"
            ) from exc
        client = anthropic.Anthropic()

        def _run(story_text: str) -> str:
            try:
                msg = client.messages.create(
                    model=model,
                    max_tokens=512,
                    temperature=args.judge_temperature,
                    messages=[
                        {"role": "user", "content": story_text},
                    ],
                )
            except Exception as exc:  # noqa: BLE001
                raise JudgeError(
                    f"anthropic provider call failed: {exc}"
                ) from exc
            return "".join(
                b.text for b in msg.content
                if getattr(b, "type", None) == "text"
            ).strip()
        return _run

    if kind == "openai":
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as exc:
            raise JudgeError(
                "openai SDK missing; `pip install openai`"
            ) from exc
        client = OpenAI()

        def _run(story_text: str) -> str:
            try:
                resp = client.chat.completions.create(
                    model=model,
                    temperature=args.judge_temperature,
                    max_tokens=512,
                    messages=[{"role": "user", "content": story_text}],
                )
            except Exception as exc:  # noqa: BLE001
                raise JudgeError(
                    f"openai provider call failed: {exc}"
                ) from exc
            return (resp.choices[0].message.content or "").strip()
        return _run

    if kind == "gemini":
        try:
            from google import genai  # type: ignore
        except ImportError as exc:
            raise JudgeError(
                "google-genai SDK missing"
            ) from exc
        import os
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get(
            "GEMINI_API_KEY",
        )
        if not api_key:
            raise JudgeError("GOOGLE_API_KEY / GEMINI_API_KEY missing")
        client = genai.Client(api_key=api_key)

        def _run(story_text: str) -> str:
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=[{
                        "role": "user",
                        "parts": [{"text": story_text}],
                    }],
                    config={
                        "temperature": args.judge_temperature,
                        "max_output_tokens": 512,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                raise JudgeError(
                    f"gemini provider call failed: {exc}"
                ) from exc
            return (resp.text or "").strip()
        return _run

    raise ValueError(f"unknown judge {kind!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage A1: extract writing prompts from human stories.",
    )
    parser.add_argument(
        "--human-corpus", type=Path, required=True,
        help="JSONL of human stories with keys {story_id, story_text}.",
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True,
    )
    parser.add_argument(
        "--judge",
        choices=("mock", "anthropic", "openai", "gemini"),
        default="mock",
    )
    parser.add_argument("--judge-model", default=None)
    parser.add_argument(
        "--judge-temperature", type=float, default=0.3,
        help=(
            "Paper used Gemini 2.5 Flash default temperature for "
            "prompt extraction; 0.3 keeps prompts focused while "
            "preserving variation across stories."
        ),
    )
    parser.add_argument(
        "--target-words", type=int, default=5000,
        help="Word count to embed in the prompt for downstream A2.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process only the first N stories (smoke-test flag).",
    )
    args = parser.parse_args(argv)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    template = load_prompt_template()
    runner = build_a1_judge(args)

    rows_out: list[PromptRow] = []
    counts = {"ok": 0, "judge_error": 0, "skipped": 0}
    for i, story in enumerate(load_jsonl(args.human_corpus)):
        if args.limit is not None and i >= args.limit:
            break
        story_id = str(story.get("story_id") or i)
        story_text = story.get("story_text") or story.get("text") or ""
        if not story_text:
            counts["skipped"] += 1
            continue
        rendered = render_prompt(template, story_text)
        try:
            prompt_text = runner(rendered)
        except JudgeError as exc:
            counts["judge_error"] += 1
            print(
                f"warn: story {story_id} judge_error: {exc}",
                file=sys.stderr,
            )
            continue
        if not prompt_text:
            counts["judge_error"] += 1
            continue
        rows_out.append(PromptRow(
            prompt_id=f"prompt_{story_id}",
            source_story_id=story_id,
            prompt_text=prompt_text,
            target_words=args.target_words,
            judge_identity={
                "kind": args.judge,
                "model": args.judge_model,
            },
        ))
        counts["ok"] += 1

    out_jsonl = args.output_dir / "prompts.jsonl"
    n_written = write_jsonl(out_jsonl, rows_out)

    sidecar = StageSidecar(
        stage="A1",
        tool="scripts/replication/stages/a1_prompt_extraction.py",
        version=SCRIPT_VERSION,
        prompt_fingerprint_sha256=fingerprint(template),
        judge_identity={
            "kind": args.judge,
            "model": args.judge_model,
            "temperature": args.judge_temperature,
        },
        input_manifest_sha256=sha256_path(args.human_corpus),
        row_count=n_written,
        completed_at_utc=utc_now(),
        row_status=counts,
    )
    sidecar.write(args.output_dir / "prompts.manifest.json")
    print(
        f"A1: {n_written} prompts written "
        f"(skipped={counts['skipped']}, "
        f"judge_error={counts['judge_error']})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
