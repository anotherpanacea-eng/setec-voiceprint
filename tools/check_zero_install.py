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
import hashlib
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
from seed_capabilities import parse_module  # noqa: E402


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

    Refuse source symlinks before copying: dereferencing could launder
    outside content into the copy, while preserving links would leave the
    supposedly bare tree dependent on its source checkout."""
    symlinks = [path for path in PLUGIN_ROOT.rglob("*") if path.is_symlink()]
    if symlinks:
        relative = ", ".join(
            str(path.relative_to(PLUGIN_ROOT)) for path in symlinks[:5]
        )
        raise GateError(f"plugin tree contains symlink(s): {relative}")
    dest = tmp_root / "setec-voiceprint"
    shutil.copytree(
        PLUGIN_ROOT, dest, symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
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
    manifest_entries = []
    for frag in sorted(manifest_dir.glob("*.yaml")):
        if frag.name == "_meta.yaml":
            continue
        doc = yaml.safe_load(frag.read_text(encoding="utf-8"))
        manifest_entries.extend((doc or {}).get("entries", []))
    for entry in manifest_entries:
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
        else:
            try:
                seed = parse_module(target, bare_root / "scripts", bare_root)
                if seed is None or seed.surface != entry.get("surface"):
                    problems.append(f"{entry.get('id')}: implementation TASK_SURFACE does not match manifest")
            except ValueError as exc:
                problems.append(f"{entry.get('id')}: {exc}")
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
    ("punctuation", "scripts/punctuation_cadence_audit.py", ["--help"]),
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


def check_punctuation_conformance(bare_root: Path, outside_cwd: Path, report: Report) -> None:
    """Exercise the first relocated family through its public launch routes."""
    scripts = bare_root / "scripts"
    launcher = scripts / "punctuation_cadence_audit.py"
    target = outside_cwd / "punctuation-input.txt"
    target.write_text("One clause; another (an aside). Why? A pause -- then, yes!\n", encoding="utf-8")
    # runpy does not add the script's directory. Ordinary import must return
    # the package object, with monkeypatches visible from both names.
    identity = (
        "import sys, importlib; "
        "sys.path.insert(0, sys.argv[1]); "
        "old = importlib.import_module('punctuation_cadence_audit'); "
        "new = importlib.import_module('setec.surfaces.punctuation_cadence_audit'); "
        "assert old is new; "
        "assert str(new.SCRIPT_DIR) == sys.argv[1]; "
        "new._word_count = lambda text: 17; "
        "assert old.audit_punctuation_cadence('words')['n_words'] == 17; "
        "old._word_count = lambda text: 23; "
        "assert new.audit_punctuation_cadence('words')['n_words'] == 23"
    )
    runpy_code = (
        "import runpy, sys; path = sys.argv.pop(1); "
        "runpy.run_path(path, run_name='__main__')"
    )
    # -m needs scripts/ as the module search root; its cwd is in the bare
    # plugin, never the repository. Other launch routes use a foreign cwd.
    commands = [
        ("identity", ["-c", identity, str(scripts)], outside_cwd),
        ("direct", [str(launcher), str(target), "--json"], outside_cwd),
        ("runpy", ["-c", runpy_code, str(launcher), str(target), "--json"], outside_cwd),
        ("module", ["-m", "setec.surfaces.punctuation_cadence_audit", str(target), "--json"], scripts),
        ("dispatch", [str(scripts / "setec_run.py"), "punctuation_cadence_audit", str(target), "--json"], outside_cwd),
    ]
    expected = {"schema_version": "1.0", "tool": "punctuation_cadence_audit",
                "task_surface": "voice_coherence", "available": True}
    for name, argv, cwd in commands:
        try:
            proc = subprocess.run([sys.executable, *argv], cwd=cwd, env=_clean_env(),
                                  capture_output=True, text=True, timeout=30)
            ok = proc.returncode == 0 and "Traceback" not in proc.stderr
            if name != "identity":
                envelope = json.loads(proc.stdout)
                ok = ok and isinstance(envelope, dict) and all(
                    envelope.get(key) == value for key, value in expected.items()
                )
            report.add(f"punctuation:{name}", ok,
                       "" if ok else f"exit={proc.returncode} stderr={proc.stderr[-300:]!r}")
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            report.add(f"punctuation:{name}", False, str(exc))


def check_paragraph_conformance(bare_root: Path, outside_cwd: Path, report: Report) -> None:
    """Keep direct paragraph JSON working without promoting its TODO capability."""
    scripts = bare_root / "scripts"
    launcher = scripts / "paragraph_audit.py"
    target = outside_cwd / "paragraph-input.txt"
    target.write_text(
        "One paragraph has several words. It ends here.\n\n"
        "Why another paragraph? A different rhythm follows.\n", encoding="utf-8",
    )
    identity = (
        "import sys, importlib; sys.path.insert(0, sys.argv[1]); "
        "old = importlib.import_module('paragraph_audit'); "
        "new = importlib.import_module('setec.surfaces.paragraph_audit'); "
        "assert old is new; assert str(new.SCRIPT_DIR) == sys.argv[1]; "
        "new.word_count = lambda text: 17; "
        "assert old.audit_paragraphs('Three words here.')['paragraph_word_counts'] == [17]; "
        "old.word_count = lambda text: 23; "
        "assert new.audit_paragraphs('Three words here.')['paragraph_word_counts'] == [23]"
    )
    runpy_code = (
        "import runpy, sys; path = sys.argv.pop(1); "
        "runpy.run_path(path, run_name='__main__')"
    )
    # Module execution uses the bare scripts search root; direct/runpy and
    # the dispatcher do not need a repository cwd or PYTHONPATH.
    commands = [
        ("identity", ["-c", identity, str(scripts)], outside_cwd),
        ("direct", [str(launcher), str(target), "--json"], outside_cwd),
        ("runpy", ["-c", runpy_code, str(launcher), str(target), "--json"], outside_cwd),
        ("module", ["-m", "setec.surfaces.paragraph_audit", str(target), "--json"], scripts),
        ("dispatch_refusal", [str(scripts / "setec_run.py"), "paragraph_audit", str(target), "--json"], outside_cwd),
        ("missing_input", [str(launcher), str(outside_cwd / "absent-paragraph.txt"), "--json"], outside_cwd),
        ("missing_baseline", [str(launcher), str(target), "--baseline-dir", str(outside_cwd / "absent-paragraph-baseline"), "--json"], outside_cwd),
    ]
    expected = {"schema_version": "1.0", "tool": "paragraph_audit",
                "task_surface": "smoothing_diagnosis", "available": True}
    refusal = {"schema_version": "1.0", "tool": "setec_run", "task_surface": None,
               "surface": "paragraph_audit", "available": False, "reason_category": "bad_input"}
    for name, argv, cwd in commands:
        try:
            proc = subprocess.run([sys.executable, *argv], cwd=cwd, env=_clean_env(),
                                  capture_output=True, text=True, timeout=30)
            ok = "Traceback" not in proc.stderr
            if name in {"missing_input", "missing_baseline"}:
                message = "Input not found:" if name == "missing_input" else "baseline error:"
                ok = ok and proc.returncode == 2 and not proc.stdout.strip() and message in proc.stderr
            elif name == "identity":
                ok = ok and proc.returncode == 0
            else:
                envelope = json.loads(proc.stdout)
                keys = refusal if name == "dispatch_refusal" else expected
                ok = ok and proc.returncode == (2 if name == "dispatch_refusal" else 0)
                ok = ok and isinstance(envelope, dict) and all(
                    envelope.get(key) == value for key, value in keys.items()
                )
                if name == "dispatch_refusal":
                    ok = ok and "unknown surface 'paragraph_audit'" in envelope.get("reason", "")
                else:
                    ok = ok and isinstance(envelope.get("results"), dict) and envelope["results"].get("n_paragraphs") == 2
            report.add(f"paragraph:{name}", ok,
                       "" if ok else f"exit={proc.returncode} stdout={proc.stdout[-300:]!r} stderr={proc.stderr[-300:]!r}")
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            report.add(f"paragraph:{name}", False, str(exc))


def check_repetition_conformance(bare_root: Path, outside_cwd: Path, report: Report) -> None:
    """Exercise the whole family without promoting its two TODO entries."""
    scripts = bare_root / "scripts"
    target = outside_cwd / "repetition-input.txt"
    target.write_text(
        "# Chapter 1\nCopper copper copper lantern.\n"
        "# Chapter 2\nSilver silver silver lantern.\n", encoding="utf-8",
    )
    baseline = outside_cwd / "repetition-baseline"
    baseline.mkdir(exist_ok=True)
    (baseline / "reference.txt").write_text("Lantern river stone.\n", encoding="utf-8")
    runpy_code = (
        "import runpy, sys; path = sys.argv.pop(1); old = sys.modules['__main__']\n"
        "try:\n    runpy.run_path(path, run_name='__main__')\n"
        "finally:\n    assert sys.modules['__main__'] is old"
    )
    # The manuscript helper has optional model imports. -S deliberately keeps
    # these stdlib-only family routes model-free in a bare plugin.
    for stem in ("repetition_audit", "manuscript_repetition_audit", "chapter_distinctiveness_audit"):
        launcher = scripts / (stem + ".py")
        args = [str(target), "--json"]
        if stem != "chapter_distinctiveness_audit":
            args += ["--baseline-dir", str(baseline)]
        commands = [
            ("direct", ["-S", str(launcher), *args], outside_cwd),
            ("runpy", ["-S", "-c", runpy_code, str(launcher), *args], outside_cwd),
            ("module", ["-S", "-m", "setec.surfaces." + stem, *args], scripts),
            ("dispatch", [str(scripts / "setec_run.py"), stem, *args], outside_cwd),
        ]
        for mode, argv, cwd in commands:
            todo = mode == "dispatch" and stem != "repetition_audit"
            expected = {"schema_version": "1.0", "tool": "setec_run" if todo else stem,
                        "task_surface": None if todo else "smoothing_diagnosis",
                        "available": not todo}
            try:
                proc = subprocess.run([sys.executable, "-B", *argv], cwd=cwd, env=_clean_env(),
                                      capture_output=True, text=True, timeout=30)
                envelope = json.loads(proc.stdout)
                ok = proc.returncode == (2 if todo else 0) and "Traceback" not in proc.stderr
                ok = ok and isinstance(envelope, dict) and all(
                    envelope.get(key) == value for key, value in expected.items()
                )
                if todo:
                    ok = ok and envelope.get("surface") == stem and envelope.get("reason_category") == "bad_input"
                    ok = ok and f"unknown surface '{stem}'" in envelope.get("reason", "")
                else:
                    results = envelope.get("results")
                    ok = ok and isinstance(results, dict)
                    if stem == "repetition_audit":
                        ok = ok and any(c.get("word") == "copper" and c.get("count") == 3
                                        for c in results.get("candidates", []))
                    else:
                        ok = ok and results.get("n_chapters") == 2
                report.add(f"{stem}:{mode}", ok,
                           "" if ok else f"exit={proc.returncode} stdout={proc.stdout[-300:]!r} stderr={proc.stderr[-300:]!r}")
            except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                report.add(f"{stem}:{mode}", False, str(exc))


def check_narrative_conformance(bare_root: Path, outside_cwd: Path, report: Report) -> None:
    """Keep the base consumer live and long-form experimental, with mock judging only."""
    scripts = bare_root / "scripts"
    target = outside_cwd / "narrative-input.txt"
    target.write_text("The lantern crossed the river. " * 400, encoding="utf-8")
    runpy_code = (
        "import runpy, sys; path = sys.argv.pop(1); old = sys.modules['__main__']\n"
        "try:\n    runpy.run_path(path, run_name='__main__')\n"
        "finally:\n    assert sys.modules['__main__'] is old"
    )
    for stem in ("narrative_decision_audit", "narrative_decision_long_form"):
        launcher = scripts / (stem + ".py")
        base = stem == "narrative_decision_audit"
        args = [str(target), "--judge", "mock", "--json"] if base else ["--help"]
        commands = [
            ("direct", ["-S", str(launcher), *args], outside_cwd),
            ("runpy", ["-S", "-c", runpy_code, str(launcher), *args], outside_cwd),
            ("module", ["-S", "-m", "setec.surfaces." + stem, *args], scripts),
            ("dispatch", [str(scripts / "setec_run.py"), stem, str(target), "--judge", "mock", "--json"], outside_cwd),
            ("missing_input", ["-S", str(launcher), str(outside_cwd / "absent-narrative.txt"), "--json"], outside_cwd),
        ]
        for mode, argv, cwd in commands:
            try:
                proc = subprocess.run([sys.executable, "-B", *argv], cwd=cwd, env=_clean_env(),
                                      capture_output=True, text=True, timeout=30)
                ok = "Traceback" not in proc.stderr
                if not base and mode in {"direct", "runpy", "module"}:
                    ok = ok and proc.returncode == 0 and "--calibration-emit-segments" in proc.stdout
                elif base and mode == "missing_input":
                    ok = ok and proc.returncode == 1 and not proc.stdout.strip() and "target file not found" in proc.stderr
                else:
                    envelope = json.loads(proc.stdout)
                    refusal = not base
                    expected = {"schema_version": "1.0", "tool": "setec_run" if mode == "dispatch" and refusal else stem,
                                "task_surface": None if mode == "dispatch" and refusal else stem,
                                "available": not refusal}
                    expected_exit = (2 if mode == "dispatch" else 1) if refusal else 0
                    ok = ok and proc.returncode == expected_exit
                    ok = ok and isinstance(envelope, dict) and all(
                        envelope.get(key) == value for key, value in expected.items()
                    )
                    if refusal:
                        ok = ok and envelope.get("reason_category") == "bad_input"
                        if mode == "dispatch":
                            ok = ok and envelope.get("surface") == stem and f"unknown surface '{stem}'" in envelope.get("reason", "")
                        else:
                            ok = ok and "target file not found" in envelope.get("reason", "")
                    else:
                        results = envelope.get("results")
                        ok = ok and isinstance(results, dict) and results.get("judge", {}).get("judge_identity", {}).get("kind") == "mock"
                        ok = ok and isinstance(envelope.get("claim_license"), dict)
                        ok = ok and target.with_suffix(".txt.narrative.json").is_file() and target.with_suffix(".txt.narrative.md").is_file()
                report.add(f"{stem}:{mode}", ok,
                           "" if ok else f"exit={proc.returncode} stdout={proc.stdout[-300:]!r} stderr={proc.stderr[-300:]!r}")
            except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                report.add(f"{stem}:{mode}", False, str(exc))


def check_argument_conformance(bare_root: Path, outside_cwd: Path, report: Report) -> None:
    """Exercise argument JSON delivery and setup/input refusals with no provider."""
    scripts = bare_root / "scripts"
    stem = "argument_decision_audit"
    launcher = scripts / (stem + ".py")
    target = outside_cwd / "argument-input.txt"
    target.write_text("\n\n".join("Because evidence matters, we should act. " * 20 for _ in range(3)), encoding="utf-8")
    runpy_code = (
        "import runpy, sys; path = sys.argv.pop(1); old = sys.modules['__main__']\n"
        "try:\n    runpy.run_path(path, run_name='__main__')\n"
        "finally:\n    assert sys.modules['__main__'] is old"
    )
    args = [str(target), "--judge", "mock", "--json"]
    commands = [
        ("direct", ["-S", str(launcher), *args], outside_cwd),
        ("runpy", ["-S", "-c", runpy_code, str(launcher), *args], outside_cwd),
        ("module", ["-S", "-m", "setec.surfaces." + stem, *args], scripts),
        ("dispatch", [str(scripts / "setec_run.py"), stem, *args], outside_cwd),
        ("missing_input", ["-S", str(launcher), str(outside_cwd / "absent-argument.txt"), "--json"], outside_cwd),
        ("missing_manifest", [str(scripts / "setec_run.py"), stem, str(target), "--judge", "manifest", "--json"], outside_cwd),
        ("baseline_without_register", [str(scripts / "setec_run.py"), stem, *args, "--baseline-dir", str(outside_cwd)], outside_cwd),
    ]
    for mode, argv, cwd in commands:
        try:
            proc = subprocess.run([sys.executable, "-B", *argv], cwd=cwd, env=_clean_env(),
                                  capture_output=True, text=True, timeout=30)
            ok = "Traceback" not in proc.stderr
            if mode == "missing_input":
                ok = ok and proc.returncode == 1 and not proc.stdout.strip() and "target file not found" in proc.stderr
            else:
                envelope = json.loads(proc.stdout)
                refusal = mode in {"missing_manifest", "baseline_without_register"}
                expected = {"schema_version": "1.0", "tool": "setec_run" if refusal else stem,
                            "task_surface": None if refusal else stem, "available": not refusal}
                ok = ok and proc.returncode == (3 if refusal else 0)
                ok = ok and isinstance(envelope, dict) and all(envelope.get(k) == v for k, v in expected.items())
                if refusal:
                    message = "judge construction failed" if mode == "missing_manifest" else "--baseline-dir requires --register"
                    ok = ok and envelope.get("surface") == stem and envelope.get("reason_category") == "bad_input"
                    ok = ok and message in envelope.get("reason", "")
                else:
                    results = envelope.get("results")
                    ok = ok and isinstance(results, dict) and results.get("judge", {}).get("judge_identity", {}).get("kind") == "mock"
                    ok = ok and results.get("aggregate", {}).get("verdict_band") == "uncalibrated"
                    ok = ok and isinstance(envelope.get("claim_license"), dict)
                    ok = ok and target.with_suffix(".txt.argument.json").is_file() and target.with_suffix(".txt.argument.md").is_file()
            report.add(f"{stem}:{mode}", ok,
                       "" if ok else f"exit={proc.returncode} stdout={proc.stdout[-300:]!r} stderr={proc.stderr[-300:]!r}")
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            report.add(f"{stem}:{mode}", False, str(exc))


def check_argument_pattern_conformance(bare_root: Path, outside_cwd: Path, report: Report) -> None:
    """Offline whole-family launch routes; only AGD retains normalized delivery."""
    scripts = bare_root / "scripts"
    target = outside_cwd / "pattern-input.txt"
    target.write_text("Because evidence matters, we should act. Therefore the council may want to reconsider. " * 20,
                      encoding="utf-8")
    runpy_code = (
        "import runpy, sys; path = sys.argv.pop(1); old = sys.modules['__main__']\n"
        "try:\n    runpy.run_path(path, run_name='__main__')\n"
        "finally:\n    assert sys.modules['__main__'] is old"
    )
    for stem in ("agd_move_scan", "enthymeme_gapflag", "fallacy_scan", "warrant_probe"):
        launcher = scripts / (stem + ".py")
        structural = stem == "enthymeme_gapflag"
        consumer = stem == "agd_move_scan"
        surface = stem if consumer else "argument_pattern_scan"
        args = (["--target", str(target)] if structural else [str(target), "--judge", "mock"]) + ["--json"]
        missing = outside_cwd / "absent-pattern.txt"
        missing_args = (["--target", str(missing)] if structural else [str(missing), "--judge", "mock"]) + ["--json"]
        identity = (
            "import importlib,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "a=importlib.import_module(sys.argv[2]); b=importlib.import_module('setec.surfaces.'+sys.argv[2]); "
            "assert a is b; assert a.SCRIPT_DIR == Path(sys.argv[1]); "
            "b.count_words=lambda text:17; assert a.count_words('ignored')==17; "
            "a.count_words=lambda text:23; assert b.count_words('ignored')==23; "
            "assert not any(n in sys.modules for n in ('torch','spacy','transformers','anthropic','openai'))"
        )
        commands = [
            ("identity", ["-S", "-c", identity, str(scripts), stem], outside_cwd),
            ("direct", ["-S", str(launcher), *args], outside_cwd),
            ("runpy", ["-S", "-c", runpy_code, str(launcher), *args], outside_cwd),
            ("module", ["-S", "-m", "setec.surfaces." + stem, *args], scripts),
            ("dispatch", [str(scripts / "setec_run.py"), stem, *args], outside_cwd),
            ("missing_input", ["-S", str(launcher), *missing_args], outside_cwd),
        ]
        if consumer:
            commands.extend([
                ("missing_judge", [str(scripts / "setec_run.py"), stem, str(target), "--json"], outside_cwd),
                ("missing_manifest", [str(scripts / "setec_run.py"), stem, str(target),
                                      "--judge", "manifest", "--json"], outside_cwd),
            ])
        for mode, argv, cwd in commands:
            try:
                proc = subprocess.run([sys.executable, "-B", *argv], cwd=cwd, env=_clean_env(),
                                      capture_output=True, text=True, timeout=30)
                ok = "Traceback" not in proc.stderr
                if mode == "identity":
                    ok = ok and proc.returncode == 0
                else:
                    envelope = json.loads(proc.stdout)
                    unknown = mode == "dispatch" and not consumer
                    setup = mode in {"missing_judge", "missing_manifest"}
                    missing_input = mode == "missing_input"
                    refusal = unknown or setup or missing_input
                    dispatcher_error = unknown or mode == "missing_judge"
                    expected = {"schema_version": "1.0", "tool": "setec_run" if dispatcher_error else stem,
                                "task_surface": None if dispatcher_error else surface, "available": not refusal}
                    expected_exit = 2 if unknown else 3 if setup or (missing_input and structural) else 0
                    ok = ok and proc.returncode == expected_exit and isinstance(envelope, dict)
                    ok = ok and all(envelope.get(k) == v for k, v in expected.items())
                    if refusal:
                        ok = ok and envelope.get("reason_category") == "bad_input"
                        if unknown or setup:
                            message = (f"unknown surface '{stem}'" if unknown else
                                       "judge construction failed" if mode == "missing_manifest" else "--judge")
                            ok = ok and message in envelope.get("reason", "")
                            if dispatcher_error:
                                ok = ok and envelope.get("surface") == stem
                        else:
                            ok = ok and "cannot read" in envelope.get("reason", "")
                    else:
                        results = envelope.get("results")
                        key = {"agd_move_scan": "observations", "enthymeme_gapflag": "enthymeme_gap_flags",
                               "fallacy_scan": "rhetorical_move_flags", "warrant_probe": "warrant_coverage"}[stem]
                        ok = ok and isinstance(results, dict) and isinstance(results.get(key), list)
                        ok = ok and isinstance(envelope.get("claim_license"), dict)
                        if not structural:
                            ok = ok and results.get("judge", {}).get("judge_identity", {}).get("kind") == "mock"
                report.add(f"{stem}:{mode}", ok,
                           "" if ok else f"exit={proc.returncode} stdout={proc.stdout[-300:]!r} stderr={proc.stderr[-300:]!r}")
            except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
                report.add(f"{stem}:{mode}", False, str(exc))


def check_argument_quality_conformance(bare_root: Path, outside_cwd: Path, report: Report) -> None:
    """Offline quality/calibration CLI artifacts, without promoting either consumer."""
    scripts = bare_root / "scripts"
    target = outside_cwd / "quality-input.txt"
    target.write_text(
        "Because evidence matters, the council should consider the available trials. " * 20
        + "\n\n[[claim support=none topic=t_x]] Zoning reform clearly works, without question.",
        encoding="utf-8",
    )
    runpy_code = (
        "import runpy,sys; path=sys.argv.pop(1); old=sys.modules['__main__']\n"
        "try:\n    runpy.run_path(path,run_name='__main__')\n"
        "finally:\n    assert sys.modules['__main__'] is old"
    )
    for stem, surface in (("argquality_dimension_profile", "argquality_dimension_profile"),
                          ("argument_certainty_calibration", "argument_calibration")):
        quality = stem == "argquality_dimension_profile"
        launcher = scripts / (stem + ".py")
        identity = (
            "import importlib,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "a=importlib.import_module(sys.argv[2]); b=importlib.import_module('setec.surfaces.'+sys.argv[2]); "
            "assert a is b and a.SCRIPT_DIR==Path(sys.argv[1]); "
            "b.count_words=lambda text:17; assert a.count_words('ignored')==17; "
            "a.count_words=lambda text:23; assert b.count_words('ignored')==23; "
            "assert not any(n in sys.modules for n in ('torch','spacy','transformers','anthropic','openai'))"
        )
        for mode in ("identity", "direct", "runpy", "module", "dispatch", "missing_input"):
            output = outside_cwd / (stem + "-" + mode + ".json")
            markdown = output.with_suffix(".md")
            input_path = outside_cwd / "absent-quality.txt" if mode == "missing_input" else target
            args = [str(input_path), "--judge", "mock", "--json", "--out", str(output)]
            if quality:
                args += ["--out-md", str(markdown)]
            cwd = outside_cwd
            if mode == "identity":
                argv = ["-S", "-c", identity, str(scripts), stem]
            elif mode == "module":
                argv, cwd = ["-S", "-m", "setec.surfaces." + stem, *args], scripts
            elif mode == "runpy":
                argv = ["-S", "-c", runpy_code, str(launcher), *args]
            elif mode == "dispatch":
                argv = [str(scripts / "setec_run.py"), stem, *args]
            else:
                argv = ["-S", str(launcher), *args]
            try:
                proc = subprocess.run([sys.executable, "-B", *argv], cwd=cwd, env=_clean_env(),
                                      capture_output=True, text=True, timeout=30)
                ok = "Traceback" not in proc.stderr
                if mode == "identity":
                    ok = ok and proc.returncode == 0
                else:
                    dispatch = mode == "dispatch"
                    missing = mode == "missing_input"
                    envelope = json.loads(proc.stdout if dispatch or quality else output.read_text(encoding="utf-8"))
                    expected = {"schema_version": "1.0", "tool": "setec_run" if dispatch else stem,
                                "task_surface": None if dispatch else surface, "available": not (dispatch or missing)}
                    expected_exit = 2 if dispatch else 1 if missing and not quality else 0
                    ok = ok and proc.returncode == expected_exit and isinstance(envelope, dict)
                    ok = ok and all(envelope.get(k) == v for k, v in expected.items())
                    if dispatch:
                        ok = ok and envelope.get("surface") == stem and envelope.get("reason_category") == "bad_input"
                        ok = ok and f"unknown surface '{stem}'" in envelope.get("reason", "") and not output.exists()
                    else:
                        ok = ok and json.loads(output.read_text(encoding="utf-8")) == envelope
                        if not quality:
                            ok = ok and not proc.stdout.strip()
                        if missing:
                            ok = ok and envelope.get("reason_category") == "bad_input" and "cannot read" in envelope.get("reason", "")
                        else:
                            results = envelope.get("results", {})
                            ok = ok and isinstance(envelope.get("claim_license"), dict)
                            if quality:
                                ok = ok and set(results.get("dimensions", {})) == {"logic", "rhetoric", "dialectic"}
                                ok = ok and results.get("judge", {}).get("judge_identity", {}).get("kind") == "mock"
                                ok = ok and markdown.read_text(encoding="utf-8").startswith("# ")
                            else:
                                ok = ok and bool(results.get("claims")) and results.get("judge", {}).get("kind") == "mock"
                report.add(f"{stem}:{mode}", ok,
                           "" if ok else f"exit={proc.returncode} stdout={proc.stdout[-300:]!r} stderr={proc.stderr[-300:]!r}")
            except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
                report.add(f"{stem}:{mode}", False, str(exc))


def check_argument_consistency_conformance(bare_root: Path, outside_cwd: Path, report: Report) -> None:
    """Mock pair routes; isolate optional NLP imports, not the dispatcher contract."""
    scripts = bare_root / "scripts"
    focal = outside_cwd / "consistency-focal.txt"
    reference = outside_cwd / "consistency-pool.jsonl"
    focal.write_text("[[topic=tax type=claim stance=for]] The policy should proceed. " * 15, encoding="utf-8")
    reference.write_text(json.dumps({"id": "pool", "text":
        "[[topic=tax type=claim stance=against]] The policy should not proceed. " * 15}) + "\n", encoding="utf-8")
    work = outside_cwd / "position-input.txt"
    work.write_text(
        "[[pair=p1 side=a q=What policy should apply?]] The council should act. "
        "[[pair=p1 side=b q=What policy should apply?]] The council should wait. "
        + "Because evidence matters, consider the available trials. " * 45, encoding="utf-8",
    )
    runpy_code = (
        "import runpy,sys; path=sys.argv.pop(1); old=sys.modules['__main__']\n"
        "try:\n    runpy.run_path(path,run_name='__main__')\n"
        "finally:\n    assert sys.modules['__main__'] is old"
    )
    # The dispatcher needs PyYAML, but its surface child must not load optional
    # NLP models or trigger variance_audit's NLTK download. Only interpreter
    # flags change at its existing subprocess seam; discovery/output stay real.
    dispatch_code = (
        "import runpy,sys; from pathlib import Path; path=sys.argv.pop(1); "
        "sys.path.insert(0,str(Path(path).parent)); "
        "sys.modules.update(dict.fromkeys(('torch','transformers','spacy','nltk','sentence_transformers','anthropic','openai'))); "
        "ns=runpy.run_path(path); main=ns['main']; g=main.__globals__; original=g['_run_subprocess']; "
        "g['_run_subprocess']=lambda cmd: original([cmd[0],'-B','-S',*cmd[1:]]); "
        "sys.exit(main())"
    )
    for stem, surface in (("cross_doc_argument_consistency", "argument_consistency"),
                          ("position_pair_register", "position_pair_register")):
        position = stem == "position_pair_register"
        launcher = scripts / (stem + ".py")
        identity = (
            "import importlib,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "a=importlib.import_module(sys.argv[2]); b=importlib.import_module('setec.surfaces.'+sys.argv[2]); "
            "assert a is b and a.SCRIPT_DIR==Path(sys.argv[1]); "
        )
        identity += ("a.render_markdown=lambda env:'shared'; assert b.render_markdown({})=='shared'" if position else
                     "a.classify_legitimate_variation=lambda text:('genuine','shared'); "
                     "assert b.classify_legitimate_variation('ignored')==('genuine','shared')")
        identity += "; assert not any(n in sys.modules for n in ('torch','spacy','nltk','transformers','anthropic','openai'))"
        for mode in ("identity", "direct", "runpy", "module", "dispatch", "missing_input"):
            output = outside_cwd / (stem + "-" + mode + ".json")
            markdown = output.with_suffix(".md")
            input_path = outside_cwd / "absent-consistency.txt" if mode == "missing_input" else work if position else focal
            args = ([str(input_path)] if position else
                    ["--focal", str(input_path), "--reference-manifest", str(reference)]) + ["--judge", "mock", "--json"]
            if mode != "dispatch":
                args += ["--out", str(output)]
                if position:
                    args += ["--out-md", str(markdown)]
            cwd = outside_cwd
            if mode == "identity":
                argv = ["-S", "-c", identity, str(scripts), stem]
            elif mode == "runpy":
                argv = ["-S", "-c", runpy_code, str(launcher), *args]
            elif mode == "module":
                argv, cwd = ["-S", "-m", "setec.surfaces." + stem, *args], scripts
            elif mode == "dispatch":
                argv = ["-c", dispatch_code, str(scripts / "setec_run.py"), stem, *args]
            else:
                argv = ["-S", str(launcher), *args]
            try:
                proc = subprocess.run([sys.executable, "-B", *argv], cwd=cwd, env=_clean_env(),
                                      capture_output=True, text=True, timeout=30)
                ok = "Traceback" not in proc.stderr
                if mode == "identity":
                    ok = ok and proc.returncode == 0
                else:
                    unknown = mode == "dispatch" and not position
                    missing = mode == "missing_input"
                    envelope = json.loads(proc.stdout if position or unknown else output.read_text(encoding="utf-8"))
                    expected = {"schema_version": "1.0", "tool": "setec_run" if unknown else stem,
                                "task_surface": None if unknown else surface, "available": not (unknown or missing)}
                    expected_exit = 2 if unknown else (3 if position else 1) if missing else 0
                    ok = ok and proc.returncode == expected_exit and isinstance(envelope, dict)
                    ok = ok and all(envelope.get(k) == v for k, v in expected.items())
                    if unknown:
                        ok = ok and envelope.get("surface") == stem and envelope.get("reason_category") == "bad_input"
                        ok = ok and f"unknown surface '{stem}'" in envelope.get("reason", "")
                    else:
                        if mode != "dispatch":
                            ok = ok and json.loads(output.read_text(encoding="utf-8")) == envelope
                        if not position:
                            ok = ok and not proc.stdout.strip()
                        if missing:
                            message = "target file not found" if position else "cannot read focal document"
                            ok = ok and envelope.get("reason_category") == "bad_input" and message in envelope.get("reason", "")
                        else:
                            results = envelope.get("results", {})
                            ok = ok and isinstance(envelope.get("claim_license"), dict)
                            if position:
                                ok = ok and bool(results.get("pairs")) and results.get("judge", {}).get("judge_identity", {}).get("kind") == "mock"
                                if mode != "dispatch":
                                    ok = ok and markdown.read_text(encoding="utf-8").startswith("# Position-Pair Register")
                            else:
                                ok = ok and results.get("n_docs") == 2 and results.get("judge", {}).get("kind") == "mock"
                report.add(f"{stem}:{mode}", ok,
                           "" if ok else f"exit={proc.returncode} stdout={proc.stdout[-300:]!r} stderr={proc.stderr[-300:]!r}")
            except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
                report.add(f"{stem}:{mode}", False, str(exc))


def check_non_voice_structure_conformance(bare_root: Path, outside_cwd: Path, report: Report) -> None:
    """Whole stdlib descriptive surfaces; normalized dispatch remains unsupported."""
    scripts = bare_root / "scripts"
    target = outside_cwd / "structure-input.md"
    target.write_text("# Heading\n\n" + "word " * 320 +
                      "\n- at the end of the day\n> according to Smith (Smith, 2019)\n"
                      "[source](https://example.com/p)\n", encoding="utf-8")
    short = outside_cwd / "structure-short.md"
    short.write_text("A short document.", encoding="utf-8")
    runpy_code = (
        "import runpy,sys; path=sys.argv.pop(1); old=sys.modules['__main__']\n"
        "try:\n    runpy.run_path(path,run_name='__main__')\n"
        "finally:\n    assert sys.modules['__main__'] is old"
    )
    for stem, surface in (("document_layout_audit", "document_layout"),
                          ("formulaicity_audit", "formulaicity"),
                          ("reference_ecology_audit", "reference_ecology")):
        launcher = scripts / (stem + ".py")
        identity = (
            "import importlib,sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "a=importlib.import_module(sys.argv[2]); b=importlib.import_module('setec.surfaces.'+sys.argv[2]); "
            "assert a is b and a.SCRIPT_DIR==Path(sys.argv[1]); "
            "a.count_words=lambda text:17; assert b.count_words('ignored')==17; "
            "b.count_words=lambda text:23; assert a.count_words('ignored')==23; "
            "assert not any(n in sys.modules for n in ('torch','spacy','transformers','anthropic','openai'))"
        )
        for mode in ("identity", "direct", "runpy", "module", "output_file", "short", "missing_input", "dispatch"):
            output = outside_cwd / (stem + "-structure.json")
            input_path = short if mode == "short" else outside_cwd / "absent-structure.md" if mode == "missing_input" else target
            args = [str(input_path), "--json"]
            if mode == "output_file":
                args += ["--out", str(output)]
            cwd = outside_cwd
            if mode == "identity":
                argv = ["-S", "-c", identity, str(scripts), stem]
            elif mode == "module":
                argv, cwd = ["-S", "-m", "setec.surfaces." + stem, *args], scripts
            elif mode == "runpy":
                argv = ["-S", "-c", runpy_code, str(launcher), *args]
            elif mode == "dispatch":
                argv = [str(scripts / "setec_run.py"), stem, *args]
            else:
                argv = ["-S", str(launcher), *args]
            try:
                proc = subprocess.run([sys.executable, "-B", *argv], cwd=cwd, env=_clean_env(),
                                      capture_output=True, text=True, timeout=30)
                ok = "Traceback" not in proc.stderr
                if mode == "identity":
                    ok = ok and proc.returncode == 0
                elif mode == "missing_input":
                    ok = ok and proc.returncode == 2 and not proc.stdout.strip() and "Input not found:" in proc.stderr
                else:
                    envelope = json.loads(output.read_text(encoding="utf-8") if mode == "output_file" else proc.stdout)
                    dispatch = mode == "dispatch"
                    unavailable = dispatch or mode == "short"
                    expected = {"schema_version": "1.0", "tool": "setec_run" if dispatch else stem,
                                "task_surface": None if dispatch else surface, "available": not unavailable}
                    ok = ok and proc.returncode == (2 if dispatch else 0) and isinstance(envelope, dict)
                    ok = ok and all(envelope.get(k) == v for k, v in expected.items())
                    if dispatch:
                        ok = ok and envelope.get("surface") == stem and envelope.get("reason_category") == "bad_input"
                        ok = ok and f"unknown surface '{stem}'" in envelope.get("reason", "")
                    elif mode == "short":
                        ok = ok and envelope.get("results") == {} and envelope.get("claim_license") is None
                        ok = ok and any("300-word" in w for w in envelope.get("warnings", []))
                    else:
                        results = envelope.get("results", {})
                        ok = ok and isinstance(envelope.get("claim_license"), dict)
                        if surface == "document_layout":
                            ok = ok and results.get("headings", {}).get("count") == 1
                        elif surface == "formulaicity":
                            ok = ok and results.get("total_hits") == 1
                        else:
                            ok = ok and results.get("citations", {}).get("parenthetical") == 1
                    if mode == "output_file":
                        ok = ok and not proc.stdout.strip() and "Wrote report to" in proc.stderr
                report.add(f"{stem}:{mode}", ok,
                           "" if ok else f"exit={proc.returncode} stdout={proc.stdout[-300:]!r} stderr={proc.stderr[-300:]!r}")
            except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
                report.add(f"{stem}:{mode}", False, str(exc))


# ---------- consumer-case table (CB-6) ------------------------------------
#
# Real `setec_run.py <id> ... --json` runs, in the bare copy with
# setec_hermetic_backend=stub, for the capabilities apodictic and
# setec-voicewright consume. Each envelope must equal its checked-in
# tools/zero_install_cases/<case>.json exactly, apart from the dotted fields a
# row lists under "normalize" (set to null on both sides). Expected envelopes
# are written once and reviewed; this gate never regenerates them.

CASES_DIR = REPO_ROOT / "tools" / "zero_install_cases"
CONSUMER_INPUT = (
    "The river ran past the old mill. She watched it for a long time; nobody came.\n\n"
    "Later, the lamps were lit. \"Why now?\" he asked. She did not answer.\n\n"
) * 6
# A small synthetic baseline folder, for surfaces that compare against one.
CONSUMER_BASELINE = [
    "Morning came slowly over the harbour. The boats rocked, and the gulls argued over scraps.\n\n" * 8,
    "He kept his letters in a tin box. Some he read twice; others he never opened at all.\n\n" * 8,
    "The road bent east past the chapel. Nobody walked it after dark, though no one said why.\n\n" * 8,
]
# Upstream results for mimicry: every listed phrase occurs in CONSUMER_INPUT and
# the distance is high, which is the lexical-without-syntactic shape it flags.
CONSUMER_JSON_INPUTS = {
    "consumer-idiolect.json": {"preservation_list": [
        {"phrase": "the old mill"}, {"phrase": "nobody came"},
        {"phrase": "the lamps were lit"}]},
    "consumer-distance.json": {"overall": {"weighted_delta": 2.0}},
    # The synthetic six-family request the s5_distance contract tests use.
    "consumer-s5-request.json": json.loads(
        (PLUGIN_ROOT / "scripts" / "tests" / "fixtures" / "s5_distance_request.json")
        .read_text(encoding="utf-8")),
}
# A tiny general_imposters corpus: two consumer-baseline files are the
# candidate's identity baseline; the third plus these four are five impostor
# writers (its MIN_IMPOSTORS).
CONSUMER_IMPOSTOR_TEXTS = [
    "The ferry left at noon. Passengers crowded the rail and watched the island shrink.\n\n" * 8,
    "Grain prices rose again this spring. Farmers held their stock and waited for rain.\n\n" * 8,
    "She tuned the old piano herself. Two keys still stuck, but the rest rang true.\n\n" * 8,
    "Snow closed the pass by evening. The drivers slept in their cabs until the ploughs came.\n\n" * 8,
]
# Marked prose for position_pair_register's mock judge, which pairs only
# passages tagged [[pair=<id> side=<a|b> q=<question>]].
CONSUMER_PAIRS_INPUT = (
    "[[pair=p1 side=a q=Should the town keep the old mill?]] The mill should stay. "
    "It anchors the river walk, and the repairs cost less than a new hall.\n\n"
    "[[pair=p2 side=a q=Who should pay for the lamps?]] The shops should pay, since "
    "they gain the evening trade.\n\n"
    "[[pair=p1 side=b q=Should the town keep the old mill?]] The mill should go. "
    "Its floors are unsafe, and the land would serve the school better.\n\n"
    "[[pair=p2 side=b q=Who should pay for the lamps?]] The council should pay, "
    "since the lamps light a public road.\n\n"
)
CONSUMER_CASES = [
    {"case": "punctuation", "id": "punctuation_cadence_audit",
     "args": ["consumer-input.txt"], "exit": 0},
    # mdd_sd is listed only when spaCy is installed, so the list is not pinned.
    {"case": "manuscript", "id": "manuscript_audit", "args": ["consumer-input.txt"], "exit": 0,
     "normalize": ["results.chapters.0.compression.skipped_signals"]},
    {"case": "narrative_mock", "id": "narrative_decision_audit",
     "args": ["consumer-input.txt", "--judge", "mock"], "exit": 0,
     "normalize": ["results.run_timestamp_utc"]},
    {"case": "narrative_api_refused", "id": "narrative_decision_audit",
     "args": ["consumer-input.txt", "--judge", "anthropic", "--judge-model", "synthetic-model"],
     "exit": 3},
    {"case": "argument_mock", "id": "argument_decision_audit",
     "args": ["consumer-input.txt", "--judge", "mock"], "exit": 0,
     "normalize": ["results.run_timestamp_utc"]},
    {"case": "repetition", "id": "repetition_audit",
     "args": ["consumer-input.txt", "--baseline-dir", "consumer-baseline"], "exit": 0},
    {"case": "mimicry", "id": "mimicry_cosplay_audit",
     "args": ["--target", "consumer-input.txt", "--idiolect-json", "consumer-idiolect.json",
              "--voice-distance-json", "consumer-distance.json"], "exit": 0},
    # Tier 2 is spaCy POS output, which moves with the installed model; Tier 3
    # and FKGL change when sentence_transformers or textstat is installed.
    {"case": "variance", "id": "variance_audit",
     "args": ["consumer-input.txt", "--no-tier2", "--no-tier3"], "exit": 0,
     "normalize": ["results.audit.tier1.fkgl"]},
    {"case": "agd_mock", "id": "agd_move_scan",
     "args": ["consumer-input.txt", "--judge", "mock"], "exit": 0},
    {"case": "position_pairs_mock", "id": "position_pair_register",
     "args": ["consumer-pairs.txt", "--judge", "mock"], "exit": 0,
     "normalize": ["results.run_timestamp_utc"]},
    # implementation_sha256 hashes the surface's own source, so any edit moves it.
    {"case": "s5", "id": "s5_distance", "args": ["consumer-s5-request.json"], "exit": 0,
     "normalize": ["results.implementation_sha256"]},
    # POS and dependency families need spaCy, which moves with the installed model.
    {"case": "author_export", "id": "author_corpus_export",
     "args": ["--source-manifest", "gmail_sent=ai-prose-baselines-private/gmail_sent/draft_manifest.jsonl",
              "--register-map", "gmail_sent:personal=email.personal",
              "--allowed-ai-status", "pre_ai_human", "--persona", "fixture_persona",
              "--hmac-key", "ai-prose-baselines-private/export.key",
              "--output-dir", "ai-prose-baselines-private/export", "--dry-run"],
     "exit": 0, "normalize": ["results.producer_receipt.producer_revision"]},
    # A real stage-01 run under the default umask: it completes only because
    # acquisition writes owner-only artifacts. The descriptor carries a timestamp
    # and the manifest the acquisition date, so their identities (and the domain
    # and lineage hashes over them) are normalized; the config identity and the
    # artifact set are pinned.
    {"case": "gmail_pipeline_smoke", "id": "gmail_author_pipeline",
     "args": ["--request", "ai-prose-baselines-private/pipeline/runs/one/request.json"], "exit": 0,
     "normalize": ["results.domain_receipts.0.domain_identity",
                   "results.lineage_receipt_sha256",
                   "results.output_identities.0.identity",
                   "results.output_identities.1.identity"]},
    {"case": "general_imposters", "id": "general_imposters",
     "args": ["--target", "consumer-input.txt", "--manifest", "consumer-gi.jsonl",
              "--candidate-persona", "cand"], "exit": 0},
    {"case": "idiolect", "id": "idiolect_detector",
     "args": ["--target-dir", "consumer-target", "--reference-dir", "consumer-baseline"],
     "exit": 0},
    {"case": "voice_distance", "id": "voice_distance",
     "args": ["consumer-input.txt", "--baseline-dir", "consumer-baseline", "--no-spacy"],
     "exit": 0, "normalize": ["target.spacy_available"]},
    {"case": "voice_profile", "id": "voice_profile",
     "args": ["--baseline-dir", "consumer-baseline", "--no-spacy", "--char-top", "20"],
     "exit": 0},
    {"case": "pov_profile", "id": "pov_voice_profile",
     "args": ["--manifest", "consumer-pov.jsonl", "--no-spacy",
              "--top-distinguishing", "3"], "exit": 0},
    # The model surfaces: the mode disables their required model modules, so
    # each refuses with the same envelope whether or not they are installed.
    {"case": "binoculars_refused", "id": "binoculars_audit",
     "args": ["consumer-input.txt"], "exit": 3},
    {"case": "voice_fingerprint_refused", "id": "voice_fingerprint",
     "args": ["consumer-input.txt"], "exit": 3},
]
# Consumer IDs with no case yet. Adding a case means deleting its ID here.
PENDING_CONSUMERS: frozenset[str] = frozenset()


def _blank(envelope: dict, dotted: str) -> None:
    """Null one dotted field; a numeric segment indexes a list."""
    *parents, leaf = dotted.split(".")
    node = envelope
    for key in parents:
        if isinstance(node, list) and key.isdigit() and int(key) < len(node):
            node = node[int(key)]
        else:
            node = node.get(key) if isinstance(node, dict) else None
    if isinstance(node, dict) and leaf in node:
        node[leaf] = None


def _posix_strings(value):
    """Native Windows paths in an envelope, compared in POSIX form."""
    if isinstance(value, str):
        return value.replace("\\", "/")
    if isinstance(value, list):
        return [_posix_strings(item) for item in value]
    if isinstance(value, dict):
        return {_posix_strings(key): _posix_strings(item) for key, item in value.items()}
    return value


def _write_author_export_inputs(outside_cwd: Path) -> None:
    """One synthetic gmail_sent source, a 0600 HMAC key and an output folder,
    inside a private root, in the shape the exporter's own tests use."""
    root = outside_cwd / "ai-prose-baselines-private"
    source = root / "gmail_sent"
    source.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    text = "Email message words in a somewhat more deliberate synthetic rhythm."
    (source / "piece.txt").write_bytes(text.encode("utf-8"))
    content_hash = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    (source / "piece.meta.json").write_text(json.dumps({
        "content_hash": content_hash,
        "author_corpus_thread_locator": "sha256:" + "3" * 64,
        "author_corpus_entry_locator": "sha256:" + "4" * 64,
        "author_corpus_order_timestamp": "2017-01-02T12:00:00+00:00",
    }), encoding="utf-8")
    entry = {
        "id": "gmail_sent-1", "path": "piece.txt", "author": "Synthetic Author",
        "persona": "fixture_persona", "register": "personal", "date_written": "2017-01-02",
        "ai_status": "pre_ai_human", "language_status": "native", "word_count": 10,
        "use": ["voice_profile"], "split": "baseline", "privacy": "private",
        "content_hash": content_hash, "source": "gmail_takeout_local",
        "corpus_role": "identity_baseline", "era": "pre_chatgpt",
        "consent_status": "author_consent", "acquired_via": "acquire_gmail_sent_1",
    }
    # Bytes, not text mode: the receipt hashes the raw manifest, so no CRLF on Windows.
    (source / "draft_manifest.jsonl").write_bytes((json.dumps(entry) + "\n").encode("utf-8"))
    key = root / "export.key"
    key.write_bytes(b"k" * 32)
    if os.name != "nt":
        key.chmod(0o600)


def _write_gmail_pipeline_inputs(outside_cwd: Path) -> None:
    """A one-message synthetic Sent mbox, an HMAC key and a stage-01 request for
    gmail_author_pipeline, all owner-only, in the shape its own tests use."""
    # The acquirer writes only under a directory named ai-prose-baselines-private.
    private = outside_cwd / "ai-prose-baselines-private"
    root = private / "pipeline"
    run = root / "runs" / "one"
    run.mkdir(parents=True, exist_ok=True)
    for path in (private, root, root / "runs", run):
        path.chmod(0o700)
    mbox = ("From owner@example.test Mon Jan 04 12:00:00 2021\n"
            "From: Synthetic Owner <owner@example.test>\nTo: friend@example.test\n"
            "Subject: Synthetic note\nDate: Mon, 04 Jan 2021 12:00:00 +0000\n"
            "Message-ID: <synthetic-1@example.test>\nX-Gmail-Labels: Sent\n"
            "Content-Type: text/plain; charset=utf-8\n\n"
            "The river ran past the old mill and nobody came, so I wrote this note anyway.\n\n")
    source = {
        "adapter": "gmail_takeout_sent/1", "mbox": "source.mbox",
        "own_addresses": ["owner@example.test"], "persona": "synthetic-author",
        "author": None, "register": "personal", "since": None, "until": None,
        "sent_label_token": "Sent", "recipient_map": None, "name_map": None,
        "own_signature_lines": None, "consent_status": "author_consent",
        "min_words_per_piece": 1, "max_items": 5, "allow_empty": False,
        "output_dir": "acquired", "manifest": "acquired/source.jsonl",
        "smoke_dir": "smoke", "smoke_since": "2020-01-01", "smoke_until": None,
    }
    corpus = {
        "strict_manifest": True, "check_conflict_copies": True,
        "dedup_manifest": "acquired/dedup.jsonl", "dedup_report": "dedup-report.json",
        "dedup_threshold": 0.8, "dedup_num_perm": 8, "dedup_shingle_size": 2,
        "hmac_key": "hmac.key", "allowed_ai_status": ["unknown"],
        "register_map": {"personal": "email.personal"},
        "package_smoke_dir": "packages/smoke", "package_dir": "packages/full",
        "producer_envelope": "producer-envelope.json",
        "smoke_max_records": 1, "smoke_max_text_bytes": 128,
    }
    request = {
        "schema": "setec-gmail-author-pipeline-request/1", "action": "run",
        "stage_id": "01_source_smoke", "private_root": str(root), "run_root": "runs/one",
        "config": {"source": source, "corpus": corpus}, "prior": [],
    }
    for path, data in ((root / "source.mbox", mbox.encode("utf-8")),
                       (root / "hmac.key", b"k" * 32),
                       (run / "request.json",
                        json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8"))):
        path.write_bytes(data)
        path.chmod(0o600)


def check_consumer_cases(bare_root: Path, outside_cwd: Path, report: Report) -> None:
    yaml = _load_yaml()
    consumers = set()
    for frag in sorted((bare_root / "capabilities.d").glob("*.yaml")):
        for entry in (yaml.safe_load(frag.read_text(encoding="utf-8")) or {}).get("entries", []):
            # Only json_delivery entries are setec_run-dispatchable; the rest
            # sit outside the normalized dispatcher and cannot have a case here.
            if entry.get("json_delivery") is not None and \
                    {"apodictic", "setec-voicewright"} & set(entry.get("consumers") or []):
                consumers.add(entry["id"])
    cased = {row["id"] for row in CONSUMER_CASES}
    unknown = sorted((cased | PENDING_CONSUMERS) - consumers)
    missing = sorted(consumers - cased - PENDING_CONSUMERS)
    both = sorted(cased & PENDING_CONSUMERS)
    report.add("consumer_cases:coverage", not (unknown or missing or both),
               "" if not (unknown or missing or both) else
               f"no case or pending entry: {missing}; not a consumer ID: {unknown}; "
               f"cased but still pending: {both}")

    (outside_cwd / "consumer-input.txt").write_text(CONSUMER_INPUT, encoding="utf-8")
    (outside_cwd / "consumer-pairs.txt").write_text(CONSUMER_PAIRS_INPUT, encoding="utf-8")
    (outside_cwd / "consumer-target").mkdir(exist_ok=True)
    (outside_cwd / "consumer-target" / "t.txt").write_text(CONSUMER_INPUT, encoding="utf-8")
    _write_author_export_inputs(outside_cwd)
    _write_gmail_pipeline_inputs(outside_cwd)
    gi_rows = [{"id": f"c{i}", "path": f"consumer-baseline/b{i}.txt", "persona": "cand",
                "corpus_role": "identity_baseline"} for i in (0, 1)]
    gi_rows.append({"id": "p0", "path": "consumer-baseline/b2.txt", "persona": "p0",
                    "corpus_role": "impostor", "impostor_for": ["cand"]})
    for i, text in enumerate(CONSUMER_IMPOSTOR_TEXTS, 1):
        (outside_cwd / f"consumer-imp-{i}.txt").write_text(text, encoding="utf-8")
        gi_rows.append({"id": f"p{i}", "path": f"consumer-imp-{i}.txt", "persona": f"p{i}",
                        "corpus_role": "impostor", "impostor_for": ["cand"]})
    (outside_cwd / "consumer-gi.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in gi_rows), encoding="utf-8")
    # Two POVs of two documents each, from the baseline and impostor texts.
    pov_rows = [{"id": f"pov-{pov}-{i}", "path": path, "pov": pov, "use": ["voice_profile"],
                 "ai_status": "pre_ai_human", "register": "literary_fiction",
                 "privacy": "private", "era": "pre_chatgpt"}
                for pov, paths in (("a", ["consumer-baseline/b0.txt", "consumer-baseline/b1.txt"]),
                                   ("b", ["consumer-imp-1.txt", "consumer-imp-2.txt"]))
                for i, path in enumerate(paths)]
    (outside_cwd / "consumer-pov.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in pov_rows), encoding="utf-8")
    for name, payload in CONSUMER_JSON_INPUTS.items():
        (outside_cwd / name).write_text(json.dumps(payload), encoding="utf-8")
    baseline = outside_cwd / "consumer-baseline"
    baseline.mkdir(exist_ok=True)
    for index, text in enumerate(CONSUMER_BASELINE):
        (baseline / f"b{index}.txt").write_text(text, encoding="utf-8")
    # COLUMNS fixes argparse usage wrapping, which refusal envelopes quote.
    env = {**_clean_env(), "setec_hermetic_backend": "stub", "COLUMNS": "80"}
    for row in CONSUMER_CASES:
        name = f"consumer_cases:{row['case']}"
        argv = [sys.executable, str(bare_root / "scripts" / "setec_run.py"), row["id"],
                *row["args"], "--json"]
        try:
            proc = subprocess.run(argv, cwd=outside_cwd, env=env,
                                  capture_output=True, text=True, timeout=120)
            actual = json.loads(proc.stdout)
            expected = json.loads((CASES_DIR / f"{row['case']}.json").read_text(encoding="utf-8"))
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
            report.add(name, False, str(exc))
            continue
        if os.sep == "\\":
            actual = _posix_strings(actual)
        for dotted in row.get("normalize", []):
            _blank(actual, dotted)
            _blank(expected, dotted)
        ok = proc.returncode == row["exit"] and actual == expected
        report.add(name, ok, "" if ok else
                   f"exit={proc.returncode} (want {row['exit']}); envelope differs from "
                   f"tools/zero_install_cases/{row['case']}.json")


def run(keep_scratch: bool = False) -> tuple[bool, Report]:
    report = Report()
    tmp_root = Path(tempfile.mkdtemp(prefix="setec_zero_install_"))
    try:
        bare_root = make_bare_copy(tmp_root)
        outside_cwd = tmp_root
        check_structural_reachability(bare_root, report)
        check_launcher_classes(bare_root, outside_cwd, report)
        check_setec_run_bare_dispatch(bare_root, outside_cwd, report)
        check_punctuation_conformance(bare_root, outside_cwd, report)
        check_paragraph_conformance(bare_root, outside_cwd, report)
        check_repetition_conformance(bare_root, outside_cwd, report)
        check_narrative_conformance(bare_root, outside_cwd, report)
        check_argument_conformance(bare_root, outside_cwd, report)
        check_argument_pattern_conformance(bare_root, outside_cwd, report)
        check_argument_quality_conformance(bare_root, outside_cwd, report)
        check_argument_consistency_conformance(bare_root, outside_cwd, report)
        check_non_voice_structure_conformance(bare_root, outside_cwd, report)
        check_consumer_cases(bare_root, outside_cwd, report)
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
