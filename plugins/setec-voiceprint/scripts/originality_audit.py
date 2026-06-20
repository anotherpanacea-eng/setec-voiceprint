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
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_schema import build_error_output, build_output  # noqa: E402
from claim_license import from_legacy  # noqa: E402

TASK_SURFACE = "set_level_diversity"
TOOL_NAME = "originality_audit"
SCRIPT_VERSION = "1.0"

DEFAULT_MIN_NGRAM = 8
_MAX_SPAN = 256          # cap on a single matched span (bounds the per-position search)
_SENTINEL = "\x00"       # doc separator in the search string — spans can't cross documents
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    """Lowercase word tokens — the unit DJ-Search matches over (case/punctuation-insensitive)."""
    return _TOKEN.findall(text.lower())


# ---- reference corpus loading ------------------------------------------------

def _load_reference_dir(root: Path, suffixes=(".txt", ".md")) -> list[tuple[str, str, Path | None]]:
    """(source, text, resolved_path) for every text file under `root` (recursive)."""
    out: list[tuple[str, str, Path | None]] = []
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        if p.suffix.lower() in suffixes:
            out.append((p.relative_to(root).as_posix(),
                        p.read_text(encoding="utf-8", errors="replace"), p.resolve()))
    return out


def _load_reference_manifest(path: Path) -> list[tuple[str, str, Path | None]]:
    """(source, text, resolved_path) from a JSONL manifest. Each row carries inline `text`
    (path None), or a `text_path`/`path` resolved relative to the manifest's directory."""
    out: list[tuple[str, str, Path | None]] = []
    base = path.resolve().parent
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"  manifest line {line_no}: {e}; skipping\n")
            continue
        if not isinstance(row, dict):
            # #225: a valid-JSON-but-non-object row (array / number / string) has no .get —
            # skip it rather than tracebacking.
            sys.stderr.write(f"  manifest line {line_no}: not a JSON object; skipping\n")
            continue
        src = str(row.get("id") or row.get("path") or row.get("text_path") or f"line{line_no}")
        if isinstance(row.get("text"), str):
            out.append((src, row["text"], None))
            continue
        rel = row.get("text_path") or row.get("path")
        if rel:
            fp = (base / rel)
            if fp.is_file():
                out.append((src, fp.read_text(encoding="utf-8", errors="replace"), fp.resolve()))
            else:
                sys.stderr.write(f"  manifest line {line_no}: {fp} not found; skipping\n")
    return out


# ---- DJ-Search coverage ------------------------------------------------------

def _bounded(toks: list[str], i: int, length: int) -> str:
    return " " + " ".join(toks[i:i + length]) + " "


def _match_len(target: list[str], i: int, ref_search: str, max_len: int) -> int:
    """Longest L in [0, max_len] such that the space-bounded span target[i:i+L] is a substring of
    `ref_search`. Monotonic in L (a matching span's bounded prefix also matches), so binary-searched."""
    if _bounded(target, i, 1) not in ref_search:
        return 0
    lo, hi = 1, min(max_len, len(target) - i)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _bounded(target, i, mid) in ref_search:
            lo = mid
        else:
            hi = mid - 1
    return lo


def audit_originality(target_text: str, reference: list[tuple[str, str]], *,
                      min_ngram: int = DEFAULT_MIN_NGRAM,
                      max_span: int = _MAX_SPAN) -> dict[str, Any]:
    """Greedily cover the target with longest left-to-right reference matches (DJ Search).

    Returns the value-level results: `coverage`/`originality`, span stats, and attribution for the
    longest spans. Deterministic. Raises ValueError on an empty target or empty reference (the caller
    maps that to a bad_input envelope — no division by zero, no silent 1.0).

    `max_span` bounds each per-position match (search cost). It is SURFACED, not hidden (#225 P2):
    when a match reaches the cap, `longest_match_tokens` is a LOWER BOUND and `longest_match_capped`
    is True — raise --max-span for the exact value rather than silently reporting the cap."""
    target = _tokens(target_text)
    if not target:
        raise ValueError("target has no word tokens")
    ref_docs = [(src, _tokens(t)) for src, t in reference]
    ref_docs = [(src, toks) for src, toks in ref_docs if toks]
    if not ref_docs:
        raise ValueError("reference corpus has no word tokens")

    # One search string; the sentinel between docs keeps a span from crossing a document boundary.
    ref_search = " " + (" " + _SENTINEL + " ").join(" ".join(toks) for _, toks in ref_docs) + " "

    n = len(target)
    covered = 0
    spans: list[dict[str, Any]] = []
    longest_overall = 0
    i = 0
    while i < n:
        L = _match_len(target, i, ref_search, max_span)
        longest_overall = max(longest_overall, L)
        if L >= min_ngram:
            covered += L
            spans.append({"start": i, "length": L,
                          "text": " ".join(target[i:i + L])})
            i += L
        else:
            i += 1

    coverage = covered / n
    # Span-length histogram (counted spans only), small integer buckets.
    histogram: dict[str, int] = {}
    for s in spans:
        b = str(s["length"])
        histogram[b] = histogram.get(b, 0) + 1

    # Attribution: for the longest few spans, the first reference source that contains them.
    def _source_of(span_text: str) -> str | None:
        needle = " " + span_text + " "
        for src, toks in ref_docs:
            if needle in " " + " ".join(toks) + " ":
                return src
        return None

    attribution = [
        {"length": s["length"], "text": s["text"], "source": _source_of(s["text"])}
        for s in sorted(spans, key=lambda s: -s["length"])[:5]
    ]

    return {
        "coverage": round(coverage, 6),
        "originality": round(1.0 - coverage, 6),
        "longest_match_tokens": longest_overall,
        # #225 P2: the per-span search is capped at max_span_cap; when hit, longest_match_tokens
        # is a LOWER BOUND (the true span may be longer). Surfaced so the stat isn't silently false.
        "max_span_cap": max_span,
        "longest_match_capped": longest_overall >= max_span,
        "n_matched_spans": len(spans),
        "matched_token_histogram": dict(sorted(histogram.items(), key=lambda kv: int(kv[0]))),
        "attribution": attribution,
        "min_ngram": min_ngram,
        "target_tokens": n,
        "n_reference_docs": len(ref_docs),
        "n_reference_tokens": sum(len(toks) for _, toks in ref_docs),
        "assumptions": {
            "method": "DJ-Search greedy longest-span coverage (arXiv:2410.04265)",
            "orientation": "originality gt = less reconstructible from the reference pool "
                           "(NOT 'more human')",
            "corpus_dependence": "reconstructibility is corpus- and register-dependent — a "
                                 "thin/narrow reference pool inflates apparent originality; "
                                 "ESL/dialect or genre-formula text is not adjudicated here",
            "span_cap": f"per-span search is capped at max_span_cap={max_span} tokens; when "
                        "longest_match_capped is true, longest_match_tokens is a LOWER BOUND "
                        "(raise --max-span for the exact value). Coverage is unaffected (a capped "
                        "match continues from the next position).",
        },
    }


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
    target_abs = target_path.resolve()
    n_dropped_self = sum(1 for _, _, pth in loaded if pth == target_abs)
    reference = [(src, text) for src, text, pth in loaded if pth != target_abs]

    try:
        results = audit_originality(target_text, reference, min_ngram=args.min_ngram,
                                    max_span=args.max_span)
    except ValueError as e:
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME,
                                  version=SCRIPT_VERSION, target_path=str(target_path),
                                  reason=str(e), reason_category="bad_input")

    warnings: list[str] = []
    if n_dropped_self:
        warnings.append(f"dropped {n_dropped_self} reference doc(s) identical to the target path "
                        "(self-exclusion); the target does not reconstruct itself")
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
