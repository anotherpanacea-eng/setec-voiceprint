"""Whole quality/calibration alias behavior; synthetic inputs and offline judges only."""

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

STEMS = ("argquality_dimension_profile", "argument_certainty_calibration")
SCRIPTS = Path(__file__).resolve().parents[1]


def _probe(code, cwd, *args):
    return run_environment_probe(os, subprocess, sys, code, cwd, *args)


@pytest.mark.parametrize("stem", STEMS)
@pytest.mark.parametrize("package_first", (False, True))
def test_shared_module_and_monkeypatch_identity(tmp_path, stem, package_first):
    proc = _probe("""
import importlib, sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
names=[sys.argv[2], 'setec.surfaces.'+sys.argv[2]]
if sys.argv[3]=='True': names.reverse()
a,b=[importlib.import_module(n) for n in names]
assert a is b and a.SCRIPT_DIR==Path(sys.argv[1])
assert a.build_output is importlib.import_module('output_schema').build_output
contract=importlib.import_module('claim_license')
if sys.argv[2]=='argquality_dimension_profile':
    assert a.ClaimLicense is contract.ClaimLicense
    judge=importlib.import_module('argquality_judge')
    assert a.build_judge is judge.build_judge and a.JudgeError is judge.JudgeError
else:
    assert a.from_legacy is contract.from_legacy
    assert a.cjudge is importlib.import_module('argument_certainty_judge')
    assert a.SchemaError is importlib.import_module('argument_certainty_calibration_schema').SchemaError
for writer,reader,n in [(a,b,17),(b,a,23)]:
    writer.count_words=lambda text:n
    assert reader.count_words('ignored')==n
if sys.argv[2]=='argument_certainty_calibration':
    b.classify_certainty=lambda quote:'tentative'
    assert a.build_claim_rows('Obviously.',[a.cjudge.Claim('t','Obviously.',0,10,'Obviously.','none')],{})[0]['certainty']=='tentative'
assert not any(n in sys.modules for n in ('torch','spacy','transformers','anthropic','openai'))
""", tmp_path, SCRIPTS, stem, package_first)
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("stem", STEMS)
def test_named_runpy_does_not_call_main(tmp_path, stem):
    proc = _probe("""
import importlib,runpy,sys
from pathlib import Path
path=Path(sys.argv[1]); sys.path.insert(0,str(path.parent))
module=importlib.import_module('setec.surfaces.'+path.stem)
module.main=lambda *args: (_ for _ in ()).throw(AssertionError('main called'))
old=sys.modules['__main__']
ns=runpy.run_path(str(path),run_name='detached_quality')
assert ns['TASK_SURFACE']==module.TASK_SURFACE and sys.modules['__main__'] is old
""", tmp_path, SCRIPTS / (stem + ".py"))
    assert proc.returncode == 0, proc.stderr


@pytest.fixture(scope="module")
def bare_plugin(tmp_path_factory):
    root = tmp_path_factory.mktemp("quality")
    return root, make_bare_copy(root) / "scripts"


@pytest.mark.parametrize("fingerprint", ["recorded-old-prompt", None])
def test_copied_quality_manifest_independent_bands_and_own_fingerprint(bare_plugin, fingerprint):
    root, scripts = bare_plugin
    target = root / "quality.txt"
    sentence = "Because evidence matters, the council should consider the available trials."
    target.write_text(sentence * 20, encoding="utf-8")
    identity = {"model": "synthetic-recorded-judge"}
    if fingerprint is not None:
        identity["prompt_fingerprint_sha256"] = fingerprint
    dimensions = {"logic": {"band": "higher", "evidence_spans": [sentence], "basis": "synthetic"},
                  "rhetoric": {"band": "lower", "evidence_spans": [], "basis": "synthetic"},
                  "dialectic": {"band": None, "evidence_spans": [], "basis": "declined"}}
    manifest = root / "quality-manifest.json"
    manifest.write_text(json.dumps({"values": {"dimensions": dimensions}, "judge_identity": identity}), encoding="utf-8")
    output, markdown = root / "quality.json", root / "quality.md"
    argv = [scripts / "argquality_dimension_profile.py", target, "--judge", "manifest", "--judge-manifest", manifest,
            "--out", output, "--out-md", markdown, "--json"]
    code = "import runpy,sys; path=sys.argv.pop(1); runpy.run_path(path,run_name='__main__')"
    proc = _probe(code, root, *argv)
    assert proc.returncode == 0, proc.stderr
    envelope = json.loads(proc.stdout)
    assert envelope["available"] is True and envelope["task_surface"] == "argquality_dimension_profile"
    results = envelope["results"]
    assert results["dimensions"] == dimensions
    assert results["prompt_fingerprint_sha256"] == fingerprint
    assert results["judge"]["judge_identity"]["model"] == identity["model"]
    assert json.loads(output.read_text(encoding="utf-8")) == envelope
    assert markdown.read_text(encoding="utf-8").startswith("# ")
    refused = _probe(code, root, *argv, "--expect-fingerprint", "different-prompt")
    assert refused.returncode == 0, refused.stderr
    unavailable = json.loads(refused.stdout)
    assert unavailable["available"] is False and unavailable["reason_category"] == "bad_input"
    assert "drift" in unavailable["reason"]


@pytest.mark.parametrize("fabricated", [False, True])
def test_copied_certainty_support_locus_and_cli_artifact(bare_plugin, fabricated):
    root, scripts = bare_plugin
    support = "Three randomized trials in the appendix each found a measurable drop in rents."
    text = support + "\n\n[[claim support=none topic=t_x]] Zoning reform clearly works, without question.\n"
    target = root / "certainty.txt"
    target.write_text(text, encoding="utf-8")
    sidecar = root / "support.json"
    locus = {"start_char": 0, "end_char": len(support), "quote": "fabricated evidence" if fabricated else support}
    sidecar.write_text(json.dumps({"support_loci": {"t_x": [locus]}}), encoding="utf-8")
    output = root / "certainty.json"
    proc = _probe("import runpy,sys; path=sys.argv.pop(1); runpy.run_path(path,run_name='__main__')", root,
                  scripts / "argument_certainty_calibration.py", target, "--judge", "mock", "--json",
                  "--length-floor-words", "10", "--support-loci", sidecar, "--out", output)
    assert proc.returncode == (1 if fabricated else 0), proc.stderr
    assert not proc.stdout.strip()
    envelope = json.loads(output.read_text(encoding="utf-8"))
    assert envelope["task_surface"] == "argument_calibration"
    assert envelope["available"] is not fabricated
    if fabricated:
        assert envelope["reason_category"] == "internal_error"
    else:
        row = envelope["results"]["claims"][0]
        assert row["alignment"] == "aligned" and row["defense"] == "defended_elsewhere"
        assert envelope["results"]["judge"]["kind"] == "mock"
        assert isinstance(envelope["claim_license"], dict)


@pytest.mark.parametrize("recorded", [{"model": "synthetic-recorded-judge", "prompt_version": "recorded-v1"}, {}])
def test_copied_certainty_manifest_does_not_invent_provenance(bare_plugin, recorded):
    root, scripts = bare_plugin
    text = "The policy obviously fails, always, without question."
    target = root / "certainty-manifest-target.txt"
    target.write_text(text, encoding="utf-8")
    claim = {"topic_ref": "t_x", "statement": text, "start_char": 0, "end_char": len(text),
             "quote": text, "support": "none"}
    manifest = root / "certainty-manifest.json"
    manifest.write_text(json.dumps({"claims": [claim], "judge_identity": recorded}), encoding="utf-8")
    output = root / "certainty-manifest-output.json"
    proc = _probe("import runpy,sys; path=sys.argv.pop(1); runpy.run_path(path,run_name='__main__')", root,
                  scripts / "argument_certainty_calibration.py", target, "--judge", "manifest", "--json",
                  "--judge-manifest", manifest, "--length-floor-words", "5", "--out", output)
    assert proc.returncode == 0, proc.stderr
    assert not proc.stdout.strip()
    envelope = json.loads(output.read_text(encoding="utf-8"))
    assert envelope["available"] is True
    assert envelope["results"]["judge"] == {"kind": "manifest", "manifest_path": str(manifest),
                                             "model": recorded.get("model"), "prompt_version": recorded.get("prompt_version")}
    assert envelope["results"]["claims"][0]["alignment"] == "overclaim"
