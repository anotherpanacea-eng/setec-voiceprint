"""Copied-plugin contracts for the remaining pure core libraries."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


LIBRARIES = (
    "diveye_signals",
    "passage_source_population_commitment",
    "passage_lineage_crosswalk",
)


@pytest.fixture(scope="module")
def copied_plugin(tmp_path_factory):
    outside = tmp_path_factory.mktemp("core_libraries")
    plugin = outside / "plugin"
    shutil.copytree(
        Path(__file__).resolve().parents[2], plugin,
        ignore=shutil.ignore_patterns("__pycache__", "tests"),
    )
    return outside, plugin / "scripts"


def _run(copied_plugin, *args):
    outside, _ = copied_plugin
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, "-B", "-S", *map(str, args)],
        cwd=outside, env=env, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("name", LIBRARIES)
@pytest.mark.parametrize("package_first", [False, True])
def test_copied_imports_share_module_and_legacy_pickle(copied_plugin, name, package_first):
    _, scripts = copied_plugin
    result = _run(copied_plugin, "-c", """
import importlib, pickle, sys
scripts, name, package_first = sys.argv[1:]
sys.path.insert(0, scripts)
names = [name, 'setec.core.' + name]
if package_first == 'True': names.reverse()
legacy, packaged = [importlib.import_module(value) for value in names]
assert legacy is packaged
sentinel = object()
legacy._packaging_test_sentinel = sentinel
assert packaged._packaging_test_sentinel is sentinel
if name == 'diveye_signals':
    assert legacy.TASK_SURFACE is None
if name == 'passage_lineage_crosswalk':
    # A historical protocol-0 GLOBAL resolves through the permanent old name.
    assert pickle.loads(b'cpassage_lineage_crosswalk\\nLineageProjection\\n.') is legacy.LineageProjection
assert not any(value in sys.modules for value in ('torch', 'transformers', 'spacy', 'sklearn', 'xgboost'))
""", scripts, name, package_first)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", LIBRARIES)
@pytest.mark.parametrize("mode", ["direct", "runpy"])
def test_no_main_libraries_stay_silent_without_install(copied_plugin, name, mode):
    _, scripts = copied_plugin
    path = scripts / (name + ".py")
    args = [path] if mode == "direct" else [
        "-c", "import runpy,sys; runpy.run_path(sys.argv[1], run_name='__main__')", path,
    ]
    result = _run(copied_plugin, *args)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
