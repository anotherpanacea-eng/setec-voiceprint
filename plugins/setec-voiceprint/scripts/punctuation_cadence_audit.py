#!/usr/bin/env python3
"""punctuation_cadence_audit.py — punctuation rhythm + interruption-grammar audit.

Surfaces Tier-2 promotion, paired-release schedule Release 5.
``voice_profile.py`` already captures comma / semicolon / colon /
dash / parenthesis / ellipsis rates as feature columns inside the
durable voiceprint. What's missing is the top-level surface that
treats punctuation as its own diagnostic dimension: punctuation
n-gram divergence, interruption-grammar profile (parentheses /
em-dashes / appositives), smoothing flags for dash-collapse /
semicolon-suppression / comma-regularization.

AI smoothing and professional copyediting often **regularize
punctuation before they erase vocabulary**. This makes punctuation
cadence one of the earliest signals to fire when prose has been
edited toward house style or AI-shaped uniformity — and one of the
most legible to writers, who can read changes in their own
punctuation habits more easily than changes in lexical-diversity
quantiles.

Outputs:

  - Per-mark density (per 1,000 words): comma, semicolon, colon,
    em-dash / hyphen-pair, parenthesis-pair, ellipsis,
    quotation-pair, exclamation, question.
  - Sentence-final punctuation distribution (period vs. question
    vs. exclamation as fractions of sentence-final marks).
  - **Interruption grammar** — the rate of mid-sentence asides:
    parenthetical insertions, em-dash interruptions, comma-bounded
    appositives.
  - **Punctuation bigrams** — pairs of adjacent punctuation marks
    that fall within the same sentence (e.g., ``,—``, ``;-``,
    ``):``). Captures patterns like multi-clause concession or
    nested interruption.
  - Compression-fraction band call (Lightly / Moderately / Heavily
    regularized) over six rhythm signals.
  - Optional baseline comparison emits per-signal z-scores plus
    Manhattan distance over normalized punctuation distributions.

Hardened baseline ingestion (matches paragraph_audit / discourse /
agency 1.34.x conventions): validates baseline directory exists,
surfaces skipped files in ``skipped_files``, excludes target-in-
baseline overlap, anonymizes filenames in JSON output by default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import (  # type: ignore
    ClaimLicense,
    with_state_caveats,
)
from output_schema import build_baseline_metadata, build_output  # type: ignore
from preprocessing import strip_non_prose  # type: ignore

TASK_SURFACE = "voice_coherence"
TOOL_NAME = "punctuation_cadence_audit"
SCRIPT_VERSION = "1.0"


# --- Patterns --------------------------------------------------

_WORD_RE = re.compile(r"\b\w+\b")

# Per-mark counters. We count occurrences across the whole text.
# Em-dashes: include both U+2014 and the typewriter "--" pattern.
# Parentheses: count opens (each open is one parenthetical).
# Ellipsis: U+2026 OR three+ literal dots.
_MARKS = {
    "comma": re.compile(r","),
    "semicolon": re.compile(r";"),
    "colon": re.compile(r":"),
    "em_dash": re.compile(r"—|--+"),
    "en_dash": re.compile(r"–"),
    "parenthesis": re.compile(r"\("),
    "bracket": re.compile(r"\["),
    "ellipsis": re.compile(r"…|\.{3,}"),
    "double_quote_pair": re.compile(r'["“]'),
    "single_quote_pair": re.compile(r"['‘]"),
    "exclamation": re.compile(r"!"),
    "question": re.compile(r"\?"),
}

_SENTENCE_FINAL = re.compile(r"[.!?](?=\s|$)")
_PERIOD_FINAL = re.compile(r"\.(?=\s|$)")
_QUESTION_FINAL = re.compile(r"\?(?=\s|$)")
_EXCL_FINAL = re.compile(r"!(?=\s|$)")

# Interruption grammar: text inside parens, text bounded by em-dashes,
# comma-appositives (commas around 2-6-word noun phrases). The last
# is approximate — anything between two commas of length 2-6 words
# without sentence-final punctuation in between.
_PAREN_INTERRUPTION = re.compile(r"\([^()\n]{3,200}\)")
_EM_DASH_INTERRUPTION = re.compile(
    r"(?:—|--+)[^—\n]{3,150}(?:—|--+)"
)
_COMMA_APPOSITIVE = re.compile(r",\s+\w[\w\s]{1,40}\w\s*,")

# Punctuation bigrams: adjacent punctuation marks inside the same
# sentence. We collect all punctuation "runs" of length >= 2.
_PUNCT_RUN = re.compile(
    r"[,;:.\"'!?—–‘’“”()\[\]…]{2,}"
)


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _content_fingerprint(cleaned_text: str) -> str:
    """sha256 of the WHOLE ``strip_non_prose``-cleaned text — the exact string
    ``audit_punctuation_cadence(cleaned)`` reads. Callers pass the ``strip_non_prose`` output computed
    with the SAME strip options the baseline loader uses, so a baseline file carrying a copy of the
    target — even at a DIFFERENT path than ``--target`` (which the path guard misses under another
    filename), and even one wrapped in front matter the preprocessing strips away — has the same
    cleaned scoring input and is dropped before its cadence pools into the baseline mean/SD and
    deflates the z-scores toward a false "in-distribution" result.

    Unlike the token-stream siblings in the self-exclusion sweep, this surface's signal IS the
    punctuation over the character sequence (per-mark densities, interruption grammar, sentence-final
    distribution, punctuation bigrams) — there is no word-token stream that carries it, so there is
    nothing to hash but the text itself. Hashing the cleaned string directly (no NFC normalization,
    which could over-collapse a Unicode-composition variant the audit treats as distinct) aligns this
    fingerprint to the cleaned scoring input, like ``voice_distance`` (PR #307 Codex review). Its
    equivalence class is the cleaned string itself: it drops only an exact cleaned-text copy (and so
    catches a front-matter-wrapped copy) while KEEPING any text the audit would score differently
    (different whitespace, punctuation, or wording)."""
    return hashlib.sha256(cleaned_text.encode("utf-8")).hexdigest()


def _per_thousand(count: int, n_words: int) -> float:
    if n_words <= 0:
        return 0.0
    return 1000.0 * count / n_words


def audit_punctuation_cadence(text: str) -> dict[str, Any]:
    """Compute the per-mark + interruption + bigram + final-position
    signals for a single text. Pure function; no I/O."""
    n_words = _word_count(text)
    if n_words == 0:
        return {
            "task_surface": TASK_SURFACE,
            "tool": TOOL_NAME,
            "version": SCRIPT_VERSION,
            "available": False,
            "reason": "empty text",
        }

    # Per-mark counts and densities.
    raw_counts: dict[str, int] = {}
    densities: dict[str, float] = {}
    for name, pattern in _MARKS.items():
        c = len(pattern.findall(text))
        raw_counts[name] = c
        densities[f"{name}_per_1k"] = _per_thousand(c, n_words)

    # Sentence-final distribution.
    n_sentence_final = len(_SENTENCE_FINAL.findall(text))
    n_period = len(_PERIOD_FINAL.findall(text))
    n_question = len(_QUESTION_FINAL.findall(text))
    n_excl = len(_EXCL_FINAL.findall(text))
    if n_sentence_final > 0:
        sentence_final_distribution = {
            "period": n_period / n_sentence_final,
            "question": n_question / n_sentence_final,
            "exclamation": n_excl / n_sentence_final,
        }
    else:
        sentence_final_distribution = {
            "period": 1.0, "question": 0.0, "exclamation": 0.0,
        }

    # Interruption grammar.
    n_paren_interruptions = len(_PAREN_INTERRUPTION.findall(text))
    n_em_dash_interruptions = len(_EM_DASH_INTERRUPTION.findall(text))
    n_comma_appositives = len(_COMMA_APPOSITIVE.findall(text))
    interruption_total = (
        n_paren_interruptions
        + n_em_dash_interruptions
        + n_comma_appositives
    )
    interruption_grammar = {
        "parenthetical_per_1k": _per_thousand(n_paren_interruptions, n_words),
        "em_dash_aside_per_1k": _per_thousand(
            n_em_dash_interruptions, n_words,
        ),
        "comma_appositive_per_1k": _per_thousand(
            n_comma_appositives, n_words,
        ),
        "total_interruption_per_1k": _per_thousand(
            interruption_total, n_words,
        ),
    }

    # Punctuation bigrams (multi-mark runs).
    bigram_counts: Counter[str] = Counter()
    for run in _PUNCT_RUN.findall(text):
        # Take the first 2 chars of each run as the bigram. Multi-
        # char runs (e.g. `?!?`) reduce to their leading pair.
        if len(run) >= 2:
            bigram_counts[run[:2]] += 1

    # Compression-fraction band: each sub-signal contributes [0, 1]
    # toward "regularized punctuation rhythm." Calibration-pending
    # heuristic thresholds.
    flagged_signals: list[str] = []

    # Comma-period dominance: if commas + periods together account
    # for > 95% of all internal punctuation marks, the writer's
    # range has collapsed.
    internal_marks = (
        raw_counts["comma"] + raw_counts["semicolon"]
        + raw_counts["colon"] + raw_counts["em_dash"]
        + raw_counts["parenthesis"] + raw_counts["ellipsis"]
        + n_period
    )
    if internal_marks > 0:
        comma_period_share = (
            raw_counts["comma"] + n_period
        ) / internal_marks
    else:
        comma_period_share = 1.0
    if comma_period_share >= 0.95:
        flagged_signals.append("comma_period_dominance")

    # Semicolon suppression: < 0.3 / 1k is low for prose-of-record
    # registers (essay / academic / policy); LLM smoothing tends
    # to remove semicolons.
    if (
        densities.get("semicolon_per_1k", 0.0) < 0.3
        and n_words >= 500
    ):
        flagged_signals.append("low_semicolon_density")

    # Em-dash suppression: < 0.5 / 1k for prose with heavy
    # interruption-grammar style would be unusual.
    if (
        densities.get("em_dash_per_1k", 0.0) < 0.5
        and n_words >= 500
    ):
        flagged_signals.append("low_em_dash_density")

    # Low interruption grammar: parentheticals + em-dash asides +
    # comma-appositives < 3 / 1k for prose-of-record genres.
    if (
        interruption_grammar["total_interruption_per_1k"] < 3.0
        and n_words >= 500
    ):
        flagged_signals.append("low_interruption_grammar")

    # Sentence-final uniformity: if 100% of finals are periods,
    # the writer never uses questions or exclamations — flat
    # interrogative cadence.
    if (
        sentence_final_distribution["period"] >= 0.98
        and n_sentence_final >= 10
    ):
        flagged_signals.append("uniform_sentence_finals")

    # Punctuation bigram poverty: the writer never combines marks
    # within a sentence (no `,—`, `;:`, etc.). This suggests a
    # narrowed punctuation-pair vocabulary.
    if (
        sum(bigram_counts.values()) < max(1, n_words // 1000)
        and n_words >= 1000
    ):
        flagged_signals.append("low_punctuation_bigram_diversity")

    n_signals = 6
    compression_fraction = len(flagged_signals) / n_signals
    if compression_fraction < 0.20:
        band = "Lightly regularized"
    elif compression_fraction < 0.50:
        band = "Moderately regularized"
    else:
        band = "Heavily regularized"

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "available": True,
        "n_words": n_words,
        "n_sentence_final": n_sentence_final,
        "raw_counts": raw_counts,
        "densities_per_1k": densities,
        "sentence_final_distribution": sentence_final_distribution,
        "interruption_grammar": interruption_grammar,
        "punctuation_bigrams": dict(bigram_counts.most_common(20)),
        "comma_period_share": round(comma_period_share, 3),
        "compression": {
            "band": band,
            "compression_fraction": round(compression_fraction, 3),
            "flagged_signals": flagged_signals,
            "n_flagged": len(flagged_signals),
            "n_signals": n_signals,
        },
    }


# --- Baseline comparison ---------------------------------------


def audit_baseline_punctuation(
    baseline_dir: str,
    *,
    allow_non_prose: bool = False,
    strip_rules: str | Iterable[str] | None = None,
    strip_aggressive: bool = False,
    strip_masking: str | Iterable[str] | None = None,
    target_path: Path | None = None,
    target_fingerprint: str | None = None,
    include_filenames: bool = False,
) -> dict[str, Any]:
    """Hardened baseline ingestion (1.34.1 conventions): validate
    directory, surface skipped files, exclude target overlap,
    anonymize filenames by default."""
    base = Path(baseline_dir)
    if not base.is_dir():
        raise FileNotFoundError(
            f"Baseline directory not found or not a directory: "
            f"{baseline_dir}"
        )
    paths = sorted(base.glob("*.txt")) + sorted(base.glob("*.md"))
    paths = [p for p in paths if not p.name.lower().startswith("readme")]

    target_resolved: Path | None = None
    if target_path is not None:
        try:
            target_resolved = Path(target_path).resolve()
        except OSError:
            target_resolved = None

    skipped_files: list[dict[str, str]] = []
    per_file: list[dict[str, Any]] = []
    pooled_densities: dict[str, list[float]] = {}
    pooled_interruption: dict[str, list[float]] = {}
    pooled_finals: dict[str, list[float]] = {}
    next_anon_id = 1

    for p in paths:
        if target_resolved is not None:
            try:
                if p.resolve() == target_resolved:
                    sys.stderr.write(
                        f"  excluding {p.name} from punctuation "
                        "baseline (matches target path)\n"
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
        if (
            target_fingerprint is not None
            and _content_fingerprint(cleaned) == target_fingerprint
        ):
            # A copy of the target at a different path: its cleaned scoring input IS the target's, so
            # pooling it into the baseline would pull the mean/SD toward the target and deflate the
            # z-scores. Compared on the CLEANED text so a copy wrapped in stripped front matter is
            # still caught (and a merely punctuation-/case-distinct baseline, which the audit scores
            # differently, is NOT over-excluded).
            sys.stderr.write(
                f"  excluding {p.name} from punctuation baseline "
                "(content-duplicate of the target)\n"
            )
            continue
        a = audit_punctuation_cadence(cleaned)
        if not a.get("available"):
            skipped_files.append({
                "name": p.name if include_filenames else f"file_{next_anon_id:03d}",
                "reason": f"audit unavailable: {a.get('reason', 'unknown')}",
            })
            next_anon_id += 1
            continue
        per_file.append({
            "file": (
                p.name if include_filenames
                else f"baseline_{next_anon_id:03d}"
            ),
            "densities_per_1k": a["densities_per_1k"],
            "interruption_grammar": a["interruption_grammar"],
            "sentence_final_distribution": a["sentence_final_distribution"],
        })
        next_anon_id += 1
        for k, v in a["densities_per_1k"].items():
            pooled_densities.setdefault(k, []).append(v)
        for k, v in a["interruption_grammar"].items():
            pooled_interruption.setdefault(k, []).append(v)
        for k, v in a["sentence_final_distribution"].items():
            pooled_finals.setdefault(k, []).append(v)

    def _mean_sd(vs: list[float]) -> dict[str, float]:
        if not vs:
            return {"mean": 0.0, "sd": 0.0, "n": 0}
        m = sum(vs) / len(vs)
        if len(vs) > 1:
            var = sum((x - m) ** 2 for x in vs) / (len(vs) - 1)
            sd = var ** 0.5
        else:
            sd = 0.0
        return {"mean": m, "sd": sd, "n": len(vs)}

    return {
        "n_files": len(per_file),
        "n_skipped": len(skipped_files),
        "skipped_files": skipped_files,
        "per_file_summaries": per_file,
        "aggregate_densities": {
            k: _mean_sd(v) for k, v in pooled_densities.items()
        },
        "aggregate_interruption": {
            k: _mean_sd(v) for k, v in pooled_interruption.items()
        },
        "aggregate_sentence_finals": {
            k: _mean_sd(v) for k, v in pooled_finals.items()
        },
        "include_filenames": include_filenames,
    }


def compare_to_baseline(
    target: dict[str, Any],
    baseline_block: dict[str, Any],
) -> dict[str, Any]:
    if not target.get("available"):
        return {"available": False, "reason": "target unavailable"}
    if baseline_block.get("n_files", 0) == 0:
        return {"available": False, "reason": "baseline empty"}

    def _z(value: float, bucket: dict[str, float]) -> float | None:
        sd = bucket.get("sd", 0.0)
        if sd <= 0 or bucket.get("n", 0) < 2:
            return None
        return (value - bucket["mean"]) / sd

    z_densities: dict[str, float | None] = {}
    for k, v in target["densities_per_1k"].items():
        bucket = baseline_block["aggregate_densities"].get(k, {})
        z_densities[k] = _z(v, bucket)

    z_interruption: dict[str, float | None] = {}
    for k, v in target["interruption_grammar"].items():
        bucket = baseline_block["aggregate_interruption"].get(k, {})
        z_interruption[k] = _z(v, bucket)

    # Manhattan distance between target and baseline mean punctuation
    # distributions (densities-per-1k normalized by total).
    target_total = sum(target["densities_per_1k"].values()) or 1.0
    base_means = {
        k: bucket.get("mean", 0.0)
        for k, bucket in baseline_block["aggregate_densities"].items()
    }
    base_total = sum(base_means.values()) or 1.0
    keys = set(target["densities_per_1k"]) | set(base_means)
    distance = sum(
        abs(
            target["densities_per_1k"].get(k, 0.0) / target_total
            - base_means.get(k, 0.0) / base_total
        )
        for k in keys
    )

    return {
        "available": True,
        "z_density": z_densities,
        "z_interruption": z_interruption,
        "punctuation_distribution_distance": distance,
    }


# --- Markdown rendering ----------------------------------------


def _claim_license(audit: dict[str, Any]) -> ClaimLicense:
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Punctuation cadence and interruption-grammar profile "
            "of the input: per-mark density, sentence-final "
            "distribution, parenthetical / em-dash / appositive "
            "interruption rates, punctuation bigram inventory. "
            "Surfaces *what kind* of punctuation rhythm the writer "
            "uses, including patterns that often regularize before "
            "lexical-diversity signals fire."
        ),
        does_not_license=(
            "An AI-provenance verdict. Heavy regularization is "
            "characteristic of professional copyediting, "
            "institutional house style, AI-edited drafts, and "
            "writers in low-interruption-grammar genres alike. The "
            "differential diagnosis of cause is the confounder "
            "audit's job."
        ),
        comparison_set={
            "n_words": audit.get("n_words"),
            "band": audit.get("compression", {}).get("band"),
        },
        additional_caveats=[
            "Pattern matching is regex over English orthography; "
            "non-standard mark conventions (e.g. typewriter dashes "
            "vs. em-dashes vs. en-dashes) collapse into the same "
            "category. Foreign-language quotation marks are "
            "approximated, not exact.",
            "Heuristic thresholds (band call) are calibration-"
            "pending; treat the band as a cue, not a verdict.",
            "Punctuation is genre-bound: a heavily-regularized "
            "punctuation cadence can be entirely correct for "
            "memo / brief / press-release prose. Read alongside "
            "register match.",
        ],
    )
    # B.3: append state-routed caveats when the operator supplied
    # --ai-status. No-op when ai_status is absent — pre-B.3 callers
    # keep their previous behavior.
    return with_state_caveats(
        lic, target_ai_status=audit.get("ai_status"),
    )


def _claim_license_block(audit: dict[str, Any]) -> str:
    return _claim_license(audit).render_block().rstrip()


_RESULTS_KEYS = (
    "n_sentence_final", "raw_counts", "densities_per_1k",
    "sentence_final_distribution", "interruption_grammar",
    "punctuation_bigrams", "comma_period_share", "compression",
)


def build_audit_payload(
    audit: dict[str, Any],
    *,
    target_path: Path | str,
    baseline_block: dict[str, Any] | None,
    baseline_comparison: dict[str, Any] | None,
) -> dict[str, Any]:
    """Wrap the punctuation-cadence audit dict in the schema_version
    1.0 envelope per ``internal/SPEC_output_schema_unification.md``.
    """
    available = bool(audit.get("available", True))
    n_words = int(audit.get("n_words", 0) or 0)
    target_extra: dict[str, Any] = {}
    if "preprocessing" in audit:
        target_extra["preprocessing"] = audit["preprocessing"]

    results: dict[str, Any] = {}
    if available:
        for k in _RESULTS_KEYS:
            if k in audit:
                results[k] = audit[k]
        if baseline_comparison is not None:
            results["baseline_comparison"] = baseline_comparison

    baseline_meta: dict[str, Any] | None = None
    if baseline_block is not None:
        baseline_meta = build_baseline_metadata(
            n_files=int(baseline_block.get("n_files", 0) or 0),
            words=int(baseline_block.get("n_words", 0) or 0),
            extra={
                k: v for k, v in baseline_block.items()
                if k not in {"n_files", "n_words"}
            } or None,
        )

    warnings: list[str] = []
    if not available and "reason" in audit:
        warnings.append(audit["reason"])

    lic = _claim_license(audit) if available else None

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=n_words,
        baseline=baseline_meta,
        results=results,
        claim_license=lic,
        available=available,
        warnings=warnings,
        ai_status=audit.get("ai_status"),
        target_extra=target_extra or None,
    )


def render_report(
    audit: dict[str, Any],
    baseline_comparison: dict[str, Any] | None = None,
) -> str:
    if not audit.get("available"):
        return (
            "# Punctuation cadence audit\n\n"
            f"_Unavailable: {audit.get('reason', 'unknown')}._\n"
        )
    c = audit["compression"]
    densities = audit["densities_per_1k"]
    interruption = audit["interruption_grammar"]
    finals = audit["sentence_final_distribution"]
    lines: list[str] = [
        "# Punctuation cadence audit",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Words:** {audit['n_words']:,}",
        "",
        f"**Band:** {c['band']}  "
        f"(compression fraction {c['compression_fraction']:.2f}; "
        f"{c['n_flagged']}/{c['n_signals']} signals fired)",
        "",
        f"**Comma + period share of internal marks:** "
        f"{audit['comma_period_share']:.1%}",
        "",
        "## Per-mark densities (per 1,000 words)",
        "",
        "| mark | count | density |",
        "|---|---:|---:|",
    ]
    raw = audit.get("raw_counts", {})
    label_for = {
        "comma": "comma",
        "semicolon": "semicolon",
        "colon": "colon",
        "em_dash": "em dash",
        "en_dash": "en dash",
        "parenthesis": "parenthesis (open)",
        "bracket": "bracket (open)",
        "ellipsis": "ellipsis",
        "double_quote_pair": "double quote",
        "single_quote_pair": "single quote",
        "exclamation": "exclamation",
        "question": "question",
    }
    for key in _MARKS.keys():
        density_key = f"{key}_per_1k"
        lines.append(
            f"| {label_for.get(key, key)} | {raw.get(key, 0)} | "
            f"{densities.get(density_key, 0.0):.2f} |"
        )
    lines.append("")

    lines.append("## Sentence-final distribution")
    lines.append("")
    lines.append(
        f"- Period: {finals['period']:.1%}  "
        f"Question: {finals['question']:.1%}  "
        f"Exclamation: {finals['exclamation']:.1%}"
    )
    lines.append("")

    lines.append("## Interruption grammar")
    lines.append("")
    lines.append(
        f"- **Parenthetical asides:** "
        f"{interruption['parenthetical_per_1k']:.2f} / 1k"
    )
    lines.append(
        f"- **Em-dash interruptions:** "
        f"{interruption['em_dash_aside_per_1k']:.2f} / 1k"
    )
    lines.append(
        f"- **Comma appositives:** "
        f"{interruption['comma_appositive_per_1k']:.2f} / 1k"
    )
    lines.append(
        f"- **Total interruption rate:** "
        f"{interruption['total_interruption_per_1k']:.2f} / 1k"
    )
    lines.append("")

    if c["flagged_signals"]:
        lines.append("## Flagged signals")
        lines.append("")
        for sig in c["flagged_signals"]:
            lines.append(f"- `{sig}`")
        lines.append("")

    bigrams = audit.get("punctuation_bigrams", {})
    if bigrams:
        lines.append("## Top punctuation bigrams")
        lines.append("")
        lines.append("| bigram | count |")
        lines.append("|---|---:|")
        for bg, cnt in list(bigrams.items())[:10]:
            lines.append(f"| `{bg}` | {cnt} |")
        lines.append("")

    if baseline_comparison and baseline_comparison.get("available"):
        lines.append("## Baseline comparison")
        lines.append("")
        lines.append(
            f"- **Punctuation distribution Manhattan distance:** "
            f"{baseline_comparison['punctuation_distribution_distance']:.4f}"
        )
        lines.append("")
        lines.append("### Per-mark z-scores")
        lines.append("")
        lines.append("| signal | z-score |")
        lines.append("|---|---:|")
        for k, z in baseline_comparison["z_density"].items():
            z_str = f"{z:+.2f}" if isinstance(z, (int, float)) else "n/a"
            lines.append(f"| {k} | {z_str} |")
        lines.append("")

    lines.append(_claim_license_block(audit))
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- CLI -------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="punctuation_cadence_audit.py",
        description=(
            "Punctuation rhythm + interruption-grammar audit. "
            "Catches the regularization patterns AI editing and "
            "professional copyediting often produce before "
            "lexical-diversity signals fire."
        ),
    )
    p.add_argument("input", help="Path to .txt or .md target file.")
    p.add_argument("--baseline-dir", help="Optional baseline directory.")
    p.add_argument("--json", action="store_true", help="Emit JSON.")
    p.add_argument("--out", help="Write output to this path.")
    p.add_argument("--allow-non-prose", action="store_true")
    p.add_argument("--strip-rules", help="Comma-separated strip rules.")
    p.add_argument("--strip-aggressive", action="store_true")
    p.add_argument(
        "--strip-masking",
        help="Optional masking profile (prose_body_only, etc.).",
    )
    p.add_argument(
        "--include-baseline-filenames", action="store_true",
        help=(
            "Include raw baseline filenames in `per_file_summaries` "
            "(privacy default: anonymized as `baseline_001`)."
        ),
    )
    # B.3 (v1.53.0+): authorship-state routing for the ClaimLicense
    # block. The operator's manifest entry for the target carries
    # an `ai_status` value (pre_ai_human, ai_generated_from_outline,
    # etc.). Surface it to the audit so the rendered license block
    # carries the matching state-specific caveats. Per SPEC §9.2,
    # this is the operational consequence of the B.2 vocabulary —
    # not threshold-shipping, just per-state licensure language.
    p.add_argument(
        "--ai-status",
        default=None,
        help=(
            "Manifest ai_status value for the target text (e.g., "
            "pre_ai_human, ai_generated, ai_generated_from_outline, "
            "ai_assisted, ai_edited, mixed, unknown). When supplied, "
            "the ClaimLicense block gains state-specific caveats per "
            "SPEC_authorship_states.md §9.2."
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
        raw, args.strip_rules,
        allow_non_prose=args.allow_non_prose,
        strip_aggressive=args.strip_aggressive,
        strip_masking=args.strip_masking,
    )
    audit = audit_punctuation_cadence(cleaned)
    audit["preprocessing"] = prep_meta
    # B.3: surface --ai-status into the audit dict so
    # _claim_license_block can route per state.
    if args.ai_status:
        audit["ai_status"] = args.ai_status

    baseline_comparison: dict[str, Any] | None = None
    if args.baseline_dir:
        try:
            block = audit_baseline_punctuation(
                args.baseline_dir,
                allow_non_prose=args.allow_non_prose,
                strip_rules=args.strip_rules,
                strip_aggressive=args.strip_aggressive,
                strip_masking=args.strip_masking,
                target_path=target_path,
                target_fingerprint=_content_fingerprint(cleaned),
                include_filenames=args.include_baseline_filenames,
            )
        except FileNotFoundError as exc:
            sys.stderr.write(f"  baseline error: {exc}\n")
            return 2
        audit["baseline_block"] = block
        if block.get("n_files", 0) == 0:
            sys.stderr.write(
                f"  baseline at {args.baseline_dir} produced 0 "
                "usable files; baseline comparison skipped.\n"
            )
        baseline_comparison = compare_to_baseline(audit, block)
        audit["baseline_comparison"] = baseline_comparison

    if args.json:
        payload = build_audit_payload(
            audit,
            target_path=target_path,
            baseline_block=audit.get("baseline_block"),
            baseline_comparison=baseline_comparison,
        )
        out = json.dumps(payload, indent=2, default=str)
    else:
        out = render_report(audit, baseline_comparison)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
