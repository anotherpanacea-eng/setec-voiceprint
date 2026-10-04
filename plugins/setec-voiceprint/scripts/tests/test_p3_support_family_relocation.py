"""Support-library legacy imports, serialization and zero-install compatibility."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import assert_isolated_probe

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN / "scripts"
APIS = {
    "atomic_publish": "_identity",
    "embedding_backend": "_resolve_dtype",
    "embeddings": "_get_nlp",
    "passage_remediation_projection": "_normalize_masks",
    "pool_guard": "refusal_reason",
    "shingle_dedup_checkpoint": "_canonical",
    "shingle_dedup_io": "_absolute",
    "shingle_dedup_validate": "_refuse",
    "surprisal_backend": "_resolve_dtype",
    "windows_descriptor_io": "_valid_component",
}


def _probe(code, scripts, cwd, *args):
    assert_isolated_probe(subprocess.run, sys.executable, code, scripts, cwd, *args)


@pytest.mark.parametrize("name,api", APIS.items())
@pytest.mark.parametrize("package_first", (False, True))
def test_import_identity_private_api_and_shared_patches(name, api, package_first, tmp_path):
    _probe('''
import importlib, os, sys
sys.path.insert(0, sys.argv[1])
name, key = sys.argv[2:4]
paths = [name, 'setec.core.' + name]
if sys.argv[4] == 'True': paths.reverse()
if name == 'windows_descriptor_io' and os.name != 'nt':
    for path in paths:
        try:
            importlib.import_module(path)
        except ImportError as exc:
            assert str(exc) == 'windows_descriptor_io is Windows-only'
        else:
            raise AssertionError('Windows-only module imported on POSIX')
else:
    a, b = [importlib.import_module(path) for path in paths]
    assert a is b
    original = getattr(a, key)
    sentinel = object()
    setattr(a, key, lambda *args, **kwargs: sentinel)
    assert getattr(b, key)('synthetic') is sentinel
    setattr(b, key, original)
    assert getattr(a, key) is original
assert not any(n in sys.modules for n in (
    'torch', 'transformers', 'sentence_transformers', 'spacy', 'numpy', 'openai'))
''', SCRIPTS, tmp_path, name, api, package_first)


@pytest.mark.parametrize("package_first", (False, True))
def test_legacy_class_references_and_unloaded_value_pickles(package_first, tmp_path):
    _probe('''
import importlib, os, pickle, sys
sys.path.insert(0, sys.argv[1])
prefix = 'setec.core.' if sys.argv[2] == 'True' else ''
classes = {
    'embedding_backend': ('EmbeddingBackendError', 'EmbeddingBackend'),
    'embeddings': ('EmbeddingsBackendError',),
    'surprisal_backend': ('SurprisalBackendError', 'SurprisalBackend'),
    'passage_remediation_projection': ('RemediationError', 'RemediationProjection', '_UnionFind'),
    'shingle_dedup_checkpoint': ('CheckpointRefusal', '_SharedVmBudget', 'CheckpointSnapshot',
                                'CheckpointState', 'ImmutableShardDirectory', 'CheckpointDirectory'),
    'shingle_dedup_io': ('SecureIOError',),
    'shingle_dedup_validate': ('IndexValidationError', 'VmBudget'),
}
if os.name == 'nt':
    classes['windows_descriptor_io'] = (
        'UNICODE_STRING', 'OBJECT_ATTRIBUTES', 'IO_STATUS_BLOCK', 'FILETIME',
        'BY_HANDLE_FILE_INFORMATION', 'FILE_BASIC_INFORMATION', 'ACL_SIZE_INFORMATION',
        'ACE_HEADER', 'ACCESS_ALLOWED_ACE', 'TOKEN_USER', 'NodeInfo')
mods = {name: importlib.import_module(prefix + name) for name in classes}
for name, names in classes.items():
    for key in names:
        cls = getattr(mods[name], key)
        assert pickle.loads(('c' + name + '\\n' + key + '\\n.').encode()) is cls
        payload = pickle.dumps(cls)
        assert pickle.loads(payload) is cls
        assert b'setec.core.' not in payload
# No live model, handle or SQLite connection is serialized or created.
snapshot = mods['shingle_dedup_checkpoint'].CheckpointSnapshot(
    'inventory-00000001.sqlite', 'inventory', 1, b'synthetic', {'key': 'value'})
values = [
    mods['embedding_backend'].EmbeddingBackend('synthetic/local', device='cpu'),
    mods['surprisal_backend'].SurprisalBackend('synthetic/local', device='cpu'),
    mods['passage_remediation_projection'].RemediationProjection([], [], [], [], [], []),
    mods['passage_remediation_projection']._UnionFind(['a', 'b']),
    snapshot,
    mods['shingle_dedup_checkpoint'].CheckpointState('synthetic', (snapshot,), {}),
    mods['shingle_dedup_checkpoint']._SharedVmBudget(),
    mods['shingle_dedup_validate'].VmBudget(),
    mods['embedding_backend'].EmbeddingBackendError('synthetic'),
    mods['embeddings'].EmbeddingsBackendError('synthetic'),
    mods['surprisal_backend'].SurprisalBackendError('synthetic'),
    mods['passage_remediation_projection'].RemediationError('synthetic'),
    mods['shingle_dedup_checkpoint'].CheckpointRefusal('synthetic'),
    mods['shingle_dedup_validate'].IndexValidationError('synthetic'),
]
for value in values:
    payload = pickle.dumps(value)
    restored = pickle.loads(payload)
    assert type(restored) is type(value)
    assert restored.__dict__ == value.__dict__
    if isinstance(value, Exception):
        assert str(restored) == str(value)
    assert b'setec.core.' not in payload
assert not any(n in sys.modules for n in ('torch', 'transformers', 'spacy', 'numpy'))
''', SCRIPTS, tmp_path, package_first)


@pytest.mark.parametrize("mode", ("direct", "runpy", "package"))
def test_copied_plugin_from_foreign_cwd(mode, tmp_path):
    copied = tmp_path / "plugin"
    shutil.copytree(PLUGIN, copied, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    scripts = copied / "scripts"
    for name in APIS:
        path = scripts / (name + ".py")
        if mode == "direct":
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-B", str(path)], cwd=foreign,
                text=True, capture_output=True, timeout=30,
            )
            if name == "windows_descriptor_io" and os.name != "nt":
                assert result.returncode != 0
                assert "ImportError: windows_descriptor_io is Windows-only" in result.stderr
                assert result.stdout == ""
            else:
                assert result.returncode == 0, result.stderr
                assert result.stdout == result.stderr == ""
        else:
            _probe('''
import importlib, os, runpy, sys
old = sys.modules['__main__']
name, mode = sys.argv[2:4]
try:
    if mode == 'runpy':
        runpy.run_path(sys.argv[4], run_name='__main__')
    else:
        sys.path.insert(0, sys.argv[1])
        a = importlib.import_module('setec.core.' + name)
        assert a is importlib.import_module(name)
except ImportError as exc:
    assert name == 'windows_descriptor_io' and os.name != 'nt'
    assert str(exc) == 'windows_descriptor_io is Windows-only'
else:
    assert name != 'windows_descriptor_io' or os.name == 'nt'
assert sys.modules['__main__'] is old
''', scripts, foreign, name, mode, path)
