#!/usr/bin/env python3
"""phrase_overuse_profile.py — over-represented phrase profile vs a human reference (VP-RS4).

Profiles the word n-grams a target corpus (typically one model's outputs) uses far
more often than a human reference corpus does: the profiling half of Antislop
(Paech et al., arXiv:2510.15061, code MIT). For every n-gram seen at least
``min_count`` times in the target (and in at least ``min_docs`` target documents,
so one repetitive document cannot dominate) it reports the frequency ratio
against the reference with add-half smoothing, and Dunning's log-likelihood G2
as a stability read. N-grams made only of function words are dropped by default.
The list is ordered by ratio; the order is the whole of the ranking.

Descriptive and stdlib-only. It names phrases a writer or model leans on; it does
not say who or what wrote the target. Abstains when the reference is too small
for a ratio to mean anything (``--min-reference-tokens``, default 20,000).

TASK_SURFACE: formulaicity (existing fragment).
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from setec.contract.output_schema import build_error_output, build_output  # noqa: E402
from setec.contract.claim_license import from_legacy  # noqa: E402

TASK_SURFACE = "formulaicity"
TOOL_NAME = "phrase_overuse_profile"
SCRIPT_VERSION = "1.0"

DEFAULT_N = (2, 3)
DEFAULT_MIN_COUNT = 3
DEFAULT_MIN_DOCS = 2
DEFAULT_MAX_REPORTED = 50
DEFAULT_MIN_REFERENCE_TOKENS = 20_000
SMOOTHING = 0.5

_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")
_BOUNDARY = re.compile(r"[.!?;:\n]+")
FUNCTION_WORDS = frozenset("""
a about above after again against all am an and any are as at be because been before being
below between both but by can could did do does doing down during each few for from further
had has have having he her here hers herself him himself his how i if in into is it its itself
just me more most my myself no nor not now of off on once only or other our ours ourselves out
over own same she should so some such than that the their theirs them themselves then there
these they this those through to too under until up upon very was we were what when where which
while who whom why will with would you your yours yourself yourselves 's 't s t
""".split())


class PhraseProfileError(ValueError):
    """Bad input reported as a bad_input envelope."""


def tokenize_segments(text: str) -> list[list[str]]:
    """Lowercased word tokens, split into segments at sentence punctuation and
    line breaks so an n-gram never spans a sentence boundary."""
    curly = text.replace("’", "'").replace("‘", "'")
    return [toks for seg in _BOUNDARY.split(curly.lower()) if (toks := _WORD.findall(seg))]


def ngram_counts(docs: Iterable[str], n_values: tuple[int, ...]):
    """Return (counts, doc_counts, n_tokens) over all docs."""
    counts: Counter = Counter()
    doc_counts: Counter = Counter()
    n_tokens = 0
    for text in docs:
        seen: set[tuple[str, ...]] = set()
        for seg in tokenize_segments(text):
            n_tokens += len(seg)
            for n in n_values:
                for i in range(len(seg) - n + 1):
                    g = tuple(seg[i:i + n])
                    counts[g] += 1
                    seen.add(g)
        doc_counts.update(seen)
    return counts, doc_counts, n_tokens


def _g2(a: int, b: int, n1: int, n2: int) -> float:
    """Dunning log-likelihood for a count a of n1 vs b of n2."""
    total = n1 + n2
    e1 = n1 * (a + b) / total
    e2 = n2 * (a + b) / total
    g = 0.0
    if a > 0:
        g += a * math.log(a / e1)
    if b > 0:
        g += b * math.log(b / e2)
    return 2.0 * g


def profile_overuse(
    target_docs: list[str],
    reference_docs: list[str],
    *,
    n_values: tuple[int, ...] = DEFAULT_N,
    min_count: int = DEFAULT_MIN_COUNT,
    min_docs: int = DEFAULT_MIN_DOCS,
    max_reported: int = DEFAULT_MAX_REPORTED,
    keep_function_ngrams: bool = False,
) -> dict[str, Any]:
    if not target_docs or not reference_docs:
        raise PhraseProfileError("need at least one target and one reference document")
    t_counts, t_docs, n_t = ngram_counts(target_docs, n_values)
    r_counts, _, n_r = ngram_counts(reference_docs, n_values)
    if n_t == 0 or n_r == 0:
        raise PhraseProfileError("target or reference has no word tokens")
    effective_min_docs = min(min_docs, len(target_docs))
    rows = []
    considered = 0
    for g, c in t_counts.items():
        if c < min_count or t_docs[g] < effective_min_docs:
            continue
        if not keep_function_ngrams and all(w in FUNCTION_WORDS for w in g):
            continue
        considered += 1
        rc = r_counts.get(g, 0)
        ratio = ((c + SMOOTHING) / n_t) / ((rc + SMOOTHING) / n_r)
        if ratio <= 1.0:
            continue
        rows.append({
            "ngram": " ".join(g), "n": len(g), "target_count": c, "target_docs": t_docs[g],
            "reference_count": rc,
            "target_per_million": 1e6 * c / n_t, "reference_per_million": 1e6 * rc / n_r,
            "overuse_ratio": ratio, "g2": _g2(c, rc, n_t, n_r),
        })
    rows.sort(key=lambda r: (-r["overuse_ratio"], -r["target_count"], r["ngram"]))
    return {
        "phrase_overuse": {
            "n_values": list(n_values),
            "target_tokens": n_t, "reference_tokens": n_r,
            "target_documents": len(target_docs), "reference_documents": len(reference_docs),
            "min_count": min_count, "min_docs": effective_min_docs,
            "n_ngrams_considered": considered,
            "n_over_represented": len(rows),
            "over_represented": rows[:max_reported],
            "calibration_status": "provisional",
            "assumptions": {
                "method": "Antislop-style over-representation profile (arXiv:2510.15061): "
                          "target vs reference n-gram frequency ratio, add-half smoothing",
                "order": "by overuse_ratio, then target count",
                "g2": "Dunning log-likelihood of the target vs reference counts; descriptive, no cut-off",
                "tokens": "lowercased word tokens; n-grams do not cross sentence punctuation or line breaks",
                "function_ngrams": "kept" if keep_function_ngrams else "dropped when every word is a function word",
            },
        }
    }


def _read_docs(path: Path) -> list[str]:
    if path.is_dir():
        files = sorted(p for p in path.rglob("*") if p.suffix.lower() in (".txt", ".md") and p.is_file())
        if not files:
            raise PhraseProfileError(f"no .txt/.md files under {path}")
        return [p.read_text(encoding="utf-8", errors="replace") for p in files]
    if path.suffix.lower() == ".jsonl":
        docs = []
        with path.open(encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if line.strip():
                    text = json.loads(line).get("text")
                    if not isinstance(text, str):
                        raise PhraseProfileError(f"{path.name} line {lineno}: missing 'text'")
                    docs.append(text)
        return docs
    return [path.read_text(encoding="utf-8", errors="replace")]


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "A ranked list of word n-grams the target corpus uses more often than the reference "
            "corpus, with counts, per-million rates, a smoothed frequency ratio and Dunning G2."
        ),
        "does_not_license": (
            "This does NOT identify the author, model or provenance of the target, and an "
            "over-represented phrase is not by itself a flaw: genre, topic and period vocabulary "
            "all raise ratios. Ratios depend on the reference chosen. No cut-off is calibrated; "
            "calibration is PROVISIONAL."
        ),
    }


def _parse_n(value: str) -> tuple[int, ...]:
    try:
        ns = tuple(sorted({int(x) for x in value.split(",") if x.strip()}))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--n takes a comma list of integers") from exc
    if not ns or any(n < 1 or n > 6 for n in ns):
        raise argparse.ArgumentTypeError("--n values must be between 1 and 6")
    return ns


def _run(args: argparse.Namespace) -> dict[str, Any]:
    def error(reason: str, category: str) -> dict[str, Any]:
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
                                  target_path=args.target, reason=reason, reason_category=category)

    try:
        target_docs = _read_docs(Path(args.target))
        reference_docs = _read_docs(Path(args.reference))
    except (OSError, json.JSONDecodeError, PhraseProfileError) as exc:
        return error(f"cannot read input: {exc}", "bad_input")
    ref_tokens = sum(len(s) for d in reference_docs for s in tokenize_segments(d))
    if ref_tokens < args.min_reference_tokens:
        return error(
            f"reference has {ref_tokens} word tokens, below --min-reference-tokens "
            f"{args.min_reference_tokens}; ratios against a reference this small are noise",
            "text_too_short")
    try:
        results = profile_overuse(
            target_docs, reference_docs, n_values=args.n, min_count=args.min_count,
            min_docs=args.min_docs, max_reported=args.max_reported,
            keep_function_ngrams=args.keep_function_ngrams)
    except PhraseProfileError as exc:
        return error(str(exc), "bad_input")
    block = results["phrase_overuse"]
    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=args.target, target_words=block["target_tokens"],
        baseline={"n_files": block["reference_documents"], "words": block["reference_tokens"]},
        results=results,
        claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        validate_bounds=True,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", required=True, help="target file, directory of .txt/.md, or JSONL with 'text'")
    ap.add_argument("--reference", required=True, help="human reference: file, directory, or JSONL")
    ap.add_argument("--n", type=_parse_n, default=DEFAULT_N, help="n-gram sizes, comma list (default 2,3)")
    ap.add_argument("--min-count", type=int, default=DEFAULT_MIN_COUNT)
    ap.add_argument("--min-docs", type=int, default=DEFAULT_MIN_DOCS,
                    help="minimum target documents an n-gram must appear in (capped at the document count)")
    ap.add_argument("--max-reported", type=int, default=DEFAULT_MAX_REPORTED)
    ap.add_argument("--min-reference-tokens", type=int, default=DEFAULT_MIN_REFERENCE_TOKENS)
    ap.add_argument("--keep-function-ngrams", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    envelope = _run(args)
    text = json.dumps(envelope, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.json or not args.out:
        print(text)
    return 0 if envelope.get("available", True) else 3


if __name__ == "__main__":
    raise SystemExit(main())
