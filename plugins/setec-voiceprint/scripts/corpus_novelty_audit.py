#!/usr/bin/env python3
"""corpus_novelty_audit.py — the set-wide DJ-Search novelty *distribution* (spec 28, M1a).

Where `originality_audit` scores ONE target against a pool, this scores a *corpus* against itself:
for each document it runs DJ-Search reconstructibility against the rest of the corpus (leave-one-out),
then reports the **distribution** of that per-document originality — never a single "corpus originality
score" (a lone scalar is a verdict in disguise; the distribution + per-document table is the object the
human reads). Set-level axis (`set_level_diversity`), sibling to the shipped `originality_audit`.

It does NOT re-implement DJ Search: it imports `audit_originality` from `originality_audit` (the shipped
clean-room of *AI as Humanity's Salieri*, arXiv:2410.04265) and wraps it leave-one-out.

Posture (no verdict): low novelty = more reconstructible FROM THIS corpus, **NOT 'AI'** — a tight
prompt, a shared genre, a single source, or a house style all lower it legitimately. The claim license
refuses any AI/human, plagiarism, or selection determination; thresholds are operator-side / PROVISIONAL.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_schema import build_error_output, build_output  # noqa: E402
from claim_license import from_legacy  # noqa: E402
from originality_audit import (  # noqa: E402
    DEFAULT_MIN_NGRAM,
    _MAX_SPAN,
    _load_reference_dir,
    _load_reference_manifest,
    _tokens,
    audit_originality,
)

TASK_SURFACE = "set_level_diversity"
TOOL_NAME = "corpus_novelty_audit"
SCRIPT_VERSION = "1.0"

DEFAULT_MIN_DOCS = 3
DEFAULT_MUTUAL_SHARE = 0.5


def _distribution(values: list[float]) -> dict[str, Any]:
    """Descriptive {min,p25,median,p75,max,mean,sd} + decile histogram over `values`.

    Single-value `sd` is 0.0 (statistics.stdev needs >= 2 points and would raise / NaN otherwise —
    this protects the build_output R4 finiteness gate). `values` is guaranteed non-empty by the caller
    (the set floor abstains below --min-docs)."""
    ordered = sorted(values)
    n = len(ordered)

    def _quantile(q: float) -> float:
        # Linear-interpolation quantile (deterministic, stdlib). q in [0,1].
        if n == 1:
            return ordered[0]
        pos = q * (n - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        return ordered[lo] + (ordered[hi] - ordered[lo]) * frac

    # Fixed deciles 0.0–1.0 (10 buckets); the last bucket is closed on the right (1.0 lands in [0.9,1.0]).
    histogram = [0] * 10
    for v in ordered:
        idx = min(int(v * 10), 9)
        histogram[idx] += 1

    return {
        "min": round(ordered[0], 6),
        "p25": round(_quantile(0.25), 6),
        "median": round(statistics.median(ordered), 6),
        "p75": round(_quantile(0.75), 6),
        "max": round(ordered[-1], 6),
        "mean": round(statistics.fmean(ordered), 6),
        "sd": round(statistics.stdev(ordered), 6) if n >= 2 else 0.0,
        "histogram": histogram,
    }


def audit_corpus_novelty(
    loaded: list[tuple[str, str, Path | None]],
    *,
    min_ngram: int = DEFAULT_MIN_NGRAM,
    max_span: int = _MAX_SPAN,
    mutual_share: float = DEFAULT_MUTUAL_SHARE,
) -> dict[str, Any]:
    """Leave-one-out DJ-Search novelty distribution over a loaded corpus.

    `loaded` is the §S2 loader's `list[tuple[source, text, resolved_path]]` (3-tuples). For each doc i,
    its reference is the rest of the corpus with self-path duplicates dropped, stripped to the 2-tuples
    `audit_originality` expects (mirrors originality_audit.py:249-251). Deterministic. Raises ValueError
    if no document yields a usable target+reference (caller maps to bad_input)."""
    per_document: list[dict[str, Any]] = []
    originalities: list[float] = []
    total_dropped_self = 0
    # (a, b) -> True when B covers >= mutual_share of A. Census, not a per-pair field in the output.
    mutual_hits = 0
    mutual_total = 0

    for i, (src_i, text_i, abs_i) in enumerate(loaded):
        if not _tokens(text_i):
            # An empty-token target contributes no distribution point (no span can match); skip it
            # rather than crash. If EVERY doc is empty, originalities stays empty -> caller bad_input.
            continue
        # Self-exclusion: drop any doc whose resolved path equals this doc's (a doc appearing twice),
        # AND drop this exact index. Then strip 3-tuples -> 2-tuples for audit_originality.
        reference: list[tuple[str, str]] = []
        dropped_self_i = 0
        for j, (src_j, text_j, abs_j) in enumerate(loaded):
            if j == i:
                continue
            if abs_i is not None and abs_j is not None and abs_j == abs_i:
                dropped_self_i += 1
                continue
            reference.append((src_j, text_j))
        total_dropped_self += dropped_self_i
        # A doc whose entire reference has no tokens cannot be scored (audit_originality raises);
        # record it as fully novel (nothing reconstructs it) rather than abstaining the whole run.
        if not any(_tokens(t) for _, t in reference):
            r = {"coverage": 0.0, "originality": 1.0, "longest_match_tokens": 0,
                 "attribution": []}
        else:
            r = audit_originality(text_i, reference, min_ngram=min_ngram, max_span=max_span)
        attribution = r["attribution"]
        top_source = attribution[0]["source"] if attribution else None
        originalities.append(r["originality"])
        per_document.append({
            "id": src_i,
            "originality": r["originality"],
            "coverage": r["coverage"],
            "longest_match_tokens": r["longest_match_tokens"],
            # PRECISE definition (findings P3): the source of the single longest matched span, NOT
            # "the document that most reconstructs this one" (audit_originality has no per-source
            # coverage breakdown). null when nothing matched.
            "top_source": top_source,
        })

    if not originalities:
        raise ValueError("no document in the corpus has word tokens")

    # Mutual-reconstructibility census over ordered pairs (A,B): does B cover >= mutual_share of A?
    # B covers A = audit_originality(A, [B]).coverage. Descriptive count/fraction only — NO per-pair
    # boolean is emitted (findings P3: keeps the threshold from becoming a back-door gate).
    for i, (src_i, text_i, abs_i) in enumerate(loaded):
        if not _tokens(text_i):
            continue
        for j, (src_j, text_j, abs_j) in enumerate(loaded):
            if j == i:
                continue
            if abs_i is not None and abs_j is not None and abs_j == abs_i:
                continue
            if not _tokens(text_j):
                continue
            mutual_total += 1
            cov = audit_originality(text_i, [(src_j, text_j)],
                                    min_ngram=min_ngram, max_span=max_span)["coverage"]
            if cov >= mutual_share:
                mutual_hits += 1

    return {
        "n_documents": len(per_document),
        "per_document": per_document,
        "novelty_distribution": _distribution(originalities),
        "mutual_reconstructibility": {
            "n_ordered_pairs": mutual_total,
            "count": mutual_hits,
            "fraction": round(mutual_hits / mutual_total, 6) if mutual_total else 0.0,
        },
        "min_ngram": min_ngram,
        "assumptions": {
            "method": "DJ-Search leave-one-out over the corpus (arXiv:2410.04265)",
            "orientation": "low novelty = more reconstructible FROM THIS corpus, NOT 'AI' "
                           "(a tight prompt, shared genre, single source, or house style all "
                           "lower it legitimately)",
            "corpus_dependence": "reconstructibility is corpus- and register-dependent — a "
                                 "templated genre or a single-source pool inflates apparent "
                                 "homogenization; ESL/dialect is not adjudicated here",
            "self_exclusion": f"dropped {total_dropped_self} self-path duplicate(s) across the "
                              "leave-one-out iterations (a doc never reconstructs itself)",
            "dropped_self": total_dropped_self,
            # findings P3: the share threshold is SURFACED (operator-visible), not a verdict band.
            "mutual_share": mutual_share,
        },
    }


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "The distribution of DJ-Search reconstructibility across the named corpus (each "
            "document vs the rest, leave-one-out), with a per-document table — how mutually "
            "reconstructible the set is, reported as a {min/p25/median/p75/max/mean/sd + histogram} "
            "distribution over per-document `originality = 1 - coverage`."
        ),
        "does_not_license": (
            "Any AI/human determination (low novelty is NOT 'AI'; high novelty is NOT 'human' — a "
            "tight prompt, a shared genre, a single common source, or a house style all lower novelty "
            "legitimately). Any plagiarism, derivative-work, or copyright determination — this is a "
            "span-coverage measurement, not a legal claim. Any selection / ranking-as-decision of "
            "documents (the tables are read by the human, never an automated filter). Thresholds are "
            "operator-side / PROVISIONAL; the surface emits no verdict."
        ),
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    corpus_ref = args.corpus_dir or args.manifest
    try:
        if args.corpus_dir:
            loaded = _load_reference_dir(Path(args.corpus_dir))
        else:
            loaded = _load_reference_manifest(Path(args.manifest))
    except (OSError, UnicodeDecodeError) as e:
        which = "--corpus-dir" if args.corpus_dir else "--manifest"
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME,
                                  version=SCRIPT_VERSION, target_path=str(corpus_ref),
                                  reason=f"cannot read {which}: {e}", reason_category="bad_input")

    n_docs = len(loaded)
    if n_docs < args.min_docs:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(corpus_ref),
            reason=(f"corpus has {n_docs} document(s); the novelty distribution needs at least "
                    f"--min-docs ({args.min_docs}) — a distribution over 1-2 docs is meaningless"),
            reason_category="bad_input")

    try:
        results = audit_corpus_novelty(loaded, min_ngram=args.min_ngram, max_span=args.max_span,
                                       mutual_share=args.mutual_share)
    except ValueError as e:
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME,
                                  version=SCRIPT_VERSION, target_path=str(corpus_ref),
                                  reason=str(e), reason_category="bad_input")

    total_words = sum(len(_tokens(t)) for _, t, _ in loaded)
    warnings: list[str] = []
    dropped = results["assumptions"]["dropped_self"]
    if dropped:
        warnings.append(f"dropped {dropped} self-path duplicate(s) across leave-one-out iterations "
                        "(self-exclusion); no document reconstructs itself")
    warnings = warnings or None

    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=str(corpus_ref), target_words=total_words,
        baseline={"corpus": corpus_ref, "n_docs": n_docs},
        results=results, claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        warnings=warnings,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--corpus-dir", help="Directory of corpus texts (.txt/.md, recursive).")
    g.add_argument("--manifest", help="JSONL manifest of the corpus (id + text|text_path).")
    ap.add_argument("--min-ngram", type=int, default=DEFAULT_MIN_NGRAM,
                    help=f"Minimum verbatim span counted as reconstructed (default {DEFAULT_MIN_NGRAM}).")
    ap.add_argument("--max-span", type=int, default=_MAX_SPAN,
                    help=f"Cap on a single matched span (default {_MAX_SPAN}).")
    ap.add_argument("--min-docs", type=int, default=DEFAULT_MIN_DOCS,
                    help=f"Minimum corpus size for a distribution (default {DEFAULT_MIN_DOCS}); below "
                         "it the run abstains (bad_input).")
    ap.add_argument("--mutual-share", type=float, default=DEFAULT_MUTUAL_SHARE,
                    help="Coverage share for the mutual-reconstructibility census (default "
                         f"{DEFAULT_MUTUAL_SHARE}); a DESCRIPTIVE census threshold surfaced in "
                         "assumptions.mutual_share, NOT a verdict band.")
    ap.add_argument("--json", action="store_true", help="Emit the JSON envelope to stdout.")
    ap.add_argument("--out", help="Write the JSON envelope to this path.")
    args = ap.parse_args(argv)

    if args.min_ngram < 1:
        sys.stderr.write("[corpus_novelty_audit] --min-ngram must be >= 1\n")
        return 2
    if args.max_span < args.min_ngram:
        sys.stderr.write("[corpus_novelty_audit] --max-span must be >= --min-ngram\n")
        return 2
    if args.min_docs < 2:
        sys.stderr.write("[corpus_novelty_audit] --min-docs must be >= 2 (a set needs >= 2 docs)\n")
        return 2
    if not (0.0 < args.mutual_share <= 1.0):
        sys.stderr.write("[corpus_novelty_audit] --mutual-share must be in (0, 1]\n")
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
