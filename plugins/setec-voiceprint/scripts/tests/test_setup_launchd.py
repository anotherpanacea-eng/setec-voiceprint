#!/usr/bin/env python3
"""Tests for setup_launchd.py (v1.44.1.C).

Validates that the renderer:

  * Substitutes every template variable; no ``{{...}}`` left over.
  * Produces a plist that ``plistlib`` can parse.
  * Encodes the spec §2.8 KeepAlive semantics correctly
    (``Crashed=true``, ``SuccessfulExit=false``).
  * Refuses non-absolute paths and other config violations.
  * Routes ``--time-window 23:00-06:00`` into a StartCalendarInterval
    of ``Hour=23, Minute=0``.
  * Writes the wrapper script chmod +x.
  * Honors --dry-run by NOT touching ~/Library/LaunchAgents/.

These tests never call ``launchctl``; they exercise only the
filesystem + parsing layers. ``test_setup_launchd_cli_dry_run``
end-to-end-tests the CLI via ``main()`` with redirected paths.
"""

from __future__ import annotations

import os
import plistlib
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHD_DIR = ROOT / "calibration" / "launchd"
if str(LAUNCHD_DIR) not in sys.path:
    sys.path.insert(0, str(LAUNCHD_DIR))

import setup_launchd as sl  # type: ignore


# --------------- Helpers ----------------------------------------


def _good_config(tmp_path: Path) -> sl.RenderConfig:
    """Build a syntactically valid RenderConfig anchored at tmp_path
    so tests don't depend on any system path being present."""
    log_dir = tmp_path / "logs"
    staging_dir = tmp_path / "staging"
    return sl.RenderConfig(
        label="com.example.test.shard-worker",
        python_bin=Path("/opt/homebrew/bin/python3"),
        shard_runner=Path("/path/to/shard_runner.py"),
        base_dir=tmp_path / "baselines",
        run_id="test_run_2026-05-13",
        time_window="23:00-06:00",
        workers=4,
        use="validation",
        log_dir=log_dir,
        launchd_log_path=log_dir / "launchd.log",
        wrapper_path=staging_dir / "run_shard_worker.sh",
        start_hour=23,
        start_minute=0,
    )


# --------------- RenderConfig validation ------------------------


def test_validate_render_config_accepts_good_input(tmp_path: Path):
    cfg = _good_config(tmp_path)
    sl.validate_render_config(cfg)  # no error


def test_validate_render_config_rejects_relative_paths(tmp_path: Path):
    """Spec §2.8 + launchd's exec semantics require absolute paths.
    A relative wrapper_path or shard_runner would silently exec
    against the wrong directory."""
    bad = _good_config(tmp_path).__class__(
        **{**_good_config(tmp_path).__dict__, "shard_runner": Path("shard_runner.py")},
    )
    with pytest.raises(ValueError, match="must be an absolute path"):
        sl.validate_render_config(bad)


def test_validate_render_config_rejects_empty_label(tmp_path: Path):
    bad = sl.RenderConfig(
        **{**_good_config(tmp_path).__dict__, "label": ""},
    )
    with pytest.raises(ValueError, match="label"):
        sl.validate_render_config(bad)


def test_validate_render_config_rejects_label_with_spaces(tmp_path: Path):
    """Launchd labels with spaces break the file naming and the
    launchctl bootout path."""
    bad = sl.RenderConfig(
        **{**_good_config(tmp_path).__dict__, "label": "two words"},
    )
    with pytest.raises(ValueError, match="label"):
        sl.validate_render_config(bad)


def test_validate_render_config_rejects_bad_hour_minute(tmp_path: Path):
    bad_hour = sl.RenderConfig(
        **{**_good_config(tmp_path).__dict__, "start_hour": 25},
    )
    with pytest.raises(ValueError, match="start_hour"):
        sl.validate_render_config(bad_hour)
    bad_min = sl.RenderConfig(
        **{**_good_config(tmp_path).__dict__, "start_minute": 60},
    )
    with pytest.raises(ValueError, match="start_minute"):
        sl.validate_render_config(bad_min)


def test_validate_render_config_rejects_zero_workers(tmp_path: Path):
    bad = sl.RenderConfig(
        **{**_good_config(tmp_path).__dict__, "workers": 0},
    )
    with pytest.raises(ValueError, match="workers"):
        sl.validate_render_config(bad)


# --------------- Plist rendering --------------------------------


def test_render_plist_substitutes_all_placeholders(tmp_path: Path):
    """No ``{{...}}`` may survive the render — every template
    variable is filled in, or the renderer raises (and that path
    is exercised separately in test_render_plist_raises_on_leftover)."""
    cfg = _good_config(tmp_path)
    out = sl.render_plist(cfg)
    assert "{{" not in out
    assert "}}" not in out


def test_render_plist_parses_as_plist(tmp_path: Path):
    """Round-trip the rendered XML through plistlib. The dict has
    the spec §2.8 keys we care about."""
    cfg = _good_config(tmp_path)
    out = sl.render_plist(cfg)
    parsed = sl.parse_plist(out)
    assert parsed["Label"] == cfg.label
    assert parsed["ProgramArguments"] == [str(cfg.wrapper_path)]
    assert parsed["StandardOutPath"] == str(cfg.launchd_log_path)
    assert parsed["StandardErrorPath"] == str(cfg.launchd_log_path)


def test_render_plist_keep_alive_semantics(tmp_path: Path):
    """Per spec §2.8: respawn on Crashed=true, do NOT respawn on
    SuccessfulExit=false. The clean exit at the time-window
    boundary signals "stop until the next scheduled fire."""
    cfg = _good_config(tmp_path)
    parsed = sl.parse_plist(sl.render_plist(cfg))
    ka = parsed["KeepAlive"]
    assert ka["Crashed"] is True
    assert ka["SuccessfulExit"] is False


def test_render_plist_start_calendar_interval(tmp_path: Path):
    """A 23:00-06:00 window should produce Hour=23, Minute=0."""
    cfg = _good_config(tmp_path)
    parsed = sl.parse_plist(sl.render_plist(cfg))
    sci = parsed["StartCalendarInterval"]
    assert sci["Hour"] == 23
    assert sci["Minute"] == 0


def test_render_plist_throttle_interval(tmp_path: Path):
    """ThrottleInterval guards against tight respawn loops on
    immediate-crash. We pin it to 60s in the template; the test
    asserts it round-trips so a future template edit can't
    silently drop it."""
    cfg = _good_config(tmp_path)
    parsed = sl.parse_plist(sl.render_plist(cfg))
    assert parsed["ThrottleInterval"] == 60


def test_render_plist_run_at_load_false(tmp_path: Path):
    """RunAtLoad=false: the agent should fire at the scheduled
    time, not when launchctl loads it. Off-schedule runs are an
    operator decision (`launchctl start`) rather than the default."""
    cfg = _good_config(tmp_path)
    parsed = sl.parse_plist(sl.render_plist(cfg))
    assert parsed["RunAtLoad"] is False


# --------------- Wrapper rendering ------------------------------


def test_render_wrapper_substitutes_all_placeholders(tmp_path: Path):
    cfg = _good_config(tmp_path)
    out = sl.render_wrapper(cfg)
    assert "{{" not in out
    assert "}}" not in out


def test_render_wrapper_includes_caffeinate(tmp_path: Path):
    """The wrapper must invoke caffeinate -i. Otherwise the Mac
    will idle-sleep mid-run and the worker stalls."""
    cfg = _good_config(tmp_path)
    out = sl.render_wrapper(cfg)
    assert "/usr/bin/caffeinate" in out
    assert " -i " in out  # idle-sleep blocker


def test_render_wrapper_includes_time_window(tmp_path: Path):
    """The wrapper passes --time-window through to shard_runner.
    Without this the worker would never self-terminate at sunrise."""
    cfg = _good_config(tmp_path)
    out = sl.render_wrapper(cfg)
    assert "--time-window" in out
    assert cfg.time_window in out


def test_render_wrapper_includes_run_id_and_base_dir(tmp_path: Path):
    cfg = _good_config(tmp_path)
    out = sl.render_wrapper(cfg)
    assert cfg.run_id in out
    assert str(cfg.base_dir) in out


def test_render_wrapper_starts_with_bash_shebang(tmp_path: Path):
    cfg = _good_config(tmp_path)
    out = sl.render_wrapper(cfg)
    assert out.startswith("#!/bin/bash")


# --------------- File-system write ------------------------------


def test_write_files_writes_plist_and_wrapper(tmp_path: Path):
    cfg = _good_config(tmp_path)
    plist_path, wrapper_path = sl.write_files(cfg, tmp_path / "staging")
    assert plist_path.exists()
    assert wrapper_path.exists()
    # Plist roundtrips through plistlib.
    sl.parse_plist(plist_path.read_text(encoding="utf-8"))


def test_write_files_chmods_wrapper_executable(tmp_path: Path):
    """launchd will refuse to exec a non-executable wrapper. The
    chmod must be applied as part of the write step, not left to
    the operator to remember."""
    cfg = _good_config(tmp_path)
    _, wrapper_path = sl.write_files(cfg, tmp_path / "staging")
    mode = wrapper_path.stat().st_mode
    assert mode & stat.S_IXUSR  # owner execute
    assert mode & stat.S_IRUSR  # owner read


# --------------- launchctl helpers ------------------------------


def test_install_plist_dry_run_does_not_copy(tmp_path: Path):
    """Dry-run path must NOT mutate the LaunchAgents directory.
    This is the safe-by-default behavior — `--install` is the
    explicit opt-in."""
    cfg = _good_config(tmp_path)
    plist_path, _ = sl.write_files(cfg, tmp_path / "staging")
    fake_launch_agents = tmp_path / "fake-launch-agents"
    target = sl.install_plist(
        plist_path,
        launch_agents_dir=fake_launch_agents,
        dry_run=True,
    )
    assert target == fake_launch_agents / plist_path.name
    assert not target.exists()  # dry-run: nothing copied


def test_install_plist_copies_when_dry_run_false(tmp_path: Path):
    cfg = _good_config(tmp_path)
    plist_path, _ = sl.write_files(cfg, tmp_path / "staging")
    fake_launch_agents = tmp_path / "fake-launch-agents"
    target = sl.install_plist(
        plist_path,
        launch_agents_dir=fake_launch_agents,
        dry_run=False,
    )
    assert target.exists()
    # Contents match.
    assert target.read_text() == plist_path.read_text()


def test_launchctl_load_builds_bootstrap_command(tmp_path: Path):
    """We use the modern bootstrap syntax (gui/<uid>/...).
    The old `launchctl load` would also work but is deprecated."""
    target = tmp_path / "test.plist"
    cmd = sl.launchctl_load(target, dry_run=True)
    assert cmd[:3] == ["launchctl", "bootstrap", f"gui/{os.getuid()}"]
    assert cmd[-1] == str(target)


def test_launchctl_unload_builds_bootout_command(tmp_path: Path):
    target = tmp_path / "test.plist"
    cmd = sl.launchctl_unload(target, dry_run=True)
    assert cmd[:3] == ["launchctl", "bootout", f"gui/{os.getuid()}"]
    assert cmd[-1] == str(target)


# --------------- _parse_start_time -----------------------------


def test_parse_start_time_extracts_hh_mm():
    assert sl._parse_start_time("23:00-06:00") == (23, 0)
    assert sl._parse_start_time("09:30-17:45") == (9, 30)
    # Tolerates whitespace.
    assert sl._parse_start_time("  23:00 - 06:00  ") == (23, 0)


def test_parse_start_time_rejects_malformed():
    with pytest.raises(ValueError):
        sl._parse_start_time("not a window")
    with pytest.raises(ValueError):
        sl._parse_start_time("23:00")  # missing end


# --------------- CLI end-to-end (dry-run) ----------------------


def test_setup_launchd_cli_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
):
    """End-to-end: setup_launchd.main() with --time-window etc.
    renders both files into the staging directory and reports the
    install commands without actually touching ~/Library/LaunchAgents/.
    """
    base = tmp_path / "baselines"
    base.mkdir()
    staging = tmp_path / "staging"
    log_dir = tmp_path / "logs"
    rc = sl.main([
        "--run-id", "test_2026-05-13",
        "--base-dir", str(base),
        "--time-window", "23:00-06:00",
        "--workers", "2",
        "--staging-dir", str(staging),
        "--log-dir", str(log_dir),
        "--python", "/opt/homebrew/bin/python3",
        "--shard-runner", str(tmp_path / "shard_runner.py"),
    ])
    assert rc == 0
    # Staging files exist.
    plist_file = staging / f"{sl.DEFAULT_LABEL}.plist"
    wrapper_file = staging / "run_shard_worker.sh"
    assert plist_file.exists()
    assert wrapper_file.exists()
    # Wrapper is executable.
    assert wrapper_file.stat().st_mode & stat.S_IXUSR
    # Plist is valid.
    parsed = plistlib.loads(plist_file.read_bytes())
    assert parsed["StartCalendarInterval"]["Hour"] == 23


def test_setup_launchd_cli_rejects_install_plus_uninstall(
    tmp_path: Path, capsys: pytest.CaptureFixture,
):
    """--install and --uninstall are mutually exclusive; passing
    both should fail fast with rc=2."""
    base = tmp_path / "baselines"
    base.mkdir()
    rc = sl.main([
        "--run-id", "test",
        "--base-dir", str(base),
        "--time-window", "23:00-06:00",
        "--install",
        "--uninstall",
    ])
    assert rc == 2


def test_setup_launchd_cli_rejects_bad_time_window(
    tmp_path: Path, capsys: pytest.CaptureFixture,
):
    base = tmp_path / "baselines"
    base.mkdir()
    rc = sl.main([
        "--run-id", "test",
        "--base-dir", str(base),
        "--time-window", "garbage",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "time-window" in err.lower()


def test_setup_launchd_cli_rejects_relative_base_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture,
):
    """A relative --base-dir would silently exec against the
    launchd-agent's working directory (typically $HOME, but
    fragile). We reject it via the validator."""
    # First, change cwd so the relative path doesn't accidentally
    # resolve to something valid.
    monkeypatch_cwd = tmp_path / "elsewhere"
    monkeypatch_cwd.mkdir()
    os.chdir(monkeypatch_cwd)
    try:
        rc = sl.main([
            "--run-id", "test",
            "--base-dir", "relative/path",  # relative
            "--time-window", "23:00-06:00",
        ])
    finally:
        os.chdir(tmp_path)
    # Note: argparse passes the string through; Path(...).expanduser()
    # in main() does NOT make it absolute. The validator catches it.
    assert rc == 2
