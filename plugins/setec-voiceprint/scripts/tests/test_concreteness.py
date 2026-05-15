#!/usr/bin/env python3
"""Regression tests for concreteness.py.

Pins the Brysbaert loader's contract: O(1) lookups, case-
insensitive, None for unknowns, gap computation, and graceful
handling of a missing data file. Uses both the shipped Brysbaert
CSV (for integration sanity) and a small synthetic fixture (for
isolated unit tests).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import concreteness as c  # type: ignore


# Synthetic fixture: enough rows to exercise every code path
# without depending on the shipped 40K-row CSV. Each test that
# wants isolation uses this.
_FIXTURE_CSV = """word,is_bigram,conc_mean,conc_sd,unknown_count,total_raters,percent_known,subtlex_freq
machinery,0,4.75,1.10,0,28,1.000000,250
grief,0,2.70,1.25,0,29,1.000000,180
desire,0,1.70,1.05,2,30,0.933333,420
architecture,0,3.59,0.95,0,28,1.000000,310
table,0,4.90,0.30,0,30,1.000000,2400
zero tolerance,1,2.21,1.45,0,29,1.000000,0
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
    assert "machinery" in table
    assert "zero tolerance" in table  # bigrams preserved


def test_loader_returns_floats(fixture_csv_path: Path):
    """Concreteness values must be float, not string."""
    table = c._load_concreteness_dict(str(fixture_csv_path))
    assert isinstance(table["machinery"], float)
    assert table["machinery"] == pytest.approx(4.75)


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
    assert c.get_concreteness("machinery", fixture_csv_path) == pytest.approx(4.75)


def test_unknown_word_returns_none(fixture_csv_path: Path):
    assert c.get_concreteness("xyzzy_made_up_word", fixture_csv_path) is None


def test_lookup_is_case_insensitive(fixture_csv_path: Path):
    """`MACHINERY`, `Machinery`, `machinery` all resolve identically."""
    expected = c.get_concreteness("machinery", fixture_csv_path)
    assert c.get_concreteness("Machinery", fixture_csv_path) == expected
    assert c.get_concreteness("MACHINERY", fixture_csv_path) == expected


def test_bigram_lookup_works(fixture_csv_path: Path):
    """Two-word phrases in the dataset resolve via the full phrase string."""
    assert c.get_concreteness("zero tolerance", fixture_csv_path) == pytest.approx(2.21)


# --------------- concreteness_gap contract ----------------------


def test_gap_known_pair(fixture_csv_path: Path):
    """gap(machinery, grief) = |4.75 - 2.70| = 2.05."""
    gap = c.concreteness_gap("machinery", "grief", fixture_csv_path)
    assert gap == pytest.approx(2.05, abs=0.01)


def test_gap_handles_unknown(fixture_csv_path: Path):
    """If either word is unknown, gap returns None (not 0, not an error)."""
    assert c.concreteness_gap("machinery", "xyzzy", fixture_csv_path) is None
    assert c.concreteness_gap("xyzzy", "machinery", fixture_csv_path) is None
    assert c.concreteness_gap("xyzzy", "yzxyz", fixture_csv_path) is None


def test_gap_is_symmetric(fixture_csv_path: Path):
    """gap(a, b) == gap(b, a)."""
    a = c.concreteness_gap("machinery", "desire", fixture_csv_path)
    b = c.concreteness_gap("desire", "machinery", fixture_csv_path)
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


# --------------- Integration with the shipped CSV ---------------


def test_shipped_csv_loads_with_expected_size():
    """The framework's shipped Brysbaert CSV has 39,954 entries."""
    # Default path argument exercises the production code path.
    assert c.vocab_size() == 39954


def test_shipped_csv_known_words():
    """Sanity check the shipped data against canonical concreteness anchors.

    `grief` and `machinery` are the spec's running example pair for
    AIC-8 image-conjunction; their values are pinned upstream by
    Brysbaert 2014 and the framework must read them correctly.
    """
    assert c.get_concreteness("grief") == pytest.approx(2.70, abs=0.01)
    assert c.get_concreteness("machinery") == pytest.approx(4.75, abs=0.01)
    assert c.get_concreteness("table") == pytest.approx(4.90, abs=0.01)
