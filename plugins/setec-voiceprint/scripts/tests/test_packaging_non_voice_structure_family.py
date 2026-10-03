"""Whole non-voice surface compatibility in a bare plugin, using synthetic text."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.check_zero_install import make_bare_copy

STEMS = ("document_layout_audit", "formulaicity_audit", "reference_ecology_audit")
SCRIPTS = Path(__file__).resolve().parents[1]


def _probe(code, cwd, *args):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    return subprocess.run([sys.executable, "-B", "-S", "-c", code, *map(str, args)],
                          cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                          timeout=30)


@pytest.mark.parametrize("stem", STEMS)
@pytest.mark.parametrize("package_first", (False, True))
def test_shared_identity_and_monkeypatches(tmp_path, stem, package_first):
    proc = _probe("""
import importlib,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
names=[sys.argv[2],'setec.surfaces.'+sys.argv[2]]
if sys.argv[3]=='True': names.reverse()
a,b=[importlib.import_module(n) for n in names]
assert a is b and a.SCRIPT_DIR==Path(sys.argv[1])
assert a.ClaimLicense is importlib.import_module('claim_license').ClaimLicense
assert a.build_output is importlib.import_module('output_schema').build_output
for writer,reader,n in [(a,b,17),(b,a,23)]:
    writer.count_words=lambda text:n
    assert reader.count_words('ignored')==n
assert not any(n in sys.modules for n in ('torch','spacy','transformers','anthropic','openai'))
""", tmp_path, SCRIPTS, stem, package_first)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("stem", STEMS)
def test_named_runpy_does_not_execute_main(tmp_path, stem):
    proc = _probe("""
import importlib,runpy,sys
from pathlib import Path
path=Path(sys.argv[1]); sys.path.insert(0,str(path.parent))
module=importlib.import_module('setec.surfaces.'+path.stem)
module.main=lambda *args: (_ for _ in ()).throw(AssertionError('main called'))
old=sys.modules['__main__']
ns=runpy.run_path(str(path),run_name='detached_structure')
assert ns['TASK_SURFACE']==module.TASK_SURFACE and sys.modules['__main__'] is old
assert ns['_mod'] is module
""", tmp_path, SCRIPTS / (stem + ".py"))
    assert proc.returncode == 0, proc.stderr


@pytest.fixture(scope="module")
def bare_plugin(tmp_path_factory):
    root = tmp_path_factory.mktemp("structure")
    return root, make_bare_copy(root) / "scripts"


@pytest.mark.parametrize("stem", STEMS)
@pytest.mark.parametrize("as_json", (False, True))
def test_copied_reports_stdout_and_files(bare_plugin, stem, as_json):
    root, scripts = bare_plugin
    target = root / "structure.md"
    target.write_text("# Heading\n\n" + "word " * 320 +
                      "\n- at the end of the day\n> according to Smith (Smith, 2019)\n"
                      "[source](https://example.com/p)\n", encoding="utf-8")
    args = [scripts / (stem + ".py"), target] + (["--json"] if as_json else [])
    code = "import runpy,sys; path=sys.argv.pop(1); runpy.run_path(path,run_name='__main__')"
    stdout = _probe(code, root, *args)
    assert stdout.returncode == 0, stdout.stderr
    output = root / (stem + (".json" if as_json else ".md"))
    written = _probe(code, root, *args, "--out", output)
    assert written.returncode == 0 and not written.stdout, written.stderr
    assert output.read_text(encoding="utf-8") == stdout.stdout
    if as_json:
        envelope = json.loads(stdout.stdout)
        assert envelope["available"] is True and envelope["target"]["words"] >= 300
        assert envelope["claim_license"] and envelope["results"]
        assert "band" not in envelope["results"] and "verdict" not in envelope["results"]
    else:
        assert stdout.stdout.startswith("# ") and "Descriptive only" in stdout.stdout


def test_copied_custom_phrase_parsing_and_io_exceptions(bare_plugin):
    root, scripts = bare_plugin
    proc = _probe(r"""
import importlib,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
m=importlib.import_module('formulaicity_audit'); root=Path(sys.argv[2])
p=root/'phrases.txt'
p.write_text('# comment\n\n group : marker phrase\nplain marker\ncolon:time:now\nempty:\n :blank group\n',encoding='utf-8')
groups,custom=m.load_phrases(str(p))
assert custom and groups=={'group':['marker phrase'],'custom':['plain marker'],'colon':['time:now'],'':['blank group']}
target=root/'custom.md'; target.write_text('marker phrase plain marker time:now blank group '+'word '*310,encoding='utf-8')
out=root/'custom.json'
assert m.main([str(target),'--phrases-file',str(p),'--json','--out',str(out)])==0
import json
payload=json.loads(out.read_text(encoding='utf-8'))
assert payload['results']['custom_list'] and payload['results']['total_hits']==4
bad=root/'bad-phrases.txt'; bad.write_bytes(b'\xff')
for path,kind in [(root/'absent-phrases.txt',FileNotFoundError),(bad,UnicodeDecodeError)]:
    try: m.main([str(target),'--phrases-file',str(path),'--json'])
    except kind: pass
    else: raise AssertionError('phrase IO exception swallowed')
# Short targets do not load a phrases file, even if that file is missing.
short=root/'tiny.md'; short.write_text('tiny',encoding='utf-8')
assert m.main([str(short),'--phrases-file',str(root/'absent-phrases.txt'),'--json'])==0
""", root, scripts, root)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("stem", STEMS)
def test_copied_output_io_exception_is_not_reclassified(bare_plugin, stem):
    root, scripts = bare_plugin
    proc = _probe("""
import importlib,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]); m=importlib.import_module(sys.argv[2]); root=Path(sys.argv[3])
target=root/'io.md'; target.write_text('word '*310,encoding='utf-8')
try: m.main([str(target),'--json','--out',str(root)])
except (IsADirectoryError,PermissionError): pass
else: raise AssertionError('output IO exception swallowed')
""", root, scripts, stem, root)
    assert proc.returncode == 0, proc.stderr
