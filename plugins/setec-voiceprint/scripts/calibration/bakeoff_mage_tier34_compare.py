#!/usr/bin/env python3
"""bakeoff_mage_tier34_compare.py -- read bake-off survey JSONs and
print a comparison table.

Reads every `survey_phaseA_<model>.json` and `survey_phaseB_<model>
.json` under --surveys-dir, extracts `per_signal[sig].calibration
.direction_aware_auc` for the relevant signals, and prints a
markdown comparison table per phase.

The winning configs are the ones with the highest da_AUC on each
phase's target signals. da_AUC > 0.5 means the signal discriminates
in the expected direction; da_AUC < 0.5 means polarity inversion
(signal is the WRONG direction for this corpus). Ties broken by
the maintainer's judgment (cost, reproducibility, deployment fit).

Usage:
    python bakeoff_mage_tier34_compare.py \\
        --surveys-dir ai-prose-baselines-private/calibration_runs/bakeoff_mage_tier34_5K
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PHASE_A_MODELS = ["mxbai", "gemma", "harrier", "minilm"]
PHASE_A_SIGNALS = ["adjacent_cosine_mean", "adjacent_cosine_sd"]

PHASE_B_MODELS = [
    "gpt2", "tinyllama", "llama32_1b",
    # 2026-05-31 additions: within-family parameter scan (gpt2->gpt2_medium same
    # tokenizer; llama32_1b->llama32_3b same family) + a modern long-context probe
    # (qwen25_1_5b, 32K ctx) to measure what the gpt2/tinyllama context ceiling costs.
    "gpt2_medium", "qwen25_1_5b", "llama32_3b",
]
PHASE_B_SIGNALS = [
    "surprisal_mean", "surprisal_sd", "surprisal_acf_lag1",
]


def _load_survey(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  WARN: failed to read {path.name}: {exc}", file=sys.stderr)
        return None


def _da_auc(survey: dict[str, Any], signal: str) -> float | None:
    """Pull direction_aware_auc for a signal from a survey JSON.

    The survey emits a flat `rows: [{"signal": ..., "direction_aware_auc": ...}]`
    list (see `calibration_survey.SurveyRow.to_dict`). Earlier drafts of
    this reader assumed a nested `per_signal[sig].calibration.direction_aware_auc`
    layout that the survey has never produced; that path silently returned
    None for every cell.
    """
    for row in survey.get("rows") or []:
        if not isinstance(row, dict):
            continue
        if row.get("signal") != signal:
            continue
        val = row.get("direction_aware_auc")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
        return None
    return None


def _format_da(val: float | None) -> str:
    if val is None:
        return "  --  "
    if val < 0.5:
        # Polarity inversion: signal disagrees with the registry direction.
        return f"!{val:.4f}"
    if val >= 0.55:
        return f"*{val:.4f}"  # clear separation
    return f" {val:.4f}"


def _format_table(
    phase_label: str, models: list[str], signals: list[str],
    surveys_dir: Path, file_prefix: str,
) -> str:
    """Build a markdown comparison table for one phase.

    Winner selection disqualifies any config that has ANY target signal
    either missing or polarity-inverted (da_AUC < 0.5). Among survivors,
    rank by the minimum da_AUC across the phase's target signals — the
    "weakest link" metric — so the winner is robust across every signal
    rather than excellent on one and weak on another.
    """
    rows = []
    header = "| Model | " + " | ".join(signals) + " | min da_AUC |"
    sep = "|" + "---|" * (len(signals) + 2)
    rows.append(header)
    rows.append(sep)
    summary: list[tuple[str, list[float | None]]] = []
    for model in models:
        survey_path = surveys_dir / f"{file_prefix}_{model}.json"
        survey = _load_survey(survey_path)
        if survey is None:
            rows.append(
                f"| {model} | "
                + " | ".join(["  MISSING  "] * len(signals))
                + " | -- |"
            )
            summary.append((model, [None] * len(signals)))
            continue
        per_signal_da: list[float | None] = []
        cells: list[str] = []
        for sig in signals:
            da = _da_auc(survey, sig)
            cells.append(_format_da(da))
            per_signal_da.append(da)
        observed = [d for d in per_signal_da if d is not None]
        min_label = f"{min(observed):.4f}" if observed else "--"
        rows.append(f"| {model} | " + " | ".join(cells) + f" | {min_label} |")
        summary.append((model, per_signal_da))
    rows.append("")
    rows.append(
        "Legend: `*` = da_AUC >= 0.55 (clear separation); "
        "`!` = da_AUC < 0.5 (polarity inversion). "
        "Winner column shows the MIN across signals; a config with any "
        "inverted or missing signal is disqualified from winner selection."
    )
    rows.append("")
    # Winner: every target signal must be present AND >= 0.5; rank by min.
    eligible: list[tuple[str, float]] = []
    for model, das in summary:
        if any(d is None for d in das):
            continue
        if any(d < 0.5 for d in das):
            continue
        eligible.append((model, min(das)))
    if eligible:
        eligible.sort(key=lambda x: x[1], reverse=True)
        winner_model, winner_min = eligible[0]
        rows.append(
            f"**Phase {phase_label} recommended winner**: `{winner_model}` "
            f"(min da_AUC = {winner_min:.4f} across {len(signals)} signals; "
            f"every target signal >= 0.5)."
        )
    else:
        # Diagnose why nothing was eligible so the operator knows where to look.
        inverted_models = [
            m for m, das in summary
            if any(d is not None and d < 0.5 for d in das)
        ]
        partial_models = [
            m for m, das in summary
            if any(d is None for d in das)
            and not any(d is not None and d < 0.5 for d in das)
        ]
        reasons = []
        if inverted_models:
            reasons.append(
                "polarity-inverted on at least one signal: "
                + ", ".join(f"`{m}`" for m in inverted_models)
            )
        if partial_models:
            reasons.append(
                "missing at least one signal: "
                + ", ".join(f"`{m}`" for m in partial_models)
            )
        detail = "; ".join(reasons) if reasons else "every config failed to score"
        rows.append(
            f"**Phase {phase_label} — no winner**: {detail}. "
            f"Inspect the survey JSONs for per-signal errors."
        )
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--surveys-dir", required=True, type=Path,
        help="Directory containing survey_phaseA_*.json + survey_phaseB_*.json",
    )
    args = parser.parse_args(argv)

    if not args.surveys_dir.exists():
        print(f"ERROR: {args.surveys_dir} does not exist", file=sys.stderr)
        return 2

    print("# MAGE Tier 3+4 Bake-off Comparison")
    print()
    print(f"Surveys: `{args.surveys_dir}`")
    print()
    print("## Phase A — Tier 3 embedding bake-off")
    print()
    print(_format_table(
        "A", PHASE_A_MODELS, PHASE_A_SIGNALS,
        args.surveys_dir, "survey_phaseA",
    ))
    print()
    print("## Phase B — Tier 4 surprisal bake-off")
    print()
    print(_format_table(
        "B", PHASE_B_MODELS, PHASE_B_SIGNALS,
        args.surveys_dir, "survey_phaseB",
    ))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
