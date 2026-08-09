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

import os
import subprocess
import sys
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
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=30,
    )
    assert result.returncode == 0, (
        f"variance_audit.py --help exited {result.returncode}\n"
        f"stderr:\n{result.stderr}"
    )
    # Sanity: real help text printed.
    assert "usage:" in result.stdout.lower()
    assert "--tier4" in result.stdout
