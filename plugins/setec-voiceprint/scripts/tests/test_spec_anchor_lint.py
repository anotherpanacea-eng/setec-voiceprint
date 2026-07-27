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
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import spec_anchor_lint as sal  # noqa: E402
import check_changed_spec_anchors as csa  # noqa: E402


CHANGED_GATE = TOOLS / "check_changed_spec_anchors.py"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _make_changed_gate_repo(root: Path) -> tuple[Path, Path]:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Spec Gate Test")
    _git(repo, "config", "user.email", "spec-gate@example.invalid")
    (repo / "specs").mkdir()
    (repo / "tools").mkdir()
    linter = repo / "tools" / "stub_linter.py"
    linter.write_text(
        "\n".join(
            [
                "import json, os, sys",
                "spec = sys.argv[sys.argv.index('--spec') + 1]",
                "with open(os.environ['SPEC_LINT_LOG'], 'a', encoding='utf-8') as out:",
                "    out.write(json.dumps(spec, ensure_ascii=False) + '\\n')",
                "raise SystemExit(int(os.environ.get('SPEC_LINT_RC', '0')))",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return repo, linter


def _run_changed_gate(
    repo: Path,
    *,
    base: str,
    linter: Path,
    log: Path,
    lint_rc: int = 0,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["SPEC_LINT_LOG"] = str(log)
    env["SPEC_LINT_RC"] = str(lint_rc)
    return subprocess.run(
        [
            sys.executable,
            str(CHANGED_GATE),
            "--repo",
            str(repo),
            "--base",
            base,
            "--linter",
            str(linter),
        ],
        capture_output=True,
        text=True,
        env=env,
    )


def test_changed_spec_gate_fails_closed_when_base_is_missing(tmp_path):
    repo, linter = _make_changed_gate_repo(tmp_path)
    (repo / "specs" / "12-base.md").write_text("# Spec 12\n", encoding="utf-8")
    _commit(repo, "base")
    result = _run_changed_gate(
        repo,
        base="refs/heads/does-not-exist",
        linter=linter,
        log=tmp_path / "lint.log",
    )
    assert result.returncode == 1
    assert "Unable to enumerate changed specs" in result.stderr


def test_changed_spec_gate_skips_a_true_no_spec_diff(tmp_path):
    repo, linter = _make_changed_gate_repo(tmp_path)
    (repo / "specs" / "12-base.md").write_text("# Spec 12\n", encoding="utf-8")
    base = _commit(repo, "base")
    (repo / "README.md").write_text("not a spec\n", encoding="utf-8")
    _commit(repo, "non-spec")
    log = tmp_path / "lint.log"
    result = _run_changed_gate(repo, base=base, linter=linter, log=log)
    assert result.returncode == 0
    assert "No spec changes" in result.stdout
    assert not log.exists()


def test_changed_spec_gate_preserves_whitespace_unicode_and_newline_path(tmp_path):
    repo, linter = _make_changed_gate_repo(tmp_path)
    name = "98 probe-é\nline.md"
    path = repo / "specs" / name
    path.write_text("# Spec 98\n", encoding="utf-8")
    base = _commit(repo, "base")
    path.write_text("# Spec 98\n\nchanged\n", encoding="utf-8")
    _commit(repo, "change odd path")
    log = tmp_path / "lint.log"
    result = _run_changed_gate(repo, base=base, linter=linter, log=log)
    assert result.returncode == 0, result.stderr
    linted = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert linted == [str(path)]


@pytest.mark.parametrize(
    "payload",
    [
        b"M\0specs/12-base.md",
        b"M\0\0",
        b"M100\0specs/12-base.md\0",
        b"R101\0specs/12-old.md\0specs/12-new.md\0",
        b"R55\0specs/12-old.md\0specs/12-new.md\0",
        b"C999\0specs/12-old.md\0specs/12-new.md\0",
        b"R100\0specs/12-old.md\0",
    ],
)
def test_changed_spec_gate_rejects_malformed_name_status(payload):
    with pytest.raises(csa.Refusal):
        csa._parse_name_status(payload)


@pytest.mark.parametrize("status", ["R000", "R055", "R100", "C000", "C055", "C100"])
def test_changed_spec_gate_accepts_git_similarity_statuses(status):
    parsed = csa._parse_name_status(
        status.encode("ascii") + b"\0specs/12-old.md\0specs/12-new.md\0"
    )
    assert parsed == [
        csa.Change(
            status=status,
            old_path="specs/12-old.md",
            new_path="specs/12-new.md",
        )
    ]


def test_changed_spec_gate_rejects_a_spec_symlink_type_change(tmp_path):
    repo, linter = _make_changed_gate_repo(tmp_path)
    spec = repo / "specs" / "12-base.md"
    spec.write_text("`definitely_missing.py`\n", encoding="utf-8")
    (repo / "README.md").write_text("no anchors\n", encoding="utf-8")
    base = _commit(repo, "base")
    spec.unlink()
    spec.symlink_to("../README.md")
    _commit(repo, "replace spec with symlink")
    result = _run_changed_gate(
        repo, base=base, linter=linter, log=tmp_path / "lint.log"
    )
    assert result.returncode == 1
    assert "not a direct regular file" in result.stderr


def test_changed_spec_gate_accepts_a_partial_similarity_rename(tmp_path):
    repo, linter = _make_changed_gate_repo(tmp_path)
    old = repo / "specs" / "98-old.md"
    old.write_text(
        "\n".join(f"stable distinct line {index}" for index in range(100)) + "\n",
        encoding="utf-8",
    )
    base = _commit(repo, "base")
    new = repo / "specs" / "98-new.md"
    _git(repo, "mv", str(old.relative_to(repo)), str(new.relative_to(repo)))
    new.write_text(
        "\n".join(
            [
                *[f"stable distinct line {index}" for index in range(60)],
                *[f"replacement distinct line {index}" for index in range(40)],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _commit(repo, "partial rename")
    name_status = _git(
        repo, "diff", "--name-status", "--find-renames", f"{base}...HEAD"
    ).stdout
    assert name_status.startswith("R") and not name_status.startswith("R100")
    log = tmp_path / "lint.log"
    result = _run_changed_gate(repo, base=base, linter=linter, log=log)
    assert result.returncode == 0, result.stderr
    assert [Path(json.loads(line)).name for line in log.read_text().splitlines()] == [
        "98-new.md"
    ]


def test_changed_spec_gate_rejects_deleted_spec_with_live_number_reference(tmp_path):
    repo, linter = _make_changed_gate_repo(tmp_path)
    (repo / "specs" / "12-consumer.md").write_text(
        "Depends on spec 98.\n", encoding="utf-8"
    )
    removed = repo / "specs" / "98-removed.md"
    removed.write_text("# Spec 98\n", encoding="utf-8")
    base = _commit(repo, "base")
    removed.unlink()
    _commit(repo, "delete spec")
    result = _run_changed_gate(
        repo, base=base, linter=linter, log=tmp_path / "lint.log"
    )
    assert result.returncode == 1
    assert "retains an anchor" in result.stderr
    assert "98-removed.md" in result.stderr


def test_dependency_error_escapes_workflow_commands_in_surviving_path(tmp_path):
    repo, linter = _make_changed_gate_repo(tmp_path)
    consumer = repo / "specs" / "12\n::warning::pwn.md"
    consumer.write_text("Depends on spec 98.\n", encoding="utf-8")
    removed = repo / "specs" / "98-removed.md"
    removed.write_text("# Spec 98\n", encoding="utf-8")
    base = _commit(repo, "base")
    removed.unlink()
    _commit(repo, "delete spec")
    result = _run_changed_gate(
        repo, base=base, linter=linter, log=tmp_path / "lint.log"
    )
    assert result.returncode == 1
    assert "\n::warning::" not in result.stderr
    assert "\\n::warning::pwn.md" in result.stderr


def test_changed_spec_gate_scans_nested_surviving_specs_for_old_paths(tmp_path):
    repo, linter = _make_changed_gate_repo(tmp_path)
    nested = repo / "specs" / "nested"
    nested.mkdir()
    (nested / "12-consumer.md").write_text(
        "See specs/98-old.md.\n", encoding="utf-8"
    )
    old = repo / "specs" / "98-old.md"
    new = repo / "specs" / "98-new.md"
    old.write_text("# Spec 98\n", encoding="utf-8")
    base = _commit(repo, "base")
    _git(repo, "mv", str(old.relative_to(repo)), str(new.relative_to(repo)))
    _commit(repo, "rename spec")
    result = _run_changed_gate(
        repo, base=base, linter=linter, log=tmp_path / "lint.log"
    )
    assert result.returncode == 1
    assert "nested/12-consumer.md" in result.stderr
    assert "98-old.md" in result.stderr


def test_changed_spec_gate_catches_bare_number_for_nested_deleted_spec(tmp_path):
    repo, linter = _make_changed_gate_repo(tmp_path)
    (repo / "specs" / "12-consumer.md").write_text(
        "Depends on spec 98.\n", encoding="utf-8"
    )
    nested = repo / "specs" / "nested"
    nested.mkdir()
    removed = nested / "98-old.md"
    removed.write_text("# Spec 98\n", encoding="utf-8")
    base = _commit(repo, "base")
    removed.unlink()
    _commit(repo, "delete nested spec")
    result = _run_changed_gate(
        repo, base=base, linter=linter, log=tmp_path / "lint.log"
    )
    assert result.returncode == 1
    assert "12-consumer.md" in result.stderr
    assert "nested/98-old.md" in result.stderr


def test_changed_spec_gate_rejects_rename_with_live_old_path_reference(tmp_path):
    repo, linter = _make_changed_gate_repo(tmp_path)
    consumer = repo / "specs" / "12-consumer.md"
    consumer.write_text("See specs/98-old.md.\n", encoding="utf-8")
    old = repo / "specs" / "98-old.md"
    new = repo / "specs" / "98-new.md"
    old.write_text("# Spec 98\n", encoding="utf-8")
    base = _commit(repo, "base")
    _git(repo, "mv", str(old.relative_to(repo)), str(new.relative_to(repo)))
    _commit(repo, "rename spec")
    result = _run_changed_gate(
        repo, base=base, linter=linter, log=tmp_path / "lint.log"
    )
    assert result.returncode == 1
    assert "98-old.md" in result.stderr


def test_changed_spec_gate_lints_renamed_spec_after_dependents_move(tmp_path):
    repo, linter = _make_changed_gate_repo(tmp_path)
    consumer = repo / "specs" / "12-consumer.md"
    consumer.write_text("See specs/98-old.md.\n", encoding="utf-8")
    old = repo / "specs" / "98-old.md"
    new = repo / "specs" / "98-new.md"
    old.write_text("# Spec 98\n", encoding="utf-8")
    base = _commit(repo, "base")
    _git(repo, "mv", str(old.relative_to(repo)), str(new.relative_to(repo)))
    consumer.write_text("See specs/98-new.md.\n", encoding="utf-8")
    _commit(repo, "rename and update")
    log = tmp_path / "lint.log"
    result = _run_changed_gate(repo, base=base, linter=linter, log=log)
    assert result.returncode == 0, result.stderr
    linted = {Path(json.loads(line)).name for line in log.read_text().splitlines()}
    assert linted == {"12-consumer.md", "98-new.md"}


def test_changed_spec_gate_propagates_linter_failure(tmp_path):
    repo, linter = _make_changed_gate_repo(tmp_path)
    spec = repo / "specs" / "12-base.md"
    spec.write_text("# Spec 12\n", encoding="utf-8")
    base = _commit(repo, "base")
    spec.write_text("# Spec 12\n\nchanged\n", encoding="utf-8")
    _commit(repo, "change spec")
    result = _run_changed_gate(
        repo,
        base=base,
        linter=linter,
        log=tmp_path / "lint.log",
        lint_rc=7,
    )
    assert result.returncode == 1


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


def test_path_qualified_claim_does_not_resolve_via_unrelated_basename(tmp_path):
    # Codex #249: a PATH-QUALIFIED ref must match its relative path exactly. `helper.py` exists at
    # `tools/helper.py`, but `missing/subdir/helper.py` must NOT resolve through that shared basename
    # — that's exactly the wrong anchor the gate exists to catch.
    root = _make_repo(tmp_path)
    assert _lint("uses `missing/subdir/helper.py`", root)["gated"] is True
    assert _lint("uses `tools/helper.py`", root)["gated"] is False     # exact path still resolves
    assert _lint("uses `helper.py`", root)["gated"] is False           # bare basename still advisory


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
