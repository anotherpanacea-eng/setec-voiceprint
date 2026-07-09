#!/usr/bin/env python3
"""
idiolect_detector.py

Keyness and collocation extraction for voice-coherence work.

This script asks a narrow question: which words and phrases are unusually
characteristic of a target corpus against a reference corpus? The intended
use is voice preservation ("do not normalize these phrases"), not provenance
detection or authorship certification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from manifest_validator import resolve_path, validate_manifest
from preprocessing import (
    aggregate_preprocessing_metadata,
    available_rule_names,
    parse_rule_names,
    strip_non_prose,
)
from stylometry_core import FUNCTION_WORDS, WORD_RE, word_tokens

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_baseline_metadata, build_output  # type: ignore


TASK_SURFACE = "voice_coherence"
TOOL_NAME = "idiolect_detector"
SCRIPT_VERSION = "1.0"
PRIVACY_WARNING = (
    "PRIVATE - DO NOT SHARE. Idiolect output is a voice-cloning input."
)
DEFAULT_N_VALUES = (1, 2, 3)
DEFAULT_PRESERVATION_QUOTAS = (20, 20, 10)
PRIVATE_OUTPUT_MARKER = "ai-prose-baselines-private"


class CorpusLoadError(Exception):
    """Raised when a target/reference corpus cannot be loaded safely."""


@dataclass
class TextEntry:
    id: str
    path: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CorpusData:
    label: str
    entries: list[TextEntry]
    tokens: list[str]
    surface_tokens: list[str]
    counts_by_n: dict[int, Counter[tuple[str, ...]]]
    surface_by_ngram: dict[tuple[str, ...], Counter[str]]
    preprocessing: dict[str, Any]
    skipped_files: list[str] = field(default_factory=list)

    @property
    def token_count(self) -> int:
        return len(self.tokens)

    def ngram_total(self, n: int) -> int:
        return sum(self.counts_by_n.get(n, Counter()).values())


def md_cell(value: Any) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "--"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}"
    return str(value)


def surface_word_tokens(text: str) -> list[str]:
    return WORD_RE.findall(text)


# ASCII unit separator: bounds the serialized token stream so ["ab"] and ["a","b"] stay distinct.
_FP_SEP = "\x1f"


def _content_fingerprint(text: str) -> str:
    """sha256 of the ``word_tokens`` stream (lowercased ``[A-Za-z']+``) — the keyness matcher's OWN
    equivalence class. Keyness counts ``word_tokens`` n-grams (target vs reference), so two docs with
    the same ``word_tokens`` stream contribute identically; a reference entry carrying a copy of a
    target doc (even a case/punctuation variant) is keyness-equivalent to it and must be dropped from
    the reference, or the target's own idiolectic words appear in the reference and deflate keyness.

    ``build_corpus`` runs ``strip_non_prose`` BEFORE ``word_tokens``, so this fingerprint must be
    computed on the same cleaned text the matcher actually scores — callers pass the
    ``strip_non_prose`` output (see ``exclude_target_from_reference``). Fingerprinting raw text would
    miss a reference copy that differs only in stripped material (YAML front matter, code fences,
    footers) yet scores identically after preprocessing.

    Matcher-aligned (sibling of the Codex self-exclusion sweep: originality_audit #278 /
    rank_turbulence_audit #280). Fail-CLOSED: the token stream over-collapses relative to raw text
    (case/punctuation folded), so a match only DROPS a reference entry, never re-admits one; a
    genuinely different reference doc has a different token stream and is KEPT."""
    return hashlib.sha256(_FP_SEP.join(word_tokens(text)).encode("utf-8")).hexdigest()


def _resolved_path_key(path: str | None) -> str | None:
    """Best-effort filesystem identity for the path guard. Real files resolve to an absolute path;
    non-filesystem ids (e.g. ``nltk.corpus.brown``) can't collide with a target file path, so a
    resolve failure just yields ``None`` (content guard still applies)."""
    if not path:
        return None
    try:
        return str(Path(path).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return None


def exclude_target_from_reference(
    target_entries: list[TextEntry],
    reference_entries: list[TextEntry],
    *,
    allow_non_prose: bool = False,
    strip_rules: str | None = None,
    strip_aggressive: bool = False,
) -> tuple[list[TextEntry], list[TextEntry]]:
    """Drop any reference entry that IS a target doc — by resolved PATH or content FINGERPRINT.

    Returns ``(kept_reference, dropped_reference)``. ``load_target_entries`` and
    ``load_reference_entries`` draw from INDEPENDENT sources with no cross-check, so a target doc that
    also sits in the reference pool (same path, a content-duplicate at another path, or an inline
    manifest row) contaminates the reference and deflates the target-vs-reference keyness. Path OR
    content -> exclude; a content match only DROPS, never re-admits (fail-closed).

    The content fingerprint is computed on the ``strip_non_prose``-cleaned text using the SAME strip
    options ``build_corpus`` scores with, so a reference copy that differs from a target only in
    stripped material (front matter, code fences, footers) is still recognized as a duplicate and
    dropped — the guard's equivalence class matches the keyness matcher's scoring input."""
    def _clean(text: str) -> str:
        cleaned, _ = strip_non_prose(
            text, strip_rules,
            allow_non_prose=allow_non_prose,
            strip_aggressive=strip_aggressive,
        )
        return cleaned

    target_fps = {_content_fingerprint(_clean(e.text)) for e in target_entries}
    target_paths = {k for k in (_resolved_path_key(e.path) for e in target_entries) if k is not None}
    kept: list[TextEntry] = []
    dropped: list[TextEntry] = []
    for entry in reference_entries:
        path_key = _resolved_path_key(entry.path)
        path_match = path_key is not None and path_key in target_paths
        content_match = _content_fingerprint(_clean(entry.text)) in target_fps
        if path_match or content_match:
            dropped.append(entry)
        else:
            kept.append(entry)
    return kept, dropped


def ngrams(tokens: list[str], n: int) -> Iterable[tuple[str, ...]]:
    if n <= 0:
        return
    for idx in range(0, max(0, len(tokens) - n + 1)):
        yield tuple(tokens[idx:idx + n])


def phrase_text(ngram: tuple[str, ...]) -> str:
    return " ".join(ngram)


def parse_int_list(value: str, *, field_name: str) -> tuple[int, ...]:
    try:
        parsed = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{field_name} must be a comma-separated list of integers."
        ) from exc
    if not parsed:
        raise argparse.ArgumentTypeError(f"{field_name} must include at least one value.")
    if any(v <= 0 for v in parsed):
        raise argparse.ArgumentTypeError(f"{field_name} values must be positive.")
    return parsed


def parse_filter(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    filters: dict[str, str] = {}
    for raw in value.split(","):
        part = raw.strip()
        if not part:
            continue
        if "=" not in part:
            raise CorpusLoadError(
                f"Manifest filter '{part}' is not field=value syntax."
            )
        key, expected = part.split("=", 1)
        key = key.strip()
        expected = expected.strip()
        if not key or not expected:
            raise CorpusLoadError(
                f"Manifest filter '{part}' is not field=value syntax."
            )
        filters[key] = expected
    return filters


def matches_filter(value: Any, expected: str) -> bool:
    if isinstance(value, list):
        return expected in {str(v) for v in value}
    return str(value) == expected


def manifest_entries(manifest_path: str | Path, filter_text: str | None) -> list[TextEntry]:
    manifest = Path(manifest_path)
    validation = validate_manifest(manifest)
    if validation.get("n_errors", 0):
        messages = "; ".join(
            issue.get("message", "")
            for issue in validation.get("issues", [])
            if issue.get("severity") == "error"
        )
        raise CorpusLoadError(
            "Manifest has validation errors; refusing to run. "
            + (messages or "Run manifest_validator.py for details.")
        )

    filters = parse_filter(filter_text)
    entries: list[TextEntry] = []
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CorpusLoadError(f"Could not read manifest '{manifest}': {exc}.") from exc

    for lineno, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CorpusLoadError(
                f"Malformed JSON on manifest line {lineno}: {exc.msg}."
            ) from exc
        if not isinstance(item, dict):
            raise CorpusLoadError(f"Manifest line {lineno} is not a JSON object.")
        if any(not matches_filter(item.get(k), v) for k, v in filters.items()):
            continue
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise CorpusLoadError(f"Manifest line {lineno} is missing a path.")
        path = resolve_path(manifest, raw_path)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            raise CorpusLoadError(f"Could not read manifest file '{path}': {exc}.") from exc
        entries.append(TextEntry(
            id=str(item.get("id") or path.stem),
            path=str(path),
            text=text,
            metadata=item,
        ))
    if not entries:
        raise CorpusLoadError("Manifest filters matched no entries.")
    return entries


def directory_entries(directory: str | Path) -> list[TextEntry]:
    base = Path(directory)
    if not base.exists():
        raise CorpusLoadError(f"Directory '{base}' does not exist.")
    if not base.is_dir():
        raise CorpusLoadError(f"Path '{base}' is not a directory.")
    paths = sorted(base.glob("*.txt")) + sorted(base.glob("*.md"))
    entries: list[TextEntry] = []
    skipped: list[str] = []
    for path in paths:
        if path.name.startswith(".") or path.name.lower().startswith("readme"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            skipped.append(str(path))
            continue
        entries.append(TextEntry(
            id=path.stem,
            path=str(path),
            text=text,
            metadata={"source": "directory"},
        ))
    if skipped:
        raise CorpusLoadError(
            "Could not read corpus file(s): " + ", ".join(skipped)
        )
    if not entries:
        raise CorpusLoadError(f"Directory '{base}' contained no .txt/.md corpus files.")
    return entries


def brown_reference_entries() -> list[TextEntry]:
    try:
        from nltk.corpus import brown  # type: ignore
    except ImportError as exc:
        raise CorpusLoadError(
            "The Brown reference corpus requires nltk. Install optional "
            "dependency 'nltk>=3.8' and the Brown corpus data."
        ) from exc
    try:
        words = brown.words()
    except LookupError as exc:
        raise CorpusLoadError(
            "NLTK is installed, but the Brown corpus data is missing. "
            "Run: python -m nltk.downloader brown"
        ) from exc
    text = " ".join(str(w) for w in words)
    return [TextEntry(
        id="nltk_brown",
        path="nltk.corpus.brown",
        text=text,
        metadata={"source": "nltk_brown"},
    )]


def build_corpus(
    label: str,
    entries: list[TextEntry],
    *,
    n_values: tuple[int, ...],
    allow_non_prose: bool,
    strip_rules: str | None,
    strip_aggressive: bool,
) -> CorpusData:
    all_tokens: list[str] = []
    all_surface_tokens: list[str] = []
    per_file_meta: dict[str, dict[str, Any]] = {}
    counts_by_n: dict[int, Counter[tuple[str, ...]]] = {
        n: Counter() for n in n_values
    }
    surface_by_ngram: dict[tuple[str, ...], Counter[str]] = defaultdict(Counter)

    for entry in entries:
        cleaned, meta = strip_non_prose(
            entry.text,
            strip_rules,
            allow_non_prose=allow_non_prose,
            strip_aggressive=strip_aggressive,
        )
        key = entry.path or entry.id
        per_file_meta[key] = meta
        lower_tokens = word_tokens(cleaned)
        surface_tokens = surface_word_tokens(cleaned)
        all_tokens.extend(lower_tokens)
        all_surface_tokens.extend(surface_tokens)
        surface_lower = [token.lower() for token in surface_tokens]

        for n in n_values:
            lower_ngrams = list(ngrams(lower_tokens, n))
            surface_ngrams = list(ngrams(surface_tokens, n))
            counts_by_n[n].update(lower_ngrams)
            for lower_ngram, surface_ngram in zip(lower_ngrams, surface_ngrams):
                if tuple(token.lower() for token in surface_ngram) == lower_ngram:
                    surface_by_ngram[lower_ngram][" ".join(surface_ngram)] += 1

    active_rule_names = (
        parse_rule_names(strip_rules)
        if strip_rules is not None
        else available_rule_names(aggressive=strip_aggressive)
    )
    preprocessing = aggregate_preprocessing_metadata(
        per_file_meta,
        rules_active=active_rule_names if not allow_non_prose else [],
        applied=not allow_non_prose,
        opt_out=allow_non_prose,
    )
    if not all_tokens:
        raise CorpusLoadError(f"{label} corpus yielded zero word tokens.")
    return CorpusData(
        label=label,
        entries=entries,
        tokens=all_tokens,
        surface_tokens=all_surface_tokens,
        counts_by_n=counts_by_n,
        surface_by_ngram=dict(surface_by_ngram),
        preprocessing=preprocessing,
    )


def g_test_score(a: int, b: int, target_total: int, reference_total: int) -> float:
    table = [
        [float(a), float(max(target_total - a, 0))],
        [float(b), float(max(reference_total - b, 0))],
    ]
    row_sums = [sum(row) for row in table]
    col_sums = [table[0][0] + table[1][0], table[0][1] + table[1][1]]
    total = sum(row_sums)
    if total <= 0:
        return 0.0
    score = 0.0
    for i in range(2):
        for j in range(2):
            observed = table[i][j]
            if observed <= 0:
                continue
            expected = (row_sums[i] * col_sums[j]) / total
            if expected > 0:
                score += 2.0 * observed * math.log(observed / expected)
    return score


def chi_square_score(a: int, b: int, target_total: int, reference_total: int) -> float:
    table = [
        [float(a), float(max(target_total - a, 0))],
        [float(b), float(max(reference_total - b, 0))],
    ]
    row_sums = [sum(row) for row in table]
    col_sums = [table[0][0] + table[1][0], table[0][1] + table[1][1]]
    total = sum(row_sums)
    if total <= 0:
        return 0.0
    score = 0.0
    for i in range(2):
        for j in range(2):
            expected = (row_sums[i] * col_sums[j]) / total
            if expected > 0:
                score += ((table[i][j] - expected) ** 2) / expected
    return score


def fisher_score(
    a: int,
    b: int,
    target_total: int,
    reference_total: int,
    *,
    side: str,
) -> tuple[float, float]:
    try:
        from scipy.stats import fisher_exact  # type: ignore
    except ImportError as exc:
        raise CorpusLoadError(
            "--keyness-method fisher_exact requires scipy. Install scipy "
            "or choose log_likelihood/chi_square/pmi."
        ) from exc
    table = [
        [a, max(target_total - a, 0)],
        [b, max(reference_total - b, 0)],
    ]
    alternative = "greater" if side == "idiolectic" else "less"
    _odds, p_value = fisher_exact(table, alternative=alternative)
    score = -math.log10(max(p_value, 1e-300))
    return score, p_value


def log_ratio(
    target_count: int,
    reference_count: int,
    target_total: int,
    reference_total: int,
    *,
    alpha: float,
) -> float:
    target_rate = (target_count + alpha) / max(float(target_total), 1.0)
    reference_rate = (reference_count + alpha) / max(float(reference_total), 1.0)
    if reference_rate <= 0:
        return 0.0
    return math.log2(target_rate / reference_rate)


def keyness_score(
    method: str,
    target_count: int,
    reference_count: int,
    target_total: int,
    reference_total: int,
    *,
    side: str,
) -> tuple[float, float | None, str]:
    if method == "log_likelihood":
        return g_test_score(target_count, reference_count, target_total, reference_total), None, "G2"
    if method == "chi_square":
        return chi_square_score(target_count, reference_count, target_total, reference_total), None, "chi_square"
    if method == "fisher_exact":
        score, p_value = fisher_score(
            target_count,
            reference_count,
            target_total,
            reference_total,
            side=side,
        )
        return score, p_value, "-log10_p"
    if method == "pmi":
        score = abs(log_ratio(
            target_count,
            reference_count,
            target_total,
            reference_total,
            alpha=0.5,
        ))
        return score, None, "abs_log2_ratio"
    raise ValueError(f"Unknown keyness method: {method}")


def bigram_likelihood_ratio(
    ngram: tuple[str, ...],
    counts: Counter[tuple[str, ...]],
    tokens: list[str],
) -> float:
    if len(ngram) != 2:
        return 0.0
    total_windows = max(len(tokens) - 1, 0)
    if total_windows <= 0:
        return 0.0
    left, right = ngram
    observed = counts.get(ngram, 0)
    left_count = sum(1 for token in tokens[:-1] if token == left)
    right_count = sum(1 for token in tokens[1:] if token == right)
    a = observed
    b = max(left_count - observed, 0)
    c = max(right_count - observed, 0)
    d = max(total_windows - a - b - c, 0)
    row_sums = [a + b, c + d]
    col_sums = [a + c, b + d]
    total = sum(row_sums)
    if total <= 0:
        return 0.0
    score = 0.0
    for observed_cell, row_idx, col_idx in (
        (a, 0, 0), (b, 0, 1), (c, 1, 0), (d, 1, 1)
    ):
        if observed_cell <= 0:
            continue
        expected = (row_sums[row_idx] * col_sums[col_idx]) / total
        if expected > 0:
            score += 2.0 * observed_cell * math.log(observed_cell / expected)
    return score


def ngram_pmi(
    ngram: tuple[str, ...],
    counts: Counter[tuple[str, ...]],
    tokens: list[str],
) -> float:
    total_ngrams = sum(counts.values())
    if total_ngrams <= 0:
        return 0.0
    unigram_counts = Counter(tokens)
    token_total = len(tokens)
    if token_total <= 0:
        return 0.0
    p_ngram = counts.get(ngram, 0) / total_ngrams
    if p_ngram <= 0:
        return 0.0
    p_parts = 1.0
    for token in ngram:
        p_parts *= unigram_counts.get(token, 0) / token_total
    if p_parts <= 0:
        return 0.0
    return math.log2(p_ngram / p_parts)


def collocation_signal(
    ngram: tuple[str, ...],
    corpus: CorpusData,
    *,
    min_lr: float,
    min_pmi: float,
) -> tuple[bool, float | None, float | None, str]:
    if len(ngram) < 2:
        return True, None, None, "not_applicable"
    counts = corpus.counts_by_n.get(len(ngram), Counter())
    if len(ngram) == 2:
        lr = bigram_likelihood_ratio(ngram, counts, corpus.tokens)
        pmi = ngram_pmi(ngram, counts, corpus.tokens)
        return (lr >= min_lr and pmi >= min_pmi), lr, pmi, "likelihood_ratio"
    pmi = ngram_pmi(ngram, counts, corpus.tokens)
    return pmi >= min_pmi, None, pmi, "pmi_fallback"


def display_form(
    ngram: tuple[str, ...],
    target: CorpusData,
    reference: CorpusData,
    *,
    side: str,
) -> str:
    source = target if side == "idiolectic" else reference
    forms = source.surface_by_ngram.get(ngram)
    if forms:
        return forms.most_common(1)[0][0]
    return phrase_text(ngram)


def should_skip_ngram(ngram: tuple[str, ...], *, include_function_words: bool) -> bool:
    if include_function_words:
        return False
    if len(ngram) == 1:
        return ngram[0] in FUNCTION_WORDS
    return all(token in FUNCTION_WORDS for token in ngram)


def score_candidates(
    target: CorpusData,
    reference: CorpusData,
    *,
    n_values: tuple[int, ...],
    method: str,
    min_target_count: int,
    min_reference_count: int,
    min_total_count: int,
    alpha: float,
    include_function_words: bool,
    min_collocation_lr: float,
    min_collocation_pmi: float,
    collocation_filter: bool,
) -> dict[int, dict[str, list[dict[str, Any]]]]:
    output: dict[int, dict[str, list[dict[str, Any]]]] = {}
    for n in n_values:
        target_counts = target.counts_by_n.get(n, Counter())
        reference_counts = reference.counts_by_n.get(n, Counter())
        target_total = target.ngram_total(n)
        reference_total = reference.ngram_total(n)
        rows_by_side = {"idiolectic": [], "anti_idiolectic": []}
        for ng in set(target_counts) | set(reference_counts):
            if should_skip_ngram(ng, include_function_words=include_function_words):
                continue
            t_count = target_counts.get(ng, 0)
            r_count = reference_counts.get(ng, 0)
            total_count = t_count + r_count
            if total_count < min_total_count:
                continue
            lr = log_ratio(
                t_count, r_count, target_total, reference_total, alpha=alpha
            )
            if lr == 0:
                continue
            side = "idiolectic" if lr > 0 else "anti_idiolectic"
            if side == "idiolectic" and t_count < min_target_count:
                continue
            if side == "anti_idiolectic" and r_count < min_reference_count:
                continue
            score, p_value, score_name = keyness_score(
                method,
                t_count,
                r_count,
                target_total,
                reference_total,
                side=side,
            )
            assoc_corpus = target if side == "idiolectic" else reference
            passes_colloc, colloc_lr, colloc_pmi, colloc_method = collocation_signal(
                ng,
                assoc_corpus,
                min_lr=min_collocation_lr,
                min_pmi=min_collocation_pmi,
            )
            if collocation_filter and len(ng) > 1 and not passes_colloc:
                continue
            target_per_1k = (t_count / max(target.token_count, 1)) * 1000.0
            reference_per_1k = (r_count / max(reference.token_count, 1)) * 1000.0
            row = {
                "phrase": phrase_text(ng),
                "display": display_form(ng, target, reference, side=side),
                "n": n,
                "target_count": t_count,
                "reference_count": r_count,
                "target_per_1000": round(target_per_1k, 3),
                "reference_per_1000": round(reference_per_1k, 3),
                "log2_ratio": round(lr, 4),
                "score": round(score, 4),
                "score_name": score_name,
                "p_value": p_value,
                "collocation_method": colloc_method,
                "collocation_lr": round(colloc_lr, 4) if colloc_lr is not None else None,
                "collocation_pmi": round(colloc_pmi, 4) if colloc_pmi is not None else None,
            }
            rows_by_side[side].append(row)
        for side, rows in rows_by_side.items():
            if side == "idiolectic":
                rows.sort(key=lambda r: (r["score"], r["log2_ratio"], r["target_count"]), reverse=True)
            else:
                rows.sort(key=lambda r: (r["score"], -r["log2_ratio"], r["reference_count"]), reverse=True)
        output[n] = rows_by_side
    return output


def preservation_list(
    rankings: dict[int, dict[str, list[dict[str, Any]]]],
    *,
    n_values: tuple[int, ...],
    quotas: tuple[int, ...],
    top_total: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_quota_map: dict[int, int] = {}
    for idx, n in enumerate(n_values):
        raw_quota_map[n] = quotas[idx] if idx < len(quotas) else 0

    positive_quota_total = sum(q for q in raw_quota_map.values() if q > 0)
    quota_map = dict(raw_quota_map)
    if top_total > 0 and positive_quota_total > top_total:
        scaled: dict[int, int] = {}
        fractions: list[tuple[float, int]] = []
        for n in n_values:
            q = max(raw_quota_map.get(n, 0), 0)
            if q <= 0:
                scaled[n] = 0
                continue
            exact = (q / positive_quota_total) * top_total
            scaled[n] = math.floor(exact)
            fractions.append((exact - scaled[n], n))
        remaining = top_total - sum(scaled.values())
        for _frac, n in sorted(fractions, reverse=True):
            if remaining <= 0:
                break
            scaled[n] += 1
            remaining -= 1
        quota_map = scaled

    for n in n_values:
        quota = quota_map.get(n, 0)
        if quota <= 0:
            continue
        for row in rankings.get(n, {}).get("idiolectic", []):
            key = row["phrase"]
            if key in seen:
                continue
            selected.append(row)
            seen.add(key)
            if sum(1 for item in selected if item["n"] == n) >= quota:
                break

    if len(selected) < top_total:
        backfill: list[dict[str, Any]] = []
        for n in n_values:
            backfill.extend(rankings.get(n, {}).get("idiolectic", []))
        backfill.sort(key=lambda r: (r["score"], r["log2_ratio"], r["target_count"]), reverse=True)
        for row in backfill:
            if len(selected) >= top_total:
                break
            key = row["phrase"]
            if key in seen:
                continue
            selected.append(row)
            seen.add(key)
    return selected[:top_total]


def corpus_summary(corpus: CorpusData) -> dict[str, Any]:
    return {
        "label": corpus.label,
        "n_files": len(corpus.entries),
        "n_tokens": corpus.token_count,
        "files": [
            {
                "id": entry.id,
                "path": entry.path,
                "metadata": entry.metadata,
            }
            for entry in corpus.entries
        ],
    }


def run_idiolect_detector(
    target_entries: list[TextEntry],
    reference_entries: list[TextEntry],
    *,
    n_values: tuple[int, ...] = DEFAULT_N_VALUES,
    method: str = "log_likelihood",
    min_target_count: int = 5,
    min_reference_count: int = 5,
    min_total_count: int = 10,
    alpha: float = 0.5,
    include_function_words: bool = False,
    min_collocation_lr: float = 10.83,
    min_collocation_pmi: float = 3.0,
    collocation_filter: bool = True,
    preservation_quotas: tuple[int, ...] = DEFAULT_PRESERVATION_QUOTAS,
    preservation_top: int = 50,
    allow_non_prose: bool = False,
    strip_rules: str | None = None,
    strip_aggressive: bool = False,
) -> dict[str, Any]:
    # Self-exclusion: drop any reference entry that IS a target doc (same path or content
    # fingerprint) BEFORE scoring, so the target's own idiolect can't leak into its reference and
    # deflate keyness. If this empties the reference, build_corpus below raises CorpusLoadError
    # (fail-closed — never certify an idiolect against an empty/target-contaminated reference).
    reference_entries, dropped_reference = exclude_target_from_reference(
        target_entries, reference_entries,
        allow_non_prose=allow_non_prose,
        strip_rules=strip_rules,
        strip_aggressive=strip_aggressive,
    )
    target = build_corpus(
        "target",
        target_entries,
        n_values=n_values,
        allow_non_prose=allow_non_prose,
        strip_rules=strip_rules,
        strip_aggressive=strip_aggressive,
    )
    reference = build_corpus(
        "reference",
        reference_entries,
        n_values=n_values,
        allow_non_prose=allow_non_prose,
        strip_rules=strip_rules,
        strip_aggressive=strip_aggressive,
    )
    rankings = score_candidates(
        target,
        reference,
        n_values=n_values,
        method=method,
        min_target_count=min_target_count,
        min_reference_count=min_reference_count,
        min_total_count=min_total_count,
        alpha=alpha,
        include_function_words=include_function_words,
        min_collocation_lr=min_collocation_lr,
        min_collocation_pmi=min_collocation_pmi,
        collocation_filter=collocation_filter,
    )
    preserve = preservation_list(
        rankings,
        n_values=n_values,
        quotas=preservation_quotas,
        top_total=preservation_top,
    )
    return {
        "task_surface": TASK_SURFACE,
        "privacy": PRIVACY_WARNING,
        "self_exclusion": {
            "n_reference_dropped": len(dropped_reference),
            "dropped_ids": [e.id for e in dropped_reference],
            "rationale": (
                "reference entries that are a target doc (same path or content fingerprint) are "
                "dropped before keyness so the target's own idiolect cannot leak into its reference"
            ),
        },
        "target_summary": corpus_summary(target),
        "reference_summary": corpus_summary(reference),
        "method": {
            "keyness": method,
            "n_values": list(n_values),
            "smoothing_alpha": alpha,
            "min_target_count": min_target_count,
            "min_reference_count": min_reference_count,
            "min_total_count": min_total_count,
            "include_function_words": include_function_words,
            "collocation_filter": collocation_filter,
            "min_collocation_lr": min_collocation_lr,
            "min_collocation_pmi": min_collocation_pmi,
        },
        "preprocessing": {
            "target": target.preprocessing,
            "reference": reference.preprocessing,
        },
        "rankings": rankings,
        "preservation_list": preserve,
    }


def render_table(rows: list[dict[str, Any]], *, top: int) -> list[str]:
    lines = [
        "| phrase | target n | reference n | target /1k | reference /1k | log2 ratio | score | colloc |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows[:top]:
        colloc = row.get("collocation_lr")
        if colloc is None:
            colloc = row.get("collocation_pmi")
        lines.append(
            f"| `{md_cell(row['display'])}` | "
            f"{row['target_count']} | {row['reference_count']} | "
            f"{fmt(row['target_per_1000'], 2)} | {fmt(row['reference_per_1000'], 2)} | "
            f"{fmt(row['log2_ratio'], 2)} | {fmt(row['score'], 2)} | "
            f"{fmt(colloc, 2)} |"
        )
    return lines


def render_report(result: dict[str, Any], *, top: int) -> str:
    target = result["target_summary"]
    reference = result["reference_summary"]
    method = result["method"]
    lines: list[str] = []
    lines.append("# Idiolect Detector")
    lines.append("")
    lines.append(f"**Task surface:** `{TASK_SURFACE}`")
    lines.append("")
    lines.append(f"**{PRIVACY_WARNING}**")
    lines.append("")
    lines.append(
        "Use this as a voice-preservation aid: phrases here are candidates "
        "to protect during revision, not evidence of authorship or provenance."
    )
    lines.append("")
    lines.append(f"**Target:** {target['n_files']} files, {target['n_tokens']} tokens")
    lines.append(
        f"**Reference:** {reference['n_files']} files, "
        f"{reference['n_tokens']} tokens"
    )
    lines.append(
        f"**Method:** {method['keyness']}; n={','.join(str(n) for n in method['n_values'])}; "
        f"alpha={method['smoothing_alpha']}"
    )
    for label in ("target", "reference"):
        prep = (result.get("preprocessing") or {}).get(label) or {}
        if prep:
            if prep.get("opt_out"):
                lines.append(f"**{label.title()} preprocessing:** skipped by `--allow-non-prose`")
            else:
                ratio = prep.get("strip_ratio", 0.0)
                ratio_str = f"{ratio:.1%}" if isinstance(ratio, (int, float)) else "n/a"
                lines.append(
                    f"**{label.title()} preprocessing:** stripped "
                    f"{prep.get('tokens_stripped', 0)} tokens "
                    f"({ratio_str}; dominant rule: "
                    f"{prep.get('dominant_rule') or 'none'})"
                )
    lines.append("")

    lines.append("## Preservation List")
    lines.append("")
    lines.append(
        "Top positive idiolect candidates after per-n quotas and backfill. "
        "Feed these to revision prompts as phrases not to normalize."
    )
    lines.append("")
    if result["preservation_list"]:
        lines.extend(render_table(result["preservation_list"], top=len(result["preservation_list"])))
    else:
        lines.append("No preservation candidates cleared the current floors.")
    lines.append("")

    for n, blocks in sorted(result["rankings"].items()):
        lines.append(f"## {n}-grams")
        lines.append("")
        lines.append("### Idiolectic")
        lines.append("")
        rows = blocks.get("idiolectic", [])
        if rows:
            lines.extend(render_table(rows, top=top))
        else:
            lines.append("No candidates cleared the current floors.")
        lines.append("")
        lines.append("### Anti-Idiolectic")
        lines.append("")
        anti_rows = blocks.get("anti_idiolectic", [])
        if anti_rows:
            lines.extend(render_table(anti_rows, top=top))
        else:
            lines.append("No candidates cleared the current floors.")
        lines.append("")
    return "\n".join(lines)


def is_private_output_path(path: str | None) -> bool:
    if not path:
        return True
    return PRIVATE_OUTPUT_MARKER in Path(path).expanduser().resolve().parts


def load_target_entries(args: argparse.Namespace) -> list[TextEntry]:
    if args.target_dir:
        return directory_entries(args.target_dir)
    if args.manifest:
        return manifest_entries(args.manifest, args.filter)
    raise CorpusLoadError("Provide --target-dir or --manifest + --filter.")


def load_reference_entries(args: argparse.Namespace) -> list[TextEntry]:
    if args.reference_dir:
        return directory_entries(args.reference_dir)
    if args.reference_manifest:
        return manifest_entries(args.reference_manifest, args.reference_filter)
    if args.reference_corpus == "brown":
        return brown_reference_entries()
    raise CorpusLoadError(
        "Provide one of --reference-dir, --reference-manifest, or "
        "--reference-corpus brown."
    )


def warn_preprocessing(result: dict[str, Any]) -> None:
    for label in ("target", "reference"):
        prep = (result.get("preprocessing") or {}).get(label) or {}
        if prep.get("strip_ratio", 0.0) and prep.get("strip_ratio", 0.0) >= 0.05:
            print(
                f"Warning: {label} preprocessing stripped "
                f"{prep.get('strip_ratio', 0.0):.1%} of input tokens "
                f"(dominant rule: {prep.get('dominant_rule') or 'none'}).",
                file=sys.stderr,
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract idiolectic and anti-idiolectic words/phrases by "
            "keyness against a reference corpus."
        )
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--target-dir", help="Directory of target .txt/.md files.")
    target.add_argument("--manifest", help="JSONL manifest for target entries.")
    parser.add_argument(
        "--filter",
        help="Comma-separated manifest filter for target entries, e.g. use=idiolect,persona=blog.",
    )
    reference = parser.add_mutually_exclusive_group(required=True)
    reference.add_argument("--reference-dir", help="Directory of reference .txt/.md files.")
    reference.add_argument("--reference-manifest", help="JSONL manifest for reference entries.")
    reference.add_argument(
        "--reference-corpus",
        choices=("brown",),
        help="Built-in reference corpus. Currently supports NLTK Brown.",
    )
    parser.add_argument(
        "--reference-filter",
        help="Comma-separated manifest filter for reference entries, e.g. use=negative_baseline.",
    )
    parser.add_argument(
        "--n",
        type=lambda value: parse_int_list(value, field_name="--n"),
        default=DEFAULT_N_VALUES,
        help="Comma-separated n-gram lengths to score (default 1,2,3).",
    )
    parser.add_argument(
        "--keyness-method",
        choices=("log_likelihood", "chi_square", "pmi", "fisher_exact"),
        default="log_likelihood",
        help="Association statistic for target-vs-reference keyness (default log_likelihood).",
    )
    parser.add_argument("--min-target-count", type=int, default=5)
    parser.add_argument("--min-reference-count", type=int, default=5)
    parser.add_argument("--min-total-count", type=int, default=10)
    parser.add_argument("--smoothing-alpha", type=float, default=0.5)
    parser.add_argument(
        "--include-function-words",
        action="store_true",
        help="Keep function words and all-function-word phrases.",
    )
    parser.add_argument("--min-collocation-lr", type=float, default=10.83)
    parser.add_argument("--min-collocation-pmi", type=float, default=3.0)
    parser.add_argument(
        "--no-collocation-filter",
        action="store_true",
        help="Report multiword keyness candidates even when phrase association is weak.",
    )
    parser.add_argument(
        "--preservation-top",
        type=int,
        default=50,
        help="Maximum positive candidates in the preservation list (default 50).",
    )
    parser.add_argument(
        "--preservation-quotas",
        type=lambda value: parse_int_list(value, field_name="--preservation-quotas"),
        default=DEFAULT_PRESERVATION_QUOTAS,
        help="Per-n preservation quotas in --n order (default 20,20,10).",
    )
    parser.add_argument(
        "--preservation-output",
        help="Optional path for a one-phrase-per-line preservation list.",
    )
    parser.add_argument(
        "--allow-non-prose",
        action="store_true",
        help="Skip default corpus-hygiene stripping on target and reference corpora.",
    )
    parser.add_argument(
        "--strip-rules",
        help="Comma-separated preprocessing rules to enable. Default: all conservative rules. Available: "
        + ", ".join(available_rule_names()) + ".",
    )
    parser.add_argument(
        "--strip-aggressive",
        action="store_true",
        help="Also strip URLs, image markdown, footnotes, and citation wrappers.",
    )
    parser.add_argument("--top", type=int, default=25, help="Rows per detailed table.")
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    parser.add_argument("--out", help="Write the full report to a file instead of stdout.")
    parser.add_argument(
        "--allow-public-output",
        action="store_true",
        help="Allow writing idiolect/voiceprint outputs outside ai-prose-baselines-private/.",
    )
    args = parser.parse_args()

    if args.manifest and not args.filter:
        parser.error("--manifest requires --filter so target entries are explicit.")
    if args.reference_manifest and not args.reference_filter:
        parser.error("--reference-manifest requires --reference-filter.")
    if args.min_target_count < 1 or args.min_reference_count < 1 or args.min_total_count < 1:
        parser.error("Count floors must be positive integers.")
    if args.smoothing_alpha <= 0:
        parser.error("--smoothing-alpha must be greater than 0.")
    try:
        strip_non_prose(
            "",
            args.strip_rules,
            allow_non_prose=args.allow_non_prose,
            strip_aggressive=args.strip_aggressive,
        )
    except ValueError as exc:
        parser.error(str(exc))

    output_paths = [args.out, args.preservation_output]
    public_paths = [p for p in output_paths if p and not is_private_output_path(p)]
    if public_paths and not args.allow_public_output:
        print(
            "Refusing to write idiolect output outside "
            f"{PRIVATE_OUTPUT_MARKER}/: " + ", ".join(public_paths) + ". "
            "Pass --allow-public-output to override.",
            file=sys.stderr,
        )
        return 2
    if args.allow_public_output:
        print("Warning: --allow-public-output used for voice-cloning-grade idiolect data.", file=sys.stderr)
    if not args.out:
        print(
            "Warning: writing idiolect output to stdout. Treat it as private "
            "voice-cloning-grade data.",
            file=sys.stderr,
        )

    try:
        target_entries = load_target_entries(args)
        reference_entries = load_reference_entries(args)
        result = run_idiolect_detector(
            target_entries,
            reference_entries,
            n_values=args.n,
            method=args.keyness_method,
            min_target_count=args.min_target_count,
            min_reference_count=args.min_reference_count,
            min_total_count=args.min_total_count,
            alpha=args.smoothing_alpha,
            include_function_words=args.include_function_words,
            min_collocation_lr=args.min_collocation_lr,
            min_collocation_pmi=args.min_collocation_pmi,
            collocation_filter=not args.no_collocation_filter,
            preservation_quotas=args.preservation_quotas,
            preservation_top=args.preservation_top,
            allow_non_prose=args.allow_non_prose,
            strip_rules=args.strip_rules,
            strip_aggressive=args.strip_aggressive,
        )
    except CorpusLoadError as exc:
        print(f"IdiolectError: {exc}", file=sys.stderr)
        return 1

    warn_preprocessing(result)
    if args.json:
        payload = build_audit_payload(
            result,
            target_path=(args.target_dir or args.target_manifest),
            reference_path=(args.reference_dir or args.reference_manifest),
        )
        output = json.dumps(payload, indent=2, default=str)
    else:
        output = render_report(result, top=args.top)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(output)

    if args.preservation_output:
        lines = [row["display"] for row in result.get("preservation_list", [])]
        Path(args.preservation_output).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Preservation list written to {args.preservation_output}", file=sys.stderr)
    return 0


def _claim_license(result: dict[str, Any]) -> ClaimLicense:
    """Structured ClaimLicense for the idiolect-detector output.

    Per ``internal/SPEC_output_schema_unification.md`` §11, scripts
    that lacked a claim_license gain one as part of migration. The
    preservation list is voice-coherence preservation guidance — its
    purpose is to flag phrases a writer should preserve through
    revision, not to certify authorship.
    """
    target = result.get("target_summary", {}) or {}
    reference = result.get("reference_summary", {}) or {}
    method = result.get("method", {}) or {}
    preservation = result.get("preservation_list", []) or []
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "A ranked keyness list and a preservation list of "
            "phrases unusually characteristic of the target corpus "
            "against the reference corpus. Output names the phrases "
            "a writer should consider preserving through revision so "
            "their voice survives the editing pass."
        ),
        does_not_license=(
            "An authorship verdict. The output is a corpus-relative "
            "keyness ranking, not provenance certification. Highly "
            "characteristic phrases may simply be the writer's "
            "personal lexicon, topic vocabulary, register markers, "
            "or domain terminology. CRITICAL: the preservation list "
            "is voice-cloning-grade input by design. Treat the "
            "rendered list as private to the writer and workspace."
        ),
        comparison_set={
            "target_n_files": target.get("n_files"),
            "target_n_tokens": target.get("n_tokens"),
            "reference_n_files": reference.get("n_files"),
            "reference_n_tokens": reference.get("n_tokens"),
            "keyness_method": method.get("keyness"),
            "n_values": method.get("n_values"),
            "n_preservation_entries": len(preservation),
        },
        additional_caveats=[
            "Keyness is sensitive to the choice of reference corpus. "
            "A reference corpus that under-represents the target's "
            "register / topic / writer-baseline-influence will "
            "produce inflated keyness scores; a tightly register-"
            "matched reference will produce conservative scores. "
            "Read keyness alongside the reference-corpus provenance.",
            "Collocation filters (LR / PMI) drop phrases that fail "
            "the chosen association measure, including some that "
            "are real idiolect tokens with low marginal counts. "
            "The default thresholds favor precision over recall.",
            "Preservation quotas are heuristic. The default split "
            "across n-values may over-favor unigrams when the "
            "writer's actual idiolect lives in 2- and 3-grams; "
            "review the per-n rankings, not just the merged "
            "preservation list.",
        ],
    )


def build_audit_payload(
    result: dict[str, Any],
    *,
    target_path: Path | str | None,
    reference_path: Path | str | None,
) -> dict[str, Any]:
    """Wrap the idiolect-detector result in the schema_version 1.0
    envelope per ``internal/SPEC_output_schema_unification.md``. The
    reference corpus serves as the envelope's baseline (it's what the
    target is compared against).
    """
    target = result.get("target_summary", {}) or {}
    reference = result.get("reference_summary", {}) or {}

    target_words = int(target.get("n_tokens", 0) or 0)
    target_extra: dict[str, Any] = {
        "privacy": result.get("privacy"),
        "n_files": target.get("n_files"),
    }
    if "label" in target:
        target_extra["label"] = target["label"]
    if "files" in target:
        target_extra["files"] = target["files"]
    preprocessing = result.get("preprocessing", {}) or {}
    if preprocessing.get("target"):
        target_extra["preprocessing"] = preprocessing["target"]

    baseline_meta: dict[str, Any] | None = None
    if reference:
        baseline_extras: dict[str, Any] = {}
        if "label" in reference:
            baseline_extras["label"] = reference["label"]
        if "files" in reference:
            baseline_extras["files"] = reference["files"]
        if preprocessing.get("reference"):
            baseline_extras["preprocessing"] = preprocessing["reference"]
        if reference_path is not None:
            baseline_extras["path"] = str(reference_path)
        baseline_meta = build_baseline_metadata(
            n_files=int(reference.get("n_files", 0) or 0),
            words=int(reference.get("n_tokens", 0) or 0),
            extra=baseline_extras or None,
        )

    results = {
        "method": result.get("method", {}),
        "rankings": result.get("rankings", {}),
        "preservation_list": result.get("preservation_list", []),
    }

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=baseline_meta,
        results=results,
        claim_license=_claim_license(result),
        target_extra={
            k: v for k, v in target_extra.items() if v is not None
        } or None,
    )


if __name__ == "__main__":
    sys.exit(main())
