#!/usr/bin/env python3
"""Tests for tools/check_syspath_ratchet.py.

Pins:

  * The real repo's current sys.path.insert/append count exactly equals
    the pinned ceiling (proves the ceiling isn't stale in either
    direction at HEAD).
  * AST discovery finds both `.insert(...)` and `.append(...)` forms,
    ignores an unrelated `.insert()`/`.append()` call on something that
    isn't `sys.path`, and ignores `scripts/tests/` entirely.
  * PLANTED violation: adding one more call site over the pinned
    ceiling fails the check; the exact count over ceiling is reported.
  * `--strict` also fails when the count drops below the ceiling
    without lowering it (a stale, too-loose ceiling).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import check_syspath_ratchet as csr  # type: ignore  # noqa: E402


def _write(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


class Args:
    def __init__(self, strict: bool = False, json: bool = False):
        self.strict = strict
        self.json = json


def test_real_repo_count_matches_pinned_ceiling():
    sites = csr.find_all_sites()
    assert len(sites) == csr.PINNED_CEILING


def test_real_repo_check_passes():
    assert csr.cmd_check(Args()) == 0
    assert csr.cmd_check(Args(strict=True)) == 0


def test_discovers_insert_and_append_and_ignores_lookalikes(tmp_path, monkeypatch):
    monkeypatch.setattr(csr, "SCRIPTS_ROOT", tmp_path)
    monkeypatch.setattr(csr, "REPO_ROOT", tmp_path.parent)
    _write(tmp_path, "a.py", (
        "import sys\n"
        "sys.path.insert(0, 'x')\n"
        "sys.path.append('y')\n"
        "other = {}\n"
        "other.insert(0, 'z')\n"      # lookalike: not sys.path
        "class Fake:\n"
        "    path = []\n"
        "fake = Fake()\n"
        "fake.path.append('w')\n"     # lookalike: attribute named `path`, not `sys`
    ))
    sites = csr.find_all_sites()
    assert len(sites) == 2
    methods = sorted(s.method for s in sites)
    assert methods == ["append", "insert"]


def test_tests_directory_is_excluded(tmp_path, monkeypatch):
    monkeypatch.setattr(csr, "SCRIPTS_ROOT", tmp_path)
    monkeypatch.setattr(csr, "REPO_ROOT", tmp_path.parent)
    _write(tmp_path, "tests/test_something.py", (
        "import sys\nsys.path.insert(0, 'x')\n"
    ))
    assert csr.find_all_sites() == []


def test_planted_new_site_over_ceiling_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(csr, "SCRIPTS_ROOT", tmp_path)
    monkeypatch.setattr(csr, "REPO_ROOT", tmp_path.parent)
    monkeypatch.setattr(csr, "PINNED_CEILING", 1)
    _write(tmp_path, "a.py", "import sys\nsys.path.insert(0, 'x')\n")
    _write(tmp_path, "b.py", "import sys\nsys.path.insert(0, 'y')\n")
    assert csr.cmd_check(Args()) == 1


def test_at_ceiling_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(csr, "SCRIPTS_ROOT", tmp_path)
    monkeypatch.setattr(csr, "REPO_ROOT", tmp_path.parent)
    monkeypatch.setattr(csr, "PINNED_CEILING", 1)
    _write(tmp_path, "a.py", "import sys\nsys.path.insert(0, 'x')\n")
    assert csr.cmd_check(Args()) == 0


def test_strict_fails_when_count_drops_below_stale_ceiling(tmp_path, monkeypatch):
    monkeypatch.setattr(csr, "SCRIPTS_ROOT", tmp_path)
    monkeypatch.setattr(csr, "REPO_ROOT", tmp_path.parent)
    monkeypatch.setattr(csr, "PINNED_CEILING", 5)
    _write(tmp_path, "a.py", "import sys\nsys.path.insert(0, 'x')\n")
    assert csr.cmd_check(Args()) == 0          # under ceiling is fine, non-strict
    assert csr.cmd_check(Args(strict=True)) == 1  # but stale under --strict
