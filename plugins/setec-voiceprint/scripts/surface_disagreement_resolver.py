#!/usr/bin/env python3
"""surface_disagreement_resolver.py — cross-surface interpretation meta-layer.

Trustworthiness Tier-1 build, paired-release schedule Release 7.
The framework runs multiple surfaces over the same draft —
smoothing diagnosis (variance), voice coherence (Burrows Delta),
General Imposters (impostor pool), idiolect detector (preservation
list), AIC pattern density (named figures), paragraph audit
(macro-rhythm), discourse audit (typed scaffolding), agency audit
(abstraction). Each answers a different question. **Cross-surface
interpretation** has been left to the reader to do by hand.

This module is the meta-layer. It reads any subset of audit JSONs
and surfaces interpretable disagreement patterns:

  - High smoothing + low voice drift → author likely wrote it but
    it was heavily edited / smoothed.
  - Low smoothing + high voice drift → genre shift, impostor,
    collaboration, or intentional style change.
  - High voice drift + high idiolect survival → imitation,
    self-conscious revision, or phrase-level preservation with
    deeper structural change.
  - High POS-bigram KL + normal sentence variance → syntactic-
    template shift without obvious rhythm compression.
  - High AIC density + normal Layer A → rhetorical habit issue,
    not smoothing.
  - GI gray zone + high Delta → candidate comparison inconclusive
    despite baseline distance.
  - High agency loss + normal voice distance → the writer's
    distinctive vocabulary survived but the *register* shifted
    toward institutional / abstract.

The resolver returns a ranked list of compatible interpretations,
each with the supporting cross-surface evidence and the disagreement
pattern that triggered it. It is not a classifier — multiple
interpretations may be jointly compatible, and the framework
declines to pick one. This is the meta-version of the confounder
audit's "differential diagnosis, not verdict" stance.

Inputs (any subset; resolver degrades gracefully):
  --variance-json          smoothing-diagnosis output
  --voice-distance-json    voice-coherence output
  --gi-json                General Imposters output
  --paragraph-json         paragraph audit
  --discourse-json         discourse audit
  --agency-json            agency / abstraction audit
  --aic-json               AIC pattern audit (future)
  --idiolect-json + --target-text  idiolect preservation list +
                                    target for survival rate
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

from output_schema import build_output  # type: ignore
from claim_license import (  # type: ignore
    ClaimLicense,
    with_state_caveats,
)

TASK_SURFACE = "validation"
TOOL_NAME = "surface_disagreement_resolver"
SCRIPT_VERSION = "1.0"


# --- Surface readings ------------------------------------------
#
# Each surface gets reduced to a small set of categorical
# directional labels: ``high`` / ``moderate`` / ``low`` /
# ``unknown``. The disagreement-pattern matcher reads these
# labels and proposes interpretations.

SmoothingLevel = str  # high / moderate / low / unknown
DriftLevel = str  # same vocabulary
PoolDecision = str  # consistent / inconsistent / gray_zone / unknown
IdiolectSurvival = str  # high / moderate / low / unknown


def _read_smoothing_level(variance: dict[str, Any] | None) -> SmoothingLevel:
    if not variance:
        return "unknown"
    band = (variance.get("compression") or {}).get("band")
    if band == "Heavily smoothed":
        return "high"
    if band == "Moderately smoothed":
        return "moderate"
    if band == "Lightly smoothed":
        return "low"
    return "unknown"


def _read_voice_drift_level(voice_distance: dict[str, Any] | None) -> DriftLevel:
    """Map voice_distance audit output to a drift level.

    Reads the actual band strings produced by
    ``stylometry_core.voice_distance_band``:

      - ``Close to baseline (...)``  → ``low``
      - ``Light drift (...)``        → ``moderate``
      - ``Strong drift (...)``       → ``high``
      - ``Off-baseline (...)``       → ``high``

    Falls back to ``overall.weighted_delta`` thresholds when the
    band string is missing or unrecognized (the same cutoffs the
    band assignment itself uses: 0.75 / 1.25 / 2.0). Substring
    matches on legacy / synonym strings (``near``, ``far``,
    ``moderate``, ``distant``) are kept as a last resort so older
    test fixtures and any custom band strings still parse.
    """
    if not voice_distance:
        return "unknown"
    overall = voice_distance.get("overall") or {}
    band = overall.get("band")

    # Primary path: known band strings from voice_distance_band().
    if isinstance(band, str) and band:
        band_norm = band.split("(", 1)[0].strip().lower()
        if band_norm == "close to baseline":
            return "low"
        if band_norm == "light drift":
            return "moderate"
        if band_norm == "strong drift":
            return "high"
        if band_norm == "off-baseline" or band_norm == "off baseline":
            return "high"

    # Secondary path: weighted_delta numeric fallback (matches the
    # cutoffs voice_distance_band itself uses: < 0.75 close,
    # < 1.25 light, < 2.0 strong, ≥ 2.0 off-baseline).
    score = overall.get("weighted_delta")
    if isinstance(score, (int, float)):
        if score < 0.75:
            return "low"
        if score < 1.25:
            return "moderate"
        return "high"

    # Tertiary path: legacy / synonym substring match for older
    # fixtures and custom band labels.
    if isinstance(band, str) and band:
        band_lower = band.lower()
        if "near" in band_lower or "close" in band_lower:
            return "low"
        if "moderate" in band_lower:
            return "moderate"
        if "far" in band_lower or "distant" in band_lower:
            return "high"

    return "unknown"


def _read_gi_decision(gi: dict[str, Any] | None) -> PoolDecision:
    if not gi:
        return "unknown"
    decision = gi.get("decision")
    if decision == "consistent_with_candidate":
        return "consistent"
    if decision == "inconsistent_with_candidate":
        return "inconsistent"
    if decision == "gray_zone_refused":
        return "gray_zone"
    if decision == "refused":
        return "refused"
    return "unknown"


def _read_pos_bigram_kl(variance: dict[str, Any] | None) -> str:
    """Return ``high`` / ``moderate`` / ``low`` / ``unknown`` for
    POS-bigram KL against baseline.

    The POS-bigram KL block is emitted at
    ``variance["compression"]["pos_bigram_kl"]`` (the variable lives
    inside ``classify_compression()`` whose return is assigned to
    the ``compression`` key in the audit JSON). A legacy top-level
    ``variance["pos_bigram_kl"]`` shape is accepted as a fallback
    so any older fixture or hand-built input still parses.
    """
    if not variance:
        return "unknown"
    compression = variance.get("compression") or {}
    kl_info = compression.get("pos_bigram_kl")
    if not isinstance(kl_info, dict):
        # Legacy / fixture fallback.
        kl_info = variance.get("pos_bigram_kl") or {}
    if not isinstance(kl_info, dict) or not kl_info.get("in_band"):
        return "unknown"
    if kl_info.get("compressed"):
        return "high"
    val = kl_info.get("value")
    threshold = kl_info.get("threshold", 0.15)
    if isinstance(val, (int, float)):
        if val >= threshold * 1.5:
            return "high"
        if val >= threshold:
            return "moderate"
        return "low"
    return "unknown"


def _read_aic_density(aic: dict[str, Any] | None) -> str:
    """Return a directional reading for AIC named-pattern density.

    aic_pattern_audit emits ``patterns.<pattern_key>.density_per_1k``
    (one entry per named pattern). We pick the maximum target
    density across patterns. A legacy top-level
    ``aic["pattern_densities"]`` flat-dict shape is accepted as a
    fallback so older fixtures still parse.
    """
    if not aic:
        return "unknown"

    densities: list[float] = []
    patterns = aic.get("patterns")
    if isinstance(patterns, dict) and patterns:
        for block in patterns.values():
            if not isinstance(block, dict):
                continue
            d = block.get("density_per_1k")
            if isinstance(d, (int, float)):
                densities.append(float(d))

    if not densities:
        # Legacy / fixture fallback shape.
        legacy = aic.get("pattern_densities") or {}
        if isinstance(legacy, dict):
            densities = [
                float(d) for d in legacy.values()
                if isinstance(d, (int, float))
            ]

    if not densities:
        return "unknown"
    max_d = max(densities)
    if max_d >= 1.5:
        return "high"
    if max_d >= 0.5:
        return "moderate"
    return "low"


def _read_paragraph_band(paragraph: dict[str, Any] | None) -> str:
    if not paragraph:
        return "unknown"
    band = (paragraph.get("compression") or {}).get("band")
    if band == "Heavily smoothed":
        return "high"
    if band == "Moderately smoothed":
        return "moderate"
    if band == "Lightly smoothed":
        return "low"
    return "unknown"


def _read_discourse_band(discourse: dict[str, Any] | None) -> str:
    if not discourse:
        return "unknown"
    band = (discourse.get("compression") or {}).get("band")
    if band == "Heavily scaffolded":
        return "high"
    if band == "Moderately scaffolded":
        return "moderate"
    if band == "Lightly scaffolded":
        return "low"
    return "unknown"


def _read_agency_band(agency: dict[str, Any] | None) -> str:
    if not agency:
        return "unknown"
    band = (agency.get("compression") or {}).get("band")
    if band == "Heavily abstracted":
        return "high"
    if band == "Moderately abstracted":
        return "moderate"
    if band == "Lightly abstracted":
        return "low"
    return "unknown"


def _read_idiolect_survival(
    idiolect: dict[str, Any] | None,
    target_text: str | None,
) -> IdiolectSurvival:
    """Compute preservation-list survival rate (same logic as
    confounder_audit._idiolect_survival_rate)."""
    if not idiolect or not target_text:
        return "unknown"
    preservation = idiolect.get("preservation_list") or []
    if not preservation:
        return "unknown"
    target_lower = target_text.lower()
    matches = 0
    for item in preservation:
        if isinstance(item, dict):
            phrase = item.get("phrase") or item.get("display") or ""
        elif isinstance(item, str):
            phrase = item
        else:
            phrase = ""
        if phrase and phrase.lower() in target_lower:
            matches += 1
    rate = matches / len(preservation)
    if rate >= 0.6:
        return "high"
    if rate < 0.3:
        return "low"
    return "moderate"


# --- Disagreement patterns -------------------------------------
#
# Each pattern is a dict mapping signal names to expected
# directional values, and a name + interpretation. The matcher
# finds patterns whose expected values match the readings and
# returns them as compatible interpretations.

# The magic value `*` matches any reading. `not_X` matches any
# reading other than X. Patterns can use `(low|moderate)` to
# match either.

DISAGREEMENT_PATTERNS: tuple[dict[str, Any], ...] = (
    {
        "name": "edited_authorial_voice",
        "interpretation": (
            "High smoothing + low voice drift → author likely "
            "wrote it but it was heavily edited or smoothed. "
            "Distributional compression is present; voiceprint "
            "is intact. Common with professional copyediting on "
            "the writer's own work."
        ),
        "signals": {
            "smoothing": "high",
            "voice_drift": "low",
        },
    },
    {
        "name": "register_shift_or_collaboration",
        "interpretation": (
            "Low smoothing + high voice drift → genre shift, "
            "impostor, collaboration, or intentional style "
            "change. The writer's distributional fingerprint is "
            "fine but the voice has moved. The framework cannot "
            "distinguish these without additional context."
        ),
        "signals": {
            "smoothing": "low",
            "voice_drift": "high",
        },
    },
    {
        "name": "self_conscious_imitation",
        "interpretation": (
            "High voice drift + high idiolect survival → "
            "imitation, self-conscious revision, or phrase-level "
            "preservation with deeper structural change. Writer "
            "(or imitator) preserved the recognizable phrases "
            "but the underlying syntactic / function-word pattern "
            "shifted."
        ),
        "signals": {
            "voice_drift": "high",
            "idiolect_survival": "high",
        },
    },
    {
        "name": "syntactic_template_shift",
        "interpretation": (
            "High POS-bigram KL + normal sentence variance → "
            "syntactic-template shift without obvious rhythm "
            "compression. The writer's sentence rhythm is fine "
            "but the syntactic patterns the prose relies on have "
            "moved. Often diagnostic of AI-shaped prose where "
            "Layer A passes but the underlying templates are "
            "AI-characteristic."
        ),
        "signals": {
            "pos_bigram_kl": "high",
            "smoothing": "(low|moderate)",
        },
    },
    {
        "name": "rhetorical_habit_not_smoothing",
        "interpretation": (
            "High AIC density + normal Layer A → rhetorical "
            "habit issue, not smoothing. The writer's named "
            "patterns (correctio, pseudo-aphorism, etc.) are "
            "over-represented but distributional signals are "
            "fine. This is craft territory, not provenance."
        ),
        "signals": {
            "aic_density": "high",
            "smoothing": "(low|moderate)",
        },
    },
    {
        "name": "gi_inconclusive_despite_drift",
        "interpretation": (
            "GI gray-zone + high voice drift → candidate "
            "comparison is inconclusive despite baseline "
            "distance. The writer is far from baseline AND the "
            "impostor pool can't reach a verdict. Often the "
            "case when impostors are register-mismatched."
        ),
        "signals": {
            "gi_decision": "gray_zone",
            "voice_drift": "high",
        },
    },
    {
        "name": "register_drift_to_institutional",
        "interpretation": (
            "High agency loss + normal voice distance → the "
            "writer's distinctive vocabulary survived but the "
            "*register* shifted toward institutional / abstract. "
            "The voiceprint is fine; the audience or topic moved."
        ),
        "signals": {
            "agency": "high",
            "voice_drift": "(low|moderate)",
        },
    },
    {
        "name": "discourse_scaffolding_overload",
        "interpretation": (
            "High discourse scaffolding + low smoothing → the "
            "writer is heavily marking moves (concession, "
            "consequence, exemplification) without distributional "
            "compression. Common in academic / policy registers. "
            "Not provenance evidence on its own."
        ),
        "signals": {
            "discourse": "high",
            "smoothing": "low",
        },
    },
    {
        "name": "paragraph_regularization_only",
        "interpretation": (
            "High paragraph regularization + low smoothing + "
            "low voice drift → the writer's voice and "
            "distributional signals are intact but the *macro-"
            "rhythm* has been regularized into rectangle "
            "paragraphs. Often a copyediting or template "
            "intervention; reads as voice-preserving smoothing."
        ),
        "signals": {
            "paragraph": "high",
            "smoothing": "low",
            "voice_drift": "low",
        },
    },
    {
        "name": "agreement_high_compression",
        "interpretation": (
            "All available surfaces fire high → multiple "
            "independent signals support the same call. This is "
            "agreement, not disagreement. Surface-disagreement "
            "resolver will surface a low number of compatible "
            "interpretations because the readings are mutually "
            "supporting."
        ),
        "signals": {
            "smoothing": "high",
            "voice_drift": "high",
        },
    },
)


def _matches_value(reading: str, expected: str) -> bool:
    """Match `reading` against an `expected` pattern token.

    Tokens: `*` (any), `(a|b)` (one of), bare label (exact match).
    `unknown` readings only match `*` — surfaces that didn't
    report don't trigger interpretations.
    """
    if reading == "unknown":
        return expected == "*"
    if expected == "*":
        return True
    if expected.startswith("(") and expected.endswith(")"):
        options = expected[1:-1].split("|")
        return reading in options
    if expected.startswith("not_"):
        return reading != expected[4:]
    return reading == expected


def _pattern_matches(
    pattern: dict[str, Any], readings: dict[str, str],
) -> tuple[bool, list[str]]:
    """Check whether `pattern` matches the surface `readings`.

    Returns ``(matches, supporting_evidence)`` where
    `supporting_evidence` lists the per-signal matches.
    """
    supporting: list[str] = []
    for signal, expected in pattern["signals"].items():
        reading = readings.get(signal, "unknown")
        if not _matches_value(reading, expected):
            return False, []
        supporting.append(f"{signal}={reading} (expected {expected})")
    return True, supporting


def resolve(
    *,
    variance: dict[str, Any] | None = None,
    voice_distance: dict[str, Any] | None = None,
    gi: dict[str, Any] | None = None,
    paragraph: dict[str, Any] | None = None,
    discourse: dict[str, Any] | None = None,
    agency: dict[str, Any] | None = None,
    aic: dict[str, Any] | None = None,
    idiolect: dict[str, Any] | None = None,
    target_text: str | None = None,
) -> dict[str, Any]:
    """Read inputs, extract directional readings, return matching
    interpretations + per-signal evidence."""
    readings: dict[str, str] = {
        "smoothing": _read_smoothing_level(variance),
        "voice_drift": _read_voice_drift_level(voice_distance),
        "gi_decision": _read_gi_decision(gi),
        "pos_bigram_kl": _read_pos_bigram_kl(variance),
        "aic_density": _read_aic_density(aic),
        "paragraph": _read_paragraph_band(paragraph),
        "discourse": _read_discourse_band(discourse),
        "agency": _read_agency_band(agency),
        "idiolect_survival": _read_idiolect_survival(idiolect, target_text),
    }

    matches: list[dict[str, Any]] = []
    for pattern in DISAGREEMENT_PATTERNS:
        ok, evidence = _pattern_matches(pattern, readings)
        if ok:
            matches.append({
                "name": pattern["name"],
                "interpretation": pattern["interpretation"],
                "supporting_signals": evidence,
            })

    n_known_readings = sum(
        1 for r in readings.values() if r != "unknown"
    )

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "readings": readings,
        "n_known_readings": n_known_readings,
        "matched_interpretations": matches,
        "n_matches": len(matches),
        "inputs_used": {
            "variance": variance is not None,
            "voice_distance": voice_distance is not None,
            "gi": gi is not None,
            "paragraph": paragraph is not None,
            "discourse": discourse is not None,
            "agency": agency is not None,
            "aic": aic is not None,
            "idiolect": idiolect is not None,
            "target_text": target_text is not None,
        },
    }


# --- Markdown rendering ----------------------------------------


def _claim_license(report: dict[str, Any]) -> ClaimLicense:
    n_matches = report.get("n_matches", 0)
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "A meta-interpretation of cross-surface disagreement "
            "patterns. For each disagreement pattern that matches "
            "the observed surface readings, the resolver names "
            "the interpretation (\"high smoothing + low voice "
            "drift → edited authorial voice\") and lists the "
            "supporting evidence."
        ),
        does_not_license=(
            "A verdict on which interpretation is correct. "
            "Multiple interpretations are typically jointly "
            "compatible — the framework declines to pick one. "
            "This is the meta-version of the confounder audit's "
            "stance: surface the differential, not the verdict. "
            "The interpretations are descriptive cues, not "
            "calibrated probabilities."
        ),
        comparison_set={
            "n_matched_interpretations": n_matches,
            "n_known_readings": report.get("n_known_readings", 0),
            "inputs_used": ", ".join(
                k for k, v in report.get("inputs_used", {}).items()
                if v
            ) or "(none)",
        },
        additional_caveats=[
            "The pattern catalog is heuristic and curated, not "
            "labeled-corpus-validated. Treat each match as a "
            "design hypothesis the maintainer found useful, not "
            "as a calibrated diagnostic.",
            "Surfaces marked `unknown` (no input supplied) only "
            "match the wildcard `*`; patterns that depend on a "
            "missing surface won't fire. The resolver degrades "
            "gracefully but reports fewer interpretations on "
            "thinner inputs.",
            "Multiple matches are common and expected. The "
            "framework refuses to rank them as if one were the "
            "answer.",
        ],
    )
    # B.3: append state-routed caveats when the operator supplied
    # --ai-status. No-op when ai_status is absent.
    return with_state_caveats(
        lic, target_ai_status=report.get("ai_status"),
    )


def _claim_license_block(report: dict[str, Any]) -> str:
    return _claim_license(report).render_block().rstrip()


def build_audit_payload(
    report: dict[str, Any],
    *,
    target_path: Any,
) -> dict[str, Any]:
    """Wrap surface_disagreement_resolver report in the
    schema_version 1.0 envelope per
    ``internal/SPEC_output_schema_unification.md``.
    """
    metadata_keys = {"task_surface", "tool", "version"}
    results_payload = {
        k: v for k, v in report.items() if k not in metadata_keys
    }
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=0,
        baseline=None,
        results=results_payload,
        claim_license=_claim_license(report),
        ai_status=report.get("ai_status"),
    )


def render_report(report: dict[str, Any]) -> str:
    readings = report.get("readings", {})
    matches = report.get("matched_interpretations", [])
    inputs = report.get("inputs_used", {})

    lines: list[str] = [
        "# Surface-disagreement resolver",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Surface readings supplied:** "
        f"{report.get('n_known_readings', 0)} / "
        f"{len(readings)}",
        "",
    ]

    inputs_list = [k for k, v in inputs.items() if v]
    if inputs_list:
        lines.append(f"**Inputs:** {', '.join(inputs_list)}")
    lines.append("")

    lines.append("## Surface readings")
    lines.append("")
    lines.append("| surface | reading |")
    lines.append("|---|---|")
    for k, v in readings.items():
        lines.append(f"| {k} | `{v}` |")
    lines.append("")

    if matches:
        lines.append(
            f"## Matched interpretations ({len(matches)})"
        )
        lines.append("")
        lines.append(
            "Each match is a disagreement pattern whose expected "
            "signal directions match the observed readings. "
            "**Multiple matches are expected** — the framework "
            "refuses to rank them as if one were the answer."
        )
        lines.append("")
        for m in matches:
            lines.append(f"### `{m['name']}`")
            lines.append("")
            lines.append(m["interpretation"])
            lines.append("")
            lines.append("**Supporting signals:**")
            for ev in m["supporting_signals"]:
                lines.append(f"- {ev}")
            lines.append("")
    else:
        lines.append("## Matched interpretations")
        lines.append("")
        lines.append(
            "_No patterns matched. This typically means too few "
            "surfaces were supplied, or the readings don't fit "
            "any of the curated disagreement patterns. Read the "
            "Surface readings table above and consult each "
            "surface's own claim-license block._"
        )
        lines.append("")

    lines.append(_claim_license_block(report))
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- CLI -------------------------------------------------------


def _read_json_or_none(path: str | None) -> dict[str, Any] | None:
    """Hardened JSON loader (1.34.2 conventions)."""
    if path is None:
        return None
    if not path:
        raise ValueError(
            "Empty path supplied to a JSON input flag; pass a real "
            "path or omit the flag entirely."
        )
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
        prog="surface_disagreement_resolver.py",
        description=(
            "Cross-surface interpretation meta-layer. Reads any "
            "subset of audit JSONs and surfaces interpretable "
            "disagreement patterns (high smoothing + low voice "
            "drift = edited authorial voice; low smoothing + high "
            "voice drift = register shift / collaboration / "
            "imitation; etc.). Returns the differential, not a "
            "verdict."
        ),
    )
    p.add_argument("--variance-json")
    p.add_argument("--voice-distance-json")
    p.add_argument("--gi-json")
    p.add_argument("--paragraph-json")
    p.add_argument("--discourse-json")
    p.add_argument("--agency-json")
    p.add_argument("--aic-json")
    p.add_argument("--idiolect-json")
    p.add_argument(
        "--target-text",
        help="Path to target text. Required alongside "
             "--idiolect-json for preservation-list survival.",
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--out")
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
    try:
        inputs = {
            "variance": _read_json_or_none(args.variance_json),
            "voice_distance": _read_json_or_none(args.voice_distance_json),
            "gi": _read_json_or_none(args.gi_json),
            "paragraph": _read_json_or_none(args.paragraph_json),
            "discourse": _read_json_or_none(args.discourse_json),
            "agency": _read_json_or_none(args.agency_json),
            "aic": _read_json_or_none(args.aic_json),
            "idiolect": _read_json_or_none(args.idiolect_json),
        }
    except (FileNotFoundError, ValueError) as exc:
        sys.stderr.write(f"Input error: {exc}\n")
        return 2

    target_text: str | None = None
    if args.target_text:
        tp = Path(args.target_text).expanduser()
        if not tp.is_file():
            sys.stderr.write(
                f"--target-text not found: {args.target_text}\n"
            )
            return 2
        target_text = tp.read_text(encoding="utf-8", errors="ignore")

    report = resolve(target_text=target_text, **inputs)
    # B.3: surface --ai-status into the report dict so
    # _claim_license_block can route per state.
    if args.ai_status:
        report["ai_status"] = args.ai_status

    if args.json:
        target_path = (
            args.variance_json or args.voice_distance_json
            or args.gi_json
        )
        payload = build_audit_payload(report, target_path=target_path)
        out = json.dumps(payload, indent=2, default=str)
    else:
        out = render_report(report)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
