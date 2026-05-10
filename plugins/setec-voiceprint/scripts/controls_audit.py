#!/usr/bin/env python3
"""controls_audit.py — negative + positive controls for voice-distance comparison.

Trustworthiness Tier-2 build, paired-release schedule Release 6.
The voice_distance audit reports a single number (Burrows Delta,
function-word L1 distance, etc.) for one questioned text against
one baseline. The number is calibrated against what — exactly?
Pre-1.36.0 the answer was "the writer's typical within-baseline
distance," surfaced via the length-matched bootstrap as a
percentile. That helps but reads quietly: the writer sees a
percentile and is asked to interpret it.

This module ships the simpler interpretive frame writers and
non-technical readers actually want:

  > The questioned text is closer to a known-authentic control by
  > this writer (Δ X.X) than to a known-smoothed control (Δ Y.Y).

A **negative control** is a text known to be by the writer (e.g.,
an earlier untouched draft, a published essay). A **positive
control** is a text known to be smoothed / AI-edited / heavily
copyedited. The audit computes the function-word distance from
each — questioned, negative, positive — to the same baseline,
then reports the three distances side-by-side and tells the
reader whether the questioned text falls closer to one pole or
the other.

This is **not a classifier**. It's a comparison frame. The output
licenses statements about which pole the questioned text is closer
to under the supplied baseline; it does not license a verdict on
whether the questioned text is in fact authentic or smoothed —
that depends on whether the supplied controls actually represent
the categories they claim to. The writer has to vouch for the
controls; the audit measures the comparison they make possible.

Usage:

    python3 scripts/controls_audit.py \\
        --questioned drafts/current.md \\
        --negative-control drafts/2022-essay.md \\
        --positive-control drafts/known-smoothed.md \\
        --baseline-dir baselines/blog-essay/ \\
        --json

Either `--negative-control` or `--positive-control` may be
omitted; the audit will still report the questioned-vs-baseline
distance and any control distance supplied.
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
from preprocessing import strip_non_prose  # type: ignore
from stylometry_core import (  # type: ignore
    FUNCTION_WORDS,
    function_word_features,
    load_entries,
    read_text,
    word_tokens,
)
from voice_distance import (  # type: ignore
    _baseline_mean_function_word_vector,
    _function_word_vector,
    _manhattan_distance,
)

TASK_SURFACE = "voice_coherence"
TOOL_NAME = "controls_audit"
SCRIPT_VERSION = "1.0"


# --- Computation -----------------------------------------------


def _strip_and_tokenize(
    text: str, *,
    allow_non_prose: bool = False,
    strip_rules: str | list[str] | None = None,
    strip_aggressive: bool = False,
    strip_masking: str | list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Apply the framework's standard preprocessing pipeline."""
    cleaned, prep_meta = strip_non_prose(
        text, strip_rules,
        allow_non_prose=allow_non_prose,
        strip_aggressive=strip_aggressive,
        strip_masking=strip_masking,
    )
    return cleaned, prep_meta


def _function_word_distance_to_baseline(
    text: str, baseline_mean: dict[str, float],
) -> dict[str, Any]:
    """Compute L1 distance from text's function-word vector to
    the baseline mean. Returns ``{n_words, distance}``."""
    tokens = word_tokens(text)
    n_words = len(tokens)
    if n_words == 0:
        return {"n_words": 0, "distance": None}
    target_vec = _function_word_vector(text)
    return {
        "n_words": n_words,
        "distance": _manhattan_distance(target_vec, baseline_mean),
    }


def _classify_position(
    questioned_dist: float | None,
    negative_dist: float | None,
    positive_dist: float | None,
) -> dict[str, Any]:
    """Map the three distances to an interpretation block.

    Reports which pole the questioned text falls closer to, plus
    the absolute gap. When only one pole is supplied, the report
    falls back to "X is more / less distant than the supplied
    control" without committing to a polar interpretation.
    """
    if questioned_dist is None:
        return {
            "interpretation": "questioned_unavailable",
            "narrative": (
                "Could not compute the questioned text's distance "
                "to baseline."
            ),
        }

    if negative_dist is None and positive_dist is None:
        return {
            "interpretation": "baseline_only",
            "narrative": (
                f"Questioned text's function-word distance to the "
                f"baseline is {questioned_dist:.4f}. No controls "
                "supplied; no comparative interpretation available."
            ),
        }

    if negative_dist is not None and positive_dist is not None:
        # Both poles supplied — the diagnostic case.
        gap_to_neg = questioned_dist - negative_dist
        gap_to_pos = questioned_dist - positive_dist
        if abs(gap_to_neg) < abs(gap_to_pos):
            interp = "closer_to_negative_control"
            narrative = (
                f"Questioned text (Δ {questioned_dist:.4f}) is "
                f"closer to the known-authentic control "
                f"(Δ {negative_dist:.4f}, gap {abs(gap_to_neg):.4f}) "
                f"than to the known-smoothed control "
                f"(Δ {positive_dist:.4f}, gap {abs(gap_to_pos):.4f})."
            )
        else:
            interp = "closer_to_positive_control"
            narrative = (
                f"Questioned text (Δ {questioned_dist:.4f}) is "
                f"closer to the known-smoothed control "
                f"(Δ {positive_dist:.4f}, gap {abs(gap_to_pos):.4f}) "
                f"than to the known-authentic control "
                f"(Δ {negative_dist:.4f}, gap {abs(gap_to_neg):.4f})."
            )
        # Edge case: questioned distance is between the controls,
        # which is the typical pattern. Note when it falls outside.
        within = (
            min(negative_dist, positive_dist)
            <= questioned_dist
            <= max(negative_dist, positive_dist)
        )
        return {
            "interpretation": interp,
            "narrative": narrative,
            "questioned_within_control_band": within,
            "gap_to_negative": abs(gap_to_neg),
            "gap_to_positive": abs(gap_to_pos),
        }

    if negative_dist is not None:
        return {
            "interpretation": "negative_only",
            "narrative": (
                f"Questioned text (Δ {questioned_dist:.4f}) "
                f"compared against negative control "
                f"(Δ {negative_dist:.4f}); positive control not "
                "supplied. Without both poles, only relative "
                "distance to the negative control is reportable."
            ),
            "gap_to_negative": abs(questioned_dist - negative_dist),
        }
    return {
        "interpretation": "positive_only",
        "narrative": (
            f"Questioned text (Δ {questioned_dist:.4f}) compared "
            f"against positive control (Δ {positive_dist:.4f}); "
            "negative control not supplied. Without both poles, "
            "only relative distance to the positive control is "
            "reportable."
        ),
        "gap_to_positive": abs(questioned_dist - positive_dist),
    }


def run_controls_audit(
    *,
    questioned_text: str,
    baseline_texts: list[str],
    negative_control_text: str | None = None,
    positive_control_text: str | None = None,
    allow_non_prose: bool = False,
    strip_rules: str | list[str] | None = None,
    strip_aggressive: bool = False,
    strip_masking: str | list[str] | None = None,
) -> dict[str, Any]:
    """Compute function-word distances for questioned + each
    available control against the baseline mean. Returns the
    side-by-side report."""
    if not baseline_texts:
        return {
            "task_surface": TASK_SURFACE,
            "tool": TOOL_NAME,
            "version": SCRIPT_VERSION,
            "available": False,
            "reason": "no baseline texts supplied",
        }

    # Apply preprocessing to baseline + each input.
    cleaned_baselines: list[str] = []
    for text in baseline_texts:
        cleaned, _ = _strip_and_tokenize(
            text, allow_non_prose=allow_non_prose,
            strip_rules=strip_rules,
            strip_aggressive=strip_aggressive,
            strip_masking=strip_masking,
        )
        if cleaned.strip():
            cleaned_baselines.append(cleaned)

    if not cleaned_baselines:
        return {
            "task_surface": TASK_SURFACE,
            "tool": TOOL_NAME,
            "version": SCRIPT_VERSION,
            "available": False,
            "reason": "all baseline texts empty after preprocessing",
        }

    baseline_mean = _baseline_mean_function_word_vector(cleaned_baselines)

    def _measure(label: str, text: str | None) -> dict[str, Any]:
        if text is None:
            return {"label": label, "supplied": False, "n_words": 0}
        cleaned, _ = _strip_and_tokenize(
            text, allow_non_prose=allow_non_prose,
            strip_rules=strip_rules,
            strip_aggressive=strip_aggressive,
            strip_masking=strip_masking,
        )
        m = _function_word_distance_to_baseline(cleaned, baseline_mean)
        return {"label": label, "supplied": True, **m}

    questioned = _measure("questioned", questioned_text)
    negative = _measure("negative_control", negative_control_text)
    positive = _measure("positive_control", positive_control_text)

    classification = _classify_position(
        questioned.get("distance"),
        negative.get("distance"),
        positive.get("distance"),
    )

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "available": True,
        "n_baseline_files": len(cleaned_baselines),
        "questioned": questioned,
        "negative_control": negative,
        "positive_control": positive,
        "classification": classification,
    }


# --- Markdown rendering ----------------------------------------


def _claim_license_block(report: dict[str, Any]) -> str:
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "A side-by-side comparison of function-word distance "
            "from questioned text and each supplied control "
            "(known-authentic = negative, known-smoothed = positive) "
            "to the same baseline corpus. Reports which pole the "
            "questioned text is closer to."
        ),
        does_not_license=(
            "A verdict on whether the questioned text is "
            "authentic or smoothed. The audit measures distance, "
            "not provenance. The interpretation depends entirely "
            "on whether the supplied controls actually represent "
            "the categories the user claims for them — the framework "
            "trusts the user's labels; if the supplied negative "
            "control is itself smoothed, the report is misleading. "
            "Authorship attribution requires out-of-framework "
            "evidence."
        ),
        comparison_set={
            "n_baseline_files": report.get("n_baseline_files", 0),
            "negative_control_supplied": report.get(
                "negative_control", {}).get("supplied", False),
            "positive_control_supplied": report.get(
                "positive_control", {}).get("supplied", False),
            "interpretation": (
                report.get("classification") or {}
            ).get("interpretation"),
        },
        additional_caveats=[
            "The metric is function-word L1 distance, not full "
            "Burrows Delta. Results are robust enough for "
            "comparison-frame interpretation but should not be "
            "read as the literature's reference Δ values.",
            "Both controls should be roughly the same length as "
            "the questioned text. Length-matched bootstrap (per "
            "voice_distance.py --bootstrap) is the right way to "
            "confirm the gaps are meaningful at the questioned "
            "text's word count.",
            "If only one control is supplied, the report degrades "
            "to single-pole comparison — useful but weaker than "
            "a both-poles comparison.",
        ],
    )
    return lic.render_block().rstrip()


def render_report(report: dict[str, Any]) -> str:
    if not report.get("available"):
        return (
            "# Controls audit\n\n"
            f"_Unavailable: {report.get('reason', 'unknown')}._\n"
        )
    questioned = report.get("questioned", {})
    negative = report.get("negative_control", {})
    positive = report.get("positive_control", {})
    classification = report.get("classification", {})

    lines: list[str] = [
        "# Controls audit",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Baseline files:** {report.get('n_baseline_files', 0)}",
        "",
        "## Side-by-side distances",
        "",
        "| text | n_words | function-word L1 distance to baseline |",
        "|---|---:|---:|",
    ]
    for entry in (questioned, negative, positive):
        if not entry.get("supplied", True):
            lines.append(
                f"| {entry['label']} | _(not supplied)_ | _(not supplied)_ |"
            )
            continue
        d = entry.get("distance")
        d_str = f"{d:.4f}" if isinstance(d, (int, float)) else "n/a"
        lines.append(
            f"| {entry['label']} | {entry.get('n_words', 0):,} | "
            f"{d_str} |"
        )
    lines.append("")

    lines.append("## Interpretation")
    lines.append("")
    lines.append(
        f"**Classification:** `{classification.get('interpretation', 'unknown')}`"
    )
    lines.append("")
    lines.append(classification.get("narrative", ""))
    lines.append("")
    if "questioned_within_control_band" in classification:
        within = classification["questioned_within_control_band"]
        lines.append(
            f"**Within control band:** "
            f"{'yes' if within else 'no — questioned distance falls outside the [negative, positive] interval'}"
        )
        lines.append("")

    lines.append(_claim_license_block(report))
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- CLI -------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="controls_audit.py",
        description=(
            "Negative + positive controls for voice-distance "
            "comparison. Reports whether the questioned text is "
            "closer to a known-authentic control or a known-"
            "smoothed control, with the same baseline."
        ),
    )
    p.add_argument(
        "--questioned", required=True,
        help="Path to the questioned text.",
    )
    p.add_argument(
        "--negative-control",
        help=(
            "Path to a known-authentic control by the same writer "
            "(e.g., earlier untouched draft, published essay)."
        ),
    )
    p.add_argument(
        "--positive-control",
        help=(
            "Path to a known-smoothed / AI-edited / heavily "
            "copyedited control."
        ),
    )
    p.add_argument(
        "--baseline-dir",
        help="Directory of baseline .txt / .md files.",
    )
    p.add_argument(
        "--manifest",
        help="Optional JSONL corpus manifest.",
    )
    p.add_argument("--use", default="baseline")
    p.add_argument("--split")
    p.add_argument("--register")
    p.add_argument("--persona")
    p.add_argument("--ai-status", default="pre_ai_human")
    p.add_argument("--allow-non-prose", action="store_true")
    p.add_argument("--strip-rules")
    p.add_argument("--strip-aggressive", action="store_true")
    p.add_argument("--strip-masking")
    p.add_argument("--json", action="store_true")
    p.add_argument("--out")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not args.baseline_dir and not args.manifest:
        sys.stderr.write(
            "Provide either --baseline-dir or --manifest.\n"
        )
        return 2

    questioned_path = Path(args.questioned).expanduser()
    if not questioned_path.is_file():
        sys.stderr.write(f"--questioned not found: {args.questioned}\n")
        return 2
    questioned_text = questioned_path.read_text(
        encoding="utf-8", errors="ignore",
    )

    def _read_optional_path(label: str, path: str | None) -> tuple[str | None, bool]:
        """Return ``(text_or_None, error_occurred)``.

        1.37.1 hardening: pre-1.37.1 a missing user-supplied
        control path printed an error and returned None silently,
        so the CLI exited 0 with a misleading single-pole report.
        Now: a None path (flag not supplied) returns ``(None, False)``;
        a non-empty path that doesn't resolve returns
        ``(None, True)`` so the CLI can return rc=2 — same hardened-
        input convention as confounder_audit.py and
        evidentiary_conditions_gate.py.
        """
        if not path:
            return None, False
        p = Path(path).expanduser()
        if not p.is_file():
            sys.stderr.write(f"{label} not found: {path}\n")
            return None, True
        return p.read_text(encoding="utf-8", errors="ignore"), False

    negative_text, neg_err = _read_optional_path(
        "--negative-control", args.negative_control,
    )
    positive_text, pos_err = _read_optional_path(
        "--positive-control", args.positive_control,
    )
    if neg_err or pos_err:
        return 2

    # Load baseline texts.
    try:
        baseline_entries = load_entries(
            baseline_dir=args.baseline_dir,
            manifest=args.manifest,
            use=args.use,
            split=args.split,
            register=args.register,
            persona=args.persona,
            ai_status=args.ai_status,
        )
    except (FileNotFoundError, ValueError) as exc:
        sys.stderr.write(f"Baseline error: {exc}\n")
        return 2

    if not baseline_entries:
        sys.stderr.write("No baseline entries matched the filter.\n")
        return 2

    # Drop any baseline entry that resolves to the questioned /
    # negative / positive paths (self-overlap guard, same convention
    # as voice_distance.py and general_imposters.py).
    try:
        questioned_resolved = questioned_path.resolve()
    except OSError:
        questioned_resolved = questioned_path
    paths_to_exclude = {questioned_resolved}
    if args.negative_control:
        try:
            paths_to_exclude.add(
                Path(args.negative_control).expanduser().resolve()
            )
        except OSError:
            pass
    if args.positive_control:
        try:
            paths_to_exclude.add(
                Path(args.positive_control).expanduser().resolve()
            )
        except OSError:
            pass

    filtered: list[dict[str, Any]] = []
    dropped: list[str] = []
    for entry in baseline_entries:
        try:
            entry_resolved = Path(entry["path"]).resolve()
        except OSError:
            entry_resolved = Path(entry["path"])
        if entry_resolved in paths_to_exclude:
            dropped.append(entry["id"])
            continue
        filtered.append(entry)
    if dropped:
        sys.stderr.write(
            f"Dropped {len(dropped)} baseline entries overlapping "
            f"questioned/control paths: {', '.join(dropped)}\n"
        )

    # 1.37.1 hardening: pre-1.37.1, if every baseline entry
    # overlapped the questioned/control paths, the filter
    # returned an empty list and the audit exited 0 with
    # `available:false`. That misreports a self-overlap-guard
    # failure as a normal output. Hard-fail instead — same
    # convention paragraph_audit + general_imposters use.
    if not filtered:
        sys.stderr.write(
            "Baseline empty after dropping overlap with "
            "questioned/control paths. Point --baseline-dir at a "
            "directory that doesn't contain the questioned text "
            "or supplied controls, or pass --manifest with "
            "non-overlapping entries.\n"
        )
        return 2

    baseline_texts = [
        read_text(Path(entry["path"])) for entry in filtered
    ]

    report = run_controls_audit(
        questioned_text=questioned_text,
        baseline_texts=baseline_texts,
        negative_control_text=negative_text,
        positive_control_text=positive_text,
        allow_non_prose=args.allow_non_prose,
        strip_rules=args.strip_rules,
        strip_aggressive=args.strip_aggressive,
        strip_masking=args.strip_masking,
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
