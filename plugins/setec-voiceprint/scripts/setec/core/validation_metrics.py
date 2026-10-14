"""Validation metrics core: label mapping, manifest loading and ranking metrics.

Moved verbatim from ``setec.surfaces.validation_harness`` (CB-5) so calibration
and surface modules can use these helpers without importing that surface.
``validation_harness`` re-exports every name here as the same object. Stdlib plus
``setec.core.manifest_core`` only.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any, Callable, Sequence

from setec.core.manifest_core import resolve_path


DEFAULT_POSITIVE_STATUSES = (
    "ai_generated",
    "ai_assisted",
    "ai_edited",
)


DEFAULT_NEGATIVE_STATUSES = ("pre_ai_human",)


def _entry_uses(entry: dict[str, Any], use_tag: str) -> bool:
    use = entry.get("use")
    return isinstance(use, list) and use_tag in use


def load_manifest_entries(manifest_path: str | Path) -> list[dict[str, Any]]:
    """Load JSONL manifest entries and attach line/path metadata.

    ``validate_manifest`` has already checked schema and path integrity;
    this loader keeps parsing minimal and mirrors its path resolution.
    """
    path = Path(manifest_path)
    entries: list[dict[str, Any]] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entry = json.loads(line)
        if not isinstance(entry, dict):
            continue
        raw_path = entry.get("path")
        if isinstance(raw_path, str):
            entry["_resolved_path"] = str(resolve_path(path, raw_path))
        entry["_lineno"] = lineno
        entries.append(entry)
    return entries


def label_for_status(
    ai_status: Any,
    positive_statuses: set[str],
    negative_statuses: set[str],
) -> int | None:
    if not isinstance(ai_status, str):
        return None
    if ai_status in positive_statuses:
        return 1
    if ai_status in negative_statuses:
        return 0
    return None


def _rankdata_average(values: Sequence[float]) -> list[float]:
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


def fallback_roc_auc(labels: Sequence[int], scores: Sequence[float]) -> float | None:
    n_pos = sum(1 for y in labels if y == 1)
    n_neg = sum(1 for y in labels if y == 0)
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = _rankdata_average(scores)
    sum_pos_ranks = sum(rank for rank, y in zip(ranks, labels) if y == 1)
    return (sum_pos_ranks - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def fallback_average_precision(labels: Sequence[int], scores: Sequence[float]) -> float | None:
    n_pos = sum(1 for y in labels if y == 1)
    if n_pos == 0:
        return None
    pairs = sorted(zip(scores, labels), key=lambda x: x[0], reverse=True)
    hits = 0
    precision_sum = 0.0
    for rank, (_score, label) in enumerate(pairs, start=1):
        if label == 1:
            hits += 1
            precision_sum += hits / rank
    return precision_sum / n_pos


def _quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    idx = q * (len(ordered) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    frac = idx - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def paired_bootstrap_ci(
    labels: Sequence[int],
    scores: Sequence[float],
    metric_fn: Callable[[Sequence[int], Sequence[float]], float | None],
    *,
    n_resamples: int,
    confidence_level: float,
    seed: int | None,
) -> dict[str, Any]:
    """Percentile bootstrap CI over paired ``(label, score)`` rows.

    Resamples that contain only one class are skipped because ROC AUC
    and AP-as-validation-ranking both need positives and negatives to
    answer the binary discrimination question.
    """
    if n_resamples <= 0:
        return {
            "available": False,
            "reason": "metric bootstrap disabled",
            "method": "none",
            "n_resamples": 0,
            "n_valid_resamples": 0,
        }
    pairs = list(zip(labels, scores))
    if not pairs:
        return {
            "available": False,
            "reason": "no scored records",
            "method": "paired_percentile_bootstrap",
            "n_resamples": n_resamples,
            "n_valid_resamples": 0,
        }
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(n_resamples):
        sample = [pairs[rng.randrange(len(pairs))] for _ in range(len(pairs))]
        sample_labels = [y for y, _s in sample]
        if 0 not in sample_labels or 1 not in sample_labels:
            continue
        sample_scores = [s for _y, s in sample]
        value = metric_fn(sample_labels, sample_scores)
        if value is None or not math.isfinite(float(value)):
            continue
        estimates.append(float(value))
    if not estimates:
        return {
            "available": False,
            "reason": "no bootstrap resamples contained both classes",
            "method": "paired_percentile_bootstrap",
            "n_resamples": n_resamples,
            "n_valid_resamples": 0,
        }
    alpha = 1 - confidence_level
    return {
        "available": True,
        "ci_low": _quantile(estimates, alpha / 2),
        "ci_high": _quantile(estimates, 1 - alpha / 2),
        "confidence_level": confidence_level,
        "method": "paired_percentile_bootstrap",
        "n_resamples": n_resamples,
        "n_valid_resamples": len(estimates),
    }


def collect_signal_records(
    records: Sequence[dict[str, Any]],
    signal_path: str,
) -> list[tuple[int, float]]:
    """Return a `(label_int, score_float)` paired sample for the named
    dotted signal path, drawn from `records`.

    Used by both `per_signal_ranking_metrics` (which aggregates into
    AUC/AP) and `scripts/calibration/calibrate_thresholds.py` (which
    sweeps thresholds direction-aware on the raw arrays). Centralizing
    the per-signal extraction here guarantees both consumers operate
    on identical paired samples — important when the calibrator's
    derived threshold is later checked against the harness's reported
    AUC.

    Records lacking a binary label or a finite numeric value for the
    signal are silently dropped, matching the existing harness
    behavior.
    """
    pairs: list[tuple[int, float]] = []
    for r in records:
        label = r.get("label")
        if label not in (0, 1):
            continue
        sig_scores = r.get("per_signal_scores") or {}
        v = sig_scores.get(signal_path)
        if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            continue
        pairs.append((int(label), float(v)))
    return pairs
