#!/usr/bin/env python3
"""Unit tests for tools/check_zero_install.py.

The gate's own full run (`python3 tools/check_zero_install.py`) copies
the whole plugin tree and runs several real subprocesses — a
legitimate but slower end-to-end CI gate, run as its own step exactly
like check_capabilities_drift.py / check_claim_license_guard.py. This
file pins the gate's classification logic directly:

  * `check_setec_run_bare_dispatch` classifies the documented known
    gap (internal_error + "can't open file") as a PASS.
  * It classifies an outright success (available: true) as a PASS too
    — the gap closing shouldn't fail the gate.
  * It classifies any OTHER failure shape (wrong reason_category, a
    crash, a timeout) as a FAIL — the gate must not silently absorb a
    drifted failure mode.
  * `make_bare_copy` produces a BARE `<tmp>/setec-voiceprint` with no
    `plugins/` parent — the exact shape the removed hermetic gate got
    wrong (build-review P1 finding #2).
"""

from __future__ import annotations

import json
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


def test_make_bare_copy_has_no_plugins_wrapper(tmp_path):
    bare_root = zi.make_bare_copy(tmp_path)
    assert bare_root == tmp_path / "setec-voiceprint"
    assert bare_root.is_dir()
    assert not (tmp_path / "plugins").exists()
    assert (bare_root / "scripts" / "setec_run.py").is_file()
    assert (bare_root / ".claude-plugin" / "plugin.json").is_file()


def _fake_proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["x"], returncode, stdout=stdout, stderr=stderr)


def test_documented_known_gap_is_a_pass(tmp_path, monkeypatch):
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
    assert result.passed
    assert "documented known gap" in result.detail


def test_gap_closed_success_is_also_a_pass(tmp_path):
    envelope = {"available": True, "reason_category": None}
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
    assert "gap appears closed" in result.detail


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
    assert "drifted" in result.detail
