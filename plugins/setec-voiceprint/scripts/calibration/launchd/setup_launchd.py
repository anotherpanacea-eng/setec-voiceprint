#!/usr/bin/env python3
"""setup_launchd.py — render + install the macOS launchd nightly agent.

Operator-facing helper for v1.44.1.C of the sharded-calibration
toolchain. Takes runtime parameters (paths, run_id, time window,
worker count) and:

  1. Builds a launchd plist dict via ``_build_plist_dict`` and
     serializes it with ``plistlib.dumps`` (which auto-escapes
     XML special characters in operator-supplied paths and ids).
     The plist's spec §2.8 contract — KeepAlive.Crashed=True,
     SuccessfulExit=False, RunAtLoad=False, ProcessType=Background,
     ThrottleInterval=60 — lives in ``_build_plist_dict``.
  2. Renders ``run_shard_worker.sh.template`` to a per-host wrapper
     script with executable permissions. Operator-supplied values
     go through ``shlex.quote`` so shell metacharacters
     (``$()``, embedded quotes) can't break or execute the
     wrapper.
  3. Optionally installs the plist into ``~/Library/LaunchAgents/``
     and loads it via ``launchctl``.

By default the helper runs in dry-run mode: it writes the rendered
files to a staging directory under ``~/.setec-voiceprint/launchd/``
and prints the ``launchctl`` commands the operator would run. This
keeps the path safe-by-default — you have to pass ``--install``
explicitly to actually mutate ``~/Library/LaunchAgents/``.

The renderer is also independently usable for testing: every
template variable maps to a ``RenderConfig`` field, and
``render_plist`` / ``render_wrapper`` are pure functions over the
config. The tests in ``test_setup_launchd.py`` exercise these
without ever touching the user's launchd state.

Reference: SPEC_sharded_calibration.md §2.8, §7.2 (v1.43.1 phase).
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = SCRIPT_DIR  # plist and wrapper templates live here

DEFAULT_LABEL = "com.anotherpanacea.setec-voiceprint.shard-worker"
DEFAULT_LOG_DIR = "~/Library/Logs/setec-voiceprint"
DEFAULT_STAGING_DIR = "~/.setec-voiceprint/launchd"
LAUNCH_AGENTS_DIR = "~/Library/LaunchAgents"


# --------------- Config dataclass --------------------------------


@dataclass(frozen=True)
class RenderConfig:
    """Inputs the renderer needs.

    Every field is validated by ``validate_render_config`` before
    rendering; the renderer itself does no further checking. This
    keeps the template-substitution layer dumb and the policy layer
    explicit.
    """

    label: str
    python_bin: Path
    shard_runner: Path
    base_dir: Path
    run_id: str
    time_window: str
    workers: int
    use: str
    log_dir: Path
    launchd_log_path: Path
    wrapper_path: Path
    start_hour: int
    start_minute: int


def validate_render_config(cfg: RenderConfig) -> None:
    """Run policy checks against the config. Raises ``ValueError``
    on any failure so the CLI fails fast before writing files."""
    if not cfg.label or " " in cfg.label:
        raise ValueError(
            f"Launchd label must be non-empty and contain no spaces: "
            f"{cfg.label!r}"
        )
    for name, path in (
        ("python_bin", cfg.python_bin),
        ("shard_runner", cfg.shard_runner),
        ("base_dir", cfg.base_dir),
        ("log_dir", cfg.log_dir),
        ("launchd_log_path", cfg.launchd_log_path),
        ("wrapper_path", cfg.wrapper_path),
    ):
        if not Path(path).is_absolute():
            raise ValueError(
                f"{name} must be an absolute path: {path}"
            )
    if not cfg.run_id:
        raise ValueError("run_id must be non-empty")
    if not cfg.time_window:
        raise ValueError("time_window must be non-empty (HH:MM-HH:MM)")
    if cfg.workers < 1:
        raise ValueError(f"workers must be >= 1: {cfg.workers}")
    if not (0 <= cfg.start_hour <= 23):
        raise ValueError(
            f"start_hour must be 0..23: {cfg.start_hour}"
        )
    if not (0 <= cfg.start_minute <= 59):
        raise ValueError(
            f"start_minute must be 0..59: {cfg.start_minute}"
        )


# --------------- Pure renderers ----------------------------------


def _load_template(name: str) -> str:
    """Read a template file from the package directory."""
    path = TEMPLATE_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"Launchd template not found: {path}. The package is "
            f"likely missing files; reinstall or check the repo."
        )
    return path.read_text(encoding="utf-8")


def _build_plist_dict(cfg: RenderConfig) -> dict:
    """Build the plist dictionary that ``plistlib.dumps`` serializes.

    Encodes the spec §2.8 contract directly: ``KeepAlive.Crashed=
    True``, ``KeepAlive.SuccessfulExit=False`` (load-bearing for
    the ``--time-window`` clean-exit semantics), ``RunAtLoad=False``,
    ``ProcessType=Background``, ``ThrottleInterval=60``.

    The ``StandardOutPath`` and ``StandardErrorPath`` both point at
    the same ``launchd_log_path`` because launchd-level log lines
    are sparse and don't need separate streams; the wrapper handles
    per-day shard-worker logging itself.
    """
    return {
        "Label": cfg.label,
        "ProgramArguments": [str(cfg.wrapper_path)],
        "StartCalendarInterval": {
            "Hour": int(cfg.start_hour),
            "Minute": int(cfg.start_minute),
        },
        "KeepAlive": {
            # Respawn on unexpected death (segfault, OOM kill).
            "Crashed": True,
            # DO NOT respawn on clean exit — that's how the
            # --time-window gate signals "stop until next scheduled
            # fire."
            "SuccessfulExit": False,
        },
        # macOS scheduler hint: gentle CPU/IO priority for a
        # long-running overnight job.
        "ProcessType": "Background",
        # Guard against tight respawn loops on immediate-crash.
        "ThrottleInterval": 60,
        # launchd-level lifecycle logs (process started / exited).
        # Per-day shard-worker logs are produced by the wrapper.
        "StandardOutPath": str(cfg.launchd_log_path),
        "StandardErrorPath": str(cfg.launchd_log_path),
        # Don't fire on `launchctl load` — only at the scheduled
        # hour. Operators who need an out-of-band run use
        # `launchctl start <label>` or invoke the wrapper directly.
        "RunAtLoad": False,
    }


def render_plist(cfg: RenderConfig) -> str:
    """Render the launchd plist with the supplied config.

    Reviewer P2 (2026-05-14): previously this used string
    substitution on a template, which produced invalid XML when
    operator-supplied paths contained ``&``, ``<``, ``>``, or
    bare-quote characters. The fix builds the plist dictionary
    programmatically with ``plistlib.dumps``, which handles XML
    escaping for every value automatically (encoding ``&`` as
    ``&amp;`` etc. before emission).

    Returns the rendered XML as a UTF-8 string. The output is
    valid Apple plist XML by construction; ``parse_plist`` exists
    only as a defense-in-depth round-trip check at test time.
    """
    plist_dict = _build_plist_dict(cfg)
    # plistlib uses FMT_XML by default. The output starts with
    # the XML 1.0 + PLIST DTD declarations, includes the
    # <plist version="1.0"> wrapper, and properly escapes every
    # value. plistlib.dumps returns bytes; decode to str so the
    # caller's `write_text(...)` path stays unchanged.
    return plistlib.dumps(plist_dict, fmt=plistlib.FMT_XML).decode("utf-8")


def render_wrapper(cfg: RenderConfig) -> str:
    """Render the wrapper shell-script template with the supplied
    config. Output is a bash script (the caller is responsible for
    writing it to disk and chmod +x; ``write_files`` does both).

    Reviewer P2 (2026-05-14): previously this used string
    substitution on the bash template, which broke (or worse,
    executed) when operator-supplied values contained shell
    metacharacters — paths with embedded quotes, run-ids with
    ``$()`` command substitution, etc. The fix runs every
    substituted value through ``shlex.quote()`` so the rendered
    script treats them as opaque literals regardless of what
    metacharacters they contain. ``bash -n`` validates the output
    syntactically; tests pass operator-controlled values through
    a deliberately-hostile fixture to confirm no command
    injection.
    """
    import shlex
    template = _load_template("run_shard_worker.sh.template")
    substitutions = {
        "{{LOG_DIR}}": shlex.quote(str(cfg.log_dir)),
        "{{PYTHON_BIN}}": shlex.quote(str(cfg.python_bin)),
        "{{SHARD_RUNNER}}": shlex.quote(str(cfg.shard_runner)),
        "{{BASE_DIR}}": shlex.quote(str(cfg.base_dir)),
        "{{RUN_ID}}": shlex.quote(cfg.run_id),
        "{{TIME_WINDOW}}": shlex.quote(cfg.time_window),
        "{{WORKERS}}": shlex.quote(str(int(cfg.workers))),
        "{{USE}}": shlex.quote(cfg.use),
    }
    rendered = template
    for key, value in substitutions.items():
        rendered = rendered.replace(key, value)
    if "{{" in rendered or "}}" in rendered:
        raise ValueError(
            "Wrapper template still contains unsubstituted placeholders "
            "after render. This is a bug in setup_launchd.py."
        )
    return rendered


def parse_plist(text: str) -> dict:
    """Round-trip the rendered plist through plistlib so the caller
    can assert on the parsed structure. ``plistlib.loads`` raises
    ``plistlib.InvalidFileException`` on malformed XML; we let that
    propagate as an early failure signal."""
    return plistlib.loads(text.encode("utf-8"))


# --------------- File I/O ----------------------------------------


def write_files(
    cfg: RenderConfig,
    staging_dir: Path,
    *,
    plist_text: str | None = None,
    wrapper_text: str | None = None,
) -> tuple[Path, Path]:
    """Write the rendered plist + wrapper to ``staging_dir``. Returns
    ``(plist_path, wrapper_path)``. ``wrapper_path`` is chmod 0o755.

    Skips re-rendering when ``plist_text`` / ``wrapper_text`` are
    pre-rendered (useful for tests that want to inspect the rendered
    output without going through I/O).
    """
    staging_dir = Path(staging_dir).expanduser()
    staging_dir.mkdir(parents=True, exist_ok=True)
    if plist_text is None:
        plist_text = render_plist(cfg)
    if wrapper_text is None:
        wrapper_text = render_wrapper(cfg)
    plist_path = staging_dir / f"{cfg.label}.plist"
    wrapper_path = Path(cfg.wrapper_path)
    wrapper_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_text, encoding="utf-8")
    wrapper_path.write_text(wrapper_text, encoding="utf-8")
    # chmod +x on the wrapper so launchd can exec it. We use the
    # stat module rather than 0o755 magic so the intent (owner +
    # group + other read+execute, owner write) is explicit in the
    # source.
    wrapper_path.chmod(
        stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR  # rwx for owner
        | stat.S_IRGRP | stat.S_IXGRP                # rx for group
        | stat.S_IROTH | stat.S_IXOTH                # rx for other
    )
    return plist_path, wrapper_path


# --------------- launchctl helpers (with --dry-run) --------------


def install_plist(
    plist_path: Path,
    *,
    launch_agents_dir: Path | None = None,
    dry_run: bool = True,
) -> Path:
    """Copy the rendered plist into ``~/Library/LaunchAgents/`` and
    return the installed path.

    With ``dry_run=True`` (the default), this only computes the
    target path and returns it without copying. The CLI prints the
    command an operator would run themselves.
    """
    launch_agents_dir = (
        Path(launch_agents_dir).expanduser()
        if launch_agents_dir is not None
        else Path(LAUNCH_AGENTS_DIR).expanduser()
    )
    target = launch_agents_dir / plist_path.name
    if dry_run:
        return target
    launch_agents_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(plist_path, target)
    return target


def launchctl_load(
    plist_path: Path,
    *,
    dry_run: bool = True,
    reload_before_bootstrap: bool = False,
) -> list[str]:
    """Build the ``launchctl load`` command for an installed plist.
    Returns the argv list of the bootstrap call. With
    ``dry_run=False`` actually runs it via subprocess.

    We use ``launchctl bootstrap gui/<uid>`` syntax (modern macOS,
    Catalina+) so the agent loads into the current GUI session.
    Older ``launchctl load`` would also work but is deprecated.

    When ``reload_before_bootstrap=True`` we first run
    ``launchctl bootout`` (best-effort, errors tolerated) on the same
    plist path. This makes the install path idempotent: re-running
    setup after config changes succeeds even when a previous agent
    is already loaded under the same label. The bootout command is
    NOT returned (the return value remains the bootstrap argv for
    backwards compatibility with callers that introspect it); use
    :func:`launchctl_unload` directly when you need the bootout argv.
    """
    uid = os.getuid()
    cmd = [
        "launchctl", "bootstrap", f"gui/{uid}", str(plist_path),
    ]
    if not dry_run:
        if reload_before_bootstrap:
            # Best-effort: clear any existing agent with the same
            # plist path before bootstrapping the new one. We
            # tolerate "not loaded" (rc != 0) since the common case
            # is no prior agent.
            bootout_cmd = [
                "launchctl",
                "bootout",
                f"gui/{uid}",
                str(plist_path),
            ]
            subprocess.run(bootout_cmd, check=False)
        subprocess.run(cmd, check=True)
    return cmd


def launchctl_unload(
    plist_path: Path, *, dry_run: bool = True,
) -> list[str]:
    """Build the ``launchctl bootout`` command. The inverse of
    ``launchctl_load``. Useful for uninstalls and reinstalls."""
    uid = os.getuid()
    cmd = [
        "launchctl", "bootout", f"gui/{uid}", str(plist_path),
    ]
    if not dry_run:
        subprocess.run(cmd, check=False)  # tolerate "not loaded"
    return cmd


# --------------- CLI ---------------------------------------------


def _parse_start_time(spec: str) -> tuple[int, int]:
    """Extract the first half of an HH:MM-HH:MM time window as the
    ``StartCalendarInterval``'s ``Hour`` / ``Minute`` pair."""
    cleaned = spec.strip()
    if "-" not in cleaned:
        raise ValueError(
            f"--time-window must be HH:MM-HH:MM; got {spec!r}"
        )
    start = cleaned.split("-", 1)[0].strip()
    if ":" not in start:
        raise ValueError(f"--time-window start must be HH:MM; got {start!r}")
    hh_s, mm_s = start.split(":", 1)
    return int(hh_s), int(mm_s)


def _default_python() -> str:
    """Default python interpreter: ``sys.executable`` so a
    Homebrew-installed `python3` is preferred over the system one,
    and launchd executes the same interpreter we're running under."""
    return sys.executable


def _default_shard_runner() -> str:
    """Default shard_runner.py: the one next to this script."""
    return str((SCRIPT_DIR.parent / "shard_runner.py").resolve())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="setup_launchd",
        description=(
            "Render and (optionally) install the macOS launchd "
            "agent for nightly sharded-calibration runs. See "
            "RUNBOOK_macos_nightly.md for a walkthrough."
        ),
    )
    p.add_argument(
        "--run-id", required=True, type=str,
        help="sharded-run identifier (matches shard_runner --run-id)",
    )
    p.add_argument(
        "--base-dir", required=True, type=str,
        help="baselines directory (matches shard_runner --base-dir)",
    )
    p.add_argument(
        "--time-window", required=True, type=str,
        metavar="HH:MM-HH:MM",
        help=(
            "schedule window for the worker; the launchd agent "
            "fires at the START of this window"
        ),
    )
    p.add_argument(
        "--workers", type=int, default=1,
        help="--workers value passed to shard_runner work (default 1)",
    )
    p.add_argument(
        "--use", type=str, default="validation",
        help="--use value passed to shard_runner work (default validation)",
    )
    p.add_argument(
        "--label", type=str, default=DEFAULT_LABEL,
        help=(
            f"launchd label (default {DEFAULT_LABEL}). The plist "
            f"filename will be <label>.plist."
        ),
    )
    p.add_argument(
        "--python", type=str, default=_default_python(),
        help=(
            "absolute path to python3 (default: the interpreter "
            "running this helper)"
        ),
    )
    p.add_argument(
        "--shard-runner", type=str, default=_default_shard_runner(),
        help="absolute path to shard_runner.py (default: sibling file)",
    )
    p.add_argument(
        "--log-dir", type=str, default=DEFAULT_LOG_DIR,
        help=f"log directory (default {DEFAULT_LOG_DIR})",
    )
    p.add_argument(
        "--staging-dir", type=str, default=DEFAULT_STAGING_DIR,
        help=(
            f"directory for rendered files before install "
            f"(default {DEFAULT_STAGING_DIR})"
        ),
    )
    p.add_argument(
        "--install", action="store_true", default=False,
        help=(
            "copy the rendered plist into ~/Library/LaunchAgents/ "
            "and bootstrap it via launchctl. Default is dry-run."
        ),
    )
    p.add_argument(
        "--uninstall", action="store_true", default=False,
        help=(
            "remove the installed plist via launchctl bootout. "
            "Mutually exclusive with --install."
        ),
    )
    args = p.parse_args(argv)
    if args.install and args.uninstall:
        sys.stderr.write(
            "--install and --uninstall are mutually exclusive.\n"
        )
        return 2

    try:
        start_hour, start_minute = _parse_start_time(args.time_window)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    log_dir = Path(args.log_dir).expanduser()
    staging_dir = Path(args.staging_dir).expanduser()
    cfg = RenderConfig(
        label=args.label,
        python_bin=Path(args.python).expanduser(),
        shard_runner=Path(args.shard_runner).expanduser(),
        base_dir=Path(args.base_dir).expanduser(),
        run_id=args.run_id,
        time_window=args.time_window,
        workers=int(args.workers),
        use=args.use,
        log_dir=log_dir,
        launchd_log_path=log_dir / "launchd.log",
        wrapper_path=staging_dir / "run_shard_worker.sh",
        start_hour=start_hour,
        start_minute=start_minute,
    )
    try:
        validate_render_config(cfg)
    except ValueError as exc:
        sys.stderr.write(f"Invalid configuration: {exc}\n")
        return 2

    plist_path, wrapper_path = write_files(cfg, staging_dir)
    sys.stderr.write(
        f"Rendered launchd plist:    {plist_path}\n"
        f"Rendered wrapper script:   {wrapper_path}\n"
    )

    if args.uninstall:
        target = (
            Path(LAUNCH_AGENTS_DIR).expanduser() / plist_path.name
        )
        cmd = launchctl_unload(target, dry_run=False)
        sys.stderr.write(f"  Ran: {' '.join(cmd)}\n")
        try:
            target.unlink()
            sys.stderr.write(f"  Removed: {target}\n")
        except FileNotFoundError:
            sys.stderr.write(f"  (no installed plist at {target}; ok)\n")
        return 0

    if args.install:
        installed = install_plist(plist_path, dry_run=False)
        sys.stderr.write(f"Installed plist: {installed}\n")
        # Idempotent install: best-effort bootout first so re-running
        # setup after a config change succeeds even when a previous
        # agent is already loaded under the same label.
        sys.stderr.write(
            "  Running best-effort bootout (errors ignored if no "
            "prior agent)...\n"
        )
        cmd = launchctl_load(
            installed, dry_run=False, reload_before_bootstrap=True,
        )
        sys.stderr.write(f"  Ran: {' '.join(cmd)}\n")
        sys.stderr.write(
            "\nAgent loaded. Check status with:\n"
            f"  launchctl print gui/$(id -u)/{cfg.label}\n"
            "Logs:\n"
            f"  tail -F {cfg.log_dir}/shard-worker-$(date +%Y-%m-%d).log\n"
        )
        return 0

    # Dry-run path: print what an operator would run.
    installed = install_plist(plist_path, dry_run=True)
    load_cmd = launchctl_load(installed, dry_run=True)
    unload_cmd = launchctl_unload(installed, dry_run=True)
    sys.stderr.write(
        "\nDry-run complete. To install, either re-run with "
        "--install, or run these commands manually:\n\n"
        f"  cp {plist_path} {installed}\n"
        f"  {' '.join(unload_cmd)}  # best-effort, ignore errors if no prior agent\n"
        f"  {' '.join(load_cmd)}\n\n"
        "To uninstall later, run this helper with --uninstall.\n"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
