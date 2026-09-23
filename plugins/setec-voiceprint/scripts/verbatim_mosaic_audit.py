#!/usr/bin/env python3
"""Descriptive verbatim-span mosaic profile against an operator-held source pool."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from claim_license import from_legacy
from output_schema import build_error_output, build_output
from segmentation_feature_lens import (
    cosine_distance, sentence_spans, window_features, z_score_against,
)
from verbatim_cover import (
    DEFAULT_MIN_NGRAM, _MAX_SPAN, _TOKEN, _content_fingerprint,
    _load_reference_dir, _load_reference_manifest, audit_originality,
)

TASK_SURFACE = "set_level_diversity"
TOOL_NAME = "verbatim_mosaic_audit"
SCRIPT_VERSION = "1.0"
HARD_MAX_TARGET_TOKENS = 60_000
HARD_MAX_REFERENCE_TOKENS = 100_000
HARD_MAX_REFERENCE_DOCS = 5_000
HARD_MAX_SPAN = 1_024


def _quantiles(values: list[int] | list[float]) -> dict[str, int | float] | None:
    if not values:
        return None
    ordered = sorted(values)
    def q(p: float) -> float:
        pos = p * (len(ordered) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(ordered) - 1)
        return round(ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo), 6) if hi != lo else ordered[lo]
    return {"p10": q(.1), "p50": q(.5), "p90": q(.9)}


def _coalesce_cap_spans(spans: list[dict[str, Any]], target_tokens: list[str],
                        reference: list[tuple[str, str]], max_span: int) -> list[dict[str, Any]]:
    """Attribute a capped continuation to the first document containing its full run."""
    source_strings = [(source, " " + " ".join(_TOKEN.findall(text.lower())) + " ")
                      for source, text in reference]
    out: list[dict[str, Any]] = []
    last_was_capped = False
    for span in spans:
        start, end = span["start"], span["start"] + span["length"]
        source = None
        if out and last_was_capped and out[-1]["end"] == start:
            needle = " " + " ".join(target_tokens[out[-1]["start"]:end]) + " "
            source = next((src for src, body in source_strings if needle in body), None)
        if source is not None:
            out[-1]["end"] = end
            out[-1]["source"] = source
        else:
            out.append({"start": start, "end": end, "source": span["source"]})
        last_was_capped = span["length"] == max_span
    return out


def _segments(spans: list[dict[str, Any]], n_tokens: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cursor = 0
    for span in spans:
        if cursor < span["start"]:
            out.append({"start": cursor, "end": span["start"], "source": None})
        end = span["end"]
        out.append(span.copy())
        cursor = end
    if cursor < n_tokens:
        out.append({"start": cursor, "end": n_tokens, "source": None})
    # A max_span cap may split a continuous copy into adjacent counted spans.
    # The style-control population is the continuous attributed source run.
    runs: list[dict[str, Any]] = []
    for segment in out:
        if runs and runs[-1]["source"] == segment["source"] and runs[-1]["end"] == segment["start"]:
            runs[-1]["end"] = segment["end"]
        else:
            runs.append(segment.copy())
    return runs


def _window_pair(text: str, boundary: int, left_start: int, right_end: int,
                 sentences: list[tuple[int, int]], count: int) -> tuple[str, str]:
    """Sentence anchored windows clipped to their segment and junction."""
    before = [s for s in sentences if s[0] < boundary and s[1] > left_start]
    after = [s for s in sentences if s[1] > boundary and s[0] < right_end]
    if not before or not after:
        return "", ""
    start = max(left_start, before[max(0, len(before) - count)][0])
    end = min(right_end, after[min(count, len(after)) - 1][1])
    return text[start:boundary].strip(), text[boundary:end].strip()


def audit_mosaic(target_text: str, reference: list[tuple[str, str]], *,
                 min_ngram: int = DEFAULT_MIN_NGRAM,
                 junction_sentences: int = 2,
                 max_span: int = _MAX_SPAN,
                 max_target_tokens: int = HARD_MAX_TARGET_TOKENS,
                 max_reference_tokens: int = HARD_MAX_REFERENCE_TOKENS,
                 max_reference_docs: int = HARD_MAX_REFERENCE_DOCS) -> dict[str, Any]:
    """Reuse DJ-Search's exact greedy cover and first-containing source rule."""
    if not 1 <= junction_sentences <= 2 or max_span < min_ngram or min_ngram < 1:
        raise ValueError("junction_sentences must be 1 or 2; min_ngram >= 1; max_span >= min_ngram")
    if max_span > HARD_MAX_SPAN:
        raise ValueError(f"max_span exceeds hard limit {HARD_MAX_SPAN}")
    for name, requested, hard in (
        ("max_target_tokens", max_target_tokens, HARD_MAX_TARGET_TOKENS),
        ("max_reference_tokens", max_reference_tokens, HARD_MAX_REFERENCE_TOKENS),
        ("max_reference_docs", max_reference_docs, HARD_MAX_REFERENCE_DOCS),
    ):
        if not 1 <= requested <= hard:
            raise ValueError(f"{name} must be between 1 and hard limit {hard}")
    target_n = len(_TOKEN.findall(target_text.lower()))
    reference_n = sum(len(_TOKEN.findall(text.lower())) for _, text in reference)
    if target_n > max_target_tokens:
        raise ValueError(f"target has {target_n} tokens; limit is {max_target_tokens}")
    if reference_n > max_reference_tokens:
        raise ValueError(f"reference pool has {reference_n} tokens; limit is {max_reference_tokens}")
    if len(reference) > max_reference_docs:
        raise ValueError(f"reference pool has {len(reference)} docs; limit is {max_reference_docs}")
    original = audit_originality(target_text, reference, min_ngram=min_ngram,
                                max_span=max_span, include_spans=True)
    spans = original["all_spans"]
    lowered = target_text.lower()
    tokens = list(_TOKEN.finditer(lowered))
    # Matching uses lowercase text, but published offsets and style windows
    # refer to the original. Unicode lowercasing can expand a character (İ),
    # shifting every subsequent match in the lowercase string.
    if len(lowered) == len(target_text):
        token_spans = [(m.start(), m.end()) for m in tokens]
    else:
        original_offsets = [i for i, ch in enumerate(target_text) for _ in ch.lower()]
        token_spans = [(original_offsets[m.start()], original_offsets[m.end() - 1] + 1)
                       for m in tokens]
    attributed = _coalesce_cap_spans(spans, [m.group() for m in tokens], reference, max_span)
    segments = _segments(attributed, len(tokens))
    covered = sum(s["length"] for s in spans)
    source_tokens = Counter()
    for span in attributed:
        if span["source"] is not None:
            source_tokens[span["source"]] += span["end"] - span["start"]
    uncovered = [s["end"] - s["start"] for s in segments if s["source"] is None]
    sentences = sentence_spans(target_text)
    # Each sentence's [token_start, token_end) range. Source runs are token
    # ranges, so containment is decided in token space: a sentence's char span
    # also carries closing punctuation and leading quotes that no token covers.
    sentence_tokens: list[tuple[int, int] | None] = []
    token_cursor = 0
    for a, b in sentences:
        while token_cursor < len(tokens) and token_spans[token_cursor][1] <= a:
            token_cursor += 1
        first = token_cursor
        while token_cursor < len(tokens) and token_spans[token_cursor][0] < b:
            token_cursor += 1
        sentence_tokens.append((first, token_cursor) if first < token_cursor else None)

    # One normalization population for both junction and within-span comparisons.
    pairs: list[tuple[str, str]] = []
    junctions: list[dict[str, Any]] = []
    for left, right in zip(segments, segments[1:]):
        if left["source"] == right["source"]:
            continue
        boundary = token_spans[right["start"]][0]
        left_start = token_spans[left["start"]][0]
        right_end = token_spans[right["end"] - 1][1]
        pair = _window_pair(target_text, boundary, left_start, right_end,
                            sentences, junction_sentences)
        idx = len(pairs) if all(pair) else None
        if idx is not None:
            pairs.append(pair)
        boundary_type = "source_join" if left["source"] is not None and right["source"] is not None else "coverage_join"
        junctions.append({
            "token_offset": right["start"], "char_offset": boundary,
            "left_tokens": left["end"] - left["start"],
            "right_tokens": right["end"] - right["start"],
            "left_source": left["source"], "right_source": right["source"],
            "boundary_type": boundary_type,
            "distance": None, "distance_reason": None if idx is not None else "insufficient_text",
            "_pair": idx,
        })

    control_indices: list[int] = []
    for segment in segments:
        if segment["source"] is None:
            continue
        whole = [span for span, rng in zip(sentences, sentence_tokens)
                 if rng is not None and segment["start"] <= rng[0]
                 and rng[1] <= segment["end"]]
        for boundary_index in range(junction_sentences,
                                    len(whole) - junction_sentences + 1):
            left = whole[boundary_index - junction_sentences:boundary_index]
            right = whole[boundary_index:boundary_index + junction_sentences]
            if left[-1][1] > right[0][0]:
                continue
            pair = (target_text[left[0][0]:left[-1][1]].strip(),
                    target_text[right[0][0]:right[-1][1]].strip())
            if all(pair):
                control_indices.append(len(pairs))
                pairs.append(pair)

    features = [window_features(w) for pair in pairs for w in pair]
    # Fit on every whole-document window of each compared size, including
    # uncovered regions. Both one- and two-sentence rows are required: fitting
    # sentence count on singleton rows alone would give a false zero variance.
    sentence_rows = [window_features(target_text[a:b]) for a, b in sentences]
    document_windows = [target_text[a:b] for a, b in sentences]
    if junction_sentences == 2:
        document_windows.extend(target_text[sentences[i][0]:sentences[i + 1][1]]
                                for i in range(len(sentences) - 1))
    basis_rows = [window_features(window) for window in document_windows]
    # Bounded vocabulary, deterministic across machines: all function words and
    # shape features, then top 256 character n-grams by aggregate raw frequency.
    is_char = lambda name: name.startswith(("ch3:", "ch4:", "ch5:"))
    stable_names = {name for row in basis_rows for name in row if not is_char(name)}
    char_totals: Counter[str] = Counter()
    def count_chars(window: str) -> None:
        normalized_text = re.sub(r"\s+", " ", window.lower()).strip()
        for n in (3, 4, 5):
            char_totals.update(f"ch{n}:{normalized_text[i:i+n]}"
                               for i in range(max(0, len(normalized_text) - n + 1)))
    for window in document_windows:
        count_chars(window)
    stable = sorted(stable_names)
    selected_chars = sorted(char_totals, key=lambda name: (-char_totals[name], name))[:256]
    names = stable + selected_chars
    normalized = z_score_against(features, basis_rows, names) if features else []
    distances = [round(cosine_distance(normalized[i], normalized[i + 1], names), 6)
                 for i in range(0, len(normalized), 2)]
    for junction in junctions:
        idx = junction.pop("_pair")
        if idx is not None:
            junction["distance"] = distances[idx]
    within_distances = [distances[i] for i in control_indices]
    sentence_series = []
    for (a, b), rng, row in zip(sentences, sentence_tokens, sentence_rows):
        sentence_series.append({
            "char_start": a, "char_end": b,
            "token_start": rng[0] if rng else None,
            "token_end": rng[1] if rng else None,
            "features": {name: row[name] for name in names if row.get(name, 0.0) != 0.0},
        })
    return {
        "coverage": original["coverage"],
        "n_counted_spans": len(spans),
        "span_length_quantiles": _quantiles([s["length"] for s in spans]),
        "n_distinct_sources": len(source_tokens),
        "sources_per_100_covered_tokens": round(100 * len(source_tokens) / covered, 6) if covered else None,
        "largest_source_share": round(max(source_tokens.values()) / covered, 6) if source_tokens and covered else None,
        "uncovered_share": round(1 - original["coverage"], 6),
        "uncovered_run_quantiles": _quantiles(uncovered),
        "junctions": junctions,
        "source_join_distance_quantiles": _quantiles([
            j["distance"] for j in junctions
            if j["boundary_type"] == "source_join" and j["distance"] is not None]),
        "coverage_join_distance_quantiles": _quantiles([
            j["distance"] for j in junctions
            if j["boundary_type"] == "coverage_join" and j["distance"] is not None]),
        "within_span_distance_quantiles": _quantiles(within_distances),
        "sentence_features": sentence_series,
        "feature_vocabulary": names,
        "min_ngram": min_ngram,
        "max_span_cap": max_span,
        "longest_match_capped": original["longest_match_capped"],
        "n_reference_docs": original["n_reference_docs"],
        "assumptions": {
            "method": "Greedy DJ-Search cover; first containing reference document attributes each counted span",
            "pool_dependence": "Only sources in the named pool can be counted; low coverage cannot establish absence of reuse",
            "junction_lens": "Shipped segmentation function-word, character n-gram and sentence-shape lens; fixed rule sentence splitter",
            "junction_sentences": junction_sentences,
            "max_span_cap": max_span,
            "input_limits": {"target_tokens": max_target_tokens,
                             "reference_tokens": max_reference_tokens,
                             "reference_docs": max_reference_docs,
                             "max_span": HARD_MAX_SPAN},
            "reference_tokens": reference_n,
            "feature_vocabulary": "all function-word and sentence-shape features plus top 256 character n-grams by summed raw window frequency, lexicographic ties",
            "zero_covered_tokens": "multiplicity rate and largest source share are null when no tokens are covered",
        },
    }


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "Coverage by verbatim token spans of at least min_ngram tokens from the named pool, "
            "the number of source IDs canonically assigned under the first-containing-document "
            "rule, and style distances at their joins."
        ),
        "does_not_license": (
            "AI/human, authorship, plagiarism, copyright, intent, or claims about sources absent "
            "from the pool. A low-coverage result cannot rule out reuse; a canonically assigned "
            "source ID need not be the actual origin when several documents contain a passage. "
            "No threshold or verdict is supplied."
        ),
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    target_path = Path(args.target)
    try:
        text = target_path.read_text(encoding="utf-8")
        loaded = (_load_reference_dir(Path(args.reference_dir)) if args.reference_dir
                  else _load_reference_manifest(Path(args.manifest)))
    except (OSError, UnicodeDecodeError) as exc:
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME,
                                  version=SCRIPT_VERSION, target_path=str(target_path),
                                  reason=f"cannot read input: {exc}", reason_category="bad_input")
    target_abs = target_path.resolve()
    fingerprint = _content_fingerprint(text)
    reference = [(src, source_text) for src, source_text, path in loaded
                 if not ((path is not None and path == target_abs)
                         or _content_fingerprint(source_text) == fingerprint)]
    try:
        results = audit_mosaic(text, reference, min_ngram=args.min_ngram,
                               junction_sentences=args.junction_sentences,
                               max_span=args.max_span,
                               max_target_tokens=args.max_target_tokens,
                               max_reference_tokens=args.max_reference_tokens,
                               max_reference_docs=args.max_reference_docs)
    except ValueError as exc:
        return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME,
                                  version=SCRIPT_VERSION, target_path=str(target_path),
                                  reason=str(exc), reason_category="bad_input")
    results["assumptions"]["n_dropped_self"] = len(loaded) - len(reference)
    return build_output(task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
                        target_path=str(target_path), target_words=len(_TOKEN.findall(text.lower())),
                        baseline={"reference": args.reference_dir or args.manifest,
                                  "n_reference_docs": results["n_reference_docs"]},
                        results=results,
                        claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--reference-dir")
    group.add_argument("--manifest")
    parser.add_argument("--min-ngram", type=int, default=DEFAULT_MIN_NGRAM)
    parser.add_argument("--max-span", type=int, default=_MAX_SPAN)
    parser.add_argument("--junction-sentences", type=int, default=2)
    parser.add_argument("--max-target-tokens", type=int, default=HARD_MAX_TARGET_TOKENS)
    parser.add_argument("--max-reference-tokens", type=int, default=HARD_MAX_REFERENCE_TOKENS)
    parser.add_argument("--max-reference-docs", type=int, default=HARD_MAX_REFERENCE_DOCS)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    envelope = _run(args)
    output = json.dumps(envelope, indent=2, sort_keys=True)
    if args.out:
        try:
            Path(args.out).write_text(output + "\n", encoding="utf-8")
        except OSError as exc:
            parser.error(f"cannot write --out: {exc}")
    if args.json or not args.out:
        print(output)
    return 0 if envelope["available"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
