#!/usr/bin/env python3
"""originality_audit.py — DJ-Search reconstructibility vs a reference pool (spec 22, M1).

Measures how much of a `--target` is *reconstructible* from a reference corpus (the operator's
impostor pool by default): the fraction of the target covered by long verbatim token spans that
appear somewhere in the reference. Clean-room reimplementation of DJ Search (Creativity Index,
*AI as Humanity's Salieri*, arXiv:2410.04265). Pure stdlib, deterministic, no model.

Set-level axis (`set_level_diversity`): every per-document SETEC surface scores ONE text against a
baseline — this scores a target against a *pool* of reference material it might recombine.

Posture (no verdict): reports `coverage` (= reconstructibility) and `originality = 1 − coverage`,
oriented **gt = less reconstructible from the named pool**. NOT "more human" — a thin/narrow pool
inflates apparent originality; quotation, shared sources and genre formula deflate it. The claim
license refuses any AI/human or plagiarism determination; thresholds are operator-side / PROVISIONAL.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_schema import build_error_output, build_output  # noqa: E402
from claim_license import from_legacy  # noqa: E402
from verbatim_cover import (  # noqa: E402,F401 -- re-exported for existing importers
    DEFAULT_MIN_NGRAM,
    _MAX_SPAN,
    _TOKEN,
    _content_fingerprint,
    _load_reference_dir,
    _load_reference_manifest,
    _tokens,
    audit_originality,
)

TASK_SURFACE = "set_level_diversity"
TOOL_NAME = "originality_audit"
SCRIPT_VERSION = "1.0"


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "The fraction of the target reconstructible from the named reference pool — the "
            "coverage of the target by verbatim token spans of length >= min_ngram that appear "
            "in the reference corpus (DJ Search), reported as `coverage` and `originality = 1 - "
            "coverage`, oriented so higher originality = less reconstructible from THAT pool."
        ),
        "does_not_license": (
            "Any AI/human determination (low originality is NOT 'AI'; high originality is NOT "
            "'human' — a thin/narrow pool inflates it). Any plagiarism, derivative-work, or "
            "copyright determination — this is a span-coverage measurement, not a legal claim. "
            "Quotation, shared sources, and genre formula legitimately lower originality; "
            "reconstructibility is corpus-dependent and register-sensitive. Thresholds are "
            "operator-side / PROVISIONAL; the surface emits no verdict."
        ),
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    target_path = Path(args.target)
    try:
        target_text = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        # #225: invalid UTF-8 raises UnicodeDecodeError (a ValueError, not OSError) — bad input,
        # not a crash.
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME,
                                  version=SCRIPT_VERSION, target_path=str(target_path),
                                  reason=f"cannot read --target: {e}", reason_category="bad_input")
    # A missing/unreadable/non-UTF-8 reference dir or manifest is bad INPUT, not a crash (#225 P2):
    # _load_reference_* call read_text(), which raises OSError on a missing path and
    # UnicodeDecodeError on a non-UTF-8 manifest file.
    try:
        if args.reference_dir:
            loaded = _load_reference_dir(Path(args.reference_dir))
        else:
            loaded = _load_reference_manifest(Path(args.manifest))
    except (OSError, UnicodeDecodeError) as e:
        which = "--reference-dir" if args.reference_dir else "--manifest"
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME,
                                  version=SCRIPT_VERSION, target_path=str(target_path),
                                  reason=f"cannot read {which}: {e}", reason_category="bad_input")

    # Self-exclusion: never let the target reconstruct itself if it sits in its own reference
    # pool (mirrors general_imposters' drop-self) — otherwise coverage trivially collapses to 1.0.
    # Drop a pool entry by EITHER guard:
    #   (a) PATH match — resolved_path == target's resolved_path (a reference FILE that IS the target).
    #   (b) CONTENT match — token-stream fingerprint == target's (sha256 over _tokens, the matcher's
    #       own case/punctuation-insensitive normalization — Codex round-2 P1). This catches an
    #       inline-text manifest row (resolved_path None) carrying a COPY of the target, including a
    #       punctuation- or case-only variant that the matcher reconstructs span-for-span; the path
    #       guard alone (None != target_abs) never self-excludes it. Sibling of the Codex P1 fixed in
    #       cross_doc_novelty_profile.py. Path OR content -> exclude; a content match only DROPS,
    #       never re-admits (fail-closed). If exclusion empties the pool, audit_originality raises
    #       ValueError and the caller below maps it to the existing bad_input envelope.
    target_abs = target_path.resolve()
    target_fingerprint = _content_fingerprint(target_text)
    reference: list[tuple[str, str]] = []
    n_dropped_self = 0
    for src, text, pth in loaded:
        path_match = pth is not None and pth == target_abs
        content_match = _content_fingerprint(text) == target_fingerprint
        if path_match or content_match:
            n_dropped_self += 1
        else:
            reference.append((src, text))

    try:
        results = audit_originality(target_text, reference, min_ngram=args.min_ngram,
                                    max_span=args.max_span)
    except ValueError as e:
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME,
                                  version=SCRIPT_VERSION, target_path=str(target_path),
                                  reason=str(e), reason_category="bad_input")

    # Surface the self-exclusion count (path OR content matches) through the assumptions block — the
    # honesty pattern: a non-zero drop means the reported originality is measured against a pool that
    # excludes the target's own copies.
    results["assumptions"]["n_dropped_self"] = n_dropped_self

    warnings: list[str] = []
    if n_dropped_self:
        warnings.append(f"dropped {n_dropped_self} reference doc(s) identical to the target by path "
                        "or content (self-exclusion); the target does not reconstruct itself")
    if results["target_tokens"] < args.min_ngram:
        warnings.append(f"target has {results['target_tokens']} tokens (< min_ngram "
                        f"{args.min_ngram}); no span can match, originality is trivially 1.0")
    warnings = warnings or None

    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=str(target_path), target_words=results["target_tokens"],
        baseline={"reference": args.reference_dir or args.manifest,
                  "n_reference_docs": results["n_reference_docs"]},
        results=results, claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        warnings=warnings,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", required=True, help="Path to the target text.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--reference-dir", help="Directory of reference texts (.txt/.md, recursive).")
    g.add_argument("--manifest", help="JSONL manifest of the reference pool (id + text|text_path).")
    ap.add_argument("--min-ngram", type=int, default=DEFAULT_MIN_NGRAM,
                    help=f"Minimum verbatim span length counted as reconstructed (default {DEFAULT_MIN_NGRAM}).")
    ap.add_argument("--max-span", type=int, default=_MAX_SPAN,
                    help=f"Cap on a single matched span, bounding the per-position search (default "
                         f"{_MAX_SPAN}). Surfaced as max_span_cap + longest_match_capped; raise it for "
                         "the exact longest_match_tokens on corpora with very long verbatim reuse.")
    ap.add_argument("--json", action="store_true", help="Emit the JSON envelope to stdout.")
    ap.add_argument("--out", help="Write the JSON envelope to this path.")
    args = ap.parse_args(argv)

    if args.min_ngram < 1:
        sys.stderr.write("[originality_audit] --min-ngram must be >= 1\n")
        return 2
    if args.max_span < args.min_ngram:
        sys.stderr.write("[originality_audit] --max-span must be >= --min-ngram "
                         "(a cap below the minimum span counts nothing)\n")
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
