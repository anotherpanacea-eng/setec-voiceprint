"""Whole argument-pattern aliases in a copied plugin, without models or providers."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import run_environment_probe

_TOOLS = Path(__file__).resolve().parents[4] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from check_zero_install import make_bare_copy  # type: ignore  # noqa: E402

STEMS = ("agd_move_scan", "enthymeme_gapflag", "fallacy_scan", "warrant_probe")
SCRIPTS = Path(__file__).resolve().parents[1]


def _probe(code, cwd, *args):
    return run_environment_probe(os, subprocess, sys, code, cwd, *args)


@pytest.mark.parametrize("stem", STEMS)
@pytest.mark.parametrize("package_first", (False, True))
def test_identity_and_shared_api_monkeypatches(tmp_path, stem, package_first):
    result = _probe("""
import importlib, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
stem = sys.argv[2]
names = [stem, 'setec.surfaces.' + stem]
if sys.argv[3] == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b and a.SCRIPT_DIR == Path(sys.argv[1])
contract = importlib.import_module('claim_license')
assert (a.from_legacy is contract.from_legacy) if stem == 'enthymeme_gapflag' else (a.ClaimLicense is contract.ClaimLicense)
assert a.build_output is importlib.import_module('output_schema').build_output
if stem != 'enthymeme_gapflag':
    judge = importlib.import_module({'agd_move_scan':'agd_move_scan_judge', 'fallacy_scan':'fallacy_judge', 'warrant_probe':'warrant_judge'}[stem])
    assert a.JudgeError is judge.JudgeError and a.build_judge is judge.build_judge
for writer, reader, n in [(a, b, 17), (b, a, 23)]:
    writer.count_words = lambda text: n
    assert reader.count_words('ignored') == n
assert not any(n in sys.modules for n in ('spacy','torch','transformers','anthropic','openai'))
""", tmp_path, SCRIPTS, stem, package_first)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("stem", STEMS)
def test_named_runpy_never_executes_main(tmp_path, stem):
    result = _probe("""
import importlib, runpy, sys
from pathlib import Path
path = Path(sys.argv[1])
sys.path.insert(0, str(path.parent))
module = importlib.import_module('setec.surfaces.' + path.stem)
module.main = lambda *args: (_ for _ in ()).throw(AssertionError('main called'))
original = sys.modules['__main__']
namespace = runpy.run_path(str(path), run_name='detached_pattern')
assert namespace['TASK_SURFACE'] == module.TASK_SURFACE
assert sys.modules['__main__'] is original
""", tmp_path, SCRIPTS / (stem + ".py"))
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="module")
def bare_plugin(tmp_path_factory):
    root = tmp_path_factory.mktemp("pattern")
    return root, make_bare_copy(root) / "scripts"


@pytest.mark.parametrize("stem,key,result_key", [
    ("agd_move_scan", "observations", "observations"),
    ("fallacy_scan", "flags", "rhetorical_move_flags"),
    ("warrant_probe", "claims", "warrant_coverage"),
])
def test_copied_manifest_keeps_recorded_identity_and_refuses_drift(bare_plugin, stem, key, result_key):
    root, scripts = bare_plugin
    target = root / (stem + ".txt")
    target.write_text("Because evidence matters, the council may want to act. " * 20, encoding="utf-8")
    recorded = {"model": "synthetic-recorded-judge", "prompt_fingerprint_sha256": "recorded-old-prompt"}
    manifest = root / (stem + "-manifest.json")
    manifest.write_text(json.dumps({"values": {key: []}, "judge_identity": recorded}), encoding="utf-8")
    output = root / (stem + "-output.json")
    markdown = root / (stem + "-output.md")
    argv = [scripts / (stem + ".py"), target, "--judge", "manifest", "--judge-manifest", manifest,
            "--out", output, "--out-md", markdown, "--json"]
    code = "import sys,runpy; path=sys.argv.pop(1); runpy.run_path(path,run_name='__main__')"
    result = _probe(code, root, *argv, "--expect-fingerprint", "recorded-old-prompt")
    assert result.returncode == 0, result.stderr
    envelope = json.loads(result.stdout)
    assert envelope["available"] is True
    results = envelope["results"]
    assert results[result_key] == []
    assert results["judge"]["judge_identity"]["kind"] == "manifest"
    assert all(results["judge"]["judge_identity"][k] == v for k, v in recorded.items())
    assert results["prompt_fingerprint_sha256"] == "recorded-old-prompt"
    assert json.loads(output.read_text(encoding="utf-8")) == envelope
    assert markdown.read_text(encoding="utf-8").startswith("# ")
    refused = _probe(code, root, *argv, "--expect-fingerprint", "different-prompt")
    assert refused.returncode == 0, refused.stderr  # existing direct unavailable-envelope convention
    unavailable = json.loads(refused.stdout)
    assert unavailable["available"] is False and unavailable["reason_category"] == "bad_input"
    assert "fingerprint drift" in unavailable["reason"]
