#!/usr/bin/env python3
"""Focused regression coverage for the opt-in sync conflict-copy preflight."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import manifest_validator as mv  # type: ignore


def _manifest(tmp_path: Path, *, warning: bool = False) -> Path:
    source = tmp_path / "source.txt"
    source.write_text("ordinary synthetic test text", encoding="utf-8")
    entry = {
        "id": "synthetic-entry",
        "path": source.name,
        "ai_status": "pre_ai_human",
        "use": ["validation"],
    }
    if warning:
        entry["unrecognized_synthetic_field"] = True
    manifest = tmp_path / "corpus_manifest.jsonl"
    manifest.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    return manifest


def _run(manifest: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(ROOT / "manifest_validator.py"), str(manifest), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_flag_absent_preserves_legacy_behavior(tmp_path: Path):
    manifest = _manifest(tmp_path)
    baseline = _run(manifest, "--json", "--progress-every", "0")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "device's conflicted copy.txt").write_text("not inspected", encoding="utf-8")

    legacy = _run(manifest, "--json", "--progress-every", "0")
    assert (legacy.returncode, legacy.stdout, legacy.stderr) == (
        baseline.returncode, baseline.stdout, baseline.stderr,
    )
    payload = json.loads(legacy.stdout)
    assert "conflict_copy_check" not in payload["results"]


def test_clean_opt_in_preserves_validator_exit_behavior(tmp_path: Path):
    clean = _run(_manifest(tmp_path), "--check-conflict-copies", "--json", "--progress-every", "0")
    assert clean.returncode == 0
    assert json.loads(clean.stdout)["results"]["conflict_copy_check"] == {
        "checked": True, "root": ".", "n_matches": 0, "matches": [],
        "n_scan_errors": 0, "scan_errors": [], "validation_ran": True,
    }

    warning_root = tmp_path / "warning"
    warning_root.mkdir()
    warned = _run(
        _manifest(warning_root, warning=True), "--check-conflict-copies", "--strict",
        "--json", "--progress-every", "0",
    )
    assert warned.returncode == 1


def test_matches_are_relative_sorted_and_directory_is_not_recursively_matched(tmp_path: Path):
    manifest = _manifest(tmp_path)
    names = [
        "z CONFLICTED COPY.txt",
        "nested/A conflicted copy.txt",
        "nested/owner's conflicted copy 2026-07-21",
    ]
    (tmp_path / names[0]).write_text("x", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / names[1]).write_text("x", encoding="utf-8")
    matching_directory = tmp_path / names[2]
    matching_directory.mkdir()
    (matching_directory / "ordinary-child.txt").write_text("x", encoding="utf-8")
    for nonmatch in ("conflicted.txt", "copy.txt", "conflicted-copy.txt"):
        (tmp_path / nonmatch).write_text("x", encoding="utf-8")

    check = mv.check_conflict_copies(manifest)
    assert check["matches"] == sorted(names, key=lambda path: (path.casefold(), path))
    assert all("ordinary-child" not in path for path in check["matches"])
    assert all(not path.startswith(str(tmp_path)) and "\\" not in path for path in check["matches"])


@pytest.mark.skipif(os.sep != "/", reason="literal backslash basenames are POSIX-only")
def test_literal_backslash_basenames_stay_distinct_and_never_fake_traversal(tmp_path: Path):
    manifest = _manifest(tmp_path)
    literal_names = [r"..\conflicted copy.txt", r"a\conflicted copy.txt"]
    for name in literal_names:
        (tmp_path / name).write_text("x", encoding="utf-8")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "conflicted copy.txt").write_text("x", encoding="utf-8")

    check = mv.check_conflict_copies(manifest)
    expected = sorted(
        [*literal_names, "a/conflicted copy.txt"],
        key=lambda path: (path.casefold(), path),
    )
    assert check["matches"] == expected
    assert check["n_matches"] == len(set(expected)) == 3
    assert "../conflicted copy.txt" not in check["matches"]


def test_matching_link_is_reported_once_and_nonmatching_link_is_pruned(tmp_path: Path):
    manifest = _manifest(tmp_path)
    outside = tmp_path.parent / "outside-conflict-copy-target"
    outside.mkdir(exist_ok=True)
    (outside / "conflicted copy outside.txt").write_text("x", encoding="utf-8")
    matching_link = tmp_path / "matching conflicted copy link"
    plain_link = tmp_path / "plain-link"
    try:
        matching_link.symlink_to(outside, target_is_directory=True)
        plain_link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable on this platform")

    check = mv.check_conflict_copies(manifest)
    assert check["matches"] == ["matching conflicted copy link"]
    assert check["n_scan_errors"] == 0


def test_guarded_windows_junction_is_pruned(monkeypatch, tmp_path: Path):
    manifest = _manifest(tmp_path)
    junction = tmp_path / "synthetic-junction"
    junction.mkdir()
    (junction / "conflicted copy behind junction.txt").write_text("x", encoding="utf-8")
    original = getattr(Path, "is_junction", None)

    def is_synthetic_junction(path: Path) -> bool:
        if path == junction:
            return True
        return original(path) if original is not None else False

    monkeypatch.setattr(Path, "is_junction", is_synthetic_junction, raising=False)
    assert mv.check_conflict_copies(manifest)["matches"] == []


def test_windows_reparse_attribute_helper_covers_nonjunction_types(monkeypatch):
    marker = 0x400
    monkeypatch.setattr(mv.stat, "FILE_ATTRIBUTE_REPARSE_POINT", marker, raising=False)

    class ReparseStat:
        st_file_attributes = marker

    class ReparseEntry:
        def stat(self, *, follow_symlinks: bool):
            assert follow_symlinks is False
            return ReparseStat()

    assert mv._is_windows_reparse_point(ReparseEntry()) is True


def test_nonjunction_directory_reparse_point_is_pruned(monkeypatch, tmp_path: Path):
    manifest = _manifest(tmp_path)
    placeholder = tmp_path / "cloud-placeholder"
    placeholder.mkdir()
    (placeholder / "conflicted copy behind reparse.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        mv, "_is_windows_reparse_point",
        lambda entry: entry.name == placeholder.name,
    )
    assert mv.check_conflict_copies(manifest)["matches"] == []


def test_injected_scan_error_refuses_with_sanitized_location(monkeypatch, tmp_path: Path):
    manifest = _manifest(tmp_path)
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    real_scandir = os.scandir

    def denied(path):
        if Path(path) == blocked:
            raise PermissionError("synthetic absolute detail must not escape")
        return real_scandir(path)

    monkeypatch.setattr(mv.os, "scandir", denied)
    check = mv.check_conflict_copies(manifest)
    assert check["validation_ran"] is False
    assert check["scan_errors"] == [{"path": "blocked", "error": "PermissionError"}]


def test_injected_scan_error_cli_returns_two_without_traceback(monkeypatch, tmp_path: Path, capsys):
    manifest = _manifest(tmp_path)
    refused = {
        "checked": True, "root": ".", "n_matches": 0, "matches": [],
        "n_scan_errors": 1,
        "scan_errors": [{"path": "blocked", "error": "PermissionError"}],
        "validation_ran": False,
    }
    monkeypatch.setattr(mv, "check_conflict_copies", lambda _: refused)
    monkeypatch.setattr(sys, "argv", [
        "manifest_validator.py", str(manifest), "--check-conflict-copies",
        "--json", "--progress-every", "0",
    ])
    assert mv.main() == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["results"]["conflict_copy_check"] == refused
    assert payload["warnings"] == [
        "Conflict-copy preflight refused manifest validation; see results.conflict_copy_check."
    ]
    assert "Traceback" not in captured.err and str(tmp_path) not in captured.err


@pytest.mark.parametrize("manifest_case", ["clean", "invalid", "strict-warning"])
def test_refusal_precedes_manifest_results_and_reports_null_counts(
    tmp_path: Path, manifest_case: str,
):
    manifest = _manifest(tmp_path, warning=manifest_case == "strict-warning")
    if manifest_case == "invalid":
        manifest.write_text("{not valid json}\n", encoding="utf-8")
    (tmp_path / "conflicted copy.txt").write_text("x", encoding="utf-8")
    extra = ("--strict",) if manifest_case == "strict-warning" else ()
    completed = _run(
        manifest, "--check-conflict-copies", "--json", "--progress-every", "0", *extra,
    )
    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    result = payload["results"]
    assert result["n_entries"] is result["n_errors"] is result["n_warnings"] is None
    assert result["issues"] == result["tripwires"] == [] and result["summary"] == {}
    assert result["conflict_copy_check"]["validation_ran"] is False
    assert payload["warnings"] == [
        "Conflict-copy preflight refused manifest validation; see results.conflict_copy_check."
    ]
    markdown = _run(manifest, "--check-conflict-copies", "--progress-every", "0")
    assert markdown.returncode == 2
    assert b"Manifest validation: NOT RUN (preflight refused)" in markdown.stdout
    assert b"Manifest is clean." not in markdown.stdout
    assert b"- `conflicted copy.txt`" in markdown.stdout


def test_all_sinks_carry_the_complete_sorted_refusal_list(tmp_path: Path):
    manifest = _manifest(tmp_path)
    expected = ["A conflicted copy.txt", "nested/z CONFLICTED COPY.txt"]
    (tmp_path / expected[0]).write_text("x", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / expected[1]).write_text("x", encoding="utf-8")

    json_run = _run(
        manifest, "--check-conflict-copies", "--json", "--progress-every", "0",
    )
    markdown_run = _run(
        manifest, "--check-conflict-copies", "--progress-every", "0",
    )
    out_path = tmp_path / "refusal.json"
    out_run = _run(
        manifest, "--check-conflict-copies", "--json", "--progress-every", "0",
        "--out", str(out_path),
    )

    assert json_run.returncode == markdown_run.returncode == out_run.returncode == 2
    assert json.loads(json_run.stdout)["results"]["conflict_copy_check"]["matches"] == expected
    assert json.loads(out_path.read_bytes())["results"]["conflict_copy_check"]["matches"] == expected
    assert out_run.stdout == b""
    for path in expected:
        assert f"- `{path}`".encode() in markdown_run.stdout


def test_flag_mode_stdout_and_out_are_identical_utf8_lf_bytes(tmp_path: Path):
    manifest = _manifest(tmp_path)
    stdout_run = _run(manifest, "--check-conflict-copies", "--json", "--progress-every", "0")
    out_path = tmp_path / "report.json"
    out_run = _run(
        manifest, "--check-conflict-copies", "--json", "--progress-every", "0",
        "--out", str(out_path),
    )
    artifact = out_path.read_bytes()
    assert stdout_run.returncode == out_run.returncode == 0
    assert out_run.stdout == b""
    assert stdout_run.stdout == artifact
    assert artifact.endswith(b"\n") and not artifact.endswith(b"\n\n")
    assert b"\r" not in artifact
    artifact.decode("utf-8")
