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


def sweep_threshold(
    pairs: Sequence[tuple[int, float]],
    direction: str,
    fpr_target: float,
) -> dict[str, Any]:
    """Direction-aware sweep. Picks the highest-TPR threshold whose
    empirical FPR <= target. Returns the threshold + rates + the full
    candidate list."""
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


def fixed_threshold_bootstrap_ci(
    pairs: Sequence[tuple[int, float]],
    threshold: float,
    direction: str,
    *,
    resamples: int,
    confidence: float,
    seed: int | None,
) -> dict[str, Any] | None:
    """Paired-record bootstrap on TPR / FPR / precision at a fixed
    threshold. Resampling pair indices with replacement; each resample
    recomputes the rates at the same threshold."""
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


def score_corpus(
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Score every (filtered, optionally sub-sampled) manifest entry.

    Pure scoring — no per-signal logic. Called once per calibration
    run; the resulting record list carries every signal's score as a
    field, so per-signal threshold sweeps later just read out the
    relevant column.

    Returns ``(records, scoring_meta)``. ``scoring_meta`` carries the
    inputs that determine cache validity:

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

    sys.stdout.write(
        f"Scoring {len(entries)} entries via variance audit "
        f"(this can take a while if Tier 2/3 are enabled)...\n"
    )
    positive_statuses = set(DEFAULT_POSITIVE_STATUSES)
    negative_statuses = set(DEFAULT_NEGATIVE_STATUSES)
    records: list[dict[str, Any]] = []
    for i, e in enumerate(entries):
        if i % 50 == 0 and i > 0:
            sys.stdout.write(f"  scored {i}/{len(entries)}...\n")
        records.append(
            score_smoothing_entry(
                e,
                positive_statuses=positive_statuses,
                negative_statuses=negative_statuses,
                do_tier2=args.tier2,
                do_tier3=args.tier3,
            )
        )

    scoring_meta = {
        "manifest_path": str(manifest_path),
        "manifest_sha256": _manifest_content_hash(manifest_path),
        "corpus_text_fingerprint": _corpus_text_fingerprint(entries),
        "use": args.use,
        "do_tier2": bool(args.tier2),
        "do_tier3": bool(args.tier3),
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
            if ok:
                records = cached.get("records") or []
                sys.stdout.write(
                    f"Cache hit: {len(records)} records loaded from "
                    f"{cache_path} (scored at {cache_meta.get('scored_at')}).\n"
                )
                return records, cache_meta, True
            sys.stdout.write(
                f"Cache at {cache_path} is incompatible ({reason}); "
                "re-scoring.\n"
            )

    records, scoring_meta = score_corpus(args)
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {"scoring_meta": scoring_meta, "records": records},
                indent=2, default=str,
            ) + "\n",
            encoding="utf-8",
        )
        sys.stdout.write(
            f"Wrote scored-records cache to {cache_path} "
            f"({len(records)} records).\n"
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
) -> dict[str, Any]:
    """Per-signal threshold sweep + provenance entry composition.

    Pure: no scoring, no I/O. Reads the cached signal column out of
    `records`, sweeps the threshold direction-aware, builds the CI,
    and assembles the provenance entry. Tagged with sub-sample
    metadata copied from `scoring_meta` so the PIPELINE CHECK
    notes-prefix propagates correctly.
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
    ci = fixed_threshold_bootstrap_ci(
        pairs,
        sweep["threshold"],
        direction,
        resamples=args.bootstrap_resamples,
        confidence=args.bootstrap_confidence,
        seed=seed,
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
            "ci_note": ci["note"] if ci else None,
        },
        "setec_commit": _git_commit(),
        "harness_command": (
            f"python3 scripts/calibration/calibrate_thresholds.py "
            f"--manifest {manifest_path} --use {args.use} "
            f"--signal {args.signal} --fpr-target {args.fpr_target}"
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
        f"  COMPRESSION_HEURISTICS[{args.signal!r}].provisional = False\n"
        f"  COMPRESSION_HEURISTICS[{args.signal!r}].value = "
        f"{entry['derived_value']}\n"
        f"and add a section to scripts/calibration/PROVENANCE.md.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
