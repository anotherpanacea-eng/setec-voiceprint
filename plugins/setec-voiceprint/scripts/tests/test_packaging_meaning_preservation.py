"""Copied-plugin identity and lazy-backend contracts for meaning preservation."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def copied_meaning(tmp_path_factory):
    outside = tmp_path_factory.mktemp("meaning_preservation")
    plugin = outside / "plugin"
    shutil.copytree(
        Path(__file__).resolve().parents[2], plugin,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    pairs = outside / "pairs.jsonl"
    pairs.write_text(json.dumps({
        "id": "invented", "source": "The lamp is lit", "rewrite": "The lamp remains lit",
    }) + "\n", encoding="utf-8")
    return outside, plugin, pairs


def _run(copied_meaning, *args):
    outside, _, _ = copied_meaning
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    env["setec_hermetic_backend"] = "stub"
    return subprocess.run(
        [sys.executable, "-B", "-S", *map(str, args)],
        cwd=outside, env=env, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("package_first", [False, True])
def test_copied_alias_loader_patches_reach_real_main(copied_meaning, package_first):
    _, plugin, pairs = copied_meaning
    result = _run(copied_meaning, "-c", """
import contextlib, importlib, io, json, pickle, sys
scripts, pairs, package_first = sys.argv[1:]
sys.path.insert(0, scripts)
names = ['meaning_preservation', 'setec.surfaces.meaning_preservation']
if package_first == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
legacy = importlib.import_module('meaning_preservation')
packaged = importlib.import_module('setec.surfaces.meaning_preservation')
calls = []
def mis(device):
    calls.append('mis')
    return lambda sources, rewrites: [0.75] * len(sources)
def sbert(model, device):
    calls.append('sbert')
    return lambda texts: [[1.0, float(len(text.split()))] for text in texts]
legacy._load_mis = mis
legacy._load_sbert = sbert
output = io.StringIO()
with contextlib.redirect_stdout(output):
    rc = packaged.main(['--pairs', pairs, '--json'])
envelope = json.loads(output.getvalue())
assert rc == 0 and envelope['available'] is True
assert sorted(calls) == ['mis', 'sbert']
channels = envelope['results']['meaning_preservation']['channels']
assert channels['mis']['mean'] == 0.75 and channels['sbert_cosine']['n'] == 1
assert pickle.loads(b'cmeaning_preservation\\nMeaningPreservationError\\n.') is legacy.MeaningPreservationError
assert not any(name in sys.modules for name in ('torch', 'transformers', 'sentence_transformers'))
""", plugin / "scripts", pairs, package_first)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mode", ["direct", "runpy"])
def test_historical_cli_without_install(copied_meaning, mode):
    _, plugin, _ = copied_meaning
    script = plugin / "scripts/meaning_preservation.py"
    args = [script, "--help"] if mode == "direct" else [
        "-c", "import runpy,sys; sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')",
        script, "--help",
    ]
    result = _run(copied_meaning, *args)
    assert result.returncode == 0, result.stderr
    assert "--pairs" in result.stdout and "--channels" in result.stdout


def test_copied_cli_refuses_without_models_and_on_bad_input(copied_meaning):
    outside, plugin, pairs = copied_meaning
    script = plugin / "scripts/meaning_preservation.py"
    result = _run(copied_meaning, script, "--pairs", pairs, "--json")
    assert result.returncode == 3, result.stderr
    assert json.loads(result.stdout)["reason_category"] == "missing_dependency"
    bad = outside / "bad.jsonl"
    bad.write_text('{"source":"invented"}\n', encoding="utf-8")
    result = _run(copied_meaning, script, "--pairs", bad, "--json")
    assert result.returncode == 3, result.stderr
    assert json.loads(result.stdout)["reason_category"] == "bad_input"
