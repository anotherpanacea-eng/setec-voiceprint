#!/usr/bin/env python3
"""paragraph_audit.py — paragraph-level distributional diagnostic.

Surfaces Tier-1 build, paired-release schedule Release 2.

The shipped Layer A suite measures distributional compression at
the *sentence* and *token* layers (sentence-length variance,
burstiness, MATTR / MTLD / Yule's K, FKGL spread, etc.) and the
sliding-window heatmap localizes those signals across word
positions. What's structurally missing: paragraph-level rhythm.
AI editing, professional copyediting, and institutional house
style frequently produce **competent rectangle paragraphs** —
similar paragraph lengths, similar opening shapes, similar
terminal sentences, low macro-rhythm. Sentence-level signals
don't see this; word-windowed heatmaps don't see it either.

This module fills the gap with a lightweight paragraph-level
audit. Signals:

  - Paragraph length distribution (mean, sd, percentiles).
  - Paragraph length variance (the "regularized rectangles"
    signal). High when the writer varies paragraph length
    according to rhetorical need; low when paragraphs converge.
  - First-sentence vs body-sentence length ratio (per-paragraph,
    median across the document).
  - Terminal-sentence punchiness — short final sentences after
    longer body. Captured as a per-paragraph "punchy ending"
    fraction.
  - One-sentence paragraph rate. Writers who use one-sentence
    paragraphs as rhetorical breaks have a distinctive cadence;
    AI-flat paragraphing often suppresses these.
  - Long-paragraph clustering — consecutive paragraphs above the
    document's 75th-percentile length, which signals stretches
    of dense exposition.
  - Paragraph opening typology — declarative / question /
    fragment / conjunction-led / character-led (proper-noun
    open) / quoted (dialogue open) / imperative.
  - Paragraph closing typology — declarative / question /
    fragment / quoted / list-or-colon-trailed / aphoristic.

The output is a band classification (Lightly / Moderately /
Heavily smoothed) over paragraph-rhythm signals, mirroring the
variance_audit conventions, plus per-signal flags and a structured
claim-license block. Optional baseline comparison: when a
``--baseline-dir`` is supplied, the audit runs the same signals
across baseline files and reports z-scores + opening-typology
divergence.

Privacy: paragraph audits emit no raw text — only structural
metrics and typology counts. Safe for public reports.

Usage:

    python3 scripts/paragraph_audit.py INPUT.txt
    python3 scripts/paragraph_audit.py INPUT.txt --json
    python3 scripts/paragraph_audit.py INPUT.txt \\
        --baseline-dir ../baselines/blog-essay/
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from preprocessing import strip_non_prose  # type: ignore

TASK_SURFACE = "smoothing_diagnosis"
TOOL_NAME = "paragraph_audit"
SCRIPT_VERSION = "1.0"

# --- Paragraph splitting ---------------------------------------

_PARAGRAPH_BOUNDARY = re.compile(r"\n\s*\n")
_SENTENCE_TERMINATORS = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(])")
_WORD_RE = re.compile(r"\b\w+\b")


def split_paragraphs(text: str, *, min_words: int = 3) -> list[str]:
    """Split on blank-line boundaries; drop empty / too-short
    paragraphs (headings of 1-2 words frequently sneak in as
    paragraphs in markdown).
    """
    raw = _PARAGRAPH_BOUNDARY.split(text)
    out: list[str] = []
    for p in raw:
        cleaned = p.strip()
        if not cleaned:
            continue
        if len(_WORD_RE.findall(cleaned)) >= min_words:
            out.append(cleaned)
    return out


def split_sentences(paragraph: str) -> list[str]:
    """Light sentence splitter — periods/exclamations/questions
    followed by whitespace and a capital or quote/paren. Doesn't
    handle every abbreviation case, but for paragraph-level rhythm
    diagnostics the noise is acceptable."""
    sentences = _SENTENCE_TERMINATORS.split(paragraph)
    return [s.strip() for s in sentences if s.strip()]


def word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


# --- Opening / closing typology --------------------------------

_OPEN_QUESTION = re.compile(r"^[\"“]?[A-Z]")  # placeholder; refined below
_OPEN_QUOTED = re.compile(r"^[\"“]")
_OPEN_FRAGMENT_HINT = re.compile(r"^(?:And|But|Or|So|Yet|Nor|For)\b", re.IGNORECASE)
_OPEN_CONJUNCTION = re.compile(
    r"^(?:And|But|Or|So|Yet|Nor|For|Because|Although|Though|While|"
    r"When|If|Whenever|Wherever|Since|Unless|Until|After|Before)\b",
    re.IGNORECASE,
)
_OPEN_QUESTION_END = re.compile(r"^\s*[^?]+\?")
_OPEN_IMPERATIVE = re.compile(
    r"^(?:Do|Don't|Stop|Wait|Listen|Look|Consider|Imagine|"
    r"Try|Take|Read|Write|Think|Notice|Observe|Remember|"
    r"Forget|Begin|Start|End|Pick|Choose|Select|Click|Buy|"
    r"Get|Pay|Give|Tell|Ask)\b",
)
_OPEN_PROPER_NOUN = re.compile(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+")


def classify_opening(sentence: str) -> str:
    """Return a typology label for the opening sentence."""
    s = sentence.lstrip()
    if not s:
        return "empty"
    if _OPEN_QUOTED.match(s):
        return "quoted"
    if _OPEN_QUESTION_END.match(s):
        return "question"
    if _OPEN_IMPERATIVE.match(s):
        return "imperative"
    if _OPEN_CONJUNCTION.match(s):
        return "conjunction_led"
    if _OPEN_PROPER_NOUN.match(s):
        return "proper_noun_led"
    if len(_WORD_RE.findall(s)) <= 4:
        return "fragment"
    return "declarative"


_CLOSE_QUESTION = re.compile(r"\?\s*$")
_CLOSE_QUOTED = re.compile(r"[\"”]\s*$")
_CLOSE_LIST_COLON = re.compile(r"[:;]\s*$")
_APHORISM_HINTS = re.compile(
    r"\b(?:always|never|every|all|only|the|a|an)\b",
    re.IGNORECASE,
)


def classify_closing(sentence: str, *, n_words: int) -> str:
    s = sentence.rstrip()
    if not s:
        return "empty"
    if _CLOSE_QUESTION.search(s):
        return "question"
    if _CLOSE_QUOTED.search(s):
        return "quoted"
    if _CLOSE_LIST_COLON.search(s):
        return "list_or_colon"
    if n_words <= 5 and _APHORISM_HINTS.search(s):
        return "aphoristic"
    if n_words <= 4:
        return "fragment"
    return "declarative"


# --- Paragraph-level signals -----------------------------------


def _quantiles(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {"p5": 0.0, "p25": 0.0, "p50": 0.0, "p75": 0.0, "p95": 0.0}
    sv = sorted(values)
    n = len(sv)

    def q(p: float) -> float:
        if n == 1:
            return float(sv[0])
        idx = p * (n - 1)
        lo = int(math.floor(idx))
        hi = int(math.ceil(idx))
        frac = idx - lo
        return float(sv[lo] * (1 - frac) + sv[hi] * frac)

    return {
        "p5": q(0.05),
        "p25": q(0.25),
        "p50": q(0.50),
        "p75": q(0.75),
        "p95": q(0.95),
    }


def audit_paragraphs(text: str) -> dict[str, Any]:
    """Compute the per-paragraph + aggregate paragraph-rhythm
    signal block for a single text. Pure function; no I/O.
    """
    paragraphs = split_paragraphs(text)
    n_paragraphs = len(paragraphs)
    if n_paragraphs == 0:
        return {
            "task_surface": TASK_SURFACE,
            "n_paragraphs": 0,
            "available": False,
            "reason": "no paragraphs",
        }

    para_word_counts: list[int] = []
    one_sentence_count = 0
    first_to_body_ratios: list[float] = []
    punchy_ending_count = 0
    opening_counts: Counter[str] = Counter()
    closing_counts: Counter[str] = Counter()
    per_paragraph: list[dict[str, Any]] = []

    long_threshold_value: float | None = None  # filled after we know p75

    for i, para in enumerate(paragraphs):
        n_words = word_count(para)
        para_word_counts.append(n_words)
        sentences = split_sentences(para)
        n_sentences = len(sentences)

        if n_sentences <= 1:
            one_sentence_count += 1

        opening = classify_opening(sentences[0]) if sentences else "empty"
        opening_counts[opening] += 1
        last_word_count = (
            word_count(sentences[-1]) if sentences else 0
        )
        closing = (
            classify_closing(sentences[-1], n_words=last_word_count)
            if sentences else "empty"
        )
        closing_counts[closing] += 1

        # First-sentence vs. body-sentence length ratio.
        if n_sentences >= 2:
            first_words = word_count(sentences[0])
            body_words = sum(word_count(s) for s in sentences[1:])
            body_mean = body_words / max(1, n_sentences - 1)
            ratio = first_words / max(1.0, body_mean)
            first_to_body_ratios.append(ratio)

        # Terminal-sentence punchiness: final sentence shorter than
        # the body's mean, and shorter than 12 words absolute.
        if n_sentences >= 2:
            body_words = sum(
                word_count(s) for s in sentences[:-1]
            )
            body_mean = body_words / max(1, n_sentences - 1)
            if last_word_count < body_mean and last_word_count <= 12:
                punchy_ending_count += 1

        per_paragraph.append({
            "index": i,
            "n_words": n_words,
            "n_sentences": n_sentences,
            "opening": opening,
            "closing": closing,
        })

    # Length stats.
    mean_words = statistics.mean(para_word_counts)
    sd_words = (
        statistics.stdev(para_word_counts)
        if n_paragraphs > 1 else 0.0
    )
    cv_words = (sd_words / mean_words) if mean_words > 0 else 0.0
    quantiles = _quantiles([float(w) for w in para_word_counts])

    # Long-paragraph clustering: contiguous runs above p75.
    p75 = quantiles["p75"]
    long_clusters: list[dict[str, int]] = []
    cur_run: list[int] = []
    for i, w in enumerate(para_word_counts):
        if w > p75:
            cur_run.append(i)
        else:
            if len(cur_run) >= 3:
                long_clusters.append({
                    "start_paragraph": cur_run[0],
                    "end_paragraph": cur_run[-1],
                    "length_paragraphs": len(cur_run),
                })
            cur_run = []
    if len(cur_run) >= 3:
        long_clusters.append({
            "start_paragraph": cur_run[0],
            "end_paragraph": cur_run[-1],
            "length_paragraphs": len(cur_run),
        })

    # Opening / closing entropy — how varied is the opening style?
    # Low entropy = uniform openings = "competent rectangle" prose.
    def _entropy(counts: Counter[str]) -> float:
        total = sum(counts.values())
        if total == 0:
            return 0.0
        h = 0.0
        for c in counts.values():
            if c <= 0:
                continue
            p = c / total
            h -= p * math.log2(p)
        return h

    opening_entropy = _entropy(opening_counts)
    closing_entropy = _entropy(closing_counts)

    # Median first-to-body ratio (only over multi-sentence
    # paragraphs).
    median_first_to_body = (
        statistics.median(first_to_body_ratios)
        if first_to_body_ratios else 1.0
    )
    one_sentence_rate = one_sentence_count / n_paragraphs
    punchy_ending_rate = punchy_ending_count / n_paragraphs

    # Composite: a smoothing-fraction across paragraph-rhythm
    # signals. Each sub-signal contributes [0, 1] to the fraction
    # when it points toward "regularized rectangles." Intentionally
    # simple — heuristic, calibration-pending.
    flagged_signals: list[str] = []

    cv_flag = 1.0 if cv_words < 0.40 else 0.0
    if cv_flag:
        flagged_signals.append("low_paragraph_length_variance")

    one_sent_flag = 1.0 if one_sentence_rate < 0.05 else 0.0
    if one_sent_flag:
        flagged_signals.append("low_one_sentence_paragraph_rate")

    punchy_flag = 1.0 if punchy_ending_rate < 0.10 else 0.0
    if punchy_flag:
        flagged_signals.append("low_punchy_ending_rate")

    open_entropy_flag = 1.0 if opening_entropy < 1.20 else 0.0
    if open_entropy_flag:
        flagged_signals.append("low_opening_entropy")

    close_entropy_flag = 1.0 if closing_entropy < 1.00 else 0.0
    if close_entropy_flag:
        flagged_signals.append("low_closing_entropy")

    # Long-cluster flag (1.34.1 fix): the previous threshold of
    # `> 0.30` was structurally unreachable, since long_clusters
    # only records runs of paragraphs above the document's p75
    # — at most ~25% of paragraphs by definition. The reviewer
    # reproduced "3/10 long run recorded but not flagged."
    # Lowered to `>= 0.20` and changed to `>=` so a contiguous
    # run of 3+ paragraphs that covers a fifth of the document
    # actually fires the flag. With the p75 ceiling this still
    # only fires on documents with a clearly clustered run, not
    # on uniform distributions.
    long_cluster_flag = 0.0
    for c in long_clusters:
        if c["length_paragraphs"] / n_paragraphs >= 0.20:
            long_cluster_flag = 1.0
            flagged_signals.append("dominant_long_paragraph_cluster")
            break

    n_signals = 6  # cv, one_sent, punchy, open_entropy, close_entropy, long_cluster
    weighted_sum = (
        cv_flag + one_sent_flag + punchy_flag
        + open_entropy_flag + close_entropy_flag + long_cluster_flag
    )
    compression_fraction = weighted_sum / n_signals

    if compression_fraction < 0.20:
        band = "Lightly smoothed"
    elif compression_fraction < 0.50:
        band = "Moderately smoothed"
    else:
        band = "Heavily smoothed"

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "available": True,
        "n_paragraphs": n_paragraphs,
        "paragraph_word_counts": para_word_counts,
        "length_summary": {
            "mean": mean_words,
            "sd": sd_words,
            "cv": cv_words,  # coefficient of variation
            **quantiles,
        },
        "rhythm_signals": {
            "one_sentence_paragraph_rate": one_sentence_rate,
            "punchy_ending_rate": punchy_ending_rate,
            "median_first_to_body_ratio": median_first_to_body,
            "long_paragraph_clusters": long_clusters,
            "opening_entropy_bits": opening_entropy,
            "closing_entropy_bits": closing_entropy,
        },
        "opening_typology": dict(opening_counts),
        "closing_typology": dict(closing_counts),
        "compression": {
            "band": band,
            "compression_fraction": round(compression_fraction, 3),
            "flagged_signals": flagged_signals,
            "n_flagged": len(flagged_signals),
            "n_signals": n_signals,
            "notes": {
                "reliability": (
                    "Below 8 paragraphs the variance estimate is "
                    "noisy; below 4 paragraphs the band call is "
                    "unreliable."
                    if n_paragraphs < 8 else None
                ),
            },
        },
        "per_paragraph": per_paragraph,
    }


# --- Baseline comparison ---------------------------------------


def audit_baseline_paragraphs(
    baseline_dir: str,
    *,
    allow_non_prose: bool = False,
    strip_rules: str | Iterable[str] | None = None,
    strip_aggressive: bool = False,
    strip_masking: str | Iterable[str] | None = None,
    target_path: Path | None = None,
    include_filenames: bool = False,
) -> dict[str, Any]:
    """Run the paragraph audit across every text file in
    ``baseline_dir`` and return aggregate per-document statistics
    plus the pooled distribution of every signal.

    Hardening (1.34.1):
      * ``baseline_dir`` must exist; raises ``FileNotFoundError``
        otherwise (the previous behavior of returning an empty
        baseline silently was a footgun every other tool already
        fixed).
      * Unreadable files surface in ``skipped_files`` with their
        error reasons, not silently dropped.
      * When ``target_path`` is supplied, baseline entries whose
        resolved path matches the target are excluded with a stderr
        notice — same self-overlap guard the GI harness uses.
      * Privacy: ``per_file_summaries`` records anonymized
        ``baseline_001`` IDs by default. Filenames often carry
        manuscript titles, client names, dates, or publication
        subjects — exactly the metadata the framework's other
        tools take care not to leak. Opt in via
        ``include_filenames=True`` when private output is intended.
    """
    base = Path(baseline_dir)
    if not base.is_dir():
        raise FileNotFoundError(
            f"Baseline directory not found or not a directory: "
            f"{baseline_dir}"
        )

    paths = (
        sorted(base.glob("*.txt"))
        + sorted(base.glob("*.md"))
    )
    paths = [p for p in paths if not p.name.lower().startswith("readme")]

    target_resolved: Path | None = None
    if target_path is not None:
        try:
            target_resolved = Path(target_path).resolve()
        except OSError:
            target_resolved = None

    skipped_files: list[dict[str, str]] = []
    per_file: list[dict[str, Any]] = []
    pooled_open: Counter[str] = Counter()
    pooled_close: Counter[str] = Counter()
    pooled_cv: list[float] = []
    pooled_one_sent: list[float] = []
    pooled_punchy: list[float] = []
    pooled_open_h: list[float] = []
    pooled_close_h: list[float] = []
    pooled_first_to_body: list[float] = []
    pooled_para_count: list[int] = []

    next_anon_id = 1
    for p in paths:
        # Target-overlap guard: drop entries whose resolved path
        # equals --target's resolved path. Same convention as
        # general_imposters.py's _exclude_target_path.
        if target_resolved is not None:
            try:
                if p.resolve() == target_resolved:
                    sys.stderr.write(
                        f"  excluding {p.name} from baseline "
                        "(matches target path)\n"
                    )
                    continue
            except OSError:
                pass
        try:
            raw = p.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            skipped_files.append({
                "name": p.name if include_filenames else f"file_{len(skipped_files):03d}",
                "reason": f"unreadable: {exc}",
            })
            continue
        cleaned, _ = strip_non_prose(
            raw, strip_rules,
            allow_non_prose=allow_non_prose,
            strip_aggressive=strip_aggressive,
            strip_masking=strip_masking,
        )
        a = audit_paragraphs(cleaned)
        if not a.get("available"):
            skipped_files.append({
                "name": p.name if include_filenames else f"file_{next_anon_id:03d}",
                "reason": f"audit unavailable: {a.get('reason', 'unknown')}",
            })
            next_anon_id += 1
            continue
        ls = a["length_summary"]
        rs = a["rhythm_signals"]
        per_file.append({
            # Privacy default (1.34.1): anonymized id; opt in to raw
            # filenames with `include_filenames=True`. Filenames
            # often carry manuscript titles / client names /
            # publication subjects.
            "file": (
                p.name if include_filenames
                else f"baseline_{next_anon_id:03d}"
            ),
            "n_paragraphs": a["n_paragraphs"],
            "length_summary": ls,
            "rhythm_signals": rs,
            "opening_typology": a["opening_typology"],
            "closing_typology": a["closing_typology"],
        })
        next_anon_id += 1
        pooled_open.update(a["opening_typology"])
        pooled_close.update(a["closing_typology"])
        pooled_cv.append(ls["cv"])
        pooled_one_sent.append(rs["one_sentence_paragraph_rate"])
        pooled_punchy.append(rs["punchy_ending_rate"])
        pooled_open_h.append(rs["opening_entropy_bits"])
        pooled_close_h.append(rs["closing_entropy_bits"])
        pooled_first_to_body.append(rs["median_first_to_body_ratio"])
        pooled_para_count.append(a["n_paragraphs"])

    def _mean_sd(values: list[float]) -> dict[str, float]:
        if not values:
            return {"mean": 0.0, "sd": 0.0, "n": 0}
        m = statistics.mean(values)
        s = statistics.stdev(values) if len(values) > 1 else 0.0
        return {"mean": m, "sd": s, "n": len(values)}

    return {
        "n_files": len(per_file),
        "n_skipped": len(skipped_files),
        "skipped_files": skipped_files,
        "per_file_summaries": per_file,
        "aggregate": {
            "cv_words": _mean_sd(pooled_cv),
            "one_sentence_paragraph_rate": _mean_sd(pooled_one_sent),
            "punchy_ending_rate": _mean_sd(pooled_punchy),
            "opening_entropy_bits": _mean_sd(pooled_open_h),
            "closing_entropy_bits": _mean_sd(pooled_close_h),
            "median_first_to_body_ratio": _mean_sd(pooled_first_to_body),
            "paragraphs_per_doc": _mean_sd(
                [float(c) for c in pooled_para_count]
            ),
        },
        "pooled_opening_typology": dict(pooled_open),
        "pooled_closing_typology": dict(pooled_close),
        "include_filenames": include_filenames,
    }


def compare_to_baseline(
    target_audit: dict[str, Any],
    baseline_block: dict[str, Any],
) -> dict[str, Any]:
    """Compare a target paragraph audit against a baseline aggregate
    and report z-scores per signal + opening/closing typology
    divergence (as Manhattan distance over normalized counts).
    """
    if not target_audit.get("available"):
        return {"available": False, "reason": "target unavailable"}
    if baseline_block.get("n_files", 0) == 0:
        return {"available": False, "reason": "baseline empty"}

    agg = baseline_block["aggregate"]
    rs = target_audit["rhythm_signals"]
    ls = target_audit["length_summary"]

    def _z(value: float, bucket: dict[str, float]) -> float | None:
        sd = bucket.get("sd", 0.0)
        if sd <= 0 or bucket.get("n", 0) < 2:
            return None
        return (value - bucket["mean"]) / sd

    z_scores = {
        "cv_words": _z(ls["cv"], agg["cv_words"]),
        "one_sentence_paragraph_rate": _z(
            rs["one_sentence_paragraph_rate"],
            agg["one_sentence_paragraph_rate"],
        ),
        "punchy_ending_rate": _z(
            rs["punchy_ending_rate"], agg["punchy_ending_rate"],
        ),
        "opening_entropy_bits": _z(
            rs["opening_entropy_bits"], agg["opening_entropy_bits"],
        ),
        "closing_entropy_bits": _z(
            rs["closing_entropy_bits"], agg["closing_entropy_bits"],
        ),
        "median_first_to_body_ratio": _z(
            rs["median_first_to_body_ratio"],
            agg["median_first_to_body_ratio"],
        ),
    }

    # Opening / closing typology divergence: Manhattan distance
    # between target's typology distribution and baseline's pooled
    # distribution (both normalized).
    def _normalize(c: dict[str, int]) -> dict[str, float]:
        total = sum(c.values()) or 1
        return {k: v / total for k, v in c.items()}

    target_open = _normalize(target_audit["opening_typology"])
    base_open = _normalize(baseline_block["pooled_opening_typology"])
    keys = set(target_open) | set(base_open)
    open_distance = sum(
        abs(target_open.get(k, 0.0) - base_open.get(k, 0.0))
        for k in keys
    )

    target_close = _normalize(target_audit["closing_typology"])
    base_close = _normalize(baseline_block["pooled_closing_typology"])
    keys = set(target_close) | set(base_close)
    close_distance = sum(
        abs(target_close.get(k, 0.0) - base_close.get(k, 0.0))
        for k in keys
    )

    return {
        "available": True,
        "z_scores": z_scores,
        "opening_typology_distance": open_distance,
        "closing_typology_distance": close_distance,
    }


# --- Markdown rendering ----------------------------------------


def _claim_license_block(audit: dict[str, Any]) -> str:
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Paragraph-level rhythm characterization of the input "
            "text: paragraph length variance, opening / closing "
            "typology, one-sentence paragraph rate, punchy-ending "
            "rate, long-paragraph clustering. The output describes "
            "macro-rhythm in this document."
        ),
        does_not_license=(
            "An AI-provenance verdict. The same regularized-"
            "paragraph signature can come from professional "
            "copyediting, institutional house style, policy-memo "
            "templates, translation cleanup, or AI editing — the "
            "audit reports the rhythm; the differential diagnosis "
            "of cause requires the confounder layer (roadmap)."
        ),
        comparison_set={
            "n_paragraphs": audit.get("n_paragraphs"),
            "band": audit.get("compression", {}).get("band"),
        },
        additional_caveats=[
            "Heuristic thresholds are calibration-pending. The "
            "compression-fraction band call uses unweighted "
            "fraction-of-fired-signals; per-signal weights and "
            "calibrated thresholds are roadmap.",
            "Paragraph splitting uses blank-line boundaries; "
            "single-blank-line markdown drafts and prose with "
            "non-standard paragraph conventions may produce "
            "noisier results.",
        ],
    )
    return lic.render_block().rstrip()


def render_report(
    audit: dict[str, Any],
    baseline_comparison: dict[str, Any] | None = None,
) -> str:
    if not audit.get("available"):
        return (
            f"# Paragraph audit\n\n"
            f"_Unavailable: {audit.get('reason', 'unknown')}._\n"
        )
    ls = audit["length_summary"]
    rs = audit["rhythm_signals"]
    c = audit["compression"]
    lines: list[str] = [
        "# Paragraph audit",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Paragraphs:** {audit['n_paragraphs']}",
        "",
        f"**Band:** {c['band']}  "
        f"(compression fraction {c['compression_fraction']:.2f}; "
        f"{c['n_flagged']}/{c['n_signals']} signals fired)",
        "",
    ]
    if c.get("notes", {}).get("reliability"):
        lines.append(f"_Reliability: {c['notes']['reliability']}_")
        lines.append("")

    lines.extend([
        "## Length distribution",
        "",
        "| mean | sd | cv | p5 | p25 | p50 | p75 | p95 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| {ls['mean']:.1f} | {ls['sd']:.1f} | {ls['cv']:.3f} "
            f"| {ls['p5']:.1f} | {ls['p25']:.1f} | {ls['p50']:.1f} "
            f"| {ls['p75']:.1f} | {ls['p95']:.1f} |"
        ),
        "",
        "## Rhythm signals",
        "",
        f"- **One-sentence paragraph rate:** "
        f"{rs['one_sentence_paragraph_rate']:.1%}",
        f"- **Punchy-ending rate:** "
        f"{rs['punchy_ending_rate']:.1%}",
        f"- **Median first/body sentence ratio:** "
        f"{rs['median_first_to_body_ratio']:.2f}",
        f"- **Opening typology entropy:** "
        f"{rs['opening_entropy_bits']:.2f} bits",
        f"- **Closing typology entropy:** "
        f"{rs['closing_entropy_bits']:.2f} bits",
    ])
    if rs["long_paragraph_clusters"]:
        clusters_str = ", ".join(
            f"paragraphs {c['start_paragraph']+1}–{c['end_paragraph']+1} "
            f"({c['length_paragraphs']} consecutive)"
            for c in rs["long_paragraph_clusters"]
        )
        lines.append(f"- **Long-paragraph clusters:** {clusters_str}")
    lines.append("")

    if c["flagged_signals"]:
        lines.append("## Flagged signals")
        lines.append("")
        for sig in c["flagged_signals"]:
            lines.append(f"- `{sig}`")
        lines.append("")

    lines.append("## Opening typology")
    lines.append("")
    lines.append("| type | count |")
    lines.append("|---|---:|")
    for k, v in sorted(
        audit["opening_typology"].items(), key=lambda kv: -kv[1],
    ):
        lines.append(f"| {k} | {v} |")
    lines.append("")

    lines.append("## Closing typology")
    lines.append("")
    lines.append("| type | count |")
    lines.append("|---|---:|")
    for k, v in sorted(
        audit["closing_typology"].items(), key=lambda kv: -kv[1],
    ):
        lines.append(f"| {k} | {v} |")
    lines.append("")

    if baseline_comparison and baseline_comparison.get("available"):
        lines.append("## Baseline comparison")
        lines.append("")
        zs = baseline_comparison["z_scores"]
        lines.append("| signal | z-score |")
        lines.append("|---|---:|")
        for k, z in zs.items():
            z_str = f"{z:+.2f}" if isinstance(z, (int, float)) else "n/a"
            lines.append(f"| {k} | {z_str} |")
        lines.extend([
            "",
            f"- **Opening typology Manhattan distance to baseline:** "
            f"{baseline_comparison['opening_typology_distance']:.3f}",
            f"- **Closing typology Manhattan distance to baseline:** "
            f"{baseline_comparison['closing_typology_distance']:.3f}",
            "",
        ])

    lines.append(_claim_license_block(audit))
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- CLI -------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="paragraph_audit.py",
        description=(
            "Paragraph-level rhythm audit. Detects regularized-"
            "paragraph signatures (the 'competent rectangle' "
            "pattern AI editing and house-style enforcement "
            "produce) that sentence-level variance signals miss."
        ),
    )
    p.add_argument("input", help="Path to .txt or .md target file.")
    p.add_argument(
        "--baseline-dir",
        help=(
            "Directory of baseline .txt / .md files. When supplied, "
            "the audit computes per-signal z-scores against the "
            "baseline aggregate plus opening / closing typology "
            "divergence."
        ),
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit JSON instead of markdown.",
    )
    p.add_argument(
        "--out", help="Write output to this path instead of stdout.",
    )
    p.add_argument(
        "--allow-non-prose", action="store_true",
        help="Skip corpus-hygiene preprocessing (off by default).",
    )
    p.add_argument(
        "--strip-rules",
        help="Comma-separated preprocessing rule names.",
    )
    p.add_argument(
        "--strip-aggressive", action="store_true",
        help="Enable aggressive preprocessing (URLs, footnotes, "
             "citations).",
    )
    p.add_argument(
        "--include-baseline-filenames", action="store_true",
        help=(
            "Include raw baseline filenames in `per_file_summaries` "
            "(privacy default: anonymized as `baseline_001`). "
            "Filenames often carry manuscript titles, client names, "
            "dates, or publication subjects — opt in only when the "
            "report stays in private channels."
        ),
    )
    p.add_argument(
        "--strip-masking",
        help=(
            "Optional masking profile or rule list. Profiles: "
            "prose_body_only, exclude_quotations, exclude_headings, "
            "prose_strict, none. See preprocessing.py for full "
            "rule set."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    target_path = Path(args.input).expanduser()
    if not target_path.is_file():
        sys.stderr.write(f"Input not found: {target_path}\n")
        return 2

    raw = target_path.read_text(encoding="utf-8", errors="ignore")
    cleaned, prep_meta = strip_non_prose(
        raw,
        args.strip_rules,
        allow_non_prose=args.allow_non_prose,
        strip_aggressive=args.strip_aggressive,
        strip_masking=args.strip_masking,
    )
    audit = audit_paragraphs(cleaned)
    audit["preprocessing"] = prep_meta

    baseline_comparison: dict[str, Any] | None = None
    if args.baseline_dir:
        try:
            base_block = audit_baseline_paragraphs(
                args.baseline_dir,
                allow_non_prose=args.allow_non_prose,
                strip_rules=args.strip_rules,
                strip_aggressive=args.strip_aggressive,
                strip_masking=args.strip_masking,
                target_path=target_path,
                include_filenames=args.include_baseline_filenames,
            )
        except FileNotFoundError as exc:
            sys.stderr.write(f"  baseline error: {exc}\n")
            return 2
        audit["baseline_block"] = base_block
        if base_block.get("n_files", 0) == 0:
            sys.stderr.write(
                f"  baseline at {args.baseline_dir} produced 0 "
                "usable files (after target-overlap exclusion + "
                "skipped unreadable files); baseline comparison "
                "skipped.\n"
            )
        baseline_comparison = compare_to_baseline(audit, base_block)
        audit["baseline_comparison"] = baseline_comparison

    if args.json:
        text_out = json.dumps(audit, indent=2, default=str)
    else:
        text_out = render_report(audit, baseline_comparison)

    if args.out:
        Path(args.out).write_text(text_out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(text_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
