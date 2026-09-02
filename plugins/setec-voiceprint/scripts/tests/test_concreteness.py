#!/usr/bin/env python3
"""Regression tests for concreteness.py.

Pins the Brysbaert loader's contract: O(1) lookups, case-
insensitive, None for unknowns, gap computation, and graceful
handling of a missing data file. Uses an optional locally fetched
Brysbaert CSV when present and a small synthetic fixture for isolated
unit tests.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

import concreteness as c  # type: ignore


# Synthetic fixture: enough rows to exercise every code path without
# depending on the optional 40K-row CSV. The words and the ratings are
# INVENTED — per the excision spec's §4 "no upstream rating row is copied
# into tests" — and chosen so the gaps the tests assert are exact.
_FIXTURE_CSV = """word,is_bigram,conc_mean,conc_sd,unknown_count,total_raters,percent_known,subtlex_freq
gralnet,0,4.60,1.10,0,28,1.000000,250
vurnish,0,2.55,1.25,0,29,1.000000,180
themblish,0,1.55,1.05,2,30,0.933333,420
korvane,0,3.44,0.95,0,28,1.000000,310
plimber,0,4.80,0.30,0,30,1.000000,2400
quilth marrow,1,2.20,1.45,0,29,1.000000,0
"""


@pytest.fixture
def fixture_csv_path(tmp_path: Path) -> Path:
    """Write the synthetic fixture to a tempfile; return its path."""
    p = tmp_path / "brysbaert_fixture.csv"
    p.write_text(_FIXTURE_CSV, encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def clear_loader_cache():
    """Reset the lru_cache between tests so fixture-path swaps work."""
    c._load_concreteness_dict.cache_clear()
    yield
    c._load_concreteness_dict.cache_clear()


# --------------- Loader contract --------------------------------


def test_loader_reads_synthetic_fixture(fixture_csv_path: Path):
    """The loader should read every row of the fixture into a dict."""
    table = c._load_concreteness_dict(str(fixture_csv_path))
    assert len(table) == 6
    assert "gralnet" in table
    assert "quilth marrow" in table  # bigrams preserved


def test_loader_returns_floats(fixture_csv_path: Path):
    """Concreteness values must be float, not string."""
    table = c._load_concreteness_dict(str(fixture_csv_path))
    assert isinstance(table["gralnet"], float)
    assert table["gralnet"] == pytest.approx(4.60)


def test_loader_raises_filenotfound_with_install_hint(tmp_path: Path):
    """Missing CSV raises FileNotFoundError with operator guidance."""
    missing = tmp_path / "does_not_exist.csv"
    with pytest.raises(FileNotFoundError) as exc:
        c._load_concreteness_dict(str(missing))
    msg = str(exc.value)
    assert "fetch_brysbaert.py" in msg
    assert str(missing) in msg


def test_loader_caches_within_path(fixture_csv_path: Path):
    """Repeated loads from the same path return the cached dict."""
    a = c._load_concreteness_dict(str(fixture_csv_path))
    b = c._load_concreteness_dict(str(fixture_csv_path))
    assert a is b


# --------------- get_concreteness contract ----------------------


def test_known_word_returns_float(fixture_csv_path: Path):
    assert c.get_concreteness("gralnet", fixture_csv_path) == pytest.approx(4.60)


def test_unknown_word_returns_none(fixture_csv_path: Path):
    assert c.get_concreteness("xyzzy_made_up_word", fixture_csv_path) is None


def test_lookup_is_case_insensitive(fixture_csv_path: Path):
    """`GRALNET`, `Gralnet`, `gralnet` all resolve identically."""
    expected = c.get_concreteness("gralnet", fixture_csv_path)
    assert c.get_concreteness("Gralnet", fixture_csv_path) == expected
    assert c.get_concreteness("GRALNET", fixture_csv_path) == expected


def test_bigram_lookup_works(fixture_csv_path: Path):
    """Two-word phrases in the dataset resolve via the full phrase string."""
    assert c.get_concreteness("quilth marrow", fixture_csv_path) == pytest.approx(2.20)


# --------------- concreteness_gap contract ----------------------


def test_gap_known_pair(fixture_csv_path: Path):
    """gap(gralnet, vurnish) = |4.60 - 2.55| = 2.05."""
    gap = c.concreteness_gap("gralnet", "vurnish", fixture_csv_path)
    assert gap == pytest.approx(2.05, abs=0.01)


def test_gap_handles_unknown(fixture_csv_path: Path):
    """If either word is unknown, gap returns None (not 0, not an error)."""
    assert c.concreteness_gap("gralnet", "xyzzy", fixture_csv_path) is None
    assert c.concreteness_gap("xyzzy", "gralnet", fixture_csv_path) is None
    assert c.concreteness_gap("xyzzy", "yzxyz", fixture_csv_path) is None


def test_gap_is_symmetric(fixture_csv_path: Path):
    """gap(a, b) == gap(b, a)."""
    a = c.concreteness_gap("gralnet", "themblish", fixture_csv_path)
    b = c.concreteness_gap("themblish", "gralnet", fixture_csv_path)
    assert a == b
    assert a == pytest.approx(3.05, abs=0.01)


# --------------- vocab_size + is_loaded -------------------------


def test_vocab_size_matches_fixture(fixture_csv_path: Path):
    assert c.vocab_size(fixture_csv_path) == 6


def test_is_loaded_true_for_existing(fixture_csv_path: Path):
    assert c.is_loaded(fixture_csv_path) is True


def test_is_loaded_false_for_missing(tmp_path: Path):
    """is_loaded swallows FileNotFoundError gracefully."""
    missing = tmp_path / "missing.csv"
    assert c.is_loaded(missing) is False


def test_default_data_is_optional_and_unavailable_in_a_clean_checkout(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(c, "_DEFAULT_DATA_PATH", tmp_path / "missing.csv")
    assert c.is_available() is False
    with pytest.raises(FileNotFoundError, match="fetch_brysbaert.py"):
        c.vocab_size()


def test_is_available_accepts_a_locally_acquired_override(fixture_csv_path: Path):
    assert c.is_available(fixture_csv_path) is True


# --------------- present-but-unusable data ----------------------
#
# Reviewer P1-1 / P1-2 repro: `is_available()` only proved the file
# OPENED. A 0-byte or header-only CSV loaded to `{}` and reported
# available, so every AIC-8 detector emitted value 0.0 / status
# provisional and variance_audit banded it "within typical range" — a
# fail-OPEN. And a CSV with the wrong columns raised KeyError while a
# latin-1 byte raised UnicodeDecodeError straight out of `is_available()`,
# which the three `_skip_no_data` markers call at import time (so pytest
# COLLECTION errored, not just the run).


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


@pytest.mark.parametrize(
    "name,body",
    [
        ("empty.csv", ""),
        (
            "header_only.csv",
            "word,is_bigram,conc_mean,conc_sd,unknown_count,"
            "total_raters,percent_known,subtlex_freq\n",
        ),
        (
            "blank_ratings.csv",
            "word,conc_mean\ngralnet,\nvurnish,\nthemblish,\n",
        ),
        (
            "non_numeric_ratings.csv",
            "word,conc_mean\ngralnet,high\nvurnish,n/a\n",
        ),
    ],
)
def test_file_that_opens_but_carries_no_ratings_is_unavailable(
    tmp_path: Path, name: str, body: str,
):
    """Content, not openability, decides availability."""
    path = _write(tmp_path, name, body)
    assert c.is_available(path) is False
    assert c.availability_reason(path) == c.DATA_MALFORMED


def test_wrong_header_is_malformed_not_missing(tmp_path: Path):
    """A present-but-wrong CSV must NOT be reported as 'not installed'
    (that guidance tells the operator to re-fetch onto a file the
    fetcher already wrote) and must not raise KeyError."""
    path = _write(tmp_path, "wrong.csv", "term,rating\ngralnet,4.6\n")
    assert c.is_available(path) is False
    assert c.availability_reason(path) == c.DATA_MALFORMED
    guidance = c.unavailable_guidance(path)
    assert guidance != c.MISSING_DATA_GUIDANCE
    assert "present but unusable" in guidance


def test_non_utf8_bytes_are_malformed_not_a_traceback(tmp_path: Path):
    path = tmp_path / "latin1.csv"
    path.write_bytes(
        b"word,conc_mean\ngr\xe9lnet,4.60\n"
    )
    assert c.is_available(path) is False
    assert c.availability_reason(path) == c.DATA_MALFORMED


def test_directory_in_place_of_file_is_unreadable(tmp_path: Path):
    """A distinct reason from malformed: no amount of re-fetching or
    repairing the CONTENT fixes a directory sitting at the path."""
    path = tmp_path / "brysbaert_concreteness.csv"
    path.mkdir()
    assert c.is_available(path) is False
    assert c.availability_reason(path) == c.DATA_UNREADABLE
    assert "could not be read" in c.unavailable_guidance(path)


def test_explicit_load_of_a_malformed_file_raises_a_typed_error(tmp_path: Path):
    path = _write(tmp_path, "wrong.csv", "term,rating\ngralnet,4.6\n")
    with pytest.raises(c.ConcretenessDataError) as exc:
        c._load_concreteness_dict(str(path))
    assert exc.value.reason == c.DATA_MALFORMED


def test_the_three_unusable_reasons_give_three_different_guidances():
    texts = {
        c.guidance_for(c.DATA_NOT_INSTALLED),
        c.guidance_for(c.DATA_MALFORMED),
        c.guidance_for(c.DATA_UNREADABLE),
    }
    assert len(texts) == 3


def test_guidance_passes_an_unrecognized_reason_through():
    """variance_audit's summary line prints EVERY unavailable reason,
    including ones this module does not own (e.g. an unimportable AIC-8
    module), so an unknown reason must survive verbatim."""
    assert c.guidance_for("AIC-8 modules unimportable: boom") == (
        "AIC-8 modules unimportable: boom"
    )


def test_is_loaded_is_an_alias_for_is_available(tmp_path, fixture_csv_path: Path):
    assert c.is_loaded(fixture_csv_path) is c.is_available(fixture_csv_path) is True
    bad = _write(tmp_path, "header_only.csv", "word,conc_mean\n")
    assert c.is_loaded(bad) is c.is_available(bad) is False
