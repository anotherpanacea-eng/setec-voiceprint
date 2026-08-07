#!/usr/bin/env python3
"""Shared plain-function test helpers hoisted out of per-file duplicates.

Each function below was AST-identical (`ast.dump()` structural comparison,
not eyeballing) across three or more test files in this directory. Only
that subset was hoisted; many more names collide across files (`envelope`,
`_run`, `_walk_keys`, `_envelope`, `make_args`, ...) but each of those has
a genuinely different body per file (different fixture module, different
fake-payload shape) and stays local by design — hoisting them would either
silently change what a test exercises or require threading per-file state
(e.g. `make_fetcher`'s reliance on each file's own `FIXTURE_DIR` /
`fixture_url_map`) through dozens of call sites for no behavioral gain.

These are plain functions, not pytest fixtures: import what you need with
`from conftest import ...`. pytest inserts this directory onto `sys.path`
during collection (no `tests/__init__.py`), so the import resolves the
same way it would for any other same-directory test module.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from preprocessing import strip_non_prose  # type: ignore


def read_manifest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _names(block):
    return {row["file"] for row in block["per_file_summaries"]}


def _cleaned(text: str) -> str:
    c, _ = strip_non_prose(text, None)
    return c


def _results(env):
    # success envelope nests results; error envelope is flat-ish — handle both.
    return env.get("results", env)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()
