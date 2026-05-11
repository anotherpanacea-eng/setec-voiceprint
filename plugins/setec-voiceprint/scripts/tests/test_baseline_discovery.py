#!/usr/bin/env python3
"""Regression tests for baseline_discovery.py.

The script searches a list of common locations for the user's
existing ``ai-prose-baselines-private`` folder, summarises each
candidate, and recommends one. Tests pin:

  * Discovery picks up an env-var-pointed folder even when nothing
    else exists.
  * Discovery walks ``tmp_path`` like a fake home dir and finds
    folders matching the marker name at varying depths.
  * Ranking prefers the folder with more manifest entries even when
    another candidate has more impostor personas (the manifest is the
    canonical signal).
  * The recommended path is the one ranked first.
  * The JSON output includes ``export_line`` when a candidate exists
    and ``None`` when none do.
  * ``--validate`` rejects a directory that doesn't end in the
    marker name (because the privacy guard would refuse it).
  * Empty / unreadable subtrees don't crash the summary.
  * The text report renders the recommended marker and surfaces
    duplicate-folder warnings.

No real filesystem assumptions: every test uses ``tmp_path``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

import baseline_discovery as bd  # type: ignore


# --------------- Helpers ----------------------------------------


def _make_baseline(
    root: Path,
    *,
    name: str = bd.PRIVATE_DIR_NAME,
    manifest_entries: int = 0,
    impostor_registers: list[str] | None = None,
    impostor_personas_per_register: int = 0,
    extra_file_sizes: list[int] | None = None,
) -> Path:
    """Build a minimal baselines folder under ``root`` for tests.

    Returns the created baselines path. Manifest entries are written
    as blank JSON-ish lines; the counter only checks for non-empty
    lines so the content doesn't need to validate.
    """
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    if manifest_entries > 0:
        manifest = folder / "manifest.jsonl"
        manifest.write_text(
            "\n".join('{"text_id":"x"}' for _ in range(manifest_entries))
            + "\n",
            encoding="utf-8",
        )
    if impostor_registers:
        for reg in impostor_registers:
            reg_dir = folder / "impostors" / reg
            reg_dir.mkdir(parents=True, exist_ok=True)
            for i in range(impostor_personas_per_register):
                (reg_dir / f"persona_{i}").mkdir(exist_ok=True)
    if extra_file_sizes:
        for idx, size in enumerate(extra_file_sizes):
            (folder / f"blob_{idx}.bin").write_bytes(b"x" * size)
    return folder


# --------------- Env-var path ------------------------------------


def test_env_var_pointing_to_existing_folder_is_discovered(tmp_path: Path):
    """A configured env var should always surface in the candidate list,
    even when filesystem scanning finds nothing else."""
    base = _make_baseline(tmp_path / "obsidian-sync", manifest_entries=5)
    # Use a non-existent script path so the repo_sibling probe misses.
    fake_script = tmp_path / "no-such" / "script.py"
    candidates = bd.discover(
        script_path=fake_script,
        max_depth=0,  # disable filesystem scan
        env_value=str(base),
    )
    assert len(candidates) >= 1
    assert candidates[0].source == "env_var"
    assert candidates[0].exists is True
    assert candidates[0].manifest_entries == 5
    assert candidates[0].is_recommended is True


def test_env_var_pointing_to_missing_path_is_recorded_but_not_recommended(
    tmp_path: Path,
):
    """If the env var points nowhere real, we still report it (so the
    user sees the configuration error) but we never recommend it."""
    fake_script = tmp_path / "no-such" / "script.py"
    candidates = bd.discover(
        script_path=fake_script,
        max_depth=0,
        env_value=str(tmp_path / "does-not-exist"),
    )
    assert len(candidates) == 1
    assert candidates[0].exists is False
    assert candidates[0].is_recommended is False


def test_env_var_pointing_to_wrong_named_folder_is_not_recommended(
    tmp_path: Path,
):
    """Reviewer P2 reproducer: the env var points at a real, populated
    folder whose final directory is named something other than
    ``ai-prose-baselines-private``. Downstream acquisition tools
    enforce a marker-name rule via ``acquisition_core.is_private_safe_path``
    and would refuse to write here. The discovery script must surface
    that mismatch and refuse to recommend the path, otherwise setup
    would tell the user to persist a broken configuration."""
    wrong = tmp_path / "my-baselines"  # not 'ai-prose-baselines-private'
    wrong.mkdir()
    (wrong / "manifest.jsonl").write_text(
        '{"text_id":"x"}\n', encoding="utf-8",
    )
    fake_script = tmp_path / "no-such" / "script.py"
    candidates = bd.discover(
        script_path=fake_script,
        max_depth=0,
        env_value=str(wrong),
    )
    assert len(candidates) == 1
    env_cand = candidates[0]
    assert env_cand.exists is True  # folder is real
    assert env_cand.is_recommended is False  # but not usable
    assert any(
        "ai-prose-baselines-private" in note for note in env_cand.notes
    ), f"expected marker-name note in {env_cand.notes!r}"


def test_env_var_invalid_but_other_valid_folder_present_recommends_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """When the env var points at a mis-named folder AND a correctly
    named folder exists elsewhere, the recommendation should fall
    through to the correctly named folder rather than leaving the
    user with no recommendation at all."""
    wrong = tmp_path / "my-baselines"
    wrong.mkdir()
    (wrong / "manifest.jsonl").write_text(
        '{"text_id":"x"}\n', encoding="utf-8",
    )
    right = _make_baseline(tmp_path / "Documents", manifest_entries=5)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    candidates = bd.discover(
        script_path=tmp_path / "no-such" / "script.py",
        max_depth=4,
        env_value=str(wrong),
    )
    existing = [c for c in candidates if c.exists]
    assert len(existing) == 2
    recommended = [c for c in existing if c.is_recommended]
    assert len(recommended) == 1
    assert recommended[0].path == str(right)
    # And the env-var candidate carries the validation note:
    env_cand = next(c for c in existing if c.source == "env_var")
    assert env_cand.is_recommended is False
    assert any(
        "ai-prose-baselines-private" in note for note in env_cand.notes
    )


def test_render_text_warns_when_env_var_points_at_wrong_named_folder(
    tmp_path: Path,
):
    """The text report must surface the env-var-invalid warning at
    the top of the output (not just inside a per-candidate notes
    block) so the user sees it without having to read the entire
    candidate listing."""
    wrong = tmp_path / "my-baselines"
    wrong.mkdir()
    candidates = bd.discover(
        script_path=tmp_path / "no-such" / "script.py",
        max_depth=0,
        env_value=str(wrong),
    )
    out = bd.render_text(candidates, env_value=str(wrong))
    assert "WARNING" in out
    assert "NOT a usable baselines folder" in out
    # And no export-line printed for an invalid env var.
    assert "No existing folder qualified as recommended." in out


# --------------- Ranking logic -----------------------------------


def test_recommended_is_the_folder_with_most_manifest_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """When two folders exist, the one with more manifest entries wins
    regardless of which container they live in."""
    small = _make_baseline(
        tmp_path / "Documents" / "old", manifest_entries=2,
        impostor_registers=["literary_fiction"],
        impostor_personas_per_register=5,
    )
    big = _make_baseline(
        tmp_path / "Obsidian Vault" / "vault", manifest_entries=50,
    )
    # Point HOME at tmp_path so the scanner finds both.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    candidates = bd.discover(
        script_path=tmp_path / "no-such" / "script.py",
        max_depth=4,
        env_value=None,
    )
    existing = [c for c in candidates if c.exists]
    assert len(existing) == 2
    recommended = [c for c in existing if c.is_recommended]
    assert len(recommended) == 1
    assert recommended[0].path == str(big)


def test_ranking_falls_back_to_impostor_count_when_manifest_ties(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Both folders have zero manifest entries; the one with more
    impostor personas should win."""
    a = _make_baseline(
        tmp_path / "Documents" / "a",
        impostor_registers=["literary_fiction"],
        impostor_personas_per_register=1,
    )
    b = _make_baseline(
        tmp_path / "Documents" / "b",
        impostor_registers=["literary_fiction", "blog_essay"],
        impostor_personas_per_register=3,
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    candidates = bd.discover(
        script_path=tmp_path / "no-such" / "script.py",
        max_depth=4,
        env_value=None,
    )
    existing = [c for c in candidates if c.exists]
    assert len(existing) == 2
    recommended = next(c for c in existing if c.is_recommended)
    assert recommended.path == str(b)


# --------------- Summary edge cases ------------------------------


def test_summarise_directory_handles_missing_path(tmp_path: Path):
    """Asking for a summary of a non-existent path returns the empty
    record with a note rather than raising."""
    out = bd._summarise_directory(tmp_path / "nope")
    assert out["manifest_entries"] == 0
    assert out["size_bytes_total"] == 0
    assert any("does not exist" in n for n in out["notes"])


def test_summarise_counts_manifest_entries_and_sizes(tmp_path: Path):
    folder = _make_baseline(
        tmp_path,
        manifest_entries=12,
        impostor_registers=["academic_philosophy", "literary_horror"],
        impostor_personas_per_register=2,
        extra_file_sizes=[1024, 2048],
    )
    out = bd._summarise_directory(folder)
    assert out["manifest_entries"] == 12
    assert out["impostor_personas"] == 4
    assert set(out["impostor_registers"]) == {
        "academic_philosophy", "literary_horror",
    }
    # The blob files plus the manifest contribute to size.
    assert out["size_bytes_total"] >= 1024 + 2048


def test_summarise_recognises_corpus_manifest_filename(tmp_path: Path):
    """Acquisition writes ``corpus_manifest.jsonl``; that filename
    should be picked up just like the calibration ``manifest.jsonl``."""
    folder = tmp_path / bd.PRIVATE_DIR_NAME
    folder.mkdir()
    (folder / "corpus_manifest.jsonl").write_text(
        '{"text_id":"a"}\n{"text_id":"b"}\n', encoding="utf-8",
    )
    out = bd._summarise_directory(folder)
    assert out["manifest_entries"] == 2
    assert out["manifest_path"] is not None
    assert out["manifest_path"].endswith("corpus_manifest.jsonl")


# --------------- Validate subcommand -----------------------------


def test_validate_accepts_correctly_named_directory(tmp_path: Path):
    folder = _make_baseline(tmp_path)
    ok, issues = bd.validate_path(folder)
    assert ok is True
    assert issues == []


def test_validate_rejects_missing_path(tmp_path: Path):
    ok, issues = bd.validate_path(tmp_path / "nope")
    assert ok is False
    assert any("does not exist" in i for i in issues)


def test_validate_rejects_wrong_directory_name(tmp_path: Path):
    """The privacy guard requires the literal marker name; a folder
    called e.g. ``my-baselines`` would silently fail downstream."""
    wrong = tmp_path / "wrong-name"
    wrong.mkdir()
    ok, issues = bd.validate_path(wrong)
    assert ok is False
    assert any("not 'ai-prose-baselines-private'" in i or
               "ai-prose-baselines-private" in i for i in issues)


# --------------- Rendering ---------------------------------------


def test_render_text_marks_recommended_and_shows_export_line(tmp_path: Path):
    base = _make_baseline(tmp_path / "vault", manifest_entries=3)
    candidates = bd.discover(
        script_path=tmp_path / "no-such" / "script.py",
        max_depth=0,
        env_value=str(base),
    )
    out = bd.render_text(candidates, env_value=str(base))
    assert "RECOMMENDED" in out
    assert f"export {bd.ENV_VAR}=" in out
    assert str(base) in out


def test_render_text_when_nothing_found(tmp_path: Path):
    """No env var, no filesystem hits — the report should still tell
    the user what to do (set the env var, or accept the default
    creation path)."""
    candidates: list[bd.Candidate] = []
    out = bd.render_text(candidates, env_value=None)
    assert "No baseline folder found" in out
    assert f"export {bd.ENV_VAR}=" in out


def test_render_json_payload_shape(tmp_path: Path):
    base = _make_baseline(tmp_path / "vault", manifest_entries=1)
    candidates = bd.discover(
        script_path=tmp_path / "no-such" / "script.py",
        max_depth=0,
        env_value=str(base),
    )
    raw = bd.render_json(candidates, env_value=str(base))
    payload = json.loads(raw)
    assert payload["task_surface"] == "setup"
    assert payload["tool"] == "baseline_discovery"
    assert payload["env_var_set"] is True
    assert payload["env_var_value"] == str(base)
    assert payload["recommended_path"] == str(base)
    assert payload["export_line"] is not None
    assert payload["export_line"].startswith(f"export {bd.ENV_VAR}=")


def test_render_text_lists_duplicate_existing_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """When the user has a recommended folder AND a stale duplicate,
    the report should call out the duplicate so they can clean up."""
    big = _make_baseline(
        tmp_path / "Obsidian" / "vault", manifest_entries=20,
    )
    stale = _make_baseline(tmp_path / "Documents" / "old", manifest_entries=0)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    candidates = bd.discover(
        script_path=tmp_path / "no-such" / "script.py",
        max_depth=4,
        env_value=None,
    )
    out = bd.render_text(candidates, env_value=None)
    assert "Other existing folders were found" in out
    assert str(stale) in out
    assert str(big) in out


# --------------- CLI integration --------------------------------


def test_main_exits_zero_when_env_var_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
):
    """When the env var points to a real folder, ``main()`` should
    exit 0 AND recommend that folder — even if a different folder
    elsewhere on disk has more content. Setting the env var is an
    explicit user choice and the script must not override it."""
    base = _make_baseline(tmp_path / "vault", manifest_entries=1)
    monkeypatch.setenv(bd.ENV_VAR, str(base))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "empty"))
    # Isolate from the real repo sibling so the test stays
    # deterministic on a developer machine that has its own baseline.
    monkeypatch.setattr(bd, "_repo_sibling", lambda script_path: None)
    monkeypatch.setattr(bd, "_candidate_dirs", lambda: [])
    rc = bd.main(["--json", "--max-depth", "0"])
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["recommended_path"] == str(base)


def test_main_exits_one_when_nothing_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
):
    """No env var and no folders on disk anywhere we look — the
    script should exit 1 to tell the setup skill the user must
    create or configure one."""
    monkeypatch.delenv(bd.ENV_VAR, raising=False)
    empty_home = tmp_path / "empty"
    empty_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: empty_home))
    monkeypatch.setattr(bd, "_repo_sibling", lambda script_path: None)
    monkeypatch.setattr(bd, "_candidate_dirs", lambda: [])
    rc = bd.main(["--json", "--max-depth", "1"])
    assert rc == 1


def test_main_validate_path_returns_two_on_bad_path(
    tmp_path: Path, capsys: pytest.CaptureFixture,
):
    wrong = tmp_path / "wrong-name"
    wrong.mkdir()
    rc = bd.main(["--validate", str(wrong)])
    assert rc == 2


def test_main_validate_path_returns_zero_on_good_path(
    tmp_path: Path, capsys: pytest.CaptureFixture,
):
    folder = _make_baseline(tmp_path)
    rc = bd.main(["--validate", str(folder)])
    assert rc == 0
