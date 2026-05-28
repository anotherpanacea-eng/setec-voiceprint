#!/usr/bin/env python3
"""Stub harness for the remaining LLM-driven replication stages.

Stages A2, B1, B2, B3, B5 follow the same shape as A1
(`a1_prompt_extraction.py`): load an input manifest, render the
stage's prompt template per row, call a judge backend, append output
rows, write a sidecar with the prompt fingerprint + input manifest
SHA-256.

This stub gives operators a tiny skeleton they can specialize per
stage by:

  1. Copying the file to ``<stage>_<name>.py`` (e.g.
     ``b1_templating.py``).
  2. Pointing ``PROMPT_FILE`` at the vendored template in
     ``references/storyscope-prompts/`` (see the README there for
     the list of files the paper's repository provides).
  3. Filling ``parse_response`` to convert the judge's text into
     the stage's output row type.
  4. Updating the SidecarStage and the argparse surface.

The non-LLM stages (B4 feature dedup, C1–C7 analytics) are wired
end-to-end and have their own scripts in
``scripts/replication/`` and ``scripts/calibration/`` — they don't
need this stub pattern.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPLICATION_DIR = SCRIPT_DIR.parent
SCRIPTS_DIR = REPLICATION_DIR.parent
for p in (
    str(SCRIPT_DIR), str(REPLICATION_DIR), str(SCRIPTS_DIR),
):
    if p not in sys.path:
        sys.path.insert(0, p)

from manifest_format import (  # type: ignore  # noqa: E402
    StageSidecar, load_jsonl, sha256_path, utc_now, write_jsonl,
)
from narrative_judge import JudgeError  # type: ignore  # noqa: E402

SCRIPT_VERSION = "0.1.0"
STAGE = "STUB"
PROMPT_FILE = Path("...")  # specialize per stage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"Stage {STAGE}: stub.",
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--judge",
        choices=("mock", "anthropic", "openai", "gemini"),
        default="mock",
    )
    parser.add_argument("--judge-model", default=None)
    args = parser.parse_args(argv)

    raise NotImplementedError(
        f"Stub for stage {STAGE}: see "
        f"`a1_prompt_extraction.py` for the canonical pattern and "
        f"the replication spec at `references/"
        f"narrative-decision-replication-spec.md` for the per-stage "
        f"prompt + IO requirements."
    )


if __name__ == "__main__":
    sys.exit(main())
