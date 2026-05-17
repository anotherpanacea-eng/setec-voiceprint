#!/usr/bin/env python3
"""
chapter_distinctiveness_audit.py
Chapter-aware vocabulary repetition diagnostic.

Sibling to ``manuscript_repetition_audit.py``. Where the manuscript-
aggregate audit asks "which words are over-represented in this chapter
versus an external baseline corpus," this script asks "which words are
over-represented in this chapter versus the rest of the manuscript."
The two audits answer different questions and surface different
patterns. A habit-vocabulary word that recurs in many chapters at
moderate ratio will land in the manuscript-aggregate audit but not
here, because the rest-of-manuscript baseline already contains it. A
word that is distinctive to one chapter (a thematic anchor, a setting
prop, a character name in close-third POV) will land here but may not
land in the manuscript-aggregate audit if the external corpus also
uses that word.

Internal-baseline construction is leave-one-out: for each chapter, the
baseline is the union of all other chapters. No external corpus is
required; the manuscript scores against itself.

Usage:
    python3 chapter_distinctiveness_audit.py MANUSCRIPT.md
    python3 chapter_distinctiveness_audit.py --chapter-dir CHAPTERS/
    python3 chapter_distinctiveness_audit.py NOVEL.md \\
        --anchors anchors.txt --top-per-chapter 10 --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from repetition_audit import (  # type: ignore
    DEFAULT_FUNCTION_WORDS,
    load_anchors,
    score_against_baseline_counts,
    tokenize,
)
from manuscript_audit import (  # type: ignore
    load_chapter_dir,
    split_manuscript,
)

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore


# See variance_audit.TASK_SURFACE for the contract.
TASK_SURFACE = "smoothing_diagnosis"
TOOL_NAME = "chapter_distinctiveness_audit"
SCRIPT_VERSION = "1.0"


def precompute_chapter_counts(
    chapters: list[dict[str, Any]],
) -> list[tuple[Counter, int]]:
    """Tokenize each chapter once and return ``[(counts, n_tokens), ...]``."""
    out: list[tuple[Counter, int]] = []
    for ch in chapters:
        tokens = tokenize(ch["text"])
        out.append((Counter(tokens), len(tokens)))
    return out


def leave_one_out_baseline(
    chapter_counts: list[tuple[Counter, int]],
    skip_index: int,
) -> tuple[Counter, int]:
    """Sum every chapter's counts except ``skip_index``."""
    total = Counter()
    n = 0
    for i, (counts, count_n) in enumerate(chapter_counts):
        if i == skip_index:
            continue
        total.update(counts)
        n += count_n
    return total, n


def audit_chapter_distinctiveness(
    chapters: list[dict[str, Any]],
    *,
    function_words: set[str],
    anchor_words: set[str],
    min_count: int,
    min_word_len: int,
    cluster_window: int,
    min_ratio: float,
) -> dict[str, Any]:
    """Score every chapter against the union of all other chapters.

    The result mirrors the manuscript-aggregate audit's per-chapter
    structure, but the baseline shifts per chapter rather than being
    shared. There is no manuscript-wide aggregator: a word's ratio
    against rest-of-manuscript in one chapter is not comparable to
    the same word's ratio in another chapter, because the baselines
    are different. The summary instead reports which chapters carry
    the most distinctive vocabulary.
    """
    if len(chapters) < 2:
        raise ValueError(
            "Chapter-distinctiveness audit needs at least two chapters; "
            "got "
            + str(len(chapters))
            + ". With one chapter there is no rest-of-manuscript baseline."
        )

    chapter_counts = precompute_chapter_counts(chapters)

    per_chapter: list[dict[str, Any]] = []
    total_words = 0
    for i, ch in enumerate(chapters):
        base_counts, base_n = leave_one_out_baseline(chapter_counts, i)
        if base_n == 0:
            # All other chapters were empty. Treat as no baseline.
            per_chapter.append({
                "label": ch["label"],
                "n_target_words": chapter_counts[i][1],
                "rest_of_manuscript_words": 0,
                "candidates": [],
                "warning": "rest-of-manuscript baseline was empty",
            })
            total_words += chapter_counts[i][1]
            continue

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
            "rest_of_manuscript_words": base_n,
            "candidates": candidates,
        })
        total_words += n_target_words

    return {
        "task_surface": TASK_SURFACE,
        "n_chapters": len(chapters),
        "total_target_words": total_words,
        "chapters": per_chapter,
    }


# ---------- Output formatting ----------

def render_report(
    result: dict[str, Any],
    *,
    top_per_chapter: int = 10,
) -> str:
    chapters = result["chapters"]
    n_chapters = result["n_chapters"]

    lines: list[str] = []
    lines.append("# Chapter Distinctiveness Audit")
    lines.append("")
    lines.append(f"**Task surface:** `{TASK_SURFACE}`")
    lines.append("")
    lines.append(
        "For each chapter, words over-represented compared to the "
        "*rest of the manuscript*. Different question from the "
        "external-baseline audit: a manuscript-wide habit word will "
        "appear in the rest-of-manuscript baseline too, so it will not "
        "show up here even if it shows up in the external audit. Words "
        "distinctive to one chapter (thematic anchors, setting props, "
        "POV-specific vocabulary) are what this audit catches."
    )
    lines.append("")
    lines.append(f"**Chapters analyzed:** {n_chapters}")
    lines.append(f"**Total chapter words:** {result['total_target_words']}")
    lines.append("")

    # Summary: which chapters carry the most distinctive vocabulary.
    counts_per_chapter = [
        (ch["label"], len(ch["candidates"])) for ch in chapters
    ]
    if counts_per_chapter:
        n_words_avg = statistics.mean(c for _, c in counts_per_chapter)
        most_distinctive = sorted(
            counts_per_chapter, key=lambda x: x[1], reverse=True
        )[:5]
        lines.append("## Distinctive-vocabulary load by chapter")
        lines.append("")
        lines.append(
            "Number of words clearing the over-representation threshold "
            "in each chapter against rest-of-manuscript. Chapters with "
            "many candidates carry vocabulary the rest of the manuscript "
            f"does not. Mean across chapters: {n_words_avg:.1f}."
        )
        lines.append("")
        lines.append("| chapter | distinctive candidates |")
        lines.append("|---|---:|")
        for label, n in sorted(
            counts_per_chapter, key=lambda x: x[1], reverse=True
        ):
            lines.append(f"| {label} | {n} |")
        lines.append("")

    # Per-chapter top distinctive vocabulary.
    lines.append("## Per-chapter distinctive vocabulary")
    lines.append("")
    lines.append(
        f"Top {top_per_chapter} words per chapter by ratio against "
        "rest-of-manuscript. Chapters with no flagged candidates are "
        "still listed so absence is visible."
    )
    lines.append("")
    for ch in chapters:
        line_header = (
            f"### {ch['label']} ({ch['n_target_words']} words, "
            f"baseline {ch['rest_of_manuscript_words']})"
        )
        lines.append(line_header)
        lines.append("")
        if not ch["candidates"]:
            warning = ch.get("warning")
            if warning:
                lines.append(f"_{warning}_")
            else:
                lines.append("_No words crossed the over-representation threshold._")
            lines.append("")
            continue
        lines.append("| word | count | per_1k | rest_per_1k | ratio | cluster_max |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for c in ch["candidates"][:top_per_chapter]:
            lines.append(
                f"| {c['word']} | {c['count']} | {c['per_1000']} | "
                f"{c['baseline_per_1000']} | {c['ratio']} | "
                f"{c['cluster_max']} |"
            )
        lines.append("")

    return "\n".join(lines)


# ---------- CLI ----------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Chapter-aware vocabulary repetition audit. Scores "
                    "each chapter against the rest of the manuscript "
                    "(leave-one-out internal baseline). Surfaces words "
                    "distinctive to one chapter rather than habit-words "
                    "dispersed across the manuscript."
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
        "--top-per-chapter", type=int, default=10,
        help="Top distinctive words per chapter (default 10).",
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
        "--min-ratio", type=float, default=1.5,
        help="Minimum target/rest-of-manuscript ratio for a candidate "
             "(default 1.5). 'Distinctive' is a stronger claim than "
             "'barely over-represented'; the higher floor cuts noise "
             "introduced by chapters that omit otherwise-dispersed "
             "habit-vocabulary. Pass --min-ratio 1.0 to match the "
             "external-baseline audit's threshold; pass 0 for legacy "
             "all-candidates behavior.",
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

    if args.manuscript:
        text = Path(args.manuscript).read_text(
            encoding="utf-8", errors="ignore"
        )
        chapters = split_manuscript(text, args.chapter_pattern)
    else:
        chapters = load_chapter_dir(args.chapter_dir)
    if not chapters:
        print("No chapters detected.", file=sys.stderr)
        return 1
    if len(chapters) < 2:
        print(
            "Chapter-distinctiveness audit needs at least two chapters. "
            "With one chapter there is no rest-of-manuscript baseline. "
            "Use repetition_audit.py against an external baseline instead.",
            file=sys.stderr,
        )
        return 1

    function_words = (
        set() if args.include_function_words else DEFAULT_FUNCTION_WORDS
    )
    anchor_words = load_anchors(args.anchors)

    result = audit_chapter_distinctiveness(
        chapters,
        function_words=function_words,
        anchor_words=anchor_words,
        min_count=args.min_count,
        min_word_len=args.min_word_len,
        cluster_window=args.cluster_window,
        min_ratio=args.min_ratio,
    )

    if args.json:
        payload = build_audit_payload(
            result,
            target_path=args.manuscript or args.chapter_dir,
        )
        output = json.dumps(payload, indent=2, default=str)
    else:
        output = render_report(result, top_per_chapter=args.top_per_chapter)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(output)
    return 0


def _claim_license(result: dict[str, Any]) -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Per-chapter distinctive-vocabulary report. For each "
            "chapter, names the words over-represented relative to "
            "the rest of the manuscript (leave-one-out internal "
            "baseline). Surfaces thematic anchors, setting props, "
            "and POV-specific vocabulary — words that distinguish "
            "one chapter from the rest."
        ),
        does_not_license=(
            "An authorship verdict. The audit is descriptive of "
            "vocabulary distribution within a manuscript and does "
            "not license claims about who wrote the manuscript or "
            "whether AI is involved. Words can be distinctive for "
            "many reasons (close-third POV, scene location, "
            "deliberate craft choice); the audit's job is to make "
            "them visible, not to judge them."
        ),
        comparison_set={
            "n_chapters": result.get("n_chapters"),
            "total_target_words": result.get("total_target_words"),
        },
        additional_caveats=[
            "Manuscript-wide habit-vocabulary will not surface here "
            "because the rest-of-manuscript baseline already "
            "contains it. Pair with `manuscript_repetition_audit` "
            "(external-baseline) to see both patterns.",
            "Chapters with very short word counts produce noisy "
            "ratios; the per-chapter `n_target_words` field "
            "documents this.",
        ],
    )


def build_audit_payload(
    result: dict[str, Any],
    *,
    target_path: Path | str | None,
) -> dict[str, Any]:
    """Wrap the chapter-distinctiveness audit in the schema_version 1.0
    envelope per ``internal/SPEC_output_schema_unification.md``.
    """
    target_words = int(result.get("total_target_words", 0) or 0)
    n_chapters = result.get("n_chapters")
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=None,
        results={
            "n_chapters": n_chapters,
            "chapters": result.get("chapters", []),
        },
        claim_license=_claim_license(result),
        target_extra=(
            {"n_chapters": n_chapters}
            if n_chapters is not None else None
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
