#!/usr/bin/env python3
"""rank_turbulence_audit.py — interpretable per-word stylometric divergence (spec 23, M1).

Rank-Turbulence Divergence (RTD) between a target and a baseline corpus, restricted by default to
the function-word distribution (so it measures *style*, not topic). Where `voice_distance` reports
one aggregate Burrows Delta, RTD's per-word contributions say WHICH words drive the difference.
Clean-room reimplementation of RTD (Dodds et al. 2020, *Allotaxonometry and rank-turbulence
divergence*; stylometric-Delta adaptation arXiv:2604.19499). Pure stdlib, deterministic, no model.

`rtd` is the normalized ratio Σ_actual / Σ_disjoint ∈ [0,1] (0 iff the two function-word rank
distributions are identical; 1 iff disjoint). Additive companion to `voice_distance` — does NOT
modify it. Posture (no verdict): a high RTD is NOT 'different author' or 'AI'; topic/genre/length
shift it (especially in `--all-words` mode). Thresholds operator-side / PROVISIONAL.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_schema import build_error_output, build_output  # noqa: E402
from claim_license import from_legacy  # noqa: E402
from stylometry_core import FUNCTION_WORDS  # noqa: E402

TASK_SURFACE = "voice_coherence"
TOOL_NAME = "rank_turbulence_audit"
SCRIPT_VERSION = "1.0"

DEFAULT_ALPHA = 1.0 / 3.0
DEFAULT_TOP_K = 20
_TOKEN = re.compile(r"[a-z]+")


def _counts(text: str, function_words_only: bool) -> Counter:
    toks = _TOKEN.findall(text.lower())
    if function_words_only:
        toks = [t for t in toks if t in FUNCTION_WORDS]
    return Counter(toks)


def _competition_ranks(counts: Counter) -> dict[str, float]:
    """Competition '1224' ranking: words sorted by frequency desc (ties by word for
    determinism); equal-frequency words share the rank of the first in the tie block."""
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ranks: dict[str, float] = {}
    prev_count = None
    rank = 0
    for idx, (word, count) in enumerate(items):
        if count != prev_count:
            rank = idx + 1
            prev_count = count
        ranks[word] = float(rank)
    return ranks


def _tie_extended(k_present: int, a_absent: int) -> float:
    """Average rank of the bottom tie block (the a_absent words missing from a system that
    has k_present distinct words): ranks k+1 .. k+a, averaged."""
    return k_present + (a_absent + 1) / 2.0 if a_absent else float(k_present)


def _bare(r1: float, r2: float, alpha: float) -> float:
    return abs(r1 ** (-alpha) - r2 ** (-alpha)) ** (1.0 / (alpha + 1.0))


def audit_rank_turbulence(target_text: str, baseline_text: str, *,
                          alpha: float = DEFAULT_ALPHA, top_k: int = DEFAULT_TOP_K,
                          function_words_only: bool = True) -> dict[str, Any]:
    """Compute RTD + per-word contributions. Raises ValueError on an empty target or baseline
    distribution (the caller maps that to bad_input). `rtd = Σ_actual / Σ_disjoint ∈ [0,1]`."""
    tc = _counts(target_text, function_words_only)
    bc = _counts(baseline_text, function_words_only)
    if not tc:
        raise ValueError("target has no countable words (function-word distribution is empty)")
    if not bc:
        raise ValueError("baseline has no countable words (function-word distribution is empty)")

    r1 = _competition_ranks(tc)
    r2 = _competition_ranks(bc)
    union = set(r1) | set(r2)
    k1, k2 = len(r1), len(r2)
    a1, a2 = len(union) - k1, len(union) - k2          # words absent from target / baseline
    tie1, tie2 = _tie_extended(k1, a1), _tie_extended(k2, a2)

    # Actual numerator + per-word contributions (bare summands).
    contributions: list[tuple[str, float, float, float]] = []   # (word, rr1, rr2, bare)
    numerator = 0.0
    for w in union:
        rr1 = r1.get(w, tie1)
        rr2 = r2.get(w, tie2)
        b = _bare(rr1, rr2, alpha)
        numerator += b
        contributions.append((w, rr1, rr2, b))

    # Disjoint normalization: the divergence if the two systems shared no words (max for the
    # given sizes). Each system's words paired against the other system's tie-extended rank.
    dr2 = k2 + (k1 + 1) / 2.0       # rank a target word would take in a disjoint baseline of size k2
    dr1 = k1 + (k2 + 1) / 2.0
    n_denom = (sum(_bare(rr, dr2, alpha) for rr in r1.values())
               + sum(_bare(dr1, rr, alpha) for rr in r2.values()))
    rtd = numerator / n_denom if n_denom > 0 else 0.0

    def _top(direction_target: bool) -> list[dict[str, Any]]:
        # over-ranked in target = more frequent there = lower rank number (rr1 < rr2)
        picked = [c for c in contributions if (c[1] < c[2]) == direction_target and c[1] != c[2]]
        picked.sort(key=lambda c: (-c[3], c[0]))      # by contribution desc, then word
        return [{"word": w, "rank_target": round(rr1, 3), "rank_baseline": round(rr2, 3),
                 "contribution": round(b, 6)} for (w, rr1, rr2, b) in picked[:top_k]]

    return {
        "rtd": round(rtd, 6),
        "alpha": alpha,
        "mode": "function_words" if function_words_only else "all_words",
        "n_vocab": len(union),
        "numerator": round(numerator, 6),
        "n_denom": round(n_denom, 6),
        "top_target": _top(True),
        "top_baseline": _top(False),
        "target_tokens": sum(tc.values()),
        "assumptions": {
            "method": "rank-turbulence divergence (Dodds et al. 2020; arXiv:2604.19499); "
                      "rtd = Sigma_actual / Sigma_disjoint in [0,1]",
            "mode": "function_words" if function_words_only else "all_words",
            "alpha": alpha,
            "tie_rule": "competition (1224); absent words take the tie-extended bottom rank",
            "topic_caveat": ("all_words mode is TOPICAL not stylometric — RTD over content words "
                             "moves with topic" if not function_words_only else
                             "function-word distribution: topic-robust, stylometric"),
        },
    }


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "A rank-based divergence (rank-turbulence divergence, in [0,1]) between the target's "
            "and the named baseline's word-rank distributions — by default the function-word "
            "distribution — with per-word contributions identifying which words drive it."
        ),
        "does_not_license": (
            "Any AI/human or authorship determination: a high RTD is NOT 'different author' and "
            "NOT 'AI'. It is a lexical-distribution divergence; topic, genre, and length shift it "
            "(especially in --all-words mode, which is topical, not stylometric). It is not a "
            "verdict; thresholds are operator-side / PROVISIONAL."
        ),
    }


def _load_baseline(args: argparse.Namespace, target_resolved: Path) -> tuple[str, int, int]:
    """Concatenated baseline text, doc count, and dropped-self count. Self-exclusion: any
    baseline file resolving to the target path is dropped (mirrors voice_distance)."""
    texts: list[str] = []
    dropped = 0
    if args.reference_dir or args.baseline_dir:
        root = Path(args.reference_dir or args.baseline_dir)
        for p in sorted(x for x in root.rglob("*") if x.is_file()):
            if p.suffix.lower() not in (".txt", ".md"):
                continue
            if p.resolve() == target_resolved:
                dropped += 1
                continue
            texts.append(p.read_text(encoding="utf-8", errors="replace"))
    else:
        base = Path(args.manifest).resolve().parent
        for raw in Path(args.manifest).read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row.get("text"), str):
                texts.append(row["text"])
                continue
            rel = row.get("text_path") or row.get("path")
            if rel:
                fp = (base / rel)
                if fp.is_file():
                    if fp.resolve() == target_resolved:
                        dropped += 1
                        continue
                    texts.append(fp.read_text(encoding="utf-8", errors="replace"))
    return "\n\n".join(texts), len(texts), dropped


def _run(args: argparse.Namespace) -> dict[str, Any]:
    target_path = Path(args.target)
    try:
        target_text = target_path.read_text(encoding="utf-8")
    except OSError as e:
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
                                  target_path=str(target_path),
                                  reason=f"cannot read --target: {e}", reason_category="bad_input")
    # A missing/unreadable baseline dir or manifest is bad INPUT, not a crash (#226 P2):
    # _load_baseline calls read_text(), which raises OSError on a missing path.
    try:
        baseline_text, n_docs, dropped = _load_baseline(args, target_path.resolve())
    except OSError as e:
        which = "--reference-dir/--baseline-dir" if (args.reference_dir or args.baseline_dir) else "--manifest"
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
                                  target_path=str(target_path),
                                  reason=f"cannot read {which}: {e}", reason_category="bad_input")
    if not baseline_text.strip():
        why = ("baseline empty after dropping the target (self-exclusion)" if dropped
               else "baseline corpus is empty")
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
                                  target_path=str(target_path), reason=why, reason_category="bad_input")
    try:
        results = audit_rank_turbulence(target_text, baseline_text, alpha=args.alpha,
                                        top_k=args.top_k, function_words_only=not args.all_words)
    except ValueError as e:
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
                                  target_path=str(target_path), reason=str(e), reason_category="bad_input")

    results["n_baseline_docs"] = n_docs
    results["assumptions"]["dropped_self"] = dropped
    warnings = []
    if dropped:
        warnings.append(f"dropped {dropped} baseline doc(s) identical to the target path (self-exclusion)")
    if args.all_words:
        warnings.append("--all-words mode is TOPICAL, not stylometric (RTD over content words moves with topic)")

    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=str(target_path), target_words=results["target_tokens"],
        baseline={"reference": args.reference_dir or args.baseline_dir or args.manifest,
                  "n_baseline_docs": n_docs},
        results=results, claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        warnings=warnings or None,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", required=True, help="Path to the target text.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--baseline-dir", help="Directory of baseline texts (.txt/.md, recursive).")
    g.add_argument("--reference-dir", help="Alias for --baseline-dir.")
    g.add_argument("--manifest", help="JSONL baseline manifest (id + text|text_path).")
    ap.add_argument("--alpha", type=float, default=DEFAULT_ALPHA,
                    help=f"RTD alpha (default {DEFAULT_ALPHA:.4f}; smaller emphasizes rare words).")
    ap.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="Top contributors per direction.")
    ap.add_argument("--all-words", action="store_true",
                    help="Use the FULL vocabulary (TOPICAL, not stylometric) instead of function words only.")
    ap.add_argument("--json", action="store_true", help="Emit the JSON envelope to stdout.")
    ap.add_argument("--out", help="Write the JSON envelope to this path.")
    args = ap.parse_args(argv)

    if args.alpha <= 0:
        sys.stderr.write("[rank_turbulence_audit] --alpha must be > 0\n")
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
