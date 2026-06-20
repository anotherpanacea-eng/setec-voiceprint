#!/usr/bin/env python3
"""Tests for tools/spec_anchor_lint.py.

Pins (against a synthetic repo so the suite is hermetic):

  * file:line — real file + in-range resolves; missing file or out-of-range gates.
  * file path — a real .py resolves; a phantom .py gates; a cross-tree .md advises (no gate).
  * sibling-spec — `spec NN` with a matching specs/NN-*.md resolves; absent gates.
  * env-var — a prefixed var present in source resolves; an invented one gates.
  * symbol / flag — absent is MEDIUM (advisory) and does not gate unless --strict.
  * conservative extraction — prose words in backticks are not high-flagged.
  * --json emits per-reference records + a `gated` bool.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import spec_anchor_lint as sal  # noqa: E402


def _make_repo(root: Path) -> Path:
    (root / "tools").mkdir()
    (root / "tools" / "helper.py").write_text(
        "\n".join(f"x{i} = {i}" for i in range(50)), encoding="utf-8")   # 50 lines
    (root / "src").mkdir()
    (root / "src" / "core.py").write_text(
        "VOICEWRIGHT_REAL_BASE = 1\n\ndef real_func():\n    return 1\n# cli: --real-flag\n",
        encoding="utf-8")
    (root / "specs").mkdir()
    (root / "specs" / "12-bar.md").write_text("# spec 12 — bar\n", encoding="utf-8")
    return root


def _lint(text: str, root: Path, strict: bool = False) -> dict:
    return sal.lint(text, sal.build_repo_index(root), strict=strict)


def test_file_line_in_range_resolves_out_of_range_gates(tmp_path):
    root = _make_repo(tmp_path)
    assert _lint("see `src/core.py:3`", root)["gated"] is False
    r = _lint("see `core.py:999`", root)               # out of range
    assert r["gated"] is True and r["high_absent"][0].kind == "file_line"
    assert _lint("see `ghost.py:1`", root)["gated"] is True   # missing file


def test_file_path_py_gates_md_advises(tmp_path):
    root = _make_repo(tmp_path)
    assert _lint("uses `src/core.py`", root)["gated"] is False
    assert _lint("uses `tools/ghost.py`", root)["gated"] is True       # phantom .py gates
    r = _lint("see `SHORT-LIST.md` and `notes/scratch.md`", root)      # cross-tree docs
    assert r["gated"] is False and r["absent"] == 2                    # advised, not gated


def test_sibling_spec_present_vs_absent(tmp_path):
    root = _make_repo(tmp_path)
    assert _lint("mirrors spec 12", root)["gated"] is False
    assert _lint("grounded on spec 26", root)["gated"] is True
    assert _lint("see specs/12-bar.md", root)["gated"] is False


def test_env_var_present_vs_invented(tmp_path):
    root = _make_repo(tmp_path)
    assert _lint("resolve VOICEWRIGHT_REAL_BASE", root)["gated"] is False
    r = _lint("resolve from VOICEWRIGHT_JUDGE_MODEL", root)
    assert r["gated"] is True and r["high_absent"][0].kind == "env_var"


def test_symbol_and_flag_are_medium_until_strict(tmp_path):
    root = _make_repo(tmp_path)
    # absent symbol + absent flag → advisory, no gate
    r = _lint("call `ghost_func` with `--ghost-flag`", root)
    assert r["gated"] is False and r["absent"] == 2
    # --strict promotes them to gating
    assert _lint("call `ghost_func`", root, strict=True)["gated"] is True
    # present ones resolve
    assert _lint("call `real_func` with `--real-flag`", root)["absent"] == 0


def test_prose_in_backticks_is_not_high_flagged(tmp_path):
    root = _make_repo(tmp_path)
    r = _lint("the `target` `verdict` `band` are descriptive", root)
    assert r["gated"] is False
    # single english words without an underscore are skipped, not flagged absent
    assert all(ref.kind != "symbol" for ref in r["references"])


def test_ambiguous_basename_is_not_gated(tmp_path):
    # A basename present in >1 location (e.g. __init__.py) is present-but-ambiguous,
    # NOT absent — gating it would be a false positive (the P2 the review caught).
    root = _make_repo(tmp_path)
    (root / "pkg").mkdir()
    (root / "pkg" / "dup.py").write_text("a = 1\n", encoding="utf-8")
    (root / "src" / "dup.py").write_text("b = 2\n", encoding="utf-8")
    assert _lint("see `dup.py`", root)["gated"] is False


def test_env_var_substring_is_not_a_false_positive(tmp_path):
    # An invented SETEC_FOO must NOT resolve just because SETEC_FOO_BAR exists in
    # source — env-var is a gating type, so a substring match is a false negative.
    root = _make_repo(tmp_path)
    (root / "src" / "more.py").write_text("SETEC_FOO_BAR = 1\n", encoding="utf-8")
    r = _lint("reads SETEC_FOO from the env", root)
    assert r["gated"] is True and r["high_absent"][0].kind == "env_var"
    # the real, full token still resolves
    assert _lint("reads SETEC_FOO_BAR", root)["gated"] is False


def test_cli_flag_substring_is_not_a_false_positive(tmp_path):
    # --ref must not resolve inside --reference-filter (whole-flag match).
    root = _make_repo(tmp_path)
    (root / "src" / "cli.py").write_text("# --reference-filter\n", encoding="utf-8")
    r = _lint("pass `--ref`", root, strict=True)   # strict so the medium flag gates
    assert r["gated"] is True
    assert _lint("pass `--reference-filter`", root)["absent"] == 0


def test_json_cli_emits_records_and_gated(tmp_path):
    root = _make_repo(tmp_path)
    spec = tmp_path / "s.md"
    spec.write_text("uses `tools/ghost.py` and VOICEWRIGHT_FAKE_X", encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(TOOLS / "spec_anchor_lint.py"),
         "--spec", str(spec), "--repo", str(root), "--json"],
        capture_output=True, text=True)
    assert out.returncode == 1                              # gated → non-zero exit
    payload = json.loads(out.stdout)
    assert payload["gated"] is True
    kinds = {r["kind"] for r in payload["references"] if r["status"] == "absent"}
    assert {"file_path", "env_var"} <= kinds
