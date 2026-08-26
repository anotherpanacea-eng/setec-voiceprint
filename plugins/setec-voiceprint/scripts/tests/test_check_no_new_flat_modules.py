#!/usr/bin/env python3
"""Tests for tools/check_no_new_flat_modules.py.

Pins:

  * The real repo's committed baseline exactly equals the current
    top-level `scripts/*.py` set (190 modules) -- the gate passes at
    HEAD with zero exemption rows needed.
  * Scan is non-recursive: a subdirectory module never counts as "flat".
  * PLANTED violation: a brand-new top-level module not in baseline or
    exemptions fails the check.
  * A matching exemption row (with all five required fields) clears
    exactly that violation; a shape-invalid row is rejected.
  * `--strict` catches a ghost exemption row (names a module that no
    longer exists) and a mutated/hand-edited baseline (added or removed
    entries since the merge base); an absent `baseline` key at the
    merge base is a no-op (this PR's own bootstrap case).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import check_no_new_flat_modules as cnfm  # type: ignore  # noqa: E402


def _write(root: Path, rel: str, content: str = "VALUE = 1\n") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _init_git_repo(root: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=root, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True,
    ).stdout.strip()


class Args:
    def __init__(self, strict: bool = False, json: bool = False, base_ref: str = "origin/main"):
        self.strict = strict
        self.json = json
        self.base_ref = base_ref


def test_real_repo_baseline_matches_current_flat_modules():
    current = set(cnfm.find_flat_modules())
    baseline = set(cnfm.load_baseline())
    rows = cnfm.load_exemptions()
    assert not cnfm.validate_exemption_rows(rows)
    exempted = {r.get("module") for r in rows}
    assert current <= (baseline | exempted)
    # Derived, not a magic inventory count: the flat tree is exactly its frozen
    # baseline plus whatever has been granted a reviewed exemption row. That is
    # the actual contract, and unlike a hardcoded 190 it does not need editing
    # every time a reviewed surface lands -- while still failing loudly if a
    # module appears with no baseline entry and no exemption, or if a baseline
    # or exemption row goes stale against the tree.
    assert len(current) == len(baseline) + len(exempted)


def test_real_repo_check_passes():
    assert cnfm.cmd_check(Args()) == 0


def test_scan_is_non_recursive(tmp_path, monkeypatch):
    monkeypatch.setattr(cnfm, "SCRIPTS_ROOT", tmp_path)
    _write(tmp_path, "top.py")
    _write(tmp_path, "calibration/nested.py")
    modules = cnfm.find_flat_modules()
    assert modules == ["top.py"]


def test_planted_new_module_fails_without_exemption(tmp_path, monkeypatch):
    monkeypatch.setattr(cnfm, "SCRIPTS_ROOT", tmp_path)
    monkeypatch.setattr(cnfm, "REPO_ROOT", tmp_path.parent)
    exemptions_path = tmp_path.parent / "flat_module_exemptions.yaml"
    monkeypatch.setattr(cnfm, "EXEMPTIONS_PATH", exemptions_path)
    _write(tmp_path, "old_one.py")
    exemptions_path.write_text(
        "schema_version: 1\nbaseline:\n- old_one.py\nexemptions: []\n", encoding="utf-8",
    )
    assert cnfm.cmd_check(Args()) == 0

    _write(tmp_path, "brand_new.py")  # planted violation
    assert cnfm.cmd_check(Args()) == 1


def test_exemption_row_clears_a_new_module(tmp_path, monkeypatch):
    monkeypatch.setattr(cnfm, "SCRIPTS_ROOT", tmp_path)
    monkeypatch.setattr(cnfm, "REPO_ROOT", tmp_path.parent)
    exemptions_path = tmp_path.parent / "flat_module_exemptions.yaml"
    monkeypatch.setattr(cnfm, "EXEMPTIONS_PATH", exemptions_path)
    _write(tmp_path, "old_one.py")
    _write(tmp_path, "brand_new.py")
    exemptions_path.write_text(
        json.dumps({
            "schema_version": 1,
            "baseline": ["old_one.py"],
            "exemptions": [{
                "module": "brand_new.py", "reason": "r", "owner": "o",
                "introduced_sha": "s", "removal_phase": "P4",
            }],
        }), encoding="utf-8",
    )
    assert cnfm.cmd_check(Args()) == 0


@pytest.mark.parametrize("bad_row,expected_fragment", [
    ({"module": "a.py", "reason": "r", "owner": "o", "introduced_sha": "s"}, "removal_phase"),
    ({"module": "a.py", "reason": "r", "owner": "o", "introduced_sha": "s",
      "removal_phase": "P99"}, "not in"),
])
def test_validate_exemption_rows_catches_shape_problems(bad_row, expected_fragment):
    problems = cnfm.validate_exemption_rows([bad_row])
    assert problems
    assert any(expected_fragment in p for p in problems)


def test_check_ghost_exemptions_catches_a_row_for_a_removed_module():
    rows = [{
        "module": "long_gone.py", "reason": "r", "owner": "o",
        "introduced_sha": "s", "removal_phase": "P4",
    }]
    problems = cnfm.check_ghost_exemptions(rows, current={"still_here.py"})
    assert len(problems) == 1
    assert "long_gone.py" in problems[0]


def test_seed_baseline_refuses_to_overwrite_without_force(tmp_path, monkeypatch):
    monkeypatch.setattr(cnfm, "SCRIPTS_ROOT", tmp_path)
    exemptions_path = tmp_path.parent / "flat_module_exemptions.yaml"
    monkeypatch.setattr(cnfm, "EXEMPTIONS_PATH", exemptions_path)
    _write(tmp_path, "a.py")
    exemptions_path.write_text(
        "schema_version: 1\nbaseline:\n- a.py\nexemptions: []\n", encoding="utf-8",
    )

    class SeedArgs:
        introduced_sha = "deadbeef"
        force = False

    assert cnfm.cmd_seed_baseline(SeedArgs()) == 2  # refuses: already seeded

    SeedArgs.force = True
    assert cnfm.cmd_seed_baseline(SeedArgs()) == 0  # --force allows re-seed


def test_strict_baseline_mutation_added_entry_is_caught(tmp_path, monkeypatch):
    monkeypatch.setattr(cnfm, "SCRIPTS_ROOT", tmp_path)
    monkeypatch.setattr(cnfm, "REPO_ROOT", tmp_path)
    exemptions_path = tmp_path / "flat_module_exemptions.yaml"
    monkeypatch.setattr(cnfm, "EXEMPTIONS_PATH", exemptions_path)
    _write(tmp_path, "a.py")
    exemptions_path.write_text(
        "schema_version: 1\nbaseline:\n- a.py\nexemptions: []\n", encoding="utf-8",
    )
    sha = _init_git_repo(tmp_path)

    # Hand-edit baseline to smuggle in a new module instead of using an
    # exemption row -- exactly the loophole --strict must close.
    _write(tmp_path, "sneaky.py")
    exemptions_path.write_text(
        "schema_version: 1\nbaseline:\n- a.py\n- sneaky.py\nexemptions: []\n",
        encoding="utf-8",
    )

    # Without --strict: sneaky.py IS in baseline, so the plain check passes
    # (the mutation itself isn't caught without --strict).
    assert cnfm.cmd_check(Args(base_ref=sha)) == 0
    # With --strict: the baseline mutation is caught.
    assert cnfm.cmd_check(Args(strict=True, base_ref=sha)) == 1


def test_strict_absent_baseline_key_at_merge_base_is_a_no_op(tmp_path, monkeypatch):
    """This ratchet's own bootstrap: the merge base's file (if any)
    predates `baseline` entirely -- nothing to compare against yet."""
    monkeypatch.setattr(cnfm, "SCRIPTS_ROOT", tmp_path)
    monkeypatch.setattr(cnfm, "REPO_ROOT", tmp_path)
    exemptions_path = tmp_path / "flat_module_exemptions.yaml"
    monkeypatch.setattr(cnfm, "EXEMPTIONS_PATH", exemptions_path)
    _write(tmp_path, "a.py")
    exemptions_path.write_text("schema_version: 1\nexemptions: []\n", encoding="utf-8")
    sha = _init_git_repo(tmp_path)

    exemptions_path.write_text(
        "schema_version: 1\nbaseline:\n- a.py\nexemptions: []\n", encoding="utf-8",
    )
    assert cnfm.cmd_check(Args(strict=True, base_ref=sha)) == 0
