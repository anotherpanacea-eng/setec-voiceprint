#!/usr/bin/env python3
"""pipeline.py — StoryScope replication orchestrator.

Chains the replication stages with operator-controlled granularity
and per-stage checkpointing. Each stage writes its output JSONL +
sidecar manifest to ``<output_dir>/<stage_id>/``; the orchestrator
verifies the upstream sidecar's SHA-256 before launching a
downstream stage so a re-run resumes cleanly from the last
completed step.

Example invocations
-------------------

Level 2 (analytics-only, paper's released feature manifest)::

    python3 pipeline.py \\
        --stages C1,C2 \\
        --feature-manifest path/to/parallel_features.jsonl \\
        --output-dir ./run-2026-05-28

Level 3 (full replication starting from a human-story corpus)::

    python3 pipeline.py \\
        --stages A1,A2,B1,B2,B3,B4,B5,C1,C2 \\
        --human-corpus path/to/human_stories.jsonl \\
        --judge anthropic \\
        --judge-model claude-sonnet-4-6 \\
        --ai-models claude_sonnet_4_6,gpt_5_4,gemini_3_flash \\
        --output-dir ./run-2026-06-01

The orchestrator does NOT call LLMs itself. Each LLM-driven stage
(A1, A2, B1, B2, B3, B5) is a separate script that the operator
runs (or that this orchestrator invokes via subprocess). The split
keeps individual stages independently re-runnable.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

STAGE_HANDLERS: dict[str, dict[str, str]] = {
    "A1": {
        "script": "stages/a1_prompt_extraction.py",
        "wired": "yes",
        "input": "--human-corpus",
        "output": "prompts.jsonl",
    },
    "A2": {
        "script": "stages/stub_template.py",
        "wired": "stub",
        "input": "--input",
        "output": "stories.jsonl",
    },
    "B1": {
        "script": "stages/stub_template.py",
        "wired": "stub",
        "input": "--input",
        "output": "templates.jsonl",
    },
    "B2": {
        "script": "stages/stub_template.py",
        "wired": "stub",
        "input": "--input",
        "output": "comparisons.jsonl",
    },
    "B3": {
        "script": "stages/stub_template.py",
        "wired": "stub",
        "input": "--input",
        "output": "candidates.jsonl",
    },
    "B4": {
        "script": "feature_dedup.py",
        "wired": "yes",
        "input": "--candidates-jsonl",
        "output": "features.jsonl",
    },
    "B5": {
        "script": "stages/stub_template.py",
        "wired": "stub",
        "input": "--input",
        "output": "feature_values.jsonl",
    },
    "C1": {
        "script": "train_xgboost.py",
        "wired": "yes",
        "input": "--feature-manifest",
        "output": "train_binary.json",
        "extra_args": ["--task", "binary"],
    },
    "C2": {
        "script": "train_xgboost.py",
        "wired": "yes",
        "input": "--feature-manifest",
        "output": "train_multiclass.json",
        "extra_args": ["--task", "multiclass"],
    },
}


def run_stage(
    stage_id: str,
    *,
    output_dir: Path,
    upstream_output: Path | None,
    judge: str,
    judge_model: str | None,
    feature_manifest: Path | None,
    human_corpus: Path | None,
    extra: list[str],
) -> Path:
    handler = STAGE_HANDLERS[stage_id]
    if handler["wired"] == "stub":
        raise NotImplementedError(
            f"Stage {stage_id} is shipped as a stub in this v0.1 "
            f"pipeline. Operators implementing it should copy "
            f"`stages/stub_template.py` to "
            f"`stages/{stage_id.lower()}_<name>.py`, follow the "
            f"pattern in `stages/a1_prompt_extraction.py`, and "
            f"document the chosen prompt template in "
            f"`references/storyscope-prompts/`. See the replication "
            f"spec for per-stage requirements."
        )
    stage_dir = output_dir / stage_id
    stage_dir.mkdir(parents=True, exist_ok=True)
    out_path = stage_dir / handler["output"]
    script = SCRIPT_DIR / handler["script"]

    if stage_id == "A1":
        input_path = human_corpus
    elif stage_id in ("C1", "C2"):
        input_path = feature_manifest or upstream_output
    elif stage_id == "B4":
        input_path = upstream_output
    else:
        input_path = upstream_output
    if input_path is None:
        raise ValueError(
            f"Stage {stage_id} has no input wired; supply "
            f"--feature-manifest / --human-corpus or run the "
            f"upstream stage first."
        )

    cmd = [
        sys.executable, str(script),
        handler["input"], str(input_path),
    ]
    # C1/C2 write to --output-dir; A1/B4 write to --out-jsonl or
    # similar. Honor each handler's surface.
    if stage_id in ("C1", "C2"):
        cmd.extend(["--output-dir", str(stage_dir)])
    elif stage_id == "B4":
        cmd.extend(["--out-jsonl", str(out_path)])
    elif stage_id == "A1":
        cmd.extend(["--output-dir", str(stage_dir)])
        if judge:
            cmd.extend(["--judge", judge])
        if judge_model:
            cmd.extend(["--judge-model", judge_model])
    for arg in handler.get("extra_args", []):
        cmd.append(arg)
    cmd.extend(extra)

    print(
        f"\n>>> {stage_id}: {' '.join(shlex.quote(c) for c in cmd)}"
    )
    rc = subprocess.call(cmd)
    if rc != 0:
        raise SystemExit(f"Stage {stage_id} failed with rc={rc}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="StoryScope replication orchestrator.",
    )
    parser.add_argument(
        "--stages", required=True,
        help="Comma-separated stage IDs (e.g. C1,C2 or A1,A2,B1,B5,C1).",
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True,
    )
    parser.add_argument("--human-corpus", type=Path, default=None)
    parser.add_argument(
        "--feature-manifest", type=Path, default=None,
        help="L2 entry point: a pre-built feature-values manifest.",
    )
    parser.add_argument(
        "--judge", default="mock",
        help="Default judge backend for LLM-driven stages.",
    )
    parser.add_argument("--judge-model", default=None)
    parser.add_argument(
        "--stage-args", action="append", default=[],
        help=(
            "Extra arguments passed to all stages, repeatable. Use "
            "shell quoting (e.g. --stage-args='--limit 50')."
        ),
    )
    args = parser.parse_args(argv)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    for s in stages:
        if s not in STAGE_HANDLERS:
            print(
                f"error: unknown stage {s!r}; known stages: "
                f"{sorted(STAGE_HANDLERS)}",
                file=sys.stderr,
            )
            return 2

    extra: list[str] = []
    for s_arg in args.stage_args:
        extra.extend(shlex.split(s_arg))

    upstream_output: Path | None = None
    for s in stages:
        upstream_output = run_stage(
            s,
            output_dir=args.output_dir,
            upstream_output=upstream_output,
            judge=args.judge,
            judge_model=args.judge_model,
            feature_manifest=args.feature_manifest,
            human_corpus=args.human_corpus,
            extra=extra,
        )
    print("\nAll stages complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
