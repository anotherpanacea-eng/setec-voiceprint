"""Acquisition compatibility from detached, copied and ordinary import contexts."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN / "scripts"


def _probe(code, cwd, *args):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("SETEC_BASELINES_DIR", None)
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", code, *map(str, args)],
        cwd=cwd, env=env, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""


@pytest.mark.parametrize("package_first", [False, True])
def test_identity_dataclasses_legacy_pickles_and_shared_patches(tmp_path, package_first):
    _probe('''
import importlib, pickle, sys
sys.path.insert(0, sys.argv[1])
names = ['acquisition_core', 'setec.surfaces.acquisition_core']
if sys.argv[2] == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
assert a.FetchResult is b.FetchResult
assert a.AcquiredPiece is b.AcquiredPiece
for name in ('FetchResult', 'AcquiredPiece', 'RunSummary', 'StableRedactionMap'):
    assert pickle.loads(('cacquisition_core\\n' + name + '\\n.').encode()) is getattr(a, name)
value = a.FetchResult(url='fixture://local', status=200, text='synthetic')
assert type(pickle.loads(pickle.dumps(value))) is b.FetchResult
original = a.resolve_baselines_dir
for writer, reader, sentinel in [(a, b, 'first'), (b, a, 'second')]:
    writer.resolve_baselines_dir = lambda: sentinel
    assert reader.resolve_baselines_dir() == sentinel
b.resolve_baselines_dir = original
assert a.resolve_baselines_dir is original
assert not any(n in sys.modules for n in ('preprocessing', 'pdf_extract', 'requests',
    'spacy', 'torch', 'transformers', 'sentence_transformers', 'openai'))
''', tmp_path, SCRIPTS, package_first)


@pytest.fixture(scope="module")
def copied_plugin(tmp_path_factory):
    root = tmp_path_factory.mktemp("acquisition_copy")
    copied = root / "marketplace" / "plugins" / "setec-voiceprint"
    shutil.copytree(PLUGIN, copied, ignore=shutil.ignore_patterns("tests", "__pycache__"))
    foreign = root / "foreign"
    foreign.mkdir()
    return copied / "scripts", foreign


@pytest.mark.parametrize("mode", ["direct", "runpy_main", "runpy_named", "package"])
def test_copied_plugin_detached_entrypoints(copied_plugin, mode):
    scripts, foreign = copied_plugin
    _probe('''
import importlib, runpy, subprocess, sys
from pathlib import Path
scripts = Path(sys.argv[1])
mode = sys.argv[2]
old = sys.modules['__main__']
path = scripts / 'acquisition_core.py'
if mode == 'direct':
    result = subprocess.run([sys.executable, '-I', '-S', '-B', str(path)],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ''
elif mode.startswith('runpy_'):
    namespace = runpy.run_path(str(path), run_name='__main__' if mode == 'runpy_main' else 'detached_acquisition')
    assert namespace['TASK_SURFACE'] == 'voice_coherence_acquisition'
    assert 'main' not in namespace
else:
    sys.path.insert(0, str(scripts))
    ac = importlib.import_module('setec.surfaces.acquisition_core')
    assert ac is importlib.import_module('acquisition_core')
    assert ac.slugify('Synthetic title!') == 'synthetic-title'
assert sys.modules['__main__'] is old
assert not any(n in sys.modules for n in ('spacy', 'torch', 'transformers', 'requests'))
''', foreign, scripts, mode)


def test_copied_lookup_preserves_env_sibling_and_fallback(copied_plugin):
    scripts, foreign = copied_plugin
    _probe('''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import acquisition_core as ac
from setec.core import acquisition_core as core
scripts = Path(sys.argv[1])
fallback = Path(sys.argv[2]) / 'synthetic-fallback'
# The lookup and its fallback constant live in core; the surface re-exports them.
core.DEFAULT_BASELINES_FALLBACK = fallback
sibling = scripts.parents[2].parent / ac.PRIVATE_DIR_NAME
assert not sibling.exists()
assert ac.resolve_baselines_dir() == fallback
sibling.mkdir()
try:
    assert ac.resolve_baselines_dir() == sibling
    os.environ['SETEC_BASELINES_DIR'] = '~/synthetic-overridden'
    assert ac.resolve_baselines_dir() == (Path.home() / 'synthetic-overridden')
    assert ac.default_output_dir('register', 'author') == Path.home() / 'synthetic-overridden/impostors/register/author'
    del os.environ['SETEC_BASELINES_DIR']
finally:
    sibling.rmdir()
assert ac.resolve_baselines_dir() == fallback
''', foreign, scripts, foreign)


@pytest.mark.parametrize("helper", ["preprocessing", "pdf_extract"])
@pytest.mark.parametrize("scenario", ["success", "import_failure", "cached", "call_failure"])
def test_lazy_helper_precedence_and_path_restoration(tmp_path, helper, scenario):
    _probe('''
import importlib, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import acquisition_core as ac
root, helper, scenario = Path(sys.argv[2]), sys.argv[3], sys.argv[4]
local = root / 'local'
shadow = root / 'shadow'
local.mkdir(); shadow.mkdir()
if helper == 'preprocessing':
    body = "def strip_non_prose(text, **kwargs):\\n    return text + ':LOCAL', kwargs\\n"
else:
    body = "from pathlib import Path\\nseen = []\\ndef extract_text_layer(path, *, artifact_profile=None):\\n    seen.append(path)\\n    assert path.read_bytes() == b'synthetic-pdf'\\n    return 'LOCAL:' + str(artifact_profile)\\n"
(local / (helper + '.py')).write_text(body, encoding='utf-8')
(shadow / (helper + '.py')).write_text(body.replace('LOCAL', 'SHADOW'), encoding='utf-8')
sys.path.insert(0, str(shadow))
if scenario == 'cached': importlib.import_module(helper)
elif scenario == 'import_failure':
    (local / (helper + '.py')).write_text("raise ImportError('planted import refusal')\\n", encoding='utf-8')
elif scenario == 'call_failure':
    if helper == 'preprocessing':
        source = "def strip_non_prose(*args, **kwargs):\\n    raise ValueError('planted call refusal')\\n"
    else:
        source = "seen = []\\ndef extract_text_layer(path, **kwargs):\\n    seen.append(path)\\n    raise ValueError('planted call refusal')\\n"
    (local / (helper + '.py')).write_text(source, encoding='utf-8')
# preprocess_text lives in core; pdf_text_from_bytes stays on the surface.
from setec.core import acquisition_core as core
(core if helper == 'preprocessing' else ac).scripts_dir = lambda: local
before = list(sys.path)
if helper == 'pdf_extract':
    assert ac.pdf_text_from_bytes(b'') == ''
    assert helper not in sys.modules or scenario == 'cached'
try:
    if helper == 'preprocessing':
        result = ac.preprocess_text('synthetic', rules=['fixture'], allow_non_prose=True, strip_aggressive=True)
    else:
        result = ac.pdf_text_from_bytes(b'synthetic-pdf', artifact_profile='tanner')
except ImportError as exc:
    assert scenario == 'import_failure' and str(exc) == 'planted import refusal'
except ValueError as exc:
    assert scenario == 'call_failure' and helper == 'preprocessing'
    assert str(exc) == 'planted call refusal'
else:
    expected = 'SHADOW' if scenario == 'cached' else 'LOCAL'
    if scenario == 'call_failure':
        assert helper == 'pdf_extract' and result == ''
    elif helper == 'preprocessing':
        assert result == ('synthetic:' + expected,
            {'rules': ['fixture'], 'allow_non_prose': True, 'strip_aggressive': True})
    else:
        assert result == expected + ':tanner'
    module = sys.modules[helper]
    assert Path(module.__file__).parent == (shadow if scenario == 'cached' else local)
    if helper == 'pdf_extract':
        assert len(module.seen) == 1 and not module.seen[0].exists()
assert sys.path == before
''', tmp_path, SCRIPTS, tmp_path, helper, scenario)
