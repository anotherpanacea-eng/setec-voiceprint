"""verbatim_cover.py — shared DJ-Search verbatim-cover primitives (L1 library).

The greedy longest-span coverage matcher, its token unit, the reference-pool
loaders, and the matcher-aligned content fingerprint behind ``originality_audit``
(spec 22), single-sourced here so other surfaces can reuse them without an
L2 -> L2 import of that surface (``tools/check_layering.py``). ``originality_audit``
re-exports these same objects, so its existing importers are unchanged.

Library only: no capability fragment, no CLI entry, no envelope emission. Pure
stdlib and deterministic. The loaders keep their pool-loader names and their
metadata-discarding ``(source, text, path)`` tuple shape on purpose: the spec-36
pool-guard and register-isolation closure sweeps
(``tests/test_pool_guard_coverage.py``, ``tests/test_register_isolation_coverage.py``)
key on exactly those names and pin exactly that shape here.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

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


# ---- content fingerprint (self-exclusion for inline-text pool entries) -------

_FP_SEP = "\x1f"  # ASCII unit separator: a non-token byte that bounds the serialized token stream


def _content_fingerprint(text: str) -> str:
    """sha256 of the CANONICAL TOKEN STREAM the matcher reconstructs over — a separator-safe
    serialization of ``_tokens(text)`` (the lowercased ``[a-z0-9]+`` runs DJ-Search matches). Used
    to self-exclude a pool entry whose *content* equals the target's even when its resolved_path is
    None (an inline-``text`` manifest row from _load_reference_manifest).

    Two pool entries with the SAME token stream are reconstruction-equivalent: the matcher (``_tokens``)
    is case- AND punctuation-insensitive, so it covers the target span-for-span from either copy. The
    fingerprint must therefore live in the matcher's own equivalence class — fingerprinting under
    ``normalize_for_char_ngrams`` (which lowercases + collapses whitespace but PRESERVES punctuation)
    was too tight: a punctuation-only variant of the target (e.g. ``"alpha, beta..."`` vs ``"alpha
    beta..."``) has identical ``_tokens`` yet a different normalize_for_char_ngrams string, so it
    escaped self-exclusion and let the target reconstruct itself (coverage trivially 1.0) — Codex
    round-2 P1. Joining with ``\\x1f`` (a non-token byte; ``[a-z0-9]+`` can never contain it) keeps
    ``["ab"]`` and ``["a","b"]`` distinct.

    Sibling of the Codex P1 fixed in cross_doc_novelty_profile.py: the path-only guard
    (``pth != target_abs``) never fires on an inline copy of the target (pth=None), letting the
    target reconstruct itself from its own copy. The content fingerprint closes that hole alongside
    the path check (path OR content -> exclude)."""
    return hashlib.sha256(_FP_SEP.join(_tokens(text)).encode("utf-8")).hexdigest()


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
                      max_span: int = _MAX_SPAN,
                      include_spans: bool = False) -> dict[str, Any]:
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
    source_strings = [(src, " " + " ".join(toks) + " ") for src, toks in ref_docs]

    def _source_of(span_text: str) -> str | None:
        needle = " " + span_text + " "
        for src, source_text in source_strings:
            if needle in source_text:
                return src
        return None

    attribution = [
        {"length": s["length"], "text": s["text"], "source": _source_of(s["text"])}
        for s in sorted(spans, key=lambda s: -s["length"])[:5]
    ]

    result = {
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
    if include_spans:
        # Same first-containing-document rule as attribution, without emitting prose.
        result["all_spans"] = [
            {"start": s["start"], "length": s["length"],
             "source": _source_of(s["text"])} for s in spans
        ]
    return result
