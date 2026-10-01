"""Behavioral compatibility of the P2 L0 move in a zero-install plugin copy."""

import json
import os
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
    return root


def _run(root, *args):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([sys.executable, "-B", *map(str, args)], cwd=root.parent,
                          env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)


@pytest.mark.parametrize("name", ["claim_license", "output_schema", "capabilities"])
@pytest.mark.parametrize("package_first", [False, True])
def test_legacy_import_is_the_same_module_and_monkeypatch_visible(bare_plugin, name, package_first):
    # A fresh process for each order: no previously imported module can conceal
    # a duplicate object or an import cycle in the copied plugin.
    code = f"""
import importlib, sys
sys.path.insert(0, {str(bare_plugin / 'scripts')!r})
names = [{name!r}, {'setec.contract.' + name!r}]
if {package_first!r}: names.reverse()
first, second = [importlib.import_module(n) for n in names]
assert first is second
first.relocation_probe = object()
assert second.relocation_probe is first.relocation_probe
assert first.__name__ == {'setec.contract.' + name!r}
"""
    result = _run(bare_plugin, "-c", code)
    assert result.returncode == 0, result.stderr


def test_stay_put_paths_and_nonempty_registries(bare_plugin):
    code = f"""
import json, sys
from pathlib import Path
sys.path.insert(0, {str(bare_plugin / 'scripts')!r})
import capabilities, claim_license, output_schema
root = Path({str(bare_plugin)!r})
assert capabilities.PLUGIN_ROOT == root
assert capabilities.MANIFEST_PATH == root / 'capabilities.d'
assert capabilities.PLUGIN_JSON_PATH == root / '.claude-plugin/plugin.json'
assert claim_license._SURFACE_LABEL_DIR == root / 'scripts/claim_license_surfaces'
assert capabilities.entries(capabilities.load_manifest())
assert claim_license.TASK_SURFACE_LABELS
assert output_schema.VALID_TASK_SURFACES == frozenset(claim_license.TASK_SURFACE_LABELS)
assert capabilities.setec_version() == json.loads((root / '.claude-plugin/plugin.json').read_text())['version']
"""
    result = _run(bare_plugin, "-c", code)
    assert result.returncode == 0, result.stderr


def test_missing_label_directory_preserves_import_and_unknown_surface_refusal(bare_plugin):
    shutil.rmtree(bare_plugin / "scripts" / "claim_license_surfaces")
    code = f"""
import sys
sys.path.insert(0, {str(bare_plugin / 'scripts')!r})
import claim_license, output_schema
assert claim_license.TASK_SURFACE_LABELS == {{}}
assert output_schema.VALID_TASK_SURFACES == frozenset()
assert claim_license.ClaimLicense('unregistered', 'reports', 'does not report').render_block()
try:
    output_schema.build_output(task_surface='unregistered', tool='probe', version='1',
        target_path=None, target_words=0, baseline=None, results={{}}, claim_license=None, available=False)
except ValueError as exc:
    assert 'Unknown task_surface' in str(exc)
else:
    raise AssertionError('unknown surface accepted')
"""
    result = _run(bare_plugin, "-c", code)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", ["claim_license", "output_schema"])
def test_no_main_launchers_execute_silently_and_runpy_does_not_replace_main(bare_plugin, name):
    launcher = bare_plugin / "scripts" / (name + ".py")
    direct = _run(bare_plugin, launcher)
    assert (direct.returncode, direct.stdout, direct.stderr) == (0, "", "")
    code = f"""
import runpy, sys
main = sys.modules['__main__']
runpy.run_path({str(launcher)!r}, run_name='__main__')
assert sys.modules['__main__'] is main
"""
    result = _run(bare_plugin, "-c", code)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("command", [["list", "--format", "json"], ["emit", "--json"]])
def test_capabilities_direct_execution_and_runpy_match_package_api(bare_plugin, command):
    launcher = bare_plugin / "scripts" / "capabilities.py"
    direct = _run(bare_plugin, launcher, *command)
    assert direct.returncode == 0, direct.stderr
    code = f"""
import runpy, sys
main = sys.modules['__main__']
sys.argv = [{str(launcher)!r}, *{command!r}]
try:
    runpy.run_path({str(launcher)!r}, run_name='__main__')
except SystemExit as exc:
    assert exc.code == 0
assert sys.modules['__main__'] is main
"""
    via_runpy = _run(bare_plugin, "-c", code)
    assert via_runpy.returncode == 0, via_runpy.stderr
    assert json.loads(via_runpy.stdout) == json.loads(direct.stdout)
    if command[0] == "emit":
        api = "capabilities.build_emit_envelope(capabilities.load_manifest())"
    else:
        api = "capabilities.filter_entries(capabilities.entries(capabilities.load_manifest()))"
    code = f"""
import json, sys
sys.path.insert(0, {str(bare_plugin / 'scripts')!r})
from setec.contract import capabilities
print(json.dumps({api}))
"""
    package = _run(bare_plugin, "-c", code)
    assert package.returncode == 0, package.stderr
    assert json.loads(package.stdout) == json.loads(direct.stdout)
