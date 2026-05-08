#!/usr/bin/env python3
"""
compare.py
Comparison-report generator for the stylometry oracle test (issue #4).

Reads the long-format CSVs produced by ``setec_to_stylo.py`` (SETEC
side) and ``run_stylo.R`` (stylo side), computes correlations and
discrepancy metrics, and writes a markdown report under
``scripts/oracle/results/oracle_comparison_report.md``.

Two phases are reported:

    Phase A: distance correctness on identical input.
        SETEC's frequency table fed into both SETEC's and stylo's
        Burrows-Delta / cosine distance computations. If SETEC's math
        is correct, the two distance matrices should be numerically
        equal (up to floating-point noise).

    Phase B: end-to-end on raw text.
        SETEC's full pipeline vs. stylo's full pipeline on the same
        raw .txt files. Differences here come from tokenization
        choices and feature-selection differences (SETEC's fixed
        Mosteller-Wallace + extensions wordlist vs. stylo's corpus-
        derived MFW). The Spearman rank correlation is the more
        informative number here than the absolute Pearson; we want
        the same authorship clusters to surface even if the absolute
        distances differ.

Usage:

    python3 scripts/oracle/compare.py
"""

from __future__ import annotations

import csv
import math
import statistics
import sys
from pathlib import Path
from typing import Sequence


HERE = Path(__file__).resolve().parent
OUTPUT_DIR = HERE / "results"


def load_long_csv(path: Path) -> dict[tuple[str, str, str], float]:
    """Read a long-format distances CSV and return
    {(doc_a, doc_b, metric): value}. Skips self-pairs (where doc_a ==
    doc_b) because their distance is trivially zero on both sides."""
    out: dict[tuple[str, str, str], float] = {}
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            a = row["doc_a"]
            b = row["doc_b"]
            metric = row["metric"]
            if a == b:
                continue
            out[(a, b, metric)] = float(row["value"])
    return out


def pair_values(
    setec: dict[tuple[str, str, str], float],
    stylo: dict[tuple[str, str, str], float],
    metric: str,
) -> tuple[list[float], list[float]]:
    """Return (setec_values, stylo_values) over the intersection of
    keys for the named metric. Order is deterministic (sorted by
    doc_a, doc_b)."""
    keys = sorted(
        k for k in setec
        if k[2] == metric and k in stylo
    )
    s = [setec[k] for k in keys]
    r = [stylo[k] for k in keys]
    return s, r


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    sx = statistics.stdev(xs)
    sy = statistics.stdev(ys)
    if sx == 0 or sy == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (len(xs) - 1)
    return cov / (sx * sy)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    rx = average_ranks(xs)
    ry = average_ranks(ys)
    return pearson(rx, ry)


def average_ranks(values: Sequence[float]) -> list[float]:
    """Mid-rank (average of ranks for ties) — same convention as
    scipy.stats.rankdata(method='average')."""
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i + 1
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[indexed[k][0]] = avg_rank
        i = j
    return ranks


def discrepancy_summary(
    s_vals: Sequence[float], r_vals: Sequence[float],
) -> dict[str, float | None]:
    """Mean absolute difference, max absolute difference, and the
    relative difference normalized by the stylo-side mean."""
    if not s_vals or not r_vals or len(s_vals) != len(r_vals):
        return {"mae": None, "max_abs_diff": None, "relative_mae": None}
    diffs = [abs(s - r) for s, r in zip(s_vals, r_vals)]
    mae = statistics.mean(diffs)
    max_abs = max(diffs)
    stylo_mean = statistics.mean(r_vals)
    rel = (mae / stylo_mean) if stylo_mean else None
    return {"mae": mae, "max_abs_diff": max_abs, "relative_mae": rel}


def render_phase_block(
    title: str,
    description: str,
    setec_path: Path,
    stylo_path: Path,
) -> str:
    lines: list[str] = [f"## {title}", "", description, ""]
    if not setec_path.exists():
        lines.append(f"_Setec output not found at `{setec_path}`._")
        return "\n".join(lines)
    if not stylo_path.exists():
        lines.append(
            f"_Stylo output not found at `{stylo_path}`. "
            f"Run `scripts/oracle/run_stylo.R` to generate it._"
        )
        return "\n".join(lines)

    setec = load_long_csv(setec_path)
    stylo = load_long_csv(stylo_path)
    metrics = sorted({k[2] for k in setec} & {k[2] for k in stylo})

    if not metrics:
        lines.append(
            "_No shared metrics between SETEC and stylo outputs. "
            "Check that both sides ran successfully._"
        )
        return "\n".join(lines)

    lines.append("| Metric | n pairs | Pearson r | Spearman ρ | Mean |Δ| | Max |Δ| | Relative MAE |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")

    for metric in metrics:
        s, r = pair_values(setec, stylo, metric)
        n = len(s)
        pear = pearson(s, r)
        spear = spearman(s, r)
        d = discrepancy_summary(s, r)
        lines.append(
            f"| `{metric}` | {n} | "
            f"{_fmt(pear, 4)} | {_fmt(spear, 4)} | "
            f"{_fmt(d['mae'], 6)} | {_fmt(d['max_abs_diff'], 6)} | "
            f"{_fmt(d['relative_mae'], 4)} |"
        )

    lines.append("")
    return "\n".join(lines)


def _fmt(value: float | None, digits: int) -> str:
    if value is None:
        return "--"
    if isinstance(value, float) and not math.isfinite(value):
        return "--"
    return f"{value:.{digits}f}"


CHAR_NGRAM_NS = (3, 4, 5)


def main() -> int:
    setec_csv = OUTPUT_DIR / "setec_distances.csv"
    stylo_a_csv = OUTPUT_DIR / "stylo_distances_phase_a.csv"
    stylo_b_csv = OUTPUT_DIR / "stylo_distances_phase_b.csv"

    if not setec_csv.exists():
        print(
            f"SETEC output not found at {setec_csv}. "
            "Run scripts/oracle/setec_to_stylo.py first.",
            file=sys.stderr,
        )
        return 1

    sections = [
        "# Stylometry oracle comparison: SETEC vs R `stylo`",
        "",
        "Generated by `scripts/oracle/compare.py`. Inputs:",
        "",
        f"- SETEC: `{setec_csv.name}` (from `setec_to_stylo.py`)",
        f"- Stylo Phase A: `{stylo_a_csv.name}` (from `run_stylo.R`)",
        f"- Stylo Phase B: `{stylo_b_csv.name}` (from `run_stylo.R`)",
        "",
        "Pearson r and Spearman ρ near 1.0 indicate close agreement; ",
        "Spearman is the appropriate metric when feature sets diverge ",
        "(Phase B). Relative MAE is mean absolute difference divided ",
        "by stylo's mean distance, so it's interpretable as a ",
        "proportion of the typical distance magnitude.",
        "",
        render_phase_block(
            "Phase A: distance correctness on identical input",
            (
                "Both sides operate on SETEC's function-word frequency "
                "table (135 words from the Mosteller-Wallace + extensions "
                "list). If SETEC's Burrows-Delta math matches stylo's, "
                "the two columns should agree to floating-point noise."
            ),
            setec_csv,
            stylo_a_csv,
        ),
        *(
            render_phase_block(
                f"Phase A char-ngrams (n={n}): distance correctness on identical input",
                (
                    f"SETEC's per-n character n-gram pipeline at n={n}. "
                    f"Both sides operate on the top-200 corpus-derived "
                    f"char-{n}-gram frequency table that SETEC's "
                    f"`stylometry_core.char_ngram_features` produces (per-n "
                    f"normalization, prefix stripped from feature names for "
                    f"the interchange CSV). If SETEC's distance math is "
                    f"correct, the agreement should match floating-point "
                    f"noise as in the function-word case."
                ),
                OUTPUT_DIR / f"setec_distances_char{n}.csv",
                OUTPUT_DIR / f"stylo_distances_phase_a_char{n}.csv",
            )
            for n in CHAR_NGRAM_NS
        ),
        render_phase_block(
            "Phase B: end-to-end on raw text",
            (
                "SETEC's full pipeline vs. stylo's full pipeline on the "
                "raw fixture. SETEC uses its fixed Mosteller-Wallace + "
                "extensions wordlist; stylo uses its corpus-derived MFW "
                "ranking at the same N. Disagreement here is expected "
                "(different feature sets) and is informative about how "
                "much the design choice matters for this fixture. "
                "Spearman rank correlation is the appropriate measure: "
                "we want the same authorship clusters to surface even if "
                "absolute distances differ."
            ),
            setec_csv,
            stylo_b_csv,
        ),
        "",
        "## Notes on interpretation",
        "",
        "**Phase A ≠ identity check.** Even on the same input frequency ",
        "table, SETEC's `setec_distances.csv` and stylo's Phase A ",
        "output are computed by independent code paths. They should ",
        "agree to ~1e-10 if both implement the same z-score-then-mean-",
        "absolute-difference formula. Any disagreement at the 1e-6 or ",
        "larger scale is a mathematical bug to investigate.",
        "",
        "**Phase B disagreement is expected.** SETEC's fixed-list ",
        "function-word selection differs from stylo's corpus-derived ",
        "MFW selection. On the Federalist Papers fixture, stylo's MFW ",
        "ranking weights words like `the`, `of`, `to`, `and`, `in` by ",
        "their actual frequency in this corpus; SETEC's list is the ",
        "Mosteller-Wallace + extensions vocabulary regardless of ",
        "corpus. The features overlap heavily but not completely. The ",
        "Spearman correlation is the right way to ask whether both ",
        "report the same Hamilton-vs-Madison cluster structure.",
        "",
        "**Limitations of this oracle test.** Six documents and 135 ",
        "function words is a small fixture; the comparison is a ",
        "correctness sanity check, not a calibration study. A larger ",
        "fixture with more authors and longer documents would tighten ",
        "the Pearson and Spearman estimates. The fixture is bounded ",
        "by the public-domain commit constraint.",
    ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "oracle_comparison_report.md"
    out_path.write_text("\n".join(sections), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
