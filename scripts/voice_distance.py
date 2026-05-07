#!/usr/bin/env python3
"""
voice_distance.py
Compare a target text against a writer/register baseline using classic
stylometric feature families.

This is a voice-coherence tool, not an AI-provenance detector.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from stylometry_core import compare_to_baseline, load_entries, read_text


# Task-surface tag. See variance_audit.TASK_SURFACE for the framework
# contract. Voice-coherence comparison answers "does this draft match
# the writer's prior corpus" - distinct from prose-quality smoothing
# diagnosis. A future validation harness must refuse to mix scores
# across surfaces because they answer different questions.
TASK_SURFACE = "voice_coherence"


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "--"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}"
    return str(value)


def md_cell(value: Any) -> str:
    text = str(value).replace("\n", " ")
    text = text.replace("|", "\\|")
    return text


def build_limits(args: argparse.Namespace) -> dict[str, int]:
    return {
        "function_words": args.function_top,
        # --char-top sets the per-n cap for each char-ngram family
        # separately (3-grams, 4-grams, 5-grams). Earlier versions
        # treated this as a single combined cap across all three.
        "char_ngrams_3": args.char_top,
        "char_ngrams_4": args.char_top,
        "char_ngrams_5": args.char_top,
        "pos_trigrams": args.pos_top,
        "dependency_ngrams": args.dep_top,
    }


def render_clusters(
    result: dict[str, Any],
    lines: list[str],
    cluster_top: int,
) -> None:
    """Append a Feature Clusters section if any family produced clusters."""
    cluster_blocks: list[tuple[str, list[dict[str, Any]]]] = []
    for family, info in sorted(result["families"].items()):
        clusters = info.get("clusters") or []
        if clusters:
            cluster_blocks.append((family, clusters))
    if not cluster_blocks:
        return
    lines.append("## Feature Clusters")
    lines.append("")
    lines.append(
        "Group-level signals: predefined clusters of related features. "
        "Directional clusters (at least 70% of matched features moving the "
        "same way, at least three matched features) often reveal authorial "
        "fingerprints that the per-feature top-N misses, where each "
        "individual feature sits below the conventional flag threshold but "
        "the cluster as a whole drifts together. Read alongside Top "
        "Deviations: single-feature breaks catch template repetition; "
        "cluster drift catches register and idiolect shifts."
    )
    lines.append("")
    for family, clusters in cluster_blocks:
        lines.append(f"### {family}")
        lines.append("")
        lines.append(
            "| cluster | matched | mean signed z | direction | "
            "consistency | directional? |"
        )
        lines.append("|---|---:|---:|---|---:|---|")
        for c in clusters[:cluster_top]:
            lines.append(
                f"| {c['cluster']} | {c['n_matched']}/{c['n_in_cluster']} | "
                f"{fmt(c['mean_signed_z'], 2)} | "
                f"{c['direction']} | "
                f"{fmt(c['direction_consistency'], 2)} | "
                f"{'yes' if c['directional'] else 'no'} |"
            )
        lines.append("")
        lines.append("Top contributing features per cluster:")
        lines.append("")
        for c in clusters[:cluster_top]:
            tops = ", ".join(
                f"`{md_cell(t['feature'])}` ({fmt(t['z'], 2)})"
                for t in c.get("top_features", [])
            )
            lines.append(f"- **{c['cluster']}** ({c['direction']}): {tops}")
        lines.append("")


def render_report(
    result: dict[str, Any],
    target_path: Path,
    top_n: int,
    cluster_top: int = 15,
) -> str:
    lines = []
    target = result["target_summary"]
    baseline = result["baseline_summary"]
    overall = result["overall"]

    lines.append(f"# Voice Distance Audit: {target_path.name}")
    lines.append("")
    lines.append(f"**Task surface:** `{TASK_SURFACE}`")
    lines.append("")
    lines.append(
        "**Use:** stylometric distance from the supplied baseline. "
        "This is not an AI-provenance verdict."
    )
    lines.append("")
    lines.append(f"**Target words:** {target.get('n_words', 0)}")
    lines.append(
        f"**Baseline:** {baseline.get('n_files', 0)} files, "
        f"{baseline.get('total_words', 0)} words "
        f"(mean {baseline.get('mean_words', 0):.0f})"
    )
    if result.get("warnings"):
        lines.append("")
        lines.append("**Warnings:**")
        for warning in result["warnings"]:
            lines.append(f"- {warning}")
    lines.append("")
    lines.append(
        f"**Overall:** {overall['band']} "
        f"(weighted Delta {overall['weighted_delta']:.3f})"
    )
    lines.append("")
    lines.append(overall["interpretation"])
    lines.append("")

    lines.append("## Family Distances")
    lines.append("")
    lines.append("| family | features | Burrows-style Delta | cosine to centroid | mean cosine to files |")
    lines.append("|---|---:|---:|---:|---:|")
    for family, info in sorted(result["families"].items()):
        delta = fmt(info["burrows_delta"], 3)
        if info.get("capped_in_overall"):
            delta = f"{delta} (capped at {info['overall_delta_contribution_cap']:.1f} in overall)"
        lines.append(
            f"| {family} | {info['n_features']} | "
            f"{delta} | "
            f"{fmt(info['cosine_distance_to_centroid'], 4)} | "
            f"{fmt(info['cosine_distance_to_baseline_mean'], 4)} |"
        )
    lines.append("")

    lines.append("## Top Deviations")
    lines.append("")
    lines.append(
        "Largest absolute z-scores against the supplied baseline. "
        "Read these as drift candidates, not automatic errors."
    )
    for family, info in sorted(result["families"].items()):
        deviations = [d for d in info.get("top_deviations", []) if d.get("z") is not None]
        if not deviations:
            continue
        lines.append("")
        lines.append(f"### {family}")
        lines.append("")
        lines.append("| feature | z | target | baseline mean | baseline sd |")
        lines.append("|---|---:|---:|---:|---:|")
        for item in deviations[:top_n]:
            lines.append(
                f"| `{md_cell(item['feature'])}` | "
                f"{fmt(item['z'], 2)} | "
                f"{fmt(item['value'], 6)} | "
                f"{fmt(item['baseline_mean'], 6)} | "
                f"{fmt(item['baseline_sd'], 6)} |"
            )
    lines.append("")

    render_clusters(result, lines, cluster_top)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare a target text to a writer/register stylometric baseline."
    )
    parser.add_argument("target", help="Target .txt or .md file.")
    parser.add_argument("--baseline-dir", help="Directory of baseline .txt/.md files.")
    parser.add_argument("--manifest", help="Optional JSONL corpus manifest.")
    parser.add_argument("--use", default="baseline",
                        help="Manifest filter: required use tag (default: baseline).")
    parser.add_argument("--split", help="Manifest filter: split value.")
    parser.add_argument("--register", help="Manifest filter: register value.")
    parser.add_argument("--persona", help="Manifest filter: persona value.")
    parser.add_argument("--ai-status", default="pre_ai_human",
                        help="Manifest filter: ai_status (default: pre_ai_human).")
    parser.add_argument("--function-top", type=int, default=100,
                        help="Top function words from baseline (default 100).")
    parser.add_argument("--char-top", type=int, default=200,
                        help="Top character n-grams per n from baseline "
                             "(default 200). Applies separately to "
                             "3-grams, 4-grams, and 5-grams.")
    parser.add_argument("--pos-top", type=int, default=300,
                        help="Top POS trigrams from baseline (default 300).")
    parser.add_argument("--dep-top", type=int, default=300,
                        help="Top dependency-label n-grams from baseline (default 300).")
    parser.add_argument("--top", type=int, default=12,
                        help="Top deviations to show per family (default 12).")
    parser.add_argument("--cluster-top", type=int, default=15,
                        help="Maximum clusters to show per family in the "
                             "cluster table (default 15).")
    parser.add_argument("--cluster-min-features", type=int, default=2,
                        help="Minimum matched features for a cluster to be "
                             "reported (default 2).")
    parser.add_argument("--no-clusters", action="store_true",
                        help="Skip the cluster aggregation pass.")
    parser.add_argument("--no-spacy", action="store_true",
                        help="Skip POS and dependency feature families.")
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    parser.add_argument("--out", help="Write report to file instead of stdout.")
    args = parser.parse_args()

    if not args.baseline_dir and not args.manifest:
        parser.error("Provide either --baseline-dir or --manifest.")

    target_path = Path(args.target)
    baseline_entries = load_entries(
        baseline_dir=args.baseline_dir,
        manifest=args.manifest,
        use=args.use,
        split=args.split,
        register=args.register,
        persona=args.persona,
        ai_status=args.ai_status,
    )
    if not baseline_entries:
        print("No baseline entries matched the requested filters.", file=sys.stderr)
        return 1

    # Drop the target from the baseline if the same file also matched the
    # baseline filter (most often when --baseline-dir contains the target).
    # Including the target self-normalizes the draft being measured: cosine
    # min collapses to 0.0 and z-scores shrink toward the per-feature mean.
    try:
        target_resolved = target_path.resolve()
    except OSError:
        target_resolved = target_path
    filtered: list[dict[str, Any]] = []
    dropped: list[str] = []
    for entry in baseline_entries:
        try:
            entry_resolved = Path(entry["path"]).resolve()
        except OSError:
            entry_resolved = Path(entry["path"])
        if entry_resolved == target_resolved:
            dropped.append(entry["id"])
            continue
        filtered.append(entry)
    if dropped:
        print(
            f"Dropped target file from baseline: {', '.join(dropped)}.",
            file=sys.stderr,
        )
    baseline_entries = filtered
    if not baseline_entries:
        print(
            "Baseline empty after removing the target file. "
            "Point --baseline-dir at a directory that does not contain the target, "
            "or pass a manifest that excludes the target id.",
            file=sys.stderr,
        )
        return 1

    result = compare_to_baseline(
        read_text(target_path),
        baseline_entries,
        include_spacy=not args.no_spacy,
        limits=build_limits(args),
        include_clusters=not args.no_clusters,
        cluster_min_features=args.cluster_min_features,
    )
    result["task_surface"] = TASK_SURFACE

    if args.json:
        output = json.dumps(result, indent=2, default=str)
    else:
        output = render_report(
            result, target_path, args.top, cluster_top=args.cluster_top
        )

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
