"""Copied Oracle exporter preserves historical resources and interchange."""

import csv
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture
def copied_exporter(tmp_path):
    plugin = tmp_path / "plugin"
    shutil.copytree(Path(__file__).resolve().parents[2], plugin,
                    ignore=shutil.ignore_patterns("__pycache__", "test_data", "results", "tests"))
    return tmp_path, plugin


def _run(copied_exporter, *args):
    outside, _ = copied_exporter
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    return subprocess.run([sys.executable, "-B", "-S", *map(str, args)],
                          cwd=outside, env=env, capture_output=True, timeout=30)


@pytest.mark.parametrize("legacy_name", ["setec_to_stylo", "oracle.setec_to_stylo"])
@pytest.mark.parametrize("package_first", [False, True])
def test_cold_imports_share_export_hooks(copied_exporter, legacy_name, package_first):
    _, plugin = copied_exporter
    result = _run(copied_exporter, "-c", """
import importlib, sys
from pathlib import Path
scripts = Path(sys.argv[1])
sys.path[:0] = [str(scripts), str(scripts / 'oracle')]
names = [sys.argv[2], 'setec.oracle.setec_to_stylo']
if sys.argv[3] == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
assert a.FIXTURE_DIR == scripts / 'test_data/federalist_oracle'
assert a.OUTPUT_DIR == scripts / 'oracle/results'
assert a.REPO_ROOT == scripts.parent
a.load_fixture = lambda: []
assert b.main() == 1
assert not any(name in sys.modules for name in ('spacy', 'torch', 'numpy', 'requests'))
""", plugin / "scripts", legacy_name, package_first)
    assert result.returncode == 0, result.stderr


def _invoke(script, runpy):
    if runpy:
        return ["-c", "import runpy,sys;p=sys.argv[1];sys.argv=[p];runpy.run_path(p,run_name='__main__')", script]
    return [script]


@pytest.mark.parametrize("runpy", [False, True])
def test_missing_fixture_refuses_without_output(copied_exporter, runpy):
    _, plugin = copied_exporter
    result = _run(copied_exporter, *_invoke(plugin / "scripts/oracle/setec_to_stylo.py", runpy))
    assert result.returncode == 1 and result.stdout == b""
    assert b"No fixture documents found under" in result.stderr
    assert not (plugin / "scripts/oracle/results").exists()


def _documents(plugin):
    fixtures = plugin / "scripts/test_data/federalist_oracle"
    fixtures.mkdir(parents=True)
    (fixtures / "a.txt").write_bytes(b"the the cat")
    (fixtures / "b.txt").write_bytes(b"a dog and a cat")
    return fixtures


@pytest.mark.parametrize("runpy", [False, True])
def test_real_cli_keeps_frequencies_distances_and_parser_skip(copied_exporter, runpy):
    _, plugin = copied_exporter
    _documents(plugin)
    result = _run(copied_exporter, *_invoke(plugin / "scripts/oracle/setec_to_stylo.py", runpy))
    assert result.returncode == 0 and result.stderr == b""
    assert b"POS / dep oracle pass: spaCy not available; skipping." in result.stdout
    results = plugin / "scripts/oracle/results"
    with (results / "setec_function_word_freqs.csv").open(encoding="utf-8", newline="") as file:
        rows = {row["doc_id"]: row for row in csv.DictReader(file)}
    assert float(rows["a"]["the"]) == pytest.approx(2 / 3)
    assert float(rows["b"]["a"]) == pytest.approx(2 / 5)
    with (results / "setec_distances.csv").open(encoding="utf-8", newline="") as file:
        distances = {(row["doc_a"], row["doc_b"], row["metric"]): float(row["value"]) for row in csv.DictReader(file)}
    assert distances["a", "a", "burrows_delta"] == 0
    assert distances["a", "b", "burrows_delta"] == pytest.approx(math.sqrt(2))
    assert distances["a", "b", "burrows_delta"] == distances["b", "a", "burrows_delta"]
    assert distances["a", "b", "cosine_distance"] == 1
    assert not (results / "parses").exists()
    assert not (plugin / "scripts/setec/oracle/results").exists()


def test_injected_parse_export_keeps_interchange_and_relative_path(copied_exporter):
    _, plugin = copied_exporter
    _documents(plugin)
    result = _run(copied_exporter, "-c", """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from setec.oracle import setec_to_stylo as o
records = [(0, 0, 'DET', 'det'), (0, 1, 'NOUN', 'nsubj'), (0, 2, 'VERB', 'ROOT')]
o.parse_documents = lambda docs: {d['id']: records for d in docs}
raise SystemExit(o.main())
""", plugin / "scripts")
    assert result.returncode == 0 and result.stderr == b""
    relative = Path("scripts/oracle/results/parses")
    assert f"wrote per-document parse TSVs to {relative}/" in result.stdout.decode()
    results = plugin / "scripts/oracle/results"
    assert (results / "parses/a.tsv").read_text(encoding="utf-8") == (
        "sent_idx\ttok_idx\tpos\tdep\n0\t0\tDET\tdet\n0\t1\tNOUN\tnsubj\n0\t2\tVERB\tROOT\n"
    )
    with (results / "setec_dep_ngram_freqs.csv").open(encoding="utf-8", newline="") as file:
        row = next(csv.DictReader(file))
    assert float(row["dep2:det-nsubj"]) == pytest.approx(1 / 3)
    assert float(row["dep3:det-nsubj-ROOT"]) == pytest.approx(1 / 3)
