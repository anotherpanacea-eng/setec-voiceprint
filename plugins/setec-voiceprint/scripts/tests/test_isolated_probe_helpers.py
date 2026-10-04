"""Behavioral controls for isolated subprocess probe success and refusal."""
import importlib
from types import SimpleNamespace

import pytest

FAMILIES = ['baseline', 'judge', 'support']


@pytest.mark.parametrize('family', FAMILIES)
def test_probe_preserves_live_bindings_and_argument_protocol(monkeypatch, tmp_path, family):
    module = importlib.import_module(f'test_p3_{family}_family_relocation')
    calls = []

    def run(argv, **options):
        calls.append((argv, options))
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(module, 'subprocess', SimpleNamespace(run=run))
    monkeypatch.setattr(module, 'sys', SimpleNamespace(executable='first interpreter'))
    scripts, cwd, extra = tmp_path / 'scripts with spaces', tmp_path / 'cwd', tmp_path / 'argument'
    assert module._probe('pass', scripts, cwd, 17, extra) is None
    assert calls == [(['first interpreter', '-I', '-S', '-B', '-c', 'pass', str(scripts), '17', str(extra)],
                      dict(cwd=cwd, text=True, capture_output=True, timeout=30))]

    later_calls = []

    def later_run(argv, **options):
        later_calls.append(argv)
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(module, 'subprocess', SimpleNamespace(run=later_run))
    monkeypatch.setattr(module, 'sys', SimpleNamespace(executable='later interpreter'))
    assert module._probe('pass', scripts, cwd) is None
    assert later_calls == [['later interpreter', '-I', '-S', '-B', '-c', 'pass', str(scripts)]]
    assert len(calls) == 1


@pytest.mark.parametrize('family', FAMILIES)
@pytest.mark.parametrize('returncode,stdout,stderr', [
    (1, '', 'synthetic runner refusal'),
    (0, 'unexpected output', ''),
    (0, '', 'unexpected diagnostic'),
])
def test_probe_refuses_failed_or_nonempty_output(monkeypatch, tmp_path, family, returncode, stdout, stderr):
    module = importlib.import_module(f'test_p3_{family}_family_relocation')
    result = SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(module, 'subprocess', SimpleNamespace(run=lambda *args, **kwargs: result))
    monkeypatch.setattr(module, 'sys', SimpleNamespace(executable='synthetic interpreter'))
    with pytest.raises(AssertionError) as raised:
        module._probe('pass', tmp_path, tmp_path)
    if returncode:
        assert str(raised.value).splitlines()[0] == stderr
