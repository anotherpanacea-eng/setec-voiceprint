"""Actual copied-plugin reporting entrypoints retain their existing contracts."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def copied_reporters(tmp_path_factory):
    outside = tmp_path_factory.mktemp("report_renderers")
    plugin = outside / "plugin"
    shutil.copytree(
        Path(__file__).resolve().parents[2], plugin,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    envelope = {
        "schema_version": "1.0", "task_surface": "smoothing_diagnosis",
        "tool": "invented_audit", "version": "1.0", "available": True,
        "target": {"path": "invented.md", "words": 40}, "results": {"fixture": {"observed": 2}},
        "claim_license": {"licenses": "Reports invented fixture evidence",
                          "does_not_license": "No authorship verdict"},
        "warnings": ["Invented warning"],
    }
    good = outside / "envelope.json"
    good.write_text(json.dumps(envelope), encoding="utf-8")
    unavailable = outside / "unavailable.json"
    unavailable.write_text(json.dumps({
        **envelope, "tool": "invented_unavailable", "available": False,
        "results": {}, "warnings": ["Invented unavailable warning"],
    }), encoding="utf-8")
    bad = outside / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    return outside, plugin, envelope, good, unavailable, bad


def _run(copied_reporters, *args, stdin=None):
    outside, *_ = copied_reporters
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-S", *map(str, args)], cwd=outside, env=env,
        input=stdin, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("name", ["evidence_pack", "explain"])
@pytest.mark.parametrize("package_first", [False, True])
def test_copied_imports_share_module_state(copied_reporters, name, package_first):
    _, plugin, *_ = copied_reporters
    result = _run(copied_reporters, "-c", """
import importlib, sys
scripts, name, package_first = sys.argv[1:]
sys.path.insert(0, scripts)
names = [name, 'setec.surfaces.' + name]
if package_first == 'True': names.reverse()
a, b = [importlib.import_module(value) for value in names]
assert a is b
a.SCRIPT_VERSION = 'synthetic sentinel'
assert b.SCRIPT_VERSION == 'synthetic sentinel'
assert not any(value in sys.modules for value in ('torch', 'spacy', 'transformers'))
""", plugin / "scripts", name, package_first)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", ["evidence_pack", "explain"])
@pytest.mark.parametrize("mode", ["direct", "runpy"])
def test_historical_cli_help_without_install(copied_reporters, name, mode):
    _, plugin, *_ = copied_reporters
    script = plugin / "scripts" / (name + ".py")
    args = [script, "--help"] if mode == "direct" else [
        "-c", "import runpy,sys; sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')",
        script, "--help",
    ]
    result = _run(copied_reporters, *args)
    assert result.returncode == 0, result.stderr
    assert "--out" in result.stdout


def test_copied_pack_formats_output_and_invalid_input(copied_reporters):
    outside, plugin, _, good, unavailable, bad = copied_reporters
    script = plugin / "scripts/evidence_pack.py"
    result = _run(copied_reporters, script, good, unavailable, bad)
    assert result.returncode == 0, result.stderr
    for text in ("invented_audit", "invented_unavailable", "_unavailable_",
                 "Reports invented fixture evidence", "No authorship verdict",
                 "Invented unavailable warning", "unreadable JSON"):
        assert text in result.stdout
    output = outside / "pack.html"
    result = _run(copied_reporters, script, good, "--format", "html", "--title", "Fixture <tag>", "--out", output)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    html = output.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert "&lt;tag&gt;" in html and "<tag>" not in html
    assert "Reports invented fixture evidence" in html and "No authorship verdict" in html
    result = _run(copied_reporters, script, bad)
    assert result.returncode == 2 and result.stdout == ""
    assert "No SETEC audit envelopes found" in result.stderr


def test_copied_explain_file_stdin_output_and_invalid_input(copied_reporters):
    outside, plugin, envelope, good, _, bad = copied_reporters
    script = plugin / "scripts/explain.py"
    result = _run(copied_reporters, script, good)
    assert result.returncode == 0, result.stderr
    assert "AI-prose smoothing diagnosis" in result.stdout
    assert "Reports invented fixture evidence" in result.stdout and "No authorship verdict" in result.stdout
    unknown = {**envelope, "task_surface": "unknown_fixture_surface", "available": False,
               "results": {}, "warnings": ["Invented unavailable warning"]}
    result = _run(copied_reporters, script, "-", stdin=json.dumps(unknown))
    assert result.returncode == 0, result.stderr
    assert "unknown_fixture_surface" in result.stdout and "Invented unavailable warning" in result.stdout
    assert "evidence with a stated scope" in result.stdout and "`fixture`" not in result.stdout
    output = outside / "explanation.md"
    result = _run(copied_reporters, script, good, "--out", output)
    assert result.returncode == 0 and result.stdout == ""
    assert "Reports invented fixture evidence" in output.read_text(encoding="utf-8")
    result = _run(copied_reporters, script, bad)
    assert result.returncode == 2 and result.stdout == ""
    assert "Input is not valid JSON" in result.stderr
