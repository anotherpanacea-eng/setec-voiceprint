#!/usr/bin/env python3
"""evidentiary_conditions_gate.py — front-door evidentiary posture.

Trustworthiness Tier-1 build, paired-release schedule Release 6.
The framework's surfaces already carry per-output `claim_license`
blocks naming what the result entitles. The 1.30.x roadmap added
the differential-diagnosis layer (`confounder_audit.py`) for
"compatible-with-what" interpretation. What was still missing —
and is the load-bearing addition this module ships — is the
*single front-door label* that answers: **what use is this output
entitled for?**

The gate consumes whichever audit JSONs the user supplies, plus
any direct evidentiary parameters, and emits an **Evidentiary
Posture** label drawn from a fixed five-tier ladder:

  - ``revision_only`` — input too short / baseline too small /
    register mismatched / contamination too high. Safe for the
    writer's own revision. Not safe for any other use.
  - ``exploratory_comparison`` — meaningful inputs but missing
    context. Useful for triangulating against other evidence;
    not on its own a claim.
  - ``internal_triage`` — sufficient evidence to flag work for
    closer review but not to publish or accuse.
  - ``research_grade_validation`` — labeled corpus, well-matched
    baseline, multiple corroborating audits, register match. Can
    support a publication-grade observation.
  - ``forensic_adjacent_nondispositive`` — strongest available
    posture short of forensic. Even at this level the framework
    REFUSES dispositive claims about authorship. Forensic-grade
    use requires human review, due process, and out-of-framework
    corroborating evidence.

The output is **not a numerical confidence score**. The framework
refuses to compress the evidentiary status into a number that can
be misused in disciplinary, accusatory, or legal contexts. The
posture label is qualitative, and the rationale enumerates which
evidence supports the level and which would raise it.

Inputs (all optional; the gate degrades gracefully):
  --variance-json, --voice-distance-json, --paragraph-json,
  --discourse-json, --agency-json, --punctuation-json,
  --stance-json, --function-grammar-json, --confounder-json,
  --gi-json (general imposters)
  --target-text          — target text path; for length / contamination
  --has-pre-edit         — flag declaring the user has the pre-edit version
  --has-known-author     — flag declaring author confirmed
  --use-case             — declared user purpose (used as posture cap)

The gate emits posture + rationale + per-indicator findings.
The rationale is human-readable — short paragraphs, not a
numeric breakdown — so a writer reading the report can see why
their question is or isn't entitled.
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
TOOL_NAME = "evidentiary_conditions_gate"
SCRIPT_VERSION = "1.0"


# --- Posture taxonomy ------------------------------------------

POSTURES: tuple[str, ...] = (
    "revision_only",
    "exploratory_comparison",
    "internal_triage",
    "research_grade_validation",
    "forensic_adjacent_nondispositive",
)

POSTURE_RANK: dict[str, int] = {p: i for i, p in enumerate(POSTURES)}

POSTURE_LABELS: dict[str, str] = {
    "revision_only": (
        "Revision-only — safe for the writer's own diagnostic "
        "revision; not safe for any other use."
    ),
    "exploratory_comparison": (
        "Exploratory comparison — useful for triangulating against "
        "other evidence; not on its own a claim."
    ),
    "internal_triage": (
        "Internal triage — sufficient evidence to flag work for "
        "closer review; not safe to publish or to make "
        "accusations."
    ),
    "research_grade_validation": (
        "Research-grade validation — can support a publication-"
        "grade observation with the comparison context disclosed."
    ),
    "forensic_adjacent_nondispositive": (
        "Forensic-adjacent (still non-dispositive) — strongest "
        "available posture; the framework still refuses "
        "dispositive authorship claims at any level."
    ),
}


# --- Evidence indicators ---------------------------------------


def _read_target_length(
    target_text: str | None,
    variance: dict[str, Any] | None,
    paragraph: dict[str, Any] | None,
) -> int:
    """Best available estimate of target word count."""
    if target_text:
        return len(target_text.split())
    for src in (variance, paragraph):
        if src:
            n = (
                (src.get("audit") or {}).get("summary", {}).get("n_words")
                or src.get("n_words")
            )
            if isinstance(n, int):
                return n
    return 0


def _read_baseline_size(
    voice_distance: dict[str, Any] | None,
    paragraph: dict[str, Any] | None,
) -> int:
    """Number of baseline files used in the comparison, if known."""
    candidates: list[int] = []
    if voice_distance:
        bs = voice_distance.get("baseline_summary") or {}
        n = bs.get("n_files")
        if isinstance(n, int):
            candidates.append(n)
    if paragraph:
        block = paragraph.get("baseline_block") or {}
        n = block.get("n_files")
        if isinstance(n, int):
            candidates.append(n)
    return max(candidates) if candidates else 0


def _read_register_match_strength(
    voice_distance: dict[str, Any] | None,
) -> str | None:
    if not voice_distance:
        return None
    rmatch = voice_distance.get("register_match") or {}
    match = rmatch.get("match") or {}
    return match.get("strength")


def _read_strip_ratio(
    variance: dict[str, Any] | None,
) -> float | None:
    if not variance:
        return None
    prep = variance.get("preprocessing") or {}
    if isinstance(prep, dict):
        ratio = prep.get("strip_ratio")
        if isinstance(ratio, (int, float)):
            return float(ratio)
    return None


def _read_impostor_pool_size(
    gi: dict[str, Any] | None,
) -> int:
    if not gi:
        return 0
    n = gi.get("n_impostors")
    return int(n) if isinstance(n, int) else 0


def _has_confounder_diagnosis(
    confounder: dict[str, Any] | None,
) -> bool:
    if not confounder:
        return False
    return bool(confounder.get("ranked_confounders"))


def _count_audit_inputs(*audits: dict[str, Any] | None) -> int:
    return sum(1 for a in audits if a is not None)


# --- Posture decision ------------------------------------------


def _cap_posture(
    current: str, cap: str,
) -> str:
    """Return the lower of `current` and `cap` on the rank ladder."""
    if POSTURE_RANK[cap] < POSTURE_RANK[current]:
        return cap
    return current


def evaluate_evidentiary_posture(
    *,
    target_length: int = 0,
    baseline_size: int = 0,
    register_match_strength: str | None = None,
    strip_ratio: float | None = None,
    impostor_pool_size: int = 0,
    n_audit_surfaces: int = 0,
    has_confounder_diagnosis: bool = False,
    has_pre_edit_version: bool = False,
    has_known_author: bool = False,
    declared_use_case: str | None = None,
) -> dict[str, Any]:
    """Map the indicator vector to an Evidentiary Posture label.

    The decision is rule-based and qualitative. Each rule either
    *caps* the posture at a level (for absent / weak evidence) or
    *promotes* it (for strong evidence). The final posture is the
    minimum across all caps and the highest-met promotion.
    """
    # Start from the *maximum* posture; rules cap it down.
    posture = "forensic_adjacent_nondispositive"
    findings: list[dict[str, Any]] = []

    # --- Caps (lower the posture) ------------------------------

    if target_length < 200:
        posture = _cap_posture(posture, "revision_only")
        findings.append({
            "indicator": "target_length",
            "value": target_length,
            "effect": "caps at revision_only",
            "reason": (
                f"Target is {target_length} words; below 200 words "
                "the band-call evidence is too noisy for any claim "
                "beyond writer's own revision."
            ),
        })
    elif target_length < 500:
        posture = _cap_posture(posture, "exploratory_comparison")
        findings.append({
            "indicator": "target_length",
            "value": target_length,
            "effect": "caps at exploratory_comparison",
            "reason": (
                f"Target is {target_length} words; sufficient for "
                "exploratory comparison but most signals' length-"
                "floor reliability requires ≥ 500 words."
            ),
        })
    elif target_length < 2000:
        posture = _cap_posture(posture, "research_grade_validation")
        findings.append({
            "indicator": "target_length",
            "value": target_length,
            "effect": "caps at research_grade_validation",
            "reason": (
                f"Target is {target_length} words; substantial "
                "but below the 2,000-word floor commonly required "
                "for forensic-adjacent claims."
            ),
        })

    if baseline_size == 0:
        posture = _cap_posture(posture, "exploratory_comparison")
        findings.append({
            "indicator": "baseline_size",
            "value": 0,
            "effect": "caps at exploratory_comparison",
            "reason": (
                "No baseline supplied. Comparison-context evidence "
                "absent; the framework cannot confirm voice or "
                "register match."
            ),
        })
    elif baseline_size < 5:
        posture = _cap_posture(posture, "internal_triage")
        findings.append({
            "indicator": "baseline_size",
            "value": baseline_size,
            "effect": "caps at internal_triage",
            "reason": (
                f"Baseline has {baseline_size} files; the per-"
                "feature variance estimate is noisy with fewer "
                "than 5 files."
            ),
        })
    elif baseline_size < 20:
        posture = _cap_posture(posture, "research_grade_validation")
        findings.append({
            "indicator": "baseline_size",
            "value": baseline_size,
            "effect": "caps at research_grade_validation",
            "reason": (
                f"Baseline has {baseline_size} files; substantial "
                "but the impostor-pool floor for forensic-adjacent "
                "claims is typically 20+ files plus 5+ impostor "
                "writers."
            ),
        })

    if register_match_strength == "mismatch":
        posture = _cap_posture(posture, "revision_only")
        findings.append({
            "indicator": "register_match",
            "value": "mismatch",
            "effect": "caps at revision_only",
            "reason": (
                "Target register doesn't match baseline. Cross-"
                "register voice distance reads as voice drift "
                "even when the writer is unchanged; reading the "
                "comparison as evidence is unsafe."
            ),
        })
    elif register_match_strength == "weak":
        posture = _cap_posture(posture, "exploratory_comparison")
        findings.append({
            "indicator": "register_match",
            "value": "weak",
            "effect": "caps at exploratory_comparison",
            "reason": (
                "Register match is weak. Comparison context is "
                "underspecified for any claim beyond exploratory."
            ),
        })

    if strip_ratio is not None and strip_ratio > 0.30:
        posture = _cap_posture(posture, "exploratory_comparison")
        findings.append({
            "indicator": "strip_ratio",
            "value": strip_ratio,
            "effect": "caps at exploratory_comparison",
            "reason": (
                f"{strip_ratio:.0%} of the target tokens were "
                "stripped as non-prose. KL / Delta readings on the "
                "remainder are not representative of the writer's "
                "habits at the document level."
            ),
        })

    if impostor_pool_size > 0 and impostor_pool_size < 5:
        posture = _cap_posture(posture, "internal_triage")
        findings.append({
            "indicator": "impostor_pool_size",
            "value": impostor_pool_size,
            "effect": "caps at internal_triage",
            "reason": (
                f"Impostor pool has {impostor_pool_size} writers; "
                "the General Imposters method's floor is 5 "
                "writers, below which the bootstrap proportion is "
                "noisy."
            ),
        })

    # --- Promotion gates: required for higher postures -----------

    # Research-grade requires a confounder diagnosis OR multiple
    # corroborating audit surfaces (3+).
    if (
        POSTURE_RANK[posture] >= POSTURE_RANK["research_grade_validation"]
        and not has_confounder_diagnosis
        and n_audit_surfaces < 3
    ):
        posture = _cap_posture(posture, "internal_triage")
        findings.append({
            "indicator": "evidence_breadth",
            "value": (
                f"{n_audit_surfaces} surfaces, "
                f"confounder={has_confounder_diagnosis}"
            ),
            "effect": "caps at internal_triage",
            "reason": (
                "Research-grade posture requires either a confounder "
                "audit (Layer D differential diagnosis) OR at least "
                "3 corroborating audit surfaces. Without those the "
                "single-surface call can't be triangulated."
            ),
        })

    # Forensic-adjacent requires a pre-edit version OR known author
    # AND a confounder diagnosis AND ≥ 5 surfaces.
    if (
        POSTURE_RANK[posture] >= POSTURE_RANK["forensic_adjacent_nondispositive"]
        and not (
            (has_pre_edit_version or has_known_author)
            and has_confounder_diagnosis
            and n_audit_surfaces >= 5
        )
    ):
        posture = _cap_posture(posture, "research_grade_validation")
        findings.append({
            "indicator": "forensic_prerequisites",
            "value": (
                f"pre_edit={has_pre_edit_version}, "
                f"known_author={has_known_author}, "
                f"confounder={has_confounder_diagnosis}, "
                f"surfaces={n_audit_surfaces}"
            ),
            "effect": "caps at research_grade_validation",
            "reason": (
                "Forensic-adjacent posture requires (a) a pre-edit "
                "version OR known author, AND (b) a confounder "
                "diagnosis, AND (c) at least 5 corroborating audit "
                "surfaces. Even at this posture the framework "
                "REFUSES dispositive authorship claims; forensic "
                "use requires human review and out-of-framework "
                "corroboration."
            ),
        })

    # User-declared use case caps the posture: a user who declares
    # `revision_only` shouldn't have the gate report something
    # higher even if the evidence supports it.
    if declared_use_case and declared_use_case in POSTURE_RANK:
        posture = _cap_posture(posture, declared_use_case)

    return {
        "posture": posture,
        "posture_label": POSTURE_LABELS[posture],
        "findings": findings,
        "indicators": {
            "target_length": target_length,
            "baseline_size": baseline_size,
            "register_match_strength": register_match_strength,
            "strip_ratio": strip_ratio,
            "impostor_pool_size": impostor_pool_size,
            "n_audit_surfaces": n_audit_surfaces,
            "has_confounder_diagnosis": has_confounder_diagnosis,
            "has_pre_edit_version": has_pre_edit_version,
            "has_known_author": has_known_author,
            "declared_use_case": declared_use_case,
        },
    }


# --- Top-level entry point -------------------------------------


def gate(
    *,
    variance: dict[str, Any] | None = None,
    voice_distance: dict[str, Any] | None = None,
    paragraph: dict[str, Any] | None = None,
    discourse: dict[str, Any] | None = None,
    agency: dict[str, Any] | None = None,
    punctuation: dict[str, Any] | None = None,
    stance: dict[str, Any] | None = None,
    function_grammar: dict[str, Any] | None = None,
    confounder: dict[str, Any] | None = None,
    gi: dict[str, Any] | None = None,
    target_text: str | None = None,
    has_pre_edit_version: bool = False,
    has_known_author: bool = False,
    declared_use_case: str | None = None,
) -> dict[str, Any]:
    """Read inputs, evaluate posture, return structured report."""
    target_length = _read_target_length(target_text, variance, paragraph)
    baseline_size = _read_baseline_size(voice_distance, paragraph)
    register_match_strength = _read_register_match_strength(voice_distance)
    strip_ratio = _read_strip_ratio(variance)
    impostor_pool_size = _read_impostor_pool_size(gi)
    has_conf = _has_confounder_diagnosis(confounder)
    n_surfaces = _count_audit_inputs(
        variance, voice_distance, paragraph, discourse, agency,
        punctuation, stance, function_grammar,
    )

    posture_result = evaluate_evidentiary_posture(
        target_length=target_length,
        baseline_size=baseline_size,
        register_match_strength=register_match_strength,
        strip_ratio=strip_ratio,
        impostor_pool_size=impostor_pool_size,
        n_audit_surfaces=n_surfaces,
        has_confounder_diagnosis=has_conf,
        has_pre_edit_version=has_pre_edit_version,
        has_known_author=has_known_author,
        declared_use_case=declared_use_case,
    )

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        **posture_result,
        "inputs_used": {
            "variance": variance is not None,
            "voice_distance": voice_distance is not None,
            "paragraph": paragraph is not None,
            "discourse": discourse is not None,
            "agency": agency is not None,
            "punctuation": punctuation is not None,
            "stance": stance is not None,
            "function_grammar": function_grammar is not None,
            "confounder": confounder is not None,
            "gi": gi is not None,
            "target_text": target_text is not None,
        },
    }


# --- Markdown rendering ----------------------------------------


def _claim_license_block(report: dict[str, Any]) -> str:
    posture = report.get("posture", "unknown")
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "An *Evidentiary Posture* label drawn from a fixed "
            "five-tier ladder: revision_only, exploratory_"
            "comparison, internal_triage, research_grade_"
            "validation, forensic_adjacent_nondispositive. The "
            "label answers what use the framework's outputs are "
            "entitled for, given the evidence supplied."
        ),
        does_not_license=(
            "A numerical confidence score. The framework refuses "
            "to compress evidentiary status into a number that "
            "could be used in disciplinary, accusatory, or legal "
            "contexts. The label is qualitative; the rationale "
            "enumerates which evidence supports the level. Even "
            "the highest posture (forensic_adjacent_nondispositive) "
            "REFUSES dispositive authorship claims; forensic-grade "
            "use requires human review, due process, and out-of-"
            "framework corroborating evidence."
        ),
        comparison_set={
            "posture": posture,
            "n_findings": len(report.get("findings", [])),
            "inputs_used": ", ".join(
                k for k, v in report.get("inputs_used", {}).items()
                if v
            ) or "(none)",
        },
        additional_caveats=[
            "Posture rules are heuristic. The thresholds (200 / "
            "500 / 2,000 words; 5 / 20 baseline files; 5 impostor "
            "writers) are calibration-pending; treat the label as "
            "a discipline cue, not a calibrated risk metric.",
            "User-declared use case caps the posture but does not "
            "promote it. A user declaring `forensic_adjacent` can "
            "still receive a `revision_only` label if the evidence "
            "doesn't support the higher level.",
            "The gate degrades gracefully — fewer inputs lower the "
            "posture toward `revision_only` rather than committing "
            "to a higher level on insufficient evidence.",
        ],
    )
    return lic.render_block().rstrip()


def render_report(report: dict[str, Any]) -> str:
    posture = report.get("posture", "unknown")
    label = report.get("posture_label", "")
    findings = report.get("findings", [])
    inputs = report.get("inputs_used", {})

    lines: list[str] = [
        "# Evidentiary Conditions Gate",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        "",
        f"## Posture: `{posture}`",
        "",
        label,
        "",
    ]

    inputs_list = [k for k, v in inputs.items() if v]
    if inputs_list:
        lines.append(f"**Inputs supplied:** {', '.join(inputs_list)}")
    else:
        lines.append("**Inputs supplied:** _(none — posture is informationless)_")
    lines.append("")

    if findings:
        lines.append("## Findings")
        lines.append("")
        lines.append(
            "Each finding records an indicator that *capped* the "
            "posture below the maximum level. Together they "
            "explain why this run lands at this posture."
        )
        lines.append("")
        for f in findings:
            lines.append(
                f"- **{f['indicator']}** = `{f['value']}` "
                f"({f['effect']}): {f['reason']}"
            )
        lines.append("")
    else:
        lines.append("## Findings")
        lines.append("")
        lines.append(
            "_No caps fired — every indicator either supported the "
            "posture or was unevaluated. (This rarely happens "
            "outside trivial inputs; check that real audits were "
            "supplied.)_"
        )
        lines.append("")

    indicators = report.get("indicators", {})
    lines.append("## Indicator values")
    lines.append("")
    lines.append("| indicator | value |")
    lines.append("|---|---|")
    for k, v in indicators.items():
        lines.append(f"| {k} | `{v}` |")
    lines.append("")

    lines.append(_claim_license_block(report))
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- CLI -------------------------------------------------------


def _read_json_or_none(path: str | None) -> dict[str, Any] | None:
    """Load an optional input JSON. Same hardened semantics as
    confounder_audit (1.34.2): None for missing flag, raises for
    user-supplied bad path."""
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
        prog="evidentiary_conditions_gate.py",
        description=(
            "Front-door evidentiary-posture gate. Reads existing "
            "audit JSON outputs and emits a qualitative label "
            "(revision_only / exploratory_comparison / internal_"
            "triage / research_grade_validation / forensic_"
            "adjacent_nondispositive) answering what use the "
            "framework's outputs are entitled for. Not a "
            "numerical confidence score."
        ),
    )
    p.add_argument("--variance-json")
    p.add_argument("--voice-distance-json")
    p.add_argument("--paragraph-json")
    p.add_argument("--discourse-json")
    p.add_argument("--agency-json")
    p.add_argument("--punctuation-json")
    p.add_argument("--stance-json")
    p.add_argument("--function-grammar-json")
    p.add_argument("--confounder-json")
    p.add_argument("--gi-json")
    p.add_argument(
        "--target-text",
        help="Path to target text. Used for length and "
             "contamination-ratio indicators when other inputs "
             "don't supply them.",
    )
    p.add_argument(
        "--has-pre-edit", action="store_true",
        help="Declare that a pre-edit version of the target is "
             "available. Required for forensic_adjacent posture.",
    )
    p.add_argument(
        "--has-known-author", action="store_true",
        help="Declare that the author is known and confirmed. "
             "Alternative to --has-pre-edit for forensic_adjacent.",
    )
    p.add_argument(
        "--use-case",
        choices=POSTURES,
        help="Declared user purpose. Caps the posture (a user "
             "declaring `revision_only` won't get a higher label "
             "even if evidence supports it).",
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--out")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        inputs = {
            "variance": _read_json_or_none(args.variance_json),
            "voice_distance": _read_json_or_none(args.voice_distance_json),
            "paragraph": _read_json_or_none(args.paragraph_json),
            "discourse": _read_json_or_none(args.discourse_json),
            "agency": _read_json_or_none(args.agency_json),
            "punctuation": _read_json_or_none(args.punctuation_json),
            "stance": _read_json_or_none(args.stance_json),
            "function_grammar": _read_json_or_none(args.function_grammar_json),
            "confounder": _read_json_or_none(args.confounder_json),
            "gi": _read_json_or_none(args.gi_json),
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

    report = gate(
        target_text=target_text,
        has_pre_edit_version=args.has_pre_edit,
        has_known_author=args.has_known_author,
        declared_use_case=args.use_case,
        **inputs,
    )

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
