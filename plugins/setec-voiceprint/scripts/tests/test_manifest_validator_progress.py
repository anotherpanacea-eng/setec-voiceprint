#!/usr/bin/env python3
"""Progress-heartbeat regressions for long manifest validation runs."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import manifest_validator as mv  # type: ignore


def _entry(source: Path, idx: int) -> dict:
    return {
        "id": f"entry-{idx}", "path": source.name, "ai_status": "pre_ai_human",
        "use": ["validation"],
    }


def _manifest(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "corpus_manifest.jsonl"
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def test_scan_cadence_counts_bad_candidates_and_completion_is_final(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_text("text", encoding="utf-8")
    manifest = _manifest(tmp_path, [
        "# ignored", "", json.dumps(_entry(source, 1)), "{bad-json", "[]",
    ])
    ticks = iter((100.0, 100.04, 100.16))
    monkeypatch.setattr(mv.time, "monotonic", lambda: next(ticks))
    progress = io.StringIO()

    result = mv.validate_manifest(manifest, progress_every=2, progress_stream=progress)

    assert result["n_entries"] == 1 and result["n_errors"] == 2
    assert progress.getvalue().splitlines() == [
        "[manifest_validator] phase=scan rows=2 entries=1 issues_so_far=1 elapsed_seconds=0.0",
        "[manifest_validator] phase=complete rows=3 entries=1 errors=2 warnings=0 elapsed_seconds=0.2",
    ]
    assert "entry-1" not in progress.getvalue() and str(tmp_path) not in progress.getvalue()


def test_exact_multiple_still_gets_post_scan_completion(monkeypatch, tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_text("text", encoding="utf-8")
    manifest = _manifest(tmp_path, [json.dumps(_entry(source, i)) for i in (1, 2)])
    ticks = iter((10.0, 10.0, 10.0))
    monkeypatch.setattr(mv.time, "monotonic", lambda: next(ticks))
    progress = io.StringIO()

    mv.validate_manifest(manifest, progress_every=2, progress_stream=progress)

    assert [line.split()[1] for line in progress.getvalue().splitlines()] == [
        "phase=scan", "phase=complete",
    ]


def test_empty_manifest_emits_completion_and_library_default_is_silent(tmp_path: Path, capsys):
    manifest = _manifest(tmp_path, [])
    progress = io.StringIO()
    mv.validate_manifest(manifest, progress_every=5, progress_stream=progress)
    assert "phase=complete rows=0 entries=0 errors=0 warnings=0" in progress.getvalue()

    mv.validate_manifest(manifest)
    assert capsys.readouterr().err == ""


def test_nonexistent_manifest_still_emits_completion_heartbeat(tmp_path: Path):
    # The unconditional completion contract must hold on the earliest bail-out, too.
    missing = tmp_path / "does_not_exist.jsonl"
    progress = io.StringIO()

    result = mv.validate_manifest(missing, progress_every=5, progress_stream=progress)

    assert result["n_errors"] == 1
    lines = progress.getvalue().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith(
        "[manifest_validator] phase=complete rows=0 entries=0 errors=1 warnings=0"
    )
    # aggregate-only: the emission carries counts, never the path.
    assert str(missing) not in progress.getvalue()


def test_unreadable_manifest_still_emits_completion_heartbeat(tmp_path: Path):
    # A directory at the manifest path exists() but raises OSError on read_text: the
    # unreadable-file bail-out. It must still emit the completion heartbeat.
    unreadable = tmp_path / "manifest_as_dir"
    unreadable.mkdir()
    progress = io.StringIO()

    result = mv.validate_manifest(unreadable, progress_every=5, progress_stream=progress)

    assert result["n_errors"] == 1
    lines = progress.getvalue().splitlines()
    assert len(lines) == 1
    assert lines[0].startswith(
        "[manifest_validator] phase=complete rows=0 entries=0 errors=1 warnings=0"
    )
    assert str(unreadable) not in progress.getvalue()


@pytest.mark.parametrize("progress_every, stream", [
    (-1, None), (True, None), (1.5, None), (1, None), (0, io.StringIO()),
])
def test_progress_option_contract_refuses_ambiguous_values(
    tmp_path: Path, progress_every, stream,
):
    manifest = _manifest(tmp_path, [])
    with pytest.raises(ValueError, match="progress"):
        mv.validate_manifest(manifest, progress_every=progress_every, progress_stream=stream)


def test_cli_heartbeat_stays_on_stderr_and_json_stdout_is_clean(monkeypatch, tmp_path: Path, capsys):
    source = tmp_path / "source.txt"
    source.write_text("text", encoding="utf-8")
    manifest = _manifest(tmp_path, [json.dumps(_entry(source, 1))])
    monkeypatch.setattr(sys, "argv", [
        "manifest_validator.py", str(manifest), "--json", "--progress-every", "1",
    ])

    assert mv.main() == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["tool"] == "manifest_validator"
    assert "phase=scan" in captured.err and "phase=complete" in captured.err
    assert str(manifest) not in captured.err and "entry-1" not in captured.err


def test_cli_rejects_negative_progress_cadence(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "manifest_validator.py", "unused.jsonl", "--progress-every", "-1",
    ])
    with pytest.raises(SystemExit) as exc:
        mv.main()
    assert exc.value.code == 2
    assert "non-negative integer" in capsys.readouterr().err
