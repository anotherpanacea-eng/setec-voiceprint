#!/usr/bin/env python3
"""adversarial_robustness_card.py — per-signal robustness card.

Trustworthiness Tier-2 build, paired-release schedule Release 7.
The validation harness's adversarial-class track (paraphrase /
humanizer / backtranslation / copyedit fixtures) was scoped pre-
schedule as fixture-acquisition + per-class slicing in the
existing harness. What was always missing was the **per-signal
output shape**: a card that says

  > burstiness_B is stable under light copyediting (Δ < 0.1 SD)
  > but collapses under paraphrase (drops 1.4 SD).

That's what this module ships. It reads the variance audit (or
any other signal-bearing audit) on a *base* text plus one or
more *fixture variants* (the same text after a transformation:
paraphrase, light copyedit, heavy copyedit, humanizer, back-
translation, voice-restoration pass, etc.) and produces a
**robustness card**: per-signal, per-fixture, the change relative
to the base reading.

The card answers two questions per signal:

  1. **Stability**: how much does this signal move under each
     transformation? A signal that holds within ± 0.5 SD across
     all fixtures is robust; one that swings 2+ SD under
     paraphrase is fragile to that transformation specifically.
  2. **Direction**: under each transformation, does the signal
     move in the *expected* direction for the framework's
     hypothesis? E.g., AI smoothing is supposed to *lower*
     burstiness — if the smoothing-fixture run lowers burstiness
     vs. the original, that's confirmation; if it doesn't, the
     fixture isn't doing what its label claims, OR the framework's
     polarity assumption is wrong on that fixture's prose.

The card lands as a robustness label per (signal, transformation):
``stable`` / ``moderate`` / ``fragile`` / ``inverted_polarity`` /
``small_base`` / ``unstable_small_base`` / ``unknown``.

This is **infrastructure**, not a fixture catalog. The fixture
acquisition (DIPPER paraphrases, humanizer outputs, etc.) lives
in the validation harness's separate adversarial-class roadmap
track. Users with their own fixtures (a paraphrased version of
their draft, an editor's heavy-copyedit pass, etc.) can use this
module immediately.

Usage:

    python3 scripts/adversarial_robustness_card.py \\
        --base original.json \\
        --fixture light_copyedit:edited_light.json \\
        --fixture heavy_copyedit:edited_heavy.json \\
        --fixture paraphrase:paraphrased.json \\
        --fixture humanizer:humanized.json \\
        --json

Each fixture is supplied as ``LABEL:PATH``. The label appears in
the card's columns; the path points at a variance_audit (or
other) JSON output run on the transformed text.
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

from claim_license import (  # type: ignore
    ClaimLicense,
    with_state_caveats,
)

TASK_SURFACE = "validation"
TOOL_NAME = "adversarial_robustness_card"
SCRIPT_VERSION = "1.0"


# --- Signal extraction -----------------------------------------
#
# Pre-1.37.0 the framework's audit JSONs are heterogeneous:
# variance_audit puts compression-fraction at top-level; voice_
# distance puts overall.weighted_delta at top-level; etc. The
# robustness card needs a uniform extraction layer.
#
# We consume variance_audit specifically because its per-signal
# numbers are the most calibrated and the most heterogeneous
# under transformation. Future expansion can add per-family
# voice-distance numbers, paragraph rhythm, etc.

# Signal name → (json path tuple) for variance_audit JSON output.
_VARIANCE_SIGNALS: dict[str, tuple[str, ...]] = {
    "burstiness_B": (
        "audit", "tier1", "sentence_length", "burstiness_B",
    ),
    "sentence_length_sd": (
        "audit", "tier1", "sentence_length", "sd",
    ),
    "mtld": ("audit", "tier1", "mtld"),
    "mattr": ("audit", "tier1", "mattr", "value"),
    "shannon_entropy": (
        "audit", "tier1", "shannon_entropy_bits",
    ),
    "yules_k": ("audit", "tier1", "yules_k"),
    "fkgl_sd": ("audit", "tier1", "fkgl", "sd"),
    "connective_density": (
        "audit", "tier1", "connective_density", "per_1000_tokens",
    ),
    "compression_fraction": (
        "compression", "compression_fraction",
    ),
}


def _extract_signal(
    data: dict[str, Any], path: tuple[str, ...],
) -> float | None:
    """Walk the JSON path; return None if any step is missing or
    the final value isn't numeric."""
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    if isinstance(cur, (int, float)):
        return float(cur)
    return None


def _extract_all_signals(data: dict[str, Any]) -> dict[str, float | None]:
    """Extract every supported signal from a variance audit JSON."""
    return {
        name: _extract_signal(data, path)
        for name, path in _VARIANCE_SIGNALS.items()
    }


# --- Robustness classification ---------------------------------


def _classify_movement(
    base_value: float | None,
    fixture_value: float | None,
    *,
    stability_threshold: float = 0.10,
    fragile_threshold: float = 0.30,
) -> tuple[str, float | None]:
    """Map (base, fixture) → (label, relative_change).

    Relative change is `(fixture - base) / |base|` for non-zero
    base; for near-zero base we report the absolute change with
    the label ``small_base`` (small absolute movement, comparison
    uninterpretable) or ``unstable_small_base`` (large absolute
    movement from a near-zero baseline — clearly notable but the
    relative-change figure can't quantify it).

    Labels:
      - ``stable`` — |Δ| ≤ stability_threshold (default 10%)
      - ``moderate`` — stability_threshold < |Δ| ≤ fragile_threshold
      - ``fragile`` — |Δ| > fragile_threshold (default 30%)
      - ``inverted_polarity`` — flagged when the fixture sign-flips
        a signal that was clearly non-zero
      - ``unknown`` — base or fixture missing
      - ``small_base`` — base near-zero AND fixture near-zero;
        the comparison is uninterpretable (and uninteresting)
      - ``unstable_small_base`` — base near-zero but fixture
        moved noticeably (|fixture| ≥ stability_threshold). The
        signal isn't stable under this fixture; the relative-
        change figure can't quantify it because the base is
        effectively zero.
    """
    if base_value is None or fixture_value is None:
        return "unknown", None
    if abs(base_value) < 1e-6:
        # Near-zero base; use absolute change. Distinguish the
        # tiny-fixture case (truly stable, label ``small_base``)
        # from the large-fixture case (the signal moved a lot
        # despite the base being effectively zero — this is
        # notable, label ``unstable_small_base``).
        if abs(fixture_value) < stability_threshold:
            return "small_base", fixture_value
        return "unstable_small_base", fixture_value

    rel = (fixture_value - base_value) / abs(base_value)
    abs_rel = abs(rel)

    # Sign flip on a clearly non-zero base.
    if (
        abs(base_value) >= 0.05
        and abs(fixture_value) >= 0.05
        and (
            (base_value > 0) != (fixture_value > 0)
        )
    ):
        return "inverted_polarity", rel

    if abs_rel <= stability_threshold:
        return "stable", rel
    if abs_rel <= fragile_threshold:
        return "moderate", rel
    return "fragile", rel


# --- Robustness card -------------------------------------------


def build_robustness_card(
    *,
    base: dict[str, Any],
    fixtures: list[tuple[str, dict[str, Any]]],
    stability_threshold: float = 0.10,
    fragile_threshold: float = 0.30,
) -> dict[str, Any]:
    """Build the per-signal robustness card.

    Returns a dict where each signal maps to a per-fixture
    breakdown plus an overall robustness summary.
    """
    base_signals = _extract_all_signals(base)
    fixture_signals: dict[str, dict[str, float | None]] = {
        label: _extract_all_signals(data) for label, data in fixtures
    }

    per_signal: dict[str, Any] = {}
    for signal in _VARIANCE_SIGNALS:
        base_val = base_signals.get(signal)
        per_fixture: dict[str, Any] = {}
        labels_seen: list[str] = []
        for fixture_label, fdata in fixtures:
            fixture_val = fixture_signals[fixture_label].get(signal)
            label, rel_change = _classify_movement(
                base_val, fixture_val,
                stability_threshold=stability_threshold,
                fragile_threshold=fragile_threshold,
            )
            per_fixture[fixture_label] = {
                "base_value": base_val,
                "fixture_value": fixture_val,
                "relative_change": (
                    round(rel_change, 4)
                    if isinstance(rel_change, float) else None
                ),
                "label": label,
            }
            # ``unknown`` and the truly-uninterpretable
            # ``small_base`` are dropped from labels_seen.
            # ``unstable_small_base`` IS counted: it means the
            # base was effectively zero but the fixture moved
            # noticeably, which is itself a robustness signal
            # (the relative-change figure can't quantify it,
            # but the report should not hide it).
            if label not in {"unknown", "small_base"}:
                labels_seen.append(label)

        # Overall summary across fixtures.
        if not labels_seen:
            overall = "unknown"
        elif (
            "fragile" in labels_seen
            or "inverted_polarity" in labels_seen
            or "unstable_small_base" in labels_seen
        ):
            # Any fragile / inverted / unstable_small_base reading
            # means the signal is not robust across the supplied
            # transformations.
            overall = "fragile"
        elif all(l == "stable" for l in labels_seen):
            overall = "stable"
        elif "moderate" in labels_seen:
            overall = "moderate"
        else:
            overall = "mixed"

        per_signal[signal] = {
            "base_value": base_val,
            "per_fixture": per_fixture,
            "overall_robustness": overall,
        }

    # Aggregate metrics.
    n_signals_with_data = sum(
        1 for s in per_signal.values()
        if s["base_value"] is not None
    )
    n_robust = sum(
        1 for s in per_signal.values()
        if s["overall_robustness"] == "stable"
    )
    n_fragile = sum(
        1 for s in per_signal.values()
        if s["overall_robustness"] == "fragile"
    )
    n_inverted = sum(
        1 for s in per_signal.values()
        if any(
            f.get("label") == "inverted_polarity"
            for f in s.get("per_fixture", {}).values()
        )
    )
    n_unstable_small_base = sum(
        1 for s in per_signal.values()
        if any(
            f.get("label") == "unstable_small_base"
            for f in s.get("per_fixture", {}).values()
        )
    )

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "n_signals_with_data": n_signals_with_data,
        "n_fixtures": len(fixtures),
        "fixture_labels": [label for label, _ in fixtures],
        "stability_threshold": stability_threshold,
        "fragile_threshold": fragile_threshold,
        "n_robust_signals": n_robust,
        "n_fragile_signals": n_fragile,
        "n_inverted_polarity_readings": n_inverted,
        "n_unstable_small_base_readings": n_unstable_small_base,
        "per_signal": per_signal,
    }


# --- Markdown rendering ----------------------------------------


def _claim_license_block(card: dict[str, Any]) -> str:
    n_signals = card.get("n_signals_with_data", 0)
    n_fixtures = card.get("n_fixtures", 0)
    lic = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "A per-signal robustness card across one or more "
            "fixture transformations (paraphrase, light / heavy "
            "copyedit, humanizer, backtranslation, voice "
            "restoration, etc.). For each (signal, fixture) cell "
            "the card reports the relative change vs. the base "
            "reading and a robustness label "
            "(stable / moderate / fragile / inverted_polarity / "
            "small_base / unstable_small_base / unknown). The "
            "overall per-signal robustness is fragile if any "
            "fixture flagged fragile, inverted_polarity, or "
            "unstable_small_base; stable if all readings were "
            "stable; mixed otherwise."
        ),
        does_not_license=(
            "A verdict on whether a signal is generally robust. "
            "The card is FIXTURE-SPECIFIC: a signal stable under "
            "the user's particular paraphrase fixture may collapse "
            "under a different paraphraser. The framework trusts "
            "the user's fixture labels — if `paraphrase.json` is "
            "actually a heavy copyedit, the card is misnamed but "
            "still measures the actual change. Validation against "
            "labeled adversarial corpora (DIPPER / humanizer-tool "
            "outputs / etc.) is roadmap; the card's output shape "
            "is what this release ships."
        ),
        comparison_set={
            "n_signals_with_data": n_signals,
            "n_fixtures": n_fixtures,
            "fixture_labels": ", ".join(
                card.get("fixture_labels") or []
            ) or "(none)",
            "stability_threshold": card.get("stability_threshold"),
            "fragile_threshold": card.get("fragile_threshold"),
        },
        additional_caveats=[
            "Stability and fragility thresholds are heuristic "
            "(default ± 10% / 30% relative change). Calibration-"
            "pending against a labeled-fixture corpus.",
            "Near-zero base values trigger one of two labels: "
            "`small_base` when the fixture is also near-zero "
            "(uninterpretable AND uninteresting), or "
            "`unstable_small_base` when the fixture moved "
            "noticeably (clearly notable, but the relative-change "
            "figure can't quantify it because |base| < 1e-6). "
            "The aggregator treats `unstable_small_base` like "
            "`fragile`; it does NOT silently drop large absolute "
            "movements from a near-zero baseline.",
            "The card's interpretive value scales with the number "
            "and quality of fixtures supplied. A single-fixture "
            "card answers \"how does this signal move under THIS \"\n            \"transformation\" — useful but narrower than a "
            "multi-fixture card.",
        ],
    )
    # B.3: append state-routed caveats when the operator supplied
    # --ai-status. No-op when ai_status is absent — pre-B.3 callers
    # keep their previous behavior.
    lic = with_state_caveats(
        lic, target_ai_status=card.get("ai_status"),
    )
    return lic.render_block().rstrip()


_LABEL_GLYPH = {
    "stable": "✓",
    "moderate": "·",
    "fragile": "✗",
    "inverted_polarity": "↺",
    "small_base": "?",
    "unstable_small_base": "!",
    "unknown": "—",
}


def render_report(card: dict[str, Any]) -> str:
    fixture_labels = card.get("fixture_labels") or []
    per_signal = card.get("per_signal", {})

    lines: list[str] = [
        "# Adversarial robustness card",
        "",
        f"**Task surface:** `{TASK_SURFACE}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        f"**Fixtures:** {card.get('n_fixtures', 0)} "
        f"({', '.join(fixture_labels) or 'none'})",
        f"**Signals with base data:** "
        f"{card.get('n_signals_with_data', 0)} / {len(per_signal)}",
        f"**Robust signals:** {card.get('n_robust_signals', 0)}  "
        f"**Fragile signals:** {card.get('n_fragile_signals', 0)}  "
        f"**Inverted-polarity readings:** "
        f"{card.get('n_inverted_polarity_readings', 0)}  "
        f"**Unstable small-base readings:** "
        f"{card.get('n_unstable_small_base_readings', 0)}",
        "",
        "## Robustness card",
        "",
        "Glyph legend: ✓ stable, · moderate, ✗ fragile, "
        "↺ inverted polarity, ? small base, ! unstable small base, "
        "— unknown.",
        "",
    ]

    if not fixture_labels:
        lines.append(
            "_(No fixtures supplied. Pass at least one "
            "`--fixture LABEL:PATH` to populate the card.)_"
        )
        lines.append("")
    else:
        # Per-signal × per-fixture matrix.
        header = ["signal", "base", "overall"]
        for fl in fixture_labels:
            header.append(fl)
        lines.append("| " + " | ".join(header) + " |")
        lines.append(
            "|" + "|".join(["---"] * len(header)) + "|"
        )
        for signal, info in per_signal.items():
            base_val = info.get("base_value")
            base_str = (
                f"{base_val:.4f}"
                if isinstance(base_val, (int, float)) else "—"
            )
            row = [
                signal,
                base_str,
                f"`{info.get('overall_robustness', 'unknown')}`",
            ]
            for fl in fixture_labels:
                cell = info["per_fixture"].get(fl, {})
                label = cell.get("label", "unknown")
                rel = cell.get("relative_change")
                glyph = _LABEL_GLYPH.get(label, "?")
                if isinstance(rel, (int, float)):
                    row.append(f"{glyph} ({rel:+.1%})")
                else:
                    row.append(glyph)
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    # Per-signal narrative for fragile / inverted signals.
    interesting = [
        (sig, info) for sig, info in per_signal.items()
        if info.get("overall_robustness") in {
            "fragile", "moderate", "mixed",
        }
    ]
    if interesting:
        lines.append("## Notable signals")
        lines.append("")
        for sig, info in interesting:
            overall = info.get("overall_robustness", "unknown")
            lines.append(f"### `{sig}` — {overall}")
            lines.append("")
            for fl in fixture_labels:
                cell = info["per_fixture"].get(fl, {})
                rel = cell.get("relative_change")
                if isinstance(rel, (int, float)):
                    lines.append(
                        f"- `{fl}`: "
                        f"{cell.get('label', 'unknown')} "
                        f"({rel:+.1%}, base "
                        f"{cell.get('base_value')}, fixture "
                        f"{cell.get('fixture_value')})"
                    )
                else:
                    lines.append(
                        f"- `{fl}`: {cell.get('label', 'unknown')}"
                    )
            lines.append("")

    lines.append(_claim_license_block(card))
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- CLI -------------------------------------------------------


def _parse_fixture_arg(value: str) -> tuple[str, str]:
    """Parse `LABEL:PATH` into a (label, path) tuple."""
    if ":" not in value:
        raise argparse.ArgumentTypeError(
            f"--fixture must be LABEL:PATH; got {value!r}"
        )
    label, _, path = value.partition(":")
    label = label.strip()
    path = path.strip()
    if not label or not path:
        raise argparse.ArgumentTypeError(
            f"--fixture LABEL and PATH must both be non-empty; "
            f"got {value!r}"
        )
    return label, path


def _read_json(path: str) -> dict[str, Any]:
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
        prog="adversarial_robustness_card.py",
        description=(
            "Per-signal robustness card across fixture "
            "transformations. Reads variance_audit JSON for a "
            "base text plus one or more fixtures and reports how "
            "much each signal moves under each transformation. "
            "Output: per-signal robustness label "
            "(stable / fragile / inverted_polarity / etc.)."
        ),
    )
    p.add_argument(
        "--base", required=True,
        help="Path to variance_audit JSON for the base (untransformed) text.",
    )
    p.add_argument(
        "--fixture", action="append", default=[],
        type=_parse_fixture_arg,
        metavar="LABEL:PATH",
        help=(
            "Fixture variant: LABEL:PATH where LABEL is the "
            "transformation name (paraphrase, light_copyedit, "
            "humanizer, backtranslation, etc.) and PATH points "
            "at the variance_audit JSON for the transformed "
            "text. Repeat for multiple fixtures."
        ),
    )
    p.add_argument(
        "--stability-threshold", type=float, default=0.10,
        help="Relative-change |Δ| threshold below which a "
             "signal is `stable` (default 10%%).",
    )
    p.add_argument(
        "--fragile-threshold", type=float, default=0.30,
        help="Relative-change |Δ| threshold above which a "
             "signal is `fragile` (default 30%%).",
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
        base = _read_json(args.base)
    except (FileNotFoundError, ValueError) as exc:
        sys.stderr.write(f"--base: {exc}\n")
        return 2

    fixtures: list[tuple[str, dict[str, Any]]] = []
    for label, path in args.fixture:
        try:
            data = _read_json(path)
        except (FileNotFoundError, ValueError) as exc:
            sys.stderr.write(f"--fixture {label}: {exc}\n")
            return 2
        fixtures.append((label, data))

    card = build_robustness_card(
        base=base,
        fixtures=fixtures,
        stability_threshold=args.stability_threshold,
        fragile_threshold=args.fragile_threshold,
    )
    # B.3: surface --ai-status into the card dict so
    # _claim_license_block can route per state.
    if args.ai_status:
        card["ai_status"] = args.ai_status

    out = (
        json.dumps(card, indent=2, default=str)
        if args.json else render_report(card)
    )
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
