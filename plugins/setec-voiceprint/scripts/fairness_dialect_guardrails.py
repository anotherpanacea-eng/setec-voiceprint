#!/usr/bin/env python3
"""fairness_dialect_guardrails.py — linguistic-background caution
surface (paired-release schedule Release 9, Trustworthiness Tier 1).

The ESL ratchet exists in `manifest_validator.py` (a warning when
voice-coherence baselines mix native and non-native English
prose). The broader linguistic-background caution layer has not
been visible at the report level. This module is that layer.

The AI-detection field has a documented history of producing
unfair false positives against nonnative English writers (Liang
et al., Patterns 2023, found 61% FPR on TOEFL essays across
seven AI-prose detectors). Even when this framework is not an AI
detector, users may try to use it that way — and the framework's
distributional signals are known to fire similarly on AI-smoothed
prose AND on prose written by writers whose first language is
not English. The guardrail's job is to make those cases visible
at report level and to refuse evaluative or disciplinary use
when the validation set does not include comparable language
backgrounds.

Eight linguistic-background conditions tracked:

  1. **nonnative_english** — declared (writer's first language is
     not English).
  2. **code_switching** — detected (presence of non-ASCII
     letters / non-English words mixed into the prose) or
     declared.
  3. **dialect_features** — declared (AAVE, Caribbean English,
     Indian English, Scottish English, etc.). Detection-only is
     not attempted; dialect identification is contested and the
     framework refuses to do it at the report level.
  4. **translation_influenced** — declared (prose translated
     from another language, or written by an L2 speaker
     consciously rendering thought in English).
  5. **speech_to_text** — declared (prose generated from a
     transcript of spoken input — automated transcription,
     dictation, podcast scripts).
  6. **neurodivergent_patterns** — declared. The framework
     refuses to detect neurodivergence from prose; this flag
     exists for the writer to declare it themselves.
  7. **educational_genre** — declared (TOEFL prep, ELL
     coursework, ESL textbook prose, scaffolded writing
     assignments). Pre-validated genre categories that
     systematically read as low-variance in stylometric
     space.
  8. **institutional_template** — declared (legal briefs,
     policy memos, grant proposals, corporate boilerplate,
     governmental forms). Template-bound prose has known
     low-variance signatures unrelated to AI smoothing.

For each condition, the guardrail emits a caution flag and (if
the framework's evidentiary-conditions gate is downstream) a
posture-cap recommendation. The check refuses evaluative use
when (a) any condition is present AND (b) the validation set
does not include comparable language backgrounds.

Detection vs. declaration:

  - **code_switching** and **nonnative_english** can be HEURISTIC
    (regex / declared-language-status fields).
  - **dialect_features**, **translation_influenced**,
    **speech_to_text**, **neurodivergent_patterns**,
    **educational_genre**, **institutional_template** are
    DECLARATION-ONLY. The framework refuses to infer them.

Usage:

    python3 scripts/fairness_dialect_guardrails.py \\
        --target target.md \\
        --declare nonnative_english \\
        --declare institutional_template \\
        --baseline-language-backgrounds native \\
        --json --out caution.json

    # With a validation manifest declaring baseline backgrounds:
    python3 scripts/fairness_dialect_guardrails.py \\
        --target target.md \\
        --manifest manifest.tsv

task_surface: validation. The output is a caution report, not a
classifier. Conditions don't determine truth; they determine
which uses of the framework's results are fair.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore


TASK_SURFACE = "validation"
TOOL_NAME = "fairness_dialect_guardrails"
SCRIPT_VERSION = "1.0"


# ---------- Conditions registry ----------


_CONDITIONS: tuple[tuple[str, str, bool], ...] = (
    # (key, description, declaration_only)
    (
        "nonnative_english",
        "Writer's first language is not English. Distributional "
        "signals (low burstiness, low entropy, formulaic POS "
        "bigrams) are known to fire similarly on L2-English "
        "prose and on AI-smoothed prose. Treat the framework's "
        "smoothing-diagnosis output as REVISION-ONLY unless the "
        "validation set explicitly includes comparable language "
        "backgrounds.",
        False,
    ),
    (
        "code_switching",
        "Prose mixes English with another language. Detected "
        "heuristically (non-ASCII Latin-extended characters, "
        "high-incidence non-English words) or declared. The "
        "framework's lexical features are not designed for "
        "code-switched prose; results are uninterpretable in "
        "the absence of a code-switching baseline.",
        False,
    ),
    (
        "dialect_features",
        "Prose uses a non-standard English dialect (AAVE, "
        "Caribbean English, Indian English, Scottish English, "
        "etc.). Declaration-only — the framework refuses to "
        "identify dialect from prose. If the validation set is "
        "Standard American / British English, results do not "
        "transfer.",
        True,
    ),
    (
        "translation_influenced",
        "Prose was translated from another language, or written "
        "by an L2 speaker consciously rendering thought from "
        "another language into English. Declaration-only. "
        "Translation-influenced prose has known distributional "
        "signatures that overlap with AI-smoothed prose (low "
        "lexical variability, formulaic syntax).",
        True,
    ),
    (
        "speech_to_text",
        "Prose was generated from a transcript of spoken input "
        "(automated transcription, dictation, podcast script). "
        "Declaration-only. Speech-to-text prose has different "
        "rhythmic and structural signatures than written prose; "
        "treating it as a writing sample for stylometric "
        "comparison is methodologically inappropriate.",
        True,
    ),
    (
        "neurodivergent_patterns",
        "Writer has declared a neurodivergent profile that may "
        "produce non-standard punctuation / structure / "
        "discourse-marker patterns. Declaration-only. The "
        "framework refuses to detect neurodivergence from prose.",
        True,
    ),
    (
        "educational_genre",
        "Prose belongs to a pre-validated educational genre "
        "(TOEFL prep, ELL coursework, ESL textbook, scaffolded "
        "writing assignments). These genres systematically read "
        "as low-variance in stylometric space; results do not "
        "transfer to general-prose claims.",
        True,
    ),
    (
        "institutional_template",
        "Prose follows an institutional template (legal briefs, "
        "policy memos, grant proposals, corporate boilerplate, "
        "governmental forms). Template-bound prose has known "
        "low-variance signatures unrelated to AI smoothing.",
        True,
    ),
)

CONDITION_KEYS: tuple[str, ...] = tuple(c[0] for c in _CONDITIONS)
DECLARATION_ONLY_CONDITIONS: frozenset[str] = frozenset(
    k for k, _, decl_only in _CONDITIONS if decl_only
)


# ---------- Heuristic detection ----------


# Latin-extended / non-ASCII letters that signal possible
# code-switching when present beyond punctuation thresholds.
# Excludes common typographic characters (em-dash, smart quotes).
_LATIN_EXTENDED_PUNCT_OK = set("—–‘’“”…")


def _is_non_english_letter(ch: str) -> bool:
    """Return True for letters that are non-ASCII and not common
    typographic punctuation. Heuristic — flags accented Latin
    letters (é, ü, ñ, ç, å) and non-Latin scripts (Cyrillic,
    CJK, etc.)."""
    if not ch.isalpha():
        return False
    if ord(ch) < 128:
        return False
    if ch in _LATIN_EXTENDED_PUNCT_OK:
        return False
    return True


def detect_code_switching(
    text: str, *, min_letters: int = 5, ratio_threshold: float = 0.005,
) -> tuple[bool, dict[str, Any]]:
    """Heuristic: code-switching is flagged when at least
    ``min_letters`` non-ASCII alphabetic characters are present
    AND they comprise at least ``ratio_threshold`` of all letters.

    Returns ``(detected, evidence)`` where evidence enumerates the
    sample of non-English letters found.
    """
    n_total = sum(1 for ch in text if ch.isalpha())
    if n_total == 0:
        return False, {"n_total_letters": 0, "n_non_english": 0}
    non_english = [ch for ch in text if _is_non_english_letter(ch)]
    n_non = len(non_english)
    sample = sorted(set(non_english))[:20]
    detected = (
        n_non >= min_letters
        and (n_non / n_total) >= ratio_threshold
    )
    return detected, {
        "n_total_letters": n_total,
        "n_non_english": n_non,
        "ratio": n_non / n_total if n_total else 0.0,
        "sample": sample,
    }


# ---------- Manifest reading (for baseline backgrounds) ----------


def _entry_uses_baseline(entry: dict[str, Any]) -> bool:
    """Return True iff the entry's ``use`` field marks it as a
    baseline. Accepts scalar (``"baseline"``) or list / set values
    (e.g., ``["baseline", "target"]``) — some manifest writers
    emit a list when an entry serves multiple roles. Both forms
    must be recognized."""
    use = entry.get("use")
    if use == "baseline":
        return True
    if isinstance(use, (list, tuple, set)):
        return "baseline" in use
    return False


def _read_manifest_language_backgrounds(
    manifest_path: Path,
) -> dict[str, int]:
    """Read a manifest and return counts of language_status
    values for entries whose ``use`` is (or contains) ``baseline``.

    Accepts three on-disk shapes:
      - ``.json`` — a JSON list of entry dicts, or an object with
        an ``entries`` key.
      - ``.jsonl`` — one JSON entry per line (the framework's
        canonical streaming-friendly manifest format).
      - any other extension — TSV with a header row.

    The ``use`` field may be a scalar (``"baseline"``) or a list
    / set value containing ``"baseline"``; both forms count.
    """
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"Manifest not found: {manifest_path}"
        )
    text = manifest_path.read_text(encoding="utf-8", errors="ignore")
    counts: dict[str, int] = {}
    suffix = manifest_path.suffix.lower()

    def _record(entry: dict[str, Any]) -> None:
        if not _entry_uses_baseline(entry):
            return
        status = entry.get("language_status") or "unknown"
        counts[status] = counts.get(status, 0) + 1

    if suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Manifest JSON malformed: {exc}"
            ) from exc
        entries = (
            data if isinstance(data, list)
            else data.get("entries", [])
        )
        for entry in entries:
            if isinstance(entry, dict):
                _record(entry)
        return counts

    if suffix == ".jsonl":
        # JSON Lines: one entry per non-empty line.
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Manifest JSONL line {lineno} malformed: {exc}"
                ) from exc
            if isinstance(entry, dict):
                _record(entry)
        return counts

    # TSV (default).
    lines = text.splitlines()
    if not lines:
        return counts
    header = lines[0].split("\t")
    use_idx = header.index("use") if "use" in header else None
    lang_idx = (
        header.index("language_status")
        if "language_status" in header else None
    )
    if use_idx is None:
        return counts
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) <= use_idx:
            continue
        # TSV doesn't naturally express list-valued cells; we
        # accept either bare "baseline" or comma-separated values
        # like "baseline,target" so the same `use`-list semantics
        # work for TSV and JSON manifests.
        use_cell = cols[use_idx]
        use_values = (
            {v.strip() for v in use_cell.split(",")}
            if "," in use_cell else {use_cell}
        )
        if "baseline" not in use_values:
            continue
        status = (
            cols[lang_idx]
            if lang_idx is not None and len(cols) > lang_idx
            else "unknown"
        )
        counts[status] = counts.get(status, 0) + 1
    return counts


# ---------- Baseline-coverage compatibility ----------


_BACKGROUND_TO_BASELINE_REQUIREMENT = {
    "nonnative_english": (
        "non_native_advanced", "non_native_intermediate", "learner",
    ),
    "code_switching": ("non_native_advanced", "non_native_intermediate"),
    "translation_influenced": (
        "non_native_advanced", "non_native_intermediate",
    ),
    "educational_genre": (
        "non_native_advanced", "non_native_intermediate", "learner",
    ),
    # Other conditions don't have a clean language_status mapping;
    # the framework's manifest doesn't track dialect, speech-to-text,
    # neurodivergent, or institutional-template backgrounds.
}


def baseline_covers_condition(
    condition: str,
    baseline_backgrounds: dict[str, int],
) -> bool:
    """Return True if the baseline includes entries with a
    language background suitable for the given condition.

    For conditions without a manifest mapping (dialect, S2T,
    neurodivergent, institutional_template), returns False —
    the framework can't verify coverage from the manifest, so
    the conservative answer is no.
    """
    required = _BACKGROUND_TO_BASELINE_REQUIREMENT.get(condition)
    if not required:
        return False
    return any(
        baseline_backgrounds.get(req, 0) > 0 for req in required
    )


# ---------- Caution report ----------


@dataclass
class ConditionFlag:
    name: str
    description: str
    source: str  # "declared" / "detected" / "absent"
    baseline_covered: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)
    posture_cap: str = "revision_only"  # default for any active condition


def _build_condition_flags(
    *,
    declared: set[str],
    text: str | None,
    baseline_backgrounds: dict[str, int],
) -> dict[str, ConditionFlag]:
    """Build per-condition flags. `declared` is the set of
    user-declared condition keys. `text` is optional; when
    supplied, code_switching is heuristically detected."""
    flags: dict[str, ConditionFlag] = {}

    for key, description, decl_only in _CONDITIONS:
        evidence: dict[str, Any] = {}
        source = "absent"
        if key in declared:
            source = "declared"
        elif key == "code_switching" and text is not None:
            detected, evid = detect_code_switching(text)
            if detected:
                source = "detected"
                evidence = evid
        if source == "absent":
            continue
        flags[key] = ConditionFlag(
            name=key,
            description=description,
            source=source,
            baseline_covered=baseline_covers_condition(
                key, baseline_backgrounds,
            ),
            evidence=evidence,
        )
    return flags


def _overall_recommendation(
    flags: dict[str, ConditionFlag],
) -> dict[str, Any]:
    """Aggregate per-condition flags into an overall posture
    recommendation.

    Logic:
      - No flags → no caution; no posture cap.
      - All flags have baseline coverage → caution noted, posture
        not capped (writer should still see the caution).
      - Any flag without baseline coverage → cap at
        ``revision_only``; refuse evaluative / disciplinary use.
    """
    if not flags:
        return {
            "overall": "no_conditions_flagged",
            "posture_cap": None,
            "refuses_evaluative_use": False,
            "n_flags": 0,
            "n_uncovered": 0,
        }

    uncovered = [
        f for f in flags.values() if not f.baseline_covered
    ]
    if not uncovered:
        return {
            "overall": "conditions_present_baseline_matched",
            "posture_cap": "exploratory_comparison",
            "refuses_evaluative_use": False,
            "n_flags": len(flags),
            "n_uncovered": 0,
            "covered_conditions": [f.name for f in flags.values()],
        }
    return {
        "overall": "conditions_present_baseline_unmatched",
        "posture_cap": "revision_only",
        "refuses_evaluative_use": True,
        "n_flags": len(flags),
        "n_uncovered": len(uncovered),
        "uncovered_conditions": [f.name for f in uncovered],
    }


def build_caution_report(
    *,
    target_text: str | None,
    declared_conditions: list[str],
    baseline_backgrounds: dict[str, int],
    declared_use_case: str | None = None,
) -> dict[str, Any]:
    """Build the full caution report.

    `target_text` is optional; when supplied, code_switching is
    detected heuristically. `declared_conditions` is a list of
    condition keys the user (or upstream tooling) has declared.
    `baseline_backgrounds` is a count map from
    `manifest_validator.ALLOWED_LANGUAGE_STATUS` values to
    counts in the validation baseline.
    """
    declared_set = {
        d for d in declared_conditions if d in CONDITION_KEYS
    }
    unknown_declared = [
        d for d in declared_conditions if d not in CONDITION_KEYS
    ]

    flags = _build_condition_flags(
        declared=declared_set,
        text=target_text,
        baseline_backgrounds=baseline_backgrounds,
    )
    recommendation = _overall_recommendation(flags)

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "declared_conditions": sorted(declared_set),
        "unknown_declared_conditions": unknown_declared,
        "baseline_backgrounds": dict(baseline_backgrounds),
        "declared_use_case": declared_use_case,
        "condition_flags": {
            key: {
                "name": flag.name,
                "description": flag.description,
                "source": flag.source,
                "baseline_covered": flag.baseline_covered,
                "evidence": flag.evidence,
                "posture_cap": flag.posture_cap,
            }
            for key, flag in flags.items()
        },
        "recommendation": recommendation,
        "claim_license": _claim_license_dict(
            n_flags=len(flags),
            recommendation=recommendation,
        ),
    }


def _claim_license_dict(
    *,
    n_flags: int,
    recommendation: dict[str, Any],
) -> dict[str, Any]:
    refuses = recommendation.get("refuses_evaluative_use", False)
    cap = recommendation.get("posture_cap")
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "A linguistic-background caution report. For each "
            "of eight conditions (nonnative English, code-"
            "switching, dialect features, translation-influenced "
            "prose, speech-to-text, neurodivergent patterns, "
            "educational genre, institutional template), the "
            "report names whether the condition is present "
            "(declared by user or detected heuristically), "
            "whether the validation baseline includes comparable "
            "language backgrounds, and what posture cap follows."
        ),
        does_not_license=(
            "A judgment about a writer's competence, identity, "
            "or status. The conditions exist to PROTECT writers "
            "from inappropriate framework use. The framework "
            "REFUSES to detect dialect, neurodivergence, "
            "translation-influence, speech-to-text origin, "
            "educational genre, or institutional-template "
            "context from prose. Detection is heuristic only "
            "for code-switching, and is conservative; "
            "user declaration is the load-bearing input. "
            "When a condition is present and the baseline does "
            "NOT include comparable backgrounds, the framework "
            "refuses evaluative or disciplinary use of its "
            "results — this is the load-bearing fairness "
            "guarantee, not a soft warning."
        ),
        comparison_set={
            "n_conditions_flagged": n_flags,
            "posture_cap": cap or "(none)",
            "refuses_evaluative_use": refuses,
            "overall": recommendation.get("overall"),
        },
        additional_caveats=[
            "Six of eight conditions are declaration-only "
            "(dialect, translation-influenced, speech-to-text, "
            "neurodivergent_patterns, educational_genre, "
            "institutional_template). The framework will not "
            "infer them. If the user does not declare them, "
            "the report cannot flag them.",
            "Code-switching detection is HEURISTIC. It flags "
            "non-ASCII Latin-extended letters and non-Latin "
            "scripts above a small threshold. It produces false "
            "positives on accented loanwords (\"naïve\", "
            "\"café\") in otherwise-Standard English; declared "
            "conditions are more reliable than heuristic ones.",
            "The framework's manifest schema tracks "
            "language_status (native / non_native_advanced / "
            "non_native_intermediate / learner / unknown). It "
            "does NOT track dialect, speech-to-text origin, "
            "neurodivergent profile, or institutional-template "
            "context. For those conditions, baseline coverage "
            "cannot be verified from the manifest — the "
            "conservative answer is `baseline_covered: false`.",
            "The fairness motivation is documented: AI-detection "
            "tools have produced 61% false-positive rates on "
            "TOEFL essays (Liang et al., Patterns 2023). Even "
            "when this framework is not an AI detector, users "
            "may apply it that way. The guardrail's job is to "
            "keep that misuse from masquerading as evidence.",
        ],
    )
    return {"rendered": lic.render_block().rstrip()}


# ---------- Markdown rendering ----------


_RECOMMENDATION_GLYPH = {
    "no_conditions_flagged": "✓",
    "conditions_present_baseline_matched": "·",
    "conditions_present_baseline_unmatched": "✗",
}


def render_report(report: dict[str, Any]) -> str:
    flags = report.get("condition_flags", {})
    rec = report.get("recommendation", {})
    overall = rec.get("overall", "unknown")
    glyph = _RECOMMENDATION_GLYPH.get(overall, "?")

    lines: list[str] = [
        "# Fairness / dialect / multilingual guardrails",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Overall:** `{overall}` {glyph}",
        f"**Posture cap:** "
        f"{rec.get('posture_cap') or '(none)'}",
        f"**Refuses evaluative / disciplinary use:** "
        f"{'**yes**' if rec.get('refuses_evaluative_use') else 'no'}",
        f"**Conditions flagged:** {rec.get('n_flags', 0)}",
        "",
    ]

    if flags:
        lines.append("## Flagged conditions")
        lines.append("")
        for key, info in flags.items():
            lines.append(f"### `{key}` — `{info.get('source')}`")
            lines.append("")
            lines.append(info.get("description", ""))
            lines.append("")
            lines.append(
                f"- **Baseline covered:** "
                f"{'yes' if info.get('baseline_covered') else 'no'}"
            )
            lines.append(
                f"- **Posture cap:** "
                f"{info.get('posture_cap', '(none)')}"
            )
            evidence = info.get("evidence", {})
            if evidence:
                lines.append("- **Evidence (heuristic):**")
                for k, v in evidence.items():
                    lines.append(f"  - `{k}`: {v}")
            lines.append("")

    backgrounds = report.get("baseline_backgrounds", {})
    if backgrounds:
        lines.append("## Baseline language backgrounds")
        lines.append("")
        for k, v in backgrounds.items():
            lines.append(f"- `{k}`: {v}")
        lines.append("")

    unknown = report.get("unknown_declared_conditions", [])
    if unknown:
        lines.append("## Unknown declared conditions")
        lines.append("")
        for k in unknown:
            lines.append(
                f"- `{k}` — not in the recognized condition set; "
                f"ignored."
            )
        lines.append("")

    license_block = report.get("claim_license", {}).get("rendered", "")
    if license_block:
        lines.append(license_block)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------- CLI ----------


def _read_target(path_str: str | None) -> str | None:
    if path_str is None:
        return None
    p = Path(path_str).expanduser()
    if not p.is_file():
        raise FileNotFoundError(
            f"--target file not found: {path_str}"
        )
    return p.read_text(encoding="utf-8", errors="ignore")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fairness_dialect_guardrails.py",
        description=(
            "Linguistic-background caution surface. Reports per-"
            "condition flags (nonnative English, code-switching, "
            "dialect, translation-influenced, speech-to-text, "
            "neurodivergent patterns, educational genre, "
            "institutional template), baseline coverage, and "
            "an overall posture-cap recommendation."
        ),
    )
    p.add_argument(
        "--target",
        help="Optional target text. When supplied, code-switching "
             "is detected heuristically.",
    )
    p.add_argument(
        "--declare", action="append", dest="declared",
        default=[],
        choices=list(CONDITION_KEYS),
        help="Declare a linguistic-background condition. Repeat "
             "for multiple. Required for any condition that is "
             "declaration-only (dialect, translation_influenced, "
             "speech_to_text, neurodivergent_patterns, "
             "educational_genre, institutional_template).",
    )
    p.add_argument(
        "--baseline-language-backgrounds", action="append",
        dest="baseline_backgrounds_raw", default=[],
        help="Declare baseline language backgrounds, format "
             "`status[:count]`, e.g., `native:30`. Repeat for "
             "multiple. Falls back to `--manifest` when "
             "supplied.",
    )
    p.add_argument(
        "--manifest",
        help="Optional manifest TSV/JSON to read baseline "
             "language_status counts from.",
    )
    p.add_argument(
        "--declared-use-case",
        choices=[
            "revision_only", "exploratory_comparison",
            "internal_triage", "research_grade_validation",
            "forensic_adjacent_nondispositive",
        ],
        help="Optional declared downstream use case (informational; "
             "the report does not promote based on this).",
    )
    p.add_argument("--json", action="store_true")
    p.add_argument("--out")
    return p


def _parse_baseline_backgrounds(raw: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in raw:
        if ":" in item:
            key, _, count = item.partition(":")
            try:
                out[key.strip()] = int(count.strip())
            except ValueError:
                out[key.strip()] = 1
        else:
            out[item.strip()] = out.get(item.strip(), 0) + 1
    return out


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        target_text = _read_target(args.target)
    except FileNotFoundError as exc:
        sys.stderr.write(f"Input error: {exc}\n")
        return 2

    baseline_backgrounds: dict[str, int] = {}
    if args.manifest:
        try:
            baseline_backgrounds = _read_manifest_language_backgrounds(
                Path(args.manifest).expanduser(),
            )
        except (FileNotFoundError, ValueError) as exc:
            sys.stderr.write(f"--manifest: {exc}\n")
            return 2
    if args.baseline_backgrounds_raw:
        # CLI declarations override / merge with manifest counts.
        cli_counts = _parse_baseline_backgrounds(
            args.baseline_backgrounds_raw
        )
        for k, v in cli_counts.items():
            baseline_backgrounds[k] = (
                baseline_backgrounds.get(k, 0) + v
            )

    report = build_caution_report(
        target_text=target_text,
        declared_conditions=args.declared,
        baseline_backgrounds=baseline_backgrounds,
        declared_use_case=args.declared_use_case,
    )

    if args.json:
        payload = build_audit_payload(report, target_path=None)
        out = json.dumps(payload, indent=2, default=str)
    else:
        out = render_report(report)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(out)
    return 0


def build_audit_payload(
    report: dict[str, Any],
    *,
    target_path: Any,
) -> dict[str, Any]:
    """Wrap fairness_dialect_guardrails report in the schema_version
    1.0 envelope per ``internal/SPEC_output_schema_unification.md``.
    """
    structured = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Fairness and dialect-condition guardrails: flags "
            "conditions (ESL / dialect / register / declared use "
            "case) that affect how SETEC's stylometric outputs "
            "should be interpreted, and the recommendations that "
            "follow from those conditions."
        ),
        does_not_license=(
            "A verdict on whether the conditions are correctly "
            "declared, or whether the framework's outputs are "
            "appropriate for the declared use case. The "
            "recommendations are conservative defaults; user "
            "judgment about the local context remains load-bearing."
        ),
        comparison_set={
            "declared_conditions": report.get("declared_conditions"),
            "declared_use_case": report.get("declared_use_case"),
            "n_flags": len(report.get("condition_flags") or {}),
        },
        additional_caveats=[
            "Condition flags are heuristic. The interaction matrix "
            "between conditions (e.g., ESL × dialect × forensic-"
            "adjacent use case) is conservative; treat the "
            "overall recommendation as a discipline cue.",
        ],
    )
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
        claim_license=structured,
    )


if __name__ == "__main__":
    sys.exit(main())
