#!/usr/bin/env python3
"""discourse_move_signature.py — typed discourse markers + move sequences.

Surfaces Tier-1 build, paired-release schedule Release 3.
Consumed by confounder_audit.py as load-bearing evidence for the
differential diagnosis ("legal/policy memo style" vs. "AI smoothing"
vs. "professional copyediting"); without typed-discourse evidence
the confounder matrix can't separate institutional prose from
AI-smoothed prose.

The shipped Layer A suite measures *connective density* as a single
ratio. That captures "how much scaffolding is present" but not
"what kind of scaffolding." A writer who concedes-then-reverses-
then-narrows uses different markers than a policy memo that
elaborates-and-recommends-and-cautions, even if both are equally
"scaffolded." This module types the markers and surfaces both
per-category density and **move-sequence bigrams** — which
adjacent move-pairs the writer falls into.

Categories (typology):

  - contrast: however, but, yet, still, nevertheless, on the other hand
  - concession: admittedly, granted, of course, although, while, despite
  - consequence: therefore, so, thus, hence, consequently, as a result
  - elaboration: in other words, that is, namely, specifically
  - exemplification: for example, for instance, such as, including
  - sequencing: first, second, finally, next, then, subsequently
  - reframing: the better question, more precisely, what matters is
  - epistemic_stance: maybe, likely, apparently, perhaps, possibly
  - boosting: clearly, obviously, definitely, certainly, indeed
  - hedging: somewhat, sort of, more or less, arguably, to some extent
  - self_correction: or rather, not exactly, more accurately
  - metadiscourse: as discussed above, in this section, returning to

Output:

  - Per-category density (per 1000 words).
  - Move-sequence bigrams: count of consecutive (move_i, move_{i+1})
    transitions across sentences.
  - Move-sequence entropy in bits — low entropy means scripted
    argumentative cadence (concession→reversal→claim repeated, or
    elaboration→exemplification→consequence repeated).
  - Compression-fraction band call — heuristic, calibration pending.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import (  # type: ignore
    ClaimLicense, with_state_caveats,
)
from preprocessing import strip_non_prose  # type: ignore

TASK_SURFACE = "smoothing_diagnosis"
TOOL_NAME = "discourse_move_signature"
SCRIPT_VERSION = "1.0"


# --- Marker typology -------------------------------------------
#
# Each category maps to a tuple of regex patterns. Patterns use
# (?im) flags (case-insensitive, multi-line) and assume word
# boundaries. Order matters within a category only for which
# pattern wins on overlapping matches; the per-category density is
# the deduplicated count regardless.

_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "contrast": (
        re.compile(r"\b(?:however|but|yet|still|nevertheless|nonetheless)\b", re.I),
        re.compile(r"\b(?:on the other hand|in contrast|conversely|by contrast|whereas)\b", re.I),
    ),
    "concession": (
        re.compile(r"\b(?:admittedly|granted|of course|to be sure|certainly|true)\b,", re.I),
        re.compile(r"\b(?:although|though|while|despite|in spite of|even though|even if)\b", re.I),
    ),
    "consequence": (
        re.compile(r"\b(?:therefore|thus|hence|consequently|accordingly)\b", re.I),
        re.compile(r"\b(?:as a result|so that|for that reason|that is why|which is why)\b", re.I),
    ),
    "elaboration": (
        re.compile(r"\b(?:in other words|that is|namely|specifically|in particular)\b", re.I),
        re.compile(r"\b(?:more (?:precisely|specifically|formally)|put (?:another|differently))\b", re.I),
    ),
    "exemplification": (
        re.compile(r"\b(?:for example|for instance|e\.?g\.?|such as|including|like)\b", re.I),
        re.compile(r"\b(?:consider|take|imagine)\s+(?:the|a|an)\s+\w+", re.I),
    ),
    "sequencing": (
        re.compile(r"\b(?:first(?:ly)?|second(?:ly)?|third(?:ly)?|finally|lastly)\b,?", re.I),
        re.compile(r"\b(?:next|then|subsequently|afterwards|meanwhile|in turn)\b", re.I),
    ),
    "reframing": (
        re.compile(r"\b(?:the (?:better|deeper|real|right) question is)\b", re.I),
        re.compile(r"\b(?:what matters is|the point is|more (?:importantly|to the point))\b", re.I),
    ),
    "epistemic_stance": (
        re.compile(r"\b(?:maybe|perhaps|possibly|likely|apparently|presumably|supposedly)\b", re.I),
        re.compile(r"\b(?:may|might|could)\s+(?:be|have|seem|suggest|indicate)\b", re.I),
        re.compile(r"\b(?:I\s+(?:think|believe|suspect|guess))\b", re.I),
    ),
    "boosting": (
        re.compile(r"\b(?:clearly|obviously|definitely|certainly|undeniably|indeed)\b", re.I),
        re.compile(r"\b(?:of course|without (?:doubt|question)|as everyone knows)\b", re.I),
    ),
    "hedging": (
        re.compile(r"\b(?:somewhat|sort of|kind of|more or less|to some extent|arguably)\b", re.I),
        re.compile(r"\b(?:in (?:some|certain) (?:sense|ways|cases)|to a (?:degree|certain extent))\b", re.I),
    ),
    "self_correction": (
        re.compile(r"\b(?:or rather|or more (?:accurately|precisely)|to put it (?:differently|another way))\b", re.I),
        re.compile(r"\b(?:not (?:exactly|quite)|better:?\s+|let me rephrase)\b", re.I),
    ),
    "metadiscourse": (
        re.compile(r"\b(?:as (?:discussed|noted|argued|shown) (?:above|earlier|previously|before))\b", re.I),
        re.compile(r"\b(?:in this (?:section|chapter|essay|piece)|returning to|coming back to)\b", re.I),
        re.compile(r"\b(?:as I (?:mentioned|said) (?:above|earlier|before))\b", re.I),
    ),
}

CATEGORIES = tuple(_PATTERNS.keys())

_SENTENCE_TERMINATORS = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(])")
_WORD_RE = re.compile(r"\b\w+\b")


def _split_sentences(text: str) -> list[str]:
    return [
        s.strip()
        for s in _SENTENCE_TERMINATORS.split(text)
        if s.strip()
    ]


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _entropy(counts: dict[Any, int]) -> float:
    """Shannon entropy in bits over a count dict."""
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


def classify_sentence(sentence: str) -> str | None:
    """Return the move category whose markers appear first in the
    sentence, or ``None`` if no marker fires.

    First-match wins so a sentence opening with "However, the
    point is..." classifies as `contrast` (its leading marker)
    rather than `reframing`. This is rough; a sentence with a
    leading "However" and a body "the better question is" is
    plausibly more contrast-led than reframing-led, and the move
    sequence bigram captures the structural pattern either way.
    """
    earliest_pos: int | None = None
    earliest_category: str | None = None
    for category, patterns in _PATTERNS.items():
        for pattern in patterns:
            m = pattern.search(sentence)
            if m is None:
                continue
            pos = m.start()
            if earliest_pos is None or pos < earliest_pos:
                earliest_pos = pos
                earliest_category = category
    return earliest_category


def audit_discourse_moves(text: str) -> dict[str, Any]:
    """Compute per-category densities + move-sequence bigrams +
    move-sequence entropy. Pure function; no I/O.
    """
    n_words = _word_count(text)
    if n_words == 0:
        return {
            "task_surface": TASK_SURFACE,
            "tool": TOOL_NAME,
            "version": SCRIPT_VERSION,
            "available": False,
            "reason": "empty text",
        }

    sentences = _split_sentences(text)

    # Per-category counts (over the whole text — pattern matches
    # across all words, not deduplicated within a sentence).
    category_counts: Counter[str] = Counter()
    for category, patterns in _PATTERNS.items():
        n_matches = 0
        for pattern in patterns:
            n_matches += len(pattern.findall(text))
        if n_matches:
            category_counts[category] = n_matches

    densities = {
        cat: 1000.0 * category_counts.get(cat, 0) / n_words
        for cat in CATEGORIES
    }
    total_marker_density = sum(densities.values())

    # Move sequence: classify each sentence's leading marker (if
    # any). Sentences without a marker label as "_unmarked".
    move_sequence: list[str] = []
    for s in sentences:
        c = classify_sentence(s)
        move_sequence.append(c if c else "_unmarked")

    # Move sequence bigrams: count consecutive (move_i, move_{i+1})
    # across the doc. Bigrams over labels, including _unmarked, so
    # "concession → unmarked → contrast" pairs both transitions.
    bigrams: Counter[tuple[str, str]] = Counter()
    for a, b in zip(move_sequence, move_sequence[1:]):
        bigrams[(a, b)] += 1

    # Move-sequence entropy: how varied is the move pattern? Low
    # entropy means scripted cadence (e.g., concession→reversal→
    # claim repeated). The unmarked-only stretch contributes a
    # single label and therefore low entropy, so we measure two
    # entropies: full (including _unmarked) and marked-only.
    move_counts: Counter[str] = Counter(move_sequence)
    full_entropy = _entropy(dict(move_counts))
    marked_only = {k: v for k, v in move_counts.items() if k != "_unmarked"}
    marked_entropy = _entropy(marked_only)

    # Composite signal: when total marker density is high AND
    # marked-only entropy is low, the prose is scaffolded with a
    # narrow set of move types — the "scripted argumentative
    # cadence" pattern. When density is low or entropy is high,
    # the prose is unscaffolded or freely-varying.
    flagged_signals: list[str] = []
    if total_marker_density >= 30.0:
        flagged_signals.append("high_total_marker_density")
    if marked_entropy <= 1.50 and sum(marked_only.values()) >= 3:
        flagged_signals.append("low_marked_move_entropy")
    if (
        densities.get("concession", 0) >= 5.0
        and densities.get("contrast", 0) >= 5.0
        and densities.get("consequence", 0) >= 3.0
    ):
        flagged_signals.append("dense_concession_contrast_consequence_triad")
    if densities.get("metadiscourse", 0) >= 3.0:
        flagged_signals.append("high_metadiscourse_density")
    if (
        densities.get("hedging", 0) > 4.0
        and densities.get("boosting", 0) > 4.0
    ):
        flagged_signals.append("high_hedging_and_boosting_oscillation")

    n_signals = 5
    compression_fraction = len(flagged_signals) / n_signals
    if compression_fraction < 0.20:
        band = "Lightly scaffolded"
    elif compression_fraction < 0.50:
        band = "Moderately scaffolded"
    else:
        band = "Heavily scaffolded"

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "available": True,
        "n_words": n_words,
        "n_sentences": len(sentences),
        "category_counts": dict(category_counts),
        "category_densities_per_1k": densities,
        "total_marker_density_per_1k": total_marker_density,
        "move_sequence": move_sequence,
        "move_sequence_bigrams": {
            f"{a}->{b}": c for (a, b), c in bigrams.most_common()
        },
        "move_sequence_entropy_bits": full_entropy,
        "marked_only_entropy_bits": marked_entropy,
        "compression": {
            "band": band,
            "compression_fraction": round(compression_fraction, 3),
            "flagged_signals": flagged_signals,
            "n_flagged": len(flagged_signals),
            "n_signals": n_signals,
        },
    }


# --- Baseline aggregate ----------------------------------------


def audit_baseline_discourse(
    baseline_dir: str,
    *,
    allow_non_prose: bool = False,
    strip_rules: str | Iterable[str] | None = None,
    strip_aggressive: bool = False,
    strip_masking: str | Iterable[str] | None = None,
    target_path: Path | None = None,
    include_filenames: bool = False,
) -> dict[str, Any]:
    """Run the discourse audit across every text file in
    ``baseline_dir``; return aggregate per-category mean+sd plus
    pooled bigram counts.

    1.34.2 hardening (mirrors paragraph_audit / general_imposters
    conventions):
      * ``baseline_dir`` must exist; raises ``FileNotFoundError``.
      * Unreadable / unaudited files surface in ``skipped_files``.
      * When ``target_path`` is supplied, baseline entries whose
        resolved path matches are excluded with a stderr notice.
      * Per-file summaries use anonymized ``baseline_001`` IDs by
        default (filenames often carry private metadata); opt in
        via ``include_filenames=True``.
    """
    base = Path(baseline_dir)
    if not base.is_dir():
        raise FileNotFoundError(
            f"Baseline directory not found or not a directory: "
            f"{baseline_dir}"
        )
    paths = (
        sorted(base.glob("*.txt")) + sorted(base.glob("*.md"))
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
    pooled_density_by_cat: dict[str, list[float]] = {c: [] for c in CATEGORIES}
    pooled_bigrams: Counter[str] = Counter()
    next_anon_id = 1
    for p in paths:
        if target_resolved is not None:
            try:
                if p.resolve() == target_resolved:
                    sys.stderr.write(
                        f"  excluding {p.name} from discourse "
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
        a = audit_discourse_moves(cleaned)
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
            "category_densities_per_1k": a["category_densities_per_1k"],
            "marked_only_entropy_bits": a["marked_only_entropy_bits"],
            "total_marker_density_per_1k": a["total_marker_density_per_1k"],
        })
        next_anon_id += 1
        for cat, density in a["category_densities_per_1k"].items():
            pooled_density_by_cat[cat].append(density)
        for bigram_str, count in a["move_sequence_bigrams"].items():
            pooled_bigrams[bigram_str] += count

    def _mean_sd(values: list[float]) -> dict[str, float]:
        if not values:
            return {"mean": 0.0, "sd": 0.0, "n": 0}
        m = sum(values) / len(values)
        if len(values) > 1:
            var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
            sd = math.sqrt(var)
        else:
            sd = 0.0
        return {"mean": m, "sd": sd, "n": len(values)}

    aggregate = {
        cat: _mean_sd(vals)
        for cat, vals in pooled_density_by_cat.items()
    }

    return {
        "n_files": len(per_file),
        "n_skipped": len(skipped_files),
        "skipped_files": skipped_files,
        "per_file_summaries": per_file,
        "aggregate_density_by_category": aggregate,
        "pooled_bigrams": dict(pooled_bigrams),
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
    z_scores: dict[str, float | None] = {}
    agg = baseline_block["aggregate_density_by_category"]
    target_dens = target["category_densities_per_1k"]
    for cat in CATEGORIES:
        bucket = agg.get(cat, {})
        sd = bucket.get("sd", 0.0)
        if sd <= 0 or bucket.get("n", 0) < 2:
            z_scores[cat] = None
            continue
        z = (target_dens.get(cat, 0.0) - bucket["mean"]) / sd
        z_scores[cat] = z
    return {
        "available": True,
        "category_density_z_scores": z_scores,
    }


# --- Markdown rendering ----------------------------------------


def _claim_license_block(audit: dict[str, Any]) -> str:
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Discourse-marker typology and move-sequence pattern of "
            "the input: per-category marker densities (contrast, "
            "concession, consequence, elaboration, exemplification, "
            "sequencing, reframing, epistemic stance, boosting, "
            "hedging, self-correction, metadiscourse) and move-"
            "sequence bigram counts. Surfaces *what kind* of "
            "scaffolding the writer uses, not just *how much*."
        ),
        does_not_license=(
            "An AI-provenance verdict. Heavy scaffolding is "
            "characteristic of legal/policy memos, academic prose, "
            "AI-edited drafts, and well-scaffolded human essayists "
            "alike. The differential diagnosis of cause is the "
            "confounder audit's job (which consumes this output as "
            "evidence). Nor does the audit license claims about "
            "which moves are 'good' or 'bad' — the typology is "
            "descriptive."
        ),
        comparison_set={
            "n_words": audit.get("n_words"),
            "n_sentences": audit.get("n_sentences"),
            "band": audit.get("compression", {}).get("band"),
        },
        additional_caveats=[
            "Marker patterns are case-insensitive English regexes. "
            "Idiomatic markers (e.g. \"the better question is\") "
            "are pattern-matched literally; metaphorical or unusual "
            "wordings will be missed.",
            "First-match wins for sentence classification: a "
            "sentence with multiple markers gets typed by the "
            "earliest one. Move-sequence bigrams capture the "
            "between-sentence pattern regardless.",
            "Heuristic thresholds (band call) are calibration-"
            "pending; treat the band as a cue, not a verdict.",
        ],
    )
    # B.3: state-routed caveats when --ai-status was passed.
    lic = with_state_caveats(
        lic, target_ai_status=audit.get("ai_status"),
    )
    return lic.render_block().rstrip()


def render_report(
    audit: dict[str, Any],
    baseline_comparison: dict[str, Any] | None = None,
) -> str:
    if not audit.get("available"):
        return (
            "# Discourse move signature\n\n"
            f"_Unavailable: {audit.get('reason', 'unknown')}._\n"
        )
    c = audit["compression"]
    lines: list[str] = [
        "# Discourse move signature",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Words:** {audit['n_words']:,}  "
        f"**Sentences:** {audit['n_sentences']}",
        "",
        f"**Band:** {c['band']}  "
        f"(compression fraction {c['compression_fraction']:.2f}; "
        f"{c['n_flagged']}/{c['n_signals']} signals fired)",
        "",
        f"**Total marker density:** "
        f"{audit['total_marker_density_per_1k']:.1f} per 1,000 words  "
        f"**Marked-only move entropy:** "
        f"{audit['marked_only_entropy_bits']:.2f} bits",
        "",
        "## Per-category densities",
        "",
        "| category | count | density / 1k words |",
        "|---|---:|---:|",
    ]
    densities = audit["category_densities_per_1k"]
    counts = audit.get("category_counts", {})
    for cat in CATEGORIES:
        lines.append(
            f"| {cat} | {counts.get(cat, 0)} | "
            f"{densities.get(cat, 0.0):.2f} |"
        )
    lines.append("")

    if c["flagged_signals"]:
        lines.append("## Flagged signals")
        lines.append("")
        for sig in c["flagged_signals"]:
            lines.append(f"- `{sig}`")
        lines.append("")

    bigrams = audit.get("move_sequence_bigrams", {})
    if bigrams:
        # Top 10 most-frequent bigrams.
        top = list(bigrams.items())[:10]
        lines.append("## Top move-sequence bigrams")
        lines.append("")
        lines.append("| bigram | count |")
        lines.append("|---|---:|")
        for bg, c_count in top:
            lines.append(f"| `{bg}` | {c_count} |")
        lines.append("")

    if baseline_comparison and baseline_comparison.get("available"):
        lines.append("## Baseline comparison")
        lines.append("")
        lines.append("| category | z-score |")
        lines.append("|---|---:|")
        zs = baseline_comparison["category_density_z_scores"]
        for cat in CATEGORIES:
            z = zs.get(cat)
            z_str = f"{z:+.2f}" if isinstance(z, (int, float)) else "n/a"
            lines.append(f"| {cat} | {z_str} |")
        lines.append("")

    lines.append(_claim_license_block(audit))
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- CLI -------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="discourse_move_signature.py",
        description=(
            "Typed discourse marker + move-sequence audit. "
            "Provides differentiating evidence for the confounder "
            "audit's differential diagnosis (legal/policy memo "
            "style vs. AI smoothing vs. professional copyediting)."
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
    # B.3 (v1.47.0+): authorship-state routing for the ClaimLicense.
    p.add_argument(
        "--ai-status",
        default=None,
        help=(
            "Manifest ai_status value for the target text. When "
            "supplied, the ClaimLicense block gains state-specific "
            "caveats per SPEC_authorship_states.md §9.2."
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
    audit = audit_discourse_moves(cleaned)
    audit["preprocessing"] = prep_meta
    # B.3: propagate --ai-status into the audit dict for the
    # claim-license block.
    if args.ai_status:
        audit["ai_status"] = args.ai_status

    baseline_comparison: dict[str, Any] | None = None
    if args.baseline_dir:
        try:
            block = audit_baseline_discourse(
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
        audit["baseline_block"] = block
        if block.get("n_files", 0) == 0:
            sys.stderr.write(
                f"  baseline at {args.baseline_dir} produced 0 "
                "usable files; baseline comparison skipped.\n"
            )
        baseline_comparison = compare_to_baseline(audit, block)
        audit["baseline_comparison"] = baseline_comparison

    out = (
        json.dumps(audit, indent=2, default=str)
        if args.json else render_report(audit, baseline_comparison)
    )
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
