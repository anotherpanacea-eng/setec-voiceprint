#!/usr/bin/env python3
"""
manuscript_bigram_diff.py
Per-bigram POS-bigram diff: corpus A aggregate vs. corpus B aggregate.

Surfaces the POS-bigrams that differ most between two corpora at the
aggregate level. Use for register-level questions like "what does my
AI-collaborated post-2022 prose do differently than my pre-AI archive
at the syntactic-template level?"

Two aggregation strategies, mirroring ``bigram_diff.py``:

    --aggregation mean      Average per-essay distributions in each
                            corpus (each file weighted equally).
                            Compares cohort-typical distributions.

    --aggregation pooled    Sum counts within each corpus, normalize
                            once. Long files dominate within each
                            corpus. Compares aggregate distributions.

    --aggregation both      Run both, report side-by-side. Default.

Smoothing: same convention as ``bigram_diff.py``. Default
``--smoothing-alpha 1.0`` (Laplace add-1) for the pooled path; the
mean path uses ε smoothing on the averaged probabilities.

Frequency floor: ``--min-count`` filters out bigrams where neither
corpus reaches the threshold. Default 1.

Usage:
    python3 manuscript_bigram_diff.py \\
        --corpus-a path/to/corpus_a/ --label-a "post-2022" \\
        --corpus-b path/to/corpus_b/ --label-b "pre-AI"
    python3 manuscript_bigram_diff.py \\
        --corpus-a-files a1.txt a2.txt --corpus-b-files b1.txt b2.txt \\
        --label-a "personas A" --label-b "personas B" \\
        --top 25 --min-count 10 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from variance_audit import (  # type: ignore
    HAS_SPACY,
    normalize_pos_bigram_counts,
    pos_bigram_kl_contributions,
)
from bigram_diff import (  # type: ignore
    aggregate_cluster_mean,
    aggregate_cluster_pooled,
    list_cluster_paths,
    parse_cluster_files,
)

TASK_SURFACE = "smoothing_diagnosis"


# ---------- comparison ----------

def diff_pooled_corpora(
    a_counts: Counter[str],
    b_counts: Counter[str],
    *,
    alpha: float,
    min_count: int,
) -> list[dict[str, Any]]:
    keys = set(a_counts) | set(b_counts)
    a_probs = normalize_pos_bigram_counts(dict(a_counts), keys, alpha=alpha)
    b_probs = normalize_pos_bigram_counts(dict(b_counts), keys, alpha=alpha)
    if not a_probs or not b_probs:
        return []
    return pos_bigram_kl_contributions(
        a_probs,
        b_probs,
        target_counts=dict(a_counts),
        baseline_counts=dict(b_counts),
        min_count=min_count,
    )


def diff_mean_corpora(
    a_mean: dict[str, float],
    b_mean: dict[str, float],
    *,
    min_count: int,
    a_counts: Counter[str] | None = None,
    b_counts: Counter[str] | None = None,
) -> list[dict[str, Any]]:
    if not a_mean or not b_mean:
        return []
    return pos_bigram_kl_contributions(
        a_mean,
        b_mean,
        target_counts=dict(a_counts) if a_counts else None,
        baseline_counts=dict(b_counts) if b_counts else None,
        min_count=min_count,
    )


# ---------- rendering ----------

def render_table(
    rows: list[dict[str, Any]],
    *,
    direction: str,
    top: int,
    a_label: str,
    b_label: str,
    a_examples: dict[str, list[str]],
    b_examples: dict[str, list[str]],
) -> str:
    filt = [r for r in rows if (r["delta"] > 0) == (direction == "over")]
    head = filt[:top]
    if not head:
        return "_(no bigrams in this direction)_"
    src_examples = a_examples if direction == "over" else b_examples
    lines = [
        f"| Rank | Bigram | {a_label} % | {b_label} % | Δ pp | log₂(p/q) | KL contrib | Examples |",
        "|---:|:--|---:|---:|---:|---:|---:|:--|",
    ]
    for i, r in enumerate(head, 1):
        bigram = r["bigram"].replace("-", "+")
        ex = src_examples.get(r["bigram"], [])
        ex_text = "; ".join(f"`{e}`" for e in ex[:2]) if ex else ""
        lines.append(
            f"| {i} | `{bigram}` | {r['target_prob']*100:.2f} | {r['baseline_prob']*100:.2f} "
            f"| {r['delta']*100:+.2f} | {r['log2_ratio']:+.2f} | {r['kl_contrib']:+.5f} "
            f"| {ex_text} |"
        )
    return "\n".join(lines)


def render_report(
    a_label: str,
    b_label: str,
    a_loaded: list[Path],
    b_loaded: list[Path],
    a_skipped: list[Path],
    b_skipped: list[Path],
    pooled_diff: list[dict[str, Any]] | None,
    mean_diff: list[dict[str, Any]] | None,
    a_examples: dict[str, list[str]],
    b_examples: dict[str, list[str]],
    *,
    top: int,
    alpha: float,
    min_count: int,
) -> str:
    lines: list[str] = []
    lines.append(f"# POS-bigram diff: `{a_label}` vs. `{b_label}`")
    lines.append("")
    lines.append(f"**Task surface:** `{TASK_SURFACE}`")
    lines.append("")
    lines.append(
        f"`{a_label}`: {len(a_loaded)} files loaded"
        + (f", {len(a_skipped)} skipped." if a_skipped else ".")
    )
    lines.append(
        f"`{b_label}`: {len(b_loaded)} files loaded"
        + (f", {len(b_skipped)} skipped." if b_skipped else ".")
    )
    lines.append(
        f"Smoothing α = {alpha}, min count floor = {min_count}."
    )
    lines.append("")

    def _section(label: str, diff: list[dict[str, Any]]) -> None:
        kl_total = sum(r["kl_contrib"] for r in diff)
        lines.append(f"## Aggregation: **{label}**")
        lines.append("")
        lines.append(
            f"Aggregate KL(`{a_label}` ‖ `{b_label}`) ≈ {kl_total:.4f} "
            "(sum of contributions)."
        )
        lines.append("")
        lines.append(f"### Top {top} over-represented in `{a_label}` ({a_label} uses more)")
        lines.append("")
        lines.append(render_table(
            diff, direction="over", top=top,
            a_label=a_label, b_label=b_label,
            a_examples=a_examples, b_examples=b_examples,
        ))
        lines.append("")
        lines.append(f"### Top {top} over-represented in `{b_label}` ({b_label} uses more)")
        lines.append("")
        lines.append(render_table(
            diff, direction="under", top=top,
            a_label=a_label, b_label=b_label,
            a_examples=a_examples, b_examples=b_examples,
        ))
        lines.append("")

    if pooled_diff is not None:
        _section("pooled counts", pooled_diff)
    if mean_diff is not None:
        _section("per-file mean", mean_diff)
    return "\n".join(lines)


def render_json(
    a_label: str,
    b_label: str,
    a_loaded: list[Path],
    b_loaded: list[Path],
    a_skipped: list[Path],
    b_skipped: list[Path],
    pooled_diff: list[dict[str, Any]] | None,
    mean_diff: list[dict[str, Any]] | None,
    *,
    top: int,
    alpha: float,
    min_count: int,
) -> str:
    out: dict[str, Any] = {
        "task_surface": TASK_SURFACE,
        "label_a": a_label,
        "label_b": b_label,
        "corpus_a_files_loaded": [str(p) for p in a_loaded],
        "corpus_b_files_loaded": [str(p) for p in b_loaded],
        "corpus_a_files_skipped": [str(p) for p in a_skipped],
        "corpus_b_files_skipped": [str(p) for p in b_skipped],
        "smoothing_alpha": alpha,
        "min_count": min_count,
        "diffs": {},
    }
    if pooled_diff is not None:
        out["diffs"]["pooled"] = {
            "kl_total": sum(r["kl_contrib"] for r in pooled_diff),
            "rows": pooled_diff[:top * 2],
        }
    if mean_diff is not None:
        out["diffs"]["mean"] = {
            "kl_total": sum(r["kl_contrib"] for r in mean_diff),
            "rows": mean_diff[:top * 2],
        }
    return json.dumps(out, indent=2, default=float)


# ---------- main ----------

def _resolve_paths(dir_arg: str | None, files_arg: list[str] | None) -> list[Path]:
    if dir_arg:
        return list_cluster_paths(dir_arg)
    return [Path(p) for p in (files_arg or [])]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Per-bigram POS-bigram diff between two corpora."
    )
    a_grp = parser.add_mutually_exclusive_group(required=True)
    a_grp.add_argument("--corpus-a-dir", help="Directory of corpus A files.")
    a_grp.add_argument("--corpus-a-files", nargs="+", help="Explicit corpus A file paths.")
    b_grp = parser.add_mutually_exclusive_group(required=True)
    b_grp.add_argument("--corpus-b-dir", help="Directory of corpus B files.")
    b_grp.add_argument("--corpus-b-files", nargs="+", help="Explicit corpus B file paths.")
    parser.add_argument("--label-a", default="corpus_a", help="Label for corpus A.")
    parser.add_argument("--label-b", default="corpus_b", help="Label for corpus B.")
    parser.add_argument(
        "--aggregation",
        choices=("mean", "pooled", "both"),
        default="both",
        help="Aggregation strategy within each corpus (default: both).",
    )
    parser.add_argument(
        "--top", type=int, default=20,
        help="Top N bigrams per direction (default 20).",
    )
    parser.add_argument(
        "--min-count", type=int, default=1,
        help="Drop bigrams where neither corpus reaches this count (default 1, no filter).",
    )
    parser.add_argument(
        "--smoothing-alpha", type=float, default=1.0,
        help="Laplace add-α smoothing for pooled mode (default 1.0).",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    parser.add_argument("--out", help="Write output to file instead of stdout.")
    args = parser.parse_args()

    if not HAS_SPACY:
        print(
            "spaCy is required for POS tagging. Install via "
            "`pip install spacy && python -m spacy download en_core_web_sm`.",
            file=sys.stderr,
        )
        return 1

    a_paths = _resolve_paths(args.corpus_a_dir, args.corpus_a_files)
    b_paths = _resolve_paths(args.corpus_b_dir, args.corpus_b_files)
    if not a_paths:
        print("No files found for corpus A.", file=sys.stderr)
        return 1
    if not b_paths:
        print("No files found for corpus B.", file=sys.stderr)
        return 1

    # Parse each corpus once; aggregators reuse the cache.
    a_cache, a_examples, a_skipped = parse_cluster_files(a_paths)
    b_cache, b_examples, b_skipped = parse_cluster_files(b_paths)
    a_loaded = [p for p, _ in a_cache]
    b_loaded = [p for p, _ in b_cache]
    if not a_cache or not b_cache:
        print("One corpus yielded zero POS-bigrams.", file=sys.stderr)
        return 1

    pooled_diff = None
    mean_diff = None
    # Always compute pooled counts (cheap given the cache); used for the
    # mean-mode min_count floor even when only the mean diff is reported.
    a_pooled_counts, _, _, _, _ = aggregate_cluster_pooled(a_paths, cache=a_cache)
    b_pooled_counts, _, _, _, _ = aggregate_cluster_pooled(b_paths, cache=b_cache)

    if args.aggregation in ("pooled", "both"):
        pooled_diff = diff_pooled_corpora(
            a_pooled_counts, b_pooled_counts,
            alpha=args.smoothing_alpha, min_count=args.min_count,
        )

    if args.aggregation in ("mean", "both"):
        a_mean, _, _, _ = aggregate_cluster_mean(a_paths, cache=a_cache)
        b_mean, _, _, _ = aggregate_cluster_mean(b_paths, cache=b_cache)
        if not a_mean or not b_mean:
            print("Per-file-mean aggregation yielded zero distribution in one corpus.", file=sys.stderr)
            return 1
        mean_diff = diff_mean_corpora(
            a_mean, b_mean,
            min_count=args.min_count,
            a_counts=a_pooled_counts,
            b_counts=b_pooled_counts,
        )

    if a_skipped or b_skipped:
        skipped_msgs = []
        if a_skipped:
            skipped_msgs.append(
                f"corpus_a: {', '.join(p.name for p in a_skipped)}"
            )
        if b_skipped:
            skipped_msgs.append(
                f"corpus_b: {', '.join(p.name for p in b_skipped)}"
            )
        print(
            "Warning: could not read or POS-tag files: "
            + "; ".join(skipped_msgs)
            + ". Their bigrams are absent from the aggregate.",
            file=sys.stderr,
        )

    if args.json:
        output = render_json(
            args.label_a, args.label_b,
            a_loaded, b_loaded, a_skipped, b_skipped,
            pooled_diff, mean_diff,
            top=args.top, alpha=args.smoothing_alpha, min_count=args.min_count,
        )
    else:
        output = render_report(
            args.label_a, args.label_b,
            a_loaded, b_loaded, a_skipped, b_skipped,
            pooled_diff, mean_diff,
            a_examples, b_examples,
            top=args.top, alpha=args.smoothing_alpha, min_count=args.min_count,
        )

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
