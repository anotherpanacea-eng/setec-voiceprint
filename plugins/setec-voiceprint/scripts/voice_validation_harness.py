#!/usr/bin/env python3
"""voice_validation_harness.py — Surface 2 sibling to validation_harness.py.

Quantifies how well SETEC's voice-distance feature machinery
discriminates same-author document pairs from different-author
document pairs on a labeled fixture, with per-feature-family ROC AUC
+ uncertainty intervals and the same publication-claim guards the
smoothing-diagnosis harness already enforces.

Structurally different from `validation_harness.py`:

- The smoothing harness scores ONE document at a time and labels by
  `ai_status`. ROC AUC ranks documents.
- This harness scores PAIRS and labels by
  `same_author = (doc_a.author == doc_b.author)`. ROC AUC ranks
  pairs by distance.

The feature-space construction is shared with production: the
selected feature names + z-score column population are built over
the entire selected validation slice, then each unordered pair is
scored inside that shared feature space. We do NOT call
`stylometry_core.family_distance()` for pairs — it is
baseline-oriented (one target vs. a baseline pool) and a one-document
"baseline" has zero SD on every feature.

Usage:

    python3 scripts/voice_validation_harness.py \\
        --manifest scripts/test_data/federalist_voice_validation_manifest.jsonl \\
        --use voice_validation \\
        --json out.json --md out.md \\
        [--bootstrap-resamples 9999] \\
        [--bootstrap-confidence 0.95] \\
        [--bootstrap-seed 0] \\
        [--bootstrap-method document_cluster|naive_pair] \\
        [--fpr-target 0.01]

See `internal/SPEC_voice_validation_harness.md` (gitignored) for the
full design.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
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
from validation_harness import (  # type: ignore
    fallback_average_precision,
    fallback_roc_auc,
)
from claim_license import ClaimLicense, from_legacy  # type: ignore  # noqa: E402
from output_schema import build_output  # type: ignore  # noqa: E402

try:
    from sklearn.metrics import (  # type: ignore
        average_precision_score,
        roc_auc_score,
    )
    HAS_SKLEARN = True
except ImportError:  # pragma: no cover - exercised on minimal installs
    HAS_SKLEARN = False


TASK_SURFACE = "voice_coherence"
TOOL_NAME = "voice_validation_harness"
SCRIPT_VERSION = "1.0"

# Families to score. These match `stylometry_core.extract_features`'s
# top-level keys when spaCy is available. Families that come up empty
# for a fixture (e.g. POS / dep when spaCy isn't installed) are
# silently skipped.
FAMILY_NAMES = (
    "function_words",
    "char_ngrams_3",
    "char_ngrams_4",
    "char_ngrams_5",
    "pos_trigrams",
    "dependency_ngrams",
)

PER_PAIR_METRICS = ("burrows_delta", "cosine_distance")


# ---- Manifest loading ----------------------------------------------


def _entry_uses(entry: dict[str, Any], use_tag: str) -> bool:
    use = entry.get("use")
    return isinstance(use, list) and use_tag in use


def load_manifest_entries(manifest_path: str | Path) -> list[dict[str, Any]]:
    """Read JSONL manifest entries and resolve paths.

    Pre-conditions: the caller has already run validate_manifest so
    schema and path integrity are clean.
    """
    path = Path(manifest_path)
    entries: list[dict[str, Any]] = []
    for lineno, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
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


# ---- Feature space + pair construction -----------------------------


def build_feature_items(
    entries: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """For each manifest entry, run extract_features and attach the
    result. Returns a list of feature-items shaped the way
    select_feature_names / feature_vector / vector_stats expect: each
    item has a `features` key holding the per-family freq dicts."""
    items: list[dict[str, Any]] = []
    for entry in entries:
        path = Path(entry["_resolved_path"])
        text = path.read_text(encoding="utf-8")
        feats = extract_features(text)
        items.append({"id": entry["id"], "features": feats["features"]})
    return items


def _informative_keys(
    stats: dict[str, dict[str, float]],
    names: Sequence[str],
) -> list[str]:
    """Names whose column has non-zero SD across the slice. Constant-SD
    features carry no Burrows-Delta information; matches the
    informative-feature denominator the oracle uses."""
    return [n for n in names if stats.get(n, {}).get("sd", 0.0) > 0.0]


def pair_burrows_delta(
    vec_a: dict[str, float],
    vec_b: dict[str, float],
    informative: Sequence[str],
    stats: dict[str, dict[str, float]],
) -> float | None:
    """Burrows-Delta as mean absolute z-difference over informative
    features. None if no informative features exist for this family on
    this slice."""
    if not informative:
        return None
    total = 0.0
    for name in informative:
        s = stats[name]
        sd = s["sd"]
        mean = s["mean"]
        z_a = (vec_a[name] - mean) / sd
        z_b = (vec_b[name] - mean) / sd
        total += abs(z_a - z_b)
    return total / len(informative)


def build_pairs(
    entries: Sequence[dict[str, Any]],
    items: Sequence[dict[str, Any]],
    selected_features: dict[str, list[str]],
    label_field: str,
) -> list[dict[str, Any]]:
    """For each unordered (i, j) pair of selected entries, compute
    per-family Burrows-Delta + cosine distance and the same-author
    label. Distance math runs in the shared feature space:
    selected_features is built over the slice, vector_stats are
    computed over the slice, z-scoring uses slice-level column stats."""
    # Build per-family vectors and column stats once.
    family_vectors: dict[str, list[dict[str, float]]] = {}
    family_stats: dict[str, dict[str, dict[str, float]]] = {}
    family_informative: dict[str, list[str]] = {}
    for family, names in selected_features.items():
        vecs = [feature_vector(it, family, names) for it in items]
        family_vectors[family] = vecs
        stats = vector_stats(vecs, names)
        family_stats[family] = stats
        family_informative[family] = _informative_keys(stats, names)

    pairs: list[dict[str, Any]] = []
    for i, j in itertools.combinations(range(len(entries)), 2):
        ent_a, ent_b = entries[i], entries[j]
        same_author = ent_a.get(label_field) == ent_b.get(label_field)
        distances: dict[str, dict[str, float | None]] = {}
        for family, names in selected_features.items():
            vec_a = family_vectors[family][i]
            vec_b = family_vectors[family][j]
            delta = pair_burrows_delta(
                vec_a, vec_b,
                family_informative[family],
                family_stats[family],
            )
            cos = cosine_distance(vec_a, vec_b, names)
            distances[family] = {
                "burrows_delta": delta,
                "cosine_distance": cos,
            }
        pairs.append({
            "doc_a": ent_a["id"],
            "doc_b": ent_b["id"],
            "doc_a_author": ent_a.get(label_field),
            "doc_b_author": ent_b.get(label_field),
            "same_author": same_author,
            "register_a": ent_a.get("register"),
            "register_b": ent_b.get("register"),
            "language_status_a": ent_a.get("language_status"),
            "language_status_b": ent_b.get("language_status"),
            "distances": distances,
        })
    return pairs


# ---- Ranking metrics + bootstrap CI --------------------------------


def _stable_seed(base_seed: int | None, *parts: str) -> int | None:
    """Derive a per-(family, metric, method) seed deterministically.

    Python's built-in `hash()` of strings/tuples is salted per process
    via PYTHONHASHSEED (random by default), so a `--bootstrap-seed`
    combined with `hash((family, metric))` produces a different RNG
    sequence on every run. SHA-256 of the joined parts is stable
    across processes, so seeds derived this way reproduce exactly when
    the same `bootstrap_seed` and parts are supplied.

    Returns None when base_seed is None (caller propagates non-
    deterministic behavior to `random.Random()`)."""
    if base_seed is None:
        return None
    payload = f"{base_seed}|{'|'.join(parts)}".encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    # Take the low 8 bytes as an unsigned 64-bit int. random.Random
    # accepts any int seed, but bounding to 64 bits keeps the value
    # representable on every platform Python supports.
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


def auc_score(labels: Sequence[int], scores: Sequence[float]) -> float | None:
    if HAS_SKLEARN:
        try:
            return float(roc_auc_score(labels, scores))
        except Exception:
            return None
    return fallback_roc_auc(labels, scores)


def ap_score(labels: Sequence[int], scores: Sequence[float]) -> float | None:
    if HAS_SKLEARN:
        try:
            return float(average_precision_score(labels, scores))
        except Exception:
            return None
    return fallback_average_precision(labels, scores)


def naive_pair_bootstrap_auc_ci(
    labels: Sequence[int],
    scores: Sequence[float],
    *,
    resamples: int,
    confidence: float,
    rng: random.Random,
) -> dict[str, Any] | None:
    """Naive resample-with-replacement over pair indices. Each pair is
    a record. Pair records are dependent (the same document appears in
    multiple pairs) so this CI is a smoke-test diagnostic, not a
    calibration-grade interval. The output labels the method as
    `naive_pair_bootstrap`."""
    n = len(labels)
    if n < 2 or len(scores) != n:
        return None
    aucs: list[float] = []
    for _ in range(resamples):
        idxs = [rng.randrange(n) for _ in range(n)]
        boot_labels = [labels[i] for i in idxs]
        boot_scores = [scores[i] for i in idxs]
        if len(set(boot_labels)) < 2:
            continue
        v = auc_score(boot_labels, boot_scores)
        if v is None:
            continue
        aucs.append(v)
    if not aucs:
        return None
    alpha = 1.0 - confidence
    lo = _quantile(aucs, alpha / 2)
    hi = _quantile(aucs, 1 - alpha / 2)
    return {
        "method": "naive_pair_bootstrap",
        "lower": lo,
        "upper": hi,
        "confidence": confidence,
        "resamples": len(aucs),
        "note": (
            "Pair records are dependent (each document appears in "
            "multiple pairs); CI is smoke-test-only, not "
            "calibration-grade."
        ),
    }


def document_cluster_bootstrap_auc_ci(
    pairs: Sequence[dict[str, Any]],
    family: str,
    metric: str,
    *,
    label_field: str,
    entries: Sequence[dict[str, Any]],
    resamples: int,
    confidence: float,
    rng: random.Random,
) -> dict[str, Any] | None:
    """Document-cluster bootstrap. Resample documents with replacement
    within each author stratum, deduplicate, rebuild unordered pairs
    over the surviving distinct documents, recompute AUC. Skip
    resamples that don't have both same-author and different-author
    pairs. Captures sampling variability over which documents we
    happened to collect, treating documents (not pairs) as the
    independent unit of evidence."""
    # Index entries by id for fast pair lookup.
    by_id = {e["id"]: e for e in entries}
    distance_lookup: dict[tuple[str, str], float | None] = {}
    for p in pairs:
        d = p["distances"].get(family, {}).get(metric)
        distance_lookup[(p["doc_a"], p["doc_b"])] = d
        distance_lookup[(p["doc_b"], p["doc_a"])] = d

    strata: dict[str, list[str]] = defaultdict(list)
    for e in entries:
        strata[e.get(label_field)].append(e["id"])

    aucs: list[float] = []
    for _ in range(resamples):
        # Resample within each author stratum, dedupe.
        survived: list[str] = []
        for ids in strata.values():
            picks = [rng.choice(ids) for _ in range(len(ids))]
            for doc_id in dict.fromkeys(picks):
                survived.append(doc_id)
        # Build pair labels and scores from the surviving distinct docs.
        labels: list[int] = []
        scores: list[float] = []
        for a, b in itertools.combinations(survived, 2):
            d = distance_lookup.get((a, b))
            if d is None:
                continue
            same = by_id[a].get(label_field) == by_id[b].get(label_field)
            labels.append(0 if same else 1)
            scores.append(d)
        if len(set(labels)) < 2:
            continue
        v = auc_score(labels, scores)
        if v is None:
            continue
        aucs.append(v)
    if not aucs:
        return None
    alpha = 1.0 - confidence
    return {
        "method": "document_cluster_bootstrap",
        "lower": _quantile(aucs, alpha / 2),
        "upper": _quantile(aucs, 1 - alpha / 2),
        "confidence": confidence,
        "resamples": len(aucs),
    }


# ---- Per-family ranking + slice rendering --------------------------


def per_family_ranking(
    pairs: Sequence[dict[str, Any]],
    *,
    bootstrap_method: str,
    bootstrap_resamples: int,
    bootstrap_confidence: float,
    bootstrap_seed: int | None,
    entries: Sequence[dict[str, Any]] | None = None,
    label_field: str = "author",
) -> list[dict[str, Any]]:
    """For each (family, metric) pair, compute AUC + AP + bootstrap
    CI and a polarity check. Skip families/metrics where any pair
    distance is None (e.g. cosine on a zero-vector pair, or Delta with
    no informative features)."""
    out: list[dict[str, Any]] = []
    families = sorted({fam for p in pairs for fam in p["distances"]})
    for family in families:
        for metric in PER_PAIR_METRICS:
            scores: list[float] = []
            labels: list[int] = []
            for p in pairs:
                d = p["distances"].get(family, {}).get(metric)
                if d is None:
                    continue
                scores.append(d)
                # Positive class = different author (higher distance
                # should rank these higher, so AUC > 0.5 is expected).
                labels.append(0 if p["same_author"] else 1)
            if len(scores) < 2 or len(set(labels)) < 2:
                continue
            auc = auc_score(labels, scores)
            ap = ap_score(labels, scores)

            if bootstrap_method == "document_cluster" and entries is not None:
                rng = random.Random(
                    _stable_seed(
                        bootstrap_seed, family, metric, "document_cluster",
                    )
                )
                ci = document_cluster_bootstrap_auc_ci(
                    pairs, family, metric,
                    label_field=label_field,
                    entries=entries,
                    resamples=bootstrap_resamples,
                    confidence=bootstrap_confidence,
                    rng=rng,
                )
            else:
                rng = random.Random(
                    _stable_seed(bootstrap_seed, family, metric, "naive_pair")
                )
                ci = naive_pair_bootstrap_auc_ci(
                    labels, scores,
                    resamples=bootstrap_resamples,
                    confidence=bootstrap_confidence,
                    rng=rng,
                )

            out.append({
                "family": family,
                "metric": metric,
                "auc": auc,
                "average_precision": ap,
                "auc_ci": ci,
                "n_pairs": len(scores),
                "polarity_ok": auc is not None and auc >= 0.5,
            })
    return out


def weighted_family_ranking(
    pairs: Sequence[dict[str, Any]],
    *,
    bootstrap_method: str,
    bootstrap_resamples: int,
    bootstrap_confidence: float,
    bootstrap_seed: int | None,
    entries: Sequence[dict[str, Any]] | None = None,
    label_field: str = "author",
) -> dict[str, Any] | None:
    """Aggregate weighted-Burrows-Delta across families using
    FAMILY_WEIGHTS and OVERALL_FAMILY_DELTA_CAP. Mirrors how
    voice_distance.py builds its overall score from per-family deltas."""
    weighted: list[float] = []
    labels: list[int] = []
    weight_sum = sum(
        FAMILY_WEIGHTS.get(fam, 1.0)
        for fam in {fam for p in pairs for fam in p["distances"]}
    )
    if weight_sum <= 0:
        return None
    for p in pairs:
        contribs: list[tuple[float, float]] = []
        for fam, metrics in p["distances"].items():
            d = metrics.get("burrows_delta")
            if d is None:
                continue
            capped = min(d, OVERALL_FAMILY_DELTA_CAP)
            contribs.append((FAMILY_WEIGHTS.get(fam, 1.0), capped))
        if not contribs:
            continue
        total_w = sum(w for w, _ in contribs)
        if total_w <= 0:
            continue
        score = sum(w * d for w, d in contribs) / total_w
        weighted.append(score)
        labels.append(0 if p["same_author"] else 1)
    if len(weighted) < 2 or len(set(labels)) < 2:
        return None
    auc = auc_score(labels, weighted)
    ap = ap_score(labels, weighted)

    # Distinct namespace from per-family ranking so the weighted-
    # family RNG sequence is independent (and reproducible).
    rng = random.Random(
        _stable_seed(bootstrap_seed, "_weighted_family", bootstrap_method)
    )
    if bootstrap_method == "document_cluster" and entries is not None:
        # Re-derive: same docs, same labels, but recompute weighted score
        # from the original pairs each resample. Simpler to wrap by
        # injecting the weighted score as a pseudo-family/metric.
        weighted_pairs: list[dict[str, Any]] = []
        for p, w_score in zip(
            (q for q in pairs if any(
                q["distances"].get(fam, {}).get("burrows_delta") is not None
                for fam in q["distances"]
            )),
            weighted,
        ):
            weighted_pairs.append({
                "doc_a": p["doc_a"],
                "doc_b": p["doc_b"],
                "same_author": p["same_author"],
                "distances": {"_weighted": {"burrows_delta": w_score}},
            })
        ci = document_cluster_bootstrap_auc_ci(
            weighted_pairs, "_weighted", "burrows_delta",
            label_field=label_field,
            entries=entries,
            resamples=bootstrap_resamples,
            confidence=bootstrap_confidence,
            rng=rng,
        )
    else:
        ci = naive_pair_bootstrap_auc_ci(
            labels, weighted,
            resamples=bootstrap_resamples,
            confidence=bootstrap_confidence,
            rng=rng,
        )

    return {
        "metric": "weighted_burrows_delta",
        "auc": auc,
        "average_precision": ap,
        "auc_ci": ci,
        "n_pairs": len(weighted),
        "polarity_ok": auc is not None and auc >= 0.5,
        "weights": dict(FAMILY_WEIGHTS),
        "delta_cap": OVERALL_FAMILY_DELTA_CAP,
    }


# ---- Operating-point + claim license -------------------------------


def threshold_at_fpr(
    pairs: Sequence[dict[str, Any]],
    family: str,
    metric: str,
    fpr_target: float,
) -> dict[str, Any]:
    """Highest-TPR threshold whose empirical FPR <= fpr_target on the
    overall slice. Uses the same convention as
    validation_harness.choose_threshold_at_fpr but on pair scores.
    Threshold semantics: predict different-author when distance >
    threshold."""
    rows: list[tuple[float, int]] = []
    for p in pairs:
        d = p["distances"].get(family, {}).get(metric)
        if d is None:
            continue
        rows.append((d, 0 if p["same_author"] else 1))
    if not rows:
        return {
            "fpr_target": fpr_target,
            "available": False,
            "reason": "no scored pairs",
        }
    rows.sort()
    n_pos = sum(1 for _, y in rows if y == 1)
    n_neg = len(rows) - n_pos
    if n_pos == 0 or n_neg == 0:
        return {
            "fpr_target": fpr_target,
            "available": False,
            "reason": "single-class fixture; no operating point",
        }
    best: dict[str, Any] | None = None
    candidate_thresholds = [r[0] for r in rows] + [
        max(r[0] for r in rows) + 1.0
    ]
    for t in sorted(set(candidate_thresholds)):
        tp = fp = tn = fn = 0
        for d, y in rows:
            pred = 1 if d > t else 0
            if y == 1 and pred == 1: tp += 1
            elif y == 0 and pred == 1: fp += 1
            elif y == 0 and pred == 0: tn += 1
            else: fn += 1
        fpr = fp / n_neg if n_neg else 0.0
        tpr = tp / n_pos if n_pos else 0.0
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        if fpr <= fpr_target and (best is None or tpr > best["tpr"]):
            best = {
                "threshold": t,
                "fpr": fpr,
                "tpr": tpr,
                "precision": prec,
                "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            }
    if best is None:
        return {
            "fpr_target": fpr_target,
            "available": False,
            "reason": "no threshold satisfies FPR target",
        }
    return {
        "fpr_target": fpr_target,
        "available": True,
        "family": family,
        "metric": metric,
        "convention": (
            "predict different-author when distance > threshold"
        ),
        **best,
    }


def claim_license_block(operating_point: dict[str, Any] | None) -> dict[str, str]:
    if operating_point is None or not operating_point.get("available"):
        op_text = "No FPR target supplied; thresholded classification rates omitted."
    else:
        op_text = (
            f"Threshold selected at FPR target {operating_point['fpr_target']} "
            f"on {operating_point['family']} / {operating_point['metric']}."
        )
    return {
        "licenses": (
            "This report describes how SETEC's voice-distance feature "
            "machinery discriminated same-author from different-author "
            "document pairs on this manifest's labeled validation entries."
        ),
        "does_not_license": (
            "It does not certify authorship for any individual document, "
            "does not generalize outside this manifest, and does not "
            "publish a single aggregate accuracy number. Thresholded "
            "rates are only reported when an explicit FPR target is "
            "supplied."
        ),
        "operating_point": op_text,
    }


# ---- Main runner ---------------------------------------------------


def build_audit_payload(
    result: dict[str, Any],
    *,
    target_path: Any,
) -> dict[str, Any]:
    """Wrap voice_validation_harness's run_harness result in the
    schema_version 1.0 envelope per
    ``internal/SPEC_output_schema_unification.md``. The legacy
    claim_license dict (when present) is upgraded to structured via
    from_legacy.
    """
    available = not result.get("failed", False)
    metadata_keys = {"task_surface", "tool", "version"}
    results_payload = {
        k: v for k, v in result.items() if k not in metadata_keys
    }
    warnings: list[str] = []
    if result.get("failed"):
        warnings.append(result.get("reason", "harness failed"))
    legacy_cl = result.get("claim_license", {})
    if isinstance(legacy_cl, dict) and legacy_cl:
        structured = from_legacy(legacy_cl, task_surface=TASK_SURFACE)
    else:
        structured = ClaimLicense(
            task_surface=TASK_SURFACE,
            licenses=(
                "Voice-coherence validation: same-author vs. "
                "different-author pair discrimination via ROC AUC "
                "per feature family on labeled manifest entries."
            ),
            does_not_license=(
                "Generalization beyond the labeled corpus. The "
                "harness reports performance on the supplied "
                "manifest; per-register or per-domain performance "
                "outside that slice is not licensed."
            ),
        ) if available else None
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=0,
        baseline=None,
        results=results_payload if available else {},
        claim_license=structured,
        available=available,
        warnings=warnings,
    )


def run_harness(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest)

    validation = validate_manifest(str(manifest_path))
    issues = validation.get("issues") or []
    fatal = [i for i in issues if i.get("severity") == "error"]
    if fatal:
        return {
            "task_surface": TASK_SURFACE,
            "tool": TOOL_NAME,
            "manifest_path": str(manifest_path),
            "failed": True,
            "reason": "manifest validation found errors",
            "manifest_validation": validation,
        }

    entries_all = load_manifest_entries(manifest_path)
    selected_entries = [
        e for e in entries_all if _entry_uses(e, args.use)
    ]
    label_field = args.label_by
    missing_author = [
        e["id"] for e in selected_entries if not e.get(label_field)
    ]
    if missing_author:
        return {
            "task_surface": TASK_SURFACE,
            "tool": TOOL_NAME,
            "manifest_path": str(manifest_path),
            "failed": True,
            "reason": (
                f"selected entries missing required field {label_field!r}: "
                f"{missing_author}"
            ),
            "manifest_validation": validation,
        }
    if len(selected_entries) < 2:
        return {
            "task_surface": TASK_SURFACE,
            "tool": TOOL_NAME,
            "manifest_path": str(manifest_path),
            "failed": True,
            "reason": (
                f"need at least 2 selected entries to form pairs; "
                f"got {len(selected_entries)} with use={args.use!r}"
            ),
            "manifest_validation": validation,
        }

    items = build_feature_items(selected_entries)
    selected_features = select_feature_names(items, limits=DEFAULT_LIMITS)
    pairs = build_pairs(
        selected_entries, items, selected_features, label_field
    )

    n_same = sum(1 for p in pairs if p["same_author"])
    n_diff = len(pairs) - n_same

    overall_per_family = per_family_ranking(
        pairs,
        bootstrap_method=args.bootstrap_method,
        bootstrap_resamples=args.bootstrap_resamples,
        bootstrap_confidence=args.bootstrap_confidence,
        bootstrap_seed=args.bootstrap_seed,
        entries=selected_entries,
        label_field=label_field,
    )
    overall_weighted = weighted_family_ranking(
        pairs,
        bootstrap_method=args.bootstrap_method,
        bootstrap_resamples=args.bootstrap_resamples,
        bootstrap_confidence=args.bootstrap_confidence,
        bootstrap_seed=args.bootstrap_seed,
        entries=selected_entries,
        label_field=label_field,
    )

    operating_point: dict[str, Any] | None = None
    if args.fpr_target is not None and overall_per_family:
        # Pick the family/metric with the highest AUC for the
        # operating-point report.
        ranked = [
            r for r in overall_per_family
            if r["auc"] is not None
        ]
        if ranked:
            best = max(ranked, key=lambda r: r["auc"])
            operating_point = threshold_at_fpr(
                pairs, best["family"], best["metric"], args.fpr_target
            )

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "manifest_path": str(manifest_path),
        "use": args.use,
        "label_by": label_field,
        "n_selected_entries": len(selected_entries),
        "n_pairs": len(pairs),
        "n_same_author": n_same,
        "n_different_author": n_diff,
        "bootstrap_method": args.bootstrap_method,
        "bootstrap_resamples": args.bootstrap_resamples,
        "bootstrap_confidence": args.bootstrap_confidence,
        "selected_feature_counts": {
            fam: len(names) for fam, names in selected_features.items()
        },
        "slices": {
            "overall": {
                "per_family_ranking": overall_per_family,
                "weighted_family_ranking": overall_weighted,
                "bootstrap_note": (
                    "On a tiny labeled fixture like Federalist (6 docs / "
                    "15 pairs), AUC CIs are wide and the smoke run is a "
                    "regression sanity check, not a calibration study."
                ),
            },
        },
        "operating_point": operating_point,
        "claim_license": claim_license_block(operating_point),
        "pairs": pairs,
        "manifest_validation": validation,
    }


# ---- Markdown rendering -------------------------------------------


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "--"
    if isinstance(value, float) and not math.isfinite(value):
        return "--"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _fmt_ci(ci: dict[str, Any] | None) -> str:
    if not ci or ci.get("lower") is None or ci.get("upper") is None:
        return "--"
    return f"[{_fmt(ci['lower'])}, {_fmt(ci['upper'])}]"


def render_report(result: dict[str, Any]) -> str:
    if result.get("failed"):
        lines = [
            "# SETEC Voice-Coherence Validation Harness",
            "",
            f"**Task surface:** `{TASK_SURFACE}`",
            f"**Manifest:** {result.get('manifest_path')}",
            "",
            "Harness did not run.",
            "",
            f"Reason: {result.get('reason', 'unknown failure')}",
        ]
        return "\n".join(lines) + "\n"

    overall = result["slices"]["overall"]
    per_family = overall["per_family_ranking"]
    weighted = overall["weighted_family_ranking"]
    operating_point = result.get("operating_point")
    license_block = result["claim_license"]

    lines = [
        "# SETEC Voice-Coherence Validation Harness",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Manifest:** `{result['manifest_path']}`",
        f"**Use slice:** `{result['use']}`",
        f"**Label field:** `{result['label_by']}`",
        f"**Pairs:** {result['n_pairs']} "
        f"({result['n_same_author']} same-author, "
        f"{result['n_different_author']} different-author)",
        f"**Bootstrap method:** `{result['bootstrap_method']}` "
        f"({result['bootstrap_resamples']} resamples at "
        f"{int(round(result['bootstrap_confidence'] * 100))}% confidence)",
        "",
        "## Per-Family Discrimination",
        "",
        "Score = pair distance; positive class = different-author "
        "(higher distance ↔ higher predicted difference). AUC > 0.5 "
        "means the family separates authors in the expected direction.",
        "",
        "| Family | Metric | AUC | AUC CI | AP | n pairs | Polarity |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in per_family:
        polarity = "OK" if row["polarity_ok"] else "INVERTED"
        lines.append(
            f"| `{row['family']}` | `{row['metric']}` | "
            f"{_fmt(row['auc'])} | {_fmt_ci(row['auc_ci'])} | "
            f"{_fmt(row['average_precision'])} | {row['n_pairs']} | "
            f"{polarity} |"
        )

    if weighted:
        lines.extend([
            "",
            "## Weighted-Family Aggregate",
            "",
            "Weighted Burrows-Delta across families using "
            "`FAMILY_WEIGHTS` and the production "
            "`OVERALL_FAMILY_DELTA_CAP`. Mirrors the overall-score "
            "shape of `voice_distance.py`. Per-family table above is "
            "the load-bearing diagnostic; this row is a compact summary.",
            "",
            f"- AUC: {_fmt(weighted['auc'])}",
            f"- AUC CI: {_fmt_ci(weighted['auc_ci'])}",
            f"- AP: {_fmt(weighted['average_precision'])}",
            f"- n pairs: {weighted['n_pairs']}",
        ])

    lines.extend([
        "",
        "## Operating Point",
        "",
        license_block["operating_point"],
        "",
    ])
    if operating_point and operating_point.get("available"):
        lines.extend([
            f"- Family / metric: `{operating_point['family']}` / "
            f"`{operating_point['metric']}`",
            f"- Threshold: {_fmt(operating_point['threshold'])}",
            f"- TPR (different-author detected): {_fmt(operating_point['tpr'])}",
            f"- FPR: {_fmt(operating_point['fpr'])}",
            f"- Precision: {_fmt(operating_point['precision'])}",
            f"- Counts: TP={operating_point['tp']} "
            f"FP={operating_point['fp']} TN={operating_point['tn']} "
            f"FN={operating_point['fn']}",
            "",
        ])

    structured = from_legacy(license_block, task_surface=TASK_SURFACE)
    structured.comparison_set = {
        "manifest": result.get("manifest_path"),
        "n_pairs": result.get("n_pairs"),
        "n_same_author": result.get("n_same_author"),
        "n_different_author": result.get("n_different_author"),
        "label_by": result.get("label_by"),
        "bootstrap_method": result.get("bootstrap_method"),
    }
    if operating_point and operating_point.get("available"):
        structured.fpr_target = operating_point.get("fpr_target")
    lines.extend([
        structured.render_block().rstrip(),
        "",
        "## Notes",
        "",
        overall["bootstrap_note"],
        "",
    ])
    return "\n".join(lines) + "\n"


# ---- CLI ----------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Voice-coherence validation harness: per-pair voice "
            "distance with per-family ROC AUC + bootstrap CI."
        )
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--use", default="voice_validation")
    parser.add_argument("--json", dest="json_path")
    parser.add_argument("--md", dest="md_path")
    parser.add_argument(
        "--label-by", default="author", choices=("author", "persona"),
    )
    parser.add_argument(
        "--bootstrap-method",
        default="document_cluster",
        choices=("document_cluster", "naive_pair"),
    )
    parser.add_argument("--bootstrap-resamples", type=int, default=2000)
    parser.add_argument("--bootstrap-confidence", type=float, default=0.95)
    parser.add_argument("--bootstrap-seed", type=int, default=None)
    parser.add_argument("--fpr-target", type=float, default=None)
    args = parser.parse_args(argv)

    result = run_harness(args)
    md = render_report(result)

    if args.json_path:
        envelope = build_audit_payload(
            result, target_path=args.manifest,
        )
        Path(args.json_path).write_text(
            json.dumps(envelope, indent=2, default=str), encoding="utf-8"
        )
    if args.md_path:
        Path(args.md_path).write_text(md, encoding="utf-8")
    if not args.json_path and not args.md_path:
        sys.stdout.write(md)

    if result.get("failed"):
        sys.stderr.write(f"voice_validation_harness: {result['reason']}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
