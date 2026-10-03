"""Whole narrative-family compatibility; synthetic inputs and manifest judges only."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.check_zero_install import make_bare_copy
from test_narrative_decision_long_form import _make_text, _keyed_manifest
from setec.core.narrative_longform_segment import segment_text

SCRIPTS = Path(__file__).resolve().parents[1]
FAMILY = ("narrative_decision_audit", "narrative_decision_long_form")


def _probe(code, cwd, *args):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-S", "-c", code, *map(str, args)],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("stem", FAMILY)
@pytest.mark.parametrize("package_first", (False, True))
def test_identity_and_shared_monkeypatches(tmp_path, stem, package_first):
    result = _probe("""
import importlib, sys
sys.path.insert(0, sys.argv[1])
stem = sys.argv[2]
names = [stem, 'setec.surfaces.' + stem]
if sys.argv[3] == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
assert a.ClaimLicense is importlib.import_module('claim_license').ClaimLicense
for writer, reader, sentinel in [(a,b,17), (b,a,23)]:
    if stem == 'narrative_decision_audit':
        writer.signal_target_value = lambda *args: sentinel
        assert reader.signal_target_value(None, None, None) == sentinel
    else:
        writer.nda.aggregate_score = lambda *args: sentinel
        assert reader.nda.aggregate_score([]) == sentinel
        assert reader.nda is importlib.import_module('narrative_decision_audit')
        writer.nj.fingerprint_prompt = lambda: 'patched'
        assert reader._base_audit_identity().endswith('+prompt:patched')
        assert reader.nj is importlib.import_module('narrative_judge')
assert not any(n in sys.modules for n in ('spacy','torch','transformers','anthropic','openai'))
""", tmp_path, SCRIPTS, stem, package_first)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("stem", FAMILY)
def test_named_runpy_does_not_run_main(tmp_path, stem):
    result = _probe("""
import runpy, sys, importlib
from pathlib import Path
path = Path(sys.argv[1])
sys.path.insert(0, str(path.parent))
module = importlib.import_module('setec.surfaces.' + path.stem)
module.main = lambda *args: (_ for _ in ()).throw(AssertionError('main called'))
original = sys.modules['__main__']
namespace = runpy.run_path(str(path), run_name='detached_narrative')
assert namespace['TASK_SURFACE'] == module.TASK_SURFACE
assert sys.modules['__main__'] is original
""", tmp_path, SCRIPTS / (stem + ".py"))
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="module")
def copied_long_case(tmp_path_factory):
    root = tmp_path_factory.mktemp("narrative_copy")
    bare = make_bare_copy(root)
    text = _make_text(150)
    target = root / "long.txt"
    target.write_text(text, encoding="utf-8")
    manifest = root / "keyed.json"
    manifest.write_text(json.dumps(_keyed_manifest(segment_text(text))), encoding="utf-8")
    return root, bare / "scripts", target, manifest


@pytest.mark.parametrize("mode", ("direct", "runpy", "module"))
def test_copied_keyed_manifest_scoring_stays_suppressed(copied_long_case, mode):
    root, scripts, target, manifest = copied_long_case
    result = _probe("""
import sys, runpy
from pathlib import Path
scripts, mode = Path(sys.argv.pop(1)), sys.argv.pop(1)
stem = 'narrative_decision_long_form'
path = scripts / (stem + '.py')
sys.argv[0] = str(path)
original = sys.modules['__main__']
try:
    if mode == 'module':
        sys.path.insert(0, str(scripts))
        runpy.run_module('setec.surfaces.' + stem, run_name='__main__')
    elif mode == 'direct':
        # Use an actual direct child, not an exec imitation of a script.
        import subprocess
        p = subprocess.run([sys.executable, '-B', '-S', str(path), *sys.argv[1:]])
        sys.exit(p.returncode)
    else:
        runpy.run_path(str(path), run_name='__main__')
finally:
    assert sys.modules['__main__'] is original
""", root, scripts, mode, target, "--judge", "manifest", "--judge-manifest", manifest,
                    "--out", root / (mode + ".json"), "--json")
    assert result.returncode == 0, result.stderr
    envelope = json.loads(result.stdout)
    assert envelope["available"] is True
    assert envelope["task_surface"] == "narrative_decision_long_form"
    assert envelope["results"]["judge"]["model"] == "test-judge"
    assert envelope["results"]["segmentation"]["n_segments"] >= 3
    assert envelope["results"]["validation_binding"]["licensed"] is False
    assert envelope["results"]["validation_binding"]["receipt_present"] is False
    assert all(row["value"] is None for row in envelope["results"]["per_signal_aggregates"].values())
    assert not envelope["results"].get("calibration_only", False)
    assert envelope["claim_license"]["licenses"]
    assert json.loads((root / (mode + ".json")).read_text(encoding="utf-8")) == envelope


def test_copied_mock_and_overwrite_refusals(copied_long_case):
    root, scripts, target, manifest = copied_long_case
    before_target, before_manifest = target.read_bytes(), manifest.read_bytes()
    code = "import runpy,sys; path=sys.argv.pop(1); runpy.run_path(path,run_name='__main__')"
    launcher = scripts / "narrative_decision_long_form.py"
    mock = _probe(code, root, launcher, target, "--judge", "mock", "--json")
    assert mock.returncode == 2, mock.stderr
    assert json.loads(mock.stdout)["reason_category"] == "policy_refused"
    for output in (target, manifest):
        result = _probe(code, root, launcher, target, "--judge", "manifest",
                        "--judge-manifest", manifest, "--out", output, "--json")
        assert result.returncode == 1, result.stderr
        assert json.loads(result.stdout)["reason_category"] == "bad_input"
        assert "refusing to overwrite" in json.loads(result.stdout)["reason"]
    assert target.read_bytes() == before_target and manifest.read_bytes() == before_manifest
