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

import json
import subprocess
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


def test_validate_exemption_rows_accepts_not_applicable_phase():
    row = {
        "path": "a.py", "symbol": "X", "reason": "r", "owner": "o",
        "introduced_sha": "s", "removal_phase": "not-applicable",
    }
    assert not cpm.validate_exemption_rows([row])


# --------------- Build-review P1 finding #5 -------------------------


def test_scope_aware_closure_does_not_conflate_function_local_name_collision(
    tmp_path, monkeypatch,
):
    """A local variable in ONE function must not be mistaken for a
    derivation of a same-named anchor that lives in a totally different,
    unrelated function — the "86 of 371 rows are non-anchors from
    function-local name collisions" defect the scope-blind
    ast.walk(tree)-over-the-whole-file closure produced."""
    monkeypatch.setattr(cpm, "REPO_ROOT", tmp_path)
    mod = tmp_path / "an_audit.py"
    mod.write_text(
        "from pathlib import Path\n"
        "\n"
        "def uses_file():\n"
        "    SCRIPT_DIR = Path(__file__).resolve().parent\n"
        "    return SCRIPT_DIR\n"
        "\n"
        "def unrelated():\n"
        "    SCRIPT_DIR = 'not a file anchor at all'\n"  # different scope, same name
        "    other = SCRIPT_DIR + '/x'\n"  # must NOT be flagged as a __file__ anchor
        "    return other\n",
        encoding="utf-8",
    )
    anchors = cpm.find_anchors_in_file(mod)
    by_symbol = {a.symbol: a for a in anchors}
    assert "SCRIPT_DIR" in by_symbol
    assert "SCRIPT_DIR" in by_symbol["SCRIPT_DIR"].expr
    assert "other" not in by_symbol


def test_scope_aware_closure_still_finds_function_local_alias_of_module_anchor(
    tmp_path, monkeypatch,
):
    """A function CAN legitimately derive from a MODULE-level anchor
    (visible per Python scoping) — this must still be found."""
    monkeypatch.setattr(cpm, "REPO_ROOT", tmp_path)
    mod = tmp_path / "an_audit.py"
    mod.write_text(
        "from pathlib import Path\n"
        "SCRIPT_DIR = Path(__file__).resolve().parent\n"
        "\n"
        "def helper():\n"
        "    data_dir = SCRIPT_DIR / 'data'\n"
        "    return data_dir\n",
        encoding="utf-8",
    )
    anchors = cpm.find_anchors_in_file(mod)
    symbols = {a.symbol for a in anchors}
    assert "SCRIPT_DIR" in symbols
    assert "data_dir" in symbols


def test_anchor_scanner_handles_annassign_and_tuple_targets(tmp_path, monkeypatch):
    monkeypatch.setattr(cpm, "REPO_ROOT", tmp_path)
    mod = tmp_path / "an_audit.py"
    mod.write_text(
        "from pathlib import Path\n"
        "SCRIPT_DIR: Path = Path(__file__).resolve().parent\n"
        "revision, blob = ('r', 'b')\n"
        "target, extra = SCRIPT_DIR, 'x'\n",
        encoding="utf-8",
    )
    anchors = cpm.find_anchors_in_file(mod)
    symbols = {a.symbol for a in anchors}
    assert "SCRIPT_DIR" in symbols  # AnnAssign anchor
    assert "target" in symbols  # tuple target deriving from an anchor
    assert "revision" not in symbols  # tuple target, unrelated RHS
    assert "blob" not in symbols


def test_manual_dispositions_are_present_in_regenerated_exemptions():
    """The two hand-reviewed 'impossible removal plan' rows build-review
    named must carry an honest not-applicable disposition, not a
    default 'pending relocation... P4' claim that will never happen."""
    rows = cpm.load_exemptions()
    by_key = {(r["path"], r["symbol"]): r for r in rows}
    baselines_row = by_key[(
        "plugins/setec-voiceprint/scripts/argument_register_baselines.py",
        "_REPO_ROOT",
    )]
    assert baselines_row["removal_phase"] == "not-applicable"
    assert "repository root" in baselines_row["reason"].lower()

    revision_row = by_key[(
        "plugins/setec-voiceprint/scripts/near_dup_dedup.py", "revision",
    )]
    assert revision_row["removal_phase"] == "not-applicable"
    assert "self-referential" in revision_row["reason"].lower()


def test_check_ghost_rows_catches_a_row_with_no_matching_anchor():
    anchors = [cpm.Anchor(path="a.py", symbol="X", lineno=1, expr="x")]
    rows = [{
        "path": "a.py", "symbol": "GHOST", "reason": "r", "owner": "o",
        "introduced_sha": "s", "removal_phase": "P4",
    }]
    problems = cpm.check_ghost_rows(rows, anchors)
    assert len(problems) == 1
    assert "GHOST" in problems[0]


def test_check_ratchet_flags_a_new_row_beyond_an_empty_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(cpm, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cpm, "EXEMPTIONS_PATH", tmp_path / "exemptions.yaml")
    (tmp_path / "exemptions.yaml").write_text(
        "schema_version: 1\nexemptions: []\n", encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=tmp_path, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip()
    # The exemptions file at THIS sha has zero rows — a genuinely new row
    # added since is fine (there's a baseline, but it's empty, not absent).
    (tmp_path / "exemptions.yaml").write_text(
        json.dumps({"schema_version": 1, "exemptions": [{
            "path": "a.py", "symbol": "X", "reason": "r", "owner": "o",
            "introduced_sha": sha, "removal_phase": "P4",
        }]}), encoding="utf-8",
    )
    problems = cpm.check_ratchet(sha)
    assert problems  # a new row beyond the (empty) merge-base baseline


def test_check_ratchet_missing_file_at_merge_base_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setattr(cpm, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cpm, "EXEMPTIONS_PATH", tmp_path / "exemptions.yaml")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "other.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base, no exemptions file yet"], cwd=tmp_path, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True,
    ).stdout.strip()
    (tmp_path / "exemptions.yaml").write_text(
        json.dumps({"schema_version": 1, "exemptions": [{
            "path": "a.py", "symbol": "X", "reason": "r", "owner": "o",
            "introduced_sha": sha, "removal_phase": "P4",
        }]}), encoding="utf-8",
    )
    problems = cpm.check_ratchet(sha)
    assert problems == []  # nothing to ratchet against — this PR's own situation
