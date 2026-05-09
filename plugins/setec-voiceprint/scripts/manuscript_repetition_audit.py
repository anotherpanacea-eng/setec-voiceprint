#!/usr/bin/env python3
"""
manuscript_repetition_audit.py
Manuscript-wide vocabulary repetition diagnostic.

Sibling to ``repetition_audit.py``. Where ``repetition_audit`` runs on a
single document, this script runs the same per-chapter scoring across a
whole manuscript and then aggregates results to surface habit-vocabulary
that a single-chapter audit misses by construction. A word that recurs
at moderate ratio in twelve chapters is dispersed habit-vocabulary; a
word that spikes once at very high ratio is more often a thematic
anchor. The two patterns deserve different revision strategies.

Usage:
    python3 manuscript_repetition_audit.py MANUSCRIPT.md \\
        --baseline-dir BASELINE_DIR

    python3 manuscript_repetition_audit.py --chapter-dir CHAPTERS/ \\
        --baseline-dir BASELINE_DIR --anchors anchors.txt

    python3 manuscript_repetition_audit.py NOVEL.md \\
        --baseline-dir B/ --chapter-pattern '^##\\s*Part\\s+\\d+' --json

Outputs a markdown dashboard with three sections: dispersed habit
vocabulary (words flagged in many chapters), concentrated repetition
(words flagged in one or two chapters at high ratio), and a per-chapter
top-N list.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from repetition_audit import (  # type: ignore
    BaselineError,
    DEFAULT_FUNCTION_WORDS,
    list_baseline_paths,
    load_anchors,
    load_baseline_counts,
    score_against_baseline_counts,
)
from manuscript_audit import (  # type: ignore
    load_chapter_dir,
    split_manuscript,
)


# See variance_audit.TASK_SURFACE for the contract.
TASK_SURFACE = "smoothing_diagnosis"


def aggregate_across_chapters(
    per_chapter: list[dict[str, Any]],
    n_chapters: int,
) -> list[dict[str, Any]]:
    """Aggregate per-chapter candidate lists into manuscript-wide stats.

    For each word that appeared in any chapter's candidate list, build a
    summary covering chapter spread, total count, the distribution of
    ratios (mean, median, min, max), the peak chapter, and the worst
    cluster_max seen anywhere. The chapter spread is the load-bearing
    signal: it separates dispersed habit-vocabulary from concentrated
    thematic anchors.
    """
    by_word: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ch in per_chapter:
        for cand in ch["candidates"]:
            by_word[cand["word"]].append({
                "label": ch["label"],
                "count": cand["count"],
                "ratio": cand["ratio"],
                "per_1000": cand["per_1000"],
                "cluster_max": cand["cluster_max"],
            })

    aggregated: list[dict[str, Any]] = []
    for word, hits in by_word.items():
        ratios = [h["ratio"] for h in hits]
        counts = [h["count"] for h in hits]
        cluster_maxes = [h["cluster_max"] for h in hits]
        peak = max(hits, key=lambda h: h["ratio"])
        aggregated.append({
            "word": word,
            "n_chapters": len(hits),
            "fraction_of_chapters": (
                len(hits) / n_chapters if n_chapters else 0.0
            ),
            "total_count": sum(counts),
            "mean_ratio": round(statistics.mean(ratios), 1),
            "median_ratio": round(statistics.median(ratios), 1),
            "max_ratio": round(max(ratios), 1),
            "min_ratio": round(min(ratios), 1),
            "peak_chapter": peak["label"],
            "peak_ratio": peak["ratio"],
            "peak_cluster_max": max(cluster_maxes),
            "chapters": hits,
        })
    return aggregated


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    try:
        return p.resolve()
    except OSError:
        return p


def audit_manuscript_repetition(
    chapters: list[dict[str, Any]],
    baseline_dir: str,
    *,
    function_words: set[str],
    anchor_words: set[str],
    min_count: int,
    min_word_len: int,
    cluster_window: int,
    min_ratio: float = 1.0,
    target_paths: set[Path] | None = None,
) -> dict[str, Any]:
    """Drive the full manuscript-aggregate pipeline.

    ``target_paths`` is a set of resolved paths the caller expects to
    score *against* the baseline (the manuscript file, or every chapter
    file in chapter-dir mode). Any baseline file whose resolved path
    appears in this set is dropped before tokenization, with a stderr
    notice; otherwise the manuscript would be its own baseline and the
    scores would self-normalize toward zero ratio.
    """
    base_paths = list_baseline_paths(baseline_dir)

    target_set = {_resolve(p) for p in (target_paths or set())}
    if target_set:
        kept: list[Path] = []
        dropped: list[str] = []
        for p in base_paths:
            if _resolve(p) in target_set:
                dropped.append(p.name)
                continue
            kept.append(p)
        if dropped:
            print(
                "Dropped manuscript files from baseline: "
                + ", ".join(dropped) + ".",
                file=sys.stderr,
            )
        base_paths = kept

    if not base_paths:
        raise BaselineError(
            "No usable baseline files. Point --baseline-dir at a directory "
            "of .txt or .md files that does not overlap with the manuscript."
        )

    base_counts, base_n, loaded, skipped = load_baseline_counts(base_paths)
    if skipped:
        print(
            "Warning: could not read baseline files: "
            + ", ".join(p.name for p in skipped)
            + ". Their tokens are absent from the baseline; ratios may "
              "be inflated.",
            file=sys.stderr,
        )
    if base_n == 0:
        raise BaselineError(
            "Baseline files contain zero tokens after reading. Check the "
            "files in --baseline-dir."
        )

    per_chapter: list[dict[str, Any]] = []
    total_words = 0
    for ch in chapters:
        candidates, n_target_words = score_against_baseline_counts(
            ch["text"], base_counts, base_n,
            function_words=function_words,
            anchor_words=anchor_words,
            min_count=min_count,
            min_word_len=min_word_len,
            cluster_window=cluster_window,
            min_ratio=min_ratio,
        )
        per_chapter.append({
            "label": ch["label"],
            "n_target_words": n_target_words,
            "candidates": candidates,
        })
        total_words += n_target_words

    aggregated = aggregate_across_chapters(per_chapter, len(chapters))
    # Sort by chapter spread first, then by median ratio so a single
    # large spike does not boost a word's rank ahead of a more
    # consistently dispersed habit. Mean stays in the report for
    # readers who want it; peak lives in the concentrated section.
    aggregated.sort(
        key=lambda w: (w["n_chapters"], w["median_ratio"]),
        reverse=True,
    )

    return {
        "task_surface": TASK_SURFACE,
        "n_chapters": len(chapters),
        "n_baseline_files": len(loaded),
        "n_baseline_files_skipped": len(skipped),
        "baseline_files_loaded": [str(p) for p in loaded],
        "baseline_files_skipped": [str(p) for p in skipped],
        "baseline_words": base_n,
        "total_target_words": total_words,
        "chapters": per_chapter,
        "aggregated": aggregated,
    }


# ---------- Output formatting ----------

def render_report(
    result: dict[str, Any],
    *,
    top_dispersed: int = 25,
    top_concentrated: int = 15,
    top_per_chapter: int = 5,
    min_dispersed_chapters: int = 0,
) -> str:
    chapters = result["chapters"]
    aggregated = result["aggregated"]
    n_chapters = result["n_chapters"]

    lines: list[str] = []
    lines.append("# Manuscript Vocabulary Repetition Audit")
    lines.append("")
    lines.append(f"**Task surface:** `{TASK_SURFACE}`")
    lines.append(f"**Chapters analyzed:** {n_chapters}")
    lines.append(f"**Baseline files loaded:** {result['n_baseline_files']}")
    if result.get("n_baseline_files_skipped"):
        skipped_names = [
            Path(p).name for p in result.get("baseline_files_skipped", [])
        ]
        lines.append(
            f"**Baseline files skipped (unreadable):** "
            f"{result['n_baseline_files_skipped']} ({', '.join(skipped_names)})"
        )
    lines.append(f"**Baseline tokens:** {result['baseline_words']}")
    lines.append(f"**Total chapter words:** {result['total_target_words']}")
    lines.append(f"**Words flagged in any chapter:** {len(aggregated)}")
    lines.append("")
    if not aggregated:
        lines.append(
            "No over-represented words crossed the per-chapter thresholds."
        )
        return "\n".join(lines)

    if min_dispersed_chapters <= 0:
        # Default: a word must show up in at least a third of chapters,
        # but never fewer than three. The third-of-chapters rule scales
        # with manuscript length; the floor of three keeps short
        # manuscripts honest.
        min_dispersed_chapters = max(3, n_chapters // 3)

    # Sort by chapter spread first, then median ratio. Median resists a
    # single high-spike chapter inflating the rank; peak ratio still
    # appears in the table for inspection but does not order it.
    dispersed = sorted(
        [w for w in aggregated if w["n_chapters"] >= min_dispersed_chapters],
        key=lambda w: (w["n_chapters"], w["median_ratio"]),
        reverse=True,
    )
    lines.append("## Dispersed habit vocabulary")
    lines.append("")
    lines.append(
        f"Words flagged as over-represented in at least "
        f"{min_dispersed_chapters} of {n_chapters} chapters. These are "
        "candidates for a manuscript-wide variation pass: their repetition "
        "is dispersed rather than scene-anchored, so single-chapter audits "
        "tend to miss them. Read these alongside the writer's anchor list "
        "before treating any one word as a problem."
    )
    lines.append("")
    if dispersed:
        lines.append(
            "| word | chapters | total | mean ratio | "
            "median ratio | peak ratio | peak chapter |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---|")
        for w in dispersed[:top_dispersed]:
            lines.append(
                f"| {w['word']} | {w['n_chapters']}/{n_chapters} | "
                f"{w['total_count']} | {w['mean_ratio']} | "
                f"{w['median_ratio']} | {w['peak_ratio']} | "
                f"{w['peak_chapter']} |"
            )
        lines.append("")
    else:
        lines.append("(No words crossed the dispersed-habit threshold.)")
        lines.append("")

    concentrated = sorted(
        [w for w in aggregated if w["n_chapters"] <= 2],
        key=lambda w: (w["peak_ratio"], w["total_count"]),
        reverse=True,
    )
    lines.append("## Concentrated repetition (1-2 chapters)")
    lines.append("")
    lines.append(
        "Words with high over-representation but limited to one or two "
        "chapters. Often these are thematic anchors carrying scene "
        "weight; verify in source-triage before treating them as repetition "
        "problems. Concentration alone does not mean the repetition is "
        "unearned."
    )
    lines.append("")
    if concentrated:
        lines.append(
            "| word | chapters | peak ratio | "
            "peak chapter | peak cluster_max |"
        )
        lines.append("|---|---:|---:|---|---:|")
        for w in concentrated[:top_concentrated]:
            lines.append(
                f"| {w['word']} | {w['n_chapters']}/{n_chapters} | "
                f"{w['peak_ratio']} | {w['peak_chapter']} | "
                f"{w['peak_cluster_max']} |"
            )
        lines.append("")
    else:
        lines.append("(No concentrated single-chapter outliers.)")
        lines.append("")

    lines.append("## Per-chapter top over-representations")
    lines.append("")
    lines.append(
        f"Top {top_per_chapter} words per chapter by ratio. Use this to "
        "scan which chapters carry the strongest local lexical signature "
        "and which words drive it. Chapters with no flagged words are "
        "omitted."
    )
    lines.append("")
    for ch in chapters:
        if not ch["candidates"]:
            continue
        line = f"- **{ch['label']}** ({ch['n_target_words']} words): "
        line += ", ".join(
            f"`{c['word']}` ({c['count']}x, r={c['ratio']})"
            for c in ch["candidates"][:top_per_chapter]
        )
        lines.append(line)
    lines.append("")

    return "\n".join(lines)


# ---------- CLI ----------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manuscript-wide vocabulary repetition audit. "
                    "Surfaces dispersed habit-vocabulary across chapters."
    )
    parser.add_argument(
        "manuscript", nargs="?",
        help="Manuscript file with chapter markers (.md, .txt).",
    )
    parser.add_argument(
        "--chapter-dir",
        help="Alternative: directory of chapter files (.txt or .md).",
    )
    parser.add_argument(
        "--baseline-dir", required=True,
        help="Baseline corpus directory (.txt or .md files).",
    )
    parser.add_argument(
        "--chapter-pattern",
        default=r"^#+\s*Chapter\s+\d+",
        help="Regex pattern for chapter markers "
             "(default: '^#+\\s*Chapter\\s+\\d+').",
    )
    parser.add_argument(
        "--anchors",
        help="Path to a project-anchor file. Anchor words are excluded "
             "from candidate scoring.",
    )
    parser.add_argument(
        "--top-dispersed", type=int, default=25,
        help="Number of dispersed-habit rows to render (default 25).",
    )
    parser.add_argument(
        "--top-concentrated", type=int, default=15,
        help="Number of concentrated-repetition rows to render (default 15).",
    )
    parser.add_argument(
        "--top-per-chapter", type=int, default=5,
        help="Top words per chapter in the per-chapter view (default 5).",
    )
    parser.add_argument(
        "--min-dispersed-chapters", type=int, default=0,
        help="Minimum number of chapters a word must hit to count as "
             "dispersed (default: max(3, n_chapters // 3)).",
    )
    parser.add_argument(
        "--min-count", type=int, default=3,
        help="Minimum chapter occurrences for a word to be a candidate "
             "(default 3, matches repetition_audit).",
    )
    parser.add_argument(
        "--min-word-len", type=int, default=4,
        help="Minimum word length (default 4).",
    )
    parser.add_argument(
        "--cluster-window", type=int, default=300,
        help="Token window for within-chapter clustering check (default 300).",
    )
    parser.add_argument(
        "--min-ratio", type=float, default=1.0,
        help="Minimum target/baseline ratio for a chapter candidate to be "
             "scored (default 1.0). Pass 0 for legacy all-candidates "
             "behavior. Words below this floor are not over-represented "
             "and would otherwise leak into the dispersed-habit table.",
    )
    parser.add_argument(
        "--include-function-words", action="store_true",
        help="Disable the default function-word filter (rarely useful).",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit JSON instead of the markdown dashboard.",
    )
    parser.add_argument(
        "--out",
        help="Write report to file instead of stdout.",
    )
    args = parser.parse_args()

    if not args.manuscript and not args.chapter_dir:
        parser.error("Provide either a manuscript file or --chapter-dir.")

    target_paths: set[Path] = set()
    if args.manuscript:
        manuscript_path = Path(args.manuscript)
        text = manuscript_path.read_text(encoding="utf-8", errors="ignore")
        chapters = split_manuscript(text, args.chapter_pattern)
        target_paths.add(manuscript_path)
    else:
        chapters = load_chapter_dir(args.chapter_dir)
        for ch in chapters:
            if "path" in ch:
                target_paths.add(Path(ch["path"]))
    if not chapters:
        print("No chapters detected.", file=sys.stderr)
        return 1

    function_words = (
        set() if args.include_function_words else DEFAULT_FUNCTION_WORDS
    )
    anchor_words = load_anchors(args.anchors)

    try:
        result = audit_manuscript_repetition(
            chapters, args.baseline_dir,
            function_words=function_words,
            anchor_words=anchor_words,
            min_count=args.min_count,
            min_word_len=args.min_word_len,
            cluster_window=args.cluster_window,
            min_ratio=args.min_ratio,
            target_paths=target_paths,
        )
    except BaselineError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        output = json.dumps(result, indent=2, default=str)
    else:
        output = render_report(
            result,
            top_dispersed=args.top_dispersed,
            top_concentrated=args.top_concentrated,
            top_per_chapter=args.top_per_chapter,
            min_dispersed_chapters=args.min_dispersed_chapters,
        )

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
