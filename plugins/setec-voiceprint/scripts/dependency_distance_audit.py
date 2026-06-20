#!/usr/bin/env python3
"""dependency_distance_audit.py — the distribution of dependency distances (spec 24).

A descriptive **syntactic-shape** profile: the linear span of each syntactic link,
`d = |token.i - token.head.i|`. The *scalar* MDD (per-sentence mean + SD) already ships as
`variance_audit.mdd_stats` (the `mdd_sd` signal) and is **reused** here, never re-implemented. The
new, additive contribution is the **distribution** (the subject of arXiv:2211.14620): the
dependency-distance histogram, the adjacent-link share (d=1), and the long-range tail (d>=K).

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
            "(d=1) and long-range (d>=threshold) shares — plus the per-sentence MDD mean/SD reused "
            "from mdd_stats. A descriptive syntactic-complexity profile."
        ),
        "does_not_license": (
            "Any AI/human or authorship verdict; any writing-quality or readability judgment; "
            "cross-language comparison (MDD norms are language-specific); a length-controlled "
            "reading — MDD and the long-range share covary mechanically with sentence length and "
            "genre (mean_sentence_length is co-reported so the confound is visible). No verdict; "
            "thresholds operator-side / PROVISIONAL."
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
