#!/usr/bin/env python3
"""stance_modality_audit.py — typed stance / modality / epistemic posture.

Surfaces Tier-2 promotion, paired-release schedule Release 5.
Stance markers — modal verbs, hedges, boosters, evidentials,
epistemic indicators — are partly captured in
``voice_profile.py``'s pronoun-modal-negation cluster, but only
at the frequency level. The deeper question — *which kind* of
modal posture the writer takes, and whether AI smoothing /
copyediting / institutional editing has shifted that posture — is
the missing top-level surface this module fills.

The audit types markers along seven axes:

  - **deontic_modality**: must / shall / should / required / ought
    (obligation language).
  - **epistemic_modality**: may / might / could / would / probably
    / likely (epistemic possibility).
  - **hedge**: somewhat / sort of / kind of / arguably / to some
    extent / in some sense.
  - **booster**: clearly / obviously / definitely / certainly /
    indeed / undeniably.
  - **evidential**: seems / suggests / shows / indicates / reveals
    / demonstrates (source-of-knowledge markers).
  - **first_person_stance**: I think / I believe / we argue / we
    suggest / it seems to me.
  - **refusal**: this does not show / cannot conclude / is not
    enough to establish.

Compression-fraction band over six rhythm signals catches
characteristic shifts: hedge-then-booster oscillation (LLM
characteristic), booster dominance (over-confidence), refusal
absence (over-claim), first-person collapse (depersonalization),
deontic dominance (institutional voice), evidential dominance
(academic voice).

Output is descriptive — modality types are not "good" or "bad"
in themselves. The audit's value is comparison: per-category
density z-scores against a baseline reveal whether the writer's
typical *epistemic posture* has shifted, which is often the
earliest place AI editing deforms voice.

Hardened baseline ingestion (1.34.x conventions): validates dir,
surfaces skipped files, excludes target overlap, anonymizes
filenames by default.
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
from output_schema import build_baseline_metadata, build_output  # type: ignore
from preprocessing import strip_non_prose  # type: ignore

TASK_SURFACE = "voice_coherence"
TOOL_NAME = "stance_modality_audit"
SCRIPT_VERSION = "1.0"


# --- Marker patterns -------------------------------------------

_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "deontic_modality": (
        re.compile(r"\b(?:must|shall|should|ought\s+to|need\s+to|"
                   r"have\s+to|required\s+to|must\s+not|shall\s+not)\b",
                   re.IGNORECASE),
        re.compile(r"\b(?:obliged|obligated|duty|mandatory|prohibited|"
                   r"forbidden|requisite)\b",
                   re.IGNORECASE),
    ),
    "epistemic_modality": (
        re.compile(r"\b(?:may|might|could|would|probably|likely|"
                   r"presumably|possibly|conceivably|plausibly)\b",
                   re.IGNORECASE),
    ),
    "hedge": (
        re.compile(r"\b(?:somewhat|sort\s+of|kind\s+of|more\s+or\s+less|"
                   r"to\s+some\s+extent|to\s+a\s+(?:degree|certain\s+extent)|"
                   r"in\s+some\s+(?:sense|ways|cases)|arguably|"
                   r"in\s+a\s+sense)\b",
                   re.IGNORECASE),
        re.compile(r"\b(?:roughly|approximately|broadly|"
                   r"generally\s+speaking|on\s+the\s+whole)\b",
                   re.IGNORECASE),
    ),
    "booster": (
        re.compile(r"\b(?:clearly|obviously|definitely|certainly|"
                   r"undeniably|indeed|of\s+course|without\s+doubt|"
                   r"without\s+question|as\s+everyone\s+knows|"
                   r"crucially|critically)\b",
                   re.IGNORECASE),
    ),
    "evidential": (
        re.compile(r"\b(?:seems|suggests|indicates|reveals|"
                   r"demonstrates|shows|implies|points\s+to|"
                   r"appears\s+to|appears|appeared)\b",
                   re.IGNORECASE),
        re.compile(r"\b(?:research|evidence|data|analysis|study)\s+"
                   r"(?:shows|suggests|indicates|reveals|"
                   r"demonstrates|finds)\b",
                   re.IGNORECASE),
    ),
    "first_person_stance": (
        re.compile(r"\bI\s+(?:think|believe|suspect|guess|"
                   r"hold|maintain|argue|contend|claim|propose|"
                   r"would\s+say|wish\s+to\s+suggest)\b",
                   re.IGNORECASE),
        re.compile(r"\bwe\s+(?:argue|propose|suggest|claim|hold|"
                   r"contend|maintain|believe|think|conclude)\b",
                   re.IGNORECASE),
        re.compile(r"\bin\s+my\s+(?:view|opinion|judgment|"
                   r"experience)\b",
                   re.IGNORECASE),
        re.compile(r"\bit\s+seems\s+to\s+me\b",
                   re.IGNORECASE),
    ),
    "refusal": (
        re.compile(r"\b(?:does\s+not|do\s+not|did\s+not|cannot|"
                   r"can't|will\s+not|won't)\s+(?:show|prove|"
                   r"establish|demonstrate|imply|entail|mean|"
                   r"suggest|indicate|warrant|justify|tell\s+us)\b",
                   re.IGNORECASE),
        re.compile(r"\b(?:is\s+not\s+enough|insufficient|"
                   r"too\s+little|too\s+early|premature)\s+to\s+"
                   r"(?:show|prove|establish|conclude|infer|"
                   r"determine|decide)\b",
                   re.IGNORECASE),
        re.compile(r"\bcannot\s+(?:conclude|infer|determine|"
                   r"establish|prove|demonstrate)\b",
                   re.IGNORECASE),
    ),
}

CATEGORIES = tuple(_PATTERNS.keys())

_WORD_RE = re.compile(r"\b\w+\b")


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _per_thousand(count: int, n_words: int) -> float:
    if n_words <= 0:
        return 0.0
    return 1000.0 * count / n_words


def _entropy(counts: dict[str, int]) -> float:
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


def audit_stance_modality(text: str) -> dict[str, Any]:
    """Compute per-category stance counts + densities + a band call.
    Pure function; no I/O.
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

    # 1.35.1 — pre-fix the count summed `len(pattern.findall(text))`
    # across patterns within a category, which double-counted
    # phrases like "evidence shows" (matched by both the bare-verb
    # pattern `shows` and the phrase pattern
    # `(evidence|research|...)\s+(shows|...)`). Reviewer reproduced
    # inflated evidential density and downstream stance entropy. Fix:
    # collect (start, end) match spans across all patterns in the
    # category, then de-duplicate by span containment — a longer
    # match that covers a shorter one wins; non-overlapping matches
    # all count.
    category_counts: Counter[str] = Counter()
    for category, patterns in _PATTERNS.items():
        spans: list[tuple[int, int]] = []
        for pattern in patterns:
            for m in pattern.finditer(text):
                spans.append((m.start(), m.end()))
        # Deduplicate spans by containment: drop any span that lies
        # entirely inside another span in the list. Sort by length
        # descending so the longest matches survive.
        spans.sort(key=lambda s: -(s[1] - s[0]))
        kept: list[tuple[int, int]] = []
        for s in spans:
            covered = False
            for k in kept:
                if k[0] <= s[0] and s[1] <= k[1]:
                    covered = True
                    break
            if not covered:
                kept.append(s)
        category_counts[category] = len(kept)

    densities = {
        cat: _per_thousand(category_counts.get(cat, 0), n_words)
        for cat in CATEGORIES
    }
    total_marker_density = sum(densities.values())

    # Stance entropy: how varied is the writer's modal posture?
    # Low entropy means the writer leans on a narrow set of stance
    # types — characteristic of institutional or AI-shaped prose.
    stance_entropy_bits = _entropy(dict(category_counts))

    # Hedge / booster ratio: the writer's epistemic equilibrium.
    # Around 1.0 = balanced; near 0 = all booster (over-confident);
    # very high = all hedge (over-cautious).
    h = densities["hedge"]
    b = densities["booster"]
    if h + b > 0:
        hedge_booster_ratio = h / (h + b)
    else:
        hedge_booster_ratio = 0.5

    flagged_signals: list[str] = []

    # Hedge-and-booster oscillation: LLM-characteristic. Both
    # categories fire at meaningful density, and they're roughly
    # balanced (suggesting the writer hedges then immediately
    # boosts — the "AI both-sides" rhythm).
    if (
        densities["hedge"] >= 3.0
        and densities["booster"] >= 3.0
        and 0.3 <= hedge_booster_ratio <= 0.7
    ):
        flagged_signals.append("hedge_booster_oscillation")

    # Booster dominance: > 5/1k boosters AND less than 1/1k hedges.
    # Over-confidence; AI editing often pushes hedged prose toward
    # this shape.
    if (
        densities["booster"] >= 5.0
        and densities["hedge"] < 1.0
    ):
        flagged_signals.append("booster_dominance")

    # Refusal absence: prose with no refusal markers AND moderate-
    # to-heavy stance density elsewhere is over-claiming.
    if (
        densities["refusal"] < 0.3
        and total_marker_density >= 12.0
        and n_words >= 500
    ):
        flagged_signals.append("low_refusal_marker_density")

    # First-person stance collapse: < 0.5/1k for prose-of-record
    # genres where the writer normally uses I/we stance markers.
    # Catches depersonalization (often AI / institutional rewrite).
    if (
        densities["first_person_stance"] < 0.5
        and n_words >= 500
    ):
        flagged_signals.append("low_first_person_stance")

    # Deontic dominance: > 5/1k must/shall — institutional voice.
    if densities["deontic_modality"] >= 5.0:
        flagged_signals.append("high_deontic_modality")

    # Stance entropy floor: < 1.0 bits across the seven categories
    # means the writer's stance vocabulary collapsed to a narrow
    # type (e.g., all-evidential or all-hedge).
    n_active_categories = sum(
        1 for d in densities.values() if d > 0.5
    )
    if (
        n_active_categories >= 2
        and stance_entropy_bits < 1.0
    ):
        flagged_signals.append("low_stance_entropy")

    n_signals = 6
    compression_fraction = len(flagged_signals) / n_signals
    if compression_fraction < 0.20:
        band = "Lightly stance-shifted"
    elif compression_fraction < 0.50:
        band = "Moderately stance-shifted"
    else:
        band = "Heavily stance-shifted"

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "available": True,
        "n_words": n_words,
        "category_counts": dict(category_counts),
        "category_densities_per_1k": densities,
        "total_marker_density_per_1k": round(total_marker_density, 3),
        "stance_entropy_bits": round(stance_entropy_bits, 3),
        "hedge_booster_ratio": round(hedge_booster_ratio, 3),
        "compression": {
            "band": band,
            "compression_fraction": round(compression_fraction, 3),
            "flagged_signals": flagged_signals,
            "n_flagged": len(flagged_signals),
            "n_signals": n_signals,
        },
    }


# --- Baseline + comparison + render + CLI ----------------------
#
# Mirrors the discourse_move_signature / agency conventions.


def audit_baseline_stance(
    baseline_dir: str,
    *,
    allow_non_prose: bool = False,
    strip_rules: str | Iterable[str] | None = None,
    strip_aggressive: bool = False,
    strip_masking: str | Iterable[str] | None = None,
    target_path: Path | None = None,
    include_filenames: bool = False,
) -> dict[str, Any]:
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
    pooled: dict[str, list[float]] = {c: [] for c in CATEGORIES}
    pooled_total: list[float] = []
    pooled_entropy: list[float] = []
    pooled_hb_ratio: list[float] = []
    next_anon_id = 1
    for p in paths:
        if target_resolved is not None:
            try:
                if p.resolve() == target_resolved:
                    sys.stderr.write(
                        f"  excluding {p.name} from stance baseline "
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
        a = audit_stance_modality(cleaned)
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
            "total_marker_density_per_1k": a["total_marker_density_per_1k"],
            "stance_entropy_bits": a["stance_entropy_bits"],
            "hedge_booster_ratio": a["hedge_booster_ratio"],
        })
        next_anon_id += 1
        for cat in CATEGORIES:
            pooled[cat].append(a["category_densities_per_1k"][cat])
        pooled_total.append(a["total_marker_density_per_1k"])
        pooled_entropy.append(a["stance_entropy_bits"])
        pooled_hb_ratio.append(a["hedge_booster_ratio"])

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
        "aggregate_density_by_category": {
            cat: _mean_sd(vals) for cat, vals in pooled.items()
        },
        "aggregate_total_density": _mean_sd(pooled_total),
        "aggregate_stance_entropy": _mean_sd(pooled_entropy),
        "aggregate_hedge_booster_ratio": _mean_sd(pooled_hb_ratio),
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

    z_per_category: dict[str, float | None] = {}
    agg = baseline_block["aggregate_density_by_category"]
    for cat in CATEGORIES:
        bucket = agg.get(cat, {})
        z_per_category[cat] = _z(
            target["category_densities_per_1k"].get(cat, 0.0), bucket,
        )
    return {
        "available": True,
        "z_per_category": z_per_category,
        "z_total_density": _z(
            target["total_marker_density_per_1k"],
            baseline_block["aggregate_total_density"],
        ),
        "z_stance_entropy": _z(
            target["stance_entropy_bits"],
            baseline_block["aggregate_stance_entropy"],
        ),
        "z_hedge_booster_ratio": _z(
            target["hedge_booster_ratio"],
            baseline_block["aggregate_hedge_booster_ratio"],
        ),
    }


# --- Markdown rendering ----------------------------------------


def _claim_license(audit: dict[str, Any]) -> ClaimLicense:
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Stance / modality / epistemic-posture profile of the "
            "input: per-category marker densities (deontic, "
            "epistemic, hedge, booster, evidential, first-person, "
            "refusal), stance entropy, hedge-booster equilibrium. "
            "Surfaces *what kind* of epistemic stance the writer "
            "takes, including patterns characteristic of "
            "institutional rewriting and AI editing."
        ),
        does_not_license=(
            "An AI-provenance verdict. Heavy boosting / low refusal "
            "is characteristic of marketing / persuasive prose; "
            "high deontic / refusal density is characteristic of "
            "legal / policy writing; AI editing has no monopoly on "
            "any of these patterns. The differential diagnosis of "
            "cause is the confounder audit's job."
        ),
        comparison_set={
            "n_words": audit.get("n_words"),
            "band": audit.get("compression", {}).get("band"),
        },
        additional_caveats=[
            "Marker patterns are case-insensitive English regexes. "
            "Idiomatic expressions of stance (irony, contextual "
            "modality) won't be caught. The audit measures explicit "
            "stance vocabulary, not stance.",
            "Heuristic thresholds (band call) are calibration-"
            "pending; treat the band as a cue, not a verdict.",
            "Modality is genre-bound: legal prose has high "
            "deontic density by convention; academic prose has "
            "high evidential density by convention. Read alongside "
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
    "category_counts", "category_densities_per_1k",
    "total_marker_density_per_1k", "stance_entropy_bits",
    "hedge_booster_ratio", "compression",
)


def build_audit_payload(
    audit: dict[str, Any],
    *,
    target_path: Path | str,
    baseline_block: dict[str, Any] | None,
    baseline_comparison: dict[str, Any] | None,
) -> dict[str, Any]:
    """Wrap the stance/modality audit dict in the schema_version 1.0
    envelope per ``internal/SPEC_output_schema_unification.md``.
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
            "# Stance / modality audit\n\n"
            f"_Unavailable: {audit.get('reason', 'unknown')}._\n"
        )
    c = audit["compression"]
    densities = audit["category_densities_per_1k"]
    counts = audit["category_counts"]
    lines: list[str] = [
        "# Stance / modality audit",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Words:** {audit['n_words']:,}",
        "",
        f"**Band:** {c['band']}  "
        f"(compression fraction {c['compression_fraction']:.2f}; "
        f"{c['n_flagged']}/{c['n_signals']} signals fired)",
        "",
        f"**Total marker density:** "
        f"{audit['total_marker_density_per_1k']:.2f} / 1k  "
        f"**Stance entropy:** "
        f"{audit['stance_entropy_bits']:.2f} bits  "
        f"**Hedge-booster ratio:** "
        f"{audit['hedge_booster_ratio']:.2f}",
        "",
        "## Per-category densities",
        "",
        "| category | count | density / 1k |",
        "|---|---:|---:|",
    ]
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

    if baseline_comparison and baseline_comparison.get("available"):
        lines.append("## Baseline comparison")
        lines.append("")
        lines.append("| signal | z-score |")
        lines.append("|---|---:|")
        for cat in CATEGORIES:
            z = baseline_comparison["z_per_category"].get(cat)
            z_str = f"{z:+.2f}" if isinstance(z, (int, float)) else "n/a"
            lines.append(f"| {cat} | {z_str} |")
        for label, key in (
            ("total density", "z_total_density"),
            ("stance entropy", "z_stance_entropy"),
            ("hedge-booster ratio", "z_hedge_booster_ratio"),
        ):
            z = baseline_comparison.get(key)
            z_str = f"{z:+.2f}" if isinstance(z, (int, float)) else "n/a"
            lines.append(f"| {label} | {z_str} |")
        lines.append("")

    lines.append(_claim_license_block(audit))
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- CLI -------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stance_modality_audit.py",
        description=(
            "Typed stance / modality / epistemic-posture audit. "
            "Surfaces the kind of modal posture the writer takes "
            "and patterns characteristic of institutional / AI "
            "rewriting (booster dominance, hedge-booster oscillation, "
            "first-person stance collapse, refusal absence)."
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
    # B.3 (v1.47.0+): authorship-state routing for the ClaimLicense
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
    audit = audit_stance_modality(cleaned)
    audit["preprocessing"] = prep_meta
    # B.3: surface --ai-status into the audit dict so
    # _claim_license_block can route per state.
    if args.ai_status:
        audit["ai_status"] = args.ai_status

    baseline_comparison: dict[str, Any] | None = None
    if args.baseline_dir:
        try:
            block = audit_baseline_stance(
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
