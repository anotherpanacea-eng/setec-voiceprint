"""Copied-plugin compatibility for the remaining nested entry points."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def copied_plugin(tmp_path_factory):
    root = tmp_path_factory.mktemp("nested_plugin")
    plugin = root / "plugin"
    shutil.copytree(
        Path(__file__).resolve().parents[2], plugin,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return root, plugin / "scripts"


def _run(copied_plugin, *args):
    root, _ = copied_plugin
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-B", "-S", *map(str, args)],
        cwd=root, env=env, capture_output=True, text=True, timeout=60,
    )


@pytest.mark.parametrize("family,name", [("replication", "train_xgboost")])
@pytest.mark.parametrize("package_first", [False, True])
def test_copied_import_identity(copied_plugin, family, name, package_first):
    _, scripts = copied_plugin
    result = _run(copied_plugin, "-c", """
import importlib, pathlib, sys
scripts, family, name, package_first = sys.argv[1:]
sys.path.insert(0, scripts)
sys.path.insert(0, str(pathlib.Path(scripts) / family))
names = [name, 'setec.' + family + '.' + name]
if package_first == 'True': names.reverse()
a, b = [importlib.import_module(n) for n in names]
assert a is b
assert a.__file__ == str(pathlib.Path(scripts) / 'setec' / family / (name + '.py'))
a.SCRIPT_VERSION = 'shared sentinel'
assert b.SCRIPT_VERSION == 'shared sentinel'
assert not any(n in sys.modules for n in ('spacy', 'torch', 'xgboost'))
""", scripts, family, name, package_first)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("family,name", [("replication", "train_xgboost")])
def test_copied_legacy_cli(copied_plugin, family, name):
    _, scripts = copied_plugin
    result = _run(copied_plugin, scripts / family / (name + ".py"), "--help")
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_copied_train_optional_dependency_and_empty_manifest(copied_plugin):
    root, scripts = copied_plugin
    result = _run(copied_plugin, "-c", """
import sys
sys.path.insert(0, sys.argv[1])
from setec.replication import train_xgboost as train
import manifest_format
assert train.load_jsonl is manifest_format.load_jsonl
assert 'xgboost' not in sys.modules
try:
    train.fit_xgb([], [], [], [], task='binary', hyperparams={}, n_classes=2)
except RuntimeError as exc:
    assert 'xgboost is required' in str(exc)
else:
    raise AssertionError('missing optional dependency was not reported')
assert train.macro_f1([0, 1], [0, 1], n_classes=2) == 1.0
""", scripts)
    assert result.returncode == 0, result.stderr
    manifest = root / "empty.jsonl"
    manifest.write_text("", encoding="utf-8")
    result = _run(
        copied_plugin, scripts / "replication/train_xgboost.py",
        "--feature-manifest", manifest, "--output-dir", root / "train-output",
    )
    assert result.returncode == 2
    assert "manifest yielded 0 usable rows" in result.stderr
