#!/usr/bin/env python3
"""
voice_distance.py
Compare a target text against a writer/register baseline using classic
stylometric feature families.

This is a voice-coherence tool, not an AI-provenance detector.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from preprocessing import available_rule_names, strip_non_prose
from stylometry_core import (
    FUNCTION_WORDS,
    compare_to_baseline,
    function_word_features,
    load_entries,
    read_text,
    word_tokens,
)

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_baseline_metadata, build_error_output, build_output  # type: ignore


# Task-surface tag. See variance_audit.TASK_SURFACE for the framework
# contract. Voice-coherence comparison answers "does this draft match
# the writer's prior corpus" - distinct from prose-quality smoothing
# diagnosis. A future validation harness must refuse to mix scores
# across surfaces because they answer different questions.
TASK_SURFACE = "voice_coherence"
TOOL_NAME = "voice_distance"
SCRIPT_VERSION = "1.0"


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


# ---------- Length-matched bootstrap (Phase 1 step 3) ----------


def _function_word_vector(text: str) -> dict[str, float]:
    """Per-function-word relative frequency over the canonical 135-word
    ``FUNCTION_WORDS`` set. Cheap (no SpaCy); used by the bootstrap as
    a proxy for "voice distance at this length" without paying the
    full ``compare_to_baseline`` cost per window.
    """
    return function_word_features(word_tokens(text))


def _content_fingerprint(cleaned_text: str) -> str:
    """sha256 of the WHOLE ``strip_non_prose``-cleaned text — the single string every scored
    feature family reads before its own normalization (``compare_to_baseline`` strips each file
    with these same options, then the function-word / char-n-gram / POS / dependency families
    extract from the result). A manifest entry carrying a copy of the target — even at a DIFFERENT
    path than ``--target`` (the path guard misses a copy under another filename) — would pool the
    target's own vector into its own baseline centroid and collapse the cosine min / Delta toward 0
    (a false "on-voice" result). The content fingerprint self-excludes it alongside the path guard.

    Matcher-aligned (sibling of the Codex self-exclusion sweep: idiolect_detector / originality_audit
    #278 / rank_turbulence_audit #280). The equivalence class is the cleaned string itself, so it is a
    strict SUBSET of every family's class: identical cleaned text ⇒ every family scores it identically
    (safe to DROP), and any punctuation-, case-, or whitespace-distinct baseline the char-n-gram / POS /
    dependency families would treat as distinct has a different cleaned string and is KEPT. Callers must
    pass the ``strip_non_prose`` output computed with the same strip options the comparison uses, so the
    guard never drops a baseline the matcher considers distinct (an earlier ``word_tokens``-stream
    fingerprint folded punctuation/case and over-excluded such baselines — PR #307)."""
    return hashlib.sha256(cleaned_text.encode("utf-8")).hexdigest()


def _baseline_mean_function_word_vector(
    baseline_texts: list[str],
) -> dict[str, float]:
    """Element-wise mean across baseline files' function-word vectors."""
    if not baseline_texts:
        return {w: 0.0 for w in sorted(FUNCTION_WORDS)}
    vectors = [_function_word_vector(t) for t in baseline_texts]
    keys = sorted(FUNCTION_WORDS)
    n = len(vectors)
    return {k: sum(v.get(k, 0.0) for v in vectors) / n for k in keys}


def _manhattan_distance(
    a: dict[str, float], b: dict[str, float],
) -> float:
    """L1 distance between two same-keyed function-word vectors. The
    metric matches the spirit of Burrows-style Delta on the
    function-word family: scale-free aggregate of per-feature gaps."""
    keys = set(a) | set(b)
    return sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys)


def bootstrap_compare(
    target_text: str,
    baseline_entries: list[dict[str, Any]],
    *,
    n_windows_per_file: int = 10,
    max_total_windows: int = 200,
    n_resamples: int = 9999,
    confidence_level: float = 0.95,
    seed: int | None = None,
    allow_non_prose: bool = False,
    strip_rules: str | list[str] | None = None,
    strip_aggressive: bool = False,
) -> dict[str, Any]:
    """Length-matched bootstrap on the function-word distance.

    Phase 1 step 3 of the validation spine for voice_distance. The
    family-level Burrows Delta and cosine distances reported by
    ``compare_to_baseline`` are full-text comparisons; at small target
    N the question "is this Delta large?" has no calibrated answer
    — Delta scales with text length and feature-vocabulary depth.

    The bootstrap takes the cheap-to-compute function-word vector
    distance (L1 distance from a window's per-function-word relative
    frequencies to the baseline corpus's mean function-word vector)
    and samples that distance across many length-N windows of the
    baseline. The empirical distribution becomes the calibrated
    "what's normal voice distance at this length?" reference. The
    target's distance to baseline mean is reported as a percentile
    in that distribution, with a bootstrap CI on the percentile.

    Why function-word vector L1 rather than full Burrows Delta on the
    expanded feature set: the heavier statistic re-extracts every
    feature family (POS bigrams, character n-grams, dependency
    n-grams) per window, which costs minutes per window with SpaCy
    on. The function-word vector is the load-bearing piece of the
    Burrows Delta machinery (it's the only family that contributes
    independent of POS-tagging), is computed in milliseconds per
    window, and produces a calibrated "at this length, baseline
    windows fall in [lo, hi]" range that's directly interpretable.
    A future expansion could add per-family bootstrap once the
    feature-extraction caching path is built.

    Output dict shape:

        {
            "available": True,
            "target_n_words": ...,
            "target_function_word_distance": ...,
            "baseline_distribution": {
                "p05", "p25", "p50", "p75", "p95",
                "min", "max", "mean", "sd",
                "n_samples"
            },
            "bootstrap": {
                "percentile", "ci_low", "ci_high",
                "method", "n_resamples", "n_baseline_windows"
            },
            "config": { ... }
        }

    Returns ``{"available": False, "reason": ...}`` if scipy is not
    installed, the baseline is empty, or no windows were produced.
    """
    try:
        from length_bootstrap import (  # type: ignore
            length_matched_bootstrap, HAS_SCIPY,
        )
    except ImportError:
        return {
            "available": False,
            "reason": "length_bootstrap module not importable",
        }
    if not HAS_SCIPY:
        return {
            "available": False,
            "reason": "scipy not installed; bootstrap CIs unavailable",
        }

    # Apply the same corpus-hygiene stripping the main voice-distance
    # comparison applies. Without this, a CSS / HTML / footer
    # artifact that gets stripped from `compare_to_baseline`'s feature
    # extraction would still contribute to the bootstrap distribution
    # — the percentile and the headline Delta would be measured on
    # different texts, and any contamination would land in the
    # bootstrap reading without being visible in the rest of the
    # report. Strip target and every baseline file before the
    # function-word vectorization runs.
    target_clean, _ = strip_non_prose(
        target_text, strip_rules,
        allow_non_prose=allow_non_prose,
        strip_aggressive=strip_aggressive,
    )
    target_tokens = word_tokens(target_clean)
    target_n_words = len(target_tokens)
    if target_n_words <= 0:
        return {
            "available": False,
            "reason": "target has zero words after preprocessing",
        }

    baseline_texts: list[str] = []
    for entry in baseline_entries:
        try:
            raw = read_text(Path(entry["path"]))
        except (OSError, KeyError):
            continue
        cleaned, _ = strip_non_prose(
            raw, strip_rules,
            allow_non_prose=allow_non_prose,
            strip_aggressive=strip_aggressive,
        )
        if cleaned.strip():
            baseline_texts.append(cleaned)
    if not baseline_texts:
        return {
            "available": False,
            "reason": "no readable baseline files (or all empty after preprocessing)",
        }

    baseline_mean = _baseline_mean_function_word_vector(baseline_texts)
    target_distance = _manhattan_distance(
        _function_word_vector(target_clean), baseline_mean,
    )

    def _stat(window_text: str) -> float | None:
        if not window_text.strip():
            return None
        try:
            return _manhattan_distance(
                _function_word_vector(window_text), baseline_mean,
            )
        except Exception:  # noqa: BLE001
            return None

    result = length_matched_bootstrap(
        baseline_texts,
        statistic_fn=_stat,
        target_value=target_distance,
        target_n_words=target_n_words,
        n_windows_per_file=n_windows_per_file,
        max_total_windows=max_total_windows,
        n_resamples=n_resamples,
        confidence_level=confidence_level,
        seed=seed,
    )
    if not result.get("available"):
        return result

    return {
        "available": True,
        "task_surface": TASK_SURFACE,
        "statistic": "function_word_vector_l1_distance",
        "target_n_words": target_n_words,
        "target_function_word_distance": target_distance,
        # `length_matched_bootstrap` returns
        # `{"n", "quantiles": {p5, p25, p50, p75, p95}, "mean", "sd"}`
        # — preserved as-is for shape parity with variance_audit's
        # `bootstrap_compare`. Do NOT flatten here; the formatter
        # below reads the nested shape (it used to read a
        # never-populated flat shape, producing all-`n/a` cells —
        # the bug the reviewer reproduced).
        "baseline_distribution": result.get("baseline_distribution"),
        "bootstrap": result.get("bootstrap"),
        "config": {
            "n_windows_per_file": n_windows_per_file,
            "max_total_windows": max_total_windows,
            "n_resamples": n_resamples,
            "confidence_level": confidence_level,
            "seed": seed,
            "allow_non_prose": allow_non_prose,
            "strip_rules": strip_rules,
            "strip_aggressive": strip_aggressive,
        },
    }


def format_bootstrap_block(boot: dict[str, Any]) -> list[str]:
    """Markdown-rendered bootstrap section for the report."""
    if not boot.get("available"):
        return [
            "## Length-matched bootstrap",
            "",
            f"_Unavailable: {boot.get('reason', 'unknown')}_",
            "",
        ]
    bd = boot.get("baseline_distribution") or {}
    bs = boot.get("bootstrap") or {}
    pct = bs.get("percentile")
    ci_lo = bs.get("ci_low")
    ci_hi = bs.get("ci_high")
    pct_str = f"{pct:.1%}" if isinstance(pct, (int, float)) else "n/a"
    ci_str = (
        f"[{ci_lo:.1%}, {ci_hi:.1%}]"
        if isinstance(ci_lo, (int, float))
        and isinstance(ci_hi, (int, float))
        else "[n/a, n/a]"
    )
    target_d = boot.get("target_function_word_distance")
    target_d_str = (
        f"{target_d:.4f}" if isinstance(target_d, (int, float)) else "n/a"
    )
    return [
        "## Length-matched bootstrap",
        "",
        "Empirical reference for the function-word distance at the "
        "target's length. Replaces the unanchored "
        "\"is this Delta large?\" question with a calibrated "
        "percentile against baseline-window-to-baseline-mean "
        "distances at the same word count.",
        "",
        f"- **Target function-word L1 distance:** {target_d_str}",
        f"- **Target length (words):** {boot.get('target_n_words')}",
        f"- **Baseline-window samples:** {bs.get('n_baseline_windows', 0)}",
        f"- **Empirical percentile:** {pct_str}  (95% CI {ci_str})",
        f"- **CI method:** `{bs.get('method', 'n/a')}`  "
        f"(`{bs.get('n_resamples', 0)}` resamples)",
        "",
        "### Baseline window distribution at this length",
        "",
        # The shape is `{n, quantiles: {p5, p25, p50, p75, p95}, mean, sd}`.
        # `min` / `max` aren't in the summary; the quantile keys are
        # `p5` (not `p05`). Reading the wrong keys here was the bug
        # that produced all-n/a cells in 1.30.0 markdown reports.
        "| p5 | p25 | p50 | p75 | p95 | mean | sd | n |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
        (
            "| "
            + " | ".join(
                _fmt_dist(_dist_value(bd, k))
                for k in (
                    "p5", "p25", "p50", "p75", "p95",
                    "mean", "sd", "n",
                )
            )
            + " |"
        ),
        "",
        "Reading: a percentile near 1.0 means the target's voice "
        "distance is at the high end of within-baseline scatter at "
        "this length (drift candidate); near 0.5 means it sits near "
        "the typical baseline-window distance from baseline mean "
        "(consistent); near 0.0 is closer to the baseline mean than "
        "most baseline windows are (suspect overfitting / "
        "self-quotation if extreme).",
        "",
    ]


def _fmt_dist(v: Any) -> str:
    if isinstance(v, (int, float)):
        return f"{v:.4f}"
    return "n/a"


def _dist_value(bd: dict[str, Any], key: str) -> Any:
    """Look up a baseline-distribution cell by name.

    `length_matched_bootstrap`'s output nests the percentile cells
    under ``quantiles`` (``p5``, ``p25``, ``p50``, ``p75``, ``p95``)
    and keeps ``mean``, ``sd``, ``n`` at the top level. This helper
    handles both shapes so the table renderer doesn't have to know
    where each cell lives.
    """
    if not isinstance(bd, dict):
        return None
    quantiles = bd.get("quantiles") or {}
    if isinstance(quantiles, dict) and key in quantiles:
        return quantiles[key]
    return bd.get(key)


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
    prep = (result.get("preprocessing") or {}).get("target") or {}
    if prep:
        if prep.get("opt_out"):
            lines.append("**Preprocessing:** skipped by `--allow-non-prose`")
        else:
            ratio = prep.get("strip_ratio", 0.0)
            ratio_str = f"{ratio:.1%}" if isinstance(ratio, (int, float)) else "n/a"
            lines.append(
                f"**Preprocessing:** stripped {prep.get('tokens_stripped', 0)} "
                f"tokens ({ratio_str}; dominant rule: "
                f"{prep.get('dominant_rule') or 'none'})"
            )
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

    rmatch = result.get("register_match")
    if rmatch:
        target_cls = rmatch.get("target_classification", {})
        match_block = rmatch.get("match", {})
        primary = target_cls.get("primary")
        conf = target_cls.get("confidence")
        if primary and primary != "unknown":
            conf_str = f" (confidence {conf:.2f})" if conf else ""
            lines.append("")
            lines.append(
                f"**Target register (heuristic):** `{primary}`"
                f"{conf_str}"
            )
        strength = match_block.get("strength")
        rationale = match_block.get("rationale")
        if strength and strength in {"weak", "mismatch"}:
            lines.append(
                f"**Register match:** ⚠️ `{strength}` — {rationale}"
            )
        elif strength:
            lines.append(
                f"**Register match:** `{strength}` — {rationale}"
            )

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

    boot = result.get("length_matched_bootstrap")
    if boot:
        lines.extend(format_bootstrap_block(boot))

    return "\n".join(lines)


def _baseline_registers(baseline_entries: list[dict[str, Any]]) -> list[str | None]:
    """Extract per-entry register from baseline entries.

    Manifest-loaded entries (`load_entries_from_manifest`) put their
    register under ``entry["metadata"]["register"]``. Directory-
    baseline entries have no register at all. Pre-1.34.1 the code
    read ``entry.get("register")`` directly, which always returned
    ``None`` for manifest-loaded entries — falsely producing
    "mismatch" warnings on every normal manifest run.

    This helper reads both shapes; falls back to top-level
    ``register`` for forward-compat with any future caller that
    supplies it directly.
    """
    out: list[str | None] = []
    for e in baseline_entries:
        metadata = e.get("metadata") or {}
        reg = metadata.get("register") or e.get("register")
        out.append(reg)
    return out


def _build_register_match(
    baseline_entries: list[dict[str, Any]],
    target_primary: str,
) -> dict[str, Any]:
    """Build the register-match block that the voice_distance result
    surfaces. When no baseline entry exposes a register, returns
    ``strength="unavailable"`` rather than calling ``register_match``
    (which would otherwise see all-unknown baseline registers and
    emit a false "mismatch" warning)."""
    from register_classifier import register_match  # type: ignore
    baseline_registers = _baseline_registers(baseline_entries)
    if all(r is None or not r.strip() for r in baseline_registers):
        return {
            "strength": "unavailable",
            "rationale": (
                "Baseline entries don't carry register tags "
                "(directory-baseline or untagged manifest). "
                "Register-match check skipped; supply --manifest "
                "with register-tagged entries to enable."
            ),
            "target": target_primary,
            "baseline_distribution": {},
        }
    return register_match(target_primary, baseline_registers)


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
    parser.add_argument("--include-biber", action="store_true",
                        help="Add the Biber lexico-grammatical register feature family "
                             "(requires a Neurobiber tagger; M2/model-CPU only).")
    parser.add_argument("--allow-non-prose", action="store_true",
                        help="Skip default corpus-hygiene stripping. Use "
                             "only when intentionally measuring code-heavy "
                             "or markup-heavy text.")
    parser.add_argument("--strip-rules",
                        help="Comma-separated preprocessing rules to enable. "
                             "Default: all conservative rules. Available: "
                             + ", ".join(available_rule_names()) + ".")
    parser.add_argument("--strip-aggressive", action="store_true",
                        help="Also strip URL-only lines, image URLs, link "
                             "wrappers, footnotes, and citations.")
    parser.add_argument(
        "--bootstrap", action="store_true",
        help="Run a length-matched bootstrap on the function-word "
             "distance (Phase 1 step 3). Replaces the unanchored "
             "\"is this Delta large?\" question with a calibrated "
             "percentile against baseline-window distances at the "
             "target's word count. Requires scipy.",
    )
    parser.add_argument(
        "--bootstrap-windows-per-file", type=int, default=10,
        help="Length-matched windows sampled per baseline file "
             "(default 10). Capped by --bootstrap-max-windows.",
    )
    parser.add_argument(
        "--bootstrap-max-windows", type=int, default=200,
        help="Cap on total length-matched windows pooled across "
             "baseline files (default 200).",
    )
    parser.add_argument(
        "--bootstrap-resamples", type=int, default=9999,
        help="Bootstrap resamples for the percentile CI (default 9999).",
    )
    parser.add_argument(
        "--bootstrap-confidence", type=float, default=0.95,
        help="Confidence level for the percentile CI (default 0.95).",
    )
    parser.add_argument(
        "--bootstrap-seed", type=int, default=None,
        help="Random seed for reproducible bootstrap sampling "
             "(default: unseeded).",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    parser.add_argument("--out", help="Write report to file instead of stdout.")
    args = parser.parse_args()

    if not args.baseline_dir and not args.manifest:
        parser.error("Provide either --baseline-dir or --manifest.")
    try:
        strip_non_prose(
            "",
            args.strip_rules,
            allow_non_prose=args.allow_non_prose,
            strip_aggressive=args.strip_aggressive,
        )
    except ValueError as exc:
        parser.error(str(exc))

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
    # A copy of the target under a DIFFERENT filename evades the path check,
    # so we also drop any entry whose content fingerprint matches the target
    # (path OR content -> exclude; a content match only DROPS, fail-closed).
    target_text = read_text(target_path)

    def _cleaned(text: str) -> str:
        # Same corpus-hygiene stripping compare_to_baseline applies to every
        # file before feature extraction, so the fingerprint's equivalence
        # class matches what the matcher actually scores (front-matter/footer
        # artifacts that get stripped do not keep a real duplicate in-pool).
        cleaned, _ = strip_non_prose(
            text, args.strip_rules,
            allow_non_prose=args.allow_non_prose,
            strip_aggressive=args.strip_aggressive,
        )
        return cleaned

    target_fingerprint = _content_fingerprint(_cleaned(target_text))
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
        path_match = entry_resolved == target_resolved
        content_match = False
        if not path_match:
            try:
                content_match = (
                    _content_fingerprint(_cleaned(read_text(Path(entry["path"]))))
                    == target_fingerprint
                )
            except (OSError, KeyError):
                content_match = False
        if path_match or content_match:
            dropped.append(entry["id"])
            continue
        filtered.append(entry)
    if dropped:
        print(
            "Dropped target file (or a content-duplicate of it) from "
            f"baseline: {', '.join(dropped)}.",
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

    # --include-biber requires the M2 Neurobiber tagger which is not present
    # in this M1 build. Emit a clean missing_dependency envelope rather than
    # crashing with ValueError inside compare_to_baseline (Codex P1).
    if args.include_biber:
        from biber_features import _try_load_real_tagger  # returns None until M2 (never raises)  # noqa: F401
        _biber_tagger = _try_load_real_tagger()
        if _biber_tagger is None:
            envelope = build_error_output(
                task_surface=TASK_SURFACE,
                tool=TOOL_NAME,
                version=SCRIPT_VERSION,
                target_path=target_path,
                reason=(
                    "--include-biber requires the M2 Neurobiber tagger, which is "
                    "not available in this build. Install the neurobiber package "
                    "and re-run, or omit --include-biber to use the M1 feature "
                    "families (function words, character n-grams, POS, dependencies)."
                ),
                reason_category="missing_dependency",
            )
            print(json.dumps(envelope, indent=2, default=str))
            return 3
    else:
        _biber_tagger = None

    # target_text was read above for the self-exclusion fingerprint; reuse it.
    result = compare_to_baseline(
        target_text,
        baseline_entries,
        include_spacy=not args.no_spacy,
        include_biber=args.include_biber,
        biber_tagger=_biber_tagger,
        limits=build_limits(args),
        include_clusters=not args.no_clusters,
        cluster_min_features=args.cluster_min_features,
        allow_non_prose=args.allow_non_prose,
        strip_rules=args.strip_rules,
        strip_aggressive=args.strip_aggressive,
    )
    result["task_surface"] = TASK_SURFACE

    # Register-match guardrail (Release 1, paired-release schedule).
    # Surfaces a register-mismatch indicator when the target's
    # heuristic register doesn't match the baseline's distribution.
    # Lightweight and honest — the classifier is heuristic, not
    # validated; the value is making register-mismatch *visible*
    # rather than silently producing numbers as if the comparison
    # were clean.
    try:
        from register_classifier import classify_register  # type: ignore
        # Pass the user-supplied --register (when set) as a hint so
        # the classifier nudges toward the declared register if the
        # heuristics are ambiguous.
        target_register_pred = classify_register(
            target_text, hint=args.register,
        )
        match = _build_register_match(
            baseline_entries, target_register_pred.get("primary"),
        )
        result["register_match"] = {
            "target_classification": {
                "primary": target_register_pred.get("primary"),
                "confidence": target_register_pred.get("confidence"),
                "secondary": target_register_pred.get("secondary"),
            },
            "match": match,
        }
    except ImportError:  # pragma: no cover - register_classifier always present
        pass

    if args.bootstrap:
        result["length_matched_bootstrap"] = bootstrap_compare(
            target_text,
            baseline_entries,
            n_windows_per_file=args.bootstrap_windows_per_file,
            max_total_windows=args.bootstrap_max_windows,
            n_resamples=args.bootstrap_resamples,
            confidence_level=args.bootstrap_confidence,
            seed=args.bootstrap_seed,
            allow_non_prose=args.allow_non_prose,
            strip_rules=args.strip_rules,
            strip_aggressive=args.strip_aggressive,
        )

    if args.json:
        payload = build_audit_payload(result, target_path=target_path)
        output = json.dumps(payload, indent=2, default=str)
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


def _claim_license(result: dict[str, Any]) -> ClaimLicense:
    """Structured ClaimLicense for the voice-distance comparison.

    Per ``internal/SPEC_output_schema_unification.md`` §11, scripts
    that lacked a claim_license gain a basic block as part of
    migration.
    """
    target = result.get("target_summary", {}) or {}
    baseline = result.get("baseline_summary", {}) or {}
    overall = result.get("overall", {}) or {}
    register_match = result.get("register_match") or {}
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Stylometric distance from a target text to a writer / "
            "register baseline. Reports per-family distances "
            "(function words, character n-grams, POS trigrams, "
            "dependency n-grams) plus a weighted overall distance "
            "and a verbal drift band. The point of the audit is "
            "voice-coherence comparison: does this draft look like "
            "prior work from the same writer or the same register?"
        ),
        does_not_license=(
            "An AI-provenance verdict. High distance can come from "
            "register shift, audience shift, time drift, deliberate "
            "stylistic experiment, AI editing, or any combination. "
            "Bands are heuristic; the literature reference values "
            "for Burrows Delta and per-feature cosine apply to "
            "specific corpora and may not generalize to the user's "
            "comparison. The differential diagnosis of cause is the "
            "confounder audit's job."
        ),
        comparison_set={
            "n_baseline_files": baseline.get("n_files"),
            "baseline_words": baseline.get("total_words"),
            "target_words": target.get("n_words"),
            "band": overall.get("band"),
            "weighted_delta": overall.get("weighted_delta"),
            "register_match": (
                register_match.get("match", {}).get("verdict")
                if register_match else None
            ),
        },
        additional_caveats=[
            "Voice distance is sensitive to baseline size. Below "
            "~20K baseline words the variance bands widen and the "
            "comparison becomes less stable; pair small-baseline "
            "runs with `--bootstrap` to surface the uncertainty.",
            "Register match is a heuristic indicator, not a "
            "validation. The register classifier is itself a "
            "heuristic; a register mismatch warning means the "
            "comparison is suspect, not that the score is wrong.",
            "Per-family distances can disagree. When the function-"
            "word and character-n-gram families point opposite "
            "directions, the differential diagnosis matters more "
            "than the weighted overall.",
        ],
    )


def build_audit_payload(
    result: dict[str, Any],
    *,
    target_path: Path | str,
) -> dict[str, Any]:
    """Wrap the voice-distance result in the schema_version 1.0
    envelope per ``internal/SPEC_output_schema_unification.md``.
    """
    target = result.get("target_summary", {}) or {}
    baseline = result.get("baseline_summary", {}) or {}
    target_words = int(target.get("n_words", 0) or 0)

    target_extra: dict[str, Any] = {}
    if "preprocessing" in result and result["preprocessing"]:
        target_extra["preprocessing"] = result["preprocessing"]
    # Surface target-summary extras (n_sentences, etc.) under target.
    for k, v in target.items():
        if k != "n_words":
            target_extra[k] = v

    baseline_meta: dict[str, Any] | None = None
    if baseline:
        extras = {
            k: v for k, v in baseline.items()
            if k not in {"n_files", "total_words"}
        }
        baseline_meta = build_baseline_metadata(
            n_files=int(baseline.get("n_files", 0) or 0),
            words=int(baseline.get("total_words", 0) or 0),
            extra=extras or None,
        )

    # Results: everything except metadata + target_summary +
    # baseline_summary + preprocessing.
    excluded = {
        "task_surface", "target_summary", "baseline_summary",
        "preprocessing", "warnings",
    }
    results = {
        k: v for k, v in result.items()
        if k not in excluded
    }

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=baseline_meta,
        results=results,
        claim_license=_claim_license(result),
        warnings=list(result.get("warnings") or []),
        target_extra=target_extra or None,
    )


if __name__ == "__main__":
    sys.exit(main())
