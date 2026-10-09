"""Public import and CLI compatibility of whole agency/stance relocations."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

_TOOLS = Path(__file__).resolve().parents[4] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
from check_zero_install import make_bare_copy

SCRIPTS = Path(__file__).resolve().parents[1]
FAMILY = ("agency_abstraction_audit", "stance_modality_audit")


def _probe(code, cwd, *args, encoding="utf-8"):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = encoding
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-S", "-c", code, *map(str, args)],
        cwd=cwd, env=env, capture_output=True, text=True,
        encoding=encoding, timeout=30,
    )


@pytest.mark.parametrize("stem", FAMILY)
@pytest.mark.parametrize("package_first", (False, True))
def test_cold_alias_identity_and_shared_monkeypatches(stem, package_first, tmp_path):
    result = _probe("""
import importlib, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
stem = sys.argv[2]
names = [stem, 'setec.surfaces.' + stem]
if sys.argv[3] == 'True': names.reverse()
a, b = [importlib.import_module(n) for n in names]
assert a is b
assert a.SCRIPT_DIR == Path(sys.argv[1])
assert a.ClaimLicense is importlib.import_module('claim_license').ClaimLicense
assert a.build_output is importlib.import_module('output_schema').build_output
from setec.core import textprims
assert a._word_count is textprims.count_words_unicode
assert a._WORD_RE is textprims._WORD_UNICODE_RE
saved = a._word_count
audit_name = 'audit_agency_abstraction' if stem.startswith('agency') else 'audit_stance_modality'
try:
    for writer, reader, count in [(a, b, 17), (b, a, 29)]:
        writer._word_count = lambda text, count=count: count
        assert getattr(reader, audit_name)('River stone.')['n_words'] == count
finally:
    a._word_count = saved
assert not any(n in sys.modules for n in ('torch','spacy','nltk','numpy','transformers','sentence_transformers'))
""", tmp_path, SCRIPTS, stem, package_first)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("stem", FAMILY)
def test_named_runpy_does_not_execute_main(stem, tmp_path):
    result = _probe("""
import importlib, runpy, sys
path, stem = sys.argv[1:]
sys.argv = ['inspection-without-input']
namespace = runpy.run_path(path, run_name='inspection')
assert namespace['_mod'] is importlib.import_module('setec.surfaces.' + stem)
assert namespace['TASK_SURFACE'] == namespace['_mod'].TASK_SURFACE
""", tmp_path, SCRIPTS / (stem + ".py"), stem)
    assert result.returncode == 0, result.stderr
    assert not result.stdout and not result.stderr


@pytest.mark.parametrize("stem", FAMILY)
def test_copied_plugin_direct_and_runpy_help(stem, tmp_path):
    plugin = make_bare_copy(tmp_path)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    script = plugin / "scripts" / (stem + ".py")
    for encoding in ("utf-8", "cp1252"):
        direct = _probe("import runpy,sys; path=sys.argv.pop(1); runpy.run_path(path,run_name='__main__')",
                        foreign, script, "--help", encoding=encoding)
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PYTHONIOENCODING"] = encoding
        executed = subprocess.run([sys.executable, "-B", "-S", str(script), "--help"],
                                  cwd=foreign, env=env, capture_output=True,
                                  text=True, encoding=encoding, timeout=30)
        assert direct.returncode == executed.returncode == 0
        assert direct.stdout == executed.stdout and direct.stdout.startswith("usage:")
        assert not direct.stderr and not executed.stderr
