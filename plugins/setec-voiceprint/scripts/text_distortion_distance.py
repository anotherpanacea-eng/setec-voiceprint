#!/usr/bin/env python3
"""text_distortion_distance.py — Stamatatos text-distortion distance to a baseline (VP-TD1).

Text distortion (Stamatatos 2017, "Authorship Attribution Using Text Distortion",
EACL) masks every word outside a small kept set (here the shared 135-word
function-word list, or the baseline's most frequent words) and keeps
punctuation, spacing and the kept words. What is left is the structure of the
prose: where the function words fall, how long the masked words are, and how
the punctuation sits around them. Character n-grams (default 3-grams) over the
masked text then profile that structure with topic largely removed.

Two masking modes, both from the paper:

* ``dv-ma`` (multiple asterisks, default): every letter of a masked word
  becomes ``*``, so word length survives ("in the *****, which ***").
* ``dv-sa`` (single asterisk): a masked word becomes one ``*``.

Digits become ``#`` in both modes.

The surface pools the target documents and the baseline documents, keeps the
``--max-features`` most frequent baseline n-grams, and reports the cosine
distance between the two relative-frequency profiles. Each target document also
gets its own distance to the whole baseline. When the baseline has at least
three documents, a leave-one-out spread (each baseline document against the
pool of the others, with the feature set rebuilt per fold) gives a reference
range and a percentile for the target. Baseline documents whose text equals a
target document are dropped first, so a copy cannot pull the distance to zero.

Stdlib only. Descriptive: a distance and where it sits against the baseline's
own spread; no threshold is calibrated and nothing here says who wrote the
target.

TASK_SURFACE: voice_coherence (existing fragment).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from setec.contract.claim_license import from_legacy
from setec.contract.output_schema import build_error_output, build_output
from setec.core.textprims import FUNCTION_WORDS

TASK_SURFACE = "voice_coherence"
TOOL_NAME = "text_distortion_distance"
SCRIPT_VERSION = "1.0"

MODES = ("dv-ma", "dv-sa")
KEEP_CHOICES = ("function-words", "baseline-top-k")
DEFAULT_MODE = "dv-ma"
DEFAULT_N = 3
DEFAULT_MAX_FEATURES = 1000
DEFAULT_TOP_K = 150
DEFAULT_MIN_TARGET_WORDS = 300
DEFAULT_MIN_BASELINE_WORDS = 1000
DEFAULT_MAX_SHIFTED = 10
MIN_LOO_DOCS = 3

_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
_DIGIT = re.compile(r"\d")
_SPACE = re.compile(r"\s+")


class TextDistortionError(ValueError):
    """Bad input reported as a bad_input envelope."""


def _norm(word: str) -> str:
    return word.lower().replace("’", "'")


def word_count(text: str) -> int:
    return len(_WORD.findall(text))


def distort(text: str, keep: frozenset[str] | set[str], mode: str = DEFAULT_MODE) -> str:
    """Return the text-distortion view of ``text``.

    Words whose lowercased form is in ``keep`` stay as written; every other
    word is masked (letters to ``*`` in dv-ma, the whole word to one ``*`` in
    dv-sa; apostrophes inside a dv-ma word stay). Digits become ``#``.
    Whitespace runs collapse to one space; all other characters stay."""
    if mode not in MODES:
        raise TextDistortionError(f"unknown mode {mode!r}; expected one of {MODES}")

    def mask(match: re.Match[str]) -> str:
        word = match.group(0)
        if _norm(word) in keep:
            return word
        if mode == "dv-sa":
            return "*"
        return "".join(ch if ch in "'’" else "*" for ch in word)

    masked = _WORD.sub(mask, text)
    masked = _DIGIT.sub("#", masked)
    return _SPACE.sub(" ", masked).strip()


def char_ngrams(text: str, n: int) -> Counter:
    return Counter(text[i:i + n] for i in range(len(text) - n + 1))


def _select_features(counts: Counter, max_features: int) -> list[str]:
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [g for g, _ in ranked[:max_features]]


def _profile(counts: Counter, features: list[str]) -> list[float]:
    total = sum(counts.values())
    if total == 0:
        return [0.0] * len(features)
    return [counts.get(g, 0) / total for g in features]


def cosine_distance(a: list[float], b: list[float]) -> float | None:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return None
    return max(0.0, 1.0 - dot / (na * nb))


def _pooled(counters: Iterable[Counter]) -> Counter:
    pooled: Counter = Counter()
    for c in counters:
        pooled.update(c)
    return pooled


def _quantile(sorted_values: list[float], q: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _percentile_of(value: float, sorted_values: list[float]) -> float:
    """Share of reference values at or below ``value`` (0..1)."""
    return sum(1 for v in sorted_values if v <= value) / len(sorted_values)


def keep_set(baseline_docs: list[str], keep: str, top_k: int) -> frozenset[str]:
    if keep == "function-words":
        return frozenset(FUNCTION_WORDS)
    if keep == "baseline-top-k":
        freq = Counter(_norm(w) for d in baseline_docs for w in _WORD.findall(d))
        ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
        return frozenset(w for w, _ in ranked[:top_k])
    raise TextDistortionError(f"unknown keep set {keep!r}; expected one of {KEEP_CHOICES}")


def compare(
    target_docs: list[dict[str, str]],
    baseline_docs: list[str],
    *,
    mode: str = DEFAULT_MODE,
    n: int = DEFAULT_N,
    max_features: int = DEFAULT_MAX_FEATURES,
    keep: str = "function-words",
    top_k: int = DEFAULT_TOP_K,
    max_shifted: int = DEFAULT_MAX_SHIFTED,
) -> dict[str, Any]:
    """Score target documents (``{"id", "text"}``) against baseline texts."""
    if not target_docs:
        raise TextDistortionError("need at least one target document")
    target_hashes = {hashlib.sha256(d["text"].encode("utf-8")).hexdigest() for d in target_docs}
    kept_baseline = [
        b for b in baseline_docs
        if hashlib.sha256(b.encode("utf-8")).hexdigest() not in target_hashes
    ]
    n_self_excluded = len(baseline_docs) - len(kept_baseline)
    if not kept_baseline:
        raise TextDistortionError("no baseline documents left after removing copies of the target")

    kept_words = keep_set(kept_baseline, keep, top_k)
    base_counts = [char_ngrams(distort(b, kept_words, mode), n) for b in kept_baseline]
    tgt_counts = [char_ngrams(distort(d["text"], kept_words, mode), n) for d in target_docs]
    base_pool = _pooled(base_counts)
    tgt_pool = _pooled(tgt_counts)
    features = _select_features(base_pool, max_features)
    if not features:
        raise TextDistortionError("baseline produced no character n-grams")
    base_vec = _profile(base_pool, features)
    tgt_vec = _profile(tgt_pool, features)
    distance = cosine_distance(tgt_vec, base_vec)

    per_doc = []
    for doc, counts in zip(target_docs, tgt_counts):
        per_doc.append({
            "id": doc["id"],
            "words": word_count(doc["text"]),
            "distance": cosine_distance(_profile(counts, features), base_vec),
        })

    loo: dict[str, Any] = {"available": False}
    if len(kept_baseline) >= MIN_LOO_DOCS:
        values = []
        for i, held in enumerate(base_counts):
            rest = base_pool.copy()
            rest.subtract(held)
            rest = +rest
            fold_features = _select_features(rest, max_features)
            d = cosine_distance(_profile(held, fold_features), _profile(rest, fold_features))
            if d is not None:
                values.append(d)
        if values:
            values.sort()
            loo = {
                "available": True,
                "n": len(values),
                "min": values[0], "p25": _quantile(values, 0.25), "median": _quantile(values, 0.5),
                "p75": _quantile(values, 0.75), "max": values[-1],
                "mean": sum(values) / len(values),
                "target_percentile": _percentile_of(distance, values) if distance is not None else None,
            }
            for row in per_doc:
                row["percentile"] = (
                    _percentile_of(row["distance"], values) if row["distance"] is not None else None
                )
    else:
        loo["reason"] = f"needs at least {MIN_LOO_DOCS} baseline documents"

    shifts = sorted(
        ({"ngram": g, "target_per_thousand": 1000 * t, "baseline_per_thousand": 1000 * b,
          "difference_per_thousand": 1000 * (t - b)}
         for g, t, b in zip(features, tgt_vec, base_vec)),
        key=lambda r: (-abs(r["difference_per_thousand"]), r["ngram"]),
    )
    more = [r for r in shifts if r["difference_per_thousand"] > 0][:max_shifted]
    less = [r for r in shifts if r["difference_per_thousand"] < 0][:max_shifted]

    return {
        "text_distortion": {
            "distance": distance,
            "mode": mode,
            "n": n,
            "keep": keep,
            "kept_word_count": len(kept_words),
            "n_features": len(features),
            "target_documents": len(target_docs),
            "target_words": sum(r["words"] for r in per_doc),
            "baseline_documents": len(kept_baseline),
            "baseline_words": sum(word_count(b) for b in kept_baseline),
            "baseline_self_excluded": n_self_excluded,
            "per_document": per_doc,
            "baseline_leave_one_out": loo,
            "target_over_baseline": more,
            "target_under_baseline": less,
            "calibration_status": "provisional",
            "assumptions": {
                "method": "Stamatatos 2017 text distortion, then character n-gram profiles "
                          "compared by cosine distance",
                "masking": ("each letter of a masked word becomes '*'" if mode == "dv-ma"
                            else "each masked word becomes one '*'") + "; digits become '#'; "
                           "whitespace runs collapse to one space",
                "features": "most frequent baseline n-grams, relative frequencies over all n-grams "
                            "of the text; rebuilt per leave-one-out fold",
                "pooling": "target and baseline documents are pooled for the headline distance",
                "length": "distances shrink as texts grow; compare documents of similar length, "
                          "and read the percentile against baseline documents of similar size",
            },
        }
    }


def _read_docs(path: Path, text_field: str) -> list[dict[str, str]]:
    if path.is_dir():
        files = sorted(p for p in path.rglob("*") if p.suffix.lower() in (".txt", ".md") and p.is_file())
        if not files:
            raise TextDistortionError(f"no .txt/.md files under {path}")
        return [{"id": str(p.relative_to(path)), "text": p.read_text(encoding="utf-8", errors="replace")}
                for p in files]
    if path.suffix.lower() == ".jsonl":
        docs = []
        with path.open(encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                text = row.get(text_field) if isinstance(row, dict) else None
                if not isinstance(text, str):
                    raise TextDistortionError(f"{path.name} line {lineno}: missing '{text_field}'")
                docs.append({"id": str(row.get("id", lineno)), "text": text})
        if not docs:
            raise TextDistortionError(f"{path.name} has no rows")
        return docs
    return [{"id": path.name, "text": path.read_text(encoding="utf-8", errors="replace")}]


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "The cosine distance between the target's and the baseline's character n-gram "
            "profiles after Stamatatos text distortion (non-kept words masked), per target "
            "document and pooled, with the baseline's own leave-one-out spread when it has "
            "three or more documents, and the masked n-grams that differ most."
        ),
        "does_not_license": (
            "This does NOT attribute authorship, detect AI involvement or judge quality. "
            "A small distance says the masked structure (function-word placement, word "
            "lengths, punctuation) resembles the baseline; it says nothing about content or "
            "fidelity. Distances depend on length, the kept-word set and the baseline chosen. "
            "No cut-off is calibrated; calibration is PROVISIONAL."
        ),
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    def error(reason: str, category: str) -> dict[str, Any]:
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
                                  target_path=args.target, reason=reason, reason_category=category)

    try:
        target_docs = _read_docs(Path(args.target), args.text_field)
        baseline_docs = [d["text"] for d in _read_docs(Path(args.baseline), args.text_field)]
    except (OSError, json.JSONDecodeError, TextDistortionError) as exc:
        return error(f"cannot read input: {exc}", "bad_input")
    target_words = sum(word_count(d["text"]) for d in target_docs)
    if target_words < args.min_target_words:
        return error(f"target has {target_words} words, below --min-target-words "
                     f"{args.min_target_words}", "text_too_short")
    baseline_words = sum(word_count(b) for b in baseline_docs)
    if baseline_words < args.min_baseline_words:
        return error(f"baseline has {baseline_words} words, below --min-baseline-words "
                     f"{args.min_baseline_words}", "text_too_short")
    try:
        results = compare(target_docs, baseline_docs, mode=args.mode, n=args.n,
                          max_features=args.max_features, keep=args.keep, top_k=args.top_k,
                          max_shifted=args.max_shifted)
    except TextDistortionError as exc:
        return error(str(exc), "bad_input")
    block = results["text_distortion"]
    warnings = []
    if block["distance"] is None:
        warnings.append("target shares no n-grams with the baseline feature set; distance undefined")
    if block["baseline_self_excluded"]:
        warnings.append(f"{block['baseline_self_excluded']} baseline document(s) equal to a target "
                        "document were excluded")
    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=args.target, target_words=block["target_words"],
        baseline={"n_files": block["baseline_documents"], "words": block["baseline_words"]},
        results=results, warnings=warnings,
        claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        validate_bounds=True,
    )


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a positive integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return number


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", required=True, help="target file, directory of .txt/.md, or JSONL")
    ap.add_argument("--baseline", required=True, help="baseline file, directory of .txt/.md, or JSONL")
    ap.add_argument("--text-field", default="text", help="JSONL text field (default text; 'id' names rows)")
    ap.add_argument("--mode", choices=MODES, default=DEFAULT_MODE)
    ap.add_argument("--n", type=_positive_int, default=DEFAULT_N, help="character n-gram size (default 3)")
    ap.add_argument("--max-features", type=_positive_int, default=DEFAULT_MAX_FEATURES)
    ap.add_argument("--keep", choices=KEEP_CHOICES, default="function-words",
                    help="words left unmasked: the shared function-word list, or the baseline's top-k words")
    ap.add_argument("--top-k", type=_positive_int, default=DEFAULT_TOP_K)
    ap.add_argument("--max-shifted", type=int, default=DEFAULT_MAX_SHIFTED)
    ap.add_argument("--min-target-words", type=int, default=DEFAULT_MIN_TARGET_WORDS)
    ap.add_argument("--min-baseline-words", type=int, default=DEFAULT_MIN_BASELINE_WORDS)
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
