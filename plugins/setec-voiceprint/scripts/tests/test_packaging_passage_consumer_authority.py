"""Copied Consumer Authority retains aliases and closed CLI behavior."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def copied_authority(tmp_path_factory):
    outside = tmp_path_factory.mktemp("consumer_authority")
    plugin = outside / "plugin"
    shutil.copytree(Path(__file__).resolve().parents[2], plugin,
                    ignore=shutil.ignore_patterns("__pycache__"))
    return outside, plugin


def _run(copied_authority, *args):
    outside, _ = copied_authority
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-S", *map(str, args)], cwd=outside, env=env,
        capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("package_first", [False, True])
def test_cold_identity_pickle_and_closed_main_hook(copied_authority, package_first):
    _, plugin = copied_authority
    result = _run(copied_authority, "-c", """
import importlib, pickle, sys
scripts, package_first = sys.argv[1:]
sys.path.insert(0, scripts)
names = ['passage_consumer_authority', 'setec.surfaces.passage_consumer_authority']
if package_first == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
for name in ('CompileTimeBindings', 'AdjacentAuthority', 'ValidateAdmission', 'AuthorityError'):
    assert pickle.loads(('cpassage_consumer_authority\\n' + name + '\\n.').encode()) is getattr(a, name)
errors = []
a._write_error = lambda code: errors.append(code)
args = ['--mode', 'mint', '--private-root', 'unused', '--descriptor', 'descriptor.json',
        '--checkpoint', 'checkpoint', '--projected-manifest', 'projected.jsonl',
        '--artifact', 'artifact.json', '--receipt', 'receipt.json']
assert b.main(args) == 3 and errors == ['policy_unresolved_refused']
assert not any(name in sys.modules for name in ('spacy', 'torch', 'transformers', 'requests'))
""", plugin / "scripts", package_first)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("runpy", [False, True])
def test_historical_help_outside_repository(copied_authority, runpy):
    _, plugin = copied_authority
    script = plugin / "scripts/passage_consumer_authority.py"
    args = [script, "--help"]
    if runpy:
        args = ["-c", "import runpy,sys;p=sys.argv[1];sys.argv=[p,'--help'];runpy.run_path(p,run_name='__main__')", script]
    result = _run(copied_authority, *args)
    assert result.returncode == 0, result.stderr
    assert "--mode" in result.stdout and "--private-root" in result.stdout


def test_actual_mint_and_usage_refusals_without_private_access(copied_authority):
    outside, plugin = copied_authority
    private = outside / "never-opened"
    script = plugin / "scripts/passage_consumer_authority.py"
    args = ["--mode", "mint", "--private-root", private, "--descriptor", "descriptor.json",
            "--checkpoint", "checkpoint", "--projected-manifest", "projected.jsonl",
            "--artifact", "artifact.json", "--receipt", "receipt.json", "--json"]
    result = _run(copied_authority, script, *args)
    assert result.returncode == 3 and result.stdout == ""
    assert json.loads(result.stderr)["code"] == "policy_unresolved_refused"
    result = _run(copied_authority, script, "--unknown")
    assert result.returncode == 2 and result.stdout == "" and "--mode" in result.stderr
    assert not private.exists()
