#!/usr/bin/env python3
"""calibrate_thresholds.py

Step 5 of the calibration toolchain. Direction-aware per-signal
threshold sweep + provenance writer.

Reads a labeled manifest, runs SETEC's variance audit on each
entry, extracts the named per-signal score array via the harness's
`collect_signal_records` helper, sweeps thresholds at the requested
FPR target, and writes a provenance entry to
`scripts/calibration/thresholds_calibrated.json`.

The derived value is encoded in `scripts/variance_audit.py`'s
`COMPRESSION_HEURISTICS` registry by setting `provenance=<slug>` on
the appropriate `ThresholdSpec` (a manual edit; this script writes
the ledger, not the registry).

Direction-awareness: each signal's `direction` (`gt` or `lt`) comes
from the registry. For `gt` signals (compressed when score >
threshold), candidate predictions are `score > threshold`. For `lt`
signals, candidate predictions are `score < threshold`. Picking
the wrong direction would invert the AUC and produce a useless
threshold; the registry's direction is the single source of truth.

FPR-resolution check: at small N, the requested FPR target may be
statistically meaningless. The script computes
`fpr_resolution = 1 / n_neg` and refuses targets below it. If
`n_neg < 30`, it warns that the FPR estimate is statistically
unstable.

Bootstrap CIs: v1 does fixed-threshold paired bootstrap on TPR /
FPR / precision at the chosen threshold. Nested bootstrap on the
threshold itself (selection uncertainty) is roadmap.

Usage:

    python3 scripts/calibration/calibrate_thresholds.py \\
        --manifest ai-prose-baselines-private/editlens/manifest_nonnative.jsonl \\
        --use validation \\
        --signal burstiness_B \\
        --fpr-target 0.01 \\
        --out scripts/calibration/thresholds_calibrated.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import random
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

# After 1.16.0, scripts live inside the plugin directory.
# parents[4] is the repo root in dev (and the marketplace root after
# install); parents[1] is the scripts/ dir for the sys.path import.
REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from manifest_validator import validate_manifest  # type: ignore
from validation_harness import (  # type: ignore
    DEFAULT_NEGATIVE_STATUSES,
    DEFAULT_POSITIVE_STATUSES,
    _entry_uses,
    collect_signal_records,
    load_manifest_entries,
    score_smoothing_entry,
)
from variance_audit import COMPRESSION_HEURISTICS  # type: ignore

# Cache key bumped when the scoring code's record shape changes in a
# way that invalidates older caches. Read by `cache_is_compatible`.
# Bump this when:
#   * `score_smoothing_entry` adds / removes / renames a signal
#     column.
#   * The Tier 2/3 feature set changes shape.
#   * A bugfix changes computed values for the same input (callers
#     must re-score to pick up the fix).
SCORER_CACHE_VERSION = "1.26.0"


def _stable_seed(base_seed: int | None, *parts: str) -> int | None:
    """SHA-256-derived seed for cross-process bootstrap reproducibility.
    Same pattern as voice_validation_harness._stable_seed (1.9.0)."""
    if base_seed is None:
        return None
    payload = f"{base_seed}|{'|'.join(parts)}".encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    frac = pos - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _confusion(
    pairs: Sequence[tuple[int, float]],
    threshold: float,
    direction: str,
) -> tuple[int, int, int, int]:
    """Return (tp, fp, tn, fn) for a direction-aware threshold call.
    direction='gt': predict positive when score > threshold.
    direction='lt': predict positive when score < threshold."""
    tp = fp = tn = fn = 0
    for label, score in pairs:
        if direction == "gt":
            predicted = score > threshold
        else:  # "lt"
            predicted = score < threshold
        if predicted and label == 1:
            tp += 1
        elif predicted and label == 0:
            fp += 1
        elif not predicted and label == 0:
            tn += 1
        else:
            fn += 1
    return tp, fp, tn, fn


def _rates(tp: int, fp: int, tn: int, fn: int) -> dict[str, float]:
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    tpr = tp / (tp + fn) if (tp + fn) else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    return {"fpr": fpr, "tpr": tpr, "precision": precision}


# Size at which sweep_threshold dispatches to the O(n log n) fast
# path. Below this the original O(n × unique_scores) implementation
# is preserved bit-exactly to keep existing test fixtures green and
# to honor the candidate_log emission on failure. At MAGE / RAID
# scale (n > 30K) the fast path is mandatory — production was taking
# 67s at n=30K and >70 min at n=338K. The fast path returns in <0.3s
# at n=338K (≥16,000× speedup empirically measured 2026-05-16).
_SWEEP_THRESHOLD_FAST_DISPATCH_N = 5000


def _sweep_threshold_loop(
    pairs: Sequence[tuple[int, float]],
    direction: str,
    fpr_target: float,
    *,
    n_pos: int,
    n_neg: int,
    fpr_resolution: float,
) -> dict[str, Any]:
    """Original O(n × k) implementation. Preserved for small
    corpora and as the bit-exact reference for tests."""
    # Candidate thresholds: every observed score, plus an "epsilon
    # outside" sentinel so the all-negative case is reachable.
    scores_sorted = sorted({s for _, s in pairs})
    eps = 1e-9
    if direction == "gt":
        candidates = [scores_sorted[-1] + eps] + scores_sorted
    else:  # "lt"
        candidates = [scores_sorted[0] - eps] + scores_sorted

    best: dict[str, Any] | None = None
    candidate_log: list[dict[str, Any]] = []
    for t in candidates:
        tp, fp, tn, fn = _confusion(pairs, t, direction)
        r = _rates(tp, fp, tn, fn)
        row = {
            "threshold": t,
            "fpr": r["fpr"],
            "tpr": r["tpr"],
            "precision": r["precision"],
            "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        }
        candidate_log.append(row)
        if r["fpr"] <= fpr_target and (best is None or r["tpr"] > best["tpr"]):
            best = row

    if best is None:
        return {
            "available": False,
            "reason": "no threshold satisfies the FPR target",
            "n_pos": n_pos,
            "n_neg": n_neg,
            "fpr_resolution": fpr_resolution,
            "candidates": candidate_log,
        }
    return {
        "available": True,
        "n_pos": n_pos,
        "n_neg": n_neg,
        "fpr_resolution": fpr_resolution,
        **best,
    }


def _sweep_threshold_fast(
    pairs: Sequence[tuple[int, float]],
    direction: str,
    fpr_target: float,
    *,
    n_pos: int,
    n_neg: int,
    fpr_resolution: float,
) -> dict[str, Any]:
    """O(n log n) sort-and-scan implementation. Mathematically
    equivalent to the loop version at the chosen operating point AND
    at the returned threshold: feeding the returned ``threshold``
    back through ``_confusion`` (strict ``>`` for ``gt``, strict
    ``<`` for ``lt``) reproduces the same TP / FP / TPR / FPR the
    fast path reported. This is the contract the downstream
    bootstrap CI + persisted-threshold consumers rely on; without
    it a calibration entry's threshold and rates disagree.

    Algorithm:
      1. Sort pairs by score in the direction-relevant order
         (descending for ``gt``, ascending for ``lt``).
      2. Walk the sorted pairs, advancing cumulative TP / FP
         counters at each new unique score. **Snapshot the
         cumulative BEFORE consuming the tied-score group** at
         ``cur_score`` — this is the operating point at
         ``threshold = cur_score`` under strict-inequality
         semantics: pairs strictly past ``cur_score`` (the ones
         consumed in prior iterations) are predicted positive,
         pairs at ``cur_score`` (this group) are NOT.
      3. Track the row with maximal TPR subject to FPR ≤ target.

    Total: one O(n log n) sort plus one O(n) walk. At MAGE scale
    (n = 338K) this returns in < 0.3 s vs > 70 min for the loop
    path. The candidate_log emitted by the loop on failure is
    omitted here — at MAGE / RAID scale that log would be 100K-8M
    rows of survey-JSON bloat that nothing consumes downstream.

    Failure case ``"no threshold satisfies the FPR target"``
    returns without ``candidates`` (the loop path's emission is
    preserved when ``n < _SWEEP_THRESHOLD_FAST_DISPATCH_N``).
    """
    if direction == "gt":
        sorted_pairs = sorted(pairs, key=lambda p: -p[1])
    else:  # "lt"
        sorted_pairs = sorted(pairs, key=lambda p: p[1])

    # Sentinel "epsilon outside" the score range — matches the loop
    # path's first candidate, where no pair is predicted positive
    # (TP = 0, FP = 0). For some FPR targets this IS the operating
    # point that maximizes TPR-under-constraint (e.g., a target so
    # tight that any non-zero FP exceeds it).
    eps = 1e-9
    sentinel_thr = (
        sorted_pairs[0][1] + eps if direction == "gt"
        else sorted_pairs[0][1] - eps
    )
    best = {
        "threshold": sentinel_thr,
        "fpr": 0.0,
        "tpr": 0.0,
        "precision": 1.0,
        "tp": 0, "fp": 0, "tn": n_neg, "fn": n_pos,
    }

    n = len(sorted_pairs)
    cumul_tp = 0
    cumul_fp = 0
    i = 0
    while i < n:
        cur_score = sorted_pairs[i][1]
        # SNAPSHOT BEFORE consuming the tied-score group. The
        # operating point at ``threshold = cur_score`` under
        # ``_confusion``'s strict ``>`` / ``<`` semantics is
        # "pairs whose score is strictly past cur_score" — i.e.
        # the pairs consumed in PRIOR iterations, not the current
        # group. Post-consumption cumulative would include the
        # current group (==cur_score) and disagree with
        # ``_confusion(pairs, cur_score, direction)`` whenever
        # there's a tied-score group of size > 0 at the boundary.
        # For continuous data the difference is one pair per
        # candidate; for tied scores the entire equality block
        # shifts the operating point.
        tp, fp = cumul_tp, cumul_fp
        tn, fn = n_neg - fp, n_pos - tp
        fpr = fp / n_neg
        tpr = tp / n_pos
        precision = (tp / (tp + fp)) if (tp + fp) > 0 else 1.0

        if fpr <= fpr_target and tpr > best["tpr"]:
            best = {
                "threshold": cur_score,
                "fpr": fpr,
                "tpr": tpr,
                "precision": precision,
                "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            }

        # Consume all pairs at cur_score, advancing the cumulative
        # for the NEXT candidate's snapshot.
        while i < n and sorted_pairs[i][1] == cur_score:
            y, _ = sorted_pairs[i]
            if y == 1:
                cumul_tp += 1
            else:
                cumul_fp += 1
            i += 1

    return {
        "available": True,
        "n_pos": n_pos,
        "n_neg": n_neg,
        "fpr_resolution": fpr_resolution,
        **best,
    }


def sweep_threshold(
    pairs: Sequence[tuple[int, float]],
    direction: str,
    fpr_target: float,
) -> dict[str, Any]:
    """Direction-aware sweep. Picks the highest-TPR threshold whose
    empirical FPR <= target. Returns the threshold + rates.

    Dispatches between two implementations based on corpus size:

      * ``n < _SWEEP_THRESHOLD_FAST_DISPATCH_N`` → original O(n × k)
        loop. Bit-exact with the pre-1.67.0 behavior; preserves the
        ``candidates`` log emission on failure for small-corpus
        debugging.
      * ``n >= _SWEEP_THRESHOLD_FAST_DISPATCH_N`` → O(n log n)
        sort-and-scan. Returns the same operating point (TPR /
        FPR / precision within float epsilon); threshold value may
        differ in the last decimal place at tied-score boundaries.
        Candidates log omitted (would be 100K-8M rows of survey-
        JSON bloat at MAGE / RAID scale).

    Empirical speedup at MAGE scale (n = 338K pairs, measured
    2026-05-16): >16,000× (>70 min → <0.3 s).
    """
    n_pos = sum(1 for y, _ in pairs if y == 1)
    n_neg = sum(1 for y, _ in pairs if y == 0)
    if n_pos == 0 or n_neg == 0:
        return {
            "available": False,
            "reason": (
                f"single-class fixture (n_pos={n_pos}, n_neg={n_neg}); "
                f"no operating point"
            ),
        }
    fpr_resolution = 1.0 / n_neg
    if fpr_target < fpr_resolution:
        return {
            "available": False,
            "reason": (
                f"FPR target {fpr_target} is below the corpus's FPR "
                f"resolution {fpr_resolution:.6f} (1/n_neg with n_neg="
                f"{n_neg}). The smallest non-zero FPR is one false "
                f"positive out of {n_neg} negatives. Either raise the "
                f"target, collect more negative-class samples, or "
                f"acknowledge that no threshold can satisfy this target."
            ),
            "n_pos": n_pos,
            "n_neg": n_neg,
            "fpr_resolution": fpr_resolution,
        }

    if len(pairs) < _SWEEP_THRESHOLD_FAST_DISPATCH_N:
        return _sweep_threshold_loop(
            pairs, direction, fpr_target,
            n_pos=n_pos, n_neg=n_neg, fpr_resolution=fpr_resolution,
        )
    return _sweep_threshold_fast(
        pairs, direction, fpr_target,
        n_pos=n_pos, n_neg=n_neg, fpr_resolution=fpr_resolution,
    )


def _fixed_threshold_bootstrap_ci_loop(
    pairs: Sequence[tuple[int, float]],
    threshold: float,
    direction: str,
    *,
    resamples: int,
    confidence: float,
    seed: int | None,
) -> dict[str, Any] | None:
    """Pure-Python loop implementation. Bit-exact with the
    pre-1.60 behavior; used as the reference for cross-engine
    statistical-equivalence testing and as the fallback when
    numpy is unavailable (which on the calibration code path
    shouldn't happen, since sklearn already pulls it in)."""
    if not pairs:
        return None
    rng = random.Random(seed)
    n = len(pairs)
    tprs: list[float] = []
    fprs: list[float] = []
    precs: list[float] = []
    for _ in range(resamples):
        idxs = [rng.randrange(n) for _ in range(n)]
        boot = [pairs[i] for i in idxs]
        if not any(y == 1 for y, _ in boot) or not any(y == 0 for y, _ in boot):
            continue
        tp, fp, tn, fn = _confusion(boot, threshold, direction)
        r = _rates(tp, fp, tn, fn)
        tprs.append(r["tpr"])
        fprs.append(r["fpr"])
        precs.append(r["precision"])
    if not tprs:
        return None
    alpha = 1.0 - confidence
    return {
        "method": "fixed_threshold_paired_bootstrap",
        "engine": "loop",
        "confidence": confidence,
        "resamples": len(tprs),
        "tpr_ci": [_quantile(tprs, alpha / 2), _quantile(tprs, 1 - alpha / 2)],
        "fpr_ci": [_quantile(fprs, alpha / 2), _quantile(fprs, 1 - alpha / 2)],
        "precision_ci": [
            _quantile(precs, alpha / 2),
            _quantile(precs, 1 - alpha / 2),
        ],
        "note": (
            "Pair records are dependent; CI is smoke-test diagnostic, "
            "not calibration-grade. Selection uncertainty (nested "
            "bootstrap on the threshold itself) is roadmap."
        ),
    }


# Per-cell peak-memory rough estimates used by ``_auto_chunk_size``.
# Each (chunk, n) cell in the inner loop costs:
#   - one index entry (int32 for numpy = 4 bytes; int64 for torch = 8)
#   - one ``sampled_cats`` cell (int8 = 1 byte)
#   - approximately four transient boolean masks during the count
#     reductions (4 × 1 byte = 4 bytes)
#   - some small per-row scratch (rates, division masks); rolled in
#     conservatively.
# Total ~ 12 bytes/cell for numpy, ~ 16 bytes/cell for torch. Codex
# review (PR #53) flagged that the original docstring counted only
# the index matrix, which undershoots actual peak by ~3x.
_PER_CELL_BYTES = {"numpy": 12, "torch": 16}

# Target peak-memory budget for the inner loop. 500 MB is a safe
# default for laptops + single-GPU consumer hosts; the operator can
# override via ``--bootstrap-chunk-size`` on the CLI. Picked at the
# crossover where MAGE-scale (n=436K) still gets a chunk near the
# legacy 200 and RAID-scale (n=8.3M) auto-shrinks to ~8 — the
# difference between "fine on a Steam Machine" and "OOM."
_AUTO_CHUNK_TARGET_BYTES = 500_000_000

# Cap on the auto-detected chunk size. Even when ``_PER_CELL_BYTES``
# would allow a huge chunk on a small corpus, no benefit to going
# past 200 (the legacy default) — the per-chunk overhead is dominated
# by the resample itself, not the loop iteration.
_AUTO_CHUNK_MAX = 200
_AUTO_CHUNK_MIN = 1


def _auto_chunk_size(n: int, engine: str = "numpy") -> int:
    """Pick a chunk size that caps the inner-loop peak memory at
    roughly ``_AUTO_CHUNK_TARGET_BYTES`` for a corpus of size ``n``.

    Engine-aware because torch indices are int64 (vs int32 for
    numpy) so the same chunk × n footprint is ~33% larger on the
    torch path. Returns the clamped chunk size; caller never needs
    to second-guess.

    Memory math (per chunk):

      bytes ≈ chunk * n * per_cell_bytes

    where per_cell_bytes accounts for the index tensor, the
    ``sampled_cats`` array, transient boolean masks during the
    count reductions, and small per-row scratch. For numpy at
    n=8.3M and a 500 MB cap, this returns chunk ≈ 5; for n=436K,
    chunk ≈ 96; for n=5K, chunk hits the 200 cap.

    Codex review (PR #53, P1): the original numpy default of 200
    silently consumed ~13 GB at RAID scale once all transients
    were included. This helper makes the budget explicit and
    operator-tunable.
    """
    if n <= 0:
        return _AUTO_CHUNK_MAX
    per_cell = _PER_CELL_BYTES.get(engine, _PER_CELL_BYTES["numpy"])
    raw = _AUTO_CHUNK_TARGET_BYTES // max(1, n * per_cell)
    return max(_AUTO_CHUNK_MIN, min(_AUTO_CHUNK_MAX, int(raw)))


def _fixed_threshold_bootstrap_ci_numpy(
    pairs: Sequence[tuple[int, float]],
    threshold: float,
    direction: str,
    *,
    resamples: int,
    confidence: float,
    seed: int | None,
    chunk_size: int | None = None,
) -> dict[str, Any] | None:
    """NumPy-vectorized equivalent of the loop implementation.

    Same paired-record bootstrap on TPR/FPR/precision at a fixed
    threshold; the speedup comes from three mechanical changes:

    1. **Per-pair categorical pre-classification.** Each input
       pair is one of 4 fixed categories at this threshold:
       ``tp``-eligible / ``fp``-eligible / ``tn``-eligible /
       ``fn``-eligible. Precompute once into an int8 array of
       length n; the per-resample work then reduces to gathering
       indices into this array and counting category values.

    2. **Chunked vectorized resampling.** Each chunk of resamples
       generates a ``(chunk_size, n)`` int32 matrix of resampled
       indices in one ``rng.integers`` call, gathers from the
       category array, and counts categories per row via
       boolean-mask sums. ``chunk_size=None`` (default) auto-sizes
       via ``_auto_chunk_size`` to cap inner-loop peak at ~500 MB
       — at RAID scale (n=8.3M) that's chunk ≈ 5; at MAGE scale
       (n=436K) chunk ≈ 96; at small N (n=5K) chunk hits the 200
       cap. Pass an explicit int to override (e.g. for memory-
       tight hosts or to maximize throughput when memory is
       plentiful). Codex review (PR #53, P1): the legacy fixed
       chunk_size=200 OOM'd at RAID scale once the transient
       boolean masks were counted; the auto-sizing default closes
       that gap.

    3. **Statistically equivalent, not bit-exact.** This is the
       important caveat: ``random.Random.randrange`` and
       ``np.random.default_rng().integers`` produce different
       streams from the same seed, so individual resample
       compositions differ. For 2000+ resamples the CI bounds
       converge to indistinguishable values modulo Monte Carlo
       noise (see ``test_engines_are_statistically_equivalent``).
       Callers needing bit-exact reproducibility against the
       pre-1.60 ledger should pass ``engine="loop"`` explicitly.

    Returns the same dict shape as the loop implementation, with
    one new field: ``engine == "numpy"``. The aggregator and
    survey ledger entries pass this through so threshold
    provenance records which implementation produced the CI.

    Expected speedup over the loop engine: 50-200x on CPU for
    MAGE-scale inputs (436K records, 2000 resamples); the
    factor grows with N because the Python-interpreter overhead
    of the loop scales linearly while the vectorized version
    moves the inner work into C.
    """
    if not pairs:
        return None
    import numpy as np  # type: ignore  # local: numpy isn't strictly required for the loop engine

    n = len(pairs)
    # Resolve chunk size: None → auto-size for n.
    if chunk_size is None:
        chunk_size = _auto_chunk_size(n, engine="numpy")
    else:
        chunk_size = max(1, int(chunk_size))
    # Build label/score arrays in one pass over the input pairs.
    # ``np.fromiter`` avoids the intermediate Python list that
    # ``np.asarray([... for ...])`` would build.
    labels = np.fromiter((p[0] for p in pairs), dtype=np.int8, count=n)
    scores = np.fromiter((p[1] for p in pairs), dtype=np.float64, count=n)

    if direction == "gt":
        predicted_positive = scores > threshold
    elif direction == "lt":
        predicted_positive = scores < threshold
    else:
        raise ValueError(
            f"direction must be 'gt' or 'lt', got {direction!r}"
        )

    # Categorical encoding:
    #   0 = tp (predicted positive, label 1)
    #   1 = fp (predicted positive, label 0)
    #   2 = tn (predicted negative, label 0)
    #   3 = fn (predicted negative, label 1)
    cats = np.empty(n, dtype=np.int8)
    cats[predicted_positive & (labels == 1)] = 0
    cats[predicted_positive & (labels == 0)] = 1
    cats[(~predicted_positive) & (labels == 0)] = 2
    cats[(~predicted_positive) & (labels == 1)] = 3

    rng = np.random.default_rng(seed)
    tprs_chunks: list[np.ndarray] = []
    fprs_chunks: list[np.ndarray] = []
    precs_chunks: list[np.ndarray] = []

    for chunk_start in range(0, resamples, chunk_size):
        chunk = min(chunk_size, resamples - chunk_start)
        # (chunk, n) int32 index matrix. int32 caps n at ~2B,
        # well past any realistic corpus size.
        idxs = rng.integers(0, n, size=(chunk, n), dtype=np.int32)
        # Fancy-index into cats: (chunk, n) int8 of categories.
        sampled_cats = cats[idxs]

        # Per-row counts. ``==`` produces bool, ``.sum(axis=1)``
        # adds along the n dimension. Using uint32 because counts
        # could exceed int16 for large n.
        tp = (sampled_cats == 0).sum(axis=1, dtype=np.uint32)
        fp = (sampled_cats == 1).sum(axis=1, dtype=np.uint32)
        tn = (sampled_cats == 2).sum(axis=1, dtype=np.uint32)
        fn = (sampled_cats == 3).sum(axis=1, dtype=np.uint32)

        # Both-classes-present filter: matches the loop's
        # ``if not any(y == 1 ...) or not any(y == 0 ...)``
        # check, which drops a resample that happened to draw
        # only one class. Bootstrap-CI literature treats this
        # as 'skip; don't substitute'.
        positive_present = (tp + fn) > 0
        negative_present = (tn + fp) > 0
        valid = positive_present & negative_present
        if not valid.any():
            continue

        # Rates. Use float64 division with explicit divide-by-
        # zero handling. ``np.errstate`` silences the warning
        # for invalid rows we're about to mask out anyway.
        tp_f = tp.astype(np.float64)
        fp_f = fp.astype(np.float64)
        tn_f = tn.astype(np.float64)
        fn_f = fn.astype(np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            tpr = np.where(positive_present, tp_f / (tp_f + fn_f), 0.0)
            fpr = np.where(negative_present, fp_f / (fp_f + tn_f), 0.0)
            denom_prec = tp_f + fp_f
            prec = np.where(denom_prec > 0, tp_f / denom_prec, 0.0)

        tprs_chunks.append(tpr[valid])
        fprs_chunks.append(fpr[valid])
        precs_chunks.append(prec[valid])

    if not tprs_chunks:
        return None

    tprs_arr = np.concatenate(tprs_chunks)
    fprs_arr = np.concatenate(fprs_chunks)
    precs_arr = np.concatenate(precs_chunks)

    alpha = 1.0 - confidence
    # Use numpy's quantile (linear interpolation, matches the
    # loop's ``_quantile`` shape).
    def q(arr: np.ndarray, frac: float) -> float:
        return float(np.quantile(arr, frac, method="linear"))

    return {
        "method": "fixed_threshold_paired_bootstrap",
        "engine": "numpy",
        "chunk_size": int(chunk_size),
        "confidence": confidence,
        "resamples": int(tprs_arr.size),
        "tpr_ci": [q(tprs_arr, alpha / 2), q(tprs_arr, 1 - alpha / 2)],
        "fpr_ci": [q(fprs_arr, alpha / 2), q(fprs_arr, 1 - alpha / 2)],
        "precision_ci": [
            q(precs_arr, alpha / 2),
            q(precs_arr, 1 - alpha / 2),
        ],
        "note": (
            "Pair records are dependent; CI is smoke-test diagnostic, "
            "not calibration-grade. Selection uncertainty (nested "
            "bootstrap on the threshold itself) is roadmap."
        ),
    }


def _torch_available() -> bool:
    """True iff ``import torch`` succeeds. Used by the dispatcher
    to give a clear error before reaching the torch engine, and by
    tests to skip cleanly when torch isn't installed."""
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


def _fixed_threshold_bootstrap_ci_torch(
    pairs: Sequence[tuple[int, float]],
    threshold: float,
    direction: str,
    *,
    resamples: int,
    confidence: float,
    seed: int | None,
    chunk_size: int | None = None,
    device: str | None = None,
) -> dict[str, Any] | None:
    """PyTorch equivalent of the numpy engine, GPU-accelerated when
    a CUDA/ROCm device is available.

    The algorithm is identical to the numpy engine: per-pair
    categorical pre-classification (tp/fp/tn/fn) at the threshold,
    then chunked resampling via ``torch.randint`` + fancy-index
    gather + boolean-mask sum + ``torch.quantile``. The advantage
    over numpy is that on a GPU the inner work runs in parallel
    across many more lanes than a CPU SIMD register can offer; on
    a 12-CU consumer GPU (e.g. RX 7900 XT) the speedup over numpy
    is roughly 5-15x for MAGE-scale inputs.

    Statistical equivalence: ``torch.randint`` uses a different RNG
    stream from ``np.random.default_rng`` and ``random.Random``, so
    individual per-resample compositions differ from both other
    engines. CI bounds converge to within Monte Carlo noise (same
    convergence test pin as ``numpy`` vs ``loop``).

    Device selection:

      - ``device="cuda"``: explicit. Errors at the ``torch.tensor``
        call if no CUDA/ROCm device is reachable.
      - ``device="cpu"``: explicit CPU. Useful for cross-platform
        bit-reproducibility tests.
      - ``device=None`` (default): auto. ``torch.cuda.is_available()``
        — note this returns True for both NVIDIA CUDA AND AMD ROCm
        builds, so the same auto-detect works for both vendors.

    The returned dict carries an ``engine`` field, a ``device``
    field, and a ``chunk_size`` field so the ledger records which
    hardware produced the CI and at what memory budget; that closes
    a reproducibility gap that's specific to the GPU path.

    ``chunk_size``: ``None`` (default) auto-sizes via
    ``_auto_chunk_size`` for the torch engine, which budgets ~16
    bytes/cell (int64 indices + int8 cats + transient masks). At
    RAID scale (n=8.3M) that's chunk ≈ 4 instead of the legacy
    fixed 200 (~13 GB index tensor, OOM on consumer GPUs). Codex
    review (PR #56, P1): the fixed 200 default OOM'd on the
    target hardware; the auto-sized default lands inside the 500
    MB budget on any modern GPU and the operator can override via
    ``--bootstrap-chunk-size``.

    Returns ``None`` for an empty input or a degenerate sweep where
    every resample drew a single-class composition. Caller is the
    same as for the numpy engine.
    """
    if not pairs:
        return None
    if not _torch_available():
        raise RuntimeError(
            "torch engine requires PyTorch; install with `pip "
            "install torch` (CPU) or follow the ROCm setup guide "
            "for AMD GPUs / CUDA setup guide for NVIDIA GPUs. The "
            "`numpy` engine is a CPU-only alternative that's still "
            "50-200x faster than the loop engine."
        )
    import torch  # type: ignore

    # Auto-detect device. ``torch.cuda.is_available()`` is True for
    # both CUDA and ROCm builds, so AMD users on a ROCm install hit
    # the GPU path the same way NVIDIA users on a CUDA install do.
    if device is None or device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    torch_device = torch.device(device)

    n = len(pairs)
    # Resolve chunk size: None → auto-size for n + torch's int64
    # indices. See ``_auto_chunk_size`` for the memory math.
    if chunk_size is None:
        chunk_size = _auto_chunk_size(n, engine="torch")
    else:
        chunk_size = max(1, int(chunk_size))
    # Two passes over ``pairs`` to fill independent typed arrays.
    # The list-of-tuples → tensor conversion can't be one-shot
    # because torch.tensor requires a homogeneous source.
    labels = torch.tensor(
        [int(p[0]) for p in pairs],
        dtype=torch.int8, device=torch_device,
    )
    scores = torch.tensor(
        [float(p[1]) for p in pairs],
        dtype=torch.float64, device=torch_device,
    )

    if direction == "gt":
        predicted_positive = scores > threshold
    elif direction == "lt":
        predicted_positive = scores < threshold
    else:
        raise ValueError(
            f"direction must be 'gt' or 'lt', got {direction!r}"
        )

    # Categorical pre-encoding: 0=tp, 1=fp, 2=tn, 3=fn. Same scheme
    # the numpy engine uses; the per-resample work then reduces to
    # ``cats[idx]`` gather + per-row boolean-mask sum.
    cats = torch.empty(n, dtype=torch.int8, device=torch_device)
    label_pos = labels == 1
    label_neg = labels == 0
    cats[predicted_positive & label_pos] = 0
    cats[predicted_positive & label_neg] = 1
    cats[(~predicted_positive) & label_neg] = 2
    cats[(~predicted_positive) & label_pos] = 3

    # Seed the RNG. ``torch.Generator(device=...)`` lets us seed
    # the GPU RNG separately from the global one — important
    # because the global default seed is captured at process start
    # and reusing it across calls produces correlated streams.
    gen = torch.Generator(device=torch_device)
    if seed is not None:
        gen.manual_seed(int(seed))

    tprs_chunks: list = []
    fprs_chunks: list = []
    precs_chunks: list = []

    for chunk_start in range(0, resamples, chunk_size):
        chunk = min(chunk_size, resamples - chunk_start)
        # int32 caps n at ~2B, well past any realistic corpus
        # size. ``torch.randint`` with a per-device generator
        # gives reproducible chunk-level streams.
        idxs = torch.randint(
            0, n, (chunk, n),
            dtype=torch.int64,  # torch indexing wants int64
            device=torch_device, generator=gen,
        )
        sampled_cats = cats[idxs]  # (chunk, n) int8

        tp = (sampled_cats == 0).sum(dim=1)
        fp = (sampled_cats == 1).sum(dim=1)
        tn = (sampled_cats == 2).sum(dim=1)
        fn = (sampled_cats == 3).sum(dim=1)

        positive_present = (tp + fn) > 0
        negative_present = (tn + fp) > 0
        valid = positive_present & negative_present
        if not bool(valid.any().item()):
            continue

        tp_f = tp.to(torch.float64)
        fp_f = fp.to(torch.float64)
        tn_f = tn.to(torch.float64)
        fn_f = fn.to(torch.float64)
        tpr = torch.where(
            positive_present, tp_f / (tp_f + fn_f),
            torch.zeros_like(tp_f),
        )
        fpr = torch.where(
            negative_present, fp_f / (fp_f + tn_f),
            torch.zeros_like(fp_f),
        )
        denom_prec = tp_f + fp_f
        prec = torch.where(
            denom_prec > 0, tp_f / denom_prec,
            torch.zeros_like(tp_f),
        )

        tprs_chunks.append(tpr[valid])
        fprs_chunks.append(fpr[valid])
        precs_chunks.append(prec[valid])

    if not tprs_chunks:
        return None

    tprs_arr = torch.cat(tprs_chunks)
    fprs_arr = torch.cat(fprs_chunks)
    precs_arr = torch.cat(precs_chunks)

    alpha = 1.0 - confidence

    def q(t, frac: float) -> float:
        # ``torch.quantile`` interpolation defaults to linear,
        # matching numpy's ``method='linear'`` and the loop's
        # ``_quantile`` shape. Move to CPU for the float
        # extraction since item() requires a host scalar.
        return float(torch.quantile(t, frac).cpu().item())

    return {
        "method": "fixed_threshold_paired_bootstrap",
        "engine": "torch",
        "device": str(torch_device),
        "chunk_size": int(chunk_size),
        "confidence": confidence,
        "resamples": int(tprs_arr.numel()),
        "tpr_ci": [q(tprs_arr, alpha / 2), q(tprs_arr, 1 - alpha / 2)],
        "fpr_ci": [q(fprs_arr, alpha / 2), q(fprs_arr, 1 - alpha / 2)],
        "precision_ci": [
            q(precs_arr, alpha / 2),
            q(precs_arr, 1 - alpha / 2),
        ],
        "note": (
            "Pair records are dependent; CI is smoke-test diagnostic, "
            "not calibration-grade. Selection uncertainty (nested "
            "bootstrap on the threshold itself) is roadmap."
        ),
    }


# Backward-compatibility dispatcher. Public callers (the survey
# aggregator, derive_threshold_from_records, end-user scripts)
# call this function; it routes to the loop, numpy, or torch
# implementation based on the ``engine`` argument. Default is
# ``"loop"`` so unchanged invocations produce byte-identical CI
# values against the pre-1.60 ledger. Pass ``engine="numpy"`` for
# the 50-200x CPU speedup on N >= ~100K-row corpora, or
# ``engine="torch"`` for an additional 5-15x speedup on a
# CUDA/ROCm GPU.
def fixed_threshold_bootstrap_ci(
    pairs: Sequence[tuple[int, float]],
    threshold: float,
    direction: str,
    *,
    resamples: int,
    confidence: float,
    seed: int | None,
    engine: str = "loop",
    chunk_size: int | None = None,
    device: str | None = None,
) -> dict[str, Any] | None:
    """Paired-record bootstrap on TPR / FPR / precision at a fixed
    threshold. Resampling pair indices with replacement; each resample
    recomputes the rates at the same threshold.

    ``engine`` selects the implementation:

    - ``"loop"`` (default): pure-Python implementation that's
      bit-exact with the pre-1.60 behavior. The right choice
      for small corpora (where bootstrap cost is irrelevant)
      and for callers that need reproducibility against
      previously-published ledger entries.

    - ``"numpy"``: NumPy-vectorized implementation. 50-200x
      faster on N >= ~100K-row corpora. Statistically
      equivalent to ``"loop"`` for 2000+ resamples; CI bounds
      converge to within Monte Carlo noise. Different per-
      resample compositions because the RNG stream differs
      (``np.random.default_rng`` vs ``random.Random``).

    - ``"torch"``: PyTorch implementation that auto-detects a
      CUDA or ROCm GPU and runs the inner gather/count/quantile
      on-device. An additional 5-15x speedup over the ``numpy``
      engine on a 12-CU consumer GPU; falls back to CPU if no
      GPU is reachable. Requires ``pip install torch``. The
      ``device`` kwarg overrides auto-detection (``"cpu"``,
      ``"cuda"``, or a specific device string like
      ``"cuda:1"``).

    ``chunk_size`` overrides the auto-detected inner-loop chunk
    size for the vectorized engines. ``None`` (default) auto-sizes
    via ``_auto_chunk_size`` to cap inner-loop peak memory at
    ~500 MB. Ignored by the ``loop`` engine. Operator-tunable via
    ``--bootstrap-chunk-size`` on both CLIs.

    ``device`` is only consulted for ``engine="torch"``.
    """
    if engine == "loop":
        return _fixed_threshold_bootstrap_ci_loop(
            pairs, threshold, direction,
            resamples=resamples, confidence=confidence, seed=seed,
        )
    if engine == "numpy":
        return _fixed_threshold_bootstrap_ci_numpy(
            pairs, threshold, direction,
            resamples=resamples, confidence=confidence, seed=seed,
            chunk_size=chunk_size,
        )
    if engine == "torch":
        return _fixed_threshold_bootstrap_ci_torch(
            pairs, threshold, direction,
            resamples=resamples, confidence=confidence, seed=seed,
            device=device,
            chunk_size=chunk_size,
        )
    raise ValueError(
        f"Unknown bootstrap engine {engine!r}. "
        f"Known: 'loop', 'numpy', 'torch'."
    )


def _ranking_metrics(
    pairs: Sequence[tuple[int, float]],
    *,
    direction: str = "gt",
) -> dict[str, float | None]:
    """Compute AUC + AP, raw and direction-aware.

    Both AUC and AP convention assume "higher score = more positive."
    For ``lt``-direction signals (registry says compressed when score
    < threshold), the *negated* score should be the positive-class
    indicator. Computing AP on raw scores for an ``lt`` signal makes
    a good discriminator look weak: if AI essays cluster at low
    burstiness and human essays at high, ranking by score pushes
    humans to the top of the list, which inverts the precision
    curve.

    Returns four fields:

      - ``auc`` (raw): polarity-blind, on a 0..1 scale where 0.5 =
        chance. Intentionally direction-blind for parity with the
        gate-1 polarity check.
      - ``ap`` (raw): the same polarity-blind shape.
      - ``direction_aware_auc``: ``auc`` for ``gt`` signals,
        ``1 - auc`` for ``lt``. Reads on a consistent "≥ 0.5 =
        polarity matches" scale.
      - ``direction_aware_ap``: AP computed with negated scores for
        ``lt`` signals. Reads on a consistent "higher = stronger
        discrimination given the registry's hypothesis" scale.

    Mirrors ``validation_harness.fallback_roc_auc`` /
    ``fallback_average_precision`` behavior when sklearn isn't
    available.
    """
    labels = [p[0] for p in pairs]
    raw_scores = [p[1] for p in pairs]
    da_scores = (
        [-s for s in raw_scores] if direction == "lt" else list(raw_scores)
    )
    try:
        from sklearn.metrics import (  # type: ignore
            average_precision_score,
            roc_auc_score,
        )
        raw_auc = float(roc_auc_score(labels, raw_scores))
        raw_ap = float(average_precision_score(labels, raw_scores))
        da_ap = float(average_precision_score(labels, da_scores))
    except Exception:
        from validation_harness import (  # type: ignore
            fallback_average_precision,
            fallback_roc_auc,
        )
        raw_auc = fallback_roc_auc(labels, raw_scores)
        raw_ap = fallback_average_precision(labels, raw_scores)
        da_ap = fallback_average_precision(labels, da_scores)

    if raw_auc is None:
        da_auc: float | None = None
    elif direction == "lt":
        da_auc = 1.0 - raw_auc
    else:
        da_auc = raw_auc

    return {
        "auc": raw_auc,
        "ap": raw_ap,
        "direction_aware_auc": da_auc,
        "direction_aware_ap": da_ap,
    }


def _build_harness_command(
    *,
    manifest_path: Path,
    use: str,
    signal: str,
    fpr_target: float,
    engine: str = "loop",
    chunk_size: int | None = None,
    device: str | None = None,
) -> str:
    """Compose the replay command stamped into the ledger entry.

    Codex review (PR #53/#56, P1): the original harness_command
    omitted ``--bootstrap-engine`` and ``--bootstrap-chunk-size``,
    so a threshold derived with the numpy or torch engine would
    silently replay on the loop engine, defeating the point of
    persisting the CI provenance. We surface every non-default
    bootstrap flag the user (or auto-detect) selected.

    Codex review (PR #53, P2): every interpolated value is shell-
    quoted via ``shlex.quote``. The runtime workspace lives under
    ``Claude Cowork Working Folder`` whose path contains a space,
    so unquoted interpolation breaks copy-paste replay on the
    operator's primary machine. ``shlex.quote`` is a no-op on
    shell-safe tokens (``--use validation`` stays bare) and wraps
    anything containing whitespace or shell metacharacters.

    Defaults are not emitted: ``engine="loop"`` is the historical
    behavior so omitting it is loud; ``chunk_size=None`` means
    auto-sized and replaying with the same n auto-sizes the same
    way; ``device=None`` means auto-detect and is only relevant
    for ``engine="torch"``.
    """
    def q(value: Any) -> str:
        """Shell-quote a value's string form. Numbers, simple
        identifiers, and POSIX-safe paths come through bare;
        anything with whitespace or shell metacharacters gets
        wrapped in single quotes."""
        return shlex.quote(str(value))

    parts = [
        "python3 scripts/calibration/calibrate_thresholds.py",
        f"--manifest {q(manifest_path)}",
        f"--use {q(use)}",
        f"--signal {q(signal)}",
        f"--fpr-target {q(fpr_target)}",
    ]
    if engine != "loop":
        parts.append(f"--bootstrap-engine {q(engine)}")
    if chunk_size is not None:
        parts.append(f"--bootstrap-chunk-size {q(chunk_size)}")
    if engine == "torch" and device is not None:
        parts.append(f"--bootstrap-device {q(device)}")
    return " ".join(parts)


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _load_fetch_record(manifest_path: Path) -> dict[str, Any]:
    """Walk up from the manifest looking for a `.fetch_record.json`
    that fetch_pangram_editlens.py wrote."""
    cur = manifest_path.resolve().parent
    while cur != cur.parent:
        record = cur / ".fetch_record.json"
        if record.is_file():
            try:
                return json.loads(record.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        cur = cur.parent
    return {}


def _stratified_subsample(
    entries: list[dict[str, Any]],
    *,
    cap: int,
    seed: int,
) -> tuple[list[dict[str, Any]], int, int]:
    """Label-stratified sub-sample; returns ``(sampled, n_pos, n_neg)``.

    Pulled out so both `score_corpus` and the cache-load path can apply
    sub-sampling consistently. Proportional to class size with a floor
    of 1 per non-empty class so the threshold sweep always has both
    labels present.
    """
    positive_statuses = set(DEFAULT_POSITIVE_STATUSES)
    negative_statuses = set(DEFAULT_NEGATIVE_STATUSES)
    positives = [e for e in entries if e.get("ai_status") in positive_statuses]
    negatives = [e for e in entries if e.get("ai_status") in negative_statuses]
    total = len(positives) + len(negatives)
    if total > 0:
        n_pos_target = max(
            1 if positives else 0,
            int(round(cap * len(positives) / total)),
        )
        n_neg_target = max(0, cap - n_pos_target)
        if n_neg_target == 0 and negatives:
            n_neg_target = 1
            n_pos_target = max(1, cap - 1)
    else:
        n_pos_target = n_neg_target = 0

    import random
    rng = random.Random(int(seed))
    if positives:
        rng.shuffle(positives)
    if negatives:
        rng.shuffle(negatives)
    sampled = positives[:n_pos_target] + negatives[:n_neg_target]
    rng.shuffle(sampled)
    return sampled, n_pos_target, n_neg_target


def _manifest_content_hash(manifest_path: Path) -> str:
    """SHA-256 of the manifest file content. Used as the cache
    invalidation key — if the user edits the manifest, the cache
    invalidates."""
    h = hashlib.sha256()
    with manifest_path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _corpus_text_fingerprint(
    entries: Sequence[dict[str, Any]],
) -> str:
    """SHA-256 over a canonical (resolved_path, text_sha256) listing.

    The manifest hash alone is not sufficient as a cache key: the
    manifest JSONL can stay byte-identical while the underlying text
    files it points to are regenerated (re-OCR, re-extraction,
    cleanup pass, preprocessing toggle change) — at which point the
    cached scored records are stale but ``cache_is_compatible``
    would still report compatible.

    This fingerprint hashes the actual bytes of every entry's
    resolved-path text plus the resolved path itself, in a
    deterministic order, so any change to any file the manifest
    references invalidates the cache. Entries whose
    ``_resolved_path`` is missing or unreadable contribute a sentinel
    so the fingerprint still differs between "file-present" and
    "file-missing" runs.
    """
    rows: list[tuple[str, str]] = []
    for entry in entries:
        resolved = entry.get("_resolved_path") or ""
        if not resolved:
            rows.append((str(entry.get("id") or ""), "no-resolved-path"))
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
        outer.update(path_str.encode("utf-8", errors="ignore"))
        outer.update(b"\x00")
        outer.update(text_hash.encode("ascii"))
        outer.update(b"\n")
    return f"sha256:{outer.hexdigest()}"


def _resolve_surprisal_dtype_label(args: argparse.Namespace) -> str | None:
    """Resolve ``--surprisal-dtype`` to the canonical loaded label.

    Returns ``"fp32"`` / ``"fp16"`` / ``"bf16"`` based on the
    current host's hardware probe. Returns ``None`` when torch isn't
    importable or when resolution otherwise fails — callers should
    only consult the result when Tier 4 is on (and therefore
    surprisal_backend must be importable for scoring to work at all).

    Used for cache identity: ``"auto"`` resolves to bf16 on H100 and
    fp32 on CPU, so storing the literal request string in cache_meta
    lets caches silently cross hardware with different actual
    precision. Recording the resolved label closes that gap.
    """
    try:
        from surprisal_backend import _resolve_dtype  # type: ignore
    except ImportError:
        return None
    try:
        _, label = _resolve_dtype(
            getattr(args, "surprisal_dtype", "auto"),
        )
    except Exception:  # noqa: BLE001
        return None
    return label


def _entry_id_for_record(entry: dict[str, Any]) -> str:
    """Match the synthesized id ``score_smoothing_entry`` writes onto
    every record. Used by the incremental-scoring resume path to skip
    entries whose records already exist in the partial cache.

    Keep in sync with the entry-id construction in
    ``validation_harness.score_smoothing_entry`` (~line 169 as of
    2026-05-16). Both must produce identical IDs from the same entry
    dict or resume will silently re-score everything.
    """
    e_id = entry.get("id")
    if isinstance(e_id, str):
        return e_id
    return f"line_{entry.get('_lineno', '?')}"


def _save_score_cache(
    path: Path,
    scoring_meta: dict[str, Any],
    records: list[dict[str, Any]],
    status: str,
) -> None:
    """Atomic write of the records cache. ``status`` is ``"in_progress"``
    during incremental flushes and ``"complete"`` on the final write
    after the scoring loop exits cleanly.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "scoring_meta": scoring_meta,
        "records": records,
        "status": status,
    }
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
        fh.write("\n")
    tmp.replace(path)


def score_corpus(
    args: argparse.Namespace,
    *,
    partial_cache_path: Path | None = None,
    flush_every: int = 100,
    refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Score every (filtered, optionally sub-sampled) manifest entry.

    Pure scoring — no per-signal logic. Called once per calibration
    run; the resulting record list carries every signal's score as a
    field, so per-signal threshold sweeps later just read out the
    relevant column.

    Incremental cache write + resume (1.69.0+): when
    ``partial_cache_path`` is provided, the function writes the cache
    atomically every ``flush_every`` entries with ``status:
    "in_progress"``, so a crash mid-scoring loses at most
    ``flush_every`` rows of work. On entry, if the partial cache
    exists with parseable ``status: "in_progress"`` AND compatible
    ``scoring_meta``, the function loads the prior records, builds
    the set of already-scored entry IDs, and skips those entries
    during the scoring loop. The caller (``load_or_score_corpus``)
    flips status to ``"complete"`` after the final return.

    Returns ``(records, scoring_meta)``. ``scoring_meta`` carries
    the inputs that determine cache validity:

      - ``manifest_path``, ``manifest_sha256`` — corpus identity.
      - ``use`` — the manifest filter.
      - ``do_tier2``, ``do_tier3`` — which signal columns are
        populated.
      - ``sub_sample`` — None for full-corpus runs; a dict
        ``{applied, n_used, n_full, fraction, seed}`` for sub-sampled
        runs (the user-visible PIPELINE CHECK marker propagates from
        here through the cache to the provenance entry).
      - ``scored_at`` — ISO timestamp.
    """
    manifest_path = Path(args.manifest)
    validation = validate_manifest(str(manifest_path))
    if validation["n_errors"] > 0:
        raise SystemExit(
            f"Manifest validation failed with {validation['n_errors']} "
            f"error(s). Aborting."
        )

    entries = [
        e for e in load_manifest_entries(manifest_path)
        if _entry_uses(e, args.use) and not _entry_uses(e, "exclude")
    ]
    if not entries:
        raise SystemExit(
            f"No entries with use={args.use!r} in {manifest_path}."
        )

    full_entry_count = len(entries)
    sub_sample_meta: dict[str, Any] | None = None
    max_entries = getattr(args, "max_entries", None)
    if max_entries is not None and max_entries > 0 and max_entries < full_entry_count:
        rng_seed = getattr(args, "max_entries_seed", None)
        if rng_seed is None:
            rng_seed = getattr(args, "bootstrap_seed", 42) or 42
        rng_seed = int(rng_seed)
        sampled, n_pos, n_neg = _stratified_subsample(
            entries, cap=int(max_entries), seed=rng_seed,
        )
        sys.stdout.write(
            f"Sub-sampling: {len(sampled)} of {full_entry_count} entries "
            f"({n_pos} pos + {n_neg} neg, seed={rng_seed}). "
            "This is a PIPELINE CHECK, not a calibration — "
            "small-N gates won't pass meaningfully.\n"
        )
        entries = sampled
        sub_sample_meta = {
            "applied": True,
            "n_used": len(sampled),
            "n_full": full_entry_count,
            "fraction": round(len(sampled) / max(full_entry_count, 1), 4),
            "seed": rng_seed,
        }

    # ----- Resume from partial cache (1.69.0+).
    # Pre-populate records + scored_ids from the partial cache if it
    # exists with status="in_progress" and compatible scoring_meta.
    # Compatibility is checked the same way load_or_score_corpus does
    # for the complete-cache hit path — same manifest hash + same
    # tier flags + same use filter.
    records: list[dict[str, Any]] = []
    scored_ids: set[str] = set()
    resumed_count = 0
    if refresh and partial_cache_path is not None and partial_cache_path.exists():
        # ``--refresh-cache`` semantics: the operator explicitly asked
        # for a fresh score pass. Do not read the partial cache (skip
        # resume) and unlink the on-disk file so the very next flush
        # writes a clean cache rather than appending the new pass to
        # the stale ``records`` array. Without the unlink we'd keep
        # status="complete" honest only after the run finished — a
        # crash mid-refresh would leave a partial cache mixing the
        # discarded prior run's first N records with the new pass's
        # first M-N. Codex P2 on PR #68.
        try:
            partial_cache_path.unlink()
            sys.stdout.write(
                f"--refresh-cache: discarded prior partial cache at "
                f"{partial_cache_path}; re-scoring from scratch.\n"
            )
        except OSError as exc:
            sys.stdout.write(
                f"--refresh-cache: could not remove prior partial "
                f"cache at {partial_cache_path} ({exc}); proceeding "
                f"without resume (cache will be overwritten on the "
                f"first flush).\n"
            )
    if (
        not refresh
        and partial_cache_path is not None
        and partial_cache_path.exists()
    ):
        try:
            cached = json.loads(
                partial_cache_path.read_text(encoding="utf-8")
            )
            cache_status = cached.get("status", "complete")
            cache_meta = cached.get("scoring_meta") or {}
            fresh_hash = _manifest_content_hash(manifest_path)
            try:
                fresh_text_fp: str | None = _corpus_text_fingerprint(entries)
            except Exception:  # noqa: BLE001
                fresh_text_fp = None
            ok, reason = cache_is_compatible(
                cache_meta, args,
                manifest_sha256=fresh_hash,
                corpus_text_fingerprint=fresh_text_fp,
            )
            if ok and cache_status == "in_progress":
                records = cached.get("records") or []
                scored_ids = {
                    r.get("id") for r in records
                    if isinstance(r.get("id"), str)
                }
                resumed_count = len(records)
                sys.stdout.write(
                    f"Resuming corpus scoring: {resumed_count} of "
                    f"{len(entries)} entries already scored in "
                    f"partial cache at {partial_cache_path}.\n"
                )
            elif not ok and cache_status == "in_progress":
                sys.stdout.write(
                    f"Partial cache at {partial_cache_path} is "
                    f"incompatible ({reason}); discarding and re-"
                    f"scoring from scratch.\n"
                )
        except (json.JSONDecodeError, OSError) as exc:
            sys.stdout.write(
                f"Partial cache at {partial_cache_path} is "
                f"unreadable ({exc}); discarding and re-scoring.\n"
            )

    to_score = [
        e for e in entries
        if _entry_id_for_record(e) not in scored_ids
    ]
    sys.stdout.write(
        f"Scoring {len(to_score)} entries via variance audit "
        f"(of {len(entries)} total; {resumed_count} resumed). "
        f"This can take a while if Tier 2/3 are enabled.\n"
    )
    positive_statuses = set(DEFAULT_POSITIVE_STATUSES)
    negative_statuses = set(DEFAULT_NEGATIVE_STATUSES)
    # 1.90.0+ batched Tier-4 surprisal scoring. When ``--tier4`` is
    # set AND a SurprisalBackend can be imported, pre-compute the
    # per-token surprisal series for chunks of ``surprisal_batch_size``
    # entries via the backend's batched ``score_texts`` path. The
    # rolling cache below holds (series, text) tuples keyed by entry
    # id; the per-entry loop body checks the cache before calling
    # ``score_smoothing_entry`` and, on a hit, supplies the cached
    # text and a precomputed-scorer closure so ``audit_text``'s Tier 4
    # block skips the per-row backend call. On a miss (Tier 4 off,
    # backend unimportable, or a batched-scoring exception), the loop
    # falls back to the legacy per-entry path bit-exactly — no
    # behavior change for the no-Tier-4 majority of callers.
    do_tier4 = bool(getattr(args, "tier4", False))
    surprisal_batch_size = int(
        getattr(args, "surprisal_batch_size", 8) or 8
    )
    batched_surprisal_backend: Any = None
    if do_tier4:
        try:
            from surprisal_backend import (  # type: ignore
                SurprisalBackend, resolve_model_arg,
            )
            batched_surprisal_backend = SurprisalBackend(
                model_id=resolve_model_arg(
                    getattr(args, "surprisal_model", None),
                ),
                revision=getattr(args, "surprisal_revision", None),
                dtype=getattr(args, "surprisal_dtype", "auto"),
            )
        except ImportError:
            batched_surprisal_backend = None

    surprisal_cache: dict[str, tuple[list[float], str]] = {}
    # 1.90.0+ batched-Tier-4 failure latch: a single batched
    # forward-pass failure (OOM, driver mismatch, model crash on a
    # pathological input) used to retry one batch per remaining row
    # with overlapping windows, producing O(N) failed forward passes
    # and matching log-spam for the rest of the run. The latch flips
    # to True on the first batch-level failure and disables batched
    # mode wholesale; the per-entry loop falls through to the legacy
    # path bit-exactly. Operators who hit this can re-run with
    # ``--surprisal-batch-size 1`` (which already routes around the
    # batched path) or with a smaller batch size after the bad chunk
    # is identified from the warning message.
    batched_surprisal_disabled = False

    def _read_entry_text(entry: dict[str, Any]) -> str | None:
        p = Path(str(
            entry.get("_resolved_path") or entry.get("path") or ""
        ))
        try:
            return p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

    def _make_precomputed_scorer(series: list[float]):
        # The closure ignores the ``text`` argument because the
        # batched-scoring pre-pass has already produced the series
        # against the same on-disk file. ``audit_surprisal``'s
        # length-mismatch sanity check inside its own pipeline will
        # surface any drift between the pre-read and the audit-time
        # tokenization as an ``available=False`` return — same shape
        # as a real backend failure.
        def scorer(_text: str, return_top_k: int = 0):
            if return_top_k > 0:
                # No top-k diagnostic in batched mode; the per-token
                # top-k surfaces only in the standalone audit, not in
                # the calibration sweep (which reads only the
                # aggregate statistics).
                return series, []
            return series
        return scorer

    def _refill_surprisal_cache(start_index: int) -> None:
        nonlocal batched_surprisal_disabled
        if (
            not do_tier4
            or batched_surprisal_backend is None
            or batched_surprisal_disabled
            or start_index >= len(to_score)
        ):
            return
        chunk = to_score[start_index:start_index + surprisal_batch_size]
        chunk_texts: list[str] = []
        chunk_ids: list[str] = []
        for ce in chunk:
            t = _read_entry_text(ce)
            chunk_texts.append(t if t is not None else "")
            chunk_ids.append(_entry_id_for_record(ce))
        try:
            series_list = batched_surprisal_backend.score_texts(
                chunk_texts, batch_size=surprisal_batch_size,
            )
        except Exception as exc:  # noqa: BLE001
            # Batched scoring failed for this chunk. Disable
            # batched mode wholesale rather than retrying overlap-
            # ping batches per row; one OOM / driver / model crash
            # is enough evidence that the batched path won't
            # recover during this run. The remaining rows go
            # through the legacy per-entry Tier-4 path (which
            # constructs a per-row backend), and audit_text's own
            # error handling surfaces the failure cleanly per row.
            # Operators who hit this should re-run with a smaller
            # ``--surprisal-batch-size`` (or 1 to bypass batching
            # entirely).
            batched_surprisal_disabled = True
            sys.stderr.write(
                f"  WARNING: batched surprisal scoring failed for "
                f"chunk starting at index {start_index} "
                f"({type(exc).__name__}: {exc}). Disabling batched "
                f"Tier-4 for the remainder of this run; the "
                f"per-entry scoring path will handle remaining "
                f"rows. Re-run with a smaller --surprisal-batch-"
                f"size (or 1) if you need the batched throughput.\n"
            )
            return
        for cid, series, txt in zip(chunk_ids, series_list, chunk_texts):
            # Don't cache empty-text entries — let the per-entry
            # path's own file-read error handling surface those.
            if txt:
                surprisal_cache[cid] = (series, txt)

    score_t0 = _dt.datetime.now()
    for i, e in enumerate(to_score):
        if i > 0 and i % flush_every == 0:
            # Progress log + atomic partial-cache flush.
            elapsed = (_dt.datetime.now() - score_t0).total_seconds()
            rate = i / max(elapsed, 1e-9)
            remaining = len(to_score) - i
            eta_s = remaining / max(rate, 1e-9)
            sys.stdout.write(
                f"  scored {i}/{len(to_score)} "
                f"({rate:.1f}/s, ETA {eta_s/60:.1f} min) "
                f"-> flushing partial cache...\n"
            )
            if partial_cache_path is not None:
                interim_meta = {
                    "manifest_path": str(manifest_path),
                    "manifest_sha256": _manifest_content_hash(manifest_path),
                    "corpus_text_fingerprint": _corpus_text_fingerprint(
                        entries,
                    ),
                    "use": args.use,
                    "do_tier2": bool(args.tier2),
                    "do_tier3": bool(args.tier3),
                    # Codex P2 on PR #77: must match the keys emitted
                    # by the final ``scoring_meta`` write below or
                    # ``cache_is_compatible`` will refuse the partial
                    # cache on resume and re-score from scratch — the
                    # opposite of the 1.79.0 incremental-resume
                    # contract. This bites exactly the expensive bake-
                    # off paths this PR enables (--tier4 +
                    # --surprisal-model + --embedding-model runs are
                    # the longest-running scoring loops the framework
                    # supports). Same five fields as scoring_meta;
                    # mirrored via getattr-with-default so the partial
                    # cache from a pre-1.80 run (no tier4/model args
                    # on the Namespace) still serializes legal None /
                    # False values.
                    "do_tier4": bool(getattr(args, "tier4", False)),
                    "embedding_model": getattr(
                        args, "embedding_model", None,
                    ),
                    "embedding_revision": getattr(
                        args, "embedding_revision", None,
                    ),
                    "surprisal_model": getattr(
                        args, "surprisal_model", None,
                    ),
                    "surprisal_revision": getattr(
                        args, "surprisal_revision", None,
                    ),
                    # 1.93.0+: dtype is part of the cache identity for
                    # Tier-4 scoring. Surprisal values produced under
                    # bf16 / fp16 / fp32 differ at the ~0.1 bit/token
                    # level (within signal-stat noise but visible in
                    # the per-token series). Two fields: ``_requested``
                    # is what the operator passed (possibly ``"auto"``)
                    # and ``_resolved`` is what _resolve_dtype on this
                    # host actually picked (``"fp32"`` / ``"fp16"`` /
                    # ``"bf16"`` — never ``"auto"``). Compat-check
                    # compares the resolved label so an ``auto`` cache
                    # scored on CPU (fp32) doesn't silently reuse on a
                    # later H100 invocation (bf16).
                    "surprisal_dtype_requested": getattr(
                        args, "surprisal_dtype", "auto",
                    ),
                    "surprisal_dtype_resolved": (
                        _resolve_surprisal_dtype_label(args)
                        if bool(getattr(args, "tier4", False))
                        else None
                    ),
                    "n_entries_full": full_entry_count,
                    "n_entries_scored": len(records),
                    "sub_sample": sub_sample_meta,
                    "scored_at": _dt.datetime.now(
                        _dt.timezone.utc,
                    ).isoformat(),
                    "scorer_version": SCORER_CACHE_VERSION,
                }
                try:
                    _save_score_cache(
                        partial_cache_path, interim_meta, records,
                        status="in_progress",
                    )
                except Exception as exc:  # noqa: BLE001
                    sys.stderr.write(
                        f"  WARNING: partial-cache flush to "
                        f"{partial_cache_path} failed: "
                        f"{type(exc).__name__}: {exc}. Continuing.\n"
                    )
        # 1.90.0+: refill the surprisal cache on demand. The cache is
        # rolling — we hold at most ``surprisal_batch_size`` entries
        # at a time, so memory stays bounded even on RAID-scale
        # corpora (~30 GB if we tried to pre-compute everything).
        entry_id_for_lookup = _entry_id_for_record(e)
        if (
            do_tier4
            and batched_surprisal_backend is not None
            and not batched_surprisal_disabled
            and entry_id_for_lookup not in surprisal_cache
        ):
            _refill_surprisal_cache(i)
        precomputed_text: str | None = None
        precomputed_tier4_score_fn = None
        if entry_id_for_lookup in surprisal_cache:
            series, precomputed_text = surprisal_cache.pop(
                entry_id_for_lookup,
            )
            precomputed_tier4_score_fn = _make_precomputed_scorer(series)
        records.append(
            score_smoothing_entry(
                e,
                positive_statuses=positive_statuses,
                negative_statuses=negative_statuses,
                do_tier2=args.tier2,
                do_tier3=args.tier3,
                # 1.80.0+: optional Tier 4 + pluggable Tier 3 embedding
                # model. Defaults preserve pre-1.80 behavior.
                do_tier4=bool(getattr(args, "tier4", False)),
                embedding_model=getattr(args, "embedding_model", None),
                embedding_revision=getattr(args, "embedding_revision", None),
                # 1.96.0+: dtype + device passthrough on the
                # embedding side. Mirrors the surprisal-side
                # passthrough below.
                embedding_dtype=getattr(args, "embedding_dtype", "auto"),
                embedding_device=getattr(args, "embedding_device", None),
                surprisal_model=getattr(args, "surprisal_model", None),
                surprisal_revision=getattr(args, "surprisal_revision", None),
                # 1.93.0+: dtype passthrough for the per-entry Tier-4
                # fallback path (when the batched backend is None or
                # the failure-latch has tripped). Without this, falls
                # back to the SurprisalBackend default of ``auto``
                # regardless of operator intent.
                surprisal_dtype=getattr(args, "surprisal_dtype", "auto"),
                # 1.90.0+: batched-Tier-4 wiring. ``text`` is None on
                # a cache miss (per-entry path reads from disk as
                # before); on a hit, the cached text + precomputed
                # score_fn route audit_text away from the per-row
                # backend call entirely.
                text=precomputed_text,
                tier4_score_fn=precomputed_tier4_score_fn,
            )
        )

    scoring_meta = {
        "manifest_path": str(manifest_path),
        "manifest_sha256": _manifest_content_hash(manifest_path),
        "corpus_text_fingerprint": _corpus_text_fingerprint(entries),
        "use": args.use,
        "do_tier2": bool(args.tier2),
        "do_tier3": bool(args.tier3),
        # 1.80.0+: mirror the new fields in scoring_meta so the cache
        # compat check refuses caches that were scored under different
        # tier4 / model choices. See ``cache_is_compatible``.
        "do_tier4": bool(getattr(args, "tier4", False)),
        "embedding_model": getattr(args, "embedding_model", None),
        "embedding_revision": getattr(args, "embedding_revision", None),
        "surprisal_model": getattr(args, "surprisal_model", None),
        "surprisal_revision": getattr(args, "surprisal_revision", None),
        # 1.93.0+: dtype identity for Tier-4 cache reuse. See the
        # matching fields in ``interim_meta`` above for rationale.
        "surprisal_dtype_requested": getattr(
            args, "surprisal_dtype", "auto",
        ),
        "surprisal_dtype_resolved": (
            _resolve_surprisal_dtype_label(args)
            if bool(getattr(args, "tier4", False))
            else None
        ),
        "n_entries_full": full_entry_count,
        "n_entries_scored": len(records),
        "sub_sample": sub_sample_meta,
        "scored_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "scorer_version": SCORER_CACHE_VERSION,
    }
    return records, scoring_meta


def cache_is_compatible(
    cache_meta: dict[str, Any],
    args: argparse.Namespace,
    *,
    manifest_sha256: str,
    corpus_text_fingerprint: str | None = None,
) -> tuple[bool, str]:
    """Decide whether a loaded cache can be reused for the current
    args. Returns ``(ok, reason_if_not)``.

    Cache invalidates on:

      - manifest content change (``manifest_sha256`` mismatch)
      - **corpus text content change** (``corpus_text_fingerprint``
        mismatch) — catches the case where the manifest stays
        byte-identical but a referenced text file was regenerated,
        re-OCR'd, cleaned, or had its preprocessing rerun. Without
        this check, ``load_or_score_corpus`` would return stale
        cached scored records from old text. When the caller
        passes ``None`` (the legacy contract) the check is skipped
        for backward compat with pre-1.29.1 caches and tests.
      - ``use`` filter change (different entry set)
      - ``tier2`` or ``tier3`` change (different signal columns
        available — a cache scored with ``do_tier2=False`` doesn't
        carry POS-bigram KL values)
      - sub-sample change (a partial cache can't satisfy a full run
        and a full cache can satisfy a partial run, but to keep the
        rule simple we invalidate on any sub-sample mismatch)
      - ``scorer_version`` change (the cache key bumps when the
        scoring code changes shape)
    """
    if cache_meta.get("manifest_sha256") != manifest_sha256:
        return False, "manifest content changed"
    if corpus_text_fingerprint is not None:
        cached_fp = cache_meta.get("corpus_text_fingerprint")
        # Legacy caches (pre-1.29.1) don't carry the fingerprint;
        # treat that as "unknown corpus" and force re-scoring rather
        # than risk returning stale records.
        if cached_fp is None:
            return False, (
                "cache predates corpus-text fingerprinting "
                "(pre-1.29.1); re-score to populate"
            )
        if cached_fp != corpus_text_fingerprint:
            return False, "corpus text content changed"
    if cache_meta.get("use") != args.use:
        return False, f"use filter changed ({cache_meta.get('use')} → {args.use})"
    if bool(cache_meta.get("do_tier2")) != bool(args.tier2):
        return False, "tier2 toggle changed"
    if bool(cache_meta.get("do_tier3")) != bool(args.tier3):
        return False, "tier3 toggle changed"
    # 1.80.0+: tier4 + model-alias compat. Pre-1.80 caches lack these
    # fields (defaulting to None / False); the check is forgiving when
    # both sides are absent so existing caches keep working.
    cur_tier4 = bool(getattr(args, "tier4", False))
    if bool(cache_meta.get("do_tier4")) != cur_tier4:
        return False, "tier4 toggle changed"
    cur_embed = getattr(args, "embedding_model", None)
    if cache_meta.get("embedding_model") != cur_embed:
        return False, (
            f"embedding_model changed "
            f"({cache_meta.get('embedding_model')!r} → {cur_embed!r})"
        )
    cur_embed_rev = getattr(args, "embedding_revision", None)
    if cache_meta.get("embedding_revision") != cur_embed_rev:
        return False, "embedding_revision changed"
    cur_surp = getattr(args, "surprisal_model", None)
    if cache_meta.get("surprisal_model") != cur_surp:
        return False, (
            f"surprisal_model changed "
            f"({cache_meta.get('surprisal_model')!r} → {cur_surp!r})"
        )
    cur_surp_rev = getattr(args, "surprisal_revision", None)
    if cache_meta.get("surprisal_revision") != cur_surp_rev:
        return False, "surprisal_revision changed"
    # 1.93.0+: dtype is part of cache identity for Tier-4 runs. The
    # ``_resolved`` label (``"fp32"`` / ``"fp16"`` / ``"bf16"``) is
    # the load-bearing field — it's host-dependent for ``auto`` runs,
    # so comparing the request string alone would let an ``auto``
    # cache scored on CPU (resolved fp32) silently reuse on H100
    # (resolves bf16). Compat-check requires the resolved labels to
    # match (or both to be None, which means torch is unavailable on
    # both sides and surprisal scoring isn't really happening). When
    # tier4 is off, surprisal scoring didn't happen and the dtype
    # field is meaningless.
    #
    # Field-missing vs field-None: we distinguish on key presence.
    # "surprisal_dtype_resolved" not in cache_meta means pre-1.93.0
    # cache (force re-score). cache_meta["..."] == None means the
    # cache was written without torch (no resolution possible); fine
    # iff the current host also can't resolve.
    if cur_tier4:
        if "surprisal_dtype_resolved" not in cache_meta:
            return False, (
                "surprisal_dtype_resolved absent on cache (pre-1.93.0 "
                "Tier-4 cache); re-scoring to record the resolved "
                "dtype on this host"
            )
        cached_resolved = cache_meta["surprisal_dtype_resolved"]
        current_resolved = _resolve_surprisal_dtype_label(args)
        if cached_resolved != current_resolved:
            return False, (
                f"surprisal_dtype_resolved changed "
                f"({cached_resolved!r} → {current_resolved!r})"
            )
    cached_sub = cache_meta.get("sub_sample")
    cur_max = getattr(args, "max_entries", None)
    cur_seed = getattr(args, "max_entries_seed", None)
    if cur_seed is None:
        cur_seed = getattr(args, "bootstrap_seed", 42) or 42
    if cur_max is None and cached_sub is not None:
        return False, "cache is sub-sampled but full run requested"
    if cur_max is not None and cached_sub is None:
        return False, "cache is full-corpus but sub-sample requested"
    if cached_sub is not None and cur_max is not None:
        if cached_sub.get("n_used") != cur_max or int(cached_sub.get("seed") or 0) != int(cur_seed):
            return False, "sub-sample cap or seed changed"
    if cache_meta.get("scorer_version") != SCORER_CACHE_VERSION:
        return False, "scorer version bumped"
    return True, ""


def load_or_score_corpus(
    args: argparse.Namespace,
    *,
    cache_path: Path | None,
    refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    """Returns ``(records, scoring_meta, cache_was_hit)``.

    If ``cache_path`` is None or the cache file doesn't exist or the
    cache is incompatible with the current args, this scores fresh
    and (when ``cache_path`` is set) writes the cache. Otherwise it
    loads from cache and returns the cached records.

    Cache layout (JSON):

    ```
    {
      "scoring_meta": { ... },
      "records": [ {...}, {...}, ... ]
    }
    ```

    ``records`` are the raw `score_smoothing_entry` outputs (pure
    dicts; JSON-friendly).
    """
    manifest_path = Path(args.manifest)
    fresh_hash = _manifest_content_hash(manifest_path)

    if cache_path and cache_path.exists() and not refresh:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            sys.stdout.write(
                f"Cache at {cache_path} is unreadable ({exc}); re-scoring.\n"
            )
            cached = None
        if cached is not None:
            cache_meta = cached.get("scoring_meta") or {}
            # Cache status (1.69.0+): "complete" is the historical
            # behavior — every record is present. "in_progress" is a
            # partial cache from a crashed prior run; the caller
            # falls through to score_corpus which resumes from it.
            # Missing status field defaults to "complete" for
            # backward compat with pre-1.69.0 caches.
            cache_status = cached.get("status", "complete")
            # Compute the corpus text fingerprint up-front — costs
            # only the file-hash pass, not the variance audit pass.
            # If this matches the cached fingerprint, the cache is
            # safe to reuse without re-scoring.
            try:
                fresh_entries = [
                    e for e in load_manifest_entries(manifest_path)
                    if _entry_uses(e, args.use)
                    and not _entry_uses(e, "exclude")
                ]
                fresh_text_fp: str | None = _corpus_text_fingerprint(
                    fresh_entries
                )
            except Exception:  # noqa: BLE001
                fresh_text_fp = None
            ok, reason = cache_is_compatible(
                cache_meta, args,
                manifest_sha256=fresh_hash,
                corpus_text_fingerprint=fresh_text_fp,
            )
            if ok and cache_status == "complete":
                records = cached.get("records") or []
                sys.stdout.write(
                    f"Cache hit: {len(records)} records loaded from "
                    f"{cache_path} (scored at {cache_meta.get('scored_at')}).\n"
                )
                return records, cache_meta, True
            if ok and cache_status == "in_progress":
                # Fall through: score_corpus will pick up where the
                # partial cache left off (it knows how to resume).
                sys.stdout.write(
                    f"Partial cache hit at {cache_path}: status="
                    f"in_progress; resuming scoring.\n"
                )
            elif not ok:
                sys.stdout.write(
                    f"Cache at {cache_path} is incompatible "
                    f"({reason}); re-scoring.\n"
                )

    flush_every = int(getattr(args, "records_cache_flush_every", 100))
    records, scoring_meta = score_corpus(
        args,
        partial_cache_path=cache_path,
        flush_every=flush_every,
        refresh=refresh,
    )
    if cache_path:
        _save_score_cache(
            cache_path, scoring_meta, records, status="complete",
        )
        sys.stdout.write(
            f"Wrote scored-records cache to {cache_path} "
            f"({len(records)} records, status=complete).\n"
        )
    return records, scoring_meta, False


# Polarity-inversion gate (1.59.0+) — refuse to publish a threshold
# entry when the corpus contradicts the registry's direction
# hypothesis. The framework's README documents the empirical
# motivation: every Tier 1 signal flipped polarity between the
# EditLens val split (2026-05-10) and MAGE (2026-05-11). Each per-
# corpus calibration produces a threshold that does NOT generalize.
# The gate enforces that finding at code level: if
# ``direction_aware_auc`` falls below the chance line for a given
# corpus + signal, the harness refuses to ship the entry as a
# load-bearing threshold. The operator can override the refusal
# with ``--allow-polarity-inversion`` when explicitly documenting
# the inversion (the override path decorates ``notes`` with a loud
# POLARITY INVERSION marker so the entry can never be silently
# treated as a calibrated threshold).
#
# Default margin is 0.0 (strict: any DA-AUC < 0.5 trips). The
# operator can widen via ``--polarity-inversion-margin 0.05`` so
# only DA-AUC < 0.45 trips (useful for borderline cases on small
# corpora where the AUC estimate has wide variance).

DEFAULT_POLARITY_INVERSION_MARGIN = 0.0

# Upper bound for the margin. A margin of exactly 0.5 would shift
# the chance line to 0.0, at which point a DA-AUC of 0.0 (the most
# extreme inverted polarity possible) just barely passes the gate
# — silently disabling the refusal. Values above 0.5 shift the
# line below zero and disable the gate entirely. The valid range
# is therefore the half-open interval [0.0, 0.5). Pinned as a
# constant so the validator, the tests, and the CLI help text all
# read from the same source.
MAX_POLARITY_INVERSION_MARGIN = 0.5


def _validate_polarity_margin(margin: Any) -> float:
    """Validate that ``margin`` is a real number in [0.0, 0.5) and
    return the normalized float.

    Raises ``SystemExit`` (which argparse turns into rc=2 at the
    CLI and lets programmatic callers catch by type) with a clear
    diagnostic if the value is outside range, NaN, or non-numeric.

    The valid range is the half-open interval ``[0.0, 0.5)``:

      * margin == 0.0 is strict (chance line stays at 0.5).
      * margin > 0.0 widens the chance line downward by exactly
        that amount.
      * margin == 0.5 would shift the line to 0.0 and a DA-AUC
        of 0.0 (the most extreme inverted polarity possible)
        would pass the gate. We refuse the boundary explicitly.
      * margin > 0.5 shifts the line below zero — every possible
        DA-AUC value passes, silently disabling the gate. This is
        the typo-class failure Codex flagged on PR #40 (e.g.,
        ``--polarity-inversion-margin 5`` instead of ``0.5``).
      * margin < 0.0 would shift the line above 0.5 — refusing
        readings that match the registry's hypothesis, which
        would invert the gate's meaning rather than disable it.
    """
    try:
        m = float(margin)
    except (TypeError, ValueError):
        raise SystemExit(
            f"--polarity-inversion-margin must be a real number; "
            f"got {margin!r}."
        )
    if m != m:  # NaN check (NaN is the only float != itself)
        raise SystemExit(
            "--polarity-inversion-margin must be a real number; "
            "got NaN."
        )
    if not (0.0 <= m < MAX_POLARITY_INVERSION_MARGIN):
        raise SystemExit(
            f"--polarity-inversion-margin must satisfy "
            f"0.0 <= margin < {MAX_POLARITY_INVERSION_MARGIN}; "
            f"got {m}. The margin shifts the chance line down "
            f"from 0.5 by exactly that amount; values outside "
            f"this range either disable the gate (margin >= "
            f"{MAX_POLARITY_INVERSION_MARGIN}, chance line at "
            f"or below 0.0 so every DA-AUC passes) or invert "
            f"its meaning (margin < 0.0, chance line above 0.5 "
            f"so the gate refuses readings that AGREE with the "
            f"registry hypothesis). Use 0.0 for strict, ~0.05 "
            f"for borderline-tolerant calibration on small "
            f"corpora."
        )
    return m


class PolarityInversionRefusal(SystemExit):
    """Raised when the corpus's direction-aware AUC falls below the
    chance line for the signal under test.

    Subclasses ``SystemExit`` so the CLI exits non-zero with the
    refusal message; programmatic callers (``derive_threshold(...)``
    invoked from a notebook or another script) can catch it
    specifically rather than the generic ``SystemExit``.

    The exception's ``code`` attribute is the diagnostic message
    string, in the style of every other SystemExit raised in this
    module.
    """


def _check_polarity_inversion(
    *,
    signal: str,
    signal_path: str,
    direction: str,
    direction_aware_auc: float | None,
    corpus_label: str,
    allow_polarity_inversion: bool,
    margin: float,
) -> tuple[bool, float]:
    """Return ``(triggered, chance_line)``.

    * ``triggered`` is True iff the polarity gate flagged an
      inversion and the run is proceeding under
      ``--allow-polarity-inversion``.
    * ``chance_line`` is the normalized cutoff (``0.5 - margin``
      after validation). Returned so the caller can use the same
      value for the gate logic and for the provenance block —
      Codex review P1 on PR #40 flagged that the two had drifted
      and that a typo-class invalid margin could silently disable
      the gate.

    Raises :class:`PolarityInversionRefusal` when DA-AUC is below
    the chance line AND the override flag is False.

    Raises ``SystemExit`` (via :func:`_validate_polarity_margin`)
    when ``margin`` is out of the valid range ``[0.0, 0.5)``.
    This validation runs even when ``direction_aware_auc`` is None
    (the no-op back-compat path) — an invalid margin should fail
    loudly regardless of whether the gate would ultimately fire.

    When ``direction_aware_auc`` is None (older test fixtures that
    mock ``_ranking_metrics`` with the legacy ``{auc, ap}`` shape),
    the gate is skipped — there is no information to refuse on.
    Same back-compat posture as the survey-row builder.
    """
    # Validate margin upfront; raises SystemExit on invalid. This
    # runs unconditionally so a typo-class margin (e.g., 5 instead
    # of 0.5) fails loudly even on the DA-AUC-is-None back-compat
    # path. Codex PR #40 review P1.
    validated_margin = _validate_polarity_margin(margin)
    chance_line = 0.5 - validated_margin
    if direction_aware_auc is None:
        return False, chance_line
    if direction_aware_auc >= chance_line:
        return False, chance_line
    # Inversion detected. Compose a diagnostic that names every
    # piece of context an operator needs to act on (or override).
    diagnostic = (
        f"\nPOLARITY INVERSION refused: signal {signal!r} "
        f"(path {signal_path!r}, registry direction {direction!r}) "
        f"shows direction_aware_auc = {direction_aware_auc:.4f}, "
        f"below the chance line {chance_line:.4f} for this corpus "
        f"({corpus_label!r}).\n"
        f"\n"
        f"What this means: the registry's hypothesis is that "
        f"AI-shaped prose has a {direction!r}-direction relationship "
        f"on this signal vs. the human comparator. On this corpus, "
        f"that direction is reversed — the AI class scores in the "
        f"opposite direction. Publishing a threshold derived from "
        f"this corpus would produce a calibration that ranks the "
        f"AI class wrong on every future input.\n"
        f"\n"
        f"This is the load-bearing failure mode documented in "
        f"README \"Why no verdict\" §cross-corpus polarity "
        f"volatility. Per-corpus polarity is corpus-bound; "
        f"calibration thresholds derived from a single corpus do "
        f"not generalize.\n"
        f"\n"
        f"Two principled paths forward:\n"
        f"  1. Refuse to ship a threshold for this signal on this "
        f"     corpus. The framework's Stylometry-to-the-people "
        f"     posture says calibration is the operator's job; this "
        f"     gate is that posture made operational.\n"
        f"  2. Override with --allow-polarity-inversion to document "
        f"     the inversion in the provenance ledger. The entry's "
        f"     notes will be prefixed with POLARITY INVERSION so "
        f"     downstream consumers cannot silently treat it as a "
        f"     calibrated load-bearing threshold. Pair with "
        f"     --polarity-inversion-margin if the AUC sits near the "
        f"     chance line and the variance is wide.\n"
    )
    if not allow_polarity_inversion:
        raise PolarityInversionRefusal(diagnostic)
    sys.stderr.write(
        f"WARNING: {diagnostic}"
        f"\n--allow-polarity-inversion set; proceeding under "
        f"override. Entry's notes will be prefixed accordingly.\n"
    )
    return True, chance_line


def derive_threshold_from_records(
    records: list[dict[str, Any]],
    *,
    args: argparse.Namespace,
    scoring_meta: dict[str, Any],
    pre_extracted_pairs: list[tuple[int, float]] | None = None,
) -> dict[str, Any]:
    """Per-signal threshold sweep + provenance entry composition.

    Pure: no scoring, no I/O. Reads the cached signal column out of
    `records`, sweeps the threshold direction-aware, builds the CI,
    and assembles the provenance entry. Tagged with sub-sample
    metadata copied from `scoring_meta` so the PIPELINE CHECK
    notes-prefix propagates correctly.

    ``pre_extracted_pairs`` is the parallel-aggregator fast-path
    (added with the hardened-aggregator PR). When provided, the
    function skips the per-signal ``collect_signal_records`` step and
    uses the supplied ``[(label, score), ...]`` list directly. This
    lets the aggregator extract pairs once in the parent process and
    dispatch only the ~4 MB-per-signal payload to workers, instead of
    the ~2 GB raw-records list that would otherwise be pickled or
    re-walked per worker. Pass ``records=[]`` when supplying pre-
    extracted pairs; the records list is unused on that path.
    """
    if args.signal not in COMPRESSION_HEURISTICS:
        raise SystemExit(
            f"Unknown signal {args.signal!r}. Known: "
            f"{', '.join(sorted(COMPRESSION_HEURISTICS))}"
        )
    spec = COMPRESSION_HEURISTICS[args.signal]
    direction = spec.direction
    signal_path = spec.signal_path
    manifest_path = Path(args.manifest)
    if pre_extracted_pairs is not None:
        pairs = pre_extracted_pairs
    else:
        pairs = collect_signal_records(records, signal_path)
    if not pairs:
        raise SystemExit(
            f"No usable (label, score) pairs for signal {signal_path!r}. "
            f"Check that records are reaching the audit and that the "
            f"signal is computable on this corpus."
        )

    sweep = sweep_threshold(pairs, direction, args.fpr_target)
    if not sweep["available"]:
        sys.stderr.write(
            f"Could not derive threshold: {sweep['reason']}\n"
        )
        raise SystemExit(2)

    metrics = _ranking_metrics(pairs, direction=direction)

    # Polarity-inversion gate (1.59.0+). Refuses to publish a
    # threshold when the corpus's direction-aware AUC falls below
    # the chance line — the canonical "this corpus's polarity
    # disagrees with the registry hypothesis" signal. See the
    # ``_check_polarity_inversion`` docstring for the design and
    # README "Why no verdict" for the empirical motivation.
    # ``getattr`` with default for back-compat with programmatic
    # callers (older tests, scripts) that build a Namespace manually
    # and don't know about the new flags.
    polarity_inversion_recorded, polarity_chance_line = (
        _check_polarity_inversion(
            signal=args.signal,
            signal_path=signal_path,
            direction=direction,
            direction_aware_auc=metrics.get("direction_aware_auc"),
            corpus_label=str(Path(args.manifest)),
            allow_polarity_inversion=bool(
                getattr(args, "allow_polarity_inversion", False)
            ),
            margin=float(getattr(
                args,
                "polarity_inversion_margin",
                DEFAULT_POLARITY_INVERSION_MARGIN,
            )),
        )
    )

    seed = _stable_seed(
        args.bootstrap_seed, args.signal, signal_path, str(args.fpr_target),
    )
    # ``getattr`` so callers that built Namespace objects manually
    # (older test fixtures, ad-hoc scripts) keep working without
    # ``bootstrap_engine`` / ``bootstrap_chunk_size`` / ``bootstrap_
    # device`` attributes. Default is the bit-exact loop engine;
    # pass ``--bootstrap-engine numpy`` on the CLI for the 50-200x
    # CPU speedup or ``--bootstrap-engine torch`` for an
    # additional 5-15x GPU speedup on CUDA/ROCm.
    engine = getattr(args, "bootstrap_engine", "loop")
    chunk_size = getattr(args, "bootstrap_chunk_size", None)
    device = getattr(args, "bootstrap_device", None)
    ci = fixed_threshold_bootstrap_ci(
        pairs,
        sweep["threshold"],
        direction,
        resamples=args.bootstrap_resamples,
        confidence=args.bootstrap_confidence,
        seed=seed,
        engine=engine,
        chunk_size=chunk_size,
        device=device,
    )

    fetch_record = _load_fetch_record(manifest_path)

    iso_date = _dt.date.today().isoformat()
    slug = args.slug or (
        f"editlens_{args.signal}_fpr{args.fpr_target}_{iso_date}"
    )

    entry = {
        "slug": slug,
        "signal": args.signal,
        "signal_path": signal_path,
        "direction": direction,
        "derived_value": sweep["threshold"],
        "corpus": {
            "name": fetch_record.get("repo_id") or manifest_path.name,
            "source": (
                f"huggingface://{fetch_record['repo_id']}"
                if fetch_record.get("repo_id") else str(manifest_path)
            ),
            "revision": fetch_record.get("revision", "unknown"),
            "license": "CC BY-NC-SA 4.0",
            "manifest_path": str(manifest_path),
            "use": args.use,
        },
        "calibration": {
            "method": "direction-aware FPR-target sweep",
            "split_role": "calibration_only",
            "fpr_target": args.fpr_target,
            "fpr_resolution": sweep["fpr_resolution"],
            "n_pos": sweep["n_pos"],
            "n_neg": sweep["n_neg"],
            "empirical_fpr": sweep["fpr"],
            "empirical_tpr": sweep["tpr"],
            "empirical_precision": sweep["precision"],
            "tpr_ci_95": ci["tpr_ci"] if ci else None,
            "fpr_ci_95": ci["fpr_ci"] if ci else None,
            "precision_ci_95": ci["precision_ci"] if ci else None,
            "auc": metrics["auc"],
            "ap": metrics["ap"],
            # Direction-aware fields default to None when older test
            # fixtures mock `_ranking_metrics` with the legacy
            # `{auc, ap}` shape; the survey row builder also tolerates
            # missing values for back-compat.
            "direction_aware_auc": metrics.get("direction_aware_auc"),
            "direction_aware_ap": metrics.get("direction_aware_ap"),
            "ci_method": ci["method"] if ci else None,
            "bootstrap_resamples": args.bootstrap_resamples,
            "bootstrap_seed": args.bootstrap_seed,
            # Engine + chunk_size are pulled from the CI dict so the
            # ledger records *what actually ran*, not what was
            # requested (relevant when ``chunk_size`` was auto-sized
            # by ``_auto_chunk_size`` and when ``engine="loop"``
            # leaves chunk_size unset). Codex review (PR #53, P1):
            # without these the ledger couldn't tell which
            # implementation produced the CI and the
            # ``--bootstrap-engine`` replay flag silently regressed
            # to the default.
            "bootstrap_engine": (ci.get("engine") if ci else engine),
            "bootstrap_chunk_size": (
                ci.get("chunk_size") if ci else None
            ),
            # Device persisted only for the torch engine; the ledger
            # records the *resolved* device string (e.g. "cuda:0",
            # "cpu") rather than the requested one so a ROCm-vs-
            # CUDA-vs-CPU reproducibility gap is auditable. Codex
            # review (PR #56, P1): without this an "auto" device
            # could resolve to CPU on one host and GPU on another
            # and the ledger would conflate them.
            "bootstrap_device": (
                ci.get("device") if ci and ci.get("engine") == "torch"
                else None
            ),
            "ci_note": ci["note"] if ci else None,
        },
        "setec_commit": _git_commit(),
        "harness_command": _build_harness_command(
            manifest_path=manifest_path,
            use=args.use,
            signal=args.signal,
            fpr_target=args.fpr_target,
            engine=engine,
            chunk_size=chunk_size,
            device=device,
        ),
        "derivation_date": iso_date,
        "notes": args.notes or (
            "In-sample calibration; treat as calibration_only until a "
            "heldout test split is added."
        ),
    }

    # Sub-sample provenance: read from scoring_meta. When the cache
    # was scored with --max-entries, the sub_sample block flows
    # through scoring_meta into every per-signal provenance entry
    # built from this cache. The notes prefix is loud enough that a
    # row in this state can never be silently treated as a calibration.
    sub_sample = scoring_meta.get("sub_sample") if scoring_meta else None
    if sub_sample:
        entry["sub_sample"] = sub_sample
        entry["notes"] = (
            "PIPELINE CHECK (sub-sampled run, NOT a calibration). "
            f"{sub_sample['n_used']}/{sub_sample['n_full']} entries used. "
            "Do not commit this entry to the ledger as a calibrated "
            "threshold; small-N gates won't pass meaningfully. "
            + entry["notes"]
        )
    # Polarity-inversion provenance: when --allow-polarity-inversion
    # is set and the corpus tripped the gate, record the inversion
    # in the entry so downstream consumers cannot silently treat
    # this as a load-bearing calibration. Same notes-prefix
    # convention sub_sample uses (PIPELINE CHECK / POLARITY INVERSION).
    if polarity_inversion_recorded:
        da_auc = metrics.get("direction_aware_auc")
        # Reuse the validated chance_line from the gate so the
        # provenance block and the gate logic agree on the exact
        # cutoff used. Codex PR #40 review P1: pre-fix the
        # provenance block recomputed `0.5 - raw_margin` without
        # validation, so a typo-class invalid margin could land
        # in the ledger as a negative chance line.
        entry["polarity_inversion"] = {
            "recorded": True,
            "direction_aware_auc": da_auc,
            "chance_line": polarity_chance_line,
            "registry_direction": direction,
        }
        da_auc_str = (
            f"{da_auc:.4f}" if isinstance(da_auc, (int, float))
            else "n/a"
        )
        entry["notes"] = (
            f"POLARITY INVERSION (corpus disagrees with registry "
            f"direction {direction!r}; direction_aware_auc="
            f"{da_auc_str}, below the chance line). Override was "
            f"explicit (--allow-polarity-inversion). DO NOT treat "
            f"this entry as a load-bearing calibration — the "
            f"threshold ranks the AI class wrong by the registry's "
            f"hypothesis. Documenting the inversion is the entry's "
            f"only legitimate use. "
            + entry["notes"]
        )
    return entry


def derive_threshold(args: argparse.Namespace) -> dict[str, Any]:
    """Backward-compat composer.

    Pre-1.26.0 callers (the standalone CLI; older test fixtures)
    expect a one-call function that scores + sweeps + builds a
    provenance entry in one shot. The new architecture splits
    these into ``score_corpus`` + ``derive_threshold_from_records``;
    this composer keeps the old surface working AND now honors the
    optional ``--records-cache`` flag so even single-signal CLI
    invocations benefit from cache reuse on re-runs.
    """
    cache_path_str = getattr(args, "records_cache", None)
    cache_path = Path(cache_path_str).expanduser() if cache_path_str else None
    refresh = bool(getattr(args, "refresh_cache", False))
    records, scoring_meta, _hit = load_or_score_corpus(
        args, cache_path=cache_path, refresh=refresh,
    )
    return derive_threshold_from_records(
        records, args=args, scoring_meta=scoring_meta,
    )


def append_to_ledger(out_path: Path, entry: dict[str, Any], replace: bool) -> None:
    if out_path.exists():
        existing = json.loads(out_path.read_text(encoding="utf-8"))
        if not isinstance(existing, list):
            raise SystemExit(
                f"Ledger {out_path} is not a JSON list; aborting."
            )
    else:
        existing = []
    matching = [
        i for i, e in enumerate(existing)
        if e.get("slug") == entry["slug"]
    ]
    if matching:
        if not replace:
            raise SystemExit(
                f"Slug {entry['slug']!r} already exists in ledger. "
                f"Pass --replace to overwrite, or use --slug to pick a "
                f"different id."
            )
        existing[matching[0]] = entry
    else:
        existing.append(entry)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Direction-aware per-signal threshold sweep + provenance "
            "writer."
        )
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--use", default="validation")
    parser.add_argument(
        "--signal", required=True,
        help=(
            "Heuristic key in COMPRESSION_HEURISTICS (e.g., burstiness_B). "
            "Direction + signal_path are looked up from the registry."
        ),
    )
    parser.add_argument("--fpr-target", type=float, required=True)
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "scripts" / "calibration" / "thresholds_calibrated.json"),
        help="Path to the JSON provenance ledger (append or update).",
    )
    parser.add_argument("--slug", default=None)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--bootstrap-resamples", type=int, default=2000)
    parser.add_argument("--bootstrap-confidence", type=float, default=0.95)
    parser.add_argument("--bootstrap-seed", type=int, default=42)
    parser.add_argument(
        "--bootstrap-engine",
        choices=["loop", "numpy", "torch"],
        default="loop",
        help=(
            "Bootstrap-CI implementation. ``loop`` (default) is "
            "pure Python; bit-exact with pre-1.60 ledger entries. "
            "``numpy`` is a vectorized NumPy implementation that "
            "is 50-200x faster on >=100K-row corpora. ``torch`` "
            "is a PyTorch implementation that auto-detects a "
            "CUDA/ROCm GPU and runs the inner gather/count/"
            "quantile on-device for an additional 5-15x speedup. "
            "All three are statistically equivalent for 2000+ "
            "resamples; only ``loop`` is bit-exact with the "
            "pre-1.60 ledger."
        ),
    )
    parser.add_argument(
        "--bootstrap-device",
        default=None,
        help=(
            "Device override for ``--bootstrap-engine torch``. "
            "Default is auto-detect: ``cuda`` if a CUDA or ROCm "
            "GPU is reachable, else ``cpu``. Pass ``cpu`` to "
            "force the CPU torch path (cross-platform "
            "reproducibility), or a specific device string like "
            "``cuda:1`` to target a non-default GPU. Ignored "
            "when ``--bootstrap-engine`` is ``loop`` or ``numpy``."
        ),
    )
    parser.add_argument(
        "--bootstrap-chunk-size",
        type=int,
        default=None,
        help=(
            "Override the inner-loop chunk size for the vectorized "
            "engines (``numpy``, ``torch``). Default is auto-sized "
            "via ``_auto_chunk_size`` to cap inner-loop peak "
            "memory at ~500 MB: at MAGE scale (n=436K) that's "
            "chunk ~96, at RAID scale (n=8.3M) chunk ~5. Pass an "
            "explicit value to override — larger chunks for "
            "throughput on memory-plentiful hosts, smaller for "
            "memory-tight ones. Ignored by the ``loop`` engine. "
            "The actual chunk size used is recorded in the "
            "ledger's ``calibration.bootstrap_chunk_size`` field."
        ),
    )
    parser.add_argument(
        "--tier2", action="store_true", default=True,
        help="Run Tier 2 (POS bigrams, MDD-SD; needs spaCy). Default on.",
    )
    parser.add_argument(
        "--tier3", action="store_true", default=True,
        help="Run Tier 3 (cohesion). Default on.",
    )
    parser.add_argument(
        "--no-tier2", dest="tier2", action="store_false",
    )
    parser.add_argument(
        "--no-tier3", dest="tier3", action="store_false",
    )
    # 1.81.0+: standalone-CLI exposure of the pipeline-wired Tier 4 +
    # pluggable-embedding flags landed in 1.80.0. score_corpus +
    # score_smoothing_entry read these via getattr; before 1.81.0 only
    # shard_runner shard's CLI populated them on the args Namespace,
    # so single-signal threshold-derivation runs from this CLI couldn't
    # exercise Tier 4 or a non-MiniLM embedding model.
    parser.add_argument(
        "--tier4", action="store_true", default=False,
        help=(
            "Enable Tier 4 (surprisal) signals on the scoring run. "
            "Opt-in. Requires transformers + torch; see RUNBOOK_tier4_"
            "install.md."
        ),
    )
    parser.add_argument(
        "--no-tier4", dest="tier4", action="store_false",
    )
    parser.add_argument(
        "--surprisal-model", default=None,
        help=(
            "Causal LM alias or HuggingFace id for Tier 4. "
            "Default (when --tier4 is set): tinyllama. See "
            "surprisal_backend.MODEL_ALIASES for the 9 candidates."
        ),
    )
    parser.add_argument(
        "--surprisal-revision", default=None,
        help=(
            "Pin a HuggingFace commit SHA for the Tier 4 causal LM "
            "(reproducibility). Default: revision-less."
        ),
    )
    parser.add_argument(
        "--surprisal-dtype",
        choices=("auto", "fp32", "fp16", "bf16"),
        default="auto",
        help=(
            "Precision for Tier 4 causal-LM inference. ``auto`` picks "
            "bf16 on supporting cuda (Ampere+ / Hopper / Ada), fp16 on "
            "older cuda (V100 / T4) where bf16 falls back to slow "
            "kernels, fp32 on CPU / MPS. Explicit values override the "
            "auto resolution. The log_softmax step is always computed "
            "in fp32 so the surprisal-series numerical contract is "
            "stable across dtype choices. No effect when --tier4 is "
            "off."
        ),
    )
    parser.add_argument(
        "--surprisal-batch-size", type=int, default=8,
        help=(
            "Batch size for Tier 4 surprisal scoring under the "
            "batched ``score_texts`` path (1.90.0+). Larger values "
            "improve GPU utilisation but raise VRAM peak; 8 is "
            "conservative for 1-2B-param causal LMs on a 24 GB L4. "
            "Bump to 16 or 32 on A100 / H100. Set to 1 to bypass "
            "batching and reproduce the legacy per-entry scoring "
            "path exactly. No effect when --tier4 is off."
        ),
    )
    parser.add_argument(
        "--embedding-model", default=None,
        help=(
            "Embedding-model alias or HuggingFace id for Tier 3 "
            "cohesion. Default: legacy MiniLM hardcode. Aliases: "
            "mxbai, gemma, harrier, minilm."
        ),
    )
    parser.add_argument(
        "--embedding-revision", default=None,
        help=(
            "Pin a HuggingFace commit SHA for the Tier 3 embedding "
            "model (reproducibility). Default: revision-less."
        ),
    )
    parser.add_argument(
        "--embedding-dtype",
        choices=("auto", "fp32", "fp16", "bf16"),
        default="auto",
        help=(
            "Precision for Tier 3 embedding-model inference. ``auto`` "
            "picks bf16 on supporting cuda (Ampere+ / Hopper / Ada), "
            "fp16 on older cuda, fp32 on CPU / MPS. Mirror of "
            "--surprisal-dtype on the embedding side (added 1.96.0). "
            "No effect when --tier3 is off, or when running through "
            "the legacy MiniLM fallback (no --embedding-model)."
        ),
    )
    parser.add_argument(
        "--embedding-device", default=None,
        help=(
            "Explicit device for the Tier 3 embedding model "
            "(e.g., ``cuda:1``). Default: defer to sentence-"
            "transformers' auto-device pick."
        ),
    )
    parser.add_argument(
        "--notes",
        help=(
            "Free-text caveat for the provenance entry. Default mentions "
            "in-sample / calibration_only."
        ),
    )
    parser.add_argument(
        "--max-entries", type=int, default=None,
        help=(
            "Cap the number of manifest entries used for scoring. "
            "Sub-sampling is label-stratified and seeded by "
            "--bootstrap-seed (or --max-entries-seed if set), so "
            "partial runs are reproducible. Use this for pipeline "
            "checks before committing to a full calibration; "
            "small-N runs will not pass the FPR-resolution and TPR-"
            "interpretability gates and the resulting threshold "
            "should NOT be committed to the ledger."
        ),
    )
    parser.add_argument(
        "--max-entries-seed", type=int, default=None,
        help=(
            "Override the seed used for stratified sub-sampling. "
            "Defaults to --bootstrap-seed."
        ),
    )
    parser.add_argument(
        "--records-cache", default=None,
        help=(
            "Path to a JSON cache of scored records. If the file "
            "exists and is compatible with the current --manifest / "
            "--use / --tier2 / --tier3 / --max-entries args, the "
            "cache is read instead of re-scoring (single-signal "
            "calls become threshold-sweep-only — seconds, not "
            "minutes). If the file doesn't exist or the cache is "
            "incompatible (manifest changed, tier toggle changed, "
            "scorer version bumped), the script scores fresh and "
            "writes the cache. Per-signal calls sharing one cache "
            "path is the recommended workflow for surveys."
        ),
    )
    parser.add_argument(
        "--refresh-cache", action="store_true",
        help=(
            "Force re-scoring even if a compatible cache exists. "
            "Use after a code change that should invalidate cached "
            "records but didn't bump SCORER_CACHE_VERSION."
        ),
    )
    parser.add_argument(
        "--records-cache-flush-every", type=int, default=100,
        help=(
            "Write --records-cache atomically every N scored entries "
            "with status='in_progress' (1.69.0+). A crash mid-scoring "
            "loses at most N entries; the next run resumes from the "
            "partial cache automatically. Default 100. Ignored when "
            "--records-cache is unset."
        ),
    )
    # Polarity-inversion gate (1.59.0+). See _check_polarity_inversion
    # for the design and README "Why no verdict" for the empirical
    # motivation. Default behavior: refuse to publish a threshold
    # when direction_aware_auc falls below the chance line. Override
    # is explicit-only — no silent fallback.
    parser.add_argument(
        "--allow-polarity-inversion", action="store_true",
        help=(
            "Override the polarity-inversion refusal gate. Use ONLY "
            "when documenting an inverted-polarity finding (the "
            "entry's notes will be loudly prefixed POLARITY "
            "INVERSION so downstream consumers cannot silently "
            "treat it as a calibrated load-bearing threshold). The "
            "default behavior — refuse to ship — is correct for "
            "every operational use of this tool."
        ),
    )
    parser.add_argument(
        "--polarity-inversion-margin", type=float,
        default=DEFAULT_POLARITY_INVERSION_MARGIN,
        help=(
            "Margin below the chance line (0.5) at which the "
            f"polarity-inversion gate trips. Default "
            f"{DEFAULT_POLARITY_INVERSION_MARGIN} (any DA-AUC < 0.5 "
            "trips; strict). A wider margin (e.g., 0.05) tolerates "
            "DA-AUC values close to chance — useful for small "
            "corpora where the AUC estimate has wide variance and "
            "you don't want the gate firing on noise. The margin "
            "shifts the line down: --polarity-inversion-margin 0.05 "
            "means only DA-AUC < 0.45 trips."
        ),
    )

    args = parser.parse_args(argv)
    entry = derive_threshold(args)
    out_path = Path(args.out)
    append_to_ledger(out_path, entry, args.replace)

    sys.stdout.write(
        f"Wrote provenance entry: {entry['slug']}\n"
        f"  signal:        {entry['signal']} (direction {entry['direction']})\n"
        f"  derived value: {entry['derived_value']}\n"
        f"  AUC / AP:      {entry['calibration']['auc']:.4f} / "
        f"{entry['calibration']['ap']:.4f}\n"
        f"  TPR @ FPR target {args.fpr_target}: "
        f"{entry['calibration']['empirical_tpr']:.4f} "
        f"(empirical FPR {entry['calibration']['empirical_fpr']:.4f})\n"
        # Use as_relative_path defensively — when the user runs from
        # a worktree, the absolute out_path may not be a subpath of
        # REPO_ROOT and `relative_to` raises. The display string is
        # informational; falling back to the absolute path is fine.
        f"  ledger: "
        f"{out_path.relative_to(REPO_ROOT) if out_path.is_relative_to(REPO_ROOT) else out_path}\n"
        f"\n"
        f"Next: edit scripts/variance_audit.py and set\n"
        f"  COMPRESSION_HEURISTICS[{args.signal!r}].provenance = "
        f"{entry['slug']!r}\n"
        f"  COMPRESSION_HEURISTICS[{args.signal!r}].status = 'calibrated'\n"
        f"  COMPRESSION_HEURISTICS[{args.signal!r}].value = "
        f"{entry['derived_value']}\n"
        f"and add a section to scripts/calibration/PROVENANCE.md.\n"
        f"(v1.66.0 retier: `provisional` is now a derived property;\n"
        f"set `status` to one of {{calibrated, literature_anchored,\n"
        f"empirically_oriented, heuristic}}.)\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
