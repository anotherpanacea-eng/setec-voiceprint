"""Copied-plugin generator preserves live contract resources and drift refusal."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def copied_generator(tmp_path_factory):
    outside = tmp_path_factory.mktemp("contract_generator")
    repo = outside / "repo"
    plugin = repo / "plugins/setec-voiceprint"
    original = Path(__file__).resolve().parents[2]
    shutil.copytree(original, plugin, ignore=shutil.ignore_patterns("__pycache__"))
    tools = repo / "tools"
    tools.mkdir()
    for name in ("check_capabilities_drift.py", "seed_capabilities.py", "r1_bundle.py", "_console.py"):
        shutil.copyfile(original.parents[1] / "tools" / name, tools / name)
    return outside, plugin, tools


def _run(bundle, *args, isolated=False):
    outside, *_ = bundle
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-B", *(["-S"] if isolated else []), *map(str, args)],
        cwd=outside, env=env, capture_output=True, timeout=60,
    )


@pytest.mark.parametrize("package_first", [False, True])
def test_cold_registry_pickle_and_main_hooks(copied_generator, package_first):
    _, plugin, _ = copied_generator
    result = _run(copied_generator, "-c", """
import importlib, pickle, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
names = ['gen_contract_fixtures', 'setec.surfaces.gen_contract_fixtures']
if sys.argv[2] == 'True': names.reverse()
a,b = [importlib.import_module(name) for name in names]
assert a is b and a.SURFACE_BUILDERS is b.SURFACE_BUILDERS
assert pickle.loads(b'cgen_contract_fixtures\\nnormalize\\n.') is a.normalize
assert a.SCRIPTS_DIR == Path(sys.argv[1])
assert a.FIXTURES_DIR == Path(sys.argv[1]).parent / 'references/contract_fixtures'
a.SURFACE_BUILDERS['synthetic'] = lambda: {'version':'invented','results':{'value':7}}
assert b.regenerate_surface('synthetic') == {'version':'<fixture>','results':{'value':7}}
a.check_all = lambda: []
assert b.main(['--check']) == 0
assert not any(name in sys.modules for name in ('spacy','torch','transformers','sentence_transformers'))
""", plugin / "scripts", package_first, isolated=True)
    assert result.returncode == 0, result.stderr


def _invocation(script, runpy, *args):
    if runpy:
        return ["-c", "import runpy,sys;p=sys.argv[1];sys.argv=[p,*sys.argv[2:]];runpy.run_path(p,run_name='__main__')", script, *args]
    return [script, *args]


@pytest.mark.parametrize("runpy", [False, True])
def test_actual_help_list_check_write_and_drift(copied_generator, runpy):
    _, plugin, tools = copied_generator
    script = plugin / "scripts/gen_contract_fixtures.py"
    fixtures = plugin / "references/contract_fixtures"
    help_result = _run(copied_generator, *_invocation(script, runpy, "--help"), isolated=True)
    assert help_result.returncode == 0 and b"--write" in help_result.stdout
    listed = _run(copied_generator, *_invocation(script, runpy, "--list"), isolated=True)
    assert listed.returncode == 0 and listed.stderr == b""
    surfaces = listed.stdout.decode().splitlines()
    assert surfaces and surfaces == sorted(set(surfaces))
    # Existing write_text translates LF to the host newline; preserve that behavior.
    expected = {name: (fixtures / (name + ".json")).read_text(encoding="utf-8")
                .replace("\n", os.linesep).encode("utf-8") for name in surfaces}
    for mode in ("--check", "--write", "--check"):
        result = _run(copied_generator, *_invocation(script, runpy, mode))
        assert result.returncode == 0, result.stdout + result.stderr
    assert {name: (fixtures / (name + ".json")).read_bytes() for name in surfaces} == expected
    # Mutate only the disposable plugin and restore even on a failed assertion.
    target = fixtures / "variance_audit.json"
    before = target.read_bytes()
    try:
        value = json.loads(before)
        value["results"]["compression"]["band"] = "MUTATED-FOR-TEST"
        target.write_text(json.dumps(value), encoding="utf-8")
        result = _run(copied_generator, *_invocation(script, runpy, "--check"))
        assert result.returncode == 1 and b"variance_audit:" in result.stdout
        result = _run(copied_generator, tools / "check_capabilities_drift.py", "--json")
        assert result.returncode == 1, result.stdout + result.stderr
        assert result.stdout, result.stderr
        report = json.loads(result.stdout)
        assert any(v["kind"] == "fixture_drift" and v["where"] == "variance_audit"
                   for v in report["violations"])
    finally:
        target.write_bytes(before)
