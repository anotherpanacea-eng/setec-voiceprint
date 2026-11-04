"""The copied Gmail map retains its metadata-only CLI and ordinary aliases."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def copied_map(tmp_path_factory):
    outside = tmp_path_factory.mktemp("gmail_map")
    plugin = outside / "plugin"
    shutil.copytree(Path(__file__).resolve().parents[2], plugin,
                    ignore=shutil.ignore_patterns("__pycache__"))
    return outside, plugin


def _run(copied_map, *args):
    outside, _ = copied_map
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-S", *map(str, args)], cwd=outside, env=env,
        capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("package_first", [False, True])
def test_cold_imports_share_result_error_and_main_hook(copied_map, package_first):
    _, plugin = copied_map
    result = _run(copied_map, "-c", """
import importlib, pickle, sys
scripts, package_first = sys.argv[1:]
sys.path.insert(0, scripts)
names = ['gmail_locator_map', 'setec.surfaces.gmail_locator_map']
if package_first == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
for name in ('LocatorMapResult', 'LocatorMapError'):
    assert pickle.loads(('cgmail_locator_map\\n' + name + '\\n.').encode()) is getattr(a, name)
a.run = lambda args: 47
assert b.main(['--output-dir', 'unused', '--map-out', 'unused']) == 47
assert not any(name in sys.modules for name in ('spacy', 'torch', 'sentence_transformers'))
""", plugin / "scripts", package_first)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("runpy", [False, True])
def test_historical_help_outside_repository(copied_map, runpy):
    _, plugin = copied_map
    script = plugin / "scripts/gmail_locator_map.py"
    args = [script, "--help"]
    if runpy:
        args = ["-c", "import runpy,sys;p=sys.argv[1];sys.argv=[p,'--help'];runpy.run_path(p,run_name='__main__')", script]
    result = _run(copied_map, *args)
    assert result.returncode == 0, result.stderr
    assert "--map-out" in result.stdout and "--allow-empty" in result.stdout


def test_actual_cli_sorts_metadata_and_keeps_gaps_on_stderr(copied_map):
    outside, plugin = copied_map
    private = outside / "ai-prose-baselines-private"
    private.mkdir()
    (private / "draft_manifest.jsonl").write_text('{"id":"b"}\n{"id":"a"}\n', encoding="utf-8")
    for index, stem in enumerate(("b", "a"), 1):
        (private / (stem + ".meta.json")).write_text(json.dumps({
            "author_corpus_entry_locator": "sha256:" + str(index) * 64,
        }), encoding="utf-8")
        (private / (stem + ".txt")).write_bytes(b"\xffInvented body must never be read")
    script = plugin / "scripts/gmail_locator_map.py"
    target = private / "map.jsonl"
    # Run the actual historical CLI while refusing every attempted body read.
    code = """
import runpy, sys
from pathlib import Path
original = Path.open
def metadata_only(self, *args, **kwargs):
    assert self.suffix != '.txt', 'attempted prose access'
    return original(self, *args, **kwargs)
Path.open = metadata_only
p, private, target = sys.argv[1:]
sys.argv = [p, '--output-dir', private, '--map-out', target]
runpy.run_path(p, run_name='__main__')
"""
    result = _run(copied_map, "-c", code, script, private, target)
    assert result.returncode == 0, result.stderr
    rows = [{"source_id": "a", "private_entry_locator": "sha256:" + "2" * 64},
            {"source_id": "b", "private_entry_locator": "sha256:" + "1" * 64}]
    expected = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode()
    assert target.read_bytes() == expected
    assert json.loads(result.stdout) == {
        "rows_written": 2, "output": str(target),
        "output_sha256": hashlib.sha256(expected).hexdigest(),
    }
    assert result.stderr == "" and len(result.stdout.splitlines()) == 1
    (private / "orphan.meta.json").write_text('{"author_corpus_entry_locator":"invented"}', encoding="utf-8")
    refused = private / "refused.jsonl"
    result = _run(copied_map, script, "--output-dir", private, "--map-out", refused)
    assert result.returncode == 2 and result.stdout == "" and not refused.exists()
    assert "locator_map_coverage_gap" in result.stderr and "orphan" in result.stderr
    assert "not prose-free" in result.stderr
    assert target.read_bytes() == expected
