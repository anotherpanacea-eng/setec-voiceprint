"""Historical oracle entry point keeps its resources and numerical reports."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture
def copied_reporter(tmp_path):
    plugin = tmp_path / "plugin"
    original = Path(__file__).resolve().parents[2]
    for relative in (
        ".claude-plugin/plugin.json", "scripts/setec/__init__.py",
        "scripts/setec/paths.py", "scripts/oracle/compare.py",
        "scripts/setec/oracle/__init__.py", "scripts/setec/oracle/compare.py",
    ):
        target = plugin / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original / relative, target)
    return tmp_path, plugin


def _run(copied_reporter, *args):
    outside, _ = copied_reporter
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-S", *map(str, args)], cwd=outside,
        env=env, capture_output=True, timeout=30,
    )


@pytest.mark.parametrize("legacy_name", ["compare", "oracle.compare"])
@pytest.mark.parametrize("package_first", [False, True])
def test_cold_imports_share_numerical_hooks(copied_reporter, legacy_name, package_first):
    _, plugin = copied_reporter
    result = _run(copied_reporter, "-c", """
import importlib, sys
from pathlib import Path
scripts = Path(sys.argv[1])
sys.path[:0] = [str(scripts), str(scripts / 'oracle')]
names = [sys.argv[2], 'setec.oracle.compare']
if sys.argv[3] == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
assert a.OUTPUT_DIR == scripts / 'oracle/results'
assert a.average_ranks([3, 1, 1]) == [3.0, 1.5, 1.5]
assert a.discrepancy_summary([1, 3], [1, 1]) == {'mae': 1, 'max_abs_diff': 2, 'relative_mae': 1}
a.pearson = lambda xs, ys: 0.125
assert b.spearman([1, 2, 3], [3, 2, 1]) == 0.125
assert not any(name in sys.modules for name in ('spacy', 'torch', 'numpy', 'requests'))
""", plugin / "scripts", legacy_name, package_first)
    assert result.returncode == 0, result.stderr


def _invocation(script, runpy):
    if runpy:
        return ["-c", "import runpy,sys;p=sys.argv[1];sys.argv=[p];runpy.run_path(p,run_name='__main__')", script]
    return [script]


@pytest.mark.parametrize("runpy", [False, True])
def test_missing_primary_input_refuses_without_report(copied_reporter, runpy):
    _, plugin = copied_reporter
    result = _run(copied_reporter, *_invocation(plugin / "scripts/oracle/compare.py", runpy))
    assert result.returncode == 1 and result.stdout == b""
    assert b"Run scripts/oracle/setec_to_stylo.py first." in result.stderr
    assert not (plugin / "scripts/oracle/results").exists()


@pytest.mark.parametrize("runpy", [False, True])
def test_default_report_uses_historical_csvs(copied_reporter, runpy):
    _, plugin = copied_reporter
    results = plugin / "scripts/oracle/results"
    results.mkdir()
    csv = "doc_a,doc_b,metric,value\na,b,delta,1\na,c,delta,2\nb,c,delta,3\na,a,delta,99\n"
    (results / "setec_distances.csv").write_bytes(csv.encode())
    (results / "stylo_distances_phase_a.csv").write_bytes(csv.encode())
    (results / "stylo_distances_phase_b.csv").write_bytes(
        b"doc_a,doc_b,metric,value\na,b,delta,3\na,c,delta,2\nb,c,delta,1\n"
    )
    for name, text in (
        ("setec_pos_trigram_freqs.csv", "doc_id,A,B\na,0.2,0.4\n"),
        ("stylo_pos_trigram_freqs.csv", "doc_id,A,C\na,0.2,0.8\n"),
    ):
        (results / name).write_bytes(text.encode())
    result = _run(copied_reporter, *_invocation(plugin / "scripts/oracle/compare.py", runpy))
    assert result.returncode == 0 and result.stderr == b""
    assert result.stdout.decode().strip() == f"Wrote {results / 'oracle_comparison_report.md'}"
    report = (results / "oracle_comparison_report.md").read_text(encoding="utf-8")
    assert "| `delta` | 3 | 1.0000 | 1.0000 | 0.000000 | 0.000000 | 0.0000 |" in report
    assert "| `delta` | 3 | -1.0000 | -1.0000 | 1.333333 | 2.000000 | 0.6667 |" in report
    assert "| 1 | 1 | 1 | -- | -- | 0.000000 | 0.000000 |" in report
    assert "_Setec output not found at" in report
    assert "**Phase B disagreement is expected.**" in report
    assert not (plugin / "scripts/setec/oracle/results").exists()
