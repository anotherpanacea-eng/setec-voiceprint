"""Observable P3 distance compatibility in a copied, zero-install plugin."""

import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def bare_plugin(tmp_path):
    root = tmp_path / "bare-plugin"
    shutil.copytree(PLUGIN_ROOT, root, ignore=shutil.ignore_patterns("__pycache__", "tests"))
    (tmp_path / "foreign-cwd").mkdir()
    return root


def _run(root, *args):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    # Isolation plus no site-packages prevents an installed setec or optional
    # backend from concealing a broken zero-install copy.
    return subprocess.run(
        [sys.executable, "-I", "-S", "-B", *map(str, args)],
        cwd=root.parent / "foreign-cwd",
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )


@pytest.mark.parametrize("package_first", [False, True])
def test_imports_share_one_module_and_monkeypatch_without_optional_dependencies(bare_plugin, package_first):
    # Keep S5's parser/model/network prohibitions active for both import orders,
    # even if production catches a refused import or network attempt.
    code = f"""
import importlib.abc
import socket
import sys

forbidden = {{
    'nltk', 'numpy', 'pandas', 'requests', 'sklearn', 'spacy',
    'torch', 'transformers', 'urllib3', 'huggingface_hub', 'sentence_transformers',
}}
attempts = []

class BlockForbidden(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition('.')[0] in forbidden:
            attempts.append(fullname)
            raise AssertionError('forbidden import: ' + fullname)
        return None

def no_network(*args, **kwargs):
    attempts.append('network')
    raise AssertionError('network access attempted')

sys.meta_path.insert(0, BlockForbidden())
socket.socket = no_network
socket.create_connection = no_network
sys.path.insert(0, {str(bare_plugin / 'scripts')!r})
names = ['stylometry_distance', 'setec.core.stylometry_distance']
if {package_first!r}:
    names.reverse()
first, second = [importlib.import_module(name) for name in names]
assert first is second
original = first.safe_mean
probe = object()
first.safe_mean = lambda values: probe
assert second.safe_mean([]) is probe
second.safe_mean = original
assert first.safe_mean([]) == 0.0
assert not attempts, attempts
assert forbidden.isdisjoint(name.partition('.')[0] for name in sys.modules)
"""
    result = _run(bare_plugin, "-c", code)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result.stderr


def test_direct_file_execution_is_silent(bare_plugin):
    result = _run(bare_plugin, bare_plugin / "scripts" / "stylometry_distance.py")
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result.stderr


def test_detached_runpy_is_silent_and_preserves_main(bare_plugin):
    launcher = bare_plugin / "scripts" / "stylometry_distance.py"
    # No scripts-root insertion: runpy must work from an unrelated CWD using
    # only the copied launcher's own bootstrap.
    code = f"""
import runpy
import sys
main = sys.modules['__main__']
runpy.run_path({str(launcher)!r}, run_name='__main__')
assert sys.modules['__main__'] is main
"""
    result = _run(bare_plugin, "-c", code)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result.stderr


def test_empty_and_degenerate_distances_remain_defined(bare_plugin):
    # Existing S5 tests cover nondegenerate six-family distances and envelopes.
    # These boundary inputs protect the pure API without freezing its inventory.
    code = f"""
import sys
sys.path.insert(0, {str(bare_plugin / 'scripts')!r})
from setec.core import stylometry_distance as distance

empty = distance.family_distance({{}}, [], 'punctuation', [])
assert empty['n_features'] == 0
assert empty['burrows_delta'] == 0.0
assert empty['cosine_distance_to_centroid'] is None
assert empty['cosine_distance_to_baseline_min'] is None

constant = {{'features': {{'punctuation': {{'comma': 1.0}}}}}}
degenerate = distance.family_distance(constant, [constant], 'punctuation', ['comma'])
assert degenerate['burrows_delta'] == 0.0
assert degenerate['top_deviations'][0]['z'] is None
assert degenerate['cosine_distance_to_centroid'] == 0.0
assert distance.cosine_distance({{}}, {{'comma': 1.0}}, ['comma']) is None
"""
    result = _run(bare_plugin, "-c", code)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result.stderr


def test_anchor_ratchet_accepts_real_p3_bootstrap_but_refuses_unrelated_additions(monkeypatch):
    # Load the repo tool through its own bootstrap, without adding an import
    # path in this test. Only the candidate and merge-base inputs are replaced.
    checker = runpy.run_path(str(PLUGIN_ROOT.parents[1] / "tools" / "check_packaging_migration.py"))
    path = "plugins/setec-voiceprint/scripts/stylometry_distance.py"
    bootstrap = next(
        row for row in checker["load_exemptions"]()
        if row["path"] == path and row["symbol"] == "_SCRIPT_DIR"
    )
    anchors = checker["find_anchors_in_file"](PLUGIN_ROOT / "scripts" / "stylometry_distance.py")
    assert checker["check_ghost_rows"]([bootstrap], anchors) == []
    assert checker["check_ghost_rows"]([bootstrap], [])

    ratchet = checker["check_ratchet"]
    candidate = [bootstrap]
    monkeypatch.setitem(ratchet.__globals__, "_exemptions_file_at", lambda base: [])
    monkeypatch.setitem(ratchet.__globals__, "load_exemptions", lambda: candidate)
    assert ratchet("base") == []

    for new_path, symbol in (
        (path, "UNREVIEWED_ANCHOR"),
        ("plugins/setec-voiceprint/scripts/new_audit.py", "_SCRIPT_DIR"),
    ):
        candidate.append(dict(bootstrap, path=new_path, symbol=symbol))
        problems = ratchet("base")
        assert any(new_path in problem and symbol in problem for problem in problems), problems
        candidate.pop()
