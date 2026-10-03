"""Argument relocation compatibility, using synthetic text and manifest/mock judges."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.check_zero_install import make_bare_copy

SCRIPTS = Path(__file__).resolve().parents[1]


def _probe(code, cwd, *args, stdlib_only=True):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-B", *(["-S"] if stdlib_only else []), "-c", code, *map(str, args)],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("package_first", (False, True))
def test_identity_and_shared_monkeypatches(tmp_path, package_first):
    result = _probe("""
import importlib, sys
sys.path.insert(0, sys.argv[1])
names = ['argument_decision_audit', 'setec.surfaces.argument_decision_audit']
if sys.argv[2] == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
assert a.ClaimLicense is importlib.import_module('claim_license').ClaimLicense
assert a.JudgeError is importlib.import_module('argument_judge').JudgeError
assert a.DERIVED_SIGNALS is importlib.import_module('argument_feature_schema').DERIVED_SIGNALS
for writer, reader, n in [(a,b,17), (b,a,23)]:
    writer.split_paragraphs = lambda text: [str(n)]
    assert reader.split_paragraphs('ignored') == [str(n)]
import argmove_profile
argmove_profile.argmove_vector = lambda text: {'_n_words': 1, 'patched': 2}
assert a.compute_reused_signals('text')['signals'] == {'patched': 2}
assert not any(n in sys.modules for n in ('spacy','torch','transformers','anthropic','openai'))
""", tmp_path, SCRIPTS, package_first)
    assert result.returncode == 0, result.stderr


def test_named_runpy_does_not_run_main(tmp_path):
    result = _probe("""
import sys, runpy, importlib
from pathlib import Path
path = Path(sys.argv[1])
sys.path.insert(0, str(path.parent))
module = importlib.import_module('setec.surfaces.argument_decision_audit')
module.main = lambda *args: (_ for _ in ()).throw(AssertionError('main called'))
original = sys.modules['__main__']
namespace = runpy.run_path(str(path), run_name='detached_argument')
assert namespace['TASK_SURFACE'] == module.TASK_SURFACE
assert sys.modules['__main__'] is original
""", tmp_path, SCRIPTS / "argument_decision_audit.py")
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="module")
def bare_plugin(tmp_path_factory):
    root = tmp_path_factory.mktemp("argument_copy")
    return root, make_bare_copy(root) / "scripts"


@pytest.mark.parametrize("empty", (False, True))
def test_copied_manifest_identity_and_unavailable_aggregate(bare_plugin, empty):
    root, scripts = bare_plugin
    target = root / "essay.txt"
    target.write_text("First argument.\n\nBecause evidence matters.\n\nTherefore act.", encoding="utf-8")
    paragraphs = [] if empty else [
        {"index": 0, "role": "thesis", "mode": "argumentation", "claim_ref": "c1", "guard_strength": "strong"},
        {"index": 1, "role": "support", "mode": "argumentation", "claim_ref": "c1", "guard_strength": "weak"},
        {"index": 2, "role": "proposal", "mode": "exposition"},
    ]
    manifest = root / "labels.json"
    recorded = {"model": "synthetic-judge", "model_revision": "old-revision", "prompt_version": "recorded-prompt"}
    manifest.write_text(json.dumps({"values": {"paragraphs": paragraphs}, "judge_identity": recorded}), encoding="utf-8")
    result = _probe(
        "import sys,runpy; path=sys.argv.pop(1); runpy.run_path(path,run_name='__main__')",
        root, scripts / "argument_decision_audit.py", target, "--judge", "manifest", "--judge-manifest", manifest, "--json",
    )
    assert result.returncode == 0, result.stderr
    envelope = json.loads(result.stdout)
    assert envelope["available"] is True and envelope["task_surface"] == "argument_decision_audit"
    results = envelope["results"]
    identity = results["judge"]["judge_identity"]
    assert identity["kind"] == "manifest"
    assert all(identity[k] == v for k, v in recorded.items())
    b5 = [c for c in results["contributions"] if c["bundle"] == "B5_collapse_dynamics"]
    assert b5 and all(c["contribution"] is None and not c["anchored"] for c in b5)
    if empty:
        assert results["aggregate"]["score"] is None
        assert results["aggregate"]["verdict_band"] == "unavailable"
        assert results["aggregate"]["n_signals_evaluated"] == 0
    else:
        assert results["aggregate"]["verdict_band"] == "uncalibrated"
        assert results["observed_signals"]["disappearing_guard_flag"] is True
    assert json.loads(target.with_suffix(".txt.argument.json").read_text(encoding="utf-8")) == envelope
    assert "Claim license" in target.with_suffix(".txt.argument.md").read_text(encoding="utf-8")


def test_copied_register_uses_explicit_scratch_baseline(bare_plugin):
    root, scripts = bare_plugin
    target = root / "registered.txt"
    target.write_text("A point.\n\nSupport it.\n\nPropose action.", encoding="utf-8")
    (root / "argument_register_baselines.yaml").write_text(
        "argument_register_baselines:\n  op-ed:\n    argumentation_share:\n"
        "      human: {mean: 0.700}\n      status: empirically_oriented\n"
        "      provenance: synthetic packaging test\n", encoding="utf-8",
    )
    result = _probe(
        "import sys,runpy; path=sys.argv.pop(1); runpy.run_path(path,run_name='__main__')",
        root, scripts / "argument_decision_audit.py", target, "--judge", "mock", "--register", "op-ed",
        "--baseline-dir", root, "--json", stdlib_only=False,
    )
    assert result.returncode == 0, result.stderr
    results = json.loads(result.stdout)["results"]
    assert results["target"]["register"]["source"] == str(root / "argument_register_baselines.yaml")
    by = {c["signal_key"]: c for c in results["contributions"]}
    assert by["argumentation_share"]["register_human_mean"] == 0.700
    assert by["argumentation_share"]["calibration_status"] == "empirically_oriented"


@pytest.mark.parametrize("bad_input", ("utf8", "manifest"))
def test_copied_invalid_input_refusals(bare_plugin, bad_input):
    root, scripts = bare_plugin
    target = root / "invalid.txt"
    target.write_bytes(b"\xff" if bad_input == "utf8" else b"A point.\n\nSupport.\n\nAction.")
    args = [target, "--json"]
    if bad_input == "manifest":
        manifest = root / "invalid.json"
        manifest.write_text("[]", encoding="utf-8")
        args += ["--judge", "manifest", "--judge-manifest", manifest]
    result = _probe(
        "import sys,runpy; path=sys.argv.pop(1); runpy.run_path(path,run_name='__main__')",
        root, scripts / "argument_decision_audit.py", *args,
    )
    assert result.returncode == (1 if bad_input == "utf8" else 2), result.stderr
    assert not result.stdout and "Traceback" not in result.stderr
    assert ("cannot read target" if bad_input == "utf8" else "judge construction failed") in result.stderr
