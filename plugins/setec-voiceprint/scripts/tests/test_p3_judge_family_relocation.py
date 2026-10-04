"""Judge-library import, legacy pickle and copied-plugin compatibility."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import assert_isolated_probe

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN / "scripts"
MODULES = (
    "agd_move_scan_judge", "argquality_judge", "argument_certainty_judge",
    "argument_judge", "cross_doc_consistency_judge", "fallacy_judge",
    "judge_backends", "narrative_judge", "position_pair_register_judge",
    "warrant_judge",
)


def _probe(code, scripts, cwd, *args):
    assert_isolated_probe(subprocess.run, sys.executable, code, scripts, cwd, *args)


@pytest.mark.parametrize("name", MODULES)
@pytest.mark.parametrize("package_first", (False, True))
def test_import_identity_private_api_and_shared_patches(name, package_first, tmp_path):
    _probe('''
import importlib, sys
sys.path.insert(0, sys.argv[1])
name = sys.argv[2]
paths = [name, 'setec.core.' + name]
if sys.argv[3] == 'True': paths.reverse()
a, b = [importlib.import_module(path) for path in paths]
assert a is b
key = 'assert_judge_generator_disjoint' if name == 'judge_backends' else 'build_judge'
original = getattr(a, key)
sentinel = object()
setattr(a, key, lambda *args, **kwargs: sentinel)
assert getattr(b, key)('synthetic') is sentinel
setattr(b, key, original)
assert getattr(a, key) is original
if name != 'judge_backends':
    assert a._mock_judge is b._mock_judge
assert not any(n in sys.modules for n in ('torch', 'transformers', 'openai', 'anthropic'))
''', SCRIPTS, tmp_path, name, package_first)


@pytest.mark.parametrize("package_first", (False, True))
def test_existing_class_pickle_contract(package_first, tmp_path):
    _probe('''
import importlib, pickle, sys
sys.path.insert(0, sys.argv[1])
prefix = 'setec.core.' if sys.argv[2] == 'True' else ''
names = ('agd_move_scan_judge', 'argquality_judge', 'argument_certainty_judge',
         'argument_judge', 'cross_doc_consistency_judge', 'fallacy_judge',
         'judge_backends', 'narrative_judge', 'position_pair_register_judge',
         'warrant_judge')
mods = {name: importlib.import_module(prefix + name) for name in names}
identity = {'kind': 'manifest', 'model': 'synthetic'}
values = []
for name, mod in mods.items():
    if name == 'judge_backends':
        values.append(mod.JudgeDisjointnessError('synthetic'))
        continue
    values.append(mod.JudgeError('synthetic'))
    if name == 'argument_certainty_judge':
        item = mod.Claim('topic', 'statement', 0, 4, 'text', 'none')
        values.extend([item, mod.JudgeResult([item], identity)])
    elif name == 'cross_doc_consistency_judge':
        item = mod.Commitment('doc', 'topic', 'claim', 'statement', 0, 4, 'text')
        values.extend([item, mod.JudgeResult([item], identity)])
    elif name == 'position_pair_register_judge':
        item = mod.PositionPair('What?', 0, 4, 'text', 5, 9, 'more')
        values.extend([item, mod.JudgeResult([item], identity)])
    elif name == 'agd_move_scan_judge':
        values.append(mod.JudgeResult({'observations': []}, identity, []))
    else:
        values.append(mod.JudgeResult({}, identity))
for value in values:
    cls = type(value)
    legacy_class = pickle.loads(('c' + cls.__module__ + '\\n' + cls.__name__ + '\\n.').encode())
    assert legacy_class is cls
    payload = pickle.dumps(value)
    restored = pickle.loads(payload)
    assert type(restored) is cls
    assert restored.__dict__ == value.__dict__
    assert str(restored) == str(value)
    assert b'setec.core.' not in payload
''', SCRIPTS, tmp_path, package_first)


@pytest.mark.parametrize("mode", ("direct", "runpy", "package"))
def test_copied_plugin_from_foreign_cwd(mode, tmp_path):
    copied = tmp_path / "plugin"
    shutil.copytree(PLUGIN, copied, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    scripts = copied / "scripts"
    for name in MODULES:
        path = scripts / (name + ".py")
        if mode == "direct":
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-B", str(path)], cwd=foreign,
                text=True, capture_output=True, timeout=30,
            )
            assert result.returncode == 0, result.stderr
            assert result.stdout == result.stderr == ""
        elif mode == "runpy":
            _probe("import runpy,sys; old=sys.modules['__main__']; "
                   "runpy.run_path(sys.argv[2],run_name='__main__'); "
                   "assert sys.modules['__main__'] is old", scripts, foreign, path)
        else:
            _probe("import importlib,sys; sys.path.insert(0,sys.argv[1]); "
                   "a=importlib.import_module('setec.core.'+sys.argv[2]); "
                   "assert a is importlib.import_module(sys.argv[2])",
                   scripts, foreign, name)
