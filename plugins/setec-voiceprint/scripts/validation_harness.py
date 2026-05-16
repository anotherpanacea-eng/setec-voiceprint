#!/usr/bin/env python3
"""
validation_harness.py
Empirical validation harness for SETEC task-surface outputs.

MVP scope: evaluate the smoothing-diagnosis surface by running
``variance_audit.audit_text`` + ``classify_compression`` on manifest
entries tagged ``use: validation``. The harness reports score
distributions, ROC / PR ranking metrics when both classes are present,
and thresholded FPR/TPR/FNR/precision only when the caller supplies an
explicit FPR target.

This is a validation tool, not a detector. It says how these signals
performed on this labeled manifest, in these registers, at these
lengths. It does not license a world-facing "AI" verdict.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Sequence

from check_corpus import check_corpus_paths
from claim_license import ClaimLicense, from_legacy
from manifest_validator import resolve_path, validate_manifest
from preprocessing import available_rule_names, strip_non_prose
from variance_audit import (
    COMPRESSION_HEURISTICS,
    _BASELINE_PATH_TO_HEURISTIC,
    _SIGNAL_PATHS,
    _extract_signal,
    audit_text,
    classify_compression,
    split_words,
)


def _expected_polarity_direction(signal_name: str) -> str | None:
    """Return 'gt' or 'lt' for signals where COMPRESSION_HEURISTICS
    encodes a direction ('compressed when value > threshold' or
    'compressed when value < threshold'); None for signals with no
    heuristic entry (e.g. function_word_ratio at the audit level
    without a baseline). The harness uses this to label whether an
    empirical AUC < 0.5 is the expected polarity for the signal or an
    unexpected inversion that may indicate a calibration issue."""
    heuristic_key = _BASELINE_PATH_TO_HEURISTIC.get(signal_name)
    if heuristic_key is None or heuristic_key not in COMPRESSION_HEURISTICS:
        return None
    direction = COMPRESSION_HEURISTICS[heuristic_key].direction
    return direction if direction in ("gt", "lt") else None


TASK_SURFACE = "validation"
EVALUATED_SURFACE = "smoothing_diagnosis"

DEFAULT_POSITIVE_STATUSES = (
    "ai_generated",
    "ai_assisted",
    "ai_edited",
)
DEFAULT_NEGATIVE_STATUSES = ("pre_ai_human",)
DEFAULT_METRIC_BOOTSTRAP_RESAMPLES = 2000
DEFAULT_RECORDS_LIMIT = 100

LENGTH_BUCKETS = (
    (0, 199, "lt_200"),
    (200, 499, "200_499"),
    (500, 999, "500_999"),
    (1000, 1999, "1000_1999"),
    (2000, 4999, "2000_4999"),
    (5000, math.inf, "5000_plus"),
)

try:
    from sklearn import metrics as sklearn_metrics  # type: ignore

    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

try:
    from statsmodels.stats.proportion import proportion_confint  # type: ignore

    HAS_STATSMODELS = True
except ImportError:
    HAS_STATSMODELS = False


# ---------- Manifest loading ----------


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


def length_bucket(n_words: int) -> str:
    for lo, hi, label in LENGTH_BUCKETS:
        if lo <= n_words <= hi:
            return label
    return "unknown"


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


# ---------- Scoring ----------


def _finite_score(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def score_smoothing_entry(
    entry: dict[str, Any],
    *,
    mattr_window: int = 50,
    do_tier2: bool = True,
    do_tier3: bool = True,
    allow_non_prose: bool = False,
    strip_rules: str | list[str] | None = None,
    strip_aggressive: bool = False,
    positive_statuses: set[str],
    negative_statuses: set[str],
) -> dict[str, Any]:
    entry_id = entry.get("id") if isinstance(entry.get("id"), str) else f"line_{entry.get('_lineno', '?')}"
    resolved_path = Path(str(entry.get("_resolved_path") or entry.get("path") or ""))

    base_record: dict[str, Any] = {
        "id": entry_id,
        "path": str(resolved_path),
        "lineno": entry.get("_lineno"),
        "register": entry.get("register", "unknown"),
        "genre": entry.get("genre", "unknown"),
        "ai_status": entry.get("ai_status", "unknown"),
        "adversarial_class": entry.get("adversarial_class", "none"),
        "source_id": entry.get("source_id"),
        "transform": entry.get("transform"),
        "language_status": entry.get("language_status", "unknown"),
        "persona": entry.get("persona", "unknown"),
        "declared_word_count": entry.get("word_count"),
        "label": label_for_status(entry.get("ai_status"), positive_statuses, negative_statuses),
        "score": None,
        "score_name": "compression_fraction",
        "usable_for_metrics": False,
    }

    try:
        text = resolved_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        base_record.update({
            "error": f"Could not read target: {exc}",
            "length_bucket": "unknown",
            "observed_word_count": None,
        })
        return base_record

    raw_n_words = len(split_words(text))
    audit = audit_text(
        text,
        mattr_window=mattr_window,
        do_tier2=do_tier2,
        do_tier3=do_tier3,
        allow_non_prose=allow_non_prose,
        strip_rules=strip_rules,
        strip_aggressive=strip_aggressive,
    )
    n_words = int(audit.get("summary", {}).get("n_words", raw_n_words) or 0)
    compression = classify_compression(audit)
    score = _finite_score(compression.get("compression_fraction"))

    # Extract per-signal scalars for the per-signal AUC table. Signals
    # that are unavailable on this entry (Tier 2 missing, length floor
    # not met for that signal) come through as None and are skipped at
    # the metric layer.
    per_signal_scores: dict[str, float | None] = {}
    for name, key_path in _SIGNAL_PATHS:
        per_signal_scores[name] = _extract_signal(audit, key_path)

    base_record.update({
        "raw_word_count": raw_n_words,
        "observed_word_count": n_words,
        "length_bucket": length_bucket(n_words),
        "preprocessing": audit.get("preprocessing", {}),
        "score": score,
        "band": compression.get("band"),
        "weighted_score": compression.get("weighted_score"),
        "available_weight": compression.get("available_weight"),
        "flagged_signals": compression.get("flagged_signals", []),
        "skipped_signals": compression.get("skipped_signals", []),
        "per_signal_scores": per_signal_scores,
        "usable_for_metrics": score is not None and base_record["label"] in (0, 1),
    })
    if score is None:
        base_record["metric_exclusion_reason"] = "compression_fraction unavailable"
    elif base_record["label"] not in (0, 1):
        base_record["metric_exclusion_reason"] = "ai_status is not mapped to a binary validation label"
    return base_record


# ---------- Metrics ----------


def scored_records(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in records if r.get("usable_for_metrics") and _finite_score(r.get("score")) is not None]


def _mean(values: Sequence[float]) -> float | None:
    return statistics.mean(values) if values else None


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def summarize_scores(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    scores = [float(r["score"]) for r in records if _finite_score(r.get("score")) is not None]
    out: dict[str, Any] = {
        "n_scored": len(scores),
        "mean": _mean(scores),
        "median": _median(scores),
        "min": min(scores) if scores else None,
        "max": max(scores) if scores else None,
    }
    by_label: dict[str, dict[str, Any]] = {}
    for label, name in ((0, "negative"), (1, "positive")):
        label_scores = [
            float(r["score"]) for r in records
            if r.get("label") == label and _finite_score(r.get("score")) is not None
        ]
        by_label[name] = {
            "n": len(label_scores),
            "mean": _mean(label_scores),
            "median": _median(label_scores),
            "min": min(label_scores) if label_scores else None,
            "max": max(label_scores) if label_scores else None,
        }
    out["by_label"] = by_label
    return out


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


def ranking_metrics(
    records: Sequence[dict[str, Any]],
    *,
    bootstrap_resamples: int,
    confidence_level: float,
    seed: int | None,
) -> dict[str, Any]:
    usable = scored_records(records)
    labels = [int(r["label"]) for r in usable]
    scores = [float(r["score"]) for r in usable]
    n_pos = sum(1 for y in labels if y == 1)
    n_neg = sum(1 for y in labels if y == 0)
    out: dict[str, Any] = {
        "n_scored": len(usable),
        "n_positive": n_pos,
        "n_negative": n_neg,
        "roc_auc": None,
        "average_precision": None,
        "method": "not_computable",
    }
    if n_pos == 0 or n_neg == 0:
        out["reason"] = "ranking metrics require at least one positive and one negative scored record"
        return out
    if HAS_SKLEARN:
        out["roc_auc"] = float(sklearn_metrics.roc_auc_score(labels, scores))
        out["average_precision"] = float(sklearn_metrics.average_precision_score(labels, scores))
        out["method"] = "sklearn"

        def average_precision_fn(ys: Sequence[int], xs: Sequence[float]) -> float | None:
            return float(sklearn_metrics.average_precision_score(ys, xs))
    else:
        out["roc_auc"] = fallback_roc_auc(labels, scores)
        out["average_precision"] = fallback_average_precision(labels, scores)
        out["method"] = "stdlib_fallback"
        average_precision_fn = fallback_average_precision
    out["roc_auc_ci"] = paired_bootstrap_ci(
        labels,
        scores,
        fallback_roc_auc,
        n_resamples=bootstrap_resamples,
        confidence_level=confidence_level,
        seed=seed,
    )
    out["average_precision_ci"] = paired_bootstrap_ci(
        labels,
        scores,
        average_precision_fn,
        n_resamples=bootstrap_resamples,
        confidence_level=confidence_level,
        seed=None if seed is None else seed + 7919,
    )
    if out["roc_auc"] is not None and out["roc_auc"] < 0.5:
        out["warning"] = (
            "ROC AUC < 0.5; score polarity may be inverted relative to "
            "the positive label mapping."
        )
    return out


def choose_threshold_at_fpr(
    records: Sequence[dict[str, Any]],
    fpr_target: float,
) -> dict[str, Any]:
    usable = scored_records(records)
    negatives = [float(r["score"]) for r in usable if r.get("label") == 0]
    positives = [float(r["score"]) for r in usable if r.get("label") == 1]
    if not negatives:
        return {
            "available": False,
            "reason": "no scored negative/control records available to set an FPR threshold",
            "fpr_target": fpr_target,
        }
    candidates = {float(r["score"]) for r in usable}
    candidates.add(-math.inf)
    chosen: dict[str, Any] | None = None
    for threshold in sorted(candidates):
        fp = sum(1 for s in negatives if s > threshold)
        tp = sum(1 for s in positives if s > threshold)
        fpr = fp / len(negatives)
        tpr = tp / len(positives) if positives else 0.0
        if fpr <= fpr_target:
            if (
                chosen is None
                or tpr > chosen["tpr"]
                or (tpr == chosen["tpr"] and fpr < chosen["fpr"])
                or (
                    tpr == chosen["tpr"]
                    and fpr == chosen["fpr"]
                    and threshold < chosen["threshold"]
                )
            ):
                chosen = {"threshold": threshold, "fp": fp, "tp": tp, "fpr": fpr, "tpr": tpr}
    if chosen is None:
        threshold = max(negatives)
        fp = sum(1 for s in negatives if s > threshold)
        tp = sum(1 for s in positives if s > threshold)
        chosen = {
            "threshold": threshold,
            "fp": fp,
            "tp": tp,
            "fpr": fp / len(negatives),
            "tpr": (tp / len(positives) if positives else 0.0),
        }
    return {
        "available": True,
        "threshold": chosen["threshold"],
        "fpr_target": fpr_target,
        "empirical_control_fpr": chosen["fpr"],
        "empirical_tpr_at_threshold": chosen["tpr"],
        "n_controls": len(negatives),
        "n_positives": len(positives),
        "threshold_rule": (
            "threshold that maximizes empirical TPR subject to control FPR "
            "<= fpr_target; scores strictly greater than threshold are "
            "predicted positive"
        ),
        "control_false_positives_at_threshold": chosen["fp"],
        "positive_true_positives_at_threshold": chosen["tp"],
    }


def wilson_interval(successes: int, n: int, confidence_level: float) -> tuple[float, float] | None:
    if n <= 0:
        return None
    z_by_conf = {
        0.80: 1.2815515655446004,
        0.90: 1.6448536269514722,
        0.95: 1.959963984540054,
        0.98: 2.3263478740408408,
        0.99: 2.5758293035489004,
    }
    z = z_by_conf.get(round(confidence_level, 2), 1.959963984540054)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def proportion_interval(
    successes: int,
    n: int,
    *,
    confidence_level: float,
    method: str,
) -> dict[str, Any]:
    if n <= 0:
        return {
            "value": None,
            "successes": successes,
            "n": n,
            "ci_low": None,
            "ci_high": None,
            "ci_method": "none",
        }
    value = successes / n
    if HAS_STATSMODELS:
        try:
            low, high = proportion_confint(
                successes,
                n,
                alpha=1 - confidence_level,
                method=method,
            )
            ci_method = f"statsmodels:{method}"
        except Exception:
            interval = wilson_interval(successes, n, confidence_level)
            low, high = interval if interval is not None else (None, None)
            ci_method = f"stdlib:wilson_fallback_from_{method}"
    else:
        interval = wilson_interval(successes, n, confidence_level)
        low, high = interval if interval is not None else (None, None)
        ci_method = "stdlib:wilson"
    return {
        "value": value,
        "successes": successes,
        "n": n,
        "ci_low": low,
        "ci_high": high,
        "confidence_level": confidence_level,
        "ci_method": ci_method,
    }


def threshold_metrics(
    records: Sequence[dict[str, Any]],
    threshold: float | None,
    *,
    confidence_level: float,
    ci_method: str,
) -> dict[str, Any] | None:
    if threshold is None:
        return None
    usable = scored_records(records)
    tp = fp = tn = fn = 0
    for r in usable:
        pred = 1 if float(r["score"]) > threshold else 0
        label = int(r["label"])
        if pred == 1 and label == 1:
            tp += 1
        elif pred == 1 and label == 0:
            fp += 1
        elif pred == 0 and label == 0:
            tn += 1
        elif pred == 0 and label == 1:
            fn += 1
    pos_n = tp + fn
    neg_n = fp + tn
    pred_pos_n = tp + fp
    return {
        "threshold": threshold,
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "rates": {
            "tpr_recall": proportion_interval(tp, pos_n, confidence_level=confidence_level, method=ci_method),
            "fnr": proportion_interval(fn, pos_n, confidence_level=confidence_level, method=ci_method),
            "fpr": proportion_interval(fp, neg_n, confidence_level=confidence_level, method=ci_method),
            "tnr_specificity": proportion_interval(tn, neg_n, confidence_level=confidence_level, method=ci_method),
            "precision": proportion_interval(tp, pred_pos_n, confidence_level=confidence_level, method=ci_method),
        },
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


def per_signal_ranking_metrics(
    records: Sequence[dict[str, Any]],
    *,
    bootstrap_resamples: int,
    confidence_level: float,
    seed: int | None,
) -> dict[str, Any]:
    """Run ranking metrics per Layer A signal.

    For each signal in ``_SIGNAL_PATHS`` (the 13 dotted paths exposed by
    ``variance_audit``), build a ``(label, signal_value)`` paired
    sample via `collect_signal_records` and compute ROC AUC + average
    precision + paired bootstrap CIs. Signals where one class lacks at
    least two usable records are reported with ``method='not_computable'``
    and a reason string.

    Polarity note: AUC < 0.5 indicates the signal moves inversely to
    the positive label class on this corpus. For some signals this is
    expected (function-word ratio rises with AI compression; MATTR
    falls with AI compression). The harness reports raw AUC; readers
    interpret the direction. The ``polarity_hint`` field flags
    inverted-direction signals so consumers can decide whether to
    flip them.

    Returns a dict keyed by dotted signal path. Signals are reported
    in ``_SIGNAL_PATHS`` order, which matches the variance audit's
    JSON output for cross-referencing.
    """
    out: dict[str, Any] = {}
    for name, _key_path in _SIGNAL_PATHS:
        # Build paired arrays for this signal via the shared helper so
        # `collect_signal_records` and this loop are guaranteed to
        # produce identical paired samples.
        pairs = collect_signal_records(records, name)
        labels = [p[0] for p in pairs]
        scores = [p[1] for p in pairs]
        n_pos = sum(1 for y in labels if y == 1)
        n_neg = sum(1 for y in labels if y == 0)

        signal_block: dict[str, Any] = {
            "n_scored": len(pairs),
            "n_positive": n_pos,
            "n_negative": n_neg,
            "roc_auc": None,
            "average_precision": None,
            "method": "not_computable",
        }

        if n_pos == 0 or n_neg == 0:
            signal_block["reason"] = (
                "per-signal ranking requires at least one record per class "
                "with this signal computable; skipped due to insufficient "
                "labeled coverage"
            )
            out[name] = signal_block
            continue

        if HAS_SKLEARN:
            signal_block["roc_auc"] = float(
                sklearn_metrics.roc_auc_score(labels, scores)
            )
            signal_block["average_precision"] = float(
                sklearn_metrics.average_precision_score(labels, scores)
            )
            signal_block["method"] = "sklearn"

            def average_precision_fn(
                ys: Sequence[int], xs: Sequence[float],
            ) -> float | None:
                return float(sklearn_metrics.average_precision_score(ys, xs))
        else:
            signal_block["roc_auc"] = fallback_roc_auc(labels, scores)
            signal_block["average_precision"] = fallback_average_precision(
                labels, scores,
            )
            signal_block["method"] = "stdlib_fallback"
            average_precision_fn = fallback_average_precision

        # Per-signal bootstrap seed derives from the global seed plus
        # the signal name so reruns are reproducible per signal and
        # different signals don't share the same resampling realization.
        per_signal_seed = derive_seed(seed, "per_signal", name)
        signal_block["roc_auc_ci"] = paired_bootstrap_ci(
            labels, scores, fallback_roc_auc,
            n_resamples=bootstrap_resamples,
            confidence_level=confidence_level,
            seed=per_signal_seed,
        )
        signal_block["average_precision_ci"] = paired_bootstrap_ci(
            labels, scores, average_precision_fn,
            n_resamples=bootstrap_resamples,
            confidence_level=confidence_level,
            seed=None if per_signal_seed is None else per_signal_seed + 7919,
        )

        # Polarity check: COMPRESSION_HEURISTICS encodes the expected
        # direction per signal (compressed when value 'gt' threshold or
        # 'lt' threshold). 'gt' signals (yules_k, connective_density,
        # function_word_ratio, adjacent_cosine_mean) should yield AUC
        # > 0.5 if the AI=positive label aligns with compression; 'lt'
        # signals (burstiness_B, mattr, mtld, shannon_entropy, fkgl_sd,
        # mdd_sd, sentence_length_sd, adjacent_cosine_sd) should yield
        # AUC < 0.5. The harness reports raw AUC and labels whether
        # the empirical direction matches the expected one.
        auc = signal_block["roc_auc"]
        expected_direction = _expected_polarity_direction(name)
        if isinstance(auc, (int, float)):
            signal_block["expected_direction"] = expected_direction
            if expected_direction == "gt":
                expected_polarity = "AUC > 0.5"
                polarity_match = auc > 0.5
            elif expected_direction == "lt":
                expected_polarity = "AUC < 0.5"
                polarity_match = auc < 0.5
            else:
                expected_polarity = None
                polarity_match = None
            signal_block["polarity_match"] = polarity_match
            if expected_polarity is None:
                if auc < 0.5:
                    signal_block["polarity_hint"] = (
                        "raw AUC < 0.5 (no expected direction encoded "
                        "for this signal; reader interprets)"
                    )
                elif auc == 0.5:
                    signal_block["polarity_hint"] = (
                        "raw AUC == 0.5 -- signal is non-informative on "
                        "this corpus"
                    )
            elif polarity_match is True:
                signal_block["polarity_hint"] = (
                    f"matches expected direction ({expected_polarity})"
                )
            elif polarity_match is False:
                signal_block["polarity_hint"] = (
                    f"does NOT match expected direction "
                    f"(heuristic predicts {expected_polarity}; "
                    f"empirical AUC={auc:.3f}). May indicate "
                    "calibration drift or label-polarity inversion on "
                    "this corpus."
                )

        out[name] = signal_block

    return out


def metric_block(
    records: Sequence[dict[str, Any]],
    *,
    threshold: float | None,
    confidence_level: float,
    ci_method: str,
    metric_bootstrap_resamples: int,
    seed: int | None,
    include_per_signal: bool = False,
) -> dict[str, Any]:
    counts = Counter(str(r.get("ai_status", "unknown")) for r in records)
    labels = Counter(
        "positive" if r.get("label") == 1 else "negative" if r.get("label") == 0 else "unlabeled"
        for r in records
    )
    block = {
        "n_records": len(records),
        "n_unscored": sum(1 for r in records if not r.get("usable_for_metrics")),
        "counts_by_ai_status": dict(counts),
        "counts_by_label": dict(labels),
        "score_summary": summarize_scores(records),
        "ranking": ranking_metrics(
            records,
            bootstrap_resamples=metric_bootstrap_resamples,
            confidence_level=confidence_level,
            seed=seed,
        ),
    }
    tm = threshold_metrics(
        records,
        threshold,
        confidence_level=confidence_level,
        ci_method=ci_method,
    )
    if tm is not None:
        block["threshold_metrics"] = tm
    # Per-signal ranking metrics surface which Layer A signals are
    # actually carrying the discrimination on this corpus, beyond the
    # aggregate compression_fraction. Only computed for the overall
    # slice; per-slice per-signal would explode report size and slice
    # samples are typically too small for stable per-signal CIs.
    if include_per_signal:
        block["per_signal_ranking"] = per_signal_ranking_metrics(
            records,
            bootstrap_resamples=metric_bootstrap_resamples,
            confidence_level=confidence_level,
            seed=seed,
        )
    return block


def group_records(records: Sequence[dict[str, Any]], field: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        key = str(r.get(field) or "unknown")
        grouped[key].append(r)
    return dict(sorted(grouped.items(), key=lambda kv: kv[0]))


def derive_seed(seed: int | None, *parts: str) -> int | None:
    """Per-(slice, signal) seed derivation that is stable across
    Python processes.

    Cross-process stability requires that the derivation NOT use
    Python's built-in ``hash()`` of strings or tuples — those are
    salted per process via ``PYTHONHASHSEED`` (random by default), so
    a stable ``--bootstrap-seed`` combined with ``hash((slice, signal))``
    would produce different RNG sequences on every run, breaking the
    reproducibility guarantee that's part of the validation-harness
    contract.

    This implementation uses ``(i + 1) * ord(ch)`` accumulation
    instead. ``ord()`` returns a Unicode code point, which is stable
    across processes regardless of ``PYTHONHASHSEED``. The voice-
    coherence harness uses a SHA-256-based variant (`_stable_seed`)
    for the same invariant; the two algorithms produce different
    numeric seeds but both satisfy the cross-process-stable contract.
    See `test_validation_harness_seeds.py` for the regression test
    that pins this behavior.
    """
    if seed is None:
        return None
    text = "|".join(parts)
    offset = sum((i + 1) * ord(ch) for i, ch in enumerate(text))
    return seed + offset


def build_slices(
    records: Sequence[dict[str, Any]],
    *,
    threshold: float | None,
    confidence_level: float,
    ci_method: str,
    metric_bootstrap_resamples: int,
    seed: int | None,
) -> dict[str, Any]:
    slices: dict[str, Any] = {
        "overall": metric_block(
            records,
            threshold=threshold,
            confidence_level=confidence_level,
            ci_method=ci_method,
            metric_bootstrap_resamples=metric_bootstrap_resamples,
            seed=seed,
            include_per_signal=True,
        ),
        "by_register": {},
        "by_length_bucket": {},
        "by_language_status": {},
        "by_adversarial_class": {},
        "by_ai_status": {},
    }
    for slice_name, field in (
        ("by_register", "register"),
        ("by_length_bucket", "length_bucket"),
        ("by_language_status", "language_status"),
        ("by_adversarial_class", "adversarial_class"),
        ("by_ai_status", "ai_status"),
    ):
        for key, group in group_records(records, field).items():
            slices[slice_name][key] = metric_block(
                group,
                threshold=threshold,
                confidence_level=confidence_level,
                ci_method=ci_method,
                metric_bootstrap_resamples=metric_bootstrap_resamples,
                seed=derive_seed(seed, slice_name, key),
            )
    return slices


# ---------- Report ----------


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "--"
    if isinstance(value, float):
        if math.isinf(value):
            return "-inf" if value < 0 else "inf"
        return f"{value:.{digits}f}"
    return str(value)


def _fmt_rate(rate: dict[str, Any] | None) -> str:
    if not rate or rate.get("value") is None:
        return "--"
    return (
        f"{rate['value']:.3f} "
        f"[{_fmt(rate.get('ci_low'))}, {_fmt(rate.get('ci_high'))}]"
    )


def _fmt_metric_ci(value: Any, ci: dict[str, Any] | None) -> str:
    if value is None:
        return "--"
    if not ci or not ci.get("available"):
        return _fmt(value)
    return f"{_fmt(value)} [{_fmt(ci.get('ci_low'))}, {_fmt(ci.get('ci_high'))}]"


def claim_license_block(result: dict[str, Any]) -> dict[str, Any]:
    operating_point = result.get("operating_point", {})
    fpr_target = operating_point.get("fpr_target")
    if fpr_target is None:
        operating_text = "No FPR target supplied; thresholded classification rates omitted."
    elif operating_point.get("available"):
        operating_text = f"Threshold selected at requested FPR target {fpr_target}."
    else:
        operating_text = (
            f"FPR target {fpr_target} was supplied, but no threshold was "
            f"selected: {operating_point.get('reason', 'unavailable')}."
        )
    return {
        "licenses": (
            "This report describes how the evaluated SETEC surface performed "
            "on this manifest's labeled validation entries, in the reported "
            "register, length, AI-status, and language-status slices."
        ),
        "does_not_license": (
            "It does not prove provenance for any individual document, does "
            "not generalize outside this manifest, and does not publish a "
            "single aggregate accuracy number. Thresholded rates are only "
            "reported when an explicit FPR target is supplied."
        ),
        "operating_point": operating_text,
    }


def render_report(result: dict[str, Any]) -> str:
    if result.get("failed"):
        lines = [
            "# SETEC Validation Harness",
            "",
            f"**Task surface:** `{TASK_SURFACE}`",
            f"**Evaluated surface:** `{result.get('evaluated_surface')}`",
            f"**Manifest:** {result.get('manifest_path')}",
            "",
            "Harness did not run.",
            "",
            f"Reason: {result.get('reason', 'unknown failure')}",
        ]
        validation = result.get("manifest_validation", {})
        issues = validation.get("issues") or []
        if issues:
            lines.extend(["", "## Manifest Issues", ""])
            for issue in issues:
                lines.append(
                    f"- {issue.get('severity')} line {issue.get('lineno')}: "
                    f"{issue.get('message')}"
                )
        corpus_hygiene = result.get("corpus_hygiene") or {}
        if corpus_hygiene.get("checked"):
            lines.extend(["", "## Corpus Hygiene", ""])
            lines.append(f"Status: `{corpus_hygiene.get('status')}`")
            lines.append(
                f"Files: {corpus_hygiene.get('n_files', 0)} "
                f"({corpus_hygiene.get('n_clean', 0)} clean, "
                f"{corpus_hygiene.get('n_warning', 0)} warning, "
                f"{corpus_hygiene.get('n_fail', 0)} fail, "
                f"{corpus_hygiene.get('n_error', 0)} error)"
            )
            lines.append("")
            lines.append("| status | stripped | ratio | dominant rule | path | error |")
            lines.append("|---|---:|---:|---|---|---|")
            for record in corpus_hygiene.get("files", []):
                if record.get("status") not in {"fail", "error", "warning"}:
                    continue
                lines.append(
                    f"| {record.get('status')} | "
                    f"{record.get('tokens_stripped', 0)} | "
                    f"{float(record.get('strip_ratio', 0.0) or 0.0):.1%} | "
                    f"{record.get('dominant_rule') or ''} | "
                    f"`{record.get('path')}` | "
                    f"{record.get('error') or ''} |"
                )
        return "\n".join(lines)

    lines: list[str] = []
    lines.append("# SETEC Validation Harness")
    lines.append("")
    lines.append(f"**Task surface:** `{TASK_SURFACE}`")
    lines.append(f"**Evaluated surface:** `{result['evaluated_surface']}`")
    lines.append(f"**Manifest:** {result['manifest_path']}")
    lines.append(f"**Validation entries:** {result['n_validation_entries']}")
    lines.append(f"**Scored entries:** {result['n_scored_records']}")
    lines.append("")

    claim = result["claim_license"]
    structured = from_legacy(claim, task_surface=TASK_SURFACE)
    structured.comparison_set = {
        "manifest": result.get("manifest_path"),
        "evaluated_surface": result.get("evaluated_surface"),
        "n_validation_entries": result.get("n_validation_entries"),
        "n_scored_records": result.get("n_scored_records"),
    }
    op = result.get("operating_point") or {}
    if isinstance(op, dict) and op.get("fpr_target") is not None:
        structured.fpr_target = op.get("fpr_target")
    structured.additional_caveats = [claim.get("operating_point", "")]
    lines.append(structured.render_block().rstrip())
    lines.append("")

    corpus_hygiene = result.get("corpus_hygiene") or {}
    if corpus_hygiene.get("checked"):
        lines.append("## Corpus Hygiene")
        lines.append("")
        lines.append(f"- **Status:** `{corpus_hygiene.get('status')}`")
        lines.append(
            f"- **Files:** {corpus_hygiene.get('n_files', 0)} "
            f"({corpus_hygiene.get('n_clean', 0)} clean, "
            f"{corpus_hygiene.get('n_warning', 0)} warning, "
            f"{corpus_hygiene.get('n_fail', 0)} fail, "
            f"{corpus_hygiene.get('n_error', 0)} error)"
        )
        lines.append(
            f"- **Aggregate stripped:** {corpus_hygiene.get('tokens_stripped', 0)} / "
            f"{corpus_hygiene.get('input_tokens_before', 0)} tokens "
            f"({float(corpus_hygiene.get('strip_ratio', 0.0) or 0.0):.1%}; "
            f"dominant rule: {corpus_hygiene.get('dominant_rule') or 'none'})"
        )
        lines.append("")

    warnings = result.get("warnings") or []
    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")

    op = result.get("operating_point") or {}
    lines.append("## Operating Point")
    lines.append("")
    if op.get("available"):
        lines.append(f"- FPR target: `{op.get('fpr_target')}`")
        lines.append(f"- Threshold: `{_fmt(op.get('threshold'))}`")
        lines.append(f"- Empirical control FPR at threshold: `{_fmt(op.get('empirical_control_fpr'))}`")
    else:
        lines.append(f"- Threshold unavailable: {op.get('reason', 'no FPR target supplied')}")
    lines.append("")

    lines.append("## Overall Metrics")
    lines.append("")
    lines.append(_render_metric_table({"overall": result["slices"]["overall"]}))
    lines.append("")

    overall_block = result["slices"].get("overall") or {}
    per_signal = overall_block.get("per_signal_ranking")
    if per_signal:
        lines.append("## Per-Signal Discrimination")
        lines.append("")
        lines.append(
            "ROC AUC and average precision computed for each Layer A "
            "signal independently against the binary AI-vs-human label. "
            "Signals are computed on stripped-prose text (after the "
            "corpus-hygiene preprocessor), with records skipped when the "
            "signal is unavailable for them (Tier 2 missing, length floor "
            "not met, etc.). AUC < 0.5 indicates the signal moves "
            "inversely to the positive class on this corpus -- expected "
            "for some signals (e.g. MATTR, MTLD, FKGL std fall under "
            "compression; function-word ratio, Yule's K, connective "
            "density rise under compression). Bootstrap CIs are paired "
            "(label, signal_value) resamples; for small validation "
            "corpora these intervals will be wide, which is the right "
            "signal that per-signal calibration awaits more labeled data."
        )
        lines.append("")
        lines.append(_render_per_signal_table(per_signal))
        lines.append("")

    for title, key in (
        ("By Register", "by_register"),
        ("By Length Bucket", "by_length_bucket"),
        ("By Language Status", "by_language_status"),
        ("By Adversarial Class", "by_adversarial_class"),
        ("By AI Status", "by_ai_status"),
    ):
        lines.append(f"## {title}")
        lines.append("")
        lines.append(_render_metric_table(result["slices"][key]))
        lines.append("")

    lines.append("## Records")
    lines.append("")
    report_options = result.get("report_options", {})
    if not report_options.get("include_records_table", True):
        lines.append("Records table omitted from markdown; full records are present in JSON output.")
    else:
        record_limit = int(report_options.get("records_limit", DEFAULT_RECORDS_LIMIT) or 0)
        sorted_records = sorted(result["records"], key=lambda x: str(x.get("id")))
        shown_records = sorted_records[:record_limit] if record_limit > 0 else sorted_records
        lines.append("| id | ai_status | label | adversarial | register | language | words | score | band |")
        lines.append("|---|---|---|---|---|---|---:|---:|---|")
        for r in shown_records:
            label = "positive" if r.get("label") == 1 else "negative" if r.get("label") == 0 else "unlabeled"
            lines.append(
                f"| `{r.get('id')}` | {r.get('ai_status')} | {label} | "
                f"{r.get('adversarial_class') or 'none'} | "
                f"{r.get('register')} | {r.get('language_status')} | "
                f"{r.get('observed_word_count') or '--'} | {_fmt(r.get('score'))} | "
                f"{r.get('band') or r.get('metric_exclusion_reason') or r.get('error') or '--'} |"
            )
        if record_limit > 0 and len(sorted_records) > record_limit:
            lines.append("")
            lines.append(
                f"Showing {record_limit} of {len(sorted_records)} records. "
                "Use `--records-limit 0` for all records, `--no-records-table` "
                "to omit this table, or `--json` for complete structured output."
            )
    lines.append("")
    return "\n".join(lines)


def _render_per_signal_table(per_signal: dict[str, Any]) -> str:
    lines = [
        "| Signal | n | pos | neg | ROC AUC [CI] | Avg precision [CI] | Notes |",
        "|---|---:|---:|---:|---:|---:|:--|",
    ]
    for name, block in per_signal.items():
        notes_parts: list[str] = []
        if block.get("polarity_hint"):
            notes_parts.append(block["polarity_hint"])
        if block.get("reason"):
            notes_parts.append(block["reason"])
        notes = "; ".join(notes_parts) or "--"
        lines.append(
            f"| `{name}` | {block.get('n_scored', 0)} | "
            f"{block.get('n_positive', 0)} | {block.get('n_negative', 0)} | "
            f"{_fmt_metric_ci(block.get('roc_auc'), block.get('roc_auc_ci'))} | "
            f"{_fmt_metric_ci(block.get('average_precision'), block.get('average_precision_ci'))} | "
            f"{notes} |"
        )
    return "\n".join(lines)


def _render_metric_table(blocks: dict[str, Any]) -> str:
    lines = [
        "| slice | n | pos | neg | ROC AUC [CI] | Avg precision [CI] | FPR | TPR | Precision |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, block in blocks.items():
        ranking = block.get("ranking", {})
        labels = block.get("counts_by_label", {})
        tm = block.get("threshold_metrics") or {}
        rates = tm.get("rates", {}) if tm else {}
        lines.append(
            f"| {name} | {block.get('n_records', 0)} | "
            f"{labels.get('positive', 0)} | {labels.get('negative', 0)} | "
            f"{_fmt_metric_ci(ranking.get('roc_auc'), ranking.get('roc_auc_ci'))} | "
            f"{_fmt_metric_ci(ranking.get('average_precision'), ranking.get('average_precision_ci'))} | "
            f"{_fmt_rate(rates.get('fpr'))} | "
            f"{_fmt_rate(rates.get('tpr_recall'))} | "
            f"{_fmt_rate(rates.get('precision'))} |"
        )
    return "\n".join(lines)


# ---------- Scoring loop with progress + optional checkpoint ----------


def _vh_manifest_content_hash(manifest_path: Path) -> str:
    """SHA-256 of the manifest file content. Used as the cache
    invalidation key — if the user edits the manifest, the cache
    invalidates. Mirrors ``calibrate_thresholds._manifest_content_
    hash`` but re-implemented locally to avoid a cross-script
    dependency (validation_harness is a sibling of, not a
    consumer of, the calibration toolchain)."""
    h = hashlib.sha256()
    try:
        with manifest_path.open("rb") as f:
            for chunk in iter(lambda: f.read(64 * 1024), b""):
                h.update(chunk)
    except OSError:
        # Manifest unreadable — return a sentinel so the cache
        # invalidates rather than silently reusing stale entries.
        return "sha256:unreadable"
    return f"sha256:{h.hexdigest()}"


def _vh_corpus_text_fingerprint(
    entries: Sequence[dict[str, Any]],
) -> str:
    """SHA-256 over a canonical (resolved_path, text_sha256)
    listing. Mirrors ``calibrate_thresholds._corpus_text_finger
    print``: the manifest hash alone is not sufficient as a cache
    key because the manifest JSONL can stay byte-identical while
    the underlying text files it points to are regenerated (re-
    OCR, re-extraction, cleanup pass, preprocessing toggle change).
    This fingerprint hashes the actual bytes of every entry's
    resolved-path text plus the resolved path itself, in a
    deterministic order, so any change to any file the manifest
    references invalidates the cache.

    Codex P2 on PR #69: was missing from the validation harness's
    cache compat check, which only compared manifest path + tier
    flags. A complete cache could be served as authoritative even
    after the underlying text changed."""
    rows: list[tuple[str, str]] = []
    for entry in entries:
        resolved = entry.get("_resolved_path") or ""
        if not resolved:
            rows.append((
                str(entry.get("id") or ""), "no-resolved-path",
            ))
            continue
        try:
            with open(resolved, "rb") as f:
                inner = hashlib.sha256()
                for chunk in iter(lambda: f.read(64 * 1024), b""):
                    inner.update(chunk)
                rows.append((str(resolved), inner.hexdigest()))
        except OSError:
            rows.append((str(resolved), "unreadable"))
    rows.sort()
    outer = hashlib.sha256()
    for path_str, text_hash in rows:
        outer.update(path_str.encode("utf-8"))
        outer.update(b"\x00")
        outer.update(text_hash.encode("utf-8"))
        outer.update(b"\x00")
    return f"sha256:{outer.hexdigest()}"


def _entry_id_for_validation_record(entry: dict[str, Any]) -> str:
    """Match the id ``score_smoothing_entry`` writes onto every
    record. Used by ``_score_validation_entries_with_progress`` to
    skip entries already in the partial cache on resume.

    Mirrors the same helper in ``calibrate_thresholds._entry_id_for_
    record`` and the entry-id construction in ``score_smoothing_
    entry`` itself (~line 169). All three must produce identical IDs
    from the same entry dict or resume silently re-scores."""
    e_id = entry.get("id")
    if isinstance(e_id, str):
        return e_id
    return f"line_{entry.get('_lineno', '?')}"


def _save_scored_records_cache(
    path: Path,
    records: list[dict[str, Any]],
    *,
    n_entries_total: int,
    status: str,
    manifest_path: str,
    manifest_sha256: str,
    corpus_text_fingerprint: str,
    use_filter: str,
    do_tier2: bool,
    do_tier3: bool,
    mattr_window: int,
    allow_non_prose: bool,
    strip_rules: Any,
    strip_aggressive: bool,
    positive_statuses: set[str],
    negative_statuses: set[str],
) -> None:
    """Atomic write of the scored-records cache. ``status`` flips
    from ``"in_progress"`` (per-flush) to ``"complete"`` on the
    final write after the scoring loop exits cleanly.

    Compat fields (codex P2 on PR #69): every scoring arg that
    affects ``score_smoothing_entry``'s output is recorded here
    and checked on the next run. Without them, a cache produced
    under different mattr_window / strip_rules / label maps would
    be served as authoritative on a re-run, silently mixing
    incompatible scored records."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "status": status,
        "tool": "validation_harness",
        "tool_version": "1.70.0",
        "scoring_meta": {
            # Corpus identity (P2 fix: SHA + content fingerprint).
            "manifest_path": manifest_path,
            "manifest_sha256": manifest_sha256,
            "corpus_text_fingerprint": corpus_text_fingerprint,
            # Filter + tier flags.
            "use_filter": use_filter,
            "do_tier2": do_tier2,
            "do_tier3": do_tier3,
            # Scoring args that change score_smoothing_entry output.
            "mattr_window": mattr_window,
            "allow_non_prose": allow_non_prose,
            "strip_rules": strip_rules,
            "strip_aggressive": strip_aggressive,
            "positive_statuses": sorted(positive_statuses),
            "negative_statuses": sorted(negative_statuses),
            # Bookkeeping.
            "n_entries_total": n_entries_total,
            "n_entries_scored": len(records),
            "scored_at": _dt.datetime.now(
                _dt.timezone.utc,
            ).isoformat(),
        },
        "records": records,
    }
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, default=str)
    tmp.replace(path)


def _scored_records_compat_reason(
    cache_meta: dict[str, Any],
    *,
    manifest_path: str,
    manifest_sha256: str,
    corpus_text_fingerprint: str,
    use_filter: str,
    do_tier2: bool,
    do_tier3: bool,
    mattr_window: int,
    allow_non_prose: bool,
    strip_rules: Any,
    strip_aggressive: bool,
    positive_statuses: set[str],
    negative_statuses: set[str],
) -> str | None:
    """Return ``None`` when the cache_meta is compatible with the
    current run, or a one-line human-readable reason string when
    not. Codex P2 on PR #69: tolerates missing fields (older caches
    pre-fix won't have them all), refuses on present-but-different.
    Mirrors the pattern used by ``cache_is_compatible`` in
    calibrate_thresholds."""
    if cache_meta.get("manifest_path") != manifest_path:
        return (
            f"manifest_path differs (prior="
            f"{cache_meta.get('manifest_path')!r}, "
            f"current={manifest_path!r})"
        )
    prior_sha = cache_meta.get("manifest_sha256")
    if prior_sha is not None and prior_sha != manifest_sha256:
        return (
            f"manifest_sha256 differs (prior={prior_sha[:16]}..., "
            f"current={manifest_sha256[:16]}...)"
        )
    prior_fp = cache_meta.get("corpus_text_fingerprint")
    if prior_fp is not None and prior_fp != corpus_text_fingerprint:
        return (
            f"corpus_text_fingerprint differs (prior="
            f"{prior_fp[:16]}..., current="
            f"{corpus_text_fingerprint[:16]}...). The manifest is "
            f"unchanged but at least one referenced text file's "
            f"content has changed."
        )
    if cache_meta.get("use_filter") != use_filter:
        return f"use_filter differs"
    if bool(cache_meta.get("do_tier2")) != do_tier2:
        return f"do_tier2 differs"
    if bool(cache_meta.get("do_tier3")) != do_tier3:
        return f"do_tier3 differs"
    prior_mattr = cache_meta.get("mattr_window")
    if prior_mattr is not None and prior_mattr != mattr_window:
        return f"mattr_window differs (prior={prior_mattr}, current={mattr_window})"
    prior_allow = cache_meta.get("allow_non_prose")
    if prior_allow is not None and bool(prior_allow) != allow_non_prose:
        return "allow_non_prose differs"
    prior_strip = cache_meta.get("strip_rules")
    if prior_strip is not None and prior_strip != strip_rules:
        return f"strip_rules differs (prior={prior_strip!r}, current={strip_rules!r})"
    prior_aggr = cache_meta.get("strip_aggressive")
    if prior_aggr is not None and bool(prior_aggr) != strip_aggressive:
        return "strip_aggressive differs"
    prior_pos = cache_meta.get("positive_statuses")
    if (
        prior_pos is not None
        and sorted(prior_pos) != sorted(positive_statuses)
    ):
        return f"positive_statuses differs"
    prior_neg = cache_meta.get("negative_statuses")
    if (
        prior_neg is not None
        and sorted(prior_neg) != sorted(negative_statuses)
    ):
        return f"negative_statuses differs"
    return None


def _score_validation_entries_with_progress(
    entries: Sequence[dict[str, Any]],
    *,
    mattr_window: int,
    do_tier2: bool,
    do_tier3: bool,
    allow_non_prose: bool,
    strip_rules: Any,
    strip_aggressive: bool,
    positive_statuses: set[str],
    negative_statuses: set[str],
    cache_path: Path | None = None,
    flush_every: int = 100,
    refresh_cache: bool = False,
    manifest_path: str = "",
    use_filter: str = "",
) -> list[dict[str, Any]]:
    """Score every validation manifest entry, with two operational
    affordances the original list-comp lacked:

      * **MEASURE** — log progress every ``flush_every`` entries
        with rate (entries/s) and ETA (minutes-to-completion). For
        an 8M-row corpus at ~100ms/row, the operator sees one line
        per ~10s instead of silence-until-completion.
      * **SAVE PROGRESS** — when ``cache_path`` is set, atomically
        write the scored-records list as a partial cache with
        ``status: "in_progress"`` every ``flush_every`` entries. A
        crash mid-loop loses at most ``flush_every`` entries. On
        the next run, the partial cache is loaded, scored entry IDs
        are skipped, and scoring resumes from where it left off.
        The final write flips status to ``"complete"``.

    Without ``cache_path``, behaves like the original list-comp
    plus the progress log — back-compat for callers that don't
    want a cache.

    ``refresh_cache`` discards any existing cache and re-scores
    from scratch. Useful after a code change that should invalidate
    cached records but won't be caught by the cache's compatibility
    check (which is intentionally minimal here — see the
    calibration_survey path for the full cache_is_compatible flow).
    """
    records: list[dict[str, Any]] = []
    scored_ids: set[str] = set()

    # Compute the manifest SHA + corpus text fingerprint up front
    # — used both for the compat check on the prior cache (if any)
    # AND for the metadata written into the new cache. Cheap
    # relative to the scoring loop (file hashing + sorted-listing
    # hash).
    manifest_sha256 = _vh_manifest_content_hash(Path(manifest_path))
    corpus_text_fingerprint = _vh_corpus_text_fingerprint(entries)

    if cache_path is not None and cache_path.exists() and not refresh_cache:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            cache_status = cached.get("status", "complete")
            cache_meta = cached.get("scoring_meta") or {}
            # Full compatibility check (codex P2 on PR #69): every
            # scoring arg that affects score_smoothing_entry's
            # output is compared. Tolerates missing fields on the
            # prior payload (pre-fix caches won't have them all);
            # refuses on present-but-different.
            incompat_reason = _scored_records_compat_reason(
                cache_meta,
                manifest_path=manifest_path,
                manifest_sha256=manifest_sha256,
                corpus_text_fingerprint=corpus_text_fingerprint,
                use_filter=use_filter,
                do_tier2=do_tier2,
                do_tier3=do_tier3,
                mattr_window=mattr_window,
                allow_non_prose=allow_non_prose,
                strip_rules=strip_rules,
                strip_aggressive=strip_aggressive,
                positive_statuses=positive_statuses,
                negative_statuses=negative_statuses,
            )
            if (
                incompat_reason is None
                and cache_status in ("in_progress", "complete")
            ):
                records = cached.get("records") or []
                scored_ids = {
                    r.get("id") for r in records
                    if isinstance(r.get("id"), str)
                }
                if cache_status == "complete":
                    sys.stderr.write(
                        f"Scored-records cache hit ({cache_path}): "
                        f"{len(records)} records loaded.\n"
                    )
                    return records
                sys.stderr.write(
                    f"Resuming validation scoring from partial cache "
                    f"({cache_path}): {len(records)} of {len(entries)} "
                    f"entries already scored.\n"
                )
            elif incompat_reason is not None:
                sys.stderr.write(
                    f"Scored-records cache at {cache_path} is "
                    f"incompatible ({incompat_reason}); discarding "
                    f"and re-scoring. Pass "
                    f"--refresh-scored-records-cache to suppress "
                    f"this check.\n"
                )
            else:
                sys.stderr.write(
                    f"Scored-records cache at {cache_path} has "
                    f"unknown status ({cache_status!r}); discarding "
                    f"and re-scoring.\n"
                )
        except (json.JSONDecodeError, OSError) as exc:
            sys.stderr.write(
                f"Scored-records cache at {cache_path} is unreadable "
                f"({exc}); discarding and re-scoring.\n"
            )

    to_score = [
        e for e in entries
        if _entry_id_for_validation_record(e) not in scored_ids
    ]
    sys.stderr.write(
        f"Scoring {len(to_score)} validation entries "
        f"(of {len(entries)} total; {len(scored_ids)} resumed).\n"
    )
    score_t0 = _dt.datetime.now()
    for i, e in enumerate(to_score):
        if i > 0 and i % flush_every == 0:
            elapsed = (_dt.datetime.now() - score_t0).total_seconds()
            rate = i / max(elapsed, 1e-9)
            remaining = len(to_score) - i
            eta_s = remaining / max(rate, 1e-9)
            sys.stderr.write(
                f"  scored {i}/{len(to_score)} "
                f"({rate:.1f}/s, ETA {eta_s/60:.1f} min)"
            )
            if cache_path is not None:
                try:
                    _save_scored_records_cache(
                        cache_path, records,
                        n_entries_total=len(entries),
                        status="in_progress",
                        manifest_path=manifest_path,
                        manifest_sha256=manifest_sha256,
                        corpus_text_fingerprint=corpus_text_fingerprint,
                        use_filter=use_filter,
                        do_tier2=do_tier2,
                        do_tier3=do_tier3,
                        mattr_window=mattr_window,
                        allow_non_prose=allow_non_prose,
                        strip_rules=strip_rules,
                        strip_aggressive=strip_aggressive,
                        positive_statuses=positive_statuses,
                        negative_statuses=negative_statuses,
                    )
                    sys.stderr.write(" -> partial cache flushed\n")
                except Exception as exc:  # noqa: BLE001
                    sys.stderr.write(
                        f" -> WARNING: partial flush failed "
                        f"({type(exc).__name__}: {exc})\n"
                    )
            else:
                sys.stderr.write("\n")
        records.append(
            score_smoothing_entry(
                e,
                mattr_window=mattr_window,
                do_tier2=do_tier2,
                do_tier3=do_tier3,
                allow_non_prose=allow_non_prose,
                strip_rules=strip_rules,
                strip_aggressive=strip_aggressive,
                positive_statuses=positive_statuses,
                negative_statuses=negative_statuses,
            )
        )

    if cache_path is not None:
        try:
            _save_scored_records_cache(
                cache_path, records,
                n_entries_total=len(entries),
                status="complete",
                manifest_path=manifest_path,
                manifest_sha256=manifest_sha256,
                corpus_text_fingerprint=corpus_text_fingerprint,
                use_filter=use_filter,
                do_tier2=do_tier2,
                do_tier3=do_tier3,
                mattr_window=mattr_window,
                allow_non_prose=allow_non_prose,
                strip_rules=strip_rules,
                strip_aggressive=strip_aggressive,
                positive_statuses=positive_statuses,
                negative_statuses=negative_statuses,
            )
            sys.stderr.write(
                f"Scored-records cache written to {cache_path} "
                f"({len(records)} records, status=complete).\n"
            )
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(
                f"WARNING: final cache write to {cache_path} failed "
                f"({type(exc).__name__}: {exc}).\n"
            )

    return records


# ---------- Harness driver ----------


def run_harness(args: argparse.Namespace) -> dict[str, Any]:
    manifest_result = validate_manifest(args.manifest)
    if manifest_result["n_errors"] > 0:
        return {
            "task_surface": TASK_SURFACE,
            "evaluated_surface": args.surface,
            "manifest_path": str(args.manifest),
            "failed": True,
            "reason": "manifest validation failed",
            "manifest_validation": manifest_result,
        }
    if args.strict_manifest and manifest_result["n_warnings"] > 0:
        return {
            "task_surface": TASK_SURFACE,
            "evaluated_surface": args.surface,
            "manifest_path": str(args.manifest),
            "failed": True,
            "reason": "manifest warnings present and --strict-manifest was supplied",
            "manifest_validation": manifest_result,
        }

    entries = [
        e for e in load_manifest_entries(args.manifest)
        if _entry_uses(e, args.use)
        and not _entry_uses(e, "exclude")
    ]
    corpus_hygiene: dict[str, Any] = {"checked": False}
    if args.check_corpus:
        corpus_hygiene = check_corpus_paths(
            [e["_resolved_path"] for e in entries if e.get("_resolved_path")],
            strip_rules=args.strip_rules,
            strip_aggressive=args.strip_aggressive,
            warn_threshold=args.corpus_warn_threshold,
            fail_threshold=args.corpus_fail_threshold,
        )
        corpus_hygiene["checked"] = True
        if corpus_hygiene.get("status") == "fail":
            return {
                "task_surface": TASK_SURFACE,
                "evaluated_surface": args.surface,
                "manifest_path": str(args.manifest),
                "failed": True,
                "reason": "corpus hygiene check failed",
                "manifest_validation": manifest_result,
                "corpus_hygiene": corpus_hygiene,
            }
    positive_statuses = set(args.positive_status)
    negative_statuses = set(args.negative_status)
    records = _score_validation_entries_with_progress(
        entries,
        mattr_window=args.mattr_window,
        do_tier2=not args.no_tier2,
        do_tier3=not args.no_tier3,
        allow_non_prose=args.allow_non_prose,
        strip_rules=args.strip_rules,
        strip_aggressive=args.strip_aggressive,
        positive_statuses=positive_statuses,
        negative_statuses=negative_statuses,
        cache_path=(
            Path(args.scored_records_cache).expanduser()
            if getattr(args, "scored_records_cache", None)
            else None
        ),
        flush_every=int(
            getattr(args, "scored_records_flush_every", 100)
        ),
        refresh_cache=bool(
            getattr(args, "refresh_scored_records_cache", False)
        ),
        manifest_path=str(args.manifest),
        use_filter=args.use,
    )
    usable = scored_records(records)

    operating_point: dict[str, Any]
    threshold: float | None = None
    if args.fpr_target is not None:
        operating_point = choose_threshold_at_fpr(usable, args.fpr_target)
        if operating_point.get("available"):
            threshold = float(operating_point["threshold"])
    else:
        operating_point = {
            "available": False,
            "reason": "no FPR target supplied",
            "threshold": None,
            "fpr_target": None,
        }

    slices = build_slices(
        records,
        threshold=threshold,
        confidence_level=args.confidence_level,
        ci_method=args.ci_method,
        metric_bootstrap_resamples=args.metric_bootstrap_resamples,
        seed=args.seed,
    )

    warnings: list[str] = []
    if not HAS_SKLEARN:
        warnings.append(
            "scikit-learn not installed; ROC AUC and average precision "
            "use the stdlib fallback. Install requirements.txt for the "
            "survey-backed metric implementation."
        )
    if not HAS_STATSMODELS:
        warnings.append(
            "statsmodels not installed; proportion intervals use a local "
            "Wilson fallback. Install requirements.txt for statsmodels "
            "interval methods."
        )
    if manifest_result["n_warnings"] > 0:
        warnings.append(
            f"Manifest validator emitted {manifest_result['n_warnings']} "
            "warnings. They are included in manifest_validation."
        )
    if corpus_hygiene.get("status") == "warning":
        warnings.append(
            "Corpus hygiene check emitted warnings. Inspect the "
            "corpus_hygiene block before treating validation metrics as stable."
        )
    if args.allow_non_prose and args.check_corpus:
        warnings.append(
            "--allow-non-prose was supplied while --check-corpus was active. "
            "The preflight reports what preprocessing would strip, but scoring "
            "kept non-prose in the text."
        )
    if any(r.get("ai_status") == "mixed" and r.get("label") is None for r in records):
        warnings.append(
            "`mixed` entries are not mapped into the default binary label set. "
            "They remain visible in the per-ai_status slice and record output; "
            "map them explicitly with --positive-status mixed if that is the "
            "research question."
        )
    if not usable:
        warnings.append("No scored validation records were available for metrics.")
    overall_auc = slices["overall"]["ranking"].get("roc_auc")
    if isinstance(overall_auc, (int, float)) and overall_auc < 0.5:
        warnings.append(
            "Overall ROC AUC is below 0.5. Score polarity may be inverted "
            "relative to the positive label mapping, or this corpus may "
            "reverse the assumed direction."
        )
    if args.fpr_target is not None and operating_point.get("available"):
        warnings.append(
            "Operating-point threshold is selected and evaluated on the same "
            "validation entries. Treat thresholded rates as in-sample until "
            "a separate calibration/test split lands."
        )

    result: dict[str, Any] = {
        "task_surface": TASK_SURFACE,
        "evaluated_surface": args.surface,
        "manifest_path": str(args.manifest),
        "use_filter": args.use,
        "score_name": "compression_fraction",
        "positive_statuses": sorted(positive_statuses),
        "negative_statuses": sorted(negative_statuses),
        "metric_bootstrap": {
            "n_resamples": args.metric_bootstrap_resamples,
            "confidence_level": args.confidence_level,
            "seed": args.seed,
        },
        "n_validation_entries": len(entries),
        "n_scored_records": len(usable),
        "manifest_validation": manifest_result,
        "corpus_hygiene": corpus_hygiene,
        "operating_point": operating_point,
        "warnings": warnings,
        "report_options": {
            "include_records_table": not args.no_records_table,
            "records_limit": args.records_limit,
        },
        "records": records,
        "slices": slices,
    }
    result["claim_license"] = claim_license_block(result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate SETEC scores against a labeled corpus manifest."
    )
    parser.add_argument("manifest", help="Path to corpus_manifest.jsonl.")
    parser.add_argument(
        "--surface",
        choices=["smoothing_diagnosis"],
        default=EVALUATED_SURFACE,
        help="Task surface to evaluate (MVP: smoothing_diagnosis only).",
    )
    parser.add_argument(
        "--use",
        default="validation",
        help="Manifest use tag to evaluate (default: validation).",
    )
    parser.add_argument(
        "--positive-status",
        action="append",
        default=None,
        help=(
            "ai_status value treated as positive. Repeatable. Defaults to "
            "ai_generated, ai_assisted, ai_edited. `mixed` is left as its "
            "own unlabeled slice unless mapped explicitly."
        ),
    )
    parser.add_argument(
        "--negative-status",
        action="append",
        default=None,
        help="ai_status value treated as negative/control. Repeatable. Default: pre_ai_human.",
    )
    parser.add_argument(
        "--fpr-target",
        type=float,
        default=None,
        help=(
            "Explicit operating-point target. When supplied, the harness "
            "selects the score threshold that maximizes empirical TPR "
            "subject to control FPR <= this value and reports thresholded "
            "rates. Omit to report ranking metrics only."
        ),
    )
    parser.add_argument(
        "--confidence-level",
        type=float,
        default=0.95,
        help="Confidence level for proportion intervals (default 0.95).",
    )
    parser.add_argument(
        "--metric-bootstrap-resamples",
        type=int,
        default=DEFAULT_METRIC_BOOTSTRAP_RESAMPLES,
        help=(
            "Paired bootstrap resamples for ROC AUC / average precision "
            f"CIs (default {DEFAULT_METRIC_BOOTSTRAP_RESAMPLES}; pass 0 "
            "to disable)."
        ),
    )
    parser.add_argument(
        "--ci-method",
        default="wilson",
        help="statsmodels proportion_confint method when statsmodels is installed (default wilson).",
    )
    parser.add_argument("--seed", type=int, default=None, help="Seed for metric bootstrap resampling.")
    parser.add_argument("--mattr-window", type=int, default=50)
    parser.add_argument("--no-tier2", action="store_true", help="Skip spaCy-backed Tier 2 metrics.")
    parser.add_argument("--no-tier3", action="store_true", help="Skip adjacent-cosine Tier 3 metrics.")
    parser.add_argument(
        "--allow-non-prose",
        action="store_true",
        help="Skip default corpus-hygiene stripping for validation entries.",
    )
    parser.add_argument(
        "--strip-rules",
        help="Comma-separated preprocessing rules to enable. Default: all "
             "conservative rules. Available: "
             + ", ".join(available_rule_names()) + ".",
    )
    parser.add_argument(
        "--strip-aggressive",
        action="store_true",
        help="Also strip URL-only lines, image URLs, link wrappers, footnotes, and citations.",
    )
    parser.add_argument(
        "--strict-manifest",
        action="store_true",
        help="Fail if manifest validation emits warnings as well as errors.",
    )
    parser.add_argument(
        "--check-corpus",
        action="store_true",
        help=(
            "Run the corpus-hygiene gate on selected validation entries before "
            "scoring. Fails the harness if any selected file exceeds the "
            "corpus fail threshold."
        ),
    )
    parser.add_argument(
        "--corpus-warn-threshold",
        type=float,
        default=0.01,
        help="Strip-ratio warning threshold for --check-corpus (default 0.01).",
    )
    parser.add_argument(
        "--corpus-fail-threshold",
        type=float,
        default=0.05,
        help="Strip-ratio fail threshold for --check-corpus (default 0.05).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown.")
    parser.add_argument("--out", help="Write report to file instead of stdout.")
    parser.add_argument(
        "--no-records-table",
        action="store_true",
        help="Omit the per-record table from markdown reports. JSON still includes all records.",
    )
    parser.add_argument(
        "--records-limit",
        type=int,
        default=DEFAULT_RECORDS_LIMIT,
        help=(
            f"Maximum records shown in markdown table (default {DEFAULT_RECORDS_LIMIT}; "
            "0 means no limit). JSON always includes all records."
        ),
    )
    # Scored-records cache + incremental checkpoint (1.70.0).
    # Mirrors the pattern in calibration_survey.py / calibrate_
    # thresholds.py (PRs #64 / #68): scoring loop writes a partial
    # cache every N entries with status='in_progress' so a crash
    # mid-loop doesn't lose what's been scored. Default off (no
    # cache, original behavior) — opt-in for long runs.
    parser.add_argument(
        "--scored-records-cache",
        default=None,
        help=(
            "Path to a JSON cache of scored records. Optional; "
            "default behavior is no cache (original list-comp + "
            "progress log only). When set, the scoring loop writes "
            "the cache atomically every "
            "--scored-records-flush-every entries with status="
            "'in_progress', then flips to 'complete' on the final "
            "write. On the next run with the same flag, scored "
            "entry IDs are loaded from the cache and skipped — "
            "scoring resumes from where it left off. The cache's "
            "compatibility check is intentionally minimal "
            "(manifest path + use filter + tier flags); operators "
            "changing scoring args should pass "
            "--refresh-scored-records-cache or a fresh path."
        ),
    )
    parser.add_argument(
        "--scored-records-flush-every",
        type=int,
        default=100,
        help=(
            "Write --scored-records-cache atomically every N "
            "entries (default 100). Lower (10-50) for slow per-"
            "entry tier3 runs with high crash exposure; higher "
            "(500+) for short-per-entry tier1-only runs where "
            "flush I/O would dominate. Ignored when "
            "--scored-records-cache is unset."
        ),
    )
    parser.add_argument(
        "--refresh-scored-records-cache",
        action="store_true",
        help=(
            "Discard any existing --scored-records-cache and "
            "re-score from scratch. Use when a code change should "
            "invalidate cached records but won't be caught by the "
            "minimal compatibility check (manifest path + use "
            "filter + tier flags)."
        ),
    )
    args = parser.parse_args(argv)

    if args.fpr_target is not None and not 0 <= args.fpr_target <= 1:
        parser.error("--fpr-target must be between 0 and 1.")
    if not 0 < args.confidence_level < 1:
        parser.error("--confidence-level must be between 0 and 1.")
    if args.metric_bootstrap_resamples < 0:
        parser.error("--metric-bootstrap-resamples must be >= 0.")
    if args.records_limit < 0:
        parser.error("--records-limit must be >= 0.")
    if args.corpus_warn_threshold < 0 or args.corpus_fail_threshold < 0:
        parser.error("Corpus hygiene thresholds must be non-negative.")
    if args.corpus_warn_threshold > args.corpus_fail_threshold:
        parser.error("--corpus-warn-threshold must be <= --corpus-fail-threshold.")
    try:
        strip_non_prose(
            "",
            args.strip_rules,
            allow_non_prose=args.allow_non_prose,
            strip_aggressive=args.strip_aggressive,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if args.positive_status is None:
        args.positive_status = list(DEFAULT_POSITIVE_STATUSES)
    if args.negative_status is None:
        args.negative_status = list(DEFAULT_NEGATIVE_STATUSES)

    result = run_harness(args)
    output = json.dumps(result, indent=2, default=str) if args.json else render_report(result)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(output)

    if result.get("failed"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
