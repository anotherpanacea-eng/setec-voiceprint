"""Whole consistency aliases and copied-plugin behavior with offline judges only."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parents[4] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from check_zero_install import make_bare_copy  # type: ignore  # noqa: E402

STEMS = ("cross_doc_argument_consistency", "position_pair_register")
SCRIPTS = Path(__file__).resolve().parents[1]


def _probe(code, cwd, *args):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    return subprocess.run([sys.executable, "-B", "-S", "-c", code, *map(str, args)],
                          cwd=cwd, env=env, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("stem", STEMS)
@pytest.mark.parametrize("package_first", (False, True))
def test_identity_and_shared_private_api_monkeypatches(tmp_path, stem, package_first):
    proc = _probe("""
import importlib,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
names=[sys.argv[2],'setec.surfaces.'+sys.argv[2]]
if sys.argv[3]=='True': names.reverse()
a,b=[importlib.import_module(n) for n in names]
assert a is b and a.SCRIPT_DIR==Path(sys.argv[1])
assert a.build_output is importlib.import_module('output_schema').build_output
if sys.argv[2]=='position_pair_register':
    assert a.ClaimLicense is importlib.import_module('claim_license').ClaimLicense
    assert a.ppj is importlib.import_module('position_pair_register_judge')
    assert a.PositionPair is a.ppj.PositionPair
    for writer,reader in [(a,b),(b,a)]:
        writer._gate_question=lambda text:'shared refusal'
        jr=reader.ppj.JudgeResult([reader.PositionPair('What policy?',0,1,'A',2,3,'B')],{'kind':'mock'})
        results,warnings=reader.build_results(jr,text_len=3,cap_per_question=12,cap_per_work=60,prompt_fingerprint='synthetic')
        assert results['pairs']==[] and results['pairs_refused_q_gate']==1
else:
    assert a.from_legacy is importlib.import_module('claim_license').from_legacy
    assert a.cjudge is importlib.import_module('cross_doc_consistency_judge')
    assert a.SchemaError is importlib.import_module('cross_doc_consistency_schema').SchemaError
    novelty=importlib.import_module('cross_doc_novelty_profile')
    assert a._load_reference_manifest is novelty._load_reference_manifest
    for writer,reader in [(a,b),(b,a)]:
        writer._detect_retraction=lambda text:(True,'shared evidence')
        writer._DETECTORS['retraction']=writer._detect_retraction
        variation,rationale=reader.classify_legitimate_variation('ignored')
        assert variation=='defended_retraction' and 'shared evidence' in rationale
assert not any(n in sys.modules for n in ('torch','spacy','nltk','transformers','anthropic','openai'))
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
ns=runpy.run_path(str(path),run_name='detached_consistency')
assert ns['TASK_SURFACE']==module.TASK_SURFACE and sys.modules['__main__'] is old
""", tmp_path, SCRIPTS / (stem + ".py"))
    assert proc.returncode == 0, proc.stderr


@pytest.fixture(scope="module")
def bare_plugin(tmp_path_factory):
    root = tmp_path_factory.mktemp("consistency")
    return root, make_bare_copy(root) / "scripts"


@pytest.mark.parametrize("recorded", [{"model": "synthetic-recorded", "prompt_version": "recorded-v1"}, {}])
def test_copied_cross_doc_manifest_and_self_exclusion(bare_plugin, recorded):
    root, scripts = bare_plugin
    pool = root / "pool"
    pool.mkdir(exist_ok=True)
    focal = pool / "focal.txt"
    positive = "The policy should proceed."
    negative = "I no longer hold that view; in 2014 the policy should not proceed."
    focal.write_text(positive * 20, encoding="utf-8")
    (pool / "copy.txt").write_text(positive * 20, encoding="utf-8")
    (pool / "other.txt").write_text(negative * 20, encoding="utf-8")
    docs = {}
    for name, quote, stance in (("focal.txt", positive, "for"), ("other.txt", negative, "against")):
        docs[name] = {"commitments": [{"topic_ref": "tax", "type": "claim", "statement": quote,
                                     "start_char": 0, "end_char": len(quote), "quote": quote, "stance": stance}]}
    manifest = root / "commitments.json"
    manifest.write_text(json.dumps({"docs": docs, "judge_identity": recorded}), encoding="utf-8")
    output = root / "cross-doc.json"
    proc = _probe("import runpy,sys; path=sys.argv.pop(1); runpy.run_path(path,run_name='__main__')", root,
                  scripts / "cross_doc_argument_consistency.py", "--focal", focal, "--reference-dir", pool,
                  "--judge", "manifest", "--judge-manifest", manifest, "--json", "--out", output)
    assert proc.returncode == 0, proc.stderr
    assert not proc.stdout.strip()
    envelope = json.loads(output.read_text(encoding="utf-8"))
    assert envelope["available"] is True and envelope["task_surface"] == "argument_consistency"
    results = envelope["results"]
    assert results["n_docs"] == 2 and envelope["baseline"]["n_files"] == 1
    assert results["tensions"][0]["legitimate_variation"] == "defended_retraction"
    assert results["judge"] == {"kind": "manifest", "manifest_path": str(manifest),
                                "model": recorded.get("model"), "prompt_version": recorded.get("prompt_version")}


@pytest.mark.parametrize("recorded", [{"model": "synthetic-recorded", "prompt_version": "recorded-v1"}, {}])
def test_copied_position_manifest_question_refusal_and_text_artifacts(bare_plugin, recorded):
    root, scripts = bare_plugin
    target = root / "work.txt"
    first, second = "The council should act.", "The council should wait."
    text = first + "\n\n" + second
    target.write_text(text, encoding="utf-8")
    pair = {"question": "What policy should apply?",
            "a": {"start_char": 0, "end_char": len(first), "quote": first},
            "b": {"start_char": len(first) + 2, "end_char": len(text), "quote": second}}
    manifest = root / "pairs.json"
    manifest.write_text(json.dumps({"pairs": [dict(pair, question="How do these claims conflict?"), pair],
                                    "judge_identity": recorded}), encoding="utf-8")
    output, markdown = root / "position.json", root / "position.md"
    args = [scripts / "position_pair_register.py", target, "--judge", "manifest", "--judge-manifest", manifest,
            "--out", output, "--out-md", markdown]
    code = "import runpy,sys; path=sys.argv.pop(1); runpy.run_path(path,run_name='__main__')"
    proc = _probe(code, root, *args, "--json")
    assert proc.returncode == 0, proc.stderr
    envelope = json.loads(proc.stdout)
    assert json.loads(output.read_text(encoding="utf-8")) == envelope
    results = envelope["results"]
    assert results["pairs"] == [dict(pair, a=dict(pair["a"], doc="target"), b=dict(pair["b"], doc="target"))]
    assert results["pairs_refused_q_gate"] == 1
    assert results["judge"]["judge_identity"] == {"kind": "manifest", "manifest_path": str(manifest),
                                                   "model": recorded.get("model"), "prompt_version": recorded.get("prompt_version")}
    rendered = markdown.read_text(encoding="utf-8")
    assert first in rendered and second in rendered and "How do these claims conflict?" not in rendered
    text_output = root / "position-text.txt"
    text_proc = _probe(code, root, *args[:-4], "--out", text_output)
    assert text_proc.returncode == 0, text_proc.stderr
    assert not text_proc.stdout.strip()
    assert text_output.read_text(encoding="utf-8").rstrip("\n") == rendered.rstrip("\n")
