"""Detached author-preparation CLIs preserve their private artifact contracts."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


NAMES = ("apply_owner_corrections", "normalize_author_registry", "prepare_author_document_adapter")


@pytest.fixture(scope="module")
def copied_preparation(tmp_path_factory):
    outside = tmp_path_factory.mktemp("author_preparation")
    plugin = outside / "plugin"
    shutil.copytree(
        Path(__file__).resolve().parents[2], plugin,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return outside, plugin


def _run(copied_preparation, *args):
    outside, _ = copied_preparation
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-S", *map(str, args)], cwd=outside, env=env,
        capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("package_first", [False, True])
def test_cold_import_and_monkeypatch_reach_real_main(copied_preparation, name, package_first):
    _, plugin = copied_preparation
    result = _run(copied_preparation, "-c", """
import contextlib, importlib, io, json, pickle, sys
scripts, name, package_first = sys.argv[1:]
sys.path.insert(0, scripts)
names = [name, 'setec.surfaces.' + name]
if package_first == 'True': names.reverse()
a, b = [importlib.import_module(value) for value in names]
assert a is b
capture = io.StringIO()
if name == 'apply_owner_corrections':
    assert pickle.loads(b'capply_owner_corrections\\nControlledFailure\\n.') is a.ControlledFailure
    a._run = lambda args: {'invented': True}
    args = ['manifest', 'corrections', '--out', 'output']
else:
    a.run = lambda args: {'invented': True}
    args = ['--persona', 'fixture-owner', '--output-dir', 'output']
    args += ['--json'] if name == 'normalize_author_registry' else ['--author-identity', 'Synthetic Author']
with contextlib.redirect_stdout(capture): assert b.main(args) == 0
assert json.loads(capture.getvalue()) == {'invented': True}
assert not any(name in sys.modules for name in ('spacy', 'torch', 'sentence_transformers'))
""", plugin / "scripts", name, package_first)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("runpy", [False, True])
def test_historical_help_outside_repository(copied_preparation, name, runpy):
    _, plugin = copied_preparation
    script = plugin / "scripts" / (name + ".py")
    args = [script, "--help"]
    if runpy:
        args = ["-c", "import runpy,sys; p=sys.argv[1]; sys.argv=[p,'--help']; runpy.run_path(p,run_name='__main__')", script]
    result = _run(copied_preparation, *args)
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_real_corrections_registry_adapter_round_trip(copied_preparation):
    outside, plugin = copied_preparation
    private = outside / "ai-prose-baselines-private"
    private.mkdir()
    exact = b"Invented author document.\tExact bytes.\r\n"
    (private / "piece.txt").write_bytes(exact)
    digest = "sha256:" + hashlib.sha256(exact).hexdigest()
    row = {
        "id": "row-1", "path": "piece.txt", "source_id": "source-1",
        "author": "Synthetic Author", "persona": "fixture-owner", "register": "personal",
        "era": "pre_chatgpt", "date_written": "2019-01-02", "ai_status": "pre_ai_human",
        "language_status": "native", "word_count": 6, "use": ["voice_profile"],
        "split": "baseline", "privacy": "private", "content_hash": digest,
        "source": "synthetic", "corpus_role": "identity_baseline",
        "consent_status": "author_consent", "acquired_via": "synthetic-test",
    }
    manifest, corrections, corrected = [private / name for name in ("source.jsonl", "corrections.jsonl", "corrected.jsonl")]
    original = (json.dumps(row) + "\n").encode()
    manifest.write_bytes(original)
    corrections.write_text(json.dumps({
        "schema": "setec-owner-correction/1", "match": {"id": "row-1"},
        "rewrite": {"register": "blog_essay", "era": "pre_ai_widespread"},
        "note": "invented fixture",
    }) + "\n", encoding="utf-8")
    scripts = plugin / "scripts"
    result = _run(copied_preparation, scripts / "apply_owner_corrections.py", manifest, corrections, "--out", corrected)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["applied"] == 1
    assert json.loads(corrected.read_bytes())["register"] == "blog_essay"
    assert manifest.read_bytes() == original and (private / "piece.txt").read_bytes() == exact
    shared = ["--source-manifest", f"synthetic={corrected}", "--register-map",
              "synthetic:blog_essay=essay.blog", "--persona", "fixture-owner"]
    registry, adapter = private / "registry", private / "adapter"
    result = _run(copied_preparation, scripts / "normalize_author_registry.py", *shared, "--output-dir", registry, "--json")
    assert result.returncode == 0, result.stderr
    records = (registry / "author_registry.jsonl").read_text(encoding="utf-8")
    assert json.loads(records)["canonical_register"] == "essay.blog"
    assert exact.decode().strip() not in records
    result = _run(copied_preparation, scripts / "prepare_author_document_adapter.py", *shared,
                  "--author-identity", "Synthetic Author", "--output-dir", adapter)
    assert result.returncode == 0, result.stderr
    prepared = json.loads((adapter / "draft_manifest.jsonl").read_bytes())
    assert (adapter / prepared["path"]).read_bytes() == exact
    assert prepared["content_hash"] == digest
    attestation = json.loads((adapter / "document_attestation.json").read_bytes())
    expected_hash = "sha256:" + hashlib.sha256((adapter / "draft_manifest.jsonl").read_bytes()).hexdigest()
    assert attestation["source_manifest_sha256"] == expected_hash
    assert attestation["allowed_ai_status"] == ["pre_ai_human"]
    refusals = [
        ("apply_owner_corrections", [manifest, corrections, "--out", corrections, "--replace"],
         "input, policy, or publication validation failed"),
        ("normalize_author_registry", [*shared, "--output-dir", outside / "forbidden", "--json"],
         "private input or policy validation failed"),
        ("prepare_author_document_adapter", [*shared, "--author-identity", "Wrong invented author",
                                             "--output-dir", private / "refused"],
         "private input or policy validation failed"),
    ]
    for name, args, message in refusals:
        result = _run(copied_preparation, scripts / (name + ".py"), *args)
        assert result.returncode == 2 and result.stdout == ""
        assert result.stderr == f"{name}: {message}\n"
