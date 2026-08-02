#!/usr/bin/env python3
"""Pure stylometric distance primitives.

This module is deliberately feature-extraction free.  Consumers that already
hold normalized feature maps can compute the same distances as
``stylometry_core`` without importing parser/model code.
"""

from __future__ import annotations

import math
import statistics
from typing import Any


CLUSTER_DIRECTIONAL_CONSISTENCY = 0.7
CLUSTER_DIRECTIONAL_MIN_FEATURES = 3


def safe_mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def safe_sd(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def feature_vector(item: dict[str, Any], family: str, names: list[str]) -> dict[str, float]:
    data = item.get("features", {}).get(family, {})
    return {name: float(data.get(name, 0.0)) for name in names}


def vector_stats(vectors: list[dict[str, float]], names: list[str]) -> dict[str, dict[str, float]]:
    stats: dict[str, dict[str, float]] = {}
    for name in names:
        values = [float(v.get(name, 0.0)) for v in vectors]
        stats[name] = {
            "mean": safe_mean(values),
            "sd": safe_sd(values),
            "n": len(values),
        }
    return stats


def cosine_distance(a: dict[str, float], b: dict[str, float], names: list[str]) -> float | None:
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for name in names:
        av = float(a.get(name, 0.0))
        bv = float(b.get(name, 0.0))
        dot += av * bv
        norm_a += av * av
        norm_b += bv * bv
    if norm_a == 0 or norm_b == 0:
        return None
    return 1.0 - (dot / (math.sqrt(norm_a) * math.sqrt(norm_b)))


def compute_clusters(
    deviations: list[dict[str, Any]],
    cluster_defs: dict[str, set[str]],
    *,
    min_features: int = 2,
) -> list[dict[str, Any]]:
    """Aggregate per-feature deviations into named clusters.

    Each cluster reports mean signed z, mean absolute z, directional
    consistency (fraction of matched features moving the same way), the
    cluster's predominant direction, and the top contributing features by
    absolute z. The group-level view catches authorial fingerprints that
    the per-feature top-N report misses when each feature in the group is
    individually below conventional flagging thresholds but the group as
    a whole drifts together.

    Clusters with fewer than `min_features` matched in the deviations
    input are skipped. A cluster is flagged `directional` when at least
    70% of its matched features pull the same way and the cluster has
    at least three matched features.

    The 70% threshold is a heuristic, not a calibrated cutoff, and has
    visible step effects: a 3-feature cluster becomes directional only
    at 3/3 (100%), a 4-feature cluster at 3/4 (75%), and a 5-feature
    cluster at 4/5 (80%). Treat the flag as a triage hint until the
    validation harness pins down per-register thresholds.

    Returns a list sorted with directional clusters first, then by
    absolute mean signed z descending. Suitable for a markdown table.
    """
    by_name = {d["feature"]: d for d in deviations}
    out: list[dict[str, Any]] = []
    for cluster_name, members in cluster_defs.items():
        matched = [
            by_name[m] for m in members
            if m in by_name and by_name[m].get("z") is not None
        ]
        if len(matched) < min_features:
            continue
        signed = [float(d["z"]) for d in matched]
        abs_signed = [abs(z) for z in signed]
        n = len(signed)
        positives = sum(1 for z in signed if z > 0)
        negatives = sum(1 for z in signed if z < 0)
        majority = max(positives, negatives)
        consistency = majority / n if n else 0.0
        mean_signed = sum(signed) / n
        net_signed = sum(signed)
        mean_abs = sum(abs_signed) / n
        max_abs = max(abs_signed)
        directional = (
            consistency >= CLUSTER_DIRECTIONAL_CONSISTENCY
            and n >= CLUSTER_DIRECTIONAL_MIN_FEATURES
        )
        # Direction reports the majority sign so it cannot contradict the
        # directional flag. mean_signed_z carries the magnitude summary;
        # readers who care about an outlier of opposite sign can read it
        # off top_features. The earlier mean-based direction could flip
        # when one large outlier overwhelmed several smaller features
        # pointing the other way, contradicting "predominant direction."
        if positives > negatives:
            direction = "high"
        elif negatives > positives:
            direction = "low"
        else:
            direction = "flat"
        top = sorted(matched, key=lambda d: abs(float(d["z"])), reverse=True)[:3]
        out.append({
            "cluster": cluster_name,
            "n_in_cluster": len(members),
            "n_matched": n,
            "mean_signed_z": mean_signed,
            "net_signed_z": net_signed,
            "mean_abs_z": mean_abs,
            "max_abs_z": max_abs,
            "direction_consistency": consistency,
            "direction": direction,
            "directional": directional,
            "top_features": [
                {"feature": d["feature"], "z": d["z"], "value": d["value"]}
                for d in top
            ],
        })
    out.sort(key=lambda c: (not c["directional"], -abs(c["mean_signed_z"])))
    return out


def family_distance(
    target: dict[str, Any],
    baseline_items: list[dict[str, Any]],
    family: str,
    names: list[str],
    *,
    clusters: dict[str, set[str]] | None = None,
    cluster_min_features: int = 2,
) -> dict[str, Any]:
    """Compute the established per-family Burrows Delta and diagnostics."""
    target_vec = feature_vector(target, family, names)
    baseline_vectors = [feature_vector(item, family, names) for item in baseline_items]
    stats = vector_stats(baseline_vectors, names)

    deviations = []
    z_values = []
    for name in names:
        info = stats[name]
        value = target_vec.get(name, 0.0)
        z = None
        if info["sd"] > 0:
            z = (value - info["mean"]) / info["sd"]
            z_values.append(abs(z))
        deviations.append({
            "feature": name,
            "value": value,
            "baseline_mean": info["mean"],
            "baseline_sd": info["sd"],
            "z": z,
            "abs_z": abs(z) if z is not None else None,
        })

    centroid = {name: stats[name]["mean"] for name in names}
    cosine_to_centroid = cosine_distance(target_vec, centroid, names)
    baseline_cosines = [
        c for c in (cosine_distance(target_vec, vec, names) for vec in baseline_vectors)
        if c is not None
    ]

    cluster_results: list[dict[str, Any]] | None = None
    if clusters:
        cluster_results = compute_clusters(
            deviations, clusters, min_features=cluster_min_features
        )

    deviations.sort(
        key=lambda x: x["abs_z"] if x["abs_z"] is not None else -1.0,
        reverse=True,
    )
    out: dict[str, Any] = {
        "n_features": len(names),
        "burrows_delta": safe_mean(z_values),
        "cosine_distance_to_centroid": cosine_to_centroid,
        "cosine_distance_to_baseline_mean": safe_mean(baseline_cosines),
        "cosine_distance_to_baseline_min": min(baseline_cosines) if baseline_cosines else None,
        "top_deviations": deviations[:25],
    }
    if cluster_results is not None:
        out["clusters"] = cluster_results
    return out
