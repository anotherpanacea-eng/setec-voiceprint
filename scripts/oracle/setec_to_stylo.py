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

    setec_function_word_freqs.csv      docs x function-words frequency table
                                       (relative frequencies, SETEC's fixed
                                       Mosteller-Wallace + extensions wordlist)
    setec_distances.csv                long-format pairwise distances
                                       (doc_a, doc_b, metric, value)
    setec_char{3,4,5}_freqs.csv        per-n char-ngram frequency tables
                                       (top-200 corpus-derived per n)
    setec_distances_char{3,4,5}.csv    per-n char-ngram pairwise distances
    setec_pos_trigram_freqs.csv        POS-trigram frequency table
                                       (top-300 corpus-derived)
    setec_distances_pos_trigrams.csv   POS-trigram pairwise distances
    setec_dep_ngram_freqs.csv          dependency n-gram (n=2,3) freq table
                                       (top-300 corpus-derived, single pool)
    setec_distances_dep_ngrams.csv     dep-n-gram pairwise distances
    parses/<doc_id>.tsv                per-document spaCy parse interchange
                                       (sent_idx, tok_idx, pos, dep) for the
                                       R side's independent n-gramming pass

The frequency-table files are inputs for ``run_stylo.R``'s Phase A
tests (distance correctness on identical input, one feature space
per file). The distance files are SETEC's own pairwise Delta +
cosine matrices, which the comparison script reads against stylo's
outputs. The parse TSVs let the R side rebuild POS-trigram and
dep-n-gram frequency tables independently of SETEC's n-gramming
code, so the n-gramming + frequency-table-construction code paths
are verified separately from the distance math (Phase A' in
``compare.py``).

The POS / dep pass requires spaCy; without it, those exports are
skipped and the rest of the oracle still runs.
"""

from __future__ import annotations

import csv
import math
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Sequence


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from stylometry_core import (  # type: ignore
    FUNCTION_WORDS,
    HAS_SPACY,
    _NLP,
    char_ngram_features,
    function_word_features,
    word_tokens,
)


# Number of most-frequent char-ngrams per n to use in the oracle test.
# Matches SETEC's --char-top default (per-n cap, applied separately to
# n=3, 4, 5) so the oracle reflects the production configuration.
CHAR_NGRAM_TOP_K = 200
CHAR_NGRAM_NS = (3, 4, 5)

# Top-K cap for POS-trigram and dependency-n-gram families, matching
# stylometry_core's family_caps for "pos_trigrams" and
# "dependency_ngrams". The oracle applies the same selection on the
# SETEC side so the R-side replication operates on a comparable
# feature set.
POS_DEP_TOP_K = 300
DEP_NGRAM_NS = (2, 3)


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


def char_ngram_table(
    docs: Sequence[dict[str, str]], n: int, top_k: int = CHAR_NGRAM_TOP_K,
) -> tuple[list[str], dict[str, dict[str, float]]]:
    """Return (sorted_ngram_list, doc_id -> {ngram: relative_freq}) for a
    given n. Selects the top-K most-frequent char-ngrams in the corpus
    (summed across documents); each document's frequencies are
    normalized within its own char-n-gram pool. Matches stylo's
    corpus-derived MFW selection convention but applied per-n
    separately, the same way ``stylometry_core.char_ngram_features``
    treats the per-n families internally.

    Returned ngram keys are bare grams (no ``chN:`` prefix); the
    prefix is SETEC's internal naming convention but isn't useful for
    the interchange CSV. stylo treats keys as opaque feature labels.
    """
    family_name = f"char_ngrams_{n}"
    per_doc_full: dict[str, dict[str, float]] = {}
    corpus_counts: Counter[str] = Counter()
    for doc in docs:
        feats_by_family = char_ngram_features(doc["text"], ns=(n,))
        family = feats_by_family.get(family_name, {})
        # Strip the ``chN:`` prefix and round-trip to absolute counts via
        # the same normalization total. We approximate counts from the
        # frequency dict by inverse-normalizing against the document's
        # implicit total. For corpus-aggregate selection, exact counts
        # are not needed -- relative-frequency sums approximate them
        # well enough to rank features.
        flat: dict[str, float] = {}
        for key, value in family.items():
            if key.startswith(f"ch{n}:"):
                bare = key[len(f"ch{n}:"):]
            else:
                bare = key
            flat[bare] = value
        per_doc_full[doc["id"]] = flat
        for k, v in flat.items():
            corpus_counts[k] += v
    # Top-K by aggregate relative frequency (proxy for total count;
    # corpus-uniform document weighting).
    top_ngrams = [k for k, _ in corpus_counts.most_common(top_k)]
    # Renormalize per-doc within the top-K subset so each row sums to
    # roughly 1.0, matching the convention stylo's dist.delta expects
    # on a frequency table.
    out: dict[str, dict[str, float]] = {}
    for doc_id, flat in per_doc_full.items():
        subset = {k: flat.get(k, 0.0) for k in top_ngrams}
        total = sum(subset.values())
        if total > 0:
            out[doc_id] = {k: v / total for k, v in subset.items()}
        else:
            out[doc_id] = {k: 0.0 for k in top_ngrams}
    return top_ngrams, out


def parse_documents(
    docs: Sequence[dict[str, str]],
) -> dict[str, list[tuple[int, int, str, str]]]:
    """Parse each document with spaCy and return per-document token
    records.

    Each record is ``(sent_idx, tok_idx_in_sent, pos, dep)``.
    ``is_space`` tokens are filtered out so the surviving sequence
    matches what ``stylometry_core.pos_trigram_features`` and
    ``dependency_ngram_features`` iterate over: their inner loop is
    ``[t.pos_ for t in sent if not t.is_space]`` (and the dep
    counterpart). ``tok_idx_in_sent`` is 0-indexed within the
    post-filter sentence, so it's a position within the sequence the
    n-gram windows actually slide over -- not the raw spaCy token
    offset.

    Returns an empty dict if spaCy is not available; the calling code
    is responsible for skipping the POS / dep oracle pass in that
    case.
    """
    if not HAS_SPACY or _NLP is None:
        return {}
    out: dict[str, list[tuple[int, int, str, str]]] = {}
    for doc in docs:
        parsed = _NLP(doc["text"])
        records: list[tuple[int, int, str, str]] = []
        for sent_idx, sent in enumerate(parsed.sents):
            tok_i = 0
            for tok in sent:
                if tok.is_space:
                    continue
                records.append((sent_idx, tok_i, tok.pos_, tok.dep_))
                tok_i += 1
        out[doc["id"]] = records
    return out


def write_parse_tsvs(
    parses: dict[str, list[tuple[int, int, str, str]]],
    parse_dir: Path,
) -> None:
    """Write one TSV per document at ``<parse_dir>/<doc_id>.tsv`` with
    columns ``sent_idx``, ``tok_idx``, ``pos``, ``dep``. The TSV is the
    interchange format the R side reads to do its own independent
    n-gramming, so the n-gramming + frequency-table-construction code
    paths can be verified independently of the spaCy parse itself."""
    parse_dir.mkdir(parents=True, exist_ok=True)
    for doc_id, records in parses.items():
        path = parse_dir / f"{doc_id}.tsv"
        with path.open("w", encoding="utf-8") as fh:
            fh.write("sent_idx\ttok_idx\tpos\tdep\n")
            for sent_idx, tok_idx, pos, dep in records:
                fh.write(f"{sent_idx}\t{tok_idx}\t{pos}\t{dep}\n")


def _pos_trigram_freqs(
    records: list[tuple[int, int, str, str]],
) -> dict[str, float]:
    """Replicate ``stylometry_core.pos_trigram_features`` on parsed
    records. Per-sentence reset, no n-gram windows cross sentence
    boundaries. Keys are ``pos:A-B-C`` (matching the production naming
    convention so the interchange CSVs preserve readable feature
    labels)."""
    counts: Counter[str] = Counter()
    total = 0
    by_sent: dict[int, list[str]] = {}
    for sent_idx, _tok_idx, pos, _dep in records:
        by_sent.setdefault(sent_idx, []).append(pos)
    for tags in by_sent.values():
        for a, b, c in zip(tags, tags[1:], tags[2:]):
            counts[f"pos:{a}-{b}-{c}"] += 1
            total += 1
    if total == 0:
        return {}
    return {k: v / total for k, v in counts.items()}


def _dep_ngram_freqs(
    records: list[tuple[int, int, str, str]],
    ns: tuple[int, ...] = DEP_NGRAM_NS,
) -> dict[str, float]:
    """Replicate ``stylometry_core.dependency_ngram_features``.
    Per-sentence reset; n-gram windows do not cross sentence
    boundaries. Single normalization pool spans all n-values (matching
    production), so ``dep2`` and ``dep3`` keys share one denominator
    per document."""
    counts: Counter[str] = Counter()
    total = 0
    by_sent: dict[int, list[str]] = {}
    for sent_idx, _tok_idx, _pos, dep in records:
        by_sent.setdefault(sent_idx, []).append(dep)
    for labels in by_sent.values():
        for n in ns:
            for gram in zip(*(labels[i:] for i in range(n))):
                counts[f"dep{n}:{'-'.join(gram)}"] += 1
                total += 1
    if total == 0:
        return {}
    return {k: v / total for k, v in counts.items()}


def pos_trigram_table(
    parses: dict[str, list[tuple[int, int, str, str]]],
    top_k: int = POS_DEP_TOP_K,
) -> tuple[list[str], dict[str, dict[str, float]]]:
    """Return (sorted_feature_list, doc_id -> {feature: relative_freq})
    for POS-trigrams. Selection mirrors the char-ngram pattern:
    top-K corpus-aggregate features, with each document's frequencies
    renormalized within the top-K subset so rows sum to ~1.0."""
    per_doc_full: dict[str, dict[str, float]] = {}
    corpus_counts: Counter[str] = Counter()
    for doc_id, records in parses.items():
        feats = _pos_trigram_freqs(records)
        per_doc_full[doc_id] = feats
        for k, v in feats.items():
            corpus_counts[k] += v
    top = [k for k, _ in corpus_counts.most_common(top_k)]
    out: dict[str, dict[str, float]] = {}
    for doc_id, feats in per_doc_full.items():
        subset = {k: feats.get(k, 0.0) for k in top}
        total = sum(subset.values())
        if total > 0:
            out[doc_id] = {k: v / total for k, v in subset.items()}
        else:
            out[doc_id] = {k: 0.0 for k in top}
    return top, out


def dep_ngram_table(
    parses: dict[str, list[tuple[int, int, str, str]]],
    top_k: int = POS_DEP_TOP_K,
    ns: tuple[int, ...] = DEP_NGRAM_NS,
) -> tuple[list[str], dict[str, dict[str, float]]]:
    """Return (sorted_feature_list, doc_id -> {feature: relative_freq})
    for dep n-grams. Same per-doc renormalization on the top-K subset
    as pos_trigram_table; ``dep2`` and ``dep3`` features share the
    same pool (matching production)."""
    per_doc_full: dict[str, dict[str, float]] = {}
    corpus_counts: Counter[str] = Counter()
    for doc_id, records in parses.items():
        feats = _dep_ngram_freqs(records, ns=ns)
        per_doc_full[doc_id] = feats
        for k, v in feats.items():
            corpus_counts[k] += v
    top = [k for k, _ in corpus_counts.most_common(top_k)]
    out: dict[str, dict[str, float]] = {}
    for doc_id, feats in per_doc_full.items():
        subset = {k: feats.get(k, 0.0) for k in top}
        total = sum(subset.values())
        if total > 0:
            out[doc_id] = {k: v / total for k, v in subset.items()}
        else:
            out[doc_id] = {k: 0.0 for k in top}
    return top, out


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

    # Char-ngram exports per n. SETEC separates char-ngrams into per-n
    # families (3, 4, 5) with per-n caps and per-n normalization; the
    # oracle test reflects that by writing one frequency table and one
    # distance matrix per n. Phase A still tests distance-math
    # correctness on identical input; Phase B in the R script compares
    # SETEC's per-n approach against stylo's unified char-ngram
    # treatment.
    print()
    for n in CHAR_NGRAM_NS:
        ngrams, char_freq = char_ngram_table(docs, n)
        char_z = z_score_columns(ngrams, char_freq)
        char_informative = informative_features(ngrams, char_freq)
        char_freq_csv = OUTPUT_DIR / f"setec_char{n}_freqs.csv"
        char_dist_csv = OUTPUT_DIR / f"setec_distances_char{n}.csv"
        write_freq_table_csv(ngrams, char_freq, char_freq_csv)
        write_distances_csv(
            docs, ngrams, char_freq, char_z, char_dist_csv,
        )
        print(
            f"Char {n}-grams: top-{CHAR_NGRAM_TOP_K} corpus-derived; "
            f"{len(char_informative)} informative across {len(ngrams)} "
            f"selected"
        )
        print(f"  wrote {char_freq_csv.name} + {char_dist_csv.name}")

    # POS-trigram and dependency-n-gram exports. Require spaCy: if not
    # available, skip with a notice rather than failing - the function-
    # word and char-ngram passes still run. The R side reads both the
    # frequency tables (for distance verification) and the parse TSVs
    # (for independent n-gramming verification).
    print()
    parses = parse_documents(docs)
    if not parses:
        print("POS / dep oracle pass: spaCy not available; skipping.")
        print("  Install spaCy + en_core_web_sm via .venv to enable.")
    else:
        parse_dir = OUTPUT_DIR / "parses"
        write_parse_tsvs(parses, parse_dir)
        rel = parse_dir.relative_to(REPO_ROOT)
        print(f"POS / dep oracle pass: wrote per-document parse TSVs to {rel}/")

        pos_features, pos_freq = pos_trigram_table(parses)
        pos_z = z_score_columns(pos_features, pos_freq)
        pos_informative = informative_features(pos_features, pos_freq)
        pos_freq_csv = OUTPUT_DIR / "setec_pos_trigram_freqs.csv"
        pos_dist_csv = OUTPUT_DIR / "setec_distances_pos_trigrams.csv"
        write_freq_table_csv(pos_features, pos_freq, pos_freq_csv)
        write_distances_csv(
            docs, pos_features, pos_freq, pos_z, pos_dist_csv,
        )
        print(
            f"  POS-trigrams: top-{POS_DEP_TOP_K} corpus-derived; "
            f"{len(pos_informative)} informative across {len(pos_features)} "
            f"selected"
        )
        print(f"    wrote {pos_freq_csv.name} + {pos_dist_csv.name}")

        dep_features, dep_freq = dep_ngram_table(parses)
        dep_z = z_score_columns(dep_features, dep_freq)
        dep_informative = informative_features(dep_features, dep_freq)
        dep_freq_csv = OUTPUT_DIR / "setec_dep_ngram_freqs.csv"
        dep_dist_csv = OUTPUT_DIR / "setec_distances_dep_ngrams.csv"
        write_freq_table_csv(dep_features, dep_freq, dep_freq_csv)
        write_distances_csv(
            docs, dep_features, dep_freq, dep_z, dep_dist_csv,
        )
        print(
            f"  Dep-n-grams (n={','.join(str(n) for n in DEP_NGRAM_NS)}): "
            f"top-{POS_DEP_TOP_K} corpus-derived; "
            f"{len(dep_informative)} informative across {len(dep_features)} "
            f"selected"
        )
        print(f"    wrote {dep_freq_csv.name} + {dep_dist_csv.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
