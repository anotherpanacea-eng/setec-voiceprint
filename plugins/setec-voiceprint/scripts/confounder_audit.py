#!/usr/bin/env python3
"""confounder_audit.py — Layer D differential diagnosis.

Trustworthiness Tier-1 build, paired-release schedule Release 3.
The most leveraged single addition on the trustworthiness roadmap:
the framework currently detects compression and drift but doesn't
synthesize "compressed *relative to what alternative explanation*."
This module is the formal expression of the framework's "the math
doesn't entitle the verdict" stance — a *differential diagnosis*
output rather than a verdict.

The audit reads the existing surface outputs (variance audit,
voice distance, paragraph audit, discourse move signature) and
runs each observed signal pattern against a confounder signature
matrix that maps each candidate alternative explanation
(professional copyediting, register/genre shift, legal/policy memo
style, translation/ESL cleanup, dictation cleanup, house-style
enforcement, developmental revision, AI smoothing, intentional
voice imitation) to expected directions across the signal set.

Output: a *ranked list of compatible explanations* — none presented
as the answer — plus the per-confounder evidence and the
distinguishing-evidence pattern that most rules in or out each
confounder.

Critical: this is NOT a classifier. It is not trained on labeled
data and does not produce probability estimates. The framework's
foundational claim is that the math doesn't entitle the verdict;
the confounder audit's job is to surface the differential, not
to commit to a single cause. The output's compatibility scores
are descriptive ("how many of the observed signals point in the
direction this explanation predicts") rather than probabilistic.

Inputs (all optional; the audit consumes whichever JSONs are
provided):
  --variance-json      output of variance_audit.py --json
  --voice-distance-json output of voice_distance.py --json
  --paragraph-json     output of paragraph_audit.py --json
  --discourse-json     output of discourse_move_signature.py --json
  --aic-json           output of aic_pattern_audit.py --json (future)

The audit downgrades gracefully — fewer inputs means fewer
distinguishing observations, but the output still names the
missing-evidence problem so the reader knows what's underspecified.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore

TASK_SURFACE = "validation"
TOOL_NAME = "confounder_audit"
SCRIPT_VERSION = "1.0"


# --- Confounder signature matrix -------------------------------
#
# Each confounder is a dict mapping a *signal name* to an expected
# direction. Direction values:
#   "high"  — signal expected ELEVATED relative to typical prose
#   "low"   — signal expected DEPRESSED
#   "any"   — signal can go either way (low diagnostic value)
#   "absent"— signal expected to be near zero / not fire
#
# Signal names refer to:
#   sentence_variance         — variance audit's burstiness / sd
#   mdd_variance              — Tier-2 dependency variance
#   lexical_diversity         — MTLD / MATTR / Yules K
#   pos_bigram_kl             — POS-bigram KL against baseline
#   char_ngram_delta          — voice distance's char n-gram family
#   punctuation_regularity    — punctuation cadence (paragraph audit)
#   idiolect_survival         — idiolect_detector preservation
#   connective_density        — variance audit's connective ratio
#   aic_pattern_density       — Layer B named-pattern density
#   paragraph_regularity      — paragraph audit's compression band
#   discourse_marker_density  — discourse audit's total density
#   marked_move_entropy       — discourse audit's marked-only entropy
#   register_match            — register classifier match strength
#   length_localization       — heatmap heterogeneity (uniform vs hot zones)
#
# The matrix below is empirical-shape, not validated. Values
# encode the framework's design hypotheses about each confounder;
# treat them as cues the maintainer can refine as evidence
# accumulates. This is the right place to *add* signal expectations
# when new audits ship (e.g. when Agency and Abstraction Audit
# lands in Release 4, the agency family folds in here).

CONFOUNDER_MATRIX: dict[str, dict[str, str]] = {
    "professional_copyediting": {
        "sentence_variance": "low",
        "lexical_diversity": "any",
        "pos_bigram_kl": "any",
        "punctuation_regularity": "high",
        "idiolect_survival": "high",
        "connective_density": "any",  # editor may normalize either way
        "paragraph_regularity": "high",
        "discourse_marker_density": "any",
        "marked_move_entropy": "any",
        "length_localization": "uniform",  # editor smooths uniformly
    },
    "register_genre_shift": {
        "sentence_variance": "any",
        "pos_bigram_kl": "high",
        "char_ngram_delta": "any",
        "register_match": "low",  # the load-bearing signal here
        "discourse_marker_density": "any",
        "idiolect_survival": "high",  # voice survives across genres
        "paragraph_regularity": "any",
    },
    "legal_or_policy_memo_style": {
        "sentence_variance": "any",
        "pos_bigram_kl": "high",
        "punctuation_regularity": "high",
        "connective_density": "high",
        "aic_pattern_density": "high",  # parallel-template patterns
        "paragraph_regularity": "high",
        "discourse_marker_density": "high",
        "marked_move_entropy": "low",  # narrow set of moves
        "register_match": "any",
    },
    "translation_or_esl_cleanup": {
        "sentence_variance": "low",
        "lexical_diversity": "low",  # idiom loss
        "pos_bigram_kl": "any",
        "punctuation_regularity": "any",
        "idiolect_survival": "low",
        "discourse_marker_density": "low",
    },
    "dictation_or_transcription_cleanup": {
        "sentence_variance": "low",
        "punctuation_regularity": "high",
        "discourse_marker_density": "low",  # fillers removed
        "idiolect_survival": "any",
        "lexical_diversity": "any",
    },
    "house_style_enforcement": {
        "sentence_variance": "any",
        "punctuation_regularity": "high",
        "connective_density": "any",  # template-driven
        "aic_pattern_density": "high",  # template patterns
        "paragraph_regularity": "high",
        "discourse_marker_density": "any",
        "register_match": "high",  # by definition, in-register
    },
    "developmental_revision": {
        "sentence_variance": "any",
        "length_localization": "localized",  # localized hot zones
        "idiolect_survival": "high",
        "paragraph_regularity": "any",
        "discourse_marker_density": "any",
    },
    "ai_smoothing": {
        "sentence_variance": "low",
        "mdd_variance": "low",
        "lexical_diversity": "low",
        "pos_bigram_kl": "high",
        "char_ngram_delta": "high",
        "punctuation_regularity": "high",
        "idiolect_survival": "low",
        "connective_density": "high",
        "aic_pattern_density": "high",
        "paragraph_regularity": "high",
        "discourse_marker_density": "high",
        "marked_move_entropy": "low",
        "length_localization": "uniform",
    },
    "intentional_voice_imitation": {
        "sentence_variance": "any",
        "idiolect_survival": "high",  # over-preserved
        "char_ngram_delta": "low",
        "discourse_marker_density": "any",
        "marked_move_entropy": "any",
        "register_match": "high",
    },
}

CONFOUNDERS = tuple(CONFOUNDER_MATRIX.keys())


# --- Observation extraction ------------------------------------


def _extract_band_signal(
    band: str | None, *, high_label: str = "Heavily smoothed",
    moderate_label: str = "Moderately smoothed",
) -> str:
    """Convert a band-string output ('Lightly smoothed' / 'Moderately
    smoothed' / 'Heavily smoothed') into a directional signal.
    """
    if band is None:
        return "unknown"
    if band == high_label:
        return "high"
    if band == moderate_label:
        return "high"  # moderate also counts as elevated
    if band.startswith("Lightly"):
        return "low"
    return "unknown"


def extract_observations(
    *,
    variance: dict[str, Any] | None = None,
    voice_distance: dict[str, Any] | None = None,
    paragraph: dict[str, Any] | None = None,
    discourse: dict[str, Any] | None = None,
    aic: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Reduce the input audit JSONs to a flat {signal: direction}
    dict. Direction values: "high" / "low" / "uniform" / "localized" /
    "unknown". Signals not in any input are absent from the output.
    """
    obs: dict[str, str] = {}

    # Variance audit ----------------------------------------------
    if variance:
        compression = variance.get("compression") or {}
        flagged = set(compression.get("flagged_signals") or [])
        # Sentence rhythm — signal "low" if any rhythm flag fired,
        # because the registry is "lt"-direction (compressed when
        # value low) for these.
        rhythm = {"burstiness_B", "sentence_length_sd", "fkgl_sd", "mdd_sd"}
        if flagged & rhythm:
            obs["sentence_variance"] = "low"
            obs["mdd_variance"] = "low"
        if flagged & {"mtld", "mattr", "shannon_entropy", "yules_k"}:
            obs["lexical_diversity"] = "low"
        if "connective_density" in flagged:
            obs["connective_density"] = "high"
        if {"adjacent_cosine_mean", "adjacent_cosine_sd"} & flagged:
            # over-cohesion is a "high" reading on baseline relative
            # cohesion; map to high.
            pass  # not directly named in matrix yet
        # POS-bigram KL participation: variance audit's band
        # incorporates POS-bigram KL when a baseline is supplied.
        # If the band is heavy/moderate AND POS-bigram KL was
        # available, we count POS-bigram KL as "high".
        baseline_div = variance.get("baseline_divergences") or {}
        if baseline_div.get("pos_bigrams"):
            kl = baseline_div["pos_bigrams"].get("kl_divergence")
            if isinstance(kl, (int, float)) and kl > 0.15:
                obs["pos_bigram_kl"] = "high"

        # Window heatmap (when present) tells us about localization
        # — uniform vs. hot-zone-clustered.
        windows = variance.get("windows") or {}
        if windows:
            results = windows.get("results") or []
            n_windows = len(results)
            n_hot = sum(
                1 for r in results
                if (r.get("compression") or {}).get("band")
                in {"Heavily smoothed", "Moderately smoothed"}
            )
            if n_windows >= 4:
                hot_fraction = n_hot / n_windows
                if 0.20 <= hot_fraction <= 0.60:
                    obs["length_localization"] = "localized"
                elif hot_fraction > 0.80:
                    obs["length_localization"] = "uniform"
                # Mid-fraction outside [0.2, 0.6] (i.e., 0.6-0.8)
                # leaves the signal unknown.

    # Voice distance ---------------------------------------------
    if voice_distance:
        # Char n-gram family Δ: read off families[char_ngrams_*].
        families = voice_distance.get("families") or {}
        char_deltas = [
            float(info.get("burrows_delta") or 0.0)
            for fam, info in families.items()
            if "char_ngram" in fam
        ]
        if char_deltas:
            mean_delta = sum(char_deltas) / len(char_deltas)
            if mean_delta > 1.5:
                obs["char_ngram_delta"] = "high"
            elif mean_delta < 0.8:
                obs["char_ngram_delta"] = "low"

        # Idiolect survival — voice_distance doesn't compute this
        # directly; surface from a register-match block when present.
        rmatch = voice_distance.get("register_match") or {}
        match_block = rmatch.get("match") or {}
        strength = match_block.get("strength")
        if strength == "strong":
            obs["register_match"] = "high"
        elif strength in {"weak", "mismatch"}:
            obs["register_match"] = "low"

    # Paragraph audit --------------------------------------------
    if paragraph:
        compression = paragraph.get("compression") or {}
        band = compression.get("band")
        para_signal = _extract_band_signal(band)
        if para_signal in {"high", "low"}:
            obs["paragraph_regularity"] = para_signal
        # Punctuation regularity isn't a paragraph_audit signal yet
        # (it lives in voice_profile feature columns); we leave
        # punctuation_regularity unobserved unless the user fills
        # it in elsewhere.

    # Discourse move signature -----------------------------------
    if discourse:
        density = discourse.get("total_marker_density_per_1k", 0.0)
        if density >= 30.0:
            obs["discourse_marker_density"] = "high"
        elif density < 8.0:
            obs["discourse_marker_density"] = "low"
        marked_h = discourse.get("marked_only_entropy_bits")
        if isinstance(marked_h, (int, float)):
            if marked_h <= 1.5:
                obs["marked_move_entropy"] = "low"
            elif marked_h >= 2.5:
                obs["marked_move_entropy"] = "high"

    # AIC pattern audit ------------------------------------------
    if aic:
        # Future: read named-pattern density. For now we only check
        # if the audit reports any pattern at high density.
        densities = aic.get("pattern_densities") or {}
        if densities:
            max_d = max(
                (d for d in densities.values() if isinstance(d, (int, float))),
                default=0.0,
            )
            if max_d >= 1.5:
                obs["aic_pattern_density"] = "high"

    return obs


# --- Confounder scoring ----------------------------------------


def score_confounders(
    observations: dict[str, str],
) -> list[dict[str, Any]]:
    """For each confounder in the matrix, compute a compatibility
    score and per-signal evidence list. Returns a list of dicts
    sorted by descending compatibility.

    Compatibility = (matches + 0.5 * any_signal_matches) / total
    where:
      - "matches" is the count of observed signals whose direction
        matches the confounder's expectation.
      - "any_signal_matches" counts signals where the confounder
        expects "any" — those add half-credit (the confounder
        doesn't predict the signal but doesn't contradict it
        either).
      - "total" is the count of signals where BOTH the confounder
        has an expectation AND the observation is non-empty.
    """
    results: list[dict[str, Any]] = []
    for name, expectations in CONFOUNDER_MATRIX.items():
        matches = 0
        contradictions = 0
        any_signal = 0
        unobserved = []
        evidence: list[str] = []
        contradiction_evidence: list[str] = []
        for signal, expected in expectations.items():
            obs = observations.get(signal)
            if obs is None:
                unobserved.append(signal)
                continue
            if expected == "any":
                any_signal += 1
                continue
            if obs == expected:
                matches += 1
                evidence.append(f"{signal}={obs} matches expected")
            else:
                contradictions += 1
                contradiction_evidence.append(
                    f"{signal}={obs} contradicts expected {expected}"
                )
        total = matches + contradictions + any_signal
        if total == 0:
            score = 0.0
        else:
            score = (matches + 0.5 * any_signal) / total
        results.append({
            "confounder": name,
            "compatibility_score": round(score, 3),
            "n_matches": matches,
            "n_contradictions": contradictions,
            "n_any": any_signal,
            "n_signals_with_expectation": len(expectations),
            "n_observations_used": total,
            "evidence_for": evidence,
            "evidence_against": contradiction_evidence,
            "unobserved_signals": unobserved,
        })
    results.sort(key=lambda r: -r["compatibility_score"])
    return results


def find_distinguishing_evidence(
    observations: dict[str, str],
    ranked_confounders: list[dict[str, Any]],
) -> list[str]:
    """Find observations that *most distinguish* among the top
    candidates — signals where the top-ranked confounders disagree
    on the expected direction.

    Returns a list of human-readable evidence strings that the
    reader can use to triangulate which top candidate is most
    plausible.
    """
    if len(ranked_confounders) < 2:
        return []
    top_confounders = [
        r["confounder"] for r in ranked_confounders[:4]
    ]
    out: list[str] = []
    for signal, observed_direction in observations.items():
        if observed_direction in {"unknown", "any"}:
            continue
        expectations_per_confounder = {}
        for c in top_confounders:
            exp = CONFOUNDER_MATRIX.get(c, {}).get(signal)
            if exp and exp != "any":
                expectations_per_confounder[c] = exp
        directions = set(expectations_per_confounder.values())
        if len(directions) >= 2:
            # Multiple top candidates make differing predictions for
            # this signal; the observation distinguishes them.
            matches = [
                c for c, e in expectations_per_confounder.items()
                if e == observed_direction
            ]
            against = [
                c for c, e in expectations_per_confounder.items()
                if e != observed_direction
            ]
            if matches and against:
                out.append(
                    f"`{signal}={observed_direction}` favors "
                    f"{', '.join(matches)} over {', '.join(against)}"
                )
    return out


def find_missing_evidence(
    observations: dict[str, str],
) -> list[str]:
    """Identify high-leverage signals that are *not* observed.
    Surfaces the missing-evidence problem so readers know what
    they could add to sharpen the differential.
    """
    important_signals = {
        "pos_bigram_kl": "no baseline supplied or POS-bigram KL not computed",
        "char_ngram_delta": "no voice-distance comparison provided",
        "idiolect_survival": "no idiolect_detector output provided",
        "punctuation_regularity": "no punctuation-cadence audit available (ROADMAP Tier-2 promotion)",
        "register_match": "no register classification or baseline match supplied",
        "aic_pattern_density": "no AIC pattern audit provided",
        "length_localization": "no sliding-window heatmap data supplied",
        "discourse_marker_density": "no discourse_move_signature output provided",
        "paragraph_regularity": "no paragraph_audit output provided",
    }
    missing: list[str] = []
    for sig, hint in important_signals.items():
        if sig not in observations:
            missing.append(f"`{sig}` — {hint}")
    return missing


def analyze_confounders(
    *,
    variance: dict[str, Any] | None = None,
    voice_distance: dict[str, Any] | None = None,
    paragraph: dict[str, Any] | None = None,
    discourse: dict[str, Any] | None = None,
    aic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Top-level entry point. Reads input audit JSONs, extracts
    observations, scores confounders, finds distinguishing
    evidence, surfaces missing-evidence list."""
    observations = extract_observations(
        variance=variance,
        voice_distance=voice_distance,
        paragraph=paragraph,
        discourse=discourse,
        aic=aic,
    )
    ranked = score_confounders(observations)
    distinguishing = find_distinguishing_evidence(observations, ranked)
    missing = find_missing_evidence(observations)
    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "observations": observations,
        "ranked_confounders": ranked,
        "distinguishing_evidence": distinguishing,
        "missing_evidence": missing,
        "n_observations": len(observations),
        "inputs_used": {
            "variance": variance is not None,
            "voice_distance": voice_distance is not None,
            "paragraph": paragraph is not None,
            "discourse": discourse is not None,
            "aic": aic is not None,
        },
    }


# --- Markdown rendering ----------------------------------------


def _claim_license_block(report: dict[str, Any]) -> str:
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "A *differential diagnosis* of the observed signal "
            "pattern: which alternative explanations (professional "
            "copyediting, register/genre shift, legal/policy memo "
            "style, translation/ESL cleanup, dictation cleanup, "
            "house-style enforcement, developmental revision, AI "
            "smoothing, intentional voice imitation) are compatible "
            "with the evidence, ranked by per-confounder "
            "compatibility score, with per-signal evidence."
        ),
        does_not_license=(
            "A verdict on which explanation is correct. The audit "
            "is not a classifier and does not produce probability "
            "estimates. The compatibility scores are descriptive "
            "(\"how many observed signals point in the direction "
            "this explanation predicts\"), not probabilistic. The "
            "framework's load-bearing claim is that the math "
            "doesn't entitle the verdict; this audit is the formal "
            "expression of that stance — it surfaces the "
            "differential, it does not commit to a cause."
        ),
        comparison_set={
            "n_observations": report.get("n_observations", 0),
            "inputs_used": ", ".join(
                k for k, v in report.get("inputs_used", {}).items()
                if v
            ) or "(none)",
            "n_confounders_ranked": len(
                report.get("ranked_confounders", [])
            ),
        },
        additional_caveats=[
            "The confounder signature matrix is empirical-shape, "
            "not labeled-corpus-validated. Treat per-confounder "
            "expectations as design hypotheses, not as a calibrated "
            "model.",
            "Compatibility scores are insensitive to the *strength* "
            "of evidence — a weak signal pointing in the expected "
            "direction counts the same as a strong one. Reading "
            "compatibility alongside the per-signal evidence list "
            "is essential.",
            "Distinguishing-evidence rules out *among the top "
            "candidates*; explanations the writer hasn't enumerated "
            "(e.g. translation cleanup combined with a register "
            "shift) aren't scored.",
            "Missing-evidence list names what the user could "
            "supply to sharpen the differential. The audit "
            "downgrades gracefully — fewer inputs means fewer "
            "distinguishing observations, but the output still "
            "surfaces the underspecification.",
        ],
        references=[
            "ROADMAP.md Trustworthiness Tier 1 — Confounder audit / Layer D",
            "1.30.3 CHANGELOG — original framing of the confounder-audit goal",
        ],
    )
    return lic.render_block().rstrip()


def render_report(report: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Confounder audit (Layer D)",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Observations used:** {report['n_observations']}",
        "",
    ]
    inputs = [
        k for k, v in report.get("inputs_used", {}).items() if v
    ]
    if inputs:
        lines.append(f"**Inputs:** {', '.join(inputs)}")
    else:
        lines.append("**Inputs:** _(none — audit will be uninformative)_")
    lines.append("")

    lines.append("## Ranked compatible explanations")
    lines.append("")
    lines.append(
        "Each row is an alternative explanation; the score is the "
        "fraction of observed signals consistent with that "
        "explanation. **Multiple high-scoring candidates is the "
        "expected output — the framework refuses to commit to a "
        "single cause.**"
    )
    lines.append("")
    lines.append("| confounder | score | matches | contradictions | any-signals | observations used |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for r in report["ranked_confounders"]:
        lines.append(
            f"| {r['confounder']} | "
            f"{r['compatibility_score']:.2f} | "
            f"{r['n_matches']} | {r['n_contradictions']} | "
            f"{r['n_any']} | {r['n_observations_used']} |"
        )
    lines.append("")

    if report.get("distinguishing_evidence"):
        lines.append("## Distinguishing evidence")
        lines.append("")
        lines.append(
            "Observations where the top candidates disagree on "
            "expected direction; reading these tells you which "
            "candidate the data favors."
        )
        lines.append("")
        for e in report["distinguishing_evidence"]:
            lines.append(f"- {e}")
        lines.append("")

    lines.append("## Per-confounder evidence")
    lines.append("")
    for r in report["ranked_confounders"]:
        lines.append(
            f"### {r['confounder']} (score {r['compatibility_score']:.2f})"
        )
        lines.append("")
        if r["evidence_for"]:
            lines.append("**For:**")
            for e in r["evidence_for"]:
                lines.append(f"- {e}")
            lines.append("")
        if r["evidence_against"]:
            lines.append("**Against:**")
            for e in r["evidence_against"]:
                lines.append(f"- {e}")
            lines.append("")
        if r["unobserved_signals"]:
            lines.append(
                "**Unobserved (would sharpen the diagnosis):** "
                + ", ".join(f"`{s}`" for s in r["unobserved_signals"])
            )
            lines.append("")

    if report.get("missing_evidence"):
        lines.append("## Missing evidence")
        lines.append("")
        lines.append(
            "High-leverage signals that were *not* observed in this "
            "run. Supplying these inputs would sharpen the "
            "differential diagnosis."
        )
        lines.append("")
        for m in report["missing_evidence"]:
            lines.append(f"- {m}")
        lines.append("")

    lines.append(_claim_license_block(report))
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- CLI -------------------------------------------------------


def _read_json_or_none(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    p = Path(path).expanduser()
    if not p.is_file():
        sys.stderr.write(f"Input not found: {path}\n")
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="confounder_audit.py",
        description=(
            "Layer D differential diagnosis. Reads existing audit "
            "JSON outputs and ranks compatible alternative "
            "explanations for the observed signal pattern. Output "
            "is the differential, not a verdict."
        ),
    )
    p.add_argument(
        "--variance-json",
        help="Path to variance_audit.py --json output.",
    )
    p.add_argument(
        "--voice-distance-json",
        help="Path to voice_distance.py --json output.",
    )
    p.add_argument(
        "--paragraph-json",
        help="Path to paragraph_audit.py --json output.",
    )
    p.add_argument(
        "--discourse-json",
        help="Path to discourse_move_signature.py --json output.",
    )
    p.add_argument(
        "--aic-json",
        help="Path to aic_pattern_audit.py --json output (future).",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON.")
    p.add_argument("--out", help="Write output to this path.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    inputs = {
        "variance": _read_json_or_none(args.variance_json),
        "voice_distance": _read_json_or_none(args.voice_distance_json),
        "paragraph": _read_json_or_none(args.paragraph_json),
        "discourse": _read_json_or_none(args.discourse_json),
        "aic": _read_json_or_none(args.aic_json),
    }
    if all(v is None for v in inputs.values()):
        sys.stderr.write(
            "No input JSONs supplied. Pass at least one of "
            "--variance-json / --voice-distance-json / "
            "--paragraph-json / --discourse-json.\n"
        )
        return 2
    report = analyze_confounders(**inputs)
    out = (
        json.dumps(report, indent=2, default=str)
        if args.json else render_report(report)
    )
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
