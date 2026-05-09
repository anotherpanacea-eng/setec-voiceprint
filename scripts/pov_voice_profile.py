#!/usr/bin/env python3
"""pov_voice_profile.py — Per-POV-character voiceprints (cathedral #6
sub-item, completes upgrade #6).

For multi-POV fiction (and any other corpus where the manifest tags
documents by an explicit `pov` field), this tracker disaggregates
the writer's baseline by POV character and reports:

  Per-POV voiceprint summary (n_docs, n_words, key features).
  Pairwise POV voice distance matrix (Burrows-Delta + cosine, per
    feature family + weighted aggregate).
  POV-vs-corpus-mean distance: how far each POV is from the
    writer's overall voice.
  Top distinguishing features per POV (where this POV's centroid
    most diverges from the mean of the other POVs).
  Voice-collapse verdict: pairs of POVs whose pairwise Burrows-
    Delta falls below a heuristic threshold are flagged as
    potentially indistinct in voice space — a diagnostic finding
    for the writer to consider.

The static voice_distance.py asks "how far is this draft from the
writer's overall baseline?" voice_drift_tracker.py answers "has the
writer's voice changed across time?" This script answers a third
question that's specific to multi-POV fiction: "are this writer's
POV characters voice-distinct, or has the writer's neutral default
collapsed multiple characters into one voice?"

Privacy: POV voiceprints are voice-cloning input. Default-private
output (refuses paths outside ai-prose-baselines-private/ unless
--allow-public-output is passed; safe override only for non-personal
corpora like Federalist).

Usage:

    # From a manifest with `pov` field:
    python3 scripts/pov_voice_profile.py \\
        --manifest baseline_manifest.jsonl \\
        --use voice_profile \\
        --out ai-prose-baselines-private/pov_profile.md \\
        --json-out ai-prose-baselines-private/pov_profile.json

task_surface: voice_coherence.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from manifest_validator import resolve_path, validate_manifest  # type: ignore
from stylometry_core import (  # type: ignore
    DEFAULT_LIMITS,
    FAMILY_WEIGHTS,
    OVERALL_FAMILY_DELTA_CAP,
    cosine_distance,
    extract_features,
    feature_vector,
    select_feature_names,
    vector_stats,
)


TASK_SURFACE = "voice_coherence"
TOOL_NAME = "pov_voice_profile"

DEFAULT_MIN_DOCS_PER_POV = 2
DEFAULT_TOP_DISTINGUISHING = 15

# Heuristic threshold: pairs of POVs whose Burrows-Delta falls below
# this are flagged as "potentially voice-collapsed." This is a soft
# threshold for diagnostic guidance, not a calibrated cutoff. The
# 0.5 value is in line with the empirical Federalist H-vs-M Burrows-
# Delta range on this fixture (~1.4 between authors, ~0.5-1.0
# within-author); collapsed POVs in fiction would land below the
# within-author noise floor.
DEFAULT_COLLAPSE_THRESHOLD = 0.5


# --------------- Entry loading ------------------------------


@dataclass
class POVEntry:
    id: str
    path: Path
    pov: str
    extra: dict[str, Any]


def _load_manifest_entries(
    manifest_path: Path, use_tag: str,
) -> list[POVEntry]:
    """Read manifest JSONL, filter by `use` tag, require non-empty
    `pov` field. Skips entries with missing pov with a stderr
    warning."""
    out: list[POVEntry] = []
    skipped = 0
    for lineno, raw in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            sys.stderr.write(
                f"Skipping line {lineno}: malformed JSON.\n"
            )
            skipped += 1
            continue
        if not isinstance(entry, dict):
            continue
        use = entry.get("use")
        if not (isinstance(use, list) and use_tag in use):
            continue
        if "exclude" in (use or []):
            continue
        pov = entry.get("pov")
        if not isinstance(pov, str) or not pov.strip():
            sys.stderr.write(
                f"Skipping {entry.get('id', f'line_{lineno}')}: "
                f"missing or empty pov field.\n"
            )
            skipped += 1
            continue
        raw_path = entry.get("path")
        if not isinstance(raw_path, str):
            continue
        resolved = resolve_path(manifest_path, raw_path)
        out.append(POVEntry(
            id=str(entry.get("id") or f"line_{lineno}"),
            path=resolved,
            pov=pov.strip(),
            extra={
                k: v for k, v in entry.items()
                if k not in {"id", "path", "use", "pov"}
            },
        ))
    if skipped:
        sys.stderr.write(
            f"Skipped {skipped} manifest entry/entries with missing "
            f"pov.\n"
        )
    return out


def group_by_pov(
    entries: Sequence[POVEntry],
    *,
    min_docs_per_pov: int = DEFAULT_MIN_DOCS_PER_POV,
) -> tuple[dict[str, list[POVEntry]], list[str]]:
    grouped: dict[str, list[POVEntry]] = defaultdict(list)
    for e in entries:
        grouped[e.pov].append(e)
    kept: dict[str, list[POVEntry]] = {}
    dropped: list[str] = []
    for pov, items in grouped.items():
        if len(items) >= min_docs_per_pov:
            kept[pov] = items
        else:
            dropped.append(pov)
    return kept, dropped


# --------------- Per-POV voiceprint -------------------------


@dataclass
class POVProfile:
    label: str
    n_docs: int
    n_words: int
    feature_items: list[dict[str, Any]]
    pov_centroids: dict[str, dict[str, float]]


def build_pov_profiles(
    grouped: dict[str, list[POVEntry]],
) -> tuple[dict[str, POVProfile], dict[str, list[str]]]:
    """For each POV, run extract_features on every doc and compute
    the POV's centroid (per-family per-doc mean across docs).
    Returns (profiles_by_pov, selected_features_by_family)."""
    items_by_pov: dict[str, list[dict[str, Any]]] = {}
    n_docs_by_pov: dict[str, int] = {}
    n_words_by_pov: dict[str, int] = {}

    for pov, entries in grouped.items():
        items: list[dict[str, Any]] = []
        n_words = 0
        for e in entries:
            text = e.path.read_text(encoding="utf-8", errors="ignore")
            feats = extract_features(text)
            items.append({"id": e.id, "features": feats["features"]})
            n_words += int(feats.get("summary", {}).get("n_words", 0))
        items_by_pov[pov] = items
        n_docs_by_pov[pov] = len(items)
        n_words_by_pov[pov] = n_words

    # Select feature names ONCE over the union of all POVs so the
    # cross-POV distance matrix lives in a shared feature space.
    all_items: list[dict[str, Any]] = []
    for items in items_by_pov.values():
        all_items.extend(items)
    selected_features = select_feature_names(all_items, limits=DEFAULT_LIMITS)

    profiles: dict[str, POVProfile] = {}
    for pov, items in items_by_pov.items():
        centroids: dict[str, dict[str, float]] = {}
        for family, names in selected_features.items():
            if not names:
                continue
            per_doc_vectors = [feature_vector(it, family, names) for it in items]
            centroid: dict[str, float] = {}
            for name in names:
                vals = [v.get(name, 0.0) for v in per_doc_vectors]
                centroid[name] = (
                    sum(vals) / len(vals) if vals else 0.0
                )
            centroids[family] = centroid
        profiles[pov] = POVProfile(
            label=pov,
            n_docs=n_docs_by_pov[pov],
            n_words=n_words_by_pov[pov],
            feature_items=items,
            pov_centroids=centroids,
        )
    return profiles, selected_features


# --------------- Cross-POV distance -------------------------


def _informative_keys(
    stats: dict[str, dict[str, float]],
    names: Sequence[str],
) -> list[str]:
    return [n for n in names if stats.get(n, {}).get("sd", 0.0) > 0.0]


def _pair_burrows_delta(
    centroid_a: dict[str, float],
    centroid_b: dict[str, float],
    informative: Sequence[str],
    stats: dict[str, dict[str, float]],
) -> float | None:
    if not informative:
        return None
    total = 0.0
    for name in informative:
        s = stats[name]
        sd = s["sd"]
        mean = s["mean"]
        z_a = (centroid_a.get(name, 0.0) - mean) / sd
        z_b = (centroid_b.get(name, 0.0) - mean) / sd
        total += abs(z_a - z_b)
    return total / len(informative)


def cross_pov_distances(
    profiles: dict[str, POVProfile],
    selected_features: dict[str, list[str]],
) -> dict[str, dict[tuple[str, str], dict[str, float | None]]]:
    """For each family, pairwise Burrows-Delta + cosine between POV
    centroids. Z-score column stats computed over the per-DOCUMENT
    feature vectors across all POVs (NOT over POV centroids alone).

    Why per-doc and not per-centroid: with K=2 POVs, stats over only
    the two centroids force every informative feature to symmetric
    z-scores ±sqrt(2)/2, collapsing |z_a - z_b| to a constant sqrt(2)
    regardless of magnitude. Per-document stats restore the
    magnitude signal: a small centroid shift relative to within-POV
    dispersion gives small z-deltas; a large centroid shift gives
    large z-deltas. Mirrors the fix in voice_drift_tracker.cross_
    period_distances and the convention in voice_validation_harness.
    """
    out: dict[str, dict[tuple[str, str], dict[str, float | None]]] = {}
    pov_labels = sorted(profiles.keys())
    for family, names in selected_features.items():
        if not names:
            continue
        per_doc_vectors: list[dict[str, float]] = []
        for p in pov_labels:
            for item in profiles[p].feature_items:
                per_doc_vectors.append(feature_vector(item, family, names))
        stats = vector_stats(per_doc_vectors, names)
        informative = _informative_keys(stats, names)
        family_distances: dict[tuple[str, str], dict[str, float | None]] = {}
        for i, p_a in enumerate(pov_labels):
            for j, p_b in enumerate(pov_labels):
                if i >= j:
                    continue
                vec_a = profiles[p_a].pov_centroids.get(family, {})
                vec_b = profiles[p_b].pov_centroids.get(family, {})
                delta = _pair_burrows_delta(vec_a, vec_b, informative, stats)
                cos = cosine_distance(vec_a, vec_b, names)
                family_distances[(p_a, p_b)] = {
                    "burrows_delta": delta,
                    "cosine_distance": cos,
                }
        if family_distances:
            out[family] = family_distances
    return out


def weighted_cross_pov_distances(
    family_distances: dict[str, dict[tuple[str, str], dict[str, float | None]]],
) -> dict[tuple[str, str], dict[str, float | None]]:
    """Aggregate per-family pairwise distances into a weighted overall
    using FAMILY_WEIGHTS and OVERALL_FAMILY_DELTA_CAP. Mirrors
    voice_validation_harness's weighted-family aggregate."""
    pair_keys: set[tuple[str, str]] = set()
    for fam_distances in family_distances.values():
        pair_keys.update(fam_distances.keys())
    out: dict[tuple[str, str], dict[str, float | None]] = {}
    for pair in pair_keys:
        delta_contribs: list[tuple[float, float]] = []
        cos_contribs: list[tuple[float, float]] = []
        for family, fam_distances in family_distances.items():
            row = fam_distances.get(pair)
            if not row:
                continue
            weight = FAMILY_WEIGHTS.get(family, 1.0)
            delta = row.get("burrows_delta")
            if isinstance(delta, (int, float)):
                delta_contribs.append((weight, min(delta, OVERALL_FAMILY_DELTA_CAP)))
            cos = row.get("cosine_distance")
            if isinstance(cos, (int, float)):
                cos_contribs.append((weight, cos))
        delta_total = (
            sum(w * d for w, d in delta_contribs) / sum(w for w, _ in delta_contribs)
            if delta_contribs else None
        )
        cos_total = (
            sum(w * c for w, c in cos_contribs) / sum(w for w, _ in cos_contribs)
            if cos_contribs else None
        )
        out[pair] = {
            "burrows_delta": delta_total,
            "cosine_distance": cos_total,
        }
    return out


# --------------- POV vs. corpus mean ------------------------


def pov_vs_corpus_mean_distances(
    profiles: dict[str, POVProfile],
    selected_features: dict[str, list[str]],
) -> dict[str, dict[str, float | None]]:
    """For each POV, compute the distance between its centroid and
    the corpus-mean centroid (mean across all POV centroids).
    Returns {pov_label: {burrows_delta, cosine_distance}} (weighted
    aggregate across families). Use case: "which POV is closest to
    the writer's neutral default?" Often the writer's first-person
    or default-narrator POV.
    """
    pov_labels = sorted(profiles.keys())
    if len(pov_labels) < 2:
        return {}
    out: dict[str, dict[str, float | None]] = {}
    # Compute corpus-mean centroid per family
    corpus_centroids: dict[str, dict[str, float]] = {}
    for family, names in selected_features.items():
        if not names:
            continue
        per_pov_centroids = [profiles[p].pov_centroids.get(family, {}) for p in pov_labels]
        corpus_centroid = {}
        for name in names:
            vals = [c.get(name, 0.0) for c in per_pov_centroids]
            corpus_centroid[name] = sum(vals) / len(vals)
        corpus_centroids[family] = corpus_centroid
    # Build per-document stats once across all POVs (the population
    # we z-score against). Same fix as cross_pov_distances:
    # centroid-only stats collapse to a constant when K is small.
    per_doc_stats_by_family: dict[str, dict[str, dict[str, float]]] = {}
    informative_by_family: dict[str, list[str]] = {}
    for family, names in selected_features.items():
        if not names:
            continue
        per_doc_vectors: list[dict[str, float]] = []
        for p in pov_labels:
            for item in profiles[p].feature_items:
                per_doc_vectors.append(feature_vector(item, family, names))
        stats = vector_stats(per_doc_vectors, names)
        per_doc_stats_by_family[family] = stats
        informative_by_family[family] = _informative_keys(stats, names)

    # For each POV, compute weighted distance to corpus mean
    for pov in pov_labels:
        delta_contribs: list[tuple[float, float]] = []
        cos_contribs: list[tuple[float, float]] = []
        for family, names in selected_features.items():
            if not names:
                continue
            pov_centroid = profiles[pov].pov_centroids.get(family, {})
            corpus_centroid = corpus_centroids[family]
            stats = per_doc_stats_by_family[family]
            informative = informative_by_family[family]
            delta = _pair_burrows_delta(
                pov_centroid, corpus_centroid, informative, stats,
            )
            cos = cosine_distance(pov_centroid, corpus_centroid, names)
            weight = FAMILY_WEIGHTS.get(family, 1.0)
            if isinstance(delta, (int, float)):
                delta_contribs.append((weight, min(delta, OVERALL_FAMILY_DELTA_CAP)))
            if isinstance(cos, (int, float)):
                cos_contribs.append((weight, cos))
        delta_agg = (
            sum(w * d for w, d in delta_contribs) / sum(w for w, _ in delta_contribs)
            if delta_contribs else None
        )
        cos_agg = (
            sum(w * c for w, c in cos_contribs) / sum(w for w, _ in cos_contribs)
            if cos_contribs else None
        )
        out[pov] = {"burrows_delta": delta_agg, "cosine_distance": cos_agg}
    return out


# --------------- Distinguishing features --------------------


def distinguishing_features(
    profiles: dict[str, POVProfile],
    selected_features: dict[str, list[str]],
    *,
    top_n: int,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """For each POV, identify the top N features where this POV's
    centroid most diverges from the mean of the OTHER POVs (not the
    overall corpus mean — that would dilute the comparison).

    Returns {pov: {family: [{feature, this_pov_value, others_mean,
    delta, log2_ratio}, ...]}}.
    """
    pov_labels = sorted(profiles.keys())
    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for pov in pov_labels:
        out[pov] = {}
        for family, names in selected_features.items():
            if not names:
                continue
            this_centroid = profiles[pov].pov_centroids.get(family, {})
            other_centroids = [
                profiles[p].pov_centroids.get(family, {})
                for p in pov_labels if p != pov
            ]
            if not other_centroids:
                continue
            rows: list[dict[str, Any]] = []
            for name in names:
                this_v = this_centroid.get(name, 0.0)
                other_vals = [c.get(name, 0.0) for c in other_centroids]
                others_mean = sum(other_vals) / len(other_vals)
                delta = this_v - others_mean
                # log2 ratio with epsilon to avoid log(0)
                eps = 1e-9
                log2_ratio = math.log2(max(this_v, eps) / max(others_mean, eps))
                rows.append({
                    "feature": name,
                    "this_pov_value": this_v,
                    "others_mean": others_mean,
                    "delta": delta,
                    "log2_ratio": log2_ratio,
                })
            # Sort by abs delta (the most distinctive features by
            # absolute relative-frequency divergence)
            rows.sort(key=lambda r: abs(r["delta"]), reverse=True)
            out[pov][family] = rows[:top_n]
    return out


# --------------- Voice-collapse verdict ---------------------


def voice_collapse_verdict(
    weighted: dict[tuple[str, str], dict[str, float | None]],
    *,
    threshold: float,
) -> list[dict[str, Any]]:
    """Identify pairs of POVs whose weighted Burrows-Delta falls
    below the threshold — flagged as "potentially voice-collapsed."
    The threshold is heuristic; calibration is roadmap. Returns a
    list of collapsed pairs sorted by distance ascending."""
    rows: list[dict[str, Any]] = []
    for (pa, pb), row in weighted.items():
        delta = row.get("burrows_delta")
        if delta is None:
            continue
        if delta < threshold:
            rows.append({
                "pov_a": pa,
                "pov_b": pb,
                "burrows_delta": delta,
                "cosine_distance": row.get("cosine_distance"),
                "verdict": "potentially_collapsed",
                "threshold": threshold,
            })
    rows.sort(key=lambda r: r["burrows_delta"])
    return rows


# --------------- Privacy guard ------------------------------


def _check_output_privacy(
    paths: list[Path | None], *, allow_public: bool,
) -> None:
    if allow_public:
        return
    repo_root = Path(__file__).resolve().parent.parent
    private_dir = repo_root / "ai-prose-baselines-private"
    for p in paths:
        if p is None:
            continue
        try:
            p.resolve().relative_to(private_dir.resolve())
        except ValueError:
            sys.stderr.write(
                f"Refusing to write {p} outside {private_dir}. POV "
                f"voiceprints are voice-cloning input. Pass "
                f"--allow-public-output to override (only for non-"
                f"personal corpora like Federalist).\n"
            )
            sys.exit(2)


# --------------- Output rendering ---------------------------


CLAIM_LICENSE = {
    "licenses": (
        "Per-POV voiceprint summary, pairwise POV voice-distance "
        "matrix, POV-vs-corpus-mean distance, top distinguishing "
        "features per POV, and a heuristic voice-collapse verdict "
        "flagging pairs of POVs whose pairwise Burrows-Delta falls "
        "below the configured threshold."
    ),
    "does_not_license": (
        "AI provenance, authorship attribution across writers, or a "
        "claim that the writer's POVs are 'good' or 'bad.' The "
        "voice-collapse verdict is a diagnostic flag, not a craft "
        "judgment — collapsed POVs may be intentional (close third "
        "with limited POV differentiation) or symptomatic (writer's "
        "neutral default leaking into multiple characters); the "
        "writer's local read decides."
    ),
}


def render_json(
    *,
    profiles: dict[str, POVProfile],
    family_distances: dict[str, dict[tuple[str, str], dict[str, float | None]]],
    weighted_distances: dict[tuple[str, str], dict[str, float | None]],
    pov_vs_mean: dict[str, dict[str, float | None]],
    distinguishing: dict[str, dict[str, list[dict[str, Any]]]],
    collapse_verdict: list[dict[str, Any]],
    dropped_povs: list[str],
    inputs: dict[str, Any],
) -> str:
    pov_labels = sorted(profiles.keys())
    pov_summary = [
        {
            "label": p,
            "n_docs": profiles[p].n_docs,
            "n_words": profiles[p].n_words,
        }
        for p in pov_labels
    ]
    family_distance_serialized: dict[str, list[dict[str, Any]]] = {}
    for family, pairs in family_distances.items():
        family_distance_serialized[family] = [
            {
                "pov_a": pa, "pov_b": pb,
                "burrows_delta": row.get("burrows_delta"),
                "cosine_distance": row.get("cosine_distance"),
            }
            for (pa, pb), row in sorted(pairs.items())
        ]
    weighted_serialized = [
        {
            "pov_a": pa, "pov_b": pb,
            "burrows_delta": row.get("burrows_delta"),
            "cosine_distance": row.get("cosine_distance"),
        }
        for (pa, pb), row in sorted(weighted_distances.items())
    ]
    out = {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "inputs": inputs,
        "claim_license": CLAIM_LICENSE,
        "n_povs": len(pov_labels),
        "povs": pov_summary,
        "dropped_povs": dropped_povs,
        "cross_pov_distances_per_family": family_distance_serialized,
        "cross_pov_distances_weighted": weighted_serialized,
        "pov_vs_corpus_mean": pov_vs_mean,
        "distinguishing_features": distinguishing,
        "voice_collapse_verdict": collapse_verdict,
    }
    return json.dumps(out, indent=2, ensure_ascii=False)


def render_markdown(
    *,
    profiles: dict[str, POVProfile],
    weighted_distances: dict[tuple[str, str], dict[str, float | None]],
    pov_vs_mean: dict[str, dict[str, float | None]],
    distinguishing: dict[str, dict[str, list[dict[str, Any]]]],
    collapse_verdict: list[dict[str, Any]],
    dropped_povs: list[str],
    collapse_threshold: float,
) -> str:
    pov_labels = sorted(profiles.keys())
    lines: list[str] = [
        "# Per-POV Voiceprint Report",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**POVs:** {len(pov_labels)} ({', '.join(pov_labels)})",
        "",
        f"**Reports:** {CLAIM_LICENSE['licenses']}",
        "",
        f"**Does NOT report:** {CLAIM_LICENSE['does_not_license']}",
        "",
    ]

    if dropped_povs:
        lines.append("## Dropped POVs (insufficient data)")
        lines.append("")
        for p in sorted(dropped_povs):
            lines.append(f"- `{p}`")
        lines.append("")

    lines.append("## Per-POV summary")
    lines.append("")
    lines.append("| POV | n docs | n words |")
    lines.append("|---|---:|---:|")
    for p in pov_labels:
        prof = profiles[p]
        lines.append(f"| `{p}` | {prof.n_docs} | {prof.n_words} |")
    lines.append("")

    if weighted_distances and len(pov_labels) > 1:
        lines.append("## Cross-POV voice distance (weighted aggregate)")
        lines.append("")
        lines.append(
            "Pairwise Burrows-Delta and cosine between POV "
            "centroids in a shared feature space. Higher = more "
            "voice-distinct."
        )
        lines.append("")
        lines.append("| POV A | POV B | Burrows-Delta | Cosine |")
        lines.append("|---|---|---:|---:|")
        for (pa, pb), row in sorted(weighted_distances.items()):
            lines.append(
                f"| `{pa}` | `{pb}` | {_fmt(row.get('burrows_delta'))} | "
                f"{_fmt(row.get('cosine_distance'))} |"
            )
        lines.append("")

    if collapse_verdict:
        lines.append("## Voice-collapse flag")
        lines.append("")
        lines.append(
            f"POV pairs with weighted Burrows-Delta below the "
            f"heuristic threshold `{collapse_threshold}` are flagged "
            f"as potentially voice-collapsed. This is a diagnostic, "
            f"not a craft judgment — collapsed POVs may be "
            f"intentional (close third with limited POV "
            f"differentiation) or symptomatic. Inspect locally."
        )
        lines.append("")
        lines.append("| POV A | POV B | Burrows-Delta | Verdict |")
        lines.append("|---|---|---:|---|")
        for row in collapse_verdict:
            lines.append(
                f"| `{row['pov_a']}` | `{row['pov_b']}` | "
                f"{_fmt(row['burrows_delta'])} | {row['verdict']} |"
            )
        lines.append("")

    if pov_vs_mean:
        lines.append("## POV vs. corpus mean")
        lines.append("")
        lines.append(
            "Each POV's distance from the corpus-mean centroid "
            "(mean across all POVs). Smaller = closer to the "
            "writer's neutral default; useful for identifying "
            "which POV is the writer's home register."
        )
        lines.append("")
        lines.append("| POV | Burrows-Delta | Cosine |")
        lines.append("|---|---:|---:|")
        for pov, row in sorted(pov_vs_mean.items()):
            lines.append(
                f"| `{pov}` | {_fmt(row.get('burrows_delta'))} | "
                f"{_fmt(row.get('cosine_distance'))} |"
            )
        lines.append("")

    if distinguishing:
        lines.append("## Top distinguishing features per POV")
        lines.append("")
        lines.append(
            "For each POV, the features where this POV's centroid "
            "most diverges from the MEAN of the OTHER POVs (not the "
            "overall corpus mean, which would dilute the comparison "
            "by including this POV itself). Surfaced for "
            "`function_words` and `pos_trigrams` only — these are "
            "the families most interpretable as voice markers; "
            "char-ngrams and dep-n-grams stay in the JSON output "
            "for richer downstream consumption."
        )
        lines.append("")
        for pov in pov_labels:
            pov_features = distinguishing.get(pov, {})
            for family in ("function_words", "pos_trigrams"):
                rows = pov_features.get(family, [])
                if not rows:
                    continue
                lines.append(f"### `{pov}` — `{family}`")
                lines.append("")
                lines.append("| Feature | This POV | Others mean | Δ | log₂ ratio |")
                lines.append("|---|---:|---:|---:|---:|")
                for row in rows[:10]:
                    lines.append(
                        f"| `{row['feature']}` | "
                        f"{_fmt(row['this_pov_value'])} | "
                        f"{_fmt(row['others_mean'])} | "
                        f"{_fmt(row['delta'])} | "
                        f"{_fmt(row['log2_ratio'])} |"
                    )
                lines.append("")

    return "\n".join(lines) + "\n"


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if not math.isfinite(v):
            return "∞"
        if abs(v) >= 100:
            return f"{v:.2f}"
        if abs(v) >= 1:
            return f"{v:.4f}"
        return f"{v:.5f}"
    return str(v)


# --------------- CLI ---------------------------------------


def run(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest)
    validation = validate_manifest(str(manifest_path))
    if validation["n_errors"] > 0:
        raise SystemExit(
            f"Manifest validation failed with "
            f"{validation['n_errors']} error(s)."
        )

    entries = _load_manifest_entries(manifest_path, args.use)
    if not entries:
        raise SystemExit(
            f"No POV-tagged entries with use={args.use!r} in "
            f"{manifest_path}. Add a `pov` field to manifest entries "
            f"or pick a different --use tag."
        )

    grouped, dropped = group_by_pov(
        entries, min_docs_per_pov=args.min_docs_per_pov,
    )
    if len(grouped) < 2:
        raise SystemExit(
            f"POV voice profiling requires at least 2 POVs with "
            f"{args.min_docs_per_pov}+ documents each. After "
            f"filtering, only {len(grouped)} POV(s) remain. "
            f"Dropped: {dropped}. Either lower --min-docs-per-pov "
            f"or add more documents per POV."
        )

    profiles, selected_features = build_pov_profiles(grouped)
    family_distances = cross_pov_distances(profiles, selected_features)
    weighted = weighted_cross_pov_distances(family_distances)
    pov_vs_mean = pov_vs_corpus_mean_distances(profiles, selected_features)
    distinguishing = distinguishing_features(
        profiles, selected_features, top_n=args.top_distinguishing,
    )
    collapse = voice_collapse_verdict(
        weighted, threshold=args.collapse_threshold,
    )

    inputs = {
        "manifest": args.manifest,
        "use": args.use,
        "min_docs_per_pov": args.min_docs_per_pov,
        "collapse_threshold": args.collapse_threshold,
    }
    return {
        "profiles": profiles,
        "family_distances": family_distances,
        "weighted_distances": weighted,
        "pov_vs_mean": pov_vs_mean,
        "distinguishing": distinguishing,
        "collapse_verdict": collapse,
        "dropped_povs": dropped,
        "inputs": inputs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Per-POV-character voiceprints for multi-POV fiction. "
            "Reports pairwise POV distance + voice-collapse verdict."
        )
    )
    parser.add_argument(
        "--manifest", required=True,
        help="JSONL manifest with `pov` field on selected entries.",
    )
    parser.add_argument(
        "--use", default="voice_profile",
        help="Manifest `use` tag to filter entries (default voice_profile).",
    )
    parser.add_argument(
        "--min-docs-per-pov", type=int, default=DEFAULT_MIN_DOCS_PER_POV,
    )
    parser.add_argument(
        "--top-distinguishing", type=int, default=DEFAULT_TOP_DISTINGUISHING,
    )
    parser.add_argument(
        "--collapse-threshold", type=float, default=DEFAULT_COLLAPSE_THRESHOLD,
        help=(
            "Burrows-Delta threshold below which a POV pair is flagged "
            "as potentially voice-collapsed. Heuristic; calibration "
            "roadmap."
        ),
    )
    parser.add_argument("--out", help="Markdown output path.")
    parser.add_argument("--json-out", help="JSON output path.")
    parser.add_argument(
        "--allow-public-output", action="store_true",
        help=(
            "Allow output outside ai-prose-baselines-private/. POV "
            "voiceprints are voice-cloning input; default-private."
        ),
    )

    args = parser.parse_args(argv)

    out_path = Path(args.out) if args.out else None
    json_path = Path(args.json_out) if args.json_out else None
    _check_output_privacy(
        [out_path, json_path], allow_public=args.allow_public_output,
    )

    result = run(args)

    json_str = render_json(
        profiles=result["profiles"],
        family_distances=result["family_distances"],
        weighted_distances=result["weighted_distances"],
        pov_vs_mean=result["pov_vs_mean"],
        distinguishing=result["distinguishing"],
        collapse_verdict=result["collapse_verdict"],
        dropped_povs=result["dropped_povs"],
        inputs=result["inputs"],
    )
    md = render_markdown(
        profiles=result["profiles"],
        weighted_distances=result["weighted_distances"],
        pov_vs_mean=result["pov_vs_mean"],
        distinguishing=result["distinguishing"],
        collapse_verdict=result["collapse_verdict"],
        dropped_povs=result["dropped_povs"],
        collapse_threshold=args.collapse_threshold,
    )

    if json_path:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json_str, encoding="utf-8")
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
    if not json_path and not out_path:
        # Stdout is also voice-cloning-sensitive output. The privacy
        # guard's path-based check would have blocked any file
        # outside ai-prose-baselines-private/; stdout has no path
        # and was previously a hole. Refuse stdout writes unless
        # --allow-public-output is passed.
        if not args.allow_public_output:
            sys.stderr.write(
                "Refusing to write per-POV voiceprint report to "
                "stdout without --allow-public-output. POV "
                "voiceprints are voice-cloning input; default-"
                "private posture requires either --out / --json-out "
                "into ai-prose-baselines-private/, or --allow-public-"
                "output for non-personal corpora (e.g., Federalist).\n"
            )
            return 2
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
