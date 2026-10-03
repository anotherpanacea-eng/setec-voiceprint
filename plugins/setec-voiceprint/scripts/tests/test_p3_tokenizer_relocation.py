"""Frozen tokenizer compatibility in a copied, zero-install plugin."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize("package_first", [False, True])
def test_copied_plugin_alias_data_and_exception_compatibility(tmp_path, package_first):
    plugin = tmp_path / "plugin"
    shutil.copytree(
        Path(__file__).resolve().parents[2], plugin,
        ignore=shutil.ignore_patterns("__pycache__", "tests"),
    )
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONUTF8"] = "1"
    code = f"""
import importlib, pickle, sys
from pathlib import Path
sys.path.insert(0, {str(plugin / 'scripts')!r})
names = ['passage_tokenizer_v1', 'setec.core.passage_tokenizer_v1']
if {package_first!r}: names.reverse()
first, second = [importlib.import_module(name) for name in names]
assert first is second
original = first._digest
first._digest = lambda raw: 'shared'
assert second._digest(b'probe') == 'shared'
first._digest = original
assert first.DATA_FILE == Path({str(plugin / 'scripts/passage_tokenizer_data_v1.json')!r})
assert first.load_data()[2]['data_commitment_sha256'].startswith('sha256:')
assert first.tokenize('A_\\u00c9!') == [
    {{'char_start': 0, 'char_end': 3, 'normalized_token': 'a_\\u00e9'}}]
assert first.TokenizerDataError is second.TokenizerDataError
assert first.TokenizerDataError.__module__ == 'passage_tokenizer_v1'
exc = first.TokenizerDataError('historic')
restored = pickle.loads(pickle.dumps(exc))
assert type(restored) is first.TokenizerDataError and restored.args == ('historic',)
# Protocol-zero bytes emitted before relocation retain the legacy global name.
historic = b'cpassage_tokenizer_v1\\nTokenizerDataError\\np0\\n(Vhistoric\\np1\\ntp2\\nRp3\\n.'
restored = pickle.loads(historic)
assert type(restored) is first.TokenizerDataError and restored.args == ('historic',)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code], cwd=tmp_path, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stderr
    launcher = plugin / "scripts/passage_tokenizer_v1.py"
    direct = subprocess.run(
        [sys.executable, "-B", str(launcher)], cwd=tmp_path, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert (direct.returncode, direct.stdout, direct.stderr) == (0, "", "")
    runpy = subprocess.run(
        [sys.executable, "-B", "-c", f"""
import runpy, sys
main = sys.modules['__main__']
runpy.run_path({str(launcher)!r}, run_name='__main__')
assert sys.modules['__main__'] is main
"""], cwd=tmp_path, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=30,
    )
    assert (runpy.returncode, runpy.stdout, runpy.stderr) == (0, "", "")
