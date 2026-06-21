#!/usr/bin/env python3
"""dependency_distance_audit.py — the distribution of dependency distances (spec 24).

A descriptive **syntactic-shape** profile: the linear span of each syntactic link,
`d = |token.i - token.head.i|`. The *scalar* MDD (per-sentence mean + SD) already ships as
`variance_audit.mdd_stats` (the `mdd_sd` signal) and is **reused** here, never re-implemented. The
new, additive contribution is the **distribution** (the subject of arXiv:2211.14620): the
dependency-distance histogram, the adjacent-link share (d=1), the long-range tail (d>=K), and the
**shape** descriptors of the pooled per-link distribution (`results["shape"]`): population
variance/sd, Fisher-Pearson skewness (g1) and excess kurtosis (g2), and tail quantiles
(p50/p90/p99/max). The shape `sd` is the within-POOL per-link SD — a DIFFERENT quantity from the
reused `mdd_sd` (the across-SENTENCE SD of per-sentence means). All stdlib, no new model dependency.

Parser-tier: reuses the shared spaCy pipeline (`variance_audit._NLP` / `HAS_SPACY` / `en_core_web_sm`).
There is no faithful parse-free dependency distance, so without the parser the surface ABSTAINS
(`available:false` / `missing_dependency`) — the general_imposters whole-surface pattern. Posture
(no verdict): not authorship/AI, not a quality/readability score, and NOT length-controlled — MDD and
the long-range share covary with sentence length (so `mean_sentence_length` is co-reported). Thresholds
operator-side / PROVISIONAL.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_schema import build_error_output, build_output  # noqa: E402
from claim_license import from_legacy  # noqa: E402
from variance_audit import HAS_SPACY, _NLP, mdd_stats  # noqa: E402

TASK_SURFACE = "voice_coherence"
TOOL_NAME = "dependency_distance_audit"
SCRIPT_VERSION = "1.0"

DEFAULT_LONG_THRESHOLD = 7
DEFAULT_MAX_BUCKET = 15


def _nearest_rank_quantile(sorted_d: list[int], q: float) -> float:
    """Nearest-rank percentile of an ascending-sorted, non-empty list (q in [0, 1]).
    rank = ceil(q * N), clamped to [1, N]; index = rank - 1. Returns the value as a float."""
    n = len(sorted_d)
    rank = max(1, min(n, math.ceil(q * n)))
    return float(sorted_d[rank - 1])


def _distance_shape(distances: list[int]) -> dict[str, Any]:
    """Distribution-SHAPE descriptors of the POOLED per-link distance list (arXiv:2211.14620):
    population variance/sd, Fisher-Pearson moment skewness (g1) and excess kurtosis (g2), and
    nearest-rank tail quantiles (p50/p90/p99/max). Pure stdlib (statistics + arithmetic) over the one
    `distances` list the audit already builds — NO numpy/scipy, NO re-parse, model-free (CI-runnable).

    Population moments (N, not N-1): this is a descriptive summary of THIS document's observed
    distribution, not a sample estimate of a super-population. `sd` here is the within-POOL per-link
    SD — a DIFFERENT quantity from `mdd_sd` (the across-SENTENCE SD of per-sentence MDD means).

    Degenerate handling (no fabricated values): `skewness` / `excess_kurtosis` are `None` (not 0.0)
    when `sd == 0` (all distances equal -> standardized moments undefined) or `n_links < 3` (the
    third/fourth standardized moments are undefined for n < 3). `variance` / `sd` / `quantiles` stay
    defined (0.0 / the single value) for any non-empty list. Raises ValueError on an empty list (the
    caller guarantees >= 1 link, so this is a defensive guard, never reached in the normal path)."""
    n = len(distances)
    if n == 0:
        raise ValueError("_distance_shape: empty distances list")

    mean = sum(distances) / n
    # Central moments m2/m3/m4 (population): m_k = mean((d - mean)^k).
    m2 = sum((d - mean) ** 2 for d in distances) / n
    variance = m2                                   # statistics.pvariance(distances), inlined for one pass
    sd = variance ** 0.5

    if n < 3 or sd == 0.0:
        skewness: float | None = None
        excess_kurtosis: float | None = None
    else:
        m3 = sum((d - mean) ** 3 for d in distances) / n
        m4 = sum((d - mean) ** 4 for d in distances) / n
        skewness = m3 / (sd ** 3)                   # Fisher-Pearson g1 (population)
        excess_kurtosis = m4 / (m2 ** 2) - 3.0      # g2 (excess; normal -> 0)

    sorted_d = sorted(distances)
    quantiles = {
        "p50": _nearest_rank_quantile(sorted_d, 0.50),
        "p90": _nearest_rank_quantile(sorted_d, 0.90),
        "p99": _nearest_rank_quantile(sorted_d, 0.99),
        "max": float(sorted_d[-1]),
    }

    return {
        "variance": round(variance, 6),
        "sd": round(sd, 6),
        "skewness": (round(skewness, 6) if skewness is not None else None),
        "excess_kurtosis": (round(excess_kurtosis, 6) if excess_kurtosis is not None else None),
        "quantiles": quantiles,
        "n_links": n,                               # == results["n_links"]; echoed so shape self-describes
        "assumptions": {
            "population": "pooled per-link distances (NOT per-sentence means); population moments "
                          "(N, not N-1)",
            "distinct_from_mdd_sd": "mdd_sd is the across-SENTENCE SD of per-sentence MDD means; "
                                    "shape.sd is the within-POOL SD of per-LINK distances — a "
                                    "different quantity",
            "skew_kurtosis": "Fisher-Pearson g1 / excess g2; right-skew (g1>0) and heavy tail (g2>0) "
                             "are the expected DDD shape (arXiv:2211.14620), reported descriptively "
                             "with no band",
            "degenerate": "skewness/excess_kurtosis are null when sd==0 or n_links<3 (undefined "
                          "moments), not 0.0 — absence is reported as null, never a fabricated value",
            "quantiles": "nearest-rank percentiles of the pooled per-link distances; more robust to "
                         "sentence COUNT than a share, but NOT to sentence LENGTH",
        },
    }


def audit_dependency_distance(text: str, *, long_threshold: int = DEFAULT_LONG_THRESHOLD,
                              max_bucket: int = DEFAULT_MAX_BUCKET) -> dict[str, Any]:
    """The dependency-distance distribution of `text`. Link set is pinned to match
    `variance_audit.mdd_stats` (punctuation kept; ROOT/self-links excluded), so the new histogram is
    consistent with the reused scalar MDD. Raises ValueError if no dependency links are found (the
    caller maps that to bad_input). Assumes the parser is available (the caller guards HAS_SPACY)."""
    doc = _NLP(text)
    distances: list[int] = []
    sentence_lengths: list[int] = []
    for sent in doc.sents:
        toks = [t for t in sent if not t.is_space]          # keep punctuation (match mdd_stats)
        if len(toks) < 2:
            continue
        sent_d = [abs(t.i - t.head.i) for t in toks if not (t.dep_ == "ROOT" or t.head is t)]
        if sent_d:
            distances.extend(sent_d)
            sentence_lengths.append(len(toks))
    if not distances:
        raise ValueError("no dependency links found (too short / unparseable)")

    counts = Counter(distances)
    histogram = {str(d): counts.get(d, 0) for d in range(1, max_bucket)}
    histogram[f">={max_bucket}"] = sum(v for d, v in counts.items() if d >= max_bucket)
    n_links = len(distances)

    # Scalars: REUSED from mdd_stats (per-sentence MDD mean/SD) — not re-derived.
    stats = mdd_stats(text) or {}

    return {
        "distance_histogram": histogram,
        "adjacent_share": round(histogram["1"] / n_links, 6),
        "long_range_share": round(sum(1 for d in distances if d >= long_threshold) / n_links, 6),
        "mdd_mean": round(stats.get("mean", 0.0), 6),
        "mdd_sd": round(stats.get("sd", 0.0), 6),
        "mean_sentence_length": round(statistics.mean(sentence_lengths), 4),
        "long_threshold": long_threshold,
        "n_links": n_links,
        "n_sentences": len(sentence_lengths),
        "n_tokens": sum(sentence_lengths),
        # Distribution-SHAPE descriptors of the pooled per-link distances (arXiv:2211.14620): the
        # geometry of the curve (variance/skew/kurtosis/tail-quantiles), DISTINCT from mdd_sd and the
        # histogram. Additive, descriptive, no verdict/band. Stdlib over the same `distances` list.
        "shape": _distance_shape(distances),
        "assumptions": {
            "method": "dependency-distance distribution (arXiv:2211.14620); d = |i - head.i|",
            "link_set": "punctuation kept (non-space); ROOT/self-links excluded — matches "
                        "variance_audit.mdd_stats",
            "scalars": "mdd_mean / mdd_sd reused verbatim from variance_audit.mdd_stats",
            "length_confound": "MDD and long_range_share covary with sentence length / genre; "
                               "mean_sentence_length co-reported, not length-controlled",
            "parser": "spaCy en_core_web_sm (shared variance_audit._NLP)",
        },
    }


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "The distribution of dependency distances of the target — the histogram, the adjacent "
            "(d=1) and long-range (d>=threshold) shares, and the SHAPE descriptors of the pooled "
            "per-link distribution (variance/sd, Fisher-Pearson skewness g1, excess kurtosis g2, and "
            "the p50/p90/p99/max tail quantiles) — plus the per-sentence MDD mean/SD reused from "
            "mdd_stats. A descriptive syntactic-complexity profile, reported as values with no band."
        ),
        "does_not_license": (
            "Any AI/human or authorship verdict; any writing-quality or readability judgment; "
            "cross-language comparison (MDD norms are language-specific); a length-controlled "
            "reading — MDD and the long-range share covary mechanically with sentence length and "
            "genre (mean_sentence_length is co-reported so the confound is visible). The shape "
            "moments are descriptive: skewness/excess_kurtosis are NOT a complexity *score* and "
            "NOT an AI signal — they are the observed geometry of this document's distance "
            "distribution, with no band and no baseline. No verdict; thresholds operator-side / "
            "PROVISIONAL."
        ),
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if not HAS_SPACY or _NLP is None:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=args.target,
            reason="dependency parsing needs spaCy + en_core_web_sm; install it to run this surface",
            reason_category="missing_dependency")
    target_path = Path(args.target)
    try:
        text = target_path.read_text(encoding="utf-8")
    except OSError as e:
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
                                  target_path=str(target_path),
                                  reason=f"cannot read target: {e}", reason_category="bad_input")
    try:
        results = audit_dependency_distance(text, long_threshold=args.long_threshold,
                                            max_bucket=args.max_bucket)
    except ValueError as e:
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
                                  target_path=str(target_path), reason=str(e),
                                  reason_category="bad_input")

    warnings = None
    if results["n_tokens"] < 150:
        warnings = [f"target parsed only {results['n_tokens']} tokens (< ~150); the dependency-"
                    "distance distribution may be unstable"]

    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=str(target_path), target_words=results["n_tokens"], baseline=None,
        results=results, claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        target_extra={"spacy_available": True}, warnings=warnings)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="Path to the target text.")
    ap.add_argument("--long-threshold", type=int, default=DEFAULT_LONG_THRESHOLD,
                    help=f"Distance counted as long-range (default {DEFAULT_LONG_THRESHOLD}).")
    ap.add_argument("--max-bucket", type=int, default=DEFAULT_MAX_BUCKET,
                    help=f"Histogram tail bucket (>= this; default {DEFAULT_MAX_BUCKET}).")
    ap.add_argument("--json", action="store_true", help="Emit the JSON envelope to stdout.")
    ap.add_argument("--out", help="Write the JSON envelope to this path.")
    args = ap.parse_args(argv)

    if args.max_bucket < 2 or args.long_threshold < 1:
        sys.stderr.write("[dependency_distance_audit] --max-bucket >= 2 and --long-threshold >= 1\n")
        return 2

    envelope = _run(args)
    text = json.dumps(envelope, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.json or not args.out:
        print(text)
    return 0 if envelope.get("available", True) else 3


if __name__ == "__main__":
    raise SystemExit(main())
