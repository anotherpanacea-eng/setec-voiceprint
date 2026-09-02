"""Regression tests for fetch_brysbaert.py — the opt-in local acquirer.

Reviewer P1-1 repro: `convert_xlsx_to_csv` opened the TARGET path with
"w", wrote the header, then streamed rows with no temp-and-rename and no
post-write validation. A Ctrl-C, a full disk, or a non-float `Conc.M` cell
therefore left exactly a header-only / truncated CSV at the conventional
install path — the file shape `concreteness.is_available()` used to report
as available, which made every AIC-8 detector emit 0.0.

Reviewer P2-6 repro: `--keep-xlsx` parked the publisher's raw XLSX at
`plugins/setec-voiceprint/data/brysbaert_concreteness.xlsx`, which the
.gitignore did not cover (it listed only the .csv), and the rename sat in
a `finally` so it fired even when the conversion raised.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import concreteness as c  # type: ignore
import fetch_brysbaert as fb  # type: ignore


REPO_ROOT = Path(__file__).resolve().parents[4]

_HEADER = (
    "Word", "Bigram", "Conc.M", "Conc.SD", "Unknown",
    "Total", "Percent_known", "SUBTLEX",
)


def _write_xlsx(path: Path, n_rows: int) -> Path:
    """Write a Brysbaert-layout workbook with ``n_rows`` invented rows."""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(list(_HEADER))
    for i in range(n_rows):
        ws.append([f"gralnet{i}", 0, 4.60, 1.10, 0, 28, 1.0, 250])
    wb.save(path)
    return path


@pytest.fixture(autouse=True)
def clear_loader_cache():
    c._load_concreteness_dict.cache_clear()
    yield
    c._load_concreteness_dict.cache_clear()


def test_short_conversion_leaves_no_partial_file_at_the_target(tmp_path: Path):
    """The failure mode the reviewer reproduced: a truncated stream must
    not install a plausible-looking CSV at the conventional path."""
    xlsx = _write_xlsx(tmp_path / "src.xlsx", n_rows=3)
    out = tmp_path / "data" / "brysbaert_concreteness.csv"
    with pytest.raises(ValueError, match="refusing to install a partial"):
        fb.convert_xlsx_to_csv(xlsx, out)
    assert not out.exists()
    assert list(out.parent.glob("*.part")) == []


def test_short_conversion_preserves_an_existing_good_file(tmp_path: Path):
    """os.replace only after validation: a failed re-fetch must not
    destroy the copy the user already had."""
    out = tmp_path / "brysbaert_concreteness.csv"
    out.write_text("word,conc_mean\ngralnet,4.60\n", encoding="utf-8")
    assert c.is_available(out) is True
    xlsx = _write_xlsx(tmp_path / "src.xlsx", n_rows=3)
    with pytest.raises(ValueError):
        fb.convert_xlsx_to_csv(xlsx, out)
    c._load_concreteness_dict.cache_clear()
    assert c.is_available(out) is True
    assert c.get_concreteness("gralnet", out) == pytest.approx(4.60)


def test_successful_conversion_installs_a_loadable_table(tmp_path: Path):
    xlsx = _write_xlsx(tmp_path / "src.xlsx", n_rows=12)
    out = tmp_path / "data" / "brysbaert_concreteness.csv"
    n = fb.convert_xlsx_to_csv(xlsx, out, min_rows=10)
    assert n == 12
    assert c.is_available(out) is True
    assert c.vocab_size(out) == 12
    assert list(out.parent.glob("*.part")) == []


def test_keep_xlsx_keeps_only_a_successful_conversion(tmp_path: Path, monkeypatch):
    """The rename used to sit in a `finally`, so a failed run still parked
    the publisher's raw XLSX beside the CSV."""
    out = tmp_path / "data" / "brysbaert_concreteness.csv"
    out.parent.mkdir(parents=True)
    kept = out.with_suffix(".xlsx")

    def fake_download(url=None, dest=None):
        return _write_xlsx(tmp_path / "downloaded.xlsx", n_rows=3)

    monkeypatch.setattr(fb, "download_xlsx", fake_download)
    with pytest.raises(ValueError):
        fb.main(["--output", str(out), "--keep-xlsx"])
    assert not kept.exists()
    assert not out.exists()

    def fake_download_ok(url=None, dest=None):
        return _write_xlsx(tmp_path / "downloaded_ok.xlsx", n_rows=12)

    monkeypatch.setattr(fb, "download_xlsx", fake_download_ok)
    assert fb.main(
        ["--output", str(out), "--keep-xlsx", "--min-rows", "10"]
    ) == 0
    assert kept.exists()
    assert out.exists()


def test_local_xlsx_and_csv_paths_are_both_gitignored():
    """P2-6: .gitignore listed only the .csv, so `--keep-xlsx` produced a
    publisher artifact git was willing to commit."""
    for suffix in (".csv", ".xlsx"):
        relative = f"plugins/setec-voiceprint/data/brysbaert_concreteness{suffix}"
        assert subprocess.run(
            ["git", "check-ignore", "--quiet", "--no-index", relative],
            cwd=REPO_ROOT,
            check=False,
        ).returncode == 0, relative
