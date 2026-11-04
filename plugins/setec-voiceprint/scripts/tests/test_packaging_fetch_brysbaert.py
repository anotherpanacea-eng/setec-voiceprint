"""Detached opt-in fetcher paths stay aligned with the canonical loader."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture(scope="module")
def copied_fetcher(tmp_path_factory):
    outside = tmp_path_factory.mktemp("fetch_brysbaert")
    plugin = outside / "plugin"
    shutil.copytree(
        Path(__file__).resolve().parents[2], plugin,
        ignore=shutil.ignore_patterns("__pycache__", "brysbaert_concreteness.csv", "brysbaert_concreteness.xlsx"),
    )
    return outside, plugin


def _run(copied_fetcher, *args):
    outside, _ = copied_fetcher
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-B", "-S", *map(str, args)], cwd=outside, env=env,
        capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("package_first", [False, True])
def test_cold_identity_hooks_reach_real_main_and_keep_source(copied_fetcher, package_first):
    outside, plugin = copied_fetcher
    output = outside / f"experiment-{package_first}.csv"
    result = _run(copied_fetcher, "-c", """
import importlib, sys
from pathlib import Path
scripts, package_first, output = sys.argv[1:]
sys.path.insert(0, scripts)
names = ['fetch_brysbaert', 'setec.surfaces.fetch_brysbaert']
if package_first == 'True': names.reverse()
a, b = [importlib.import_module(name) for name in names]
assert a is b
output = Path(output)
source = output.with_name(output.stem + '-download.xlsx')
calls = []
def invented_download(**kwargs):
    calls.append(kwargs)
    source.write_bytes(b'invented workbook transport fixture')
    return source
def invented_conversion(xlsx, csv, *, min_rows):
    assert xlsx == source and min_rows == 1
    csv.write_text('word,conc_mean\\ngralnet,4.6\\n', encoding='utf-8')
    return 1
a.download_xlsx = invented_download
a.convert_xlsx_to_csv = invented_conversion
assert b.main(['--output', str(output), '--min-rows', '1', '--keep-xlsx']) == 0
assert len(calls) == 1 and not source.exists()
assert output.with_suffix('.xlsx').read_bytes() == b'invented workbook transport fixture'
assert output.read_text(encoding='utf-8') == 'word,conc_mean\\ngralnet,4.6\\n'
assert not any(name in sys.modules for name in ('openpyxl', 'spacy', 'torch', 'requests'))
""", plugin / "scripts", package_first, output)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("runpy", [False, True])
def test_historical_help_without_install_or_workbook_reader(copied_fetcher, runpy):
    _, plugin = copied_fetcher
    script = plugin / "scripts/fetch_brysbaert.py"
    args = [script, "--help"]
    if runpy:
        args = ["-c", "import runpy,sys;p=sys.argv[1];sys.argv=[p,'--help'];runpy.run_path(p,run_name='__main__')", script]
    result = _run(copied_fetcher, *args)
    assert result.returncode == 0, result.stderr
    assert "--keep-xlsx" in result.stdout and "--min-rows" in result.stdout


def test_default_floor_matches_loader_and_refuses_before_download(copied_fetcher):
    _, plugin = copied_fetcher
    result = _run(copied_fetcher, "-c", """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from setec.surfaces import fetch_brysbaert as fetcher
import fetch_brysbaert as historical
expected = Path(sys.argv[1]).parent / 'data/brysbaert_concreteness.csv'
assert fetcher.data_dir() / expected.name == expected
assert fetcher.concreteness._DEFAULT_DATA_PATH == expected
def forbidden(**kwargs):
    raise AssertionError('download attempted before floor refusal')
historical.download_xlsx = forbidden
assert fetcher.main(['--min-rows', '1']) == 2
assert not expected.exists()
assert 'openpyxl' not in sys.modules and 'requests' not in sys.modules
""", plugin / "scripts")
    assert result.returncode == 0, result.stderr
    assert "below the loader's floor" in result.stderr
