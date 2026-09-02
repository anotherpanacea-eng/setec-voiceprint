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


# ---- the fetcher must not install what the loader rejects -----------
#
# Re-review P2 repro: only the ROW floor was shared. `convert_xlsx_to_csv`
# never value-checked, so an XLSX carrying one cell of 7.5 installed a
# full-length table that `concreteness` then reported as `data_malformed`
# — and the malformed guidance named `fetch_brysbaert.py` as the remedy,
# i.e. the command that had just produced the file. `--min-rows 1` was the
# second way to manufacture a file the loader rejects.


def _write_xlsx_with_rating(path: Path, n_rows: int, bad_rating) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(list(_HEADER))
    for i in range(n_rows):
        ws.append([f"gralnet{i}", 0, 4.60, 1.10, 0, 28, 1.0, 250])
    ws.append(["vurnish", 0, bad_rating, 1.10, 0, 28, 1.0, 250])
    wb.save(path)
    return path


# NOTE: a float NaN does not survive the XLSX round trip — openpyxl stores
# it as an empty cell, which is an ABSENT rating both here and in the
# loader. A TEXT "nan"/"inf" cell does survive and is the shape that used
# to parse into the CSV and blow up downstream, so it is covered here.
@pytest.mark.parametrize(
    "bad_rating", [7.5, 0.5, 1000000, -1000000, "nan", "inf", "-inf"],
)
def test_out_of_scale_cell_aborts_the_conversion(tmp_path: Path, bad_rating):
    xlsx = _write_xlsx_with_rating(tmp_path / "src.xlsx", 12, bad_rating)
    out = tmp_path / "data" / "brysbaert_concreteness.csv"
    with pytest.raises(ValueError, match="refusing to install"):
        fb.convert_xlsx_to_csv(xlsx, out, min_rows=10)
    assert not out.exists()
    assert list(out.parent.glob("*.part")) == []


def test_out_of_scale_cell_preserves_a_previously_good_file(tmp_path: Path):
    out = tmp_path / "brysbaert_concreteness.csv"
    out.write_text("word,conc_mean\ngralnet,4.60\n", encoding="utf-8")
    xlsx = _write_xlsx_with_rating(tmp_path / "src.xlsx", 12, 7.5)
    with pytest.raises(ValueError):
        fb.convert_xlsx_to_csv(xlsx, out, min_rows=10)
    c._load_concreteness_dict.cache_clear()
    assert c.is_available(out) is True
    assert c.get_concreteness("gralnet", out) == pytest.approx(4.60)


def _write_duplicated_xlsx(path: Path, n_distinct: int, n_repeats: int) -> Path:
    """A Brysbaert-layout workbook with duplicated words.

    Real upstream tables carry case variants and repeats; the loader keys
    its dict by the lowercased word, so duplicates COLLAPSE and the file's
    real size is its distinct-word count.
    """
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(list(_HEADER))
    for repeat in range(n_repeats):
        for i in range(n_distinct):
            word = f"gralnet{i}" if repeat % 2 == 0 else f"GRALNET{i}"
            ws.append([word, 0, 4.60, 1.10, 0, 28, 1.0, 250])
    wb.save(path)
    return path


def test_duplicated_table_below_the_distinct_floor_is_refused(tmp_path: Path):
    """P3 repro: the fetcher counted usable ROWS while the loader counts
    DISTINCT lowercased words, so a duplicated upstream table of 12,000
    rows over 6,000 distinct words installed and then read as
    data_malformed — a third axis on which the docs' "a fetch that succeeds
    cannot leave a file the loader would reject" was absolute."""
    n_distinct = c.MIN_USABLE_ROWS // 2 + 1000  # 6,000 distinct
    xlsx = _write_duplicated_xlsx(
        tmp_path / "src.xlsx", n_distinct=n_distinct, n_repeats=2,
    )
    out = tmp_path / "data" / "brysbaert_concreteness.csv"
    with pytest.raises(ValueError, match="distinct usable word"):
        fb.convert_xlsx_to_csv(xlsx, out)
    assert not out.exists()
    assert list(out.parent.glob("*.part")) == []


@pytest.mark.parametrize("shape", ["unique", "duplicated"])
def test_everything_the_fetcher_installs_the_loader_accepts(tmp_path: Path, shape):
    """The claim the three docs make, asserted rather than asserted-about:
    a conversion that succeeds produces a file `is_available()` accepts at
    the conventional path — same floor, same value oracle, same counted
    quantity — for a unique table AND for one carrying case-variant
    duplicates."""
    if shape == "unique":
        xlsx = _write_xlsx(tmp_path / "src.xlsx", n_rows=c.MIN_USABLE_ROWS)
        expected_rows = c.MIN_USABLE_ROWS
    else:
        xlsx = _write_duplicated_xlsx(
            tmp_path / "src.xlsx", n_distinct=c.MIN_USABLE_ROWS, n_repeats=2,
        )
        expected_rows = c.MIN_USABLE_ROWS * 2
    out = tmp_path / "brysbaert_concreteness.csv"
    assert fb.convert_xlsx_to_csv(xlsx, out) == expected_rows
    c._load_concreteness_dict.cache_clear()
    assert c.availability_reason(out) is None
    assert c.vocab_size(out) == c.MIN_USABLE_ROWS


def test_min_rows_below_the_loader_floor_is_refused_at_the_conventional_path(
    tmp_path: Path, monkeypatch, capsys,
):
    conventional = tmp_path / "data" / "brysbaert_concreteness.csv"
    conventional.parent.mkdir(parents=True)
    monkeypatch.setattr(c, "_DEFAULT_DATA_PATH", conventional)

    called = []
    monkeypatch.setattr(
        fb, "download_xlsx", lambda *a, **k: called.append(1),
    )
    assert fb.main(["--output", str(conventional), "--min-rows", "1"]) == 2
    assert "below the loader's floor" in capsys.readouterr().err
    assert called == [], "must refuse before downloading anything"
    assert not conventional.exists()


def test_min_rows_override_still_works_off_the_conventional_path(
    tmp_path: Path, monkeypatch,
):
    monkeypatch.setattr(
        c, "_DEFAULT_DATA_PATH", tmp_path / "data" / "brysbaert_concreteness.csv",
    )
    out = tmp_path / "elsewhere" / "brysbaert_concreteness.csv"
    monkeypatch.setattr(
        fb, "download_xlsx",
        lambda *a, **k: _write_xlsx(tmp_path / "src.xlsx", n_rows=12),
    )
    assert fb.main(["--output", str(out), "--min-rows", "10"]) == 0
    assert out.exists()
