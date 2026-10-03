#!/usr/bin/env python3
"""Unit tests for tools/check_zero_install.py.

The gate's own full run (`python3 tools/check_zero_install.py`) copies
the whole plugin tree and runs several real subprocesses — a
legitimate but slower end-to-end CI gate, run as its own step exactly
like check_capabilities_drift.py / check_packaging_migration.py. This
file pins the gate's classification logic directly:

  * `check_setec_run_bare_dispatch` requires an exact successful envelope.
  * Any failure, unrelated available envelope, crash, or timeout fails.
  * `make_bare_copy` produces a BARE `<tmp>/setec-voiceprint` with no
    `plugins/` parent — the exact shape the removed hermetic gate got
    wrong (build-review P1 finding #2).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import check_zero_install as zi  # type: ignore  # noqa: E402


def test_paraphrase_ladder_help_is_cp1252_safe():
    script = (
        REPO_ROOT / "plugins" / "setec-voiceprint" / "scripts"
        / "calibration" / "paraphrase_ladder.py"
    )
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode("cp1252", errors="replace")
    assert b"--stability-threshold" in completed.stdout
    assert b"--fragile-threshold" in completed.stdout


def test_make_bare_copy_has_no_plugins_wrapper(tmp_path):
    bare_root = zi.make_bare_copy(tmp_path)
    assert bare_root == tmp_path / "setec-voiceprint"
    assert bare_root.is_dir()
    assert not (tmp_path / "plugins").exists()
    assert (bare_root / "scripts" / "setec_run.py").is_file()
    assert (bare_root / ".claude-plugin" / "plugin.json").is_file()
    report = zi.Report()
    zi.check_punctuation_conformance(bare_root, tmp_path, report)
    zi.check_paragraph_conformance(bare_root, tmp_path, report)
    zi.check_repetition_conformance(bare_root, tmp_path, report)
    zi.check_narrative_conformance(bare_root, tmp_path, report)
    assert report.passed, [(r.name, r.detail) for r in report.results if not r.passed]


def test_make_bare_copy_refuses_source_symlinks(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("secret = True\n", encoding="utf-8")
    try:
        (source / "laundered.py").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    monkeypatch.setattr(zi, "PLUGIN_ROOT", source)

    with pytest.raises(zi.GateError, match="contains symlink"):
        zi.make_bare_copy(tmp_path / "copy")


def _fake_proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["x"], returncode, stdout=stdout, stderr=stderr)


def test_dispatch_failure_is_not_a_pass(tmp_path, monkeypatch):
    envelope = {
        "available": False,
        "reason_category": "internal_error",
        "reason": "variance_audit: the dispatcher could not launch the resolved script (exit 2): python: can't open file 'x': [Errno 2] No such file or directory",
    }
    with mock.patch.object(
        zi.subprocess, "run",
        return_value=_fake_proc(1, stdout=json.dumps(envelope)),
    ):
        report = zi.Report()
        (tmp_path / "scripts" / "test_data").mkdir(parents=True)
        (tmp_path / "scripts" / "test_data" / "human_sample.txt").write_text("x", encoding="utf-8")
        (tmp_path / "scripts" / "setec_run.py").write_text("", encoding="utf-8")
        zi.check_setec_run_bare_dispatch(tmp_path, tmp_path, report)
    result = report.results[0]
    assert not result.passed


def test_gap_closed_success_is_also_a_pass(tmp_path):
    envelope = {
        "schema_version": "1.0",
        "task_surface": "smoothing_diagnosis",
        "tool": "variance_audit",
        "available": True,
    }
    with mock.patch.object(
        zi.subprocess, "run",
        return_value=_fake_proc(0, stdout=json.dumps(envelope)),
    ):
        report = zi.Report()
        (tmp_path / "scripts" / "test_data").mkdir(parents=True)
        (tmp_path / "scripts" / "test_data" / "human_sample.txt").write_text("x", encoding="utf-8")
        (tmp_path / "scripts" / "setec_run.py").write_text("", encoding="utf-8")
        zi.check_setec_run_bare_dispatch(tmp_path, tmp_path, report)
    result = report.results[0]
    assert result.passed
    assert "successfully" in result.detail


def test_unrelated_available_envelope_fails(tmp_path):
    envelope = {
        "schema_version": "1.0",
        "task_surface": "wrong_surface",
        "tool": "unrelated",
        "available": True,
    }
    with mock.patch.object(
        zi.subprocess, "run",
        return_value=_fake_proc(0, stdout=json.dumps(envelope)),
    ):
        report = zi.Report()
        (tmp_path / "scripts" / "test_data").mkdir(parents=True)
        (tmp_path / "scripts" / "test_data" / "human_sample.txt").write_text("x", encoding="utf-8")
        (tmp_path / "scripts" / "setec_run.py").write_text("", encoding="utf-8")
        zi.check_setec_run_bare_dispatch(tmp_path, tmp_path, report)
    assert not report.results[0].passed


def test_structural_reachability_rejects_parent_escape(tmp_path):
    bare = tmp_path / "setec-voiceprint"
    manifest_dir = bare / "capabilities.d"
    manifest_dir.mkdir(parents=True)
    (tmp_path / "escape.py").write_text("pass\n", encoding="utf-8")
    (manifest_dir / "escape.yaml").write_text(
        "entries:\n  - id: escape\n"
        "    script_path: plugins/setec-voiceprint/../escape.py\n",
        encoding="utf-8",
    )
    report = zi.Report()
    zi.check_structural_reachability(bare, report)
    assert not report.results[0].passed


@pytest.mark.parametrize("failure", ["missing", "cyclic", "escape", "mismatch"])
def test_structural_reachability_checks_implementation(tmp_path, failure):
    bare = tmp_path / "setec-voiceprint"
    manifest_dir = bare / "capabilities.d"
    scripts = bare / "scripts"
    manifest_dir.mkdir(parents=True)
    scripts.mkdir()
    source = {"missing": "from absent import TASK_SURFACE\n",
              "cyclic": "from old import TASK_SURFACE\n",
              "escape": "from ..outside import TASK_SURFACE\n",
              "mismatch": 'TASK_SURFACE = "wrong"\n'}[failure]
    (scripts / "old.py").write_text(source, encoding="utf-8")
    (manifest_dir / "old.yaml").write_text(
        "entries:\n  - id: old\n    surface: setup\n"
        "    script_path: plugins/setec-voiceprint/scripts/old.py\n", encoding="utf-8",
    )
    report = zi.Report()
    zi.check_structural_reachability(bare, report)
    assert not report.passed


def test_punctuation_success_with_wrong_surface_is_refused(tmp_path):
    wrong = {"schema_version": "1.0", "tool": "punctuation_cadence_audit",
             "task_surface": "setup", "available": True}
    with mock.patch.object(zi.subprocess, "run", return_value=_fake_proc(0, json.dumps(wrong))):
        report = zi.Report()
        zi.check_punctuation_conformance(tmp_path, tmp_path, report)
    assert not report.passed
    assert all(not r.passed for r in report.results if r.name != "punctuation:identity")


def test_paragraph_success_with_wrong_surface_is_refused(tmp_path):
    wrong = {"schema_version": "1.0", "tool": "paragraph_audit",
             "task_surface": "setup", "available": True, "results": {"n_paragraphs": 2}}
    with mock.patch.object(zi.subprocess, "run", return_value=_fake_proc(0, json.dumps(wrong))):
        report = zi.Report()
        zi.check_paragraph_conformance(tmp_path, tmp_path, report)
    assert all(not r.passed for r in report.results if r.name in {
        "paragraph:direct", "paragraph:runpy", "paragraph:module",
    })


@pytest.mark.parametrize("returncode,changes", [
    (0, {}),  # refusal must keep the native exit code, not merely unavailable JSON
    (2, {"available": True}),
    (2, {"reason_category": "internal_error"}),
    (2, {"reason": "could not open a launcher"}),
    (2, {"surface": "punctuation_cadence_audit"}),
])
def test_paragraph_dispatch_cannot_mask_a_changed_refusal(tmp_path, returncode, changes):
    envelope = {"schema_version": "1.0", "tool": "setec_run", "task_surface": None,
                "surface": "paragraph_audit", "available": False, "reason_category": "bad_input",
                "reason": "unknown surface 'paragraph_audit'; known consumer surfaces: variance_audit"}
    envelope.update(changes)
    with mock.patch.object(zi.subprocess, "run", return_value=_fake_proc(returncode, json.dumps(envelope))):
        report = zi.Report()
        zi.check_paragraph_conformance(tmp_path, tmp_path, report)
    result = next(r for r in report.results if r.name == "paragraph:dispatch_refusal")
    assert not result.passed


def test_paragraph_input_refusals_cannot_pass_as_json_success(tmp_path):
    success = {"schema_version": "1.0", "tool": "paragraph_audit",
               "task_surface": "smoothing_diagnosis", "available": True,
               "results": {"n_paragraphs": 2}}
    with mock.patch.object(zi.subprocess, "run", return_value=_fake_proc(0, json.dumps(success))):
        report = zi.Report()
        zi.check_paragraph_conformance(tmp_path, tmp_path, report)
    assert all(not r.passed for r in report.results if r.name in {
        "paragraph:missing_input", "paragraph:missing_baseline",
    })


@pytest.mark.parametrize("case,source", [
    ("renamed", "from impl import TASK_SURFACE as OTHER\n"),
    ("deleted", "from impl import TASK_SURFACE\ndel TASK_SURFACE\n"),
    ("rebound", "from impl import TASK_SURFACE\nTASK_SURFACE = None\n"),
    ("annotated", "from impl import TASK_SURFACE\nTASK_SURFACE: str | None = None\n"),
    ("unpacked", "from impl import TASK_SURFACE\nTASK_SURFACE, other = None, 1\n"),
    ("augmented", "from impl import TASK_SURFACE\nTASK_SURFACE += '_wrong'\n"),
    ("conditional", "from impl import TASK_SURFACE\nif True:\n    del TASK_SURFACE\n"),
    ("type_only", "from impl import TASK_SURFACE\nTASK_SURFACE: str | None\n"),
    ("function_local", "from impl import TASK_SURFACE\ndef helper():\n    TASK_SURFACE = None\n    del TASK_SURFACE\n"),
    ("class_local", "from impl import TASK_SURFACE\nclass Helper:\n    TASK_SURFACE = None\n    del TASK_SURFACE\n"),
    ("valid", "from impl import TASK_SURFACE\n"),
])
def test_reachability_requires_exported_alias_surface(tmp_path, case, source):
    scripts = tmp_path / "scripts"
    manifests = tmp_path / "capabilities.d"
    scripts.mkdir()
    manifests.mkdir()
    (scripts / "impl.py").write_text('TASK_SURFACE = "setup"\n', encoding="utf-8")
    (scripts / "old.py").write_text(source, encoding="utf-8")
    (manifests / "old.yaml").write_text(
        "entries:\n  - id: old\n    surface: setup\n"
        "    script_path: plugins/setec-voiceprint/scripts/old.py\n", encoding="utf-8",
    )
    report = zi.Report()
    zi.check_structural_reachability(tmp_path, report)
    assert report.passed is (case in {"valid", "type_only", "function_local", "class_local"})


@pytest.mark.parametrize("stem", [
    "repetition_audit", "manuscript_repetition_audit", "chapter_distinctiveness_audit",
])
def test_repetition_conformance_rejects_wrong_surface(tmp_path, stem):
    wrong = {"schema_version": "1.0", "tool": stem, "task_surface": "setup",
             "available": True, "results": {"n_chapters": 2, "candidates": [{"word": "copper", "count": 3}]}}
    report = zi.Report()
    with mock.patch.object(zi.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(wrong), "")):
        zi.check_repetition_conformance(tmp_path, tmp_path, report)
    assert not any(r.passed for r in report.results if r.name.startswith(stem + ":"))


@pytest.mark.parametrize("stem", ["manuscript_repetition_audit", "chapter_distinctiveness_audit"])
@pytest.mark.parametrize("change", ["promoted", "wrong_reason", "wrong_surface", "wrong_exit"])
def test_repetition_todo_dispatch_refusals_stay_truthful(tmp_path, stem, change):
    envelope = {"schema_version": "1.0", "tool": "setec_run", "task_surface": None,
                "available": False, "surface": stem, "reason_category": "bad_input",
                "reason": f"unknown surface '{stem}'"}
    code = 2
    if change == "promoted":
        envelope.update(available=True, tool=stem, task_surface="smoothing_diagnosis")
        code = 0
    elif change == "wrong_reason":
        envelope["reason"] = "could not open launcher"
    elif change == "wrong_surface":
        envelope["surface"] = "other"
    else:
        code = 0
    report = zi.Report()
    with mock.patch.object(zi.subprocess, "run", return_value=subprocess.CompletedProcess([], code, json.dumps(envelope), "")):
        zi.check_repetition_conformance(tmp_path, tmp_path, report)
    result = next(r for r in report.results if r.name == stem + ":dispatch")
    assert result.passed is False


@pytest.mark.parametrize("change", ["promoted", "wrong_reason", "wrong_surface", "wrong_exit"])
def test_narrative_experimental_dispatch_refusal_stays_truthful(tmp_path, change):
    stem = "narrative_decision_long_form"
    envelope = {"schema_version": "1.0", "tool": "setec_run", "task_surface": None,
                "available": False, "surface": stem, "reason_category": "bad_input",
                "reason": f"unknown surface '{stem}'"}
    code = 2
    if change == "promoted":
        envelope.update(available=True, tool=stem, task_surface=stem)
        code = 0
    elif change == "wrong_reason":
        envelope["reason"] = "could not open launcher"
    elif change == "wrong_surface":
        envelope["surface"] = "other"
    else:
        code = 0
    report = zi.Report()
    with mock.patch.object(zi.subprocess, "run", return_value=_fake_proc(code, json.dumps(envelope))):
        zi.check_narrative_conformance(tmp_path, tmp_path, report)
    assert not next(r for r in report.results if r.name == stem + ":dispatch").passed


@pytest.mark.parametrize("change", ["wrong_surface", "wrong_exit", "unavailable", "missing_license"])
def test_narrative_base_dispatch_requires_its_real_success(tmp_path, change):
    stem = "narrative_decision_audit"
    envelope = {"schema_version": "1.0", "tool": stem, "task_surface": stem,
                "available": True, "claim_license": {},
                "results": {"judge": {"judge_identity": {"kind": "mock"}}}}
    code = 0
    if change == "wrong_surface":
        envelope["task_surface"] = "other"
    elif change == "wrong_exit":
        code = 1
    elif change == "unavailable":
        envelope["available"] = False
    else:
        del envelope["claim_license"]
    for suffix in ("json", "md"):
        (tmp_path / ("narrative-input.txt.narrative." + suffix)).write_text("synthetic", encoding="utf-8")
    report = zi.Report()
    with mock.patch.object(zi.subprocess, "run", return_value=_fake_proc(code, json.dumps(envelope))):
        zi.check_narrative_conformance(tmp_path, tmp_path, report)
    assert not next(r for r in report.results if r.name == stem + ":dispatch").passed


def test_reachability_checks_every_entry_in_fragment(tmp_path):
    manifest_dir = tmp_path / "capabilities.d"
    scripts = tmp_path / "scripts"
    manifest_dir.mkdir()
    scripts.mkdir()
    (scripts / "ordinary.py").write_text('TASK_SURFACE = "setup"\n', encoding="utf-8")
    (manifest_dir / "two.yaml").write_text(
        "entries:\n  - id: first\n    surface: setup\n"
        "    script_path: plugins/setec-voiceprint/scripts/ordinary.py\n"
        "  - id: second\n    surface: setup\n"
        "    script_path: plugins/setec-voiceprint/scripts/missing.py\n", encoding="utf-8",
    )
    report = zi.Report()
    zi.check_structural_reachability(tmp_path, report)
    assert not report.passed and "second" in report.results[0].detail


@pytest.mark.parametrize("envelope,stdout_override", [
    ({"available": False, "reason_category": "policy_refused", "reason": "can't open file 'x'"}, None),
    ({"available": False, "reason_category": "internal_error", "reason": "some other failure"}, None),
    (None, "not json at all"),
])
def test_drifted_failure_shape_is_a_fail(tmp_path, envelope, stdout_override):
    stdout = stdout_override if stdout_override is not None else json.dumps(envelope)
    with mock.patch.object(
        zi.subprocess, "run",
        return_value=_fake_proc(1, stdout=stdout),
    ):
        report = zi.Report()
        (tmp_path / "scripts" / "test_data").mkdir(parents=True)
        (tmp_path / "scripts" / "test_data" / "human_sample.txt").write_text("x", encoding="utf-8")
        (tmp_path / "scripts" / "setec_run.py").write_text("", encoding="utf-8")
        zi.check_setec_run_bare_dispatch(tmp_path, tmp_path, report)
    result = report.results[0]
    assert not result.passed
    assert "exact envelope check" in result.detail
