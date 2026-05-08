#!/usr/bin/env python3
"""
setec_to_stylo.py
SETEC side of the stylometry oracle test (issue #4).

For a small public-domain fixture corpus, computes SETEC's
function-word frequency table and pairwise distance matrices
(Burrows-style Delta and cosine), and writes them in a layout that
the companion ``run_stylo.R`` script can consume so its outputs are
directly comparable. The comparison report (``compare.py``) reads
both sides and produces a markdown summary.

The fixture is six Federalist Papers (3 Hamilton, 3 Madison) at
``scripts/test_data/federalist_oracle/``. The Hamilton-vs-Madison
binary is the canonical Mosteller-Wallace stylometric benchmark, so
both SETEC and stylo should produce distance matrices where the
within-author distances cluster together and the cross-author
distances open up. The oracle test is whether SETEC's *numbers*
match stylo's, not just the *ranking*.

Usage:

    python3 scripts/oracle/setec_to_stylo.py

Output files (under ``scripts/oracle/results/``):

    setec_function_word_freqs.csv   docs x function-words frequency table
                                    (relative frequencies, SETEC's fixed
                                    Mosteller-Wallace + extensions wordlist)
    setec_distances.csv             long-format pairwise distances
                                    (doc_a, doc_b, metric, value)

The first file is the input for ``run_stylo.R``'s Phase A test
(distance correctness on identical input). The second is SETEC's
own pairwise Delta + cosine matrix, which the comparison script
compares against stylo's outputs.
"""

from __future__ import annotations

import csv
import math
import statistics
import sys
from pathlib import Path
from typing import Sequence


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from stylometry_core import (  # type: ignore
    FUNCTION_WORDS,
    function_word_features,
    word_tokens,
)


FIXTURE_DIR = REPO_ROOT / "scripts" / "test_data" / "federalist_oracle"
OUTPUT_DIR = HERE / "results"


def load_fixture() -> list[dict[str, str]]:
    """Return [{id, path, text}, ...] for the fixture corpus, in
    deterministic id order. Skips the README."""
    paths = sorted(FIXTURE_DIR.glob("*.txt"))
    docs = []
    for p in paths:
        if p.name.lower().startswith("readme"):
            continue
        text = p.read_text(encoding="utf-8")
        docs.append({"id": p.stem, "path": str(p), "text": text})
    return docs


def function_word_table(docs: Sequence[dict[str, str]]) -> tuple[list[str], dict[str, dict[str, float]]]:
    """Return (sorted_word_list, doc_id -> {word: relative_freq}).

    Uses SETEC's fixed Mosteller-Wallace + extensions wordlist
    (``stylometry_core.FUNCTION_WORDS``) rather than corpus-derived
    MFW selection. Relative frequencies are token counts / total token
    count per document, which matches the convention SETEC's
    ``function_word_features`` uses and what stylo's ``dist.delta``
    expects as input when called with a frequency matrix directly.
    """
    sorted_words = sorted(FUNCTION_WORDS)
    table: dict[str, dict[str, float]] = {}
    for doc in docs:
        words = word_tokens(doc["text"])
        feats = function_word_features(words)
        # Ensure full coverage of the fixed wordlist (zeros for absent words).
        table[doc["id"]] = {w: feats.get(w, 0.0) for w in sorted_words}
    return sorted_words, table


def write_freq_table_csv(
    sorted_words: list[str],
    table: dict[str, dict[str, float]],
    out_path: Path,
) -> None:
    """Write the frequency table as a CSV that R can read directly.
    Row order: sorted by document id. Column order: function words
    sorted alphabetically. First column is the document id; subsequent
    columns are the per-word relative frequencies."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["doc_id"] + sorted_words)
        for doc_id in sorted(table):
            row = [doc_id] + [f"{table[doc_id][word]:.10f}" for word in sorted_words]
            w.writerow(row)


def z_score_columns(
    sorted_words: list[str],
    table: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    """Z-score each function-word column across all documents in the
    corpus. Matches stylo's ``dist.delta`` convention: the z-score
    population is the corpus itself, including all documents. Returns
    doc_id -> {word: z}.

    Columns with zero standard deviation produce z = 0 for every
    document (otherwise division by zero); this matches stylo's
    treatment of constant features.
    """
    doc_ids = sorted(table)
    z_table: dict[str, dict[str, float]] = {d: {} for d in doc_ids}
    for word in sorted_words:
        col = [table[d][word] for d in doc_ids]
        mu = statistics.mean(col)
        sd = statistics.stdev(col) if len(col) > 1 else 0.0
        for d in doc_ids:
            if sd == 0:
                z_table[d][word] = 0.0
            else:
                z_table[d][word] = (table[d][word] - mu) / sd
    return z_table


def burrows_delta(
    a_z: dict[str, float],
    b_z: dict[str, float],
    sorted_words: list[str],
    informative_words: list[str] | None = None,
) -> float:
    """Burrows' Delta: mean absolute difference between two
    corpus-z-scored frequency vectors over the *informative* features
    (features with non-zero variance across the corpus). Constant-SD
    features carry no information and are excluded from the average.

    This matches stylo's ``dist.delta`` convention and the production
    SETEC behavior in ``stylometry_core.family_distance`` (which only
    accumulates abs(z) when ``sd > 0`` and averages by the count of
    informative features). An earlier draft of this oracle harness
    averaged over all features in ``sorted_words``, including
    constant-zero columns from the Mosteller-Wallace + extensions
    list that don't appear in the fixture; that produced a systematic
    factor-of-(n_informative / n_total) underestimate. Both stylo and
    the production SETEC pipeline use the informative-only denominator;
    this oracle harness now matches.

    Mathematically: Δ(a, b) = (1/k) Σ_{i ∈ I} |z_a(i) - z_b(i)|
    where I is the set of informative features (k = |I|).
    """
    keys = informative_words if informative_words is not None else sorted_words
    if not keys:
        return 0.0
    s = sum(abs(a_z[w] - b_z[w]) for w in keys)
    return s / len(keys)


def cosine_dist(
    a: dict[str, float],
    b: dict[str, float],
    sorted_words: list[str],
) -> float:
    """1 - cosine similarity on the raw (non-z-scored) relative-
    frequency vectors. Matches stylo's ``dist.cosine`` default which
    operates on the frequency table directly. Returns 1.0 if either
    vector is the zero vector."""
    dot = sum(a[w] * b[w] for w in sorted_words)
    norm_a = math.sqrt(sum(a[w] ** 2 for w in sorted_words))
    norm_b = math.sqrt(sum(b[w] ** 2 for w in sorted_words))
    if norm_a == 0 or norm_b == 0:
        return 1.0
    return 1.0 - (dot / (norm_a * norm_b))


def informative_features(
    sorted_words: list[str],
    table: dict[str, dict[str, float]],
) -> list[str]:
    """Return the subset of ``sorted_words`` whose column has non-zero
    standard deviation across the corpus. Constant-SD features carry
    no discriminative information and are excluded from Burrows-Delta
    averaging (matching stylo and SETEC's production convention)."""
    doc_ids = sorted(table)
    out: list[str] = []
    for word in sorted_words:
        col = [table[d][word] for d in doc_ids]
        if len(col) > 1 and statistics.stdev(col) > 0:
            out.append(word)
    return out


def write_distances_csv(
    docs: list[dict[str, str]],
    sorted_words: list[str],
    freq_table: dict[str, dict[str, float]],
    z_table: dict[str, dict[str, float]],
    out_path: Path,
) -> None:
    """Pairwise Burrows-Delta and cosine distances in long format:
    (doc_a, doc_b, metric, value). Includes self-pairs (distance 0)
    so consumers can rebuild a square matrix without ambiguity.

    Burrows-Delta is averaged over the *informative* features only
    (those with non-zero SD across the corpus). Cosine distance uses
    all features in the original wordlist (zero columns contribute 0
    to dot product and 0 to norms; the result is unaffected by their
    inclusion in the loop, so we keep ``sorted_words`` for clarity)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc_ids = sorted(d["id"] for d in docs)
    informative = informative_features(sorted_words, freq_table)
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["doc_a", "doc_b", "metric", "value"])
        for a in doc_ids:
            for b in doc_ids:
                delta = burrows_delta(
                    z_table[a], z_table[b], sorted_words,
                    informative_words=informative,
                )
                cos = cosine_dist(freq_table[a], freq_table[b], sorted_words)
                w.writerow([a, b, "burrows_delta", f"{delta:.10f}"])
                w.writerow([a, b, "cosine_distance", f"{cos:.10f}"])


def main() -> int:
    docs = load_fixture()
    if not docs:
        print(f"No fixture documents found under {FIXTURE_DIR}", file=sys.stderr)
        return 1

    sorted_words, freq_table = function_word_table(docs)
    z_table = z_score_columns(sorted_words, freq_table)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    freq_csv = OUTPUT_DIR / "setec_function_word_freqs.csv"
    dist_csv = OUTPUT_DIR / "setec_distances.csv"
    write_freq_table_csv(sorted_words, freq_table, freq_csv)
    write_distances_csv(docs, sorted_words, freq_table, z_table, dist_csv)

    informative = informative_features(sorted_words, freq_table)
    print(f"Fixture: {len(docs)} documents.")
    for d in docs:
        n = len(word_tokens(d["text"]))
        print(f"  {d['id']}: {n} tokens")
    print(f"Function-word vocabulary (SETEC fixed list): {len(sorted_words)} words")
    print(f"  informative (non-constant SD across corpus): {len(informative)}")
    print(f"  excluded (zero SD; not in any fixture document): "
          f"{len(sorted_words) - len(informative)}")
    print(f"Wrote frequency table: {freq_csv}")
    print(f"Wrote pairwise distances: {dist_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
