"""Copied-plugin compatibility for the descriptive phrase profile."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def copied_phrase(tmp_path_factory):
    outside = tmp_path_factory.mktemp("phrase_profile")
    plugin = outside / "plugin"
    shutil.copytree(
        Path(__file__).resolve().parents[2], plugin,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return outside, plugin


def _run(copied_phrase, *args):
    outside, _ = copied_phrase
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-S", *map(str, args)],
        cwd=outside, env=env, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("package_first", [False, True])
def test_copied_import_and_exception_identity(copied_phrase, package_first):
    _, plugin = copied_phrase
    result = _run(copied_phrase, "-c", """
import importlib, pickle, sys
scripts, package_first = sys.argv[1:]
sys.path.insert(0, scripts)
names = ['phrase_overuse_profile', 'setec.surfaces.phrase_overuse_profile']
if package_first == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
a.SCRIPT_VERSION = 'synthetic sentinel'
assert b.SCRIPT_VERSION == 'synthetic sentinel'
assert pickle.loads(b'cphrase_overuse_profile\\nPhraseProfileError\\n.') is a.PhraseProfileError
assert not any(name in sys.modules for name in ('spacy', 'torch', 'transformers', 'sklearn'))
""", plugin / "scripts", package_first)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mode", ["direct", "runpy"])
def test_historical_cli_without_install(copied_phrase, mode):
    _, plugin = copied_phrase
    script = plugin / "scripts/phrase_overuse_profile.py"
    args = [script, "--help"] if mode == "direct" else [
        "-c", "import runpy,sys; sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')",
        script, "--help",
    ]
    result = _run(copied_phrase, *args)
    assert result.returncode == 0, result.stderr
    assert "--target" in result.stdout and "--reference" in result.stdout


def test_copied_cli_preserves_success_abstention_and_bad_input(copied_phrase):
    outside, plugin = copied_phrase
    target, reference = outside / "target", outside / "reference"
    target.mkdir()
    reference.mkdir()
    for index in (1, 2):
        (target / f"{index}.txt").write_text(
            "Amber ribbon turns. Amber ribbon returns. Amber ribbon glows.", encoding="utf-8",
        )
        (reference / f"{index}.txt").write_text(
            "River stones gather. Lantern windows open. Winter meadows shine.", encoding="utf-8",
        )
    script = plugin / "scripts/phrase_overuse_profile.py"
    args = [script, "--target", target, "--reference", reference, "--json"]
    result = _run(copied_phrase, *args, "--min-reference-tokens", "1")
    assert result.returncode == 0, result.stderr
    envelope = json.loads(result.stdout)
    assert envelope["available"] is True and envelope["task_surface"] == "formulaicity"
    assert "amber ribbon" in {
        row["ngram"] for row in envelope["results"]["phrase_overuse"]["over_represented"]
    }
    result = _run(copied_phrase, *args)
    assert result.returncode == 3, result.stderr
    assert json.loads(result.stdout)["reason_category"] == "text_too_short"
    bad = outside / "bad.jsonl"
    bad.write_text('{"body": "invented"}\n', encoding="utf-8")
    result = _run(copied_phrase, script, "--target", bad, "--reference", reference, "--json")
    assert result.returncode == 3, result.stderr
    assert json.loads(result.stdout)["reason_category"] == "bad_input"
