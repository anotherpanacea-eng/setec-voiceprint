"""Behavioral controls for environment isolation and opaque probe results."""
import importlib
from subprocess import TimeoutExpired
from types import SimpleNamespace

import pytest

FAMILIES = ['consistency', 'pattern', 'quality']


@pytest.mark.parametrize('family', FAMILIES)
@pytest.mark.parametrize('returncode', [0, 3])
def test_probe_preserves_environment_result_and_live_lookup(monkeypatch, tmp_path, family, returncode):
    module = importlib.import_module(f'test_packaging_argument_{family}_family')
    original = {'PYTHONPATH': 'synthetic unused', 'OTHER': 'keep',
                'PYTHONDONTWRITEBYTECODE': '0', 'PYTHONUTF8': '0'}
    snapshot = dict(original)
    calls = []
    result = SimpleNamespace(returncode=returncode, stdout='synthetic output', stderr='synthetic diagnostic')

    def run(argv, **options):
        calls.append((argv, options))
        return result

    monkeypatch.setattr(module, 'os', SimpleNamespace(environ=original))
    monkeypatch.setattr(module, 'subprocess', SimpleNamespace(run=run))
    monkeypatch.setattr(module, 'sys', SimpleNamespace(executable='first interpreter'))
    cwd, extra = tmp_path / 'cwd', tmp_path / 'argument with spaces'
    assert module._probe('pass', cwd, 17, extra) is result
    assert calls == [(['first interpreter', '-B', '-S', '-c', 'pass', '17', str(extra)],
                      dict(cwd=cwd, env={'OTHER': 'keep', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'},
                           capture_output=True, text=True, timeout=30))]
    assert original == snapshot
    assert calls[0][1]['env'] is not original
    original['OTHER'] = 'changed'
    assert calls[0][1]['env']['OTHER'] == 'keep'

    later_calls, copies = [], []
    later_source = {'PYTHONPATH': 'later unused', 'EXTRA': 'later'}
    later_result = object()
    sys_binding = SimpleNamespace(executable='before copy interpreter')
    process_binding = SimpleNamespace(run=lambda *args, **kwargs: pytest.fail('runner captured before copy'))

    def later_run(argv, **options):
        later_calls.append((argv, options))
        return later_result

    class Environment:
        def copy(self):
            copies.append('copied')
            process_binding.run = later_run
            sys_binding.executable = 'after copy interpreter'
            return dict(later_source)

    monkeypatch.setattr(module, 'os', SimpleNamespace(environ=Environment()))
    monkeypatch.setattr(module, 'subprocess', process_binding)
    monkeypatch.setattr(module, 'sys', sys_binding)
    assert module._probe('later payload', cwd, extra) is later_result
    assert copies == ['copied']
    assert later_calls == [(['after copy interpreter', '-B', '-S', '-c', 'later payload', str(extra)],
                            dict(cwd=cwd, env={'EXTRA': 'later', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'},
                                 capture_output=True, text=True, timeout=30))]
    assert later_source == {'PYTHONPATH': 'later unused', 'EXTRA': 'later'}
    assert len(calls) == 1


@pytest.mark.parametrize('family', FAMILIES)
def test_probe_propagates_runner_exception_identity(monkeypatch, tmp_path, family):
    module = importlib.import_module(f'test_packaging_argument_{family}_family')
    failure = TimeoutExpired('synthetic command', 30)

    def run(*args, **kwargs):
        raise failure

    monkeypatch.setattr(module, 'os', SimpleNamespace(environ={}))
    monkeypatch.setattr(module, 'subprocess', SimpleNamespace(run=run))
    monkeypatch.setattr(module, 'sys', SimpleNamespace(executable='synthetic interpreter'))
    with pytest.raises(TimeoutExpired) as raised:
        module._probe('pass', tmp_path)
    assert raised.value is failure
