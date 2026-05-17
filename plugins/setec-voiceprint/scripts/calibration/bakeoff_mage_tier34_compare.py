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

PHASE_B_MODELS = ["gpt2", "tinyllama", "llama32_1b"]
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
    """Pull direction_aware_auc for a signal from a survey JSON."""
    per_sig = (survey.get("per_signal") or {}).get(signal) or {}
    cal = per_sig.get("calibration") or {}
    val = cal.get("direction_aware_auc")
    if isinstance(val, (int, float)):
        return float(val)
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
    """Build a markdown comparison table for one phase."""
    rows = []
    # Header: model | sig1 | sig2 | ... | winner
    header = "| Model | " + " | ".join(signals) + " | Best da_AUC |"
    sep = "|" + "---|" * (len(signals) + 2)
    rows.append(header)
    rows.append(sep)
    summary: list[tuple[str, float | None]] = []
    for model in models:
        survey_path = surveys_dir / f"{file_prefix}_{model}.json"
        survey = _load_survey(survey_path)
        if survey is None:
            row = f"| {model} | " + " | ".join(["  MISSING  "] * len(signals)) + " | -- |"
            summary.append((model, None))
            rows.append(row)
            continue
        cells = []
        max_da = None
        for sig in signals:
            da = _da_auc(survey, sig)
            cells.append(_format_da(da))
            if da is not None:
                if max_da is None or da > max_da:
                    max_da = da
        summary.append((model, max_da))
        best = f"{max_da:.4f}" if max_da is not None else "--"
        rows.append(f"| {model} | " + " | ".join(cells) + f" | {best} |")
    rows.append("")
    rows.append("Legend: `*` = da_AUC >= 0.55 (clear separation); "
                "`!` = da_AUC < 0.5 (polarity inversion).")
    rows.append("")
    # Recommended winner per phase.
    valid = [(m, d) for m, d in summary if d is not None and d >= 0.5]
    if valid:
        valid.sort(key=lambda x: x[1], reverse=True)
        winner_model, winner_da = valid[0]
        rows.append(
            f"**Phase {phase_label} recommended winner**: `{winner_model}` "
            f"(best da_AUC = {winner_da:.4f})."
        )
    else:
        rows.append(
            f"**Phase {phase_label} — no winner**: every config either "
            f"polarity-inverted (da_AUC < 0.5) or failed to score. "
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
