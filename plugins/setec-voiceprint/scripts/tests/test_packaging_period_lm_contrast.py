"""Copied-plugin compatibility for precomputed and cached period contrasts."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def copied_period(tmp_path_factory):
    outside = tmp_path_factory.mktemp("period_contrast")
    plugin = outside / "plugin"
    shutil.copytree(
        Path(__file__).resolve().parents[2], plugin,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    passages = outside / "passages.jsonl"
    passages.write_text("".join(
        json.dumps({"id": str(index), "text": f"Invented lamp passage number {index}"}) + "\n"
        for index in range(4)
    ), encoding="utf-8")
    return outside, plugin, passages


def _run(copied_period, *args):
    outside, _, _ = copied_period
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
def test_copied_alias_patch_and_cached_replay(copied_period, package_first):
    outside, plugin, passages = copied_period
    cache = outside / f"cache-{package_first}.jsonl"
    result = _run(copied_period, "-c", """
import contextlib, importlib, io, json, pickle, sys
scripts, passages, cache, package_first = sys.argv[1:]
sys.path.insert(0, scripts)
names = ['period_lm_contrast', 'setec.surfaces.period_lm_contrast']
if package_first == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
legacy = importlib.import_module('period_lm_contrast')
packaged = importlib.import_module('setec.surfaces.period_lm_contrast')
period, modern = legacy.PAIRS['talkie']
loaded = []
def loader(model_id, **kwargs):
    assert model_id in (period, modern)
    loaded.append(model_id)
    return lambda texts: [2.0 if model_id == period else 3.0] * len(texts)
legacy._load_scorer = loader
args = ['--passages', passages, '--score-cache', cache, '--n-boot', '100', '--json']
first = io.StringIO()
with contextlib.redirect_stdout(first):
    rc = packaged.main(args)
assert rc == 0 and sorted(loaded) == sorted((period, modern))
def forbidden(*args, **kwargs):
    raise AssertionError('A fully cached run must not construct a scorer')
legacy._load_scorer = forbidden
again = io.StringIO()
with contextlib.redirect_stdout(again):
    rc = packaged.main(args)
assert rc == 0
assert json.loads(first.getvalue())['results'] == json.loads(again.getvalue())['results']
assert pickle.loads(b'cperiod_lm_contrast\\nContrastError\\n.') is legacy.ContrastError
assert pickle.loads(b'cperiod_lm_contrast\\nPolicyRefusal\\n.') is legacy.PolicyRefusal
assert not any(name in sys.modules for name in ('torch', 'transformers', 'sentence_transformers'))
""", plugin / "scripts", passages, cache, package_first)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mode", ["direct", "runpy"])
def test_historical_cli_without_install(copied_period, mode):
    _, plugin, _ = copied_period
    script = plugin / "scripts/period_lm_contrast.py"
    args = [script, "--help"] if mode == "direct" else [
        "-c", "import runpy,sys; sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')",
        script, "--help",
    ]
    result = _run(copied_period, *args)
    assert result.returncode == 0, result.stderr
    assert "--passages" in result.stdout and "--score-cache" in result.stdout


def test_copied_cli_precomputed_success_and_refusals(copied_period):
    outside, plugin, passages = copied_period
    precomputed = outside / "precomputed.jsonl"
    precomputed.write_text("".join(
        json.dumps({"id": str(index), "text": f"Invented passage {index}",
                    "period_bpb": 2.0, "modern_bpb": 3.0}) + "\n"
        for index in range(4)
    ), encoding="utf-8")
    script = plugin / "scripts/period_lm_contrast.py"
    result = _run(copied_period, script, "--passages", precomputed, "--n-boot", "100", "--json")
    assert result.returncode == 0, result.stderr
    envelope = json.loads(result.stdout)
    assert envelope["available"] is True and envelope["task_surface"] == "period_register"
    assert envelope["results"]["period_lm_contrast"]["candidates"]["mean_contrast_bpb"] == -1.0
    result = _run(copied_period, script, "--passages", passages, "--json")
    assert result.returncode == 3, result.stderr
    assert json.loads(result.stdout)["reason_category"] == "missing_dependency"
    result = _run(copied_period, script, "--passages", precomputed, "--pair", "typewriter", "--json")
    assert result.returncode == 3, result.stderr
    assert json.loads(result.stdout)["reason_category"] == "bad_input"
