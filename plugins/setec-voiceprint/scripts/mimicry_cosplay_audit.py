#!/usr/bin/env python3
"""mimicry_cosplay_audit.py — detect lexical mimicry without
syntactic conformity (paired-release schedule Release 10,
Surfaces Tier 3).

The framework already ships `before_after_restoration.py` with a
metric-gaming heuristic that catches the case where a single
target signal improved while a related aggregate moved against
it. What it does NOT catch is the failure mode where idiolect
phrases survive too conspicuously while function-word grammar
fails to match the lexical mimicry. That's the **style-cosplay**
signature: a draft that scores well on per-feature metrics
because the writer's signature phrases are present, but reads
as imitation because the underlying syntactic and function-word
profile doesn't match the lexical surface.

The methodology is non-obvious and the framework's earlier
write-up of it explicitly named this gap: phrase-level
survival and syntactic Delta need to be **cross-checked**, not
aggregated. A single composite score that averaged phrase
survival and Delta would HIDE the dissociation that is the
cosplay signature. This module does the cross-check.

Two cosplay shapes detected:

  1. **Lexical-without-syntactic**: idiolect-phrase survival is
     high (the writer's signature phrases appear at or above
     baseline-typical density) AND function-word / syntactic
     Delta is also high (the target deviates from the writer's
     baseline on the syntactic axis). Normal authorial work
     shows the OPPOSITE pattern — idiolect survival and Delta
     are negatively correlated. The dissociation is the flag.

  2. **Phrase-density anomaly**: signature phrases appear at
     UNNATURALLY high density. A baseline-frequency-aware check
     compares the target's per-1k density to an expected
     density derived from the baseline; large positive
     deviations flag over-preservation.

Use cases:
  - Adversarial-fixture review: when the framework's own
    restoration tools produce a draft, a cosplay audit catches
    over-targeted revisions that hit the metric but lost the
    voice.
  - Imitation detection: when a draft is suspected to be
    written ABOUT a writer (or by an LLM mimicking the writer),
    cosplay shape #1 is the primary signal.
  - Restoration QC: a writer's editor running cosplay over the
    edited draft can catch over-preserved idiolect that reads
    as caricature.

Inputs:
  --target TEXT                  text to audit
  --idiolect-json IDIOLECT.json  preservation list (output of
                                 idiolect_detector.py)
  --voice-distance-json VOICE.json  voice_distance audit on the
                                    same target (provides
                                    weighted_delta + per-family
                                    Deltas)
  --variance-json VARIANCE.json  optional variance_audit on the
                                 target (used for POS-bigram KL)
  --baseline-density-per-1k FLOAT
                                 optional: expected idiolect-
                                 phrase density per 1,000 words
                                 in baseline prose (default 5.0
                                 — heuristic; calibration-pending)
  --high-survival-threshold FLOAT
                                 idiolect survival rate above
                                 which the lexical-without-
                                 syntactic shape can fire
                                 (default 0.6)
  --high-delta-threshold FLOAT   weighted_delta above which the
                                 syntactic axis is "high"
                                 (default 1.25, matches
                                 voice_distance_band's "Light
                                 drift" cutoff)

task_surface: voice_coherence. The audit refuses authorship
verdicts. A cosplay flag is evidence the draft is NOT a normal
authorial sample of this writer — it does not commit to whether
the sample is AI-generated, human imitation, an over-targeted
voice restoration, or the writer self-consciously imitating
themselves.
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

from claim_license import ClaimLicense  # type: ignore


TASK_SURFACE = "voice_coherence"
TOOL_NAME = "mimicry_cosplay_audit"
SCRIPT_VERSION = "1.0"


# ---------- Idiolect-phrase survival ----------


def _extract_phrases_from_idiolect(
    idiolect: dict[str, Any],
) -> list[str]:
    """Read the preservation list out of an `idiolect_detector`
    JSON. Accepts both the structured-list shape (each item a
    dict with `phrase`) and the bare-string-list shape."""
    preservation = idiolect.get("preservation_list") or []
    out: list[str] = []
    for item in preservation:
        if isinstance(item, dict):
            phrase = item.get("phrase") or item.get("display") or ""
        elif isinstance(item, str):
            phrase = item
        else:
            phrase = ""
        if phrase:
            out.append(phrase)
    return out


def _phrase_hits(
    target_text: str, phrases: list[str],
) -> tuple[int, int, list[str], list[str]]:
    """Return ``(n_unique_matched, n_total_occurrences,
    matched_phrases, missing_phrases)`` over case-insensitive
    substring search.

    ``n_unique_matched`` counts each preservation-list phrase at
    most once and is the right value for the survival-rate
    diagnostic (\"how many of the writer's signature phrases
    survive in the revision?\").

    ``n_total_occurrences`` counts each phrase by its number of
    appearances in the target and is the right value for the
    density-anomaly diagnostic (\"is the writer's signature
    phrase appearing at unnaturally high density in the
    revision?\"). The two diagnostics are intentionally
    separate — over-preservation by repetition would be invisible
    if both used the unique count.
    """
    if not phrases:
        return 0, 0, [], []
    text_lower = target_text.lower()
    matched: list[str] = []
    missing: list[str] = []
    n_total_occurrences = 0
    for p in phrases:
        if not p:
            continue
        # Count case-insensitive non-overlapping occurrences in
        # the target. Falls back to substring `count()` which
        # is sufficient for word-boundary phrase matches in
        # natural prose (overlapping idiolect phrases are rare).
        count = text_lower.count(p.lower())
        if count > 0:
            matched.append(p)
            n_total_occurrences += count
        else:
            missing.append(p)
    return len(matched), n_total_occurrences, matched, missing


def compute_idiolect_survival(
    *,
    idiolect: dict[str, Any],
    target_text: str,
) -> dict[str, Any]:
    phrases = _extract_phrases_from_idiolect(idiolect)
    n_unique, n_occurrences, matched, missing = _phrase_hits(
        target_text, phrases,
    )
    rate = n_unique / len(phrases) if phrases else None
    n_words = len(re.findall(r"\b\w+\b", target_text))
    # Two densities tracked separately:
    # - ``target_density_per_1k`` (occurrence-based): the right
    #   value for the density-anomaly cosplay shape.
    # - ``unique_phrase_density_per_1k`` (coverage-based): kept
    #   for legacy compatibility with downstream callers and as
    #   a coverage diagnostic.
    density = (
        n_occurrences / n_words * 1000 if n_words else 0.0
    )
    unique_density = (
        n_unique / n_words * 1000 if n_words else 0.0
    )
    return {
        "n_phrases": len(phrases),
        "n_matched": n_unique,
        "n_total_occurrences": n_occurrences,
        "matched_phrases": matched[:50],
        "missing_phrases": missing[:50],
        "survival_rate": rate,
        # Occurrence-based density — the value the density-anomaly
        # cosplay shape reads. A signature phrase repeated 20 times
        # in 2k words contributes 20 to the count.
        "target_density_per_1k": density,
        # Coverage-based density — what proportion of the
        # preservation list is represented at all. Stays informative
        # alongside the survival_rate diagnostic.
        "unique_phrase_density_per_1k": unique_density,
        "target_words": n_words,
    }


# ---------- Voice-distance reading ----------


def _read_weighted_delta(
    voice_distance: dict[str, Any] | None,
) -> float | None:
    if not voice_distance:
        return None
    overall = voice_distance.get("overall") or {}
    val = overall.get("weighted_delta")
    if isinstance(val, (int, float)):
        return float(val)
    return None


def _read_pos_bigram_kl(
    variance: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the POS-bigram KL block if available. Reads
    `variance['compression']['pos_bigram_kl']` (the correct
    nesting per the 1.37.2 fix)."""
    if not variance:
        return None
    compression = variance.get("compression") or {}
    block = compression.get("pos_bigram_kl")
    if not isinstance(block, dict):
        block = variance.get("pos_bigram_kl")
    if not isinstance(block, dict):
        return None
    return block


# ---------- Cosplay verdict ----------


def _classify_cosplay(
    *,
    survival: dict[str, Any],
    weighted_delta: float | None,
    pos_bigram_kl: dict[str, Any] | None,
    baseline_density_per_1k: float,
    high_survival_threshold: float,
    high_delta_threshold: float,
    over_preservation_factor: float,
) -> dict[str, Any]:
    """Return verdict + per-shape evidence."""
    survival_rate = survival.get("survival_rate")
    target_density = survival.get("target_density_per_1k") or 0.0

    survival_high = (
        survival_rate is not None
        and survival_rate >= high_survival_threshold
    )
    delta_high = (
        weighted_delta is not None
        and weighted_delta >= high_delta_threshold
    )
    kl_compressed = bool(
        pos_bigram_kl and pos_bigram_kl.get("compressed")
    )

    # Shape 1: lexical-without-syntactic dissociation.
    shape1_lexical_without_syntactic = (
        survival_high and (delta_high or kl_compressed)
    )

    # Shape 2: phrase-density anomaly.
    over_density = (
        target_density >= baseline_density_per_1k * over_preservation_factor
    )
    shape2_density_anomaly = over_density and survival_rate is not None

    # Verdict.
    if shape1_lexical_without_syntactic and shape2_density_anomaly:
        verdict = "cosplay_suspected"
    elif shape1_lexical_without_syntactic or shape2_density_anomaly:
        verdict = "mixed"
    elif (
        survival_rate is None
        and weighted_delta is None
        and pos_bigram_kl is None
    ):
        verdict = "unknown"
    elif (
        survival_rate is not None
        and not survival_high
    ) or (
        weighted_delta is not None and not delta_high
    ):
        verdict = "not_cosplay"
    else:
        verdict = "unknown"

    return {
        "verdict": verdict,
        "shapes": {
            "lexical_without_syntactic": {
                "fired": shape1_lexical_without_syntactic,
                "survival_high": survival_high,
                "delta_high": delta_high,
                "pos_bigram_kl_compressed": kl_compressed,
            },
            "density_anomaly": {
                "fired": shape2_density_anomaly,
                "target_density_per_1k": target_density,
                "baseline_density_per_1k": baseline_density_per_1k,
                "over_preservation_factor": over_preservation_factor,
            },
        },
        "thresholds_used": {
            "high_survival_threshold": high_survival_threshold,
            "high_delta_threshold": high_delta_threshold,
            "baseline_density_per_1k": baseline_density_per_1k,
            "over_preservation_factor": over_preservation_factor,
        },
    }


# ---------- Top-level audit ----------


def audit_cosplay(
    *,
    target_text: str,
    idiolect: dict[str, Any] | None,
    voice_distance: dict[str, Any] | None,
    variance: dict[str, Any] | None,
    baseline_density_per_1k: float = 5.0,
    high_survival_threshold: float = 0.6,
    high_delta_threshold: float = 1.25,
    over_preservation_factor: float = 2.0,
) -> dict[str, Any]:
    if idiolect is None:
        survival: dict[str, Any] = {
            "n_phrases": 0,
            "n_matched": 0,
            "n_total_occurrences": 0,
            "survival_rate": None,
            "target_density_per_1k": 0.0,
            "unique_phrase_density_per_1k": 0.0,
            "target_words": len(
                re.findall(r"\b\w+\b", target_text)
            ),
        }
    else:
        survival = compute_idiolect_survival(
            idiolect=idiolect, target_text=target_text,
        )

    weighted_delta = _read_weighted_delta(voice_distance)
    pos_bigram_kl = _read_pos_bigram_kl(variance)

    classification = _classify_cosplay(
        survival=survival,
        weighted_delta=weighted_delta,
        pos_bigram_kl=pos_bigram_kl,
        baseline_density_per_1k=baseline_density_per_1k,
        high_survival_threshold=high_survival_threshold,
        high_delta_threshold=high_delta_threshold,
        over_preservation_factor=over_preservation_factor,
    )

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "idiolect_survival": survival,
        "voice_distance": {
            "weighted_delta": weighted_delta,
            "available": weighted_delta is not None,
        },
        "pos_bigram_kl": (
            pos_bigram_kl if pos_bigram_kl else {"available": False}
        ),
        "verdict": classification["verdict"],
        "shapes": classification["shapes"],
        "thresholds_used": classification["thresholds_used"],
        "claim_license": _claim_license_dict(
            verdict=classification["verdict"],
            shapes=classification["shapes"],
            survival=survival,
        ),
    }


def _claim_license_dict(
    *,
    verdict: str,
    shapes: dict[str, Any],
    survival: dict[str, Any],
) -> dict[str, Any]:
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "A cosplay-shape detection report. Tells the reader "
            "whether the cross-checked pattern of (high "
            "idiolect-phrase survival + high syntactic Delta) "
            "or (unnaturally high phrase density) is present in "
            "the target, AND surfaces the per-shape evidence "
            "that drove the verdict. The audit's value is in "
            "**not aggregating** these signals — it reports the "
            "dissociation between lexical and syntactic axes "
            "that aggregate scores would hide."
        ),
        does_not_license=(
            "An authorship verdict. A `cosplay_suspected` "
            "verdict is evidence the target is NOT a normal "
            "authorial sample of this writer; it does NOT "
            "commit to whether the sample is AI-generated, "
            "human imitation of the writer, an over-targeted "
            "voice-restoration pass, or the writer self-"
            "consciously imitating themselves. The framework's "
            "discipline of \"differential diagnosis, not "
            "verdict\" applies in full. Pair this audit with "
            "the confounder audit and the evidentiary-conditions "
            "gate before drawing conclusions."
        ),
        comparison_set={
            "n_idiolect_phrases": survival.get("n_phrases", 0),
            "n_matched_phrases": survival.get("n_matched", 0),
            "survival_rate": survival.get("survival_rate"),
            "target_words": survival.get("target_words"),
            "verdict": verdict,
            "shape1_fired": (
                shapes.get("lexical_without_syntactic", {})
                .get("fired", False)
            ),
            "shape2_fired": (
                shapes.get("density_anomaly", {}).get("fired", False)
            ),
        },
        additional_caveats=[
            "Cosplay shapes are heuristic and calibration-"
            "pending. The default thresholds (0.6 survival "
            "rate, 1.25 weighted_delta — voice_distance_band's "
            "\"Light drift\" cutoff, 5.0 baseline density per "
            "1k, 2.0× over-preservation factor) are documented "
            "defaults, not labeled-corpus-validated values.",
            "Idiolect-phrase survival is computed by case-"
            "insensitive substring match, mirroring the "
            "convention `confounder_audit` uses. Phrase "
            "preservation does not require sentence-level "
            "structural equivalence; a cosplay revision that "
            "preserves the phrase but breaks the surrounding "
            "syntax will read as preserved here.",
            "The audit composes with `before_after_restoration` "
            "(metric-gaming detection), `surface_disagreement_"
            "resolver` (cross-surface meta-interpretation), and "
            "`semantic_preservation_check` (semantic guardrails) "
            "— all four catch related but distinct failure "
            "modes. A draft can pass `before_after_restoration` "
            "AND fail cosplay; the two audits do not aggregate.",
        ],
    )
    return {"rendered": lic.render_block().rstrip()}


# ---------- Markdown rendering ----------


_VERDICT_GLYPH = {
    "not_cosplay": "✓",
    "mixed": "·",
    "cosplay_suspected": "✗",
    "unknown": "—",
}


def render_report(audit: dict[str, Any]) -> str:
    verdict = audit.get("verdict", "unknown")
    glyph = _VERDICT_GLYPH.get(verdict, "?")
    survival = audit.get("idiolect_survival", {})
    voice = audit.get("voice_distance", {})
    pb = audit.get("pos_bigram_kl") or {}
    shapes = audit.get("shapes", {})

    lines: list[str] = [
        "# Mimicry / style-cosplay audit",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Verdict:** `{verdict}` {glyph}",
        "",
        "## Per-axis evidence",
        "",
        f"- **Idiolect-phrase survival:** "
        f"{survival.get('n_matched', 0)}/"
        f"{survival.get('n_phrases', 0)} "
        f"(rate "
        f"{survival.get('survival_rate') if survival.get('survival_rate') is not None else 'n/a'}, "
        f"density {survival.get('target_density_per_1k', 0):.2f}/1k)",
    ]
    if voice.get("available"):
        lines.append(
            f"- **voice_distance.weighted_delta:** "
            f"{voice.get('weighted_delta'):.3f}"
        )
    else:
        lines.append("- **voice_distance:** unavailable")
    if pb.get("in_band"):
        lines.append(
            f"- **POS-bigram KL:** "
            f"value {pb.get('value', 'n/a')}, "
            f"compressed: {pb.get('compressed', False)}"
        )
    else:
        lines.append("- **POS-bigram KL:** unavailable")
    lines.append("")

    lines.append("## Cosplay shapes")
    lines.append("")
    shape1 = shapes.get("lexical_without_syntactic", {})
    lines.append(
        f"- **Lexical-without-syntactic:** "
        f"{'**fired**' if shape1.get('fired') else 'not fired'}"
    )
    lines.append(
        f"  - survival_high: {shape1.get('survival_high')}"
    )
    lines.append(
        f"  - delta_high: {shape1.get('delta_high')}"
    )
    lines.append(
        f"  - pos_bigram_kl_compressed: "
        f"{shape1.get('pos_bigram_kl_compressed')}"
    )
    shape2 = shapes.get("density_anomaly", {})
    lines.append(
        f"- **Density anomaly:** "
        f"{'**fired**' if shape2.get('fired') else 'not fired'}"
    )
    lines.append(
        f"  - target density: "
        f"{shape2.get('target_density_per_1k', 0):.2f}/1k"
    )
    lines.append(
        f"  - baseline expected: "
        f"{shape2.get('baseline_density_per_1k', 0):.2f}/1k"
    )
    lines.append(
        f"  - factor: {shape2.get('over_preservation_factor', 0)}×"
    )
    lines.append("")

    license_block = audit.get("claim_license", {}).get("rendered", "")
    if license_block:
        lines.append(license_block)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------- CLI ----------


def _read_json_or_none(path: str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(
            f"User-supplied JSON input not found: {path}"
        )
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"User-supplied JSON input {path} is not valid JSON: {exc}"
        ) from exc


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mimicry_cosplay_audit.py",
        description=(
            "Detect lexical mimicry without syntactic conformity "
            "(style-cosplay shape). Cross-checks idiolect-phrase "
            "survival against function-word / POS-bigram Delta; "
            "flags the dissociation pattern that aggregate "
            "scoring hides."
        ),
    )
    p.add_argument(
        "--target", required=True,
        help="Path to the target text.",
    )
    p.add_argument(
        "--idiolect-json",
        help="Path to an idiolect_detector JSON output "
             "(provides preservation_list).",
    )
    p.add_argument(
        "--voice-distance-json",
        help="Path to a voice_distance audit JSON.",
    )
    p.add_argument(
        "--variance-json",
        help="Path to a variance_audit JSON (provides POS-bigram KL).",
    )
    p.add_argument(
        "--baseline-density-per-1k", type=float, default=5.0,
        help="Expected idiolect-phrase density per 1k words in "
             "baseline prose (default 5.0; calibration-pending).",
    )
    p.add_argument(
        "--high-survival-threshold", type=float, default=0.6,
        help="Idiolect-phrase survival rate above which the "
             "lexical-without-syntactic shape can fire "
             "(default 0.6).",
    )
    p.add_argument(
        "--high-delta-threshold", type=float, default=1.25,
        help="weighted_delta above which the syntactic axis is "
             "high (default 1.25, voice_distance_band's "
             "\"Light drift\" cutoff).",
    )
    p.add_argument(
        "--over-preservation-factor", type=float, default=2.0,
        help="target_density / baseline_density factor above "
             "which density anomaly fires (default 2.0×).",
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--out")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    target_path = Path(args.target).expanduser()
    if not target_path.is_file():
        sys.stderr.write(f"--target: file not found: {args.target}\n")
        return 2
    target_text = target_path.read_text(
        encoding="utf-8", errors="ignore",
    )
    if not target_text.strip():
        sys.stderr.write(
            f"--target: file is empty: {args.target}\n"
        )
        return 2

    try:
        idiolect = _read_json_or_none(args.idiolect_json)
        voice_distance = _read_json_or_none(args.voice_distance_json)
        variance = _read_json_or_none(args.variance_json)
    except (FileNotFoundError, ValueError) as exc:
        sys.stderr.write(f"Input error: {exc}\n")
        return 2

    audit = audit_cosplay(
        target_text=target_text,
        idiolect=idiolect,
        voice_distance=voice_distance,
        variance=variance,
        baseline_density_per_1k=args.baseline_density_per_1k,
        high_survival_threshold=args.high_survival_threshold,
        high_delta_threshold=args.high_delta_threshold,
        over_preservation_factor=args.over_preservation_factor,
    )

    out = (
        json.dumps(audit, indent=2, default=str)
        if args.json else render_report(audit)
    )
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
