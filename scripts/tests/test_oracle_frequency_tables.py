#!/usr/bin/env python3
"""Regression tests for the oracle frequency-table denominator fix.

The oracle's char-ngram, POS-trigram, and dep-n-gram tables export
selected feature names. They must preserve each feature's full-family
relative frequency (the denominator stylometry_core.py uses) and must
NOT renormalize over the selected subset. Earlier versions did
renormalize, producing internally-consistent but non-production
tables, which made the Phase A agreement with R `stylo` only verify
the math on the altered table rather than on production-shaped
selected-feature vectors.

These tests guard against the renormalization sneaking back in.

The function-word path is intentionally not tested here: it uses the
fixed Mosteller-Wallace + extensions wordlist and never applied
top-K selection or renormalization, so it is unaffected by the bug.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORACLE = ROOT / "oracle"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ORACLE) not in sys.path:
    sys.path.insert(0, str(ORACLE))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover - direct unittest-style invocation
    pytest = None

from stylometry_core import char_ngram_features  # type: ignore
import setec_to_stylo as o  # type: ignore


PARSE_DIR = ROOT / "oracle" / "results" / "parses"
RESULTS_DIR = ROOT / "oracle" / "results"


def _load_parses() -> dict[str, list[tuple[int, int, str, str]]]:
    """Read the committed per-document parse TSVs into the same shape
    setec_to_stylo.parse_documents would produce. Lets the POS/dep
    tests run without spaCy installed -- the parses are committed
    derived outputs of the public-domain Federalist fixture."""
    parses: dict[str, list[tuple[int, int, str, str]]] = {}
    for p in sorted(PARSE_DIR.glob("*.tsv")):
        rows: list[tuple[int, int, str, str]] = []
        with p.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                rows.append(
                    (
                        int(row["sent_idx"]),
                        int(row["tok_idx"]),
                        row["pos"],
                        row["dep"],
                    )
                )
        parses[p.stem] = rows
    return parses


def _load_fixture_docs() -> list[dict[str, str]]:
    return o.load_fixture()


def test_char_ngram_oracle_preserves_production_denominators() -> None:
    """For each n in (3, 4, 5): exported oracle values must equal
    full-family relative frequencies from char_ngram_features. At
    least one row sum must be < 1.0 (guards against renormalization
    returning)."""
    docs = _load_fixture_docs()
    if not docs:
        if pytest is not None:
            pytest.skip("Federalist fixture not available")
        return

    for n in (3, 4, 5):
        family_name = f"char_ngrams_{n}"
        ngrams, table = o.char_ngram_table(docs, n)

        for doc in docs:
            doc_id = doc["id"]
            # Production full-family frequency dict, with the chN:
            # prefix stripped to match the oracle's interchange names.
            full = {
                k[len(f"ch{n}:"):] if k.startswith(f"ch{n}:") else k: v
                for k, v in char_ngram_features(
                    doc["text"], ns=(n,)
                ).get(family_name, {}).items()
            }
            for feat in ngrams:
                expected = full.get(feat, 0.0)
                actual = table[doc_id][feat]
                assert abs(actual - expected) < 1e-15, (
                    f"char-{n} {doc_id} {feat!r}: oracle={actual} "
                    f"production={expected}"
                )

        # At least one row sum should be < 1.0. The selected top-K
        # cannot cover every n-gram in every document, so some mass
        # is always outside the selection.
        row_sums = [sum(row.values()) for row in table.values()]
        assert min(row_sums) < 1.0, (
            f"char-{n}: every row sums to >= 1.0 ({row_sums}). "
            f"Selected-subset renormalization may have returned."
        )


def test_pos_trigram_oracle_preserves_production_denominators() -> None:
    """Exported POS-trigram values must equal full-family
    relative frequencies from _pos_trigram_freqs. At least one row
    sum must be < 1.0."""
    parses = _load_parses()
    if not parses:
        if pytest is not None:
            pytest.skip("Parse TSVs not available")
        return

    features, table = o.pos_trigram_table(parses)
    for doc_id, records in parses.items():
        full = o._pos_trigram_freqs(records)
        for feat in features:
            expected = full.get(feat, 0.0)
            actual = table[doc_id][feat]
            assert abs(actual - expected) < 1e-15, (
                f"pos {doc_id} {feat!r}: oracle={actual} "
                f"production={expected}"
            )

    row_sums = [sum(row.values()) for row in table.values()]
    assert min(row_sums) < 1.0, (
        f"pos: every row sums to >= 1.0 ({row_sums}). "
        f"Selected-subset renormalization may have returned."
    )


def test_dep_ngram_oracle_preserves_production_denominators() -> None:
    """Exported dep-n-gram values must equal full-family relative
    frequencies from _dep_ngram_freqs. At least one row sum must be
    < 1.0."""
    parses = _load_parses()
    if not parses:
        if pytest is not None:
            pytest.skip("Parse TSVs not available")
        return

    features, table = o.dep_ngram_table(parses)
    for doc_id, records in parses.items():
        full = o._dep_ngram_freqs(records)
        for feat in features:
            expected = full.get(feat, 0.0)
            actual = table[doc_id][feat]
            assert abs(actual - expected) < 1e-15, (
                f"dep {doc_id} {feat!r}: oracle={actual} "
                f"production={expected}"
            )

    row_sums = [sum(row.values()) for row in table.values()]
    assert min(row_sums) < 1.0, (
        f"dep: every row sums to >= 1.0 ({row_sums}). "
        f"Selected-subset renormalization may have returned."
    )


def test_committed_setec_and_stylo_freq_tables_match_cell_by_cell() -> None:
    """Phase A' acceptance: the committed setec_*_freqs.csv and
    stylo_*_freqs.csv files for POS-trigrams and dep-n-grams must
    agree cell-by-cell. R/stylo is not required to run this test --
    it reads the committed CSV outputs directly. If both sides are
    correctly preserving production denominators, this matches at
    floating-point precision; if either side reintroduces row
    renormalization, this fails immediately."""
    pairs = [
        ("setec_pos_trigram_freqs.csv", "stylo_pos_trigram_freqs.csv"),
        ("setec_dep_ngram_freqs.csv", "stylo_dep_ngram_freqs.csv"),
    ]
    for setec_name, stylo_name in pairs:
        setec_path = RESULTS_DIR / setec_name
        stylo_path = RESULTS_DIR / stylo_name
        if not setec_path.exists() or not stylo_path.exists():
            if pytest is not None:
                pytest.skip(
                    f"Committed oracle outputs not available: "
                    f"{setec_name} / {stylo_name}"
                )
            return

        setec_cells = _load_wide_csv(setec_path)
        stylo_cells = _load_wide_csv(stylo_path)
        common = set(setec_cells) & set(stylo_cells)
        setec_only = set(setec_cells) - set(stylo_cells)
        stylo_only = set(stylo_cells) - set(setec_cells)
        assert not setec_only, (
            f"{setec_name}: features present in setec but not stylo "
            f"({len(setec_only)}): {sorted(setec_only)[:5]}..."
        )
        assert not stylo_only, (
            f"{stylo_name}: features present in stylo but not setec "
            f"({len(stylo_only)}): {sorted(stylo_only)[:5]}..."
        )
        max_diff = max(
            abs(setec_cells[k] - stylo_cells[k]) for k in common
        )
        # Both sides write %.10f, so cell-level agreement should be
        # exact below the format precision.
        assert max_diff < 1e-9, (
            f"{setec_name} vs {stylo_name}: max |Δ| = {max_diff}"
        )


def _load_wide_csv(path: Path) -> dict[tuple[str, str], float]:
    """Read a wide-format frequency CSV and return
    {(doc_id, feature): value}."""
    out: dict[tuple[str, str], float] = {}
    with path.open(encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        feats = header[1:]
        for row in reader:
            doc_id = row[0]
            for feat, val in zip(feats, row[1:]):
                out[(doc_id, feat)] = float(val)
    return out
