#!/usr/bin/env python3
"""narrative_polarity_audit.py — cross-corpus polarity check for the
narrative-decision audit (Surface 6).

Mirrors the workflow that produced
``references/calibration-findings-2026-05-10.md`` and
``calibration-findings-2026-05-11-mage.md`` for Tier-1 variance
signals, but applied to the 33 per-signal contributions of the
Russell et al. 2026 (StoryScope) feature schema.

The question this script answers
--------------------------------

For each of the 33 paper-anchored signals, does the *polarity*
reported in the paper (human-leaning vs. AI-leaning, derived from
the sign of ``human_mean - ai_mean``) hold on the operator's
corpus, or does it invert? This matters because the paper's home
register is long-form fiction (mean 4,753 words, Books3-derived
corpus); SETEC operators will routinely run the audit on essays,
op-eds, novels, and translations, where one or more features may
be uncomputable or carry the opposite sign.

What it consumes
----------------

A JSONL manifest in which each row carries:

    {
      "text_id": str,                       # arbitrary stable id
      "label": "ai_generated" | "pre_ai_human" | ...,
      "narrative_values": { ... 30 keys ... },  # judge output (clean)
    }

The manifest is produced *outside* this script. The recommended
workflow: run ``narrative_decision_audit.py`` against each story
in the labeled corpus with ``--judge`` set to whatever model the
operator has chosen, then collect the per-story ``results.values``
into the manifest. This separation keeps the LLM-judge cost
external to the polarity audit, which is pure Python.

What it emits
-------------

A JSON report and a markdown findings document that mirror the
existing calibration-findings format:

  - per-signal direction-aware AUC against the operator's labels;
  - polarity verdict: ``matches``, ``inverted``, or ``chance``;
  - aggregate AUC for the literature-anchored scorer;
  - a section identifying which signals invert and (where
    available) the corpus characteristic that explains them.

Like the existing polarity audit, this script produces evidence,
not a calibration. It does not modify the schema's reported
polarity. Inversion findings are recorded so operators running
the surface on the same kind of corpus know which signals to
treat as suspect.

Usage
-----

    python3 narrative_polarity_audit.py \\
        --manifest path/to/judged.jsonl \\
        --out-json polarity.json \\
        --out-md polarity.md \\
        --corpus-name "EditLens val (essays, 2026-05-10)" \\
        --human-label pre_ai_human \\
        --ai-label ai_generated
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# narrative_decision_audit and its helpers live one directory up.
SCRIPT_DIR = Path(__file__).resolve().parent
PARENT_DIR = SCRIPT_DIR.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

from narrative_decision_audit import (  # type: ignore  # noqa: E402
    per_signal_contributions,
)
from narrative_feature_schema import (  # type: ignore  # noqa: E402
    BUNDLE_LABELS,
    CORE_FEATURES,
)
from narrative_judge import validate_values  # type: ignore  # noqa: E402


__all__ = [
    "Row",
    "load_manifest",
    "auc_mannwhitney",
    "hanley_mcneil_se",
    "polarity_verdict",
    "build_report",
]


# ---------- IO ------------------------------------------------------

@dataclass
class Row:
    text_id: str
    label: str  # "human" | "ai" (after the relabel pass)
    raw_label: str
    values: dict[str, Any]


def load_manifest(
    path: Path,
    *,
    human_label: str,
    ai_label: str,
) -> tuple[list[Row], dict[str, int]]:
    rows: list[Row] = []
    counts = {"loaded": 0, "skipped": 0, "validation_warnings": 0}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                counts["skipped"] += 1
                continue
            label = obj.get("label")
            if label == human_label:
                relabel = "human"
            elif label == ai_label:
                relabel = "ai"
            else:
                counts["skipped"] += 1
                continue
            values = obj.get("narrative_values") or obj.get("values") or {}
            if not isinstance(values, dict):
                counts["skipped"] += 1
                continue
            cleaned, warnings = validate_values(values)
            if warnings:
                counts["validation_warnings"] += len(warnings)
            rows.append(Row(
                text_id=str(obj.get("text_id") or len(rows)),
                label=relabel,
                raw_label=str(label),
                values=cleaned,
            ))
            counts["loaded"] += 1
    return rows, counts


# ---------- AUC helpers --------------------------------------------

def auc_mannwhitney(
    pos_scores: list[float],
    neg_scores: list[float],
) -> float | None:
    """Mann-Whitney U exact AUC.

    Treats ``pos_scores`` as the positive class (higher = positive).
    Ties contribute 0.5. Returns None when either class is empty.
    """
    if not pos_scores or not neg_scores:
        return None
    wins = 0.0
    for p in pos_scores:
        for n in neg_scores:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(pos_scores) * len(neg_scores))


def hanley_mcneil_se(
    auc: float, n_p: int, n_n: int,
) -> float:
    """Approximate SE on AUC (Hanley & McNeil 1982).

    Matches the existing ``polarity_audit.hanley_mcneil_se`` and is
    re-implemented here so this script has no dependency on the
    Tier-1 polarity audit's module.
    """
    if n_p <= 0 or n_n <= 0:
        return float("nan")
    q1 = auc / (2.0 - auc)
    q2 = 2.0 * auc * auc / (1.0 + auc)
    numer = (
        auc * (1 - auc)
        + (n_p - 1) * (q1 - auc * auc)
        + (n_n - 1) * (q2 - auc * auc)
    )
    var = numer / (n_p * n_n)
    return math.sqrt(max(var, 0.0))


def direction_aware_auc(
    raw_auc: float,
    paper_leaning: str,
) -> float:
    """Flip raw AUC so ≥0.5 means polarity matches the paper.

    ``raw_auc`` here is "P(positive_score > negative_score)" with
    label=ai being positive. The paper's leaning tells us which
    direction is *expected*:
      - paper_leaning=="ai": AI should produce HIGHER values for
        this signal's option/value. raw_auc as computed already
        treats ai-as-positive; da_auc = raw_auc.
      - paper_leaning=="human": HUMAN should produce HIGHER values.
        da_auc = 1 - raw_auc.
    """
    if paper_leaning == "ai":
        return raw_auc
    return 1.0 - raw_auc


def polarity_verdict(
    da_auc: float,
    se: float,
    *,
    z_chance: float = 1.96,
) -> str:
    """Direction-aware classification.

    Returns one of ``matches`` / ``inverted`` / ``chance`` based on
    whether the 95% Wald CI on da_AUC clears 0.5 in either
    direction.
    """
    lo = da_auc - z_chance * se
    hi = da_auc + z_chance * se
    if lo > 0.5:
        return "matches"
    if hi < 0.5:
        return "inverted"
    return "chance"


# ---------- per-signal computation ---------------------------------

@dataclass
class SignalCell:
    feature_key: str
    feature_label: str
    bundle: str
    option: str | None
    paper_leaning: str
    paper_human_mean: float
    paper_ai_mean: float
    n_pos: int  # ai
    n_neg: int  # human
    raw_auc: float | None
    da_auc: float | None
    se: float | None
    verdict: str
    notes: list[str] = field(default_factory=list)


def per_signal_polarity(
    rows: list[Row],
    *,
    min_class_n: int = 20,
) -> list[SignalCell]:
    """Compute per-signal direction-aware AUC + polarity verdict.

    Cells with fewer than ``min_class_n`` rows in either class get
    verdict ``chance`` regardless of where the (degenerate) CI lands,
    because Hanley-McNeil SE collapses to zero on perfect separation
    in tiny samples and would otherwise emit a spuriously confident
    ``matches`` / ``inverted`` label. The note still records the
    actual da_AUC and the under-floor sample sizes so operators can
    see the underlying numbers.
    """
    out: list[SignalCell] = []
    by_label = {"human": [], "ai": []}
    for r in rows:
        by_label[r.label].append(r)
    for feat in CORE_FEATURES:
        for sig in feat.signals:
            # Per-row signal target-value (numeric). Reuses the same
            # encoding the per-doc audit uses.
            pos_scores: list[float] = []  # ai
            neg_scores: list[float] = []  # human
            for r in by_label["ai"]:
                contribs = per_signal_contributions(r.values)
                for c in contribs:
                    if c.feature_key == feat.key and c.option == sig.option:
                        if c.target_value is not None:
                            pos_scores.append(c.target_value)
                        break
            for r in by_label["human"]:
                contribs = per_signal_contributions(r.values)
                for c in contribs:
                    if c.feature_key == feat.key and c.option == sig.option:
                        if c.target_value is not None:
                            neg_scores.append(c.target_value)
                        break
            n_pos = len(pos_scores)
            n_neg = len(neg_scores)
            raw_auc = auc_mannwhitney(pos_scores, neg_scores)
            notes: list[str] = []
            if raw_auc is None:
                out.append(SignalCell(
                    feature_key=feat.key,
                    feature_label=feat.label,
                    bundle=sig.bundle,
                    option=sig.option,
                    paper_leaning=sig.leaning,
                    paper_human_mean=sig.human_mean,
                    paper_ai_mean=sig.ai_mean,
                    n_pos=n_pos,
                    n_neg=n_neg,
                    raw_auc=None,
                    da_auc=None,
                    se=None,
                    verdict="unavailable",
                    notes=notes + ["no scored stories in one class"],
                ))
                continue
            da = direction_aware_auc(raw_auc, sig.leaning)
            se = hanley_mcneil_se(da, n_pos, n_neg)
            if n_pos < min_class_n or n_neg < min_class_n:
                verdict = "chance"
                notes.append(
                    f"below min_class_n={min_class_n} (n_ai={n_pos}, "
                    f"n_human={n_neg}); verdict forced to chance to "
                    f"avoid spurious confidence from "
                    f"Hanley-McNeil SE collapse on small samples; "
                    f"raw da_AUC={da:.3f}"
                )
            else:
                verdict = polarity_verdict(da, se)
            out.append(SignalCell(
                feature_key=feat.key,
                feature_label=feat.label,
                bundle=sig.bundle,
                option=sig.option,
                paper_leaning=sig.leaning,
                paper_human_mean=sig.human_mean,
                paper_ai_mean=sig.ai_mean,
                n_pos=n_pos,
                n_neg=n_neg,
                raw_auc=raw_auc,
                da_auc=da,
                se=se,
                verdict=verdict,
                notes=notes,
            ))
    return out


def aggregate_scorer_auc(rows: list[Row]) -> dict[str, Any]:
    pos_scores: list[float] = []
    neg_scores: list[float] = []
    for r in rows:
        contribs = per_signal_contributions(r.values)
        ev = [c.contribution for c in contribs if c.contribution is not None]
        if not ev:
            continue
        score = sum(ev) / len(ev)
        if r.label == "ai":
            pos_scores.append(-score)  # lower score = more AI-like
        else:
            neg_scores.append(-score)
    auc = auc_mannwhitney(pos_scores, neg_scores)
    if auc is None:
        return {
            "auc": None, "se": None, "n_pos": 0, "n_neg": 0,
        }
    se = hanley_mcneil_se(auc, len(pos_scores), len(neg_scores))
    return {
        "auc": auc,
        "se": se,
        "n_pos": len(pos_scores),
        "n_neg": len(neg_scores),
    }


# ---------- report rendering ---------------------------------------

def build_report(
    *,
    corpus_name: str,
    manifest_path: Path,
    rows: list[Row],
    counts: dict[str, int],
    cells: list[SignalCell],
    aggregate: dict[str, Any],
    min_class_n: int = 20,
) -> dict[str, Any]:
    by_verdict: dict[str, list[SignalCell]] = {
        "matches": [], "inverted": [], "chance": [], "unavailable": [],
    }
    for c in cells:
        by_verdict[c.verdict].append(c)
    return {
        "corpus_name": corpus_name,
        "manifest_path": str(manifest_path),
        "min_class_n": min_class_n,
        "n_rows": {
            "human": sum(1 for r in rows if r.label == "human"),
            "ai": sum(1 for r in rows if r.label == "ai"),
            "loaded": counts["loaded"],
            "skipped": counts["skipped"],
        },
        "validation_warning_count": counts["validation_warnings"],
        "aggregate_scorer": aggregate,
        "signal_summary": {
            "matches": len(by_verdict["matches"]),
            "inverted": len(by_verdict["inverted"]),
            "chance": len(by_verdict["chance"]),
            "unavailable": len(by_verdict["unavailable"]),
        },
        "cells": [
            {
                "feature_key": c.feature_key,
                "feature_label": c.feature_label,
                "bundle": c.bundle,
                "option": c.option,
                "paper_leaning": c.paper_leaning,
                "paper_human_mean": c.paper_human_mean,
                "paper_ai_mean": c.paper_ai_mean,
                "n_pos": c.n_pos,
                "n_neg": c.n_neg,
                "raw_auc": c.raw_auc,
                "da_auc": c.da_auc,
                "se": c.se,
                "verdict": c.verdict,
                "notes": list(c.notes),
            }
            for c in cells
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(
        f"# Calibration findings: {report['corpus_name']} — "
        f"narrative-decision polarity check"
    )
    lines.append("")
    lines.append(
        "Mirrors the cross-corpus polarity workflow that produced "
        "the 2026-05-10 EditLens and 2026-05-11 MAGE findings for "
        "Tier-1 variance signals, but for the 33 per-signal "
        "contributions of the Russell et al. 2026 (StoryScope) "
        "feature schema."
    )
    lines.append("")
    lines.append("## Headline")
    lines.append("")
    nr = report["n_rows"]
    lines.append(
        f"- Loaded: {nr['loaded']:,} rows "
        f"({nr['human']:,} human, {nr['ai']:,} AI), "
        f"{nr['skipped']:,} skipped."
    )
    summ = report["signal_summary"]
    total = sum(summ.values())
    lines.append(
        f"- Per-signal polarity: "
        f"{summ['matches']}/{total} match paper, "
        f"{summ['inverted']}/{total} inverted, "
        f"{summ['chance']}/{total} chance-band, "
        f"{summ['unavailable']}/{total} unavailable."
    )
    agg = report["aggregate_scorer"]
    if agg.get("auc") is not None:
        lines.append(
            f"- Aggregate literature-anchored scorer AUC: "
            f"{agg['auc']:.3f} (n_ai={agg['n_pos']}, "
            f"n_human={agg['n_neg']}, SE={agg['se']:.3f})."
        )
    lines.append("")
    lines.append("## What this corpus looks like")
    lines.append("")
    lines.append(
        f"- Manifest: `{report['manifest_path']}`"
    )
    lines.append("")
    lines.append("## Per-signal direction-aware AUC")
    lines.append("")
    lines.append(
        "Direction-aware AUC ≥ 0.5 means the polarity matches the "
        "paper's reported direction; < 0.5 means it inverts; the "
        "verdict reports whether the 95% Wald CI clears 0.5."
    )
    lines.append("")
    lines.append(
        "| Feature | Option | Paper leaning | n_ai | n_human "
        "| da_AUC | SE | Verdict |"
    )
    lines.append(
        "|---|---|:--:|---:|---:|---:|---:|:--|"
    )
    cells = sorted(
        report["cells"],
        key=lambda c: (
            c["verdict"],
            -(c["da_auc"] if c["da_auc"] is not None else 0.5),
        ),
    )
    for c in cells:
        opt = c["option"] or "(numeric)"
        da = (
            f"{c['da_auc']:.3f}"
            if c["da_auc"] is not None else "—"
        )
        se = f"{c['se']:.3f}" if c["se"] is not None else "—"
        lines.append(
            f"| {c['feature_label']} | `{opt}` | "
            f"{c['paper_leaning']} | {c['n_pos']} | {c['n_neg']} | "
            f"{da} | {se} | **{c['verdict']}** |"
        )
    lines.append("")
    lines.append("## Inverted signals")
    lines.append("")
    inverted = [c for c in report["cells"] if c["verdict"] == "inverted"]
    if not inverted:
        lines.append("(none)")
    else:
        lines.append(
            "These signals' polarity is the opposite of the paper's "
            "on this corpus. Possible causes: corpus-specific "
            "register, comparator-side compression (e.g., ESL "
            "writing as in the 2026-05-10 EditLens finding), or "
            "an LLM-judge mode collapse on the signal. Operators "
            "running the audit on prose in this register should "
            "record the inversion in their claim_license caveats."
        )
        lines.append("")
        for c in inverted:
            opt = c["option"] or "(numeric)"
            lines.append(
                f"- **{c['feature_label']}** "
                f"(option `{opt}`, paper says "
                f"{c['paper_leaning']}-elevated, observed "
                f"da_AUC {c['da_auc']:.3f})"
            )
    lines.append("")
    lines.append("## Per-bundle polarity")
    lines.append("")
    by_bundle: dict[str, list[dict[str, Any]]] = {}
    for c in report["cells"]:
        by_bundle.setdefault(c["bundle"], []).append(c)
    lines.append(
        "| Bundle | Match | Invert | Chance | Unavailable |"
    )
    lines.append("|---|---:|---:|---:|---:|")
    for bundle, label in BUNDLE_LABELS.items():
        cs = by_bundle.get(bundle, [])
        m = sum(1 for c in cs if c["verdict"] == "matches")
        inv = sum(1 for c in cs if c["verdict"] == "inverted")
        ch = sum(1 for c in cs if c["verdict"] == "chance")
        un = sum(1 for c in cs if c["verdict"] == "unavailable")
        lines.append(f"| {label} | {m} | {inv} | {ch} | {un} |")
    lines.append("")
    return "\n".join(lines)


# ---------- CLI -----------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Cross-corpus polarity check for the narrative-decision "
            "audit (Surface 6)."
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help=(
            "JSONL manifest of judged stories. Each row needs "
            "`text_id`, `label`, and `narrative_values`."
        ),
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        required=True,
        help="Output path for the structured polarity report.",
    )
    parser.add_argument(
        "--out-md",
        type=Path,
        required=True,
        help="Output path for the calibration-findings markdown.",
    )
    parser.add_argument(
        "--corpus-name",
        required=True,
        help=(
            "Display name for the corpus (used in the findings "
            "doc heading)."
        ),
    )
    parser.add_argument(
        "--human-label",
        default="pre_ai_human",
        help="Manifest `label` value treated as human (default pre_ai_human).",
    )
    parser.add_argument(
        "--ai-label",
        default="ai_generated",
        help="Manifest `label` value treated as AI (default ai_generated).",
    )
    parser.add_argument(
        "--min-class-n",
        type=int,
        default=20,
        help=(
            "Per-signal minimum sample size per class. Cells below "
            "this floor get verdict 'chance' regardless of the "
            "(degenerate) CI, because Hanley-McNeil SE collapses to "
            "zero on perfect separation in tiny samples and would "
            "otherwise emit spuriously confident matches/inverted "
            "labels (default 20)."
        ),
    )
    args = parser.parse_args(argv)

    if args.min_class_n < 1:
        print(
            f"error: --min-class-n must be at least 1 "
            f"(got {args.min_class_n})",
            file=sys.stderr,
        )
        return 2

    rows, counts = load_manifest(
        args.manifest,
        human_label=args.human_label,
        ai_label=args.ai_label,
    )
    if not rows:
        print(
            f"error: manifest yielded 0 rows (skipped={counts['skipped']})",
            file=sys.stderr,
        )
        return 2
    n_human = sum(1 for r in rows if r.label == "human")
    n_ai = sum(1 for r in rows if r.label == "ai")
    if n_human == 0 or n_ai == 0:
        present = (
            f"human={n_human}, ai={n_ai}"
        )
        print(
            f"error: polarity audit requires at least one row in "
            f"each class; got {present}. Check --human-label "
            f"(currently {args.human_label!r}) and --ai-label "
            f"(currently {args.ai_label!r}) against the labels "
            f"actually present in the manifest.",
            file=sys.stderr,
        )
        return 2
    cells = per_signal_polarity(rows, min_class_n=args.min_class_n)
    aggregate = aggregate_scorer_auc(rows)
    report = build_report(
        corpus_name=args.corpus_name,
        manifest_path=args.manifest,
        rows=rows,
        counts=counts,
        cells=cells,
        aggregate=aggregate,
        min_class_n=args.min_class_n,
    )
    args.out_json.write_text(
        json.dumps(report, indent=2, default=str),
        encoding="utf-8",
    )
    args.out_md.write_text(render_markdown(report), encoding="utf-8")
    print(f"JSON  → {args.out_json}")
    print(f"MD    → {args.out_md}")
    summ = report["signal_summary"]
    print(
        f"Signals: matches={summ['matches']}, "
        f"inverted={summ['inverted']}, chance={summ['chance']}, "
        f"unavailable={summ['unavailable']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
