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
