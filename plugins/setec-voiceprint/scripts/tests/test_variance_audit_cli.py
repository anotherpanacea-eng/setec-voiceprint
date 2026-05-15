#!/usr/bin/env python3
"""Tests for variance_audit.py's CLI surface.

Narrow scope: argparse parser hygiene. The rest of the CLI's behavior
is covered by ``test_variance_audit_tier4.py`` (Tier 4 integration)
and by end-to-end audits in other test files.

Regression covered here:

  * **Unescaped `%` in `help=` kwargs.** argparse formats every
    help string against a params dict (so `%(prog)s` and
    `%(default)s` substitution work). Any literal `%` that isn't
    doubled to `%%` raises ``TypeError`` the first time
    ``--help`` runs. Bug shipped in v1.59.x via the ``--window-stride``
    help text ("50% overlap"); fixed in v1.59.4 and locked here.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VARIANCE_AUDIT = ROOT / "variance_audit.py"


def test_variance_audit_help_runs_cleanly():
    """`python3 variance_audit.py --help` must exit 0 and print usage.

    Catches the unescaped-`%` regression end-to-end: argparse's
    `--help` action is what triggers the ``TypeError`` when an
    action's ``help=`` contains a bare ``%`` followed by a letter
    that looks like a format spec (``%o``, ``%d``, etc.).
    """
    result = subprocess.run(
        [sys.executable, str(VARIANCE_AUDIT), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"variance_audit.py --help exited {result.returncode}\n"
        f"stderr:\n{result.stderr}"
    )
    # Sanity: real help text printed.
    assert "usage:" in result.stdout.lower()
    assert "--tier4" in result.stdout


def test_no_unescaped_percent_in_add_argument_help():
    """Every `help=` literal must survive argparse's `%`-substitution.

    Static AST scan: faster than the subprocess smoke test above and
    surfaces the offending line number if the regression returns,
    rather than just a `TypeError` in argparse internals.

    `%(name)s`-style substitutions are fine (argparse provides the
    action's full attribute dict at format time); the forgiving
    `defaultdict` here simulates that environment.
    """
    src = VARIANCE_AUDIT.read_text()
    tree = ast.parse(src)
    offenders: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "add_argument"):
            continue
        for kw in node.keywords:
            if kw.arg != "help":
                continue
            try:
                val = ast.literal_eval(kw.value)
            except Exception:
                continue
            if not isinstance(val, str):
                continue
            try:
                val % defaultdict(lambda: "X")
            except (TypeError, ValueError) as e:
                offenders.append((node.lineno, f"{val!r}: {e}"))

    assert not offenders, (
        "Unescaped `%` in argparse help string(s). Double the `%` to "
        "`%%` so argparse renders `--help` without crashing:\n  "
        + "\n  ".join(f"line {ln}: {msg}" for ln, msg in offenders)
    )
