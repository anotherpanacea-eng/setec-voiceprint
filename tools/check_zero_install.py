#!/usr/bin/env python3
"""check_zero_install.py — the zero-install BARE-copy gate.

Per `specs/svp-packaging-conversion.md`'s outcome statement: "A copied
`plugins/setec-voiceprint/` subtree ... must work without `pip
install`, `PYTHONPATH`, or repository-relative current working
directory." The spec's invariant is a BARE copied `setec-voiceprint/`
subtree — i.e. `shutil.copytree(".../plugins/setec-voiceprint",
"<somewhere>/setec-voiceprint")` — not a copy that reconstructs the
real repo's `plugins/<name>/` nesting.

This gate makes exactly that bare copy (no synthetic `plugins/`
parent) and checks:

  1. **Structural reachability.** Every `capabilities.d` script_path
     resolves inside the bare copy (repo-relative prefix
     `plugins/setec-voiceprint/` stripped, joined onto the bare root).
  2. **Direct launcher execution — all 4 classes.** A representative
     top-level launcher, plus one from each nested capability-path
     class (`scripts/calibration/`, `scripts/external_mirror/`,
     `scripts/replication/`), run by direct file path with an empty
     `PYTHONPATH` and an outside-repo cwd. This is the part of the
     zero-install claim that IS true today and stays covered — every
     launcher's own "add scripts/ to sys.path" bootstrap only needs
     its OWN directory, never the two-level `plugins/<name>/` nesting.
  3. **`setec_run.py` dispatch.** Run a real normalized surface through
     the dispatcher from the bare copy. The manifest keeps repository-
     relative paths, so the dispatcher must strip the stable plugin prefix
     and resolve from its actual plugin root; reconstructing a synthetic
     `plugins/` parent is not allowed.

Exit codes:

    0 — all checks pass
    1 — any check fails
    2 — internal error (scratch-copy setup failure)

Usage:

    python3 tools/check_zero_install.py
    python3 tools/check_zero_install.py --json
    python3 tools/check_zero_install.py --keep-scratch
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "setec-voiceprint"

sys.path.insert(0, str(REPO_ROOT / "tools"))
from _console import enable_utf8_stdio  # noqa: E402


class GateError(RuntimeError):
    """Internal / setup failure (exit code 2)."""


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Report:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.results.append(CheckResult(name, passed, detail))


# ---------- bare copy ---------------------------------------------------


def make_bare_copy(tmp_root: Path) -> Path:
    """A BARE copy: `tmp_root/setec-voiceprint`, no `plugins/` parent
    reconstructed. This is the actual shape the spec's "a copied
    plugins/setec-voiceprint/ subtree ... must work" line asserts —
    the wrapper directory is not part of what gets copied, so it's not
    part of what this gate reproduces either.

    `shutil.copytree` with `symlinks=False` (the default) dereferences
    any symlink it meets rather than reproducing it, so the bare copy
    can never carry a live link back into the original checkout."""
    dest = tmp_root / "setec-voiceprint"
    shutil.copytree(PLUGIN_ROOT, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return env


# ---------- check 1: structural reachability -----------------------------


def _load_yaml():
    try:
        import yaml  # type: ignore
        return yaml
    except ImportError as exc:
        raise GateError("PyYAML is required (`pip install pyyaml`)") from exc


def check_structural_reachability(bare_root: Path, report: Report) -> None:
    yaml = _load_yaml()
    manifest_dir = bare_root / "capabilities.d"
    problems = []
    checked = 0
    prefix = "plugins/setec-voiceprint/"
    for frag in sorted(manifest_dir.glob("*.yaml")):
        if frag.name == "_meta.yaml":
            continue
        doc = yaml.safe_load(frag.read_text(encoding="utf-8"))
        entry = (doc or {}).get("entries", [{}])[0]
        script_path = entry.get("script_path")
        if not script_path:
            continue
        checked += 1
        if not script_path.startswith(prefix):
            problems.append(
                f"{entry.get('id')}: script_path {script_path!r} doesn't "
                f"start with {prefix!r}"
            )
            continue
        rel = script_path[len(prefix):]
        rel_path = Path(rel)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            problems.append(
                f"{entry.get('id')}: script_path {script_path!r} is not a "
                "closed plugin-relative path"
            )
            continue
        target = (bare_root / rel_path).resolve()
        try:
            target.relative_to(bare_root.resolve())
        except ValueError:
            problems.append(
                f"{entry.get('id')}: script_path {script_path!r} escapes the bare copy"
            )
            continue
        if not target.is_file():
            problems.append(f"{entry.get('id')}: {target} does not exist in the bare copy")
        elif target.is_symlink():
            problems.append(f"{entry.get('id')}: {target} is a symlink in the bare copy (unexpected)")
    report.add(
        "structural_reachability",
        not problems,
        f"{checked} script_path(s) checked" if not problems else "; ".join(problems),
    )


# ---------- check 2: direct launcher execution, all 4 classes -----------

LAUNCHER_CLASSES: list[tuple[str, str, list[str]]] = [
    ("top_level", "scripts/dependency_check.py", ["--help"]),
    ("calibration", "scripts/calibration/paraphrase_ladder.py", ["--help"]),
    ("external_mirror", "scripts/external_mirror/compose_evidence_pack.py", ["--help"]),
    ("replication", "scripts/replication/train_xgboost.py", ["--help"]),
]


def check_launcher_classes(bare_root: Path, outside_cwd: Path, report: Report) -> None:
    for label, relpath, argv in LAUNCHER_CLASSES:
        script = bare_root / relpath
        name = f"launcher_class:{label}"
        if not script.is_file():
            report.add(name, False, f"missing: {script}")
            continue
        try:
            proc = subprocess.run(
                [sys.executable, str(script), *argv],
                cwd=outside_cwd, env=_clean_env(),
                capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            report.add(name, False, f"timed out: {script}")
            continue
        ok = proc.returncode == 0 and "Traceback" not in proc.stderr
        report.add(
            name, ok,
            "" if ok else f"exit={proc.returncode} stderr_tail={proc.stderr[-300:]!r}",
        )


# ---------- check 3: setec_run.py dispatch -------------------------------


def check_setec_run_bare_dispatch(bare_root: Path, outside_cwd: Path, report: Report) -> None:
    """Run the real command from the real bare copy. This deliberately does
    not reconstruct a ``plugins/`` wrapper around the copied subtree."""
    script = bare_root / "scripts" / "setec_run.py"
    target = bare_root / "scripts" / "test_data" / "human_sample.txt"
    name = "setec_run_bare_dispatch"
    if not target.is_file():
        report.add(name, False, f"fixture missing: {target}")
        return
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "variance_audit", str(target), "--json"],
            cwd=outside_cwd, env=_clean_env(),
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        report.add(name, False, "timed out (expected either a clean success or a clean documented-gap failure — a hang is neither)")
        return

    try:
        envelope = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        envelope = None

    expected = {
        "schema_version": "1.0",
        "task_surface": "smoothing_diagnosis",
        "tool": "variance_audit",
        "available": True,
    }
    if (
        proc.returncode == 0
        and isinstance(envelope, dict)
        and all(envelope.get(key) == value for key, value in expected.items())
    ):
        report.add(
            name, True,
            "setec_run.py dispatched a normalized surface successfully from "
            "the bare plugin copy",
        )
        return

    report.add(
        name, False,
        f"bare-copy dispatch failed exact envelope check — exit={proc.returncode} "
        f"stdout={proc.stdout[-300:]!r} stderr_tail={proc.stderr[-300:]!r}",
    )


# ---------- CLI ----------------------------------------------------


def run(keep_scratch: bool = False) -> tuple[bool, Report]:
    report = Report()
    tmp_root = Path(tempfile.mkdtemp(prefix="setec_zero_install_"))
    try:
        bare_root = make_bare_copy(tmp_root)
        outside_cwd = tmp_root
        check_structural_reachability(bare_root, report)
        check_launcher_classes(bare_root, outside_cwd, report)
        check_setec_run_bare_dispatch(bare_root, outside_cwd, report)
    finally:
        if not keep_scratch:
            shutil.rmtree(tmp_root, ignore_errors=True)
        else:
            print(f"bare copy retained at {tmp_root}", file=sys.stderr)
    return report.passed, report


def main(argv: list[str] | None = None) -> int:
    enable_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Zero-install bare-copy gate (structural reachability, "
                     "direct launcher execution, setec_run.py dispatch).",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--keep-scratch", action="store_true")
    args = parser.parse_args(argv)

    try:
        passed, report = run(keep_scratch=args.keep_scratch)
    except GateError as exc:
        if args.json:
            print(json.dumps({"passed": False, "error": str(exc)}, indent=2))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({
            "passed": passed,
            "results": [
                {"name": r.name, "passed": r.passed, "detail": r.detail}
                for r in report.results
            ],
        }, indent=2))
        return 0 if passed else 1

    for r in report.results:
        mark = "OK  " if r.passed else "FAIL"
        print(f"[{mark}] {r.name}" + (f" — {r.detail}" if r.detail else ""))
    n = len(report.results)
    n_fail = sum(1 for r in report.results if not r.passed)
    if passed:
        print(f"\n{n} check(s) passed. ✔")
        return 0
    print(f"\n{n_fail}/{n} check(s) failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
