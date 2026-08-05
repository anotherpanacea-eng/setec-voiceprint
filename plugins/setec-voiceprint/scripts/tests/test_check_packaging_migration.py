#!/usr/bin/env python3
"""Tests for tools/check_packaging_migration.py.

Pins:

  * The real repo's anchors are all covered by the committed
    packaging_migration_exemptions.yaml (the gate passes at HEAD).
  * A synthetic module with an unexempted __file__ anchor is caught.
  * A "helper alias" (`Y = X.parent` where X is a direct __file__
    anchor) is discovered too — not just the direct anchor.
  * An inline (non-assigned) __file__ use is discovered.
  * tools/*.py REPO_ROOT-depth check: a wrong `parents[N]` is caught.
  * Exemption-row shape validation: a missing required field, and an
    illegal removal_phase, are both caught.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import check_packaging_migration as cpm  # type: ignore  # noqa: E402


def test_real_repo_anchors_all_exempted():
    anchors = cpm.find_all_anchors()
    rows = cpm.load_exemptions()
    assert not cpm.validate_exemption_rows(rows)
    exempted = {cpm._exemption_key(r) for r in rows}
    unexempted = [a for a in anchors if (a.path, a.symbol) not in exempted]
    assert not unexempted, [(a.path, a.symbol) for a in unexempted]


def test_real_repo_tools_repo_root_depth_clean():
    assert cpm.check_tools_repo_root() == []


def test_direct_anchor_and_helper_alias_and_inline_all_found(tmp_path, monkeypatch):
    monkeypatch.setattr(cpm, "REPO_ROOT", tmp_path)
    mod = tmp_path / "an_audit.py"
    mod.write_text(
        "from pathlib import Path\n"
        "\n"
        "SCRIPT_DIR = Path(__file__).resolve().parent\n"
        "DATA_DIR = SCRIPT_DIR.with_name('data')\n"  # helper alias
        "\n"
        "def show():\n"
        "    print(Path(__file__).name)\n"  # inline use
        "\n",
        encoding="utf-8",
    )
    anchors = cpm.find_anchors_in_file(mod)
    symbols = {a.symbol for a in anchors}
    assert "SCRIPT_DIR" in symbols  # direct anchor
    assert "DATA_DIR" in symbols  # helper alias, closed over SCRIPT_DIR
    assert "<inline>" in symbols  # inline __file__ use in show()


def test_unexempted_anchor_fails_check(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "an_audit.py").write_text(
        "from pathlib import Path\n"
        "HERE = Path(__file__).resolve().parent\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cpm, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cpm, "SCRIPTS_ROOT", scripts)
    monkeypatch.setattr(cpm, "EXEMPTIONS_PATH", tmp_path / "exemptions.yaml")

    anchors = cpm.find_all_anchors()
    assert len(anchors) == 1
    rows = cpm.load_exemptions()  # file doesn't exist -> []
    assert rows == []
    exempted = {cpm._exemption_key(r) for r in rows}
    unexempted = [a for a in anchors if (a.path, a.symbol) not in exempted]
    assert len(unexempted) == 1


def test_seed_then_check_round_trips(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "an_audit.py").write_text(
        "from pathlib import Path\n"
        "HERE = Path(__file__).resolve().parent\n",
        encoding="utf-8",
    )
    exemptions_path = tmp_path / "exemptions.yaml"
    monkeypatch.setattr(cpm, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cpm, "SCRIPTS_ROOT", scripts)
    monkeypatch.setattr(cpm, "EXEMPTIONS_PATH", exemptions_path)

    class Args:
        introduced_sha = "deadbeef"

    assert cpm.cmd_seed(Args()) == 0
    rows = cpm.load_exemptions(exemptions_path)
    assert len(rows) == 1
    assert not cpm.validate_exemption_rows(rows)


def test_wrong_repo_root_depth_is_caught(tmp_path, monkeypatch):
    monkeypatch.setattr(cpm, "REPO_ROOT", tmp_path)
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "some_tool.py").write_text(
        "from pathlib import Path\n"
        "REPO_ROOT = Path(__file__).resolve().parents[2]\n",  # wrong depth
        encoding="utf-8",
    )
    monkeypatch.setattr(cpm, "TOOLS_ROOT", tools_dir)
    violations = cpm.check_tools_repo_root()
    assert len(violations) == 1
    assert "parents[2]" in violations[0].detail


@pytest.mark.parametrize("bad_row,expected_fragment", [
    ({"path": "a.py", "symbol": "X", "reason": "r", "owner": "o",
      "introduced_sha": "s"}, "removal_phase"),  # missing field
    ({"path": "a.py", "symbol": "X", "reason": "r", "owner": "o",
      "introduced_sha": "s", "removal_phase": "P99"}, "not in"),  # illegal phase
])
def test_validate_exemption_rows_catches_shape_problems(bad_row, expected_fragment):
    problems = cpm.validate_exemption_rows([bad_row])
    assert problems
    assert any(expected_fragment in p for p in problems)


def test_validate_exemption_rows_catches_duplicate_key():
    row = {
        "path": "a.py", "symbol": "X", "reason": "r", "owner": "o",
        "introduced_sha": "s", "removal_phase": "P4",
    }
    problems = cpm.validate_exemption_rows([row, dict(row)])
    assert any("duplicate exemption" in p for p in problems)
