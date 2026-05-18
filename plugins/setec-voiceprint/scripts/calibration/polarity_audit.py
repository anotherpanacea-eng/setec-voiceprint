#!/usr/bin/env python3
"""polarity_audit.py — comparator-aware sign verdict for SETEC bake-off slicer output.

Reads the per-cell AUC CSV produced by ``slice_bakeoff_v2.py`` (or the
v1 ``slice_bakeoff.py``) and emits a structured verdict per
``(model × signal)`` saying whether the framework's registry direction
(`gt`/`lt` from ``variance_audit.COMPRESSION_HEURISTICS``) is consistent
with the empirical sign of the discrimination, inverted, or
comparator-dependent.

Per ``SPEC_polarity_audit.md``. The audit produces evidence; the
framework owner decides whether to flip registry directions on the
basis of it. The audit does NOT modify ``variance_audit.py``; it does
NOT recalibrate thresholds.

Usage
-----

Run against an existing slicer CSV (v1 or v2 format)::

    python3 polarity_audit.py \\
        --input-csv  /path/to/slice_analysis.csv \\
        --out-json   /path/to/polarity_audit.json \\
        --comparator-key notes.original_source

CSV format detection
--------------------

v1 columns: ``corpus, model, signal, slice_key, slice_value, n_pos,
n_neg, auc, da_auc, abs_signal``.

v2 columns: v1 plus ``se, auc_lo, auc_hi, da_auc_lo, da_auc_hi,
abs_signal_lo, abs_signal_hi``.

When v1 columns are present without CIs, the audit computes
Hanley-McNeil approximate CIs on the fly from ``(auc, n_pos, n_neg)``.
This is the load-bearing affordance: the v1 slicer output bundled
with desktop sessions can be audited without re-slicing the cache.

Cell classification
-------------------

The classifier asks "does the registry direction match the empirical
direction in this cell?" The natural-language rule is symmetric across
registry directions: a cell is *consistent* when the registry direction
agrees with the discrimination, *inverted* when it disagrees, and
*chance* when the CI can't distinguish either way.

Mechanically, comparing raw AUC bounds against 0.5 only works for
``gt`` signals — for ``lt`` signals it flips every verdict, because
raw AUC > 0.5 on a registered-``lt`` signal means AI scored *higher*
than the registry expected (i.e., the registry direction is wrong).

The audit converts raw bounds to *direction-aware* bounds first via
``to_direction_aware(raw_lo, raw_hi, direction)``: identity for ``gt``,
``(1 - raw_hi, 1 - raw_lo)`` for ``lt``. The swap matters — it keeps the
CI's ``lo < hi`` ordering invariant under the gt-to-lt complementation.
With direction-aware bounds in hand, the classification rule is uniform
across both registry directions:

* ``consistent``: ``da_lo > 0.5`` — signal real, registry direction matches.
* ``inverted``: ``da_hi < 0.5`` — signal real, registry direction wrong.
* ``chance``: ``0.5 ∈ [da_lo, da_hi]`` — cannot distinguish from noise.

The natural-language descriptions in ``SPEC_polarity_audit.md`` follow
this direction-aware reading; the spec's worked example uses a ``gt``
signal, which made the direction-aware step easy to miss when reading
the criteria as if they were a literal raw-AUC formula.

Verdict per (model, signal)
---------------------------

Aggregated cell classifications + the aggregate-AUC CI determine the
verdict; recommended direction follows from the verdict.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

AUDIT_VERSION = "polarity-v1"
TOOL_NAME = "polarity_audit"
TOOL_VERSION = "v1.0.0"

# Registry directions per ``variance_audit.COMPRESSION_HEURISTICS``.
# Hardcoded here for self-containment; if the registry directions
# change in variance_audit.py, this table moves in lockstep. Keep
# in sync with ``slice_bakeoff_v2.SIGNAL_SPECS``.
DEFAULT_REGISTRY_DIRECTIONS: dict[str, str] = {
    "adjacent_cosine_mean": "gt",
    "adjacent_cosine_sd": "lt",
    "surprisal_mean": "lt",
    "surprisal_sd": "lt",
    "surprisal_acf_lag1": "gt",
}


# ----------------------------------------------------------------- CI math


def hanley_mcneil_se(auc: float, n_pos: int, n_neg: int) -> float:
    """Hanley-McNeil approximate standard error of an AUC.

    SE² = (AUC(1-AUC) + (n_p-1)(Q1 - AUC²) + (n_n-1)(Q2 - AUC²)) / (n_p·n_n)
    Q1 = AUC / (2 - AUC)
    Q2 = 2·AUC² / (1 + AUC)

    Per Hanley & McNeil (1982). The same formula as in
    ``slice_bakeoff_v2.hanley_mcneil_se``; duplicated here so the
    polarity audit can run standalone against v1 CSVs without
    importing the slicer.
    """
    if n_pos <= 0 or n_neg <= 0:
        return float("nan")
    q1 = auc / (2.0 - auc) if auc < 2.0 else 0.0
    q2 = 2.0 * auc * auc / (1.0 + auc) if auc > -1.0 else 0.0
    var = (
        auc * (1.0 - auc)
        + (n_pos - 1) * (q1 - auc * auc)
        + (n_neg - 1) * (q2 - auc * auc)
    ) / (n_pos * n_neg)
    if var < 0.0:
        var = 0.0
    return math.sqrt(var)


def ci95(auc: float, se: float) -> tuple[float, float]:
    """Normal-approximation 95% CI on AUC, clipped to [0, 1]."""
    lo = max(0.0, auc - 1.96 * se)
    hi = min(1.0, auc + 1.96 * se)
    return lo, hi


# ----------------------------------------------------------------- Classification


def to_direction_aware(
    raw_lo: float, raw_hi: float, direction: str,
) -> tuple[float, float]:
    """Convert raw AUC CI bounds to direction-aware bounds.

    For ``gt`` signals the registry expects AI > human, so raw AUC > 0.5
    is the matching direction; direction-aware bounds are the raw bounds
    unchanged. For ``lt`` signals the registry expects AI < human, so
    raw AUC < 0.5 is the matching direction; direction-aware bounds are
    ``(1 - raw_hi, 1 - raw_lo)`` (note the swap: the upper bound on
    raw AUC becomes the *lower* bound on direction-aware AUC).

    With this transform, ``da > 0.5`` always means "registry direction
    matches reality" and ``da < 0.5`` always means "registry direction
    is opposite to reality", regardless of whether the signal is
    registered as gt or lt. The classifier downstream can then use a
    single direction-agnostic rule (da > 0.5 → consistent, da < 0.5 →
    inverted) instead of branching on direction.
    """
    if direction == "gt":
        return raw_lo, raw_hi
    return 1.0 - raw_hi, 1.0 - raw_lo


def classify_cell(da_auc_lo: float, da_auc_hi: float) -> str:
    """Per-cell sign classification, on *direction-aware* AUC bounds.

    Callers should pass bounds produced by ``to_direction_aware()``,
    not raw AUC bounds — raw bounds would invert every verdict for
    ``lt``-registered signals.

    ``consistent``: ``da_auc_lo > 0.5`` — signal real, registry direction
    matches the comparator.
    ``inverted``: ``da_auc_hi < 0.5`` — signal real, registry direction
    is opposite to the comparator.
    ``chance``: ``0.5 ∈ [da_auc_lo, da_auc_hi]`` — CI contains 0.5;
    cannot distinguish from noise.
    """
    if da_auc_lo > 0.5:
        return "consistent"
    if da_auc_hi < 0.5:
        return "inverted"
    return "chance"


def polarity_verdict(
    cell_classifications: list[str],
    da_aggregate_auc: float,
    da_aggregate_se: float,
) -> str:
    """Per ``SPEC_polarity_audit.md`` verdict table.

    Aggregate inputs are *direction-aware*: pass ``raw_auc`` for gt
    signals and ``1 - raw_auc`` for lt signals; ``aggregate_se`` is
    invariant under that transform (variance of ``1 - X`` equals
    variance of ``X``). ``build_audit`` handles the conversion before
    calling this.
    """
    n_consistent = cell_classifications.count("consistent")
    n_inverted = cell_classifications.count("inverted")
    agg_lo, agg_hi = ci95(da_aggregate_auc, da_aggregate_se)
    if n_inverted == 0 and (agg_lo > 0.5 or n_consistent >= 3):
        return "globally_consistent"
    if n_consistent == 0 and (agg_hi < 0.5 or n_inverted >= 3):
        return "globally_inverted"
    if n_inverted >= 2 and n_consistent >= 2:
        return "comparator_dependent"
    if n_consistent + n_inverted < 2:
        return "chance"
    return "mixed_noisy"


def polarity_recommendation(
    verdict: str,
    registry_direction: str,
    aggregate_auc: float,
    aggregate_lo: float,
    aggregate_hi: float,
) -> dict[str, Any]:
    """Per ``SPEC_polarity_audit.md`` recommendation table."""
    if verdict == "globally_consistent":
        return {
            "default": registry_direction,
            "rationale": (
                f"Aggregate raw AUC {aggregate_auc:.4f} "
                f"95% CI [{aggregate_lo:.3f}, {aggregate_hi:.3f}] is "
                f"consistent with registry direction {registry_direction!r}; "
                "no change recommended."
            ),
        }
    if verdict == "globally_inverted":
        flipped = "lt" if registry_direction == "gt" else "gt"
        return {
            "default": flipped,
            "rationale": (
                f"Aggregate raw AUC {aggregate_auc:.4f} 95% CI "
                f"[{aggregate_lo:.3f}, {aggregate_hi:.3f}] excludes 0.5 in "
                f"the direction opposite to the registry's "
                f"{registry_direction!r}. Flip recommended; human review "
                "required before shipping."
            ),
        }
    if verdict == "comparator_dependent":
        return {
            "default": None,
            "rationale": (
                "Both consistent and inverted cells present in non-trivial "
                "counts. Direction depends on comparator class. Recommend "
                "encoding `direction_by_comparator` per the active "
                "comparator key; consult the per-comparator-class breakdown "
                "in slice_analysis.md before deciding."
            ),
        }
    if verdict == "chance":
        return {
            "default": registry_direction,
            "rationale": (
                "Almost all cells in the chance class; the signal does not "
                "discriminate at the available n. No registry change; "
                "consider marking the signal `experimental`."
            ),
        }
    # mixed_noisy
    return {
        "default": registry_direction,
        "rationale": (
            "Some cells in both directions, but neither side is strong "
            "enough to call comparator-dependent. Recommend collecting "
            "more data before modifying the registry."
        ),
    }


# ----------------------------------------------------------------- CSV ingest


def parse_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load_slicer_csv(path: Path) -> list[dict[str, Any]]:
    """Load a slicer CSV (v1 or v2 format) and normalize the rows.

    Computes Hanley-McNeil CIs on the fly when v2 CI columns aren't
    present. Returns a list of dicts with at minimum:
    ``corpus, model, signal, slice_key, slice_value, n_pos, n_neg,
    auc, da_auc, auc_lo, auc_hi``.
    """
    out: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            n_pos = parse_int(row.get("n_pos"))
            n_neg = parse_int(row.get("n_neg"))
            auc = parse_float(row.get("auc"))
            da = parse_float(row.get("da_auc"))
            if n_pos is None or n_neg is None or auc is None:
                continue
            auc_lo = parse_float(row.get("auc_lo"))
            auc_hi = parse_float(row.get("auc_hi"))
            se = parse_float(row.get("se"))
            if auc_lo is None or auc_hi is None or se is None:
                # v1 CSV — compute CIs on the fly.
                se = hanley_mcneil_se(auc, n_pos, n_neg)
                auc_lo, auc_hi = ci95(auc, se)
            out.append({
                "corpus": row.get("corpus", ""),
                "model": row.get("model", ""),
                "signal": row.get("signal", ""),
                "slice_key": row.get("slice_key", ""),
                "slice_value": row.get("slice_value", ""),
                "n_pos": n_pos,
                "n_neg": n_neg,
                "auc": auc,
                "da_auc": da if da is not None else auc,
                "se": se,
                "auc_lo": auc_lo,
                "auc_hi": auc_hi,
            })
    return out


# ----------------------------------------------------------------- Audit


def build_audit(
    rows: Iterable[dict[str, Any]],
    *,
    registry_directions: dict[str, str] | None = None,
    comparator_key: str | None = None,
) -> dict[str, Any]:
    """Produce the polarity-audit JSON from slicer rows.

    Per ``SPEC_polarity_audit.md`` output shape. The rows are the
    output of ``load_slicer_csv`` (CIs already populated).
    """
    if registry_directions is None:
        registry_directions = DEFAULT_REGISTRY_DIRECTIONS

    by_model_signal: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    aggregate_by_model_signal: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        key = (r["model"], r["signal"])
        if r["slice_key"] == "ALL":
            aggregate_by_model_signal[key] = r
        else:
            by_model_signal[key].append(r)

    results: list[dict[str, Any]] = []
    for (model, signal), cells in sorted(by_model_signal.items()):
        agg = aggregate_by_model_signal.get((model, signal))
        if agg is None:
            # No aggregate row — can't compute verdict per spec.
            continue
        registry_dir = registry_directions.get(signal, "gt")
        # Direction-aware classification: convert raw CI bounds to
        # direction-aware bounds so the consistent/inverted/chance
        # rule applies uniformly to gt and lt signals. Without this
        # step, lt signals classify backwards — raw AUC > 0.5 on an
        # lt-registered signal means AI scored higher than the
        # registry expected (i.e., registry direction is wrong),
        # but a raw-bounds-only classifier would call it consistent.
        classifications = []
        for c in cells:
            da_lo, da_hi = to_direction_aware(
                float(c["auc_lo"]), float(c["auc_hi"]), registry_dir,
            )
            classifications.append(classify_cell(da_lo, da_hi))
        raw_agg_auc = float(agg["auc"])
        raw_agg_se = float(agg["se"])
        # For aggregate AUC, the SE is invariant under the gt↔lt
        # transform (variance of (1 - X) equals variance of X), but
        # the point estimate flips for lt: da_auc = 1 - raw for lt,
        # da_auc = raw for gt.
        da_agg_auc = (
            raw_agg_auc if registry_dir == "gt" else 1.0 - raw_agg_auc
        )
        verdict = polarity_verdict(
            classifications, da_agg_auc, raw_agg_se,
        )
        raw_agg_lo, raw_agg_hi = ci95(raw_agg_auc, raw_agg_se)
        recommendation = polarity_recommendation(
            verdict, registry_dir,
            raw_agg_auc, raw_agg_lo, raw_agg_hi,
        )
        results.append({
            "model": model,
            "signal": signal,
            "registry_direction": registry_dir,
            "n_cells_total": len(cells),
            "n_cells_consistent": classifications.count("consistent"),
            "n_cells_inverted": classifications.count("inverted"),
            "n_cells_chance": classifications.count("chance"),
            "aggregate_raw_auc": raw_agg_auc,
            "aggregate_raw_auc_ci": [raw_agg_lo, raw_agg_hi],
            "verdict": verdict,
            "recommended_direction": recommendation,
        })

    audit: dict[str, Any] = {
        "audit_version": AUDIT_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "date": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "results": results,
    }
    if comparator_key:
        audit["comparator_key"] = comparator_key
    return audit


# ----------------------------------------------------------------- Markdown summary


def render_audit_markdown(audit: dict[str, Any]) -> str:
    """Render a human-readable summary of the audit JSON."""
    lines = [
        "# Polarity audit",
        "",
        f"**Audit version:** {audit.get('audit_version', '?')}  ",
        f"**Date:** {audit.get('date', '?')}  ",
        f"**Tool:** {audit.get('tool', '?')} {audit.get('tool_version', '?')}",
        "",
    ]
    if audit.get("comparator_key"):
        lines.extend([
            f"**Comparator key:** `{audit['comparator_key']}`",
            "",
        ])
    lines.extend([
        "## Verdicts per (model × signal)",
        "",
        "| model | signal | registry | verdict | recommended | "
        "n_cons | n_inv | n_chance | aggregate AUC (CI) |",
        "|---|---|---|---|---|---|---|---|---|",
    ])
    for r in audit.get("results", []):
        rec_default = r.get("recommended_direction", {}).get("default")
        rec_str = "—" if rec_default is None else str(rec_default)
        agg_auc = r.get("aggregate_raw_auc", 0.0)
        agg_lo, agg_hi = r.get("aggregate_raw_auc_ci", [0.0, 1.0])
        lines.append(
            f"| {r['model']} | {r['signal']} | "
            f"{r['registry_direction']} | **{r['verdict']}** | "
            f"{rec_str} | {r['n_cells_consistent']} | "
            f"{r['n_cells_inverted']} | {r['n_cells_chance']} | "
            f"{agg_auc:.4f} [{agg_lo:.3f}, {agg_hi:.3f}] |"
        )
    lines.append("")
    lines.append("## Actions to take on the registry")
    lines.append("")
    flip_recommendations = [
        r for r in audit.get("results", [])
        if r["verdict"] == "globally_inverted"
    ]
    comparator_dependent = [
        r for r in audit.get("results", [])
        if r["verdict"] == "comparator_dependent"
    ]
    if flip_recommendations:
        lines.append("### Flip registry direction (globally_inverted)")
        lines.append("")
        for r in flip_recommendations:
            lines.append(
                f"- `{r['signal']}` on `{r['model']}`: "
                f"flip {r['registry_direction']!r} → "
                f"{r['recommended_direction']['default']!r}. "
                f"{r['recommended_direction']['rationale']}"
            )
        lines.append("")
    if comparator_dependent:
        lines.append("### Encode direction_by_comparator (comparator_dependent)")
        lines.append("")
        for r in comparator_dependent:
            lines.append(
                f"- `{r['signal']}` on `{r['model']}`: "
                f"both consistent and inverted cells present; "
                "encode comparator-dependent direction per the slice "
                "breakdown."
            )
        lines.append("")
    if not flip_recommendations and not comparator_dependent:
        lines.append("No registry changes recommended at the available n.")
        lines.append("")
    lines.append("## Caveats")
    lines.append("")
    lines.append(
        "- This audit produces *evidence*, not adjudication. The "
        "framework owner decides whether to flip registry directions "
        "on the basis of these verdicts and the surrounding "
        "slice_analysis.md breakdown."
    )
    lines.append(
        "- CIs are Hanley-McNeil normal approximations. At small cell n "
        "the approximation is generous; treat as smoke-test rigour, not "
        "publication-grade."
    )
    lines.append(
        "- A `globally_inverted` verdict only says the registry's "
        "current direction is inconsistent with the observed "
        "comparator. Validating that the *flipped* direction is "
        "correct against the literature is a separate human step."
    )
    return "\n".join(lines)


# ----------------------------------------------------------------- CLI


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="polarity_audit",
        description=(
            "Read a slice_bakeoff CSV (v1 or v2) and emit a per "
            "(model × signal) polarity-audit verdict + recommended "
            "registry direction. Pure-Python; no GPU required."
        ),
    )
    p.add_argument(
        "--input-csv", required=True, type=Path,
        help="Path to the slicer CSV (slice_analysis.csv from "
             "slice_bakeoff_v2 or v1).",
    )
    p.add_argument(
        "--out-json", required=True, type=Path,
        help="Where to write the structured audit JSON.",
    )
    p.add_argument(
        "--out-markdown", default=None, type=Path,
        help="Optional: where to write a human-readable markdown "
             "summary. If omitted, no markdown is written.",
    )
    p.add_argument(
        "--comparator-key", default=None,
        help="Slice key whose values name comparator classes. Used "
             "by recommendations on comparator-dependent verdicts. "
             "Typical: 'notes.original_source' (MAGE), 'notes.domain' "
             "(RAID).",
    )
    p.add_argument(
        "--registry-direction", action="append", default=[],
        help=(
            "Override a signal's registry direction. Format: "
            "'signal=direction', e.g., 'adjacent_cosine_mean=gt'. Can "
            "be passed multiple times. Defaults to "
            "DEFAULT_REGISTRY_DIRECTIONS."
        ),
    )
    return p


def parse_registry_overrides(items: list[str]) -> dict[str, str]:
    out = dict(DEFAULT_REGISTRY_DIRECTIONS)
    for item in items:
        if "=" not in item:
            print(
                f"warning: ignoring --registry-direction {item!r} "
                "(expected signal=direction)",
                file=sys.stderr,
            )
            continue
        sig, direction = item.split("=", 1)
        sig = sig.strip()
        direction = direction.strip()
        if direction not in ("gt", "lt"):
            print(
                f"warning: ignoring --registry-direction {item!r} "
                f"(direction must be 'gt' or 'lt', got {direction!r})",
                file=sys.stderr,
            )
            continue
        out[sig] = direction
    return out


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if not args.input_csv.exists():
        print(
            f"error: --input-csv not found: {args.input_csv}",
            file=sys.stderr,
        )
        return 2
    rows = load_slicer_csv(args.input_csv)
    if not rows:
        print(
            f"error: --input-csv {args.input_csv} contains no usable "
            "rows (missing n_pos, n_neg, or auc columns?)",
            file=sys.stderr,
        )
        return 2
    registry = parse_registry_overrides(args.registry_direction)
    audit = build_audit(
        rows,
        registry_directions=registry,
        comparator_key=args.comparator_key,
    )
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(
        json.dumps(audit, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(
        f"wrote {args.out_json}  "
        f"({len(audit.get('results', []))} (model × signal) verdicts)"
    )
    if args.out_markdown:
        args.out_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.out_markdown.write_text(
            render_audit_markdown(audit),
            encoding="utf-8",
        )
        print(f"wrote {args.out_markdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
