#!/usr/bin/env python3
"""
bigram_diff.py
Per-bigram POS-bigram diff: single target document vs. a comparison cluster.

Surfaces the POS-bigrams that differ most between one document and a
cluster of comparator documents. Use when the target's whole-document
KL against a baseline elevates and you want to know *what* the
syntactic difference is, not just its size.

Two cluster aggregation strategies:

    --cluster-mode mean     Average per-essay distributions (each cluster
                            file weighted equally regardless of length).
                            Good for "what does the cluster typically do?"
                            when essay lengths vary.

    --cluster-mode pooled   Sum counts across cluster files, normalize
                            once. Long files dominate. Good for "what is
                            the cluster doing on aggregate?" or when the
                            cluster represents a single source.

    --cluster-mode both     Run both, report side-by-side. Default.
                            Cheap; shows where the two converge or diverge.

Smoothing: see ``--smoothing-alpha`` and the docstring of
``pos_bigram_kl_contributions``. Default 1.0 (Laplace add-1, matches
``variance_audit.py``'s POS-bigram KL/JSD convention) for the pooled
path; the mean path uses ε smoothing on the averaged probabilities
because count-level smoothing of an averaged distribution is not
well-defined.

Frequency floor: ``--min-count`` filters out bigrams where neither
side reaches the threshold count. Suppresses tail noise from rare
bigrams. Default 1 (no filter).

Usage:
    python3 bigram_diff.py target.txt --cluster-dir comparators/
    python3 bigram_diff.py target.md --cluster-files a.txt b.txt c.txt
    python3 bigram_diff.py target.md --cluster-dir comparators/ \\
        --top 25 --min-count 5 --smoothing-alpha 0.5 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# The math, smoothing, and POS tagger live in variance_audit.
from claim_license import ClaimLicense  # type: ignore
from output_schema import build_baseline_metadata, build_output  # type: ignore
from variance_audit import (  # type: ignore
    HAS_SPACY,
    normalize_pos_bigram_counts,
    pos_bigram_distribution,
    pos_bigram_kl_contributions,
)

TASK_SURFACE = "smoothing_diagnosis"
TOOL_NAME = "bigram_diff"
SCRIPT_VERSION = "1.0"


# ---------- I/O helpers ----------

def list_cluster_paths(cluster_dir: str | Path) -> list[Path]:
    """Return cluster corpus files (.txt + .md), excluding READMEs and dotfiles."""
    base = Path(cluster_dir)
    paths = sorted(base.glob("*.txt")) + sorted(base.glob("*.md"))
    return [
        p for p in paths
        if not p.name.lower().startswith("readme")
        and not p.name.startswith(".")
    ]


def file_bigram_counts(path: Path) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Return (counts, examples) for one file. Skips empty results.

    examples maps bigram -> up to 3 token-pair example strings, useful
    for human readers in markdown output.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    dist = pos_bigram_distribution(text)
    if not dist:
        return {}, {}
    counts = dist.get("counts", {})
    examples = _collect_examples(text)
    return counts, examples


def _collect_examples(text: str, per_bigram: int = 3) -> dict[str, list[str]]:
    """Tag once and capture up to ``per_bigram`` token-pair examples per bigram."""
    if not HAS_SPACY:
        return {}
    from variance_audit import _NLP  # type: ignore
    if _NLP is None:
        return {}
    out: dict[str, list[str]] = {}
    doc = _NLP(text)
    for sent in doc.sents:
        toks = [(t.pos_, t.text) for t in sent if not t.is_space]
        for i in range(len(toks) - 1):
            key = f"{toks[i][0]}-{toks[i + 1][0]}"
            ex_list = out.setdefault(key, [])
            if len(ex_list) < per_bigram:
                ex_list.append(f"{toks[i][1]} {toks[i + 1][1]}")
    return out


# ---------- aggregation ----------

def parse_cluster_files(
    cluster_paths: list[Path],
) -> tuple[list[tuple[Path, dict[str, int]]], dict[str, list[str]], list[Path]]:
    """Parse every cluster file once.

    Returns ``(per_file_counts, examples, skipped)`` where
    ``per_file_counts`` is ``[(path, counts), ...]`` for files that
    POS-tagged successfully, ``examples`` aggregates token-pair
    examples across the cluster (capped at three per bigram), and
    ``skipped`` lists paths that could not be read or tagged. The two
    aggregator helpers below consume this cache; the script should
    parse files once and call both aggregators against the cache when
    running in ``--cluster-mode both`` (or ``--aggregation both``).
    """
    per_file: list[tuple[Path, dict[str, int]]] = []
    examples: dict[str, list[str]] = {}
    skipped: list[Path] = []
    for p in cluster_paths:
        try:
            counts, ex = file_bigram_counts(p)
        except Exception:
            skipped.append(p)
            continue
        if not counts:
            skipped.append(p)
            continue
        per_file.append((p, counts))
        for k, v in ex.items():
            slot = examples.setdefault(k, [])
            for tok in v:
                if len(slot) < 3 and tok not in slot:
                    slot.append(tok)
    return per_file, examples, skipped


def aggregate_cluster_pooled(
    cluster_paths: list[Path],
    *,
    cache: list[tuple[Path, dict[str, int]]] | None = None,
) -> tuple[Counter[str], dict[str, list[str]], int, list[Path], list[Path]]:
    """Sum counts across cluster files. Long files dominate.

    If ``cache`` is provided (typically from ``parse_cluster_files``),
    skip re-reading and reuse the precomputed per-file counts. Returns
    ``(pooled_counts, examples, total, loaded, skipped)``. Examples
    and skipped are empty when a cache is supplied; the caller is
    expected to carry those alongside.
    """
    if cache is not None:
        pooled: Counter[str] = Counter()
        loaded: list[Path] = []
        for path, counts in cache:
            pooled.update(counts)
            loaded.append(path)
        return pooled, {}, sum(pooled.values()), loaded, []
    per_file, examples, skipped = parse_cluster_files(cluster_paths)
    pooled = Counter()
    loaded = []
    for path, counts in per_file:
        pooled.update(counts)
        loaded.append(path)
    return pooled, examples, sum(pooled.values()), loaded, skipped


def aggregate_cluster_mean(
    cluster_paths: list[Path],
    *,
    cache: list[tuple[Path, dict[str, int]]] | None = None,
) -> tuple[dict[str, float], dict[str, list[str]], list[Path], list[Path]]:
    """Average per-file probabilities (each file weighted equally).

    If ``cache`` is provided, skip re-reading and reuse the precomputed
    per-file counts. Examples and skipped are empty when a cache is
    supplied.
    """
    if cache is not None:
        per_file_probs: list[dict[str, float]] = []
        loaded: list[Path] = []
        for path, counts in cache:
            total = sum(counts.values())
            if total == 0:
                continue
            per_file_probs.append({k: c / total for k, c in counts.items()})
            loaded.append(path)
        skipped: list[Path] = []
        examples: dict[str, list[str]] = {}
    else:
        per_file, examples, skipped = parse_cluster_files(cluster_paths)
        per_file_probs = []
        loaded = []
        for path, counts in per_file:
            total = sum(counts.values())
            if total == 0:
                continue
            per_file_probs.append({k: c / total for k, c in counts.items()})
            loaded.append(path)
    if not per_file_probs:
        return {}, examples, loaded, skipped
    vocab: set[str] = set()
    for d in per_file_probs:
        vocab.update(d.keys())
    mean_probs = {
        k: sum(d.get(k, 0.0) for d in per_file_probs) / len(per_file_probs)
        for k in vocab
    }
    return mean_probs, examples, loaded, skipped


# ---------- comparison ----------

def diff_pooled(
    target_counts: dict[str, int],
    cluster_counts: dict[str, int],
    *,
    alpha: float,
    min_count: int,
) -> list[dict[str, Any]]:
    """Pooled-counts diff with Laplace add-α smoothing at the count level."""
    keys = set(target_counts) | set(cluster_counts)
    target_probs = normalize_pos_bigram_counts(target_counts, keys, alpha=alpha)
    cluster_probs = normalize_pos_bigram_counts(cluster_counts, keys, alpha=alpha)
    if not target_probs or not cluster_probs:
        return []
    return pos_bigram_kl_contributions(
        target_probs,
        cluster_probs,
        target_counts=target_counts,
        baseline_counts=cluster_counts,
        min_count=min_count,
    )


def diff_mean(
    target_counts: dict[str, int],
    cluster_mean_probs: dict[str, float],
    *,
    min_count: int,
) -> list[dict[str, Any]]:
    """Per-essay-mean diff. ε smoothing on the probability vectors.

    NOTE on min-count behaviour: ``min_count`` is forwarded to
    ``pos_bigram_kl_contributions`` but does not filter in this path
    because ``baseline_counts`` is not supplied (the mean
    aggregator returns probabilities, not counts). The
    ``manuscript_bigram_diff.py`` script always computes pooled
    counts alongside the mean for exactly this reason; the
    single-doc path defers that polish until the discrepancy
    matters in real use. Callers passing ``--min-count > 1`` to
    ``bigram_diff.py --cluster-mode mean`` should be aware that the
    floor will not fire.
    """
    if not target_counts or not cluster_mean_probs:
        return []
    target_total = sum(target_counts.values())
    target_probs = {k: c / target_total for k, c in target_counts.items()}
    return pos_bigram_kl_contributions(
        target_probs,
        cluster_mean_probs,
        target_counts=target_counts,
        baseline_counts=None,
        min_count=min_count,
    )


# ---------- rendering ----------

def render_table(
    rows: list[dict[str, Any]],
    *,
    direction: str,
    top: int,
    examples: dict[str, list[str]],
) -> str:
    """direction: 'over' (positive delta) or 'under' (negative delta)."""
    filt = [r for r in rows if (r["delta"] > 0) == (direction == "over")]
    head = filt[:top]
    if not head:
        return "_(no bigrams in this direction)_"
    lines = [
        "| Rank | Bigram | Target % | Cluster % | Δ pp | log₂(p/q) | KL contrib | Examples |",
        "|---:|:--|---:|---:|---:|---:|---:|:--|",
    ]
    for i, r in enumerate(head, 1):
        bigram = r["bigram"].replace("-", "+")
        ex = examples.get(r["bigram"], [])
        ex_text = "; ".join(f"`{e}`" for e in ex[:2]) if ex else ""
        lines.append(
            f"| {i} | `{bigram}` | {r['target_prob']*100:.2f} | {r['baseline_prob']*100:.2f} "
            f"| {r['delta']*100:+.2f} | {r['log2_ratio']:+.2f} | {r['kl_contrib']:+.5f} "
            f"| {ex_text} |"
        )
    return "\n".join(lines)


def render_report(
    target_path: Path,
    target_counts: dict[str, int],
    cluster_loaded: list[Path],
    cluster_skipped: list[Path],
    pooled_diff: list[dict[str, Any]] | None,
    mean_diff: list[dict[str, Any]] | None,
    target_examples: dict[str, list[str]],
    cluster_examples: dict[str, list[str]],
    *,
    top: int,
    alpha: float,
    min_count: int,
) -> str:
    n_target = sum(target_counts.values())
    lines: list[str] = []
    lines.append(f"# POS-bigram diff: `{target_path.name}` vs. cluster")
    lines.append("")
    lines.append(f"**Task surface:** `{TASK_SURFACE}`")
    lines.append("")
    lines.append(
        f"Target: {n_target} bigrams, {len(target_counts)} unique. "
        f"Cluster: {len(cluster_loaded)} files loaded"
        + (f", {len(cluster_skipped)} skipped." if cluster_skipped else ".")
    )
    lines.append(
        f"Smoothing α = {alpha}, min count floor = {min_count}."
    )
    lines.append("")

    def _section(label: str, diff: list[dict[str, Any]]) -> None:
        kl_total = sum(r["kl_contrib"] for r in diff)
        lines.append(f"## Cluster mode: **{label}**")
        lines.append("")
        lines.append(f"Aggregate KL(target ‖ cluster) ≈ {kl_total:.4f} (sum of contributions).")
        lines.append("")
        lines.append(f"### Top {top} over-represented in target (target uses more)")
        lines.append("")
        lines.append(render_table(diff, direction="over", top=top, examples=target_examples))
        lines.append("")
        lines.append(f"### Top {top} under-represented in target (cluster uses more)")
        lines.append("")
        lines.append(render_table(diff, direction="under", top=top, examples=cluster_examples))
        lines.append("")

    if pooled_diff is not None:
        _section("pooled counts", pooled_diff)
    if mean_diff is not None:
        _section("per-file mean", mean_diff)
    return "\n".join(lines)


def render_json(
    target_path: Path,
    target_counts: dict[str, int],
    cluster_loaded: list[Path],
    cluster_skipped: list[Path],
    pooled_diff: list[dict[str, Any]] | None,
    mean_diff: list[dict[str, Any]] | None,
    *,
    top: int,
    alpha: float,
    min_count: int,
) -> str:
    envelope = build_audit_payload(
        target_path=target_path,
        target_counts=target_counts,
        cluster_loaded=cluster_loaded,
        cluster_skipped=cluster_skipped,
        pooled_diff=pooled_diff,
        mean_diff=mean_diff,
        top=top, alpha=alpha, min_count=min_count,
    )
    return json.dumps(envelope, indent=2, default=float)


def _claim_license(
    *,
    target_bigrams: int,
    n_cluster: int,
    alpha: float,
    min_count: int,
) -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Per-POS-bigram diff between a target text and a "
            "comparison cluster. Reports the top diverging bigrams "
            "(both directions) plus a KL total per aggregation "
            "strategy (pooled counts, per-file mean, or both)."
        ),
        does_not_license=(
            "An authorship verdict. POS-bigram divergence reflects "
            "register, genre conventions, syntactic style, and "
            "deliberate craft choice as much as drift. The audit "
            "surfaces structural patterns; the writer adjudicates "
            "whether each is symptomatic."
        ),
        comparison_set={
            "target_bigrams": target_bigrams,
            "n_cluster_files": n_cluster,
            "smoothing_alpha": alpha,
            "min_count": min_count,
        },
        additional_caveats=[
            "Requires spaCy + en_core_web_sm for POS tagging.",
            "Laplace smoothing affects pooled-counts mode; per-file "
            "mean does not smooth. Compare both before drawing "
            "conclusions about KL magnitude.",
        ],
    )


def build_audit_payload(
    *,
    target_path: Path | str,
    target_counts: dict[str, int],
    cluster_loaded: list[Path],
    cluster_skipped: list[Path],
    pooled_diff: list[dict[str, Any]] | None,
    mean_diff: list[dict[str, Any]] | None,
    top: int,
    alpha: float,
    min_count: int,
) -> dict[str, Any]:
    """Wrap the bigram-diff output in the schema_version 1.0 envelope
    per ``internal/SPEC_output_schema_unification.md``.
    """
    target_bigrams = sum(target_counts.values())
    diffs: dict[str, Any] = {}
    if pooled_diff is not None:
        diffs["pooled"] = {
            "kl_total": sum(r["kl_contrib"] for r in pooled_diff),
            "rows": pooled_diff[:top * 2],
        }
    if mean_diff is not None:
        diffs["mean"] = {
            "kl_total": sum(r["kl_contrib"] for r in mean_diff),
            "rows": mean_diff[:top * 2],
        }
    baseline_meta = build_baseline_metadata(
        n_files=len(cluster_loaded),
        words=0,  # bigram_diff counts bigrams, not words
        files_loaded=[str(p) for p in cluster_loaded],
        files_skipped=[str(p) for p in cluster_skipped] or None,
    )
    warnings: list[str] = []
    if cluster_skipped:
        warnings.append(
            f"{len(cluster_skipped)} cluster file(s) skipped; "
            "their bigrams are absent from the comparison set."
        )
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_bigrams,
        baseline=baseline_meta,
        results={
            "target_bigrams": target_bigrams,
            "target_unique": len(target_counts),
            "smoothing_alpha": alpha,
            "min_count": min_count,
            "diffs": diffs,
        },
        claim_license=_claim_license(
            target_bigrams=target_bigrams,
            n_cluster=len(cluster_loaded),
            alpha=alpha, min_count=min_count,
        ),
        warnings=warnings,
    )


# ---------- main ----------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Per-bigram POS-bigram diff: target vs. a comparison cluster."
    )
    parser.add_argument("target", help="Path to the target text file.")
    cluster_grp = parser.add_mutually_exclusive_group(required=True)
    cluster_grp.add_argument(
        "--cluster-dir",
        help="Directory of cluster comparator files (.txt + .md, READMEs skipped).",
    )
    cluster_grp.add_argument(
        "--cluster-files",
        nargs="+",
        help="Explicit list of cluster comparator file paths.",
    )
    parser.add_argument(
        "--cluster-mode",
        choices=("mean", "pooled", "both"),
        default="both",
        help="Aggregation strategy across cluster files (default: both).",
    )
    parser.add_argument(
        "--top", type=int, default=20,
        help="Top N bigrams per direction (default 20).",
    )
    parser.add_argument(
        "--min-count", type=int, default=1,
        help="Drop bigrams where neither side reaches this count (default 1, no filter).",
    )
    parser.add_argument(
        "--smoothing-alpha", type=float, default=1.0,
        help="Laplace add-α smoothing for pooled-counts mode (default 1.0).",
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

    target_path = Path(args.target)
    if not target_path.exists():
        print(f"Target file not found: {target_path}", file=sys.stderr)
        return 1

    target_counts, target_examples = file_bigram_counts(target_path)
    if not target_counts:
        print(
            f"No POS-bigrams extracted from {target_path}. "
            "Empty file or all whitespace?",
            file=sys.stderr,
        )
        return 1

    if args.cluster_dir:
        cluster_paths = list_cluster_paths(args.cluster_dir)
        if not cluster_paths:
            print(f"No .txt or .md files in {args.cluster_dir}", file=sys.stderr)
            return 1
    else:
        cluster_paths = [Path(p) for p in args.cluster_files]

    # Parse cluster files once; aggregators reuse the cache when running
    # both modes.
    cluster_cache, cluster_examples, cluster_skipped = parse_cluster_files(cluster_paths)
    cluster_loaded = [p for p, _ in cluster_cache]
    if not cluster_cache:
        print("No cluster files yielded POS-bigrams.", file=sys.stderr)
        return 1

    pooled_diff = None
    mean_diff = None

    if args.cluster_mode in ("pooled", "both"):
        pooled_counts, _ex, _n, _loaded, _skipped = aggregate_cluster_pooled(
            cluster_paths, cache=cluster_cache,
        )
        pooled_diff = diff_pooled(
            dict(target_counts), dict(pooled_counts),
            alpha=args.smoothing_alpha, min_count=args.min_count,
        )

    if args.cluster_mode in ("mean", "both"):
        mean_probs, _ex, _loaded, _skipped = aggregate_cluster_mean(
            cluster_paths, cache=cluster_cache,
        )
        mean_diff = diff_mean(
            dict(target_counts), mean_probs, min_count=args.min_count,
        )

    if cluster_skipped:
        print(
            "Warning: could not read or POS-tag cluster files: "
            + ", ".join(p.name for p in cluster_skipped)
            + ". Their bigrams are absent from the cluster aggregate.",
            file=sys.stderr,
        )

    if args.json:
        output = render_json(
            target_path, target_counts, cluster_loaded, cluster_skipped,
            pooled_diff, mean_diff,
            top=args.top, alpha=args.smoothing_alpha, min_count=args.min_count,
        )
    else:
        output = render_report(
            target_path, target_counts, cluster_loaded, cluster_skipped,
            pooled_diff, mean_diff, target_examples, cluster_examples,
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
