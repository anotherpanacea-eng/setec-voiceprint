"""Whole-family compatibility with synthetic inputs and optional NLP imports blocked."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.check_zero_install import make_bare_copy

STEMS = ("corpus_novelty_audit", "cross_doc_novelty_profile", "distinct_diversity_audit",
         "homogeneity_audit", "originality_audit", "skeleton_overlap_audit", "verbatim_mosaic_audit")


def _probe(code, cwd, *args):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    blocked = (
        "import sys; sys.modules.update(dict.fromkeys(('spacy','nltk','torch','transformers',"
        "'sentence_transformers','sklearn','textstat','openai','anthropic')));\n"
    )
    return subprocess.run([sys.executable, "-B", "-S", "-c", blocked + code, *map(str, args)],
                          cwd=cwd, env=env, capture_output=True, text=True, timeout=30)


@pytest.fixture(scope="module")
def bare_plugin(tmp_path_factory):
    root = tmp_path_factory.mktemp("diversity")
    return root, make_bare_copy(root) / "scripts"


@pytest.mark.parametrize("stem", STEMS)
@pytest.mark.parametrize("package_first", (False, True))
def test_copied_shared_module_and_monkeypatch_identity(bare_plugin, stem, package_first):
    root, scripts = bare_plugin
    proc = _probe("""
import importlib
from pathlib import Path
sys.path.insert(0,sys.argv[1])
names=[sys.argv[2],'setec.surfaces.'+sys.argv[2]]
if sys.argv[3]=='True': names.reverse()
a,b=[importlib.import_module(n) for n in names]
assert a is b and a.TASK_SURFACE=='set_level_diversity'
assert a.from_legacy is importlib.import_module('claim_license').from_legacy
assert a.build_output is importlib.import_module('output_schema').build_output
assert getattr(a,'SCRIPT_DIR',Path(sys.argv[1]))==Path(sys.argv[1])
for writer,reader,n in [(a,b,17),(b,a,23)]:
    writer._claim_license=lambda:{'probe':n}
    assert reader._claim_license()=={'probe':n}
if sys.argv[2] in ('corpus_novelty_audit','skeleton_overlap_audit'):
    original=importlib.import_module('originality_audit')
    assert a._load_reference_manifest is original._load_reference_manifest
for name in ('spacy','nltk','torch','transformers','sentence_transformers','openai','anthropic'):
    assert sys.modules.get(name) is None
""", root, scripts, stem, package_first)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("stem", STEMS)
def test_detached_named_runpy_exports_implementation_without_calling_main(bare_plugin, stem):
    root, scripts = bare_plugin
    proc = _probe("""
import importlib,runpy
from pathlib import Path
path=Path(sys.argv[1]); old=sys.modules['__main__']
ns=runpy.run_path(str(path),run_name='detached_diversity')
module=importlib.import_module('setec.surfaces.'+path.stem)
assert ns['_mod'] is module and ns['TASK_SURFACE']==module.TASK_SURFACE
module.main=lambda *args: (_ for _ in ()).throw(AssertionError('main called'))
again=runpy.run_path(str(path),run_name='detached_diversity')
assert again['_mod'] is module and sys.modules['__main__'] is old
""", root, scripts / (stem + ".py"))
    assert proc.returncode == 0, proc.stderr
