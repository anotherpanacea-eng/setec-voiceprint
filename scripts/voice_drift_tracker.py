#!/usr/bin/env python3
"""voice_drift_tracker.py — time-drift surface (cathedral #6 sub-item).

Tracks how a writer's voice changes across time periods. Reads
date-tagged baseline documents from a manifest (with `date_written`)
or a directory (with date-prefixed filenames), groups into periods
at the requested granularity (year / quarter / month / custom), and
reports:

  Per-period voiceprint summary (n_docs, n_words, key features).
  Cross-period voice-distance matrix (Burrows-Delta + cosine, per
    feature family + weighted aggregate).
  Drifting features (high cross-period variance relative to within-
    period dispersion).
  Stable features (low cross-period variance — the writer's
    durable idiolect).

The static voice-distance score (`voice_distance.py`) asks "how far
is this draft from the writer's overall baseline?" without
distinguishing recent baseline from old. This tracker disaggregates
the baseline by time so the writer can see whether the "drift"
between draft and baseline is recent stylistic evolution or sudden
distortion. Pairs with `voice_distance.py` and `voice_profile.py`;
sibling under cathedral upgrade #6.

Privacy: voice drift is voice-cloning input. By default, output
paths must be inside `ai-prose-baselines-private/`. Pass
`--allow-public-output` to override (only safe for non-personal
corpora, e.g., Federalist).

Usage:

    # From a manifest with date_written entries:
    python3 scripts/voice_drift_tracker.py \\
        --manifest baseline_manifest.jsonl \\
        --use voice_profile \\
        --period-granularity year \\
        --out ai-prose-baselines-private/drift.md \\
        --json-out ai-prose-baselines-private/drift.json

    # From a directory with date-prefixed filenames (e.g., "2022-04_essay.md"):
    python3 scripts/voice_drift_tracker.py \\
        --baseline-dir baseline/ \\
        --date-pattern '(\\d{4}-\\d{2})' \\
        --period-granularity year \\
        --out drift.md

    # Custom period boundaries (e.g., pre-AI vs. post-AI):
    python3 scripts/voice_drift_tracker.py \\
        --manifest baseline_manifest.jsonl \\
        --period-granularity custom \\
        --period-boundaries 2023-01-01 \\
        --out drift.md

task_surface: voice_coherence.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import re
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
TOOL_NAME = "voice_drift_tracker"

DEFAULT_GRANULARITY = "year"
DEFAULT_MIN_DOCS_PER_PERIOD = 2
DEFAULT_TOP_DRIFTING = 15
DEFAULT_TOP_STABLE = 15

GRANULARITIES = ("year", "quarter", "month", "custom")

# Families to surface in the drift report. POS / dep require spaCy;
# absent families silently drop out.
FAMILY_NAMES = (
    "function_words",
    "char_ngrams_3",
    "char_ngrams_4",
    "char_ngrams_5",
    "pos_trigrams",
    "dependency_ngrams",
)


# --------------- Date parsing -------------------------------


# Strict ISO format: YYYY, YYYY-MM, or YYYY-MM-DD with no trailing
# garbage. Anchored at both ends so values like "2020-01-foo" are
# rejected outright (silent suffix-stripping was a real risk for
# misclassifying drift periods).
_ISO_DATE_RE = re.compile(
    r"^(?P<y>\d{4})(?:-(?P<m>\d{2})(?:-(?P<d>\d{2}))?)?$"
)


def _parse_iso_date(s: str) -> tuple[int, int, int] | None:
    """Parse a strict ISO date (year, year-month, or year-month-day).
    Returns (year, month_or_0, day_or_0) or None on failure. Coarser
    dates (year-only or year-month) get the missing components set
    to 0; the caller decides how to handle them when grouping into
    periods.

    Strictness: the format must be exactly ``YYYY``, ``YYYY-MM``, or
    ``YYYY-MM-DD`` with no trailing characters. Full year-month-day
    values are validated via ``datetime.date`` so impossible dates
    like ``2020-02-31`` are rejected (they would otherwise silently
    misclassify documents into periods). Year-month partials get a
    range check on the month component.
    """
    if not isinstance(s, str):
        return None
    m = _ISO_DATE_RE.match(s.strip())
    if not m:
        return None
    try:
        y = int(m.group("y"))
        mo = int(m.group("m")) if m.group("m") else 0
        d = int(m.group("d")) if m.group("d") else 0
    except (ValueError, TypeError):
        return None
    if y < 1500 or y > 3000:
        return None
    # Full date: validate via datetime.date so impossible dates
    # (Feb 31, Apr 31, etc.) are rejected.
    if mo and d:
        try:
            _dt.date(y, mo, d)
        except ValueError:
            return None
    # Year-month partial: month range check only.
    elif mo:
        if mo < 1 or mo > 12:
            return None
    return y, mo, d


def _period_key(
    date_tuple: tuple[int, int, int],
    granularity: str,
    custom_boundaries: list[tuple[int, int, int]] | None = None,
) -> str:
    """Map a parsed date to the period label at the requested
    granularity. Coarse dates get a "(unknown)" suffix when the
    granularity needs sub-year precision."""
    y, mo, d = date_tuple
    if granularity == "year":
        return f"{y:04d}"
    if granularity == "quarter":
        if not mo:
            return f"{y:04d}-Q?"
        q = (mo - 1) // 3 + 1
        return f"{y:04d}-Q{q}"
    if granularity == "month":
        if not mo:
            return f"{y:04d}-??"
        return f"{y:04d}-{mo:02d}"
    if granularity == "custom":
        if not custom_boundaries:
            return "all"
        # Find the range the date falls into. Boundaries split into
        # N+1 intervals: [..., b0, b1, ..., bN, ...]. Each interval is
        # [b_i, b_{i+1}). Sentinel labels: "before_<b0>", "<bi>_to_<b{i+1}>",
        # "after_<bN>".
        sorted_bounds = sorted(custom_boundaries)
        # Assume month / day default to (1, 1) when unspecified for
        # comparison; that puts year-only dates at the start of the
        # year, which matches "this document is from year Y" semantics.
        cmp_tuple = (y, mo or 1, d or 1)
        idx = 0
        for b in sorted_bounds:
            if cmp_tuple < b:
                break
            idx += 1
        labels = []
        for i in range(len(sorted_bounds) + 1):
            if i == 0:
                labels.append(f"before_{_fmt_date(sorted_bounds[0])}")
            elif i == len(sorted_bounds):
                labels.append(f"after_{_fmt_date(sorted_bounds[-1])}")
            else:
                labels.append(
                    f"{_fmt_date(sorted_bounds[i - 1])}_to_"
                    f"{_fmt_date(sorted_bounds[i])}"
                )
        return labels[idx]
    raise ValueError(f"Unknown granularity {granularity!r}")


def _fmt_date(t: tuple[int, int, int]) -> str:
    y, m, d = t
    if d:
        return f"{y:04d}-{m:02d}-{d:02d}"
    if m:
        return f"{y:04d}-{m:02d}"
    return f"{y:04d}"


# --------------- Entry loading ------------------------------


@dataclass
class DatedEntry:
    id: str
    path: Path
    date_str: str
    date_tuple: tuple[int, int, int]
    extra: dict[str, Any]


def _load_manifest_entries(
    manifest_path: Path, use_tag: str,
) -> list[DatedEntry]:
    """Read manifest JSONL, filter by `use` tag, require `date_written`,
    parse dates. Skips entries with missing or unparseable dates with
    a stderr warning."""
    out: list[DatedEntry] = []
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
        date_str = entry.get("date_written")
        if not isinstance(date_str, str):
            sys.stderr.write(
                f"Skipping {entry.get('id', f'line_{lineno}')}: "
                f"missing date_written.\n"
            )
            skipped += 1
            continue
        date_tuple = _parse_iso_date(date_str)
        if date_tuple is None:
            sys.stderr.write(
                f"Skipping {entry.get('id', f'line_{lineno}')}: "
                f"unparseable date_written {date_str!r}.\n"
            )
            skipped += 1
            continue
        raw_path = entry.get("path")
        if not isinstance(raw_path, str):
            continue
        resolved = resolve_path(manifest_path, raw_path)
        out.append(DatedEntry(
            id=str(entry.get("id") or f"line_{lineno}"),
            path=resolved,
            date_str=date_str,
            date_tuple=date_tuple,
            extra={
                k: v for k, v in entry.items()
                if k not in {"id", "path", "use", "date_written"}
            },
        ))
    if skipped:
        sys.stderr.write(
            f"Skipped {skipped} manifest entry/entries with missing "
            f"or unparseable date_written.\n"
        )
    return out


def _load_dir_entries(
    baseline_dir: Path, date_pattern: str,
) -> list[DatedEntry]:
    """Scan baseline_dir for *.txt / *.md files, extract a date from
    each filename via the user-supplied regex. Files with no match
    are skipped (with a stderr warning)."""
    pattern = re.compile(date_pattern)
    out: list[DatedEntry] = []
    skipped = 0
    for path in sorted(baseline_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() not in (".txt", ".md"):
            continue
        m = pattern.search(path.name)
        if not m:
            sys.stderr.write(
                f"Skipping {path.name}: no date match against "
                f"{date_pattern!r}.\n"
            )
            skipped += 1
            continue
        # Use the first captured group, falling back to the full match.
        date_str = m.group(1) if m.lastindex else m.group(0)
        date_tuple = _parse_iso_date(date_str)
        if date_tuple is None:
            sys.stderr.write(
                f"Skipping {path.name}: extracted date {date_str!r} "
                f"is unparseable.\n"
            )
            skipped += 1
            continue
        out.append(DatedEntry(
            id=path.stem,
            path=path,
            date_str=date_str,
            date_tuple=date_tuple,
            extra={},
        ))
    if skipped:
        sys.stderr.write(
            f"Skipped {skipped} file(s) with no date in filename.\n"
        )
    return out


# --------------- Period grouping ----------------------------


def group_by_period(
    entries: Sequence[DatedEntry],
    granularity: str,
    *,
    custom_boundaries: list[tuple[int, int, int]] | None = None,
    min_docs_per_period: int = DEFAULT_MIN_DOCS_PER_PERIOD,
) -> tuple[dict[str, list[DatedEntry]], list[str]]:
    """Group entries into periods. Returns (kept, dropped_period_labels).
    Periods with fewer than min_docs_per_period entries are dropped."""
    grouped: dict[str, list[DatedEntry]] = defaultdict(list)
    for e in entries:
        key = _period_key(e.date_tuple, granularity, custom_boundaries)
        grouped[key].append(e)
    kept: dict[str, list[DatedEntry]] = {}
    dropped: list[str] = []
    for key, entries_in_period in grouped.items():
        if len(entries_in_period) >= min_docs_per_period:
            kept[key] = entries_in_period
        else:
            dropped.append(key)
    return kept, dropped


# --------------- Per-period voiceprint ----------------------


@dataclass
class PeriodProfile:
    label: str
    n_docs: int
    n_words: int
    feature_items: list[dict[str, Any]]  # raw feature items per doc
    period_centroids: dict[str, dict[str, float]]
    # period_centroids[family] = {feature_name: mean_relative_freq}


def build_period_profiles(
    grouped: dict[str, list[DatedEntry]],
) -> tuple[dict[str, PeriodProfile], dict[str, list[str]]]:
    """For each period, run extract_features on every doc and compute
    the period's centroid (per-family mean across docs). Returns
    (profiles_by_period, selected_features_by_family)."""
    items_by_period: dict[str, list[dict[str, Any]]] = {}
    n_docs_by_period: dict[str, int] = {}
    n_words_by_period: dict[str, int] = {}

    for period, entries in grouped.items():
        items: list[dict[str, Any]] = []
        n_words = 0
        for e in entries:
            text = e.path.read_text(encoding="utf-8", errors="ignore")
            feats = extract_features(text)
            items.append({"id": e.id, "features": feats["features"]})
            n_words += int(feats.get("summary", {}).get("n_words", 0))
        items_by_period[period] = items
        n_docs_by_period[period] = len(items)
        n_words_by_period[period] = n_words

    # Select feature names ONCE over the union of all periods so the
    # cross-period distance matrix lives in a shared feature space.
    all_items: list[dict[str, Any]] = []
    for items in items_by_period.values():
        all_items.extend(items)
    selected_features = select_feature_names(all_items, limits=DEFAULT_LIMITS)

    profiles: dict[str, PeriodProfile] = {}
    for period, items in items_by_period.items():
        centroids: dict[str, dict[str, float]] = {}
        for family, names in selected_features.items():
            if not names:
                continue
            # Per-doc vectors, then mean across docs = period centroid.
            per_doc_vectors = [feature_vector(it, family, names) for it in items]
            centroid: dict[str, float] = {}
            for name in names:
                vals = [v.get(name, 0.0) for v in per_doc_vectors]
                centroid[name] = (
                    sum(vals) / len(vals) if vals else 0.0
                )
            centroids[family] = centroid
        profiles[period] = PeriodProfile(
            label=period,
            n_docs=n_docs_by_period[period],
            n_words=n_words_by_period[period],
            feature_items=items,
            period_centroids=centroids,
        )
    return profiles, selected_features


# --------------- Cross-period distance ----------------------


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


def cross_period_distances(
    profiles: dict[str, PeriodProfile],
    selected_features: dict[str, list[str]],
) -> dict[str, dict[tuple[str, str], dict[str, float | None]]]:
    """For each family, compute pairwise Burrows-Delta + cosine
    distance between period centroids. Returns {family: {(p_a, p_b):
    {burrows_delta, cosine_distance}}}.

    Z-score column stats are computed over the per-DOCUMENT feature
    vectors across all periods (NOT over the period centroids
    themselves). This is critical for two-period reports: with stats
    computed over only K=2 centroids, every informative feature gets
    symmetric z-scores ±sqrt(2)/2, so |z_a - z_b| collapses to a
    constant sqrt(2) ≈ 1.414 regardless of the actual magnitude of
    drift. Per-document stats restore the magnitude signal: a small
    centroid shift relative to within-period dispersion gives small
    z-deltas; a large centroid shift gives large z-deltas. Same
    pattern as voice_validation_harness, which computes stats over
    the entire selected slice rather than over per-pair vectors.
    """
    out: dict[str, dict[tuple[str, str], dict[str, float | None]]] = {}
    period_labels = sorted(profiles.keys())
    for family, names in selected_features.items():
        if not names:
            continue
        # Per-doc feature vectors across all periods (the population
        # we z-score against). Period centroids are then z-scored
        # against this distribution.
        per_doc_vectors: list[dict[str, float]] = []
        for p in period_labels:
            for item in profiles[p].feature_items:
                per_doc_vectors.append(feature_vector(item, family, names))
        stats = vector_stats(per_doc_vectors, names)
        informative = _informative_keys(stats, names)
        family_distances: dict[tuple[str, str], dict[str, float | None]] = {}
        for i, p_a in enumerate(period_labels):
            for j, p_b in enumerate(period_labels):
                if i >= j:
                    continue
                vec_a = profiles[p_a].period_centroids.get(family, {})
                vec_b = profiles[p_b].period_centroids.get(family, {})
                delta = _pair_burrows_delta(vec_a, vec_b, informative, stats)
                cos = cosine_distance(vec_a, vec_b, names)
                family_distances[(p_a, p_b)] = {
                    "burrows_delta": delta,
                    "cosine_distance": cos,
                }
        if family_distances:
            out[family] = family_distances
    return out


def weighted_cross_period_distances(
    family_distances: dict[str, dict[tuple[str, str], dict[str, float | None]]],
) -> dict[tuple[str, str], dict[str, float | None]]:
    """Aggregate the per-family pairwise distances into a weighted
    overall using FAMILY_WEIGHTS and the production
    OVERALL_FAMILY_DELTA_CAP. Mirrors voice_validation_harness's
    weighted-family aggregate."""
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


# --------------- Drift scores -------------------------------


def drift_scores(
    profiles: dict[str, PeriodProfile],
    selected_features: dict[str, list[str]],
    *,
    top_drifting: int,
    top_stable: int,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Per-feature drift scoring. For each feature, compute its
    coefficient of variation across periods (SD across period
    centroids divided by mean centroid value). High CV = drifting;
    low CV = stable.

    A more sophisticated approach would use per-period within-period
    SD as the denominator (an F-statistic-like measure), but per-doc
    item lists may be small; v1 uses simpler CV across period
    centroids and lets the user inspect raw values when judgment is
    needed.

    Returns {family: {drifting: [...], stable: [...]}} with each
    feature row carrying the feature name, mean across periods, SD
    across periods, CV, and the per-period values (for the
    drifting top-N only — stable rows omit the per-period dump to
    save report bloat).
    """
    period_labels = sorted(profiles.keys())
    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for family, names in selected_features.items():
        if not names:
            continue
        rows: list[dict[str, Any]] = []
        for name in names:
            vals = [
                profiles[p].period_centroids.get(family, {}).get(name, 0.0)
                for p in period_labels
            ]
            mean = sum(vals) / len(vals) if vals else 0.0
            if len(vals) > 1:
                sd = statistics.stdev(vals)
            else:
                sd = 0.0
            cv = (sd / mean) if mean > 0 else (math.inf if sd > 0 else 0.0)
            rows.append({
                "feature": name,
                "mean_across_periods": mean,
                "sd_across_periods": sd,
                "cv": cv,
                "per_period_values": dict(zip(period_labels, vals)),
            })
        # Drifting: highest CV (with finite values; skip cv=inf which
        # is always small denominators)
        drifting = sorted(
            [r for r in rows if math.isfinite(r["cv"])],
            key=lambda r: r["cv"],
            reverse=True,
        )[:top_drifting]
        # Stable: lowest CV among features with at least some
        # presence (mean > 0)
        stable = sorted(
            [r for r in rows if math.isfinite(r["cv"]) and r["mean_across_periods"] > 0],
            key=lambda r: r["cv"],
        )[:top_stable]
        # Strip per_period_values from stable to keep the report
        # bounded (stable features are uninteresting per-period)
        stable_stripped = [
            {k: v for k, v in r.items() if k != "per_period_values"}
            for r in stable
        ]
        out[family] = {
            "drifting": drifting,
            "stable": stable_stripped,
        }
    return out


# --------------- Privacy guard ------------------------------


def _check_output_privacy(
    paths: list[Path | None], *, allow_public: bool,
) -> None:
    """Marker-based check: a path is treated as private if any
    component in its resolved-absolute form is named
    ``ai-prose-baselines-private``. Mirrors the convention
    ``voice_profile.py`` already uses (``is_private_output_path``).

    The previous implementation rooted the allowlist at
    ``<repo>/ai-prose-baselines-private/``, but the README and the
    documented standard layout use a SIBLING directory next to the
    repo (``../ai-prose-baselines-private/``). Users following the
    documented safe path were hitting the refusal and learning to
    pass ``--allow-public-output`` as a workaround — which trains
    them to bypass the privacy guard. The marker check accepts both
    repo-internal and sibling private roots without compromising the
    intent: any path the user has consciously placed under a
    directory named ``ai-prose-baselines-private`` is treated as
    private; anywhere else requires the explicit override.
    """
    if allow_public:
        return
    for p in paths:
        if p is None:
            continue
        if "ai-prose-baselines-private" not in p.expanduser().resolve().parts:
            sys.stderr.write(
                f"Refusing to write {p}: not under any directory "
                f"named 'ai-prose-baselines-private'. Voice drift "
                f"output is voice-cloning input. Either write into "
                f"a directory named 'ai-prose-baselines-private' "
                f"(repo-internal or sibling — both are accepted), "
                f"or pass --allow-public-output for non-personal "
                f"corpora (e.g., Federalist).\n"
            )
            sys.exit(2)


# --------------- Output rendering ---------------------------


CLAIM_LICENSE = {
    "licenses": (
        "Per-period voiceprint summary, cross-period voice-distance "
        "matrix, and per-feature drift scoring on a date-tagged "
        "baseline corpus. The output disaggregates the writer's "
        "baseline by time so 'drift between draft and baseline' can "
        "be distinguished from 'drift across the writer's own "
        "history'."
    ),
    "does_not_license": (
        "AI provenance, authorship attribution across writers, or a "
        "claim that the writer's voice has 'gotten worse' or 'gotten "
        "better.' The drift report measures change, not improvement; "
        "the writer's local read decides whether observed drift is "
        "natural stylistic evolution or symptomatic distortion."
    ),
}


def render_json(
    *,
    profiles: dict[str, PeriodProfile],
    family_distances: dict[str, dict[tuple[str, str], dict[str, float | None]]],
    weighted_distances: dict[tuple[str, str], dict[str, float | None]],
    drift: dict[str, dict[str, list[dict[str, Any]]]],
    dropped_periods: list[str],
    inputs: dict[str, Any],
    granularity: str,
) -> str:
    period_labels = sorted(profiles.keys())
    period_summary = [
        {
            "label": p,
            "n_docs": profiles[p].n_docs,
            "n_words": profiles[p].n_words,
        }
        for p in period_labels
    ]
    family_distance_serialized: dict[str, list[dict[str, Any]]] = {}
    for family, pairs in family_distances.items():
        family_distance_serialized[family] = [
            {
                "period_a": pa, "period_b": pb,
                "burrows_delta": row.get("burrows_delta"),
                "cosine_distance": row.get("cosine_distance"),
            }
            for (pa, pb), row in sorted(pairs.items())
        ]
    weighted_serialized = [
        {
            "period_a": pa, "period_b": pb,
            "burrows_delta": row.get("burrows_delta"),
            "cosine_distance": row.get("cosine_distance"),
        }
        for (pa, pb), row in sorted(weighted_distances.items())
    ]
    out = {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "inputs": inputs,
        "granularity": granularity,
        "claim_license": CLAIM_LICENSE,
        "n_periods": len(period_labels),
        "periods": period_summary,
        "dropped_periods": dropped_periods,
        "cross_period_distances_per_family": family_distance_serialized,
        "cross_period_distances_weighted": weighted_serialized,
        "drift_scores": drift,
    }
    return json.dumps(out, indent=2, ensure_ascii=False)


def render_markdown(
    *,
    profiles: dict[str, PeriodProfile],
    family_distances: dict[str, dict[tuple[str, str], dict[str, float | None]]],
    weighted_distances: dict[tuple[str, str], dict[str, float | None]],
    drift: dict[str, dict[str, list[dict[str, Any]]]],
    dropped_periods: list[str],
    granularity: str,
) -> str:
    period_labels = sorted(profiles.keys())
    lines: list[str] = [
        "# Voice Drift Report",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Granularity:** `{granularity}`",
        f"**Periods:** {len(period_labels)} ({', '.join(period_labels)})",
        "",
        f"**Reports:** {CLAIM_LICENSE['licenses']}",
        "",
        f"**Does NOT report:** {CLAIM_LICENSE['does_not_license']}",
        "",
    ]

    if dropped_periods:
        lines.append("## Dropped periods (insufficient data)")
        lines.append("")
        for p in sorted(dropped_periods):
            lines.append(f"- `{p}`")
        lines.append("")

    lines.append("## Per-period summary")
    lines.append("")
    lines.append("| Period | n docs | n words |")
    lines.append("|---|---:|---:|")
    for p in period_labels:
        prof = profiles[p]
        lines.append(f"| `{p}` | {prof.n_docs} | {prof.n_words} |")
    lines.append("")

    if weighted_distances and len(period_labels) > 1:
        lines.append("## Cross-period voice distance (weighted aggregate)")
        lines.append("")
        lines.append("Burrows-Delta and cosine distance computed across period centroids in a shared feature space. Higher = more drift between periods.")
        lines.append("")
        lines.append("| Period A | Period B | Burrows-Delta | Cosine |")
        lines.append("|---|---|---:|---:|")
        for (pa, pb), row in sorted(weighted_distances.items()):
            lines.append(
                f"| `{pa}` | `{pb}` | {_fmt(row.get('burrows_delta'))} | "
                f"{_fmt(row.get('cosine_distance'))} |"
            )
        lines.append("")

    if drift:
        lines.append("## Drifting features (top by cross-period CV)")
        lines.append("")
        lines.append("Features whose per-period values fluctuate most across the writer's history. High CV may indicate stylistic evolution, register shifts, or topic-driven artifacts; the writer's local read decides which.")
        lines.append("")
        for family, blocks in drift.items():
            drifting = blocks.get("drifting", [])
            if not drifting:
                continue
            lines.append(f"### `{family}` — drifting")
            lines.append("")
            lines.append("| Feature | Mean | SD | CV | Per-period values |")
            lines.append("|---|---:|---:|---:|---|")
            for row in drifting[:10]:  # cap to 10 per family in markdown
                pp = row.get("per_period_values", {})
                pp_str = ", ".join(
                    f"{p}: {_fmt(v)}" for p, v in pp.items()
                )
                lines.append(
                    f"| `{row['feature']}` | "
                    f"{_fmt(row['mean_across_periods'])} | "
                    f"{_fmt(row['sd_across_periods'])} | "
                    f"{_fmt(row['cv'])} | {pp_str} |"
                )
            lines.append("")

        lines.append("## Stable features (top by lowest CV)")
        lines.append("")
        lines.append("Features the writer uses consistently across periods — durable idiolect.")
        lines.append("")
        for family, blocks in drift.items():
            stable = blocks.get("stable", [])
            if not stable:
                continue
            lines.append(f"### `{family}` — stable")
            lines.append("")
            lines.append("| Feature | Mean | SD | CV |")
            lines.append("|---|---:|---:|---:|")
            for row in stable[:10]:
                lines.append(
                    f"| `{row['feature']}` | "
                    f"{_fmt(row['mean_across_periods'])} | "
                    f"{_fmt(row['sd_across_periods'])} | "
                    f"{_fmt(row['cv'])} |"
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


def _load_periods_json(path: Path) -> list[DatedEntry]:
    """Load explicit {period_label: [doc_paths]} mapping. The
    'date_tuple' for entries loaded this way is synthetic: each
    period label sorts in input order, so date-based grouping is
    bypassed."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(
            "--periods-json must contain a {period_label: [paths]} mapping."
        )
    out: list[DatedEntry] = []
    for label, doc_paths in data.items():
        if not isinstance(doc_paths, list):
            continue
        for raw in doc_paths:
            if not isinstance(raw, str):
                continue
            p = (path.parent / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
            if not p.is_file():
                sys.stderr.write(f"Skipping (not found): {p}\n")
                continue
            # Synthetic date tuple — _period_key won't be called for
            # periods-json mode because the script bypasses grouping.
            out.append(DatedEntry(
                id=p.stem,
                path=p,
                date_str=label,
                date_tuple=(0, 0, 0),
                extra={"_explicit_period": label},
            ))
    return out


def _parse_period_boundaries(s: str) -> list[tuple[int, int, int]]:
    """Parse comma-separated ISO dates. Returns sorted list of tuples."""
    bounds: list[tuple[int, int, int]] = []
    for chunk in s.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        t = _parse_iso_date(chunk)
        if t is None:
            raise SystemExit(f"Bad --period-boundaries entry: {chunk!r}")
        bounds.append(t)
    return sorted(bounds)


def run(args: argparse.Namespace) -> dict[str, Any]:
    """In-process entry point. Returns a structured result dict."""
    explicit_periods = bool(args.periods_json)
    if explicit_periods:
        entries = _load_periods_json(Path(args.periods_json))
        # Build grouping directly from extra._explicit_period
        grouped: dict[str, list[DatedEntry]] = defaultdict(list)
        for e in entries:
            grouped[e.extra["_explicit_period"]].append(e)
        # Apply min-docs filter
        kept: dict[str, list[DatedEntry]] = {}
        dropped: list[str] = []
        for k, v in grouped.items():
            if len(v) >= args.min_docs_per_period:
                kept[k] = v
            else:
                dropped.append(k)
        grouped = kept
    elif args.manifest:
        manifest_path = Path(args.manifest)
        validation = validate_manifest(str(manifest_path))
        if validation["n_errors"] > 0:
            raise SystemExit(
                f"Manifest validation failed with "
                f"{validation['n_errors']} error(s)."
            )
        entries = _load_manifest_entries(manifest_path, args.use)
        custom_bounds = (
            _parse_period_boundaries(args.period_boundaries)
            if args.period_boundaries else None
        )
        grouped, dropped = group_by_period(
            entries, args.period_granularity,
            custom_boundaries=custom_bounds,
            min_docs_per_period=args.min_docs_per_period,
        )
    elif args.baseline_dir:
        entries = _load_dir_entries(
            Path(args.baseline_dir), args.date_pattern,
        )
        custom_bounds = (
            _parse_period_boundaries(args.period_boundaries)
            if args.period_boundaries else None
        )
        grouped, dropped = group_by_period(
            entries, args.period_granularity,
            custom_boundaries=custom_bounds,
            min_docs_per_period=args.min_docs_per_period,
        )
    else:
        raise SystemExit(
            "Need one of --manifest, --baseline-dir, or --periods-json."
        )

    if len(grouped) < 2:
        raise SystemExit(
            f"Voice drift requires at least 2 periods with "
            f"{args.min_docs_per_period}+ documents each. After "
            f"filtering, only {len(grouped)} period(s) remain. "
            f"Dropped: {dropped}. Either lower "
            f"--min-docs-per-period, choose a finer granularity, or "
            f"add more dated baseline documents."
        )

    profiles, selected_features = build_period_profiles(grouped)
    family_distances = cross_period_distances(profiles, selected_features)
    weighted = weighted_cross_period_distances(family_distances)
    drift = drift_scores(
        profiles, selected_features,
        top_drifting=args.top_drifting,
        top_stable=args.top_stable,
    )

    inputs = {
        "manifest": args.manifest,
        "baseline_dir": args.baseline_dir,
        "periods_json": args.periods_json,
        "use": args.use,
        "period_granularity": args.period_granularity,
        "period_boundaries": args.period_boundaries,
        "min_docs_per_period": args.min_docs_per_period,
    }
    return {
        "profiles": profiles,
        "family_distances": family_distances,
        "weighted_distances": weighted,
        "drift": drift,
        "dropped_periods": dropped,
        "inputs": inputs,
        "granularity": args.period_granularity,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Track voice drift across time periods on a date-tagged "
            "baseline corpus."
        )
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--manifest", help="JSONL manifest with date_written entries.")
    src.add_argument("--baseline-dir", help="Directory of dated files.")
    src.add_argument("--periods-json", help="Explicit {period_label: [paths]} mapping.")
    parser.add_argument(
        "--date-pattern", default=r"(\d{4}-\d{2}|\d{4})",
        help="Regex with one capture group, applied to filenames in --baseline-dir.",
    )
    parser.add_argument(
        "--use", default="voice_profile",
        help="Manifest `use` tag to filter entries (default voice_profile).",
    )
    parser.add_argument(
        "--period-granularity", default=DEFAULT_GRANULARITY,
        choices=GRANULARITIES,
    )
    parser.add_argument(
        "--period-boundaries", default=None,
        help="Comma-separated ISO dates for --period-granularity custom.",
    )
    parser.add_argument(
        "--min-docs-per-period", type=int,
        default=DEFAULT_MIN_DOCS_PER_PERIOD,
    )
    parser.add_argument("--top-drifting", type=int, default=DEFAULT_TOP_DRIFTING)
    parser.add_argument("--top-stable", type=int, default=DEFAULT_TOP_STABLE)
    parser.add_argument("--out", help="Markdown output path.")
    parser.add_argument("--json-out", help="JSON output path.")
    parser.add_argument(
        "--allow-public-output", action="store_true",
        help=(
            "Allow output outside ai-prose-baselines-private/. Voice "
            "drift output is voice-cloning input; default-private."
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
        drift=result["drift"],
        dropped_periods=result["dropped_periods"],
        inputs=result["inputs"],
        granularity=result["granularity"],
    )
    md = render_markdown(
        profiles=result["profiles"],
        family_distances=result["family_distances"],
        weighted_distances=result["weighted_distances"],
        drift=result["drift"],
        dropped_periods=result["dropped_periods"],
        granularity=result["granularity"],
    )

    if json_path:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json_str, encoding="utf-8")
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
    if not json_path and not out_path:
        # Stdout is also voice-cloning-sensitive output. The privacy
        # guard's path-based check would have blocked any file outside
        # ai-prose-baselines-private/; stdout was previously a hole
        # because it has no path. Refuse stdout output unless
        # --allow-public-output is passed (mirrors the file-path
        # check), so the default-private posture holds end-to-end.
        if not args.allow_public_output:
            sys.stderr.write(
                "Refusing to write voice-drift report to stdout "
                "without --allow-public-output. POV / voice-drift "
                "output is voice-cloning input; default-private "
                "posture requires either --out / --json-out into "
                "ai-prose-baselines-private/, or --allow-public-"
                "output for non-personal corpora (e.g., Federalist).\n"
            )
            return 2
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
