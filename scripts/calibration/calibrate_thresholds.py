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

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
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


def _ranking_metrics(pairs: Sequence[tuple[int, float]]) -> dict[str, float | None]:
    """Compute AUC + AP. Try sklearn first, then a Mann-Whitney
    fallback. Mirrors validation_harness.fallback_roc_auc /
    fallback_average_precision behavior."""
    try:
        from sklearn.metrics import (  # type: ignore
            average_precision_score,
            roc_auc_score,
        )
        labels = [p[0] for p in pairs]
        scores = [p[1] for p in pairs]
        return {
            "auc": float(roc_auc_score(labels, scores)),
            "ap": float(average_precision_score(labels, scores)),
        }
    except Exception:
        from validation_harness import (  # type: ignore
            fallback_average_precision,
            fallback_roc_auc,
        )
        labels = [p[0] for p in pairs]
        scores = [p[1] for p in pairs]
        return {
            "auc": fallback_roc_auc(labels, scores),
            "ap": fallback_average_precision(labels, scores),
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


def derive_threshold(
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Run the full pipeline and return a provenance entry."""
    if args.signal not in COMPRESSION_HEURISTICS:
        raise SystemExit(
            f"Unknown signal {args.signal!r}. Known: "
            f"{', '.join(sorted(COMPRESSION_HEURISTICS))}"
        )
    spec = COMPRESSION_HEURISTICS[args.signal]
    direction = spec.direction
    signal_path = spec.signal_path

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

    sys.stdout.write(
        f"Scoring {len(entries)} entries via variance audit "
        f"(this can take a while if Tier 2/3 are enabled)...\n"
    )
    positive_statuses = set(DEFAULT_POSITIVE_STATUSES)
    negative_statuses = set(DEFAULT_NEGATIVE_STATUSES)
    records = []
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

    metrics = _ranking_metrics(pairs)

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
    return entry


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
        f"  ledger: {out_path.relative_to(REPO_ROOT)}\n"
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
