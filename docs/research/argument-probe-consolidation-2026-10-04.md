# Argument-family subprocess probe: prospective A1/A4 preparation

Settled base: `ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe`. Fleet issue76. This preparation changes only this document. No source or workflow edit has begun; preparation review grants no implementation clearance.

Fresh fixed-head Windows/Python3.13 baseline:37 passed, zero skips/errors/failures. Module counts:consistency10, pattern15, quality12. Collection/JUnit records retained locally. Windows results do not establish other-platform or hosted clearance.

## Proposed boundary

Three top-level `_probe(code, cwd, *args)` bodies are AST-identical. The future sharing candidate copies the caller's environment, removes PYTHONPATH, sets PYTHONDONTWRITEBYTECODE and PYTHONUTF8 to "1", then returns the exact result of subprocess.run. Preserve argument order `[sys.executable, "-B", "-S", "-c", code, *map(str, args)]`, cwd, env, capture_output=True, text=True and timeout=30. No -I, return-code assertion, output assertion or exception translation is authorized. Live module bindings must be supplied at call time, with environment-copy/normalization before runner/executable attribute lookup and no mutation of the original environment.

This is distinct from the P3 asserting probe and the separate packaging_argument_family probe with conditional -S. Keep their bodies/policies local. The quality cohort deliberately expects exit1 for fabricated evidence; success-only runner sharing would change its contract.

Preserve all fixture construction, code-variable assignments/concatenations, parameter decorators, module/stem tables, copied-plugin aliases, model/provider absence checks, JSON/artifact expectations, provenance and refusal branches. No assertion/scenario collapse, contract-value change, fixture edit, runtime edit or relocation is proposed. Existing normalized golden fixtures remain the content authority and are untouched.

## Selection and later mapping

The current Linux job selects the full scripts/tests directory. Six focused-platform commands do not name these three files. Keep all workflows/selectors byte-for-byte unchanged. This observation is not hosted clearance or deferred A3 completion.

A later helper-only implementation must map each original case below to the identical node ID with unchanged complete scenario semantics and matching outcomes. New controls must be listed separately. Count equality alone is insufficient.

The ordinary source excerpts below record complete selected modules at the settled base so code-variable payloads, surrounding fixtures, artifact/JSON expectations and branches are all inspectable. They are evidence, not a permanent source-freeze test or new gate. The linked base remains the canonical source; no whole-suite inventory framework is introduced.

## consistency cohort

[Complete settled-base source](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_packaging_argument_consistency_family.py)

Exact collected nodes:

- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_consistency_family.py::test_identity_and_shared_private_api_monkeypatches[False-cross_doc_argument_consistency]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_consistency_family.py::test_identity_and_shared_private_api_monkeypatches[False-position_pair_register]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_consistency_family.py::test_identity_and_shared_private_api_monkeypatches[True-cross_doc_argument_consistency]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_consistency_family.py::test_identity_and_shared_private_api_monkeypatches[True-position_pair_register]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_consistency_family.py::test_named_runpy_does_not_call_main[cross_doc_argument_consistency]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_consistency_family.py::test_named_runpy_does_not_call_main[position_pair_register]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_consistency_family.py::test_copied_cross_doc_manifest_and_self_exclusion[recorded0]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_consistency_family.py::test_copied_cross_doc_manifest_and_self_exclusion[recorded1]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_consistency_family.py::test_copied_position_manifest_question_refusal_and_text_artifacts[recorded0]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_consistency_family.py::test_copied_position_manifest_question_refusal_and_text_artifacts[recorded1]`

Complete scenario and expectation inventory:

```python
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
```

## pattern cohort

[Complete settled-base source](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_packaging_argument_pattern_family.py)

Exact collected nodes:

- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_pattern_family.py::test_identity_and_shared_api_monkeypatches[False-agd_move_scan]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_pattern_family.py::test_identity_and_shared_api_monkeypatches[False-enthymeme_gapflag]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_pattern_family.py::test_identity_and_shared_api_monkeypatches[False-fallacy_scan]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_pattern_family.py::test_identity_and_shared_api_monkeypatches[False-warrant_probe]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_pattern_family.py::test_identity_and_shared_api_monkeypatches[True-agd_move_scan]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_pattern_family.py::test_identity_and_shared_api_monkeypatches[True-enthymeme_gapflag]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_pattern_family.py::test_identity_and_shared_api_monkeypatches[True-fallacy_scan]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_pattern_family.py::test_identity_and_shared_api_monkeypatches[True-warrant_probe]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_pattern_family.py::test_named_runpy_never_executes_main[agd_move_scan]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_pattern_family.py::test_named_runpy_never_executes_main[enthymeme_gapflag]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_pattern_family.py::test_named_runpy_never_executes_main[fallacy_scan]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_pattern_family.py::test_named_runpy_never_executes_main[warrant_probe]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_pattern_family.py::test_copied_manifest_keeps_recorded_identity_and_refuses_drift[agd_move_scan-observations-observations]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_pattern_family.py::test_copied_manifest_keeps_recorded_identity_and_refuses_drift[fallacy_scan-flags-rhetorical_move_flags]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_pattern_family.py::test_copied_manifest_keeps_recorded_identity_and_refuses_drift[warrant_probe-claims-warrant_coverage]`

Complete scenario and expectation inventory:

```python
"""Whole argument-pattern aliases in a copied plugin, without models or providers."""

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

STEMS = ("agd_move_scan", "enthymeme_gapflag", "fallacy_scan", "warrant_probe")
SCRIPTS = Path(__file__).resolve().parents[1]


def _probe(code, cwd, *args):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    return subprocess.run(
        [sys.executable, "-B", "-S", "-c", code, *map(str, args)],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=30,
    )


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
```

## quality cohort

[Complete settled-base source](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_packaging_argument_quality_family.py)

Exact collected nodes:

- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_quality_family.py::test_shared_module_and_monkeypatch_identity[False-argquality_dimension_profile]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_quality_family.py::test_shared_module_and_monkeypatch_identity[False-argument_certainty_calibration]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_quality_family.py::test_shared_module_and_monkeypatch_identity[True-argquality_dimension_profile]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_quality_family.py::test_shared_module_and_monkeypatch_identity[True-argument_certainty_calibration]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_quality_family.py::test_named_runpy_does_not_call_main[argquality_dimension_profile]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_quality_family.py::test_named_runpy_does_not_call_main[argument_certainty_calibration]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_quality_family.py::test_copied_quality_manifest_independent_bands_and_own_fingerprint[recorded-old-prompt]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_quality_family.py::test_copied_quality_manifest_independent_bands_and_own_fingerprint[None]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_quality_family.py::test_copied_certainty_support_locus_and_cli_artifact[False]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_quality_family.py::test_copied_certainty_support_locus_and_cli_artifact[True]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_quality_family.py::test_copied_certainty_manifest_does_not_invent_provenance[recorded0]`
- `plugins/setec-voiceprint/scripts/tests/test_packaging_argument_quality_family.py::test_copied_certainty_manifest_does_not_invent_provenance[recorded1]`

Complete scenario and expectation inventory:

```python
"""Whole quality/calibration alias behavior; synthetic inputs and offline judges only."""

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

STEMS = ("argquality_dimension_profile", "argument_certainty_calibration")
SCRIPTS = Path(__file__).resolve().parents[1]


def _probe(code, cwd, *args):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    return subprocess.run([sys.executable, "-B", "-S", "-c", code, *map(str, args)],
                          cwd=cwd, env=env, capture_output=True, text=True, timeout=30)


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
```

## Implementation consumption record

The separate preparation is independently cleared draft PR541 at exact `9aadfe0b34620d9e329ad0e042891cec05f3a1c7`. This subsequent implementation consumes those reviewed cases. All37 original node IDs map one-to-one to identical IDs; complete scenario functions, decorators, module globals, fixtures and literal/constructed payloads remain unchanged outside the local probe/import edits. Diagnostic comparison verified the three complete module trees under that exclusion; no source-freeze test was added.

Focused Windows execution passes46 cases:37 originals and9 separate behavioral controls. The controls protect original-environment independence, PYTHONPATH removal, forced settings, live module bindings and environment-copy-before-runner/executable lookup, exact argv/options, opaque result identity even for nonzero exits/output, and natural timeout-exception identity. Module-local synthetic namespaces avoid patching global os, sys or subprocess.

Whole-suite collection completes without errors at11011 nodes. The21 explicit shared-conftest caller modules plus9 new controls produce600 passes and40 failures. All631 existing outcomes match prior broad evidence; all40 failing nodes also reproduced in the earlier exact-base failed-case run. No cause beyond reproduction is assigned. All9 added cases pass. HEAD remained fixed during these runs.

This is bounded Windows/local evidence. The complete suite was collected, not executed again. No full-suite-green, other-platform execution, hosted CI or integration clearance is claimed. Runtime code, fixture bytes, workflows/selectors and divergent asserting/conditional-S probes remain unchanged.
