#!/usr/bin/env python3
"""cross_polarity_audit.py — per-slice polarity verdicts + cross-slice synthesis.

The existing ``polarity_audit.py`` treats every non-aggregate cell in a
slicer CSV as joint evidence for a single per-``(model, signal)``
verdict. That answers "does the registry direction match the data
overall?" but it can't answer "does the registry direction hold
*uniformly* across attack classes / comparator splits / length
buckets?" — a polarity that's `globally_inverted` on average might
still be `globally_consistent` on, say, paraphrase-attack rows.

This tool pivots: it takes a slicing dimension (default
``adversarial_class``) and runs ``polarity_audit.build_audit`` once
per distinct slice value, producing one verdict per
``(model, signal, slice_value)``. It then synthesises a cross-slice
report saying, for each ``(model, signal)`` whose verdict differs
across slice values, what the per-slice verdicts are and whether the
overall polarity finding is robust to the slice.

When to use
-----------

Use the existing ``polarity_audit.py`` for the headline finding (does
the registry point the right way overall?). Use this tool when:

* The slicer CSV contains rows with multiple values of the slicing
  dimension (``adversarial_class`` carrying ``none``, ``paraphrase``,
  ``humanizer``, ``backtranslation``, etc.) AND
* You want to know whether the registry-correction recommendations
  from ``polarity_audit`` hold across all slice values, or whether the
  registry needs ``direction_by_comparator``-style routing per slice.

For the MAGE 5K bundle as of 2026-05-18, every row has
``adversarial_class=none``, so this tool's output collapses to the
same single-slice verdict as ``polarity_audit``. It comes into its own
when RAID-style data with diverse ``attack`` labels lands.

CLI
---

::

    python3 cross_polarity_audit.py \\
        --input-csv  /path/to/slice_analysis.csv \\
        --slice-by   adversarial_class \\
        --out-json   /path/to/cross_polarity_audit.json \\
        --out-markdown /path/to/cross_polarity_audit.md \\
        --comparator-key notes.original_source

``--slice-by`` defaults to ``adversarial_class`` since that's the
load-bearing case for the post-registry-flip validation question. Any
slicing key the slicer wrote into the CSV is accepted.

Output
------

JSON shape::

    {
      "tool": "cross_polarity_audit",
      "tool_version": "v1.0.0",
      "slice_by": "adversarial_class",
      "slice_values": ["none", "paraphrase", "humanizer", ...],
      "per_slice": [
        {
          "slice_value": "none",
          "results": [
            {
              "model": "tinyllama", "signal": "surprisal_mean",
              "registry_direction": "lt",
              "verdict": "globally_inverted",
              "raw_auc": 0.6332,
              "raw_auc_ci": [0.614, 0.652],
              "n_pos": 1323, "n_neg": 2497,
            },
            ...
          ]
        },
        ...
      ],
      "cross_summary": [
        {
          "model": "tinyllama",
          "signal": "surprisal_mean",
          "registry_direction": "lt",
          "verdicts_per_slice": {
            "none": "globally_inverted",
            "paraphrase": "globally_inverted",
            "humanizer": "globally_consistent",
          },
          "auc_per_slice": {
            "none": {"raw_auc": 0.63, "raw_auc_ci": [0.61, 0.65], ...},
            ...
          },
          "robust_across_slices": false,
          "registry_recommendation": "direction_by_comparator: ...",
        },
        ...
      ]
    }
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

# polarity_audit.py is in the same directory; same-package import.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import polarity_audit as pa  # type: ignore

TOOL_NAME = "cross_polarity_audit"
TOOL_VERSION = "v1.0.0"

DEFAULT_SLICE_BY = "adversarial_class"


# ----------------------------------------------------------------- Per-slice audit


def filter_rows_for_slice(
    rows: Iterable[dict[str, Any]],
    *,
    slice_by: str,
    slice_value: str,
) -> list[dict[str, Any]]:
    """Filter slicer-CSV rows to a single ``(slice_by, slice_value)`` subset.

    Returns the header cells (``slice_key == slice_by`` AND
    ``slice_value == slice_value``) — one per ``(model, signal)``
    combination. These cells carry the within-slice AUC + CI that the
    per-slice classifier consumes.

    The full corpus aggregate (``slice_key == "ALL"``) is NOT included
    in the filtered output — its AUC is computed over rows from all
    slice values, so it answers "does the registry hold on average"
    rather than "does it hold within this slice". For the
    direction_by_comparator question this tool exists to answer, the
    per-slice header cell is the right unit of evidence.
    """
    out: list[dict[str, Any]] = []
    for r in rows:
        if r["slice_key"] == slice_by and r["slice_value"] == slice_value:
            out.append(r)
    return out


def distinct_slice_values(
    rows: Iterable[dict[str, Any]], *, slice_by: str,
) -> list[str]:
    """Return the sorted distinct values of ``slice_by`` in ``rows``."""
    return sorted({
        r["slice_value"] for r in rows
        if r["slice_key"] == slice_by
    })


def classify_slice_cell(
    cell: dict[str, Any], registry_direction: str,
) -> str:
    """Direct per-slice verdict from a single header cell.

    Uses the same direction-aware bounds logic as
    ``polarity_audit.classify_cell``, applied to the slice header
    cell as both aggregate and only evidence:

    * ``da_lo > 0.5`` → ``globally_consistent`` (registry matches).
    * ``da_hi < 0.5`` → ``globally_inverted`` (registry opposite).
    * CI brackets 0.5 → ``chance`` (within this slice, the
      polarity question is undecidable at the available n).

    Why not delegate to ``polarity_audit.build_audit``: that
    function's verdict logic requires multi-cell evidence
    (``n_consistent + n_inverted >= 2``) or an aggregate CI that
    excludes 0.5. For a single-cell slice (the common case when
    the slicer has not been run with crosstabs involving the
    slicing dimension), both paths leave the verdict at
    ``chance`` regardless of how tight the slice cell's CI is.
    The direct classifier here makes the within-slice
    interpretation explicit: the slice's CI IS the evidence.
    """
    da_lo, da_hi = pa.to_direction_aware(
        float(cell["auc_lo"]), float(cell["auc_hi"]),
        registry_direction,
    )
    if da_lo > 0.5:
        return "globally_consistent"
    if da_hi < 0.5:
        return "globally_inverted"
    return "chance"


# ----------------------------------------------------------------- Cross-slice synthesis


def summarise_cross_slice(
    per_slice: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Per ``(model, signal)``, collect verdicts across slice values
    and flag robustness.

    ``robust_across_slices`` is True iff all slice values produce the
    same verdict. ``registry_recommendation`` describes the
    per-(model, signal) action:

    * If robust + ``globally_consistent`` everywhere → ``keep registry``.
    * If robust + ``globally_inverted`` everywhere → ``flip registry`` (one direction).
    * If non-robust + mix of verdicts → ``direction_by_comparator``.
    * If non-robust + all chance / mixed_noisy → ``inconclusive``.

    Input shape: ``per_slice`` is a list of ``{"slice_value": str,
    "results": [{"model": ..., "signal": ..., "registry_direction":
    ..., "verdict": ...}, ...]}`` dicts (the per-slice audit output).
    """
    by_ms: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    direction_by_ms: dict[tuple[str, str], str] = {}
    auc_by_ms: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for slice_block in per_slice:
        slice_value = slice_block["slice_value"]
        for r in slice_block["results"]:
            key = (r["model"], r["signal"])
            by_ms[key][slice_value] = r["verdict"]
            direction_by_ms[key] = r["registry_direction"]
            auc_by_ms[key][slice_value] = {
                "raw_auc": r.get("raw_auc"),
                "raw_auc_ci": r.get("raw_auc_ci"),
                "n_pos": r.get("n_pos"),
                "n_neg": r.get("n_neg"),
            }

    cross: list[dict[str, Any]] = []
    for (model, signal), per_slice_verdicts in sorted(by_ms.items()):
        verdict_set = set(per_slice_verdicts.values())
        robust = len(verdict_set) == 1
        registry_dir = direction_by_ms.get((model, signal), "gt")
        rec = _synthesise_recommendation(
            per_slice_verdicts, registry_dir,
        )
        cross.append({
            "model": model,
            "signal": signal,
            "registry_direction": registry_dir,
            "verdicts_per_slice": dict(per_slice_verdicts),
            "auc_per_slice": dict(auc_by_ms[(model, signal)]),
            "robust_across_slices": robust,
            "registry_recommendation": rec,
        })
    return cross


def _synthesise_recommendation(
    per_slice_verdicts: dict[str, str], registry_dir: str,
) -> str:
    """Per ``(model, signal)`` cross-slice recommendation.

    Pure-Python; no GPU. Mirrors the natural-language buckets of
    ``polarity_audit.polarity_recommendation`` but operates on the
    cross-slice view rather than a single verdict.
    """
    verdicts = list(per_slice_verdicts.values())
    distinct = set(verdicts)
    flipped = "lt" if registry_dir == "gt" else "gt"
    if distinct == {"globally_consistent"}:
        return f"keep registry direction {registry_dir!r}"
    if distinct == {"globally_inverted"}:
        return f"flip registry: {registry_dir!r} → {flipped!r}"
    if distinct <= {"chance", "mixed_noisy"}:
        return (
            "inconclusive: no slice produced a real-signal verdict; "
            "consider marking the signal experimental"
        )
    if "globally_consistent" in distinct and "globally_inverted" in distinct:
        consistent_slices = sorted(
            k for k, v in per_slice_verdicts.items()
            if v == "globally_consistent"
        )
        inverted_slices = sorted(
            k for k, v in per_slice_verdicts.items()
            if v == "globally_inverted"
        )
        return (
            f"direction_by_comparator: keep {registry_dir!r} on "
            f"{consistent_slices!r}; flip to {flipped!r} on "
            f"{inverted_slices!r}"
        )
    # Mixed including chance/mixed_noisy alongside one signed verdict.
    return (
        f"partially robust: {dict(per_slice_verdicts)!r}. Human review "
        "required before registry change."
    )


# ----------------------------------------------------------------- End-to-end


def build_cross_audit(
    rows: list[dict[str, Any]],
    *,
    slice_by: str = DEFAULT_SLICE_BY,
    comparator_key: str | None = None,
    registry_directions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a cross-slice polarity audit from slicer-CSV rows.

    For each distinct value V of ``slice_by``, find the per-
    ``(model, signal)`` header cell (``slice_key == slice_by AND
    slice_value == V``) and classify it directly via
    ``classify_slice_cell``. Then synthesise across slices.

    Top-level output shape per the module docstring.
    """
    if registry_directions is None:
        registry_directions = pa.DEFAULT_REGISTRY_DIRECTIONS

    slice_values = distinct_slice_values(rows, slice_by=slice_by)
    per_slice: list[dict[str, Any]] = []
    for sv in slice_values:
        subset = filter_rows_for_slice(
            rows, slice_by=slice_by, slice_value=sv,
        )
        slice_results: list[dict[str, Any]] = []
        for cell in subset:
            model = cell["model"]
            signal = cell["signal"]
            registry_dir = registry_directions.get(signal, "gt")
            verdict = classify_slice_cell(cell, registry_dir)
            slice_results.append({
                "model": model,
                "signal": signal,
                "registry_direction": registry_dir,
                "verdict": verdict,
                "raw_auc": float(cell["auc"]),
                "raw_auc_ci": [
                    float(cell["auc_lo"]),
                    float(cell["auc_hi"]),
                ],
                "n_pos": int(cell["n_pos"]),
                "n_neg": int(cell["n_neg"]),
            })
        per_slice.append({
            "slice_value": sv,
            "results": slice_results,
        })

    cross = summarise_cross_slice(per_slice)

    return {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "date": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "slice_by": slice_by,
        "slice_values": slice_values,
        "comparator_key": comparator_key,
        "per_slice": per_slice,
        "cross_summary": cross,
    }


# ----------------------------------------------------------------- Markdown


def render_cross_audit_markdown(cross_audit: dict[str, Any]) -> str:
    """Render the cross-slice audit as a markdown summary.

    Three sections:
      1. Header: tool / date / slice-by / observed slice values.
      2. Cross-slice summary table: per (model, signal), the verdict
         in each slice value + the registry recommendation.
      3. Robustness call-outs: signals whose verdicts differ across
         slices ("direction_by_comparator" candidates).
    """
    lines: list[str] = []
    lines.append(f"# cross_polarity_audit ({cross_audit['date']})")
    lines.append("")
    lines.append(f"- **slice_by**: `{cross_audit['slice_by']}`")
    lines.append(
        f"- **slice_values**: "
        f"{', '.join(f'`{v}`' for v in cross_audit['slice_values'])}"
    )
    comparator = cross_audit.get("comparator_key")
    if comparator:
        lines.append(f"- **comparator_key**: `{comparator}`")
    lines.append("")

    cross = cross_audit["cross_summary"]
    if not cross:
        lines.append("_No (model × signal) verdicts produced._")
        return "\n".join(lines) + "\n"

    # Section 2: full cross-slice table
    lines.append("## Cross-slice verdicts")
    lines.append("")
    slice_values = cross_audit["slice_values"]
    headers = ["model", "signal", "registry"] + slice_values + [
        "robust", "recommendation",
    ]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in cross:
        verdict_cells = [
            row["verdicts_per_slice"].get(sv, "—")
            for sv in slice_values
        ]
        lines.append(
            "| "
            + " | ".join([
                row["model"], row["signal"], row["registry_direction"],
                *verdict_cells,
                "yes" if row["robust_across_slices"] else "**no**",
                row["registry_recommendation"],
            ])
            + " |"
        )
    lines.append("")

    # Section 3: non-robust call-outs
    non_robust = [r for r in cross if not r["robust_across_slices"]]
    if non_robust:
        lines.append("## Non-robust signals (direction depends on slice)")
        lines.append("")
        for r in non_robust:
            lines.append(
                f"- **{r['model']} × {r['signal']}** "
                f"(registry `{r['registry_direction']}`): "
                f"{r['registry_recommendation']}"
            )
        lines.append("")
    else:
        lines.append(
            "_All signals were robust across all observed slice values "
            "(verdicts uniform). The single-slice polarity_audit "
            "result holds; no comparator-class routing needed._"
        )
        lines.append("")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------- CLI


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cross_polarity_audit.py",
        description=(
            "Per-slice polarity-audit verdicts + cross-slice "
            "synthesis. Reads a slice_bakeoff CSV that includes "
            "rows for the chosen slicing dimension (default "
            "adversarial_class), runs polarity_audit on each "
            "slice value separately, and reports whether the "
            "registry-direction recommendations from "
            "polarity_audit hold uniformly across slice values."
        ),
    )
    p.add_argument(
        "--input-csv", required=True,
        help="Path to slice_analysis.csv (v1 or v2 format).",
    )
    p.add_argument(
        "--slice-by", default=DEFAULT_SLICE_BY,
        help=(
            f"Slicing dimension to pivot on. Default "
            f"{DEFAULT_SLICE_BY!r}. Any slice_key the slicer wrote "
            f"into the CSV is accepted (length_bucket, register, "
            f"notes.original_source, etc.)."
        ),
    )
    p.add_argument(
        "--out-json", required=True,
        help="Write the cross-slice audit JSON to this path.",
    )
    p.add_argument(
        "--out-markdown", default=None,
        help=(
            "Optionally write a markdown summary to this path. "
            "Recommended for human readers."
        ),
    )
    p.add_argument(
        "--comparator-key", default=None,
        help=(
            "Comparator key for downstream consumers. Stored in the "
            "JSON output; does not affect computation here."
        ),
    )
    p.add_argument(
        "--registry-direction", action="append", default=[],
        metavar="SIGNAL=DIRECTION",
        help=(
            "Override registry direction for a signal "
            "(SIGNAL=gt|lt). Pass per signal to override; defaults "
            "fall back to polarity_audit.DEFAULT_REGISTRY_DIRECTIONS."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    csv_path = Path(args.input_csv).expanduser()
    if not csv_path.is_file():
        sys.stderr.write(f"Input CSV not found: {csv_path}\n")
        return 2

    rows = pa.load_slicer_csv(csv_path)
    overrides = pa.parse_registry_overrides(args.registry_direction)
    registry = {**pa.DEFAULT_REGISTRY_DIRECTIONS, **overrides}

    slice_values = distinct_slice_values(rows, slice_by=args.slice_by)
    if not slice_values:
        sys.stderr.write(
            f"No rows with slice_key={args.slice_by!r} in {csv_path}. "
            f"Available slice keys: "
            f"{sorted({r['slice_key'] for r in rows})}\n"
        )
        return 3

    cross_audit = build_cross_audit(
        rows,
        slice_by=args.slice_by,
        comparator_key=args.comparator_key,
        registry_directions=registry,
    )

    out_json = Path(args.out_json).expanduser()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps(cross_audit, indent=2, default=str, sort_keys=True),
        encoding="utf-8",
    )
    n_cross = len(cross_audit["cross_summary"])
    n_slices = len(cross_audit["slice_values"])
    sys.stderr.write(
        f"wrote {out_json}  "
        f"({n_cross} (model × signal) cross-slice rows across "
        f"{n_slices} slice values)\n"
    )

    if args.out_markdown:
        out_md = Path(args.out_markdown).expanduser()
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(
            render_cross_audit_markdown(cross_audit),
            encoding="utf-8",
        )
        sys.stderr.write(f"wrote {out_md}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
