"""Baseline-library import, serialization and copied-plugin compatibility."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN / "scripts"
MODULES = ("concreteness", "argument_register_baselines",
           "register_typical_baselines", "register_taxonomy")


def _probe(code, scripts, cwd, *args):
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code, str(scripts), *map(str, args)],
        cwd=cwd, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("package_first", (False, True))
def test_import_identity_and_shared_patches(name, package_first, tmp_path):
    _probe('''
import importlib, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
name = sys.argv[2]
paths = [name, 'setec.core.' + name]
if sys.argv[3] == 'True': paths.reverse()
a, b = [importlib.import_module(p) for p in paths]
assert a is b
if name == 'concreteness':
    original = a.CONC_SCALE_MAX
    a.CONC_SCALE_MAX = 2
    assert not b.is_valid_rating(3)
    b.CONC_SCALE_MAX = original
    assert a.is_valid_rating(3)
elif name == 'argument_register_baselines':
    original = a.ENV_VAR
    root = Path.cwd()
    (root / a.YAML_NAME).touch()
    os.environ['SYNTHETIC_BASELINES'] = str(root)
    a.ENV_VAR = 'SYNTHETIC_BASELINES'
    assert b.resolve_yaml_path() == root / a.YAML_NAME
    b.ENV_VAR = original
    assert a.ENV_VAR == original
elif name == 'register_typical_baselines':
    original = a.get_baseline_mean
    a.get_baseline_mean = lambda *args, **kwargs: 0.4
    assert b.resolve_baseline('synthetic', 'signal')['value'] == 0.4
    b.get_baseline_mean = original
    assert a.get_baseline_mean is original
else:
    original = a.REGISTER_TO_TIER
    a.REGISTER_TO_TIER = {'synthetic': 'private_dyadic'}
    assert b.resolve_register_tier('synthetic') == 'private_dyadic'
    b.REGISTER_TO_TIER = original
    assert a.resolve_register_tier('message.imessage') == 'private_dyadic'
''', SCRIPTS, tmp_path, name, package_first)


@pytest.mark.parametrize("package_first", (False, True))
def test_existing_class_pickle_contract(package_first, tmp_path):
    _probe('''
import importlib, pickle, sys
sys.path.insert(0, sys.argv[1])
prefix = 'setec.core.' if sys.argv[2] == 'True' else ''
a = importlib.import_module(prefix + 'argument_register_baselines')
r = importlib.import_module(prefix + 'register_typical_baselines')
values = [a.SignalBaseline('s', 0.2, None, 'heuristic', None, True),
          a.RegisterBaseline('synthetic', {}, None, 'synthetic.yaml'),
          a.RegisterBaselineError('synthetic'), r.RegisterTypicalBaselineError('synthetic')]
for value in values:
    restored = pickle.loads(pickle.dumps(value))
    assert type(restored) is type(value)
    assert restored.__dict__ == value.__dict__
    assert str(restored) == str(value)
    assert b'setec.core.' not in pickle.dumps(value)
''', SCRIPTS, tmp_path, package_first)


@pytest.mark.parametrize("mode", ("direct", "runpy", "package"))
def test_copied_plugin_and_stay_put_assets(mode, tmp_path):
    copied = tmp_path / "plugin"
    shutil.copytree(PLUGIN, copied, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    scripts = copied / "scripts"
    for name in MODULES:
        path = scripts / (name + ".py")
        if mode == "direct":
            result = subprocess.run([sys.executable, "-I", "-S", "-B", str(path)],
                                    cwd=foreign, text=True, capture_output=True, timeout=30)
            assert result.returncode == 0, result.stderr
            assert result.stdout == result.stderr == ""
        elif mode == "runpy":
            _probe("import runpy,sys; old=sys.modules['__main__']; "
                   "runpy.run_path(sys.argv[2],run_name='__main__'); "
                   "assert sys.modules['__main__'] is old", scripts, foreign, path)
        else:
            _probe('''
import importlib, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
name = sys.argv[2]
m = importlib.import_module('setec.core.' + name)
assert m is importlib.import_module(name)
root = Path(sys.argv[1]).parent
if name == 'concreteness':
    assert m._DEFAULT_DATA_PATH == root / 'data' / 'brysbaert_concreteness.csv'
elif name == 'register_taxonomy':
    assert m.REGISTRY_PATH == root / 'register_tiers.d'
    assert m.resolve_register_tier('message.imessage') == 'private_dyadic'
else:
    assert m._DEFAULT_YAML_PATH == root.parents[1] / 'baselines' / (
        'argument_register_baselines.yaml' if name == 'argument_register_baselines'
        else 'register_typical.yaml')
''', scripts, foreign, name)
