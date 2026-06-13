#!/usr/bin/env python3
"""argument_decision_audit.py — ArgScope Layer A: the argument-decision audit
surface (the argument-domain sibling of StoryScope's narrative-decision audit).

Scores HOW an argument is built against the collapse-tells reported in Kim,
Chang, Pham & Iyyer 2026, "Argument Collapse: LLMs Flatten Long-Form Public
Debate" (arXiv:2606.01736v3) — its structural arc (paragraph-role transitions,
B1) and discourse-mode mix (B2). It is NOT a provenance detector and NOT a
quality judgment: the paper measures argumentative *diversity*, not quality, and
does not claim human arguments are better. No "human = better."

The judge (`argument_judge`) labels a per-paragraph SEQUENCE (one role∈8 +
mode∈4 per paragraph); this surface computes the paper-anchored signals from
that sequence:
  * B1 support→proposal rate, support→support rate (row-normalized from the
    `support` role), thesis-opening tendency (directional, unanchored);
  * B2 argumentation discourse-mode share.
Each anchored signal's contribution is 1.0 at the paper's human mean and 0.0 at
its LLM mean; the aggregate is the mean contribution. The band is
**unconditionally `uncalibrated`** (the anchors are register-bound to
public-debate forums — directional reference, never thresholds); the consumer
(APODICTIC) maps the target's genre to matched/adjacent/distant and downgrades.

SCOPE (Inc A1): the anchorable B1/B2 judge core. B3/B4 deterministic reuse
(abstraction/stance via `argmove_profile` / `stance_modality_audit` /
`agency_abstraction_audit`) and the two dynamic/arc signals (disappearing-guard,
discounting-straw-men) are a deferred follow-up — the envelope is additive
(schema 1.0), so they slot in later without a break.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from argument_feature_schema import (  # type: ignore
    BUNDLE_LABELS,
    DERIVED_SIGNALS,
    DerivedSignal,
)
from argument_judge import (  # type: ignore
    JudgeError,
    build_judge,
    fingerprint_prompt,
    utc_now,
    validate_labels,
)
from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore

TASK_SURFACE = "argument_decision_audit"
TOOL_NAME = "argument_decision_audit"
SCRIPT_VERSION = "0.1.0"

MIN_ARGUMENT_WORDS = 300       # argument-bearing structure needs length
MIN_PARAGRAPHS = 3             # transition-matrix signals need a multi-paragraph arc
PAPER_OPED_MEAN_WORDS = 352    # NYT Room for Debate mean (Boston Review ~1,150)

DEFAULT_LICENSES = (
    "Reports how the target's argumentative STRUCTURE compares to the human / "
    "LLM group means Kim et al. 2026 (\"Argument Collapse\", arXiv:2606.01736) "
    "reported over public-debate-forum essays (NYT Room for Debate ~352w; Boston "
    "Review ~1,150w): the B1 paragraph-role transition rates (support→proposal, "
    "support→support) and the B2 argumentation discourse-mode share. Each "
    "anchored signal's contribution is 1.0 at the paper's human mean and 0.0 at "
    "its LLM mean; the aggregate is the mean per-signal contribution. Role/mode "
    "labels come from a pluggable LLM judge (read judge.provenance)."
)

DEFAULT_DOES_NOT_LICENSE = (
    "Does not license an AI / human authorship verdict — no signal here means "
    "\"written by AI\"; a human arguing thesis-first in an abstract register "
    "scores the same. Does not license a quality judgment: the paper measures "
    "argumentative DIVERSITY, not quality or accuracy, and does not claim human "
    "arguments are better (no \"human = better\"). The anchors are REGISTER-BOUND "
    "to public-debate forums; the paper's Limitations warn they may not transfer "
    "to research / legal / policy writing (the consumer's `distant` tier), so the "
    "band is unconditionally `uncalibrated` and a register mismatch downgrades to "
    "structural-signals-only. Lower-fidelity judge backends (mock / heuristic) are "
    "weaker than a faithful LLM labeler — read judge.provenance. Does not run a "
    "soundness / warrant / fairness verdict (that is dialectical-clarity / "
    "banister, which this surface may PRE-FLAG but never adjudicates). B3/B4 "
    "abstraction & stance signals and the dynamic collapse signals are a deferred "
    "follow-up, not in this surface."
)


# ---------- paragraph splitting + signal computation ----------------

def split_paragraphs(text: str) -> list[str]:
    """Split on blank lines; strip; drop empties. The judge labels exactly
    these paragraphs (aligned by index)."""
    parts = re.split(r"\n\s*\n", text.strip())
    return [p.strip() for p in parts if p.strip()]


_WORD_RE = re.compile(r"[A-Za-z']+")


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def compute_arc_signals(labels: list[dict[str, Any]]) -> dict[str, float | None]:
    """Compute the four B1/B2 derived signals from the per-paragraph label
    sequence (``labels`` = [{"role","mode"}, ...] aligned to document order).

    Transition rates are row-normalized FROM the ``support`` role and count only
    transitions whose successor paragraph is also labeled (a None successor
    neither numerator nor denominator). All signals return None when their
    denominator is empty (too few labels) — never a fabricated 0."""
    roles = [l.get("role") for l in labels]
    modes = [l.get("mode") for l in labels]

    support_succ = support_to_proposal = support_to_support = 0
    for cur, nxt in zip(roles, roles[1:]):
        if cur == "support" and nxt is not None:
            support_succ += 1
            if nxt == "proposal":
                support_to_proposal += 1
            elif nxt == "support":
                support_to_support += 1
    sp = support_to_proposal / support_succ if support_succ else None
    ss = support_to_support / support_succ if support_succ else None

    labeled_modes = [m for m in modes if m is not None]
    arg_share = (
        sum(1 for m in labeled_modes if m == "argumentation") / len(labeled_modes)
        if labeled_modes else None
    )

    thesis_open: float | None = None
    for r in roles:
        if r is not None:
            thesis_open = 1.0 if r == "thesis" else 0.0
            break

    return {
        "support_to_proposal_rate": sp,
        "support_to_support_rate": ss,
        "thesis_opening_tendency": thesis_open,
        "argumentation_share": arg_share,
    }


# ---------- contributions -------------------------------------------

@dataclass
class SignalContribution:
    signal_key: str
    label: str
    bundle: str
    leaning: str
    anchored: bool
    paper_human_mean: float | None
    paper_ai_mean: float | None
    observed_value: float | None
    contribution: float | None
    direction: str  # "human" | "ai" | "neutral" | "directional" | "unavailable"


def per_signal_contributions(
    observed: dict[str, float | None],
) -> list[SignalContribution]:
    out: list[SignalContribution] = []
    for sig in DERIVED_SIGNALS:
        ov = observed.get(sig.key)
        if not sig.anchored:
            # Directional-only (no numeric anchor): report the observed value,
            # no contribution, no human/ai placement.
            out.append(SignalContribution(
                signal_key=sig.key, label=sig.label, bundle=sig.bundle,
                leaning=sig.leaning, anchored=False,
                paper_human_mean=None, paper_ai_mean=None,
                observed_value=ov, contribution=None, direction="directional",
            ))
            continue
        if ov is None:
            out.append(SignalContribution(
                signal_key=sig.key, label=sig.label, bundle=sig.bundle,
                leaning=sig.leaning, anchored=True,
                paper_human_mean=sig.human_mean, paper_ai_mean=sig.ai_mean,
                observed_value=None, contribution=None, direction="unavailable",
            ))
            continue
        denom = sig.human_mean - sig.ai_mean
        contribution = 0.0 if denom == 0 else (ov - sig.ai_mean) / denom
        midpoint = (sig.human_mean + sig.ai_mean) / 2
        if abs(ov - midpoint) < 1e-9:
            direction = "neutral"
        elif (ov > midpoint) == (sig.human_mean > sig.ai_mean):
            direction = "human"
        else:
            direction = "ai"
        out.append(SignalContribution(
            signal_key=sig.key, label=sig.label, bundle=sig.bundle,
            leaning=sig.leaning, anchored=True,
            paper_human_mean=sig.human_mean, paper_ai_mean=sig.ai_mean,
            observed_value=ov, contribution=contribution, direction=direction,
        ))
    return out


@dataclass
class BundleAggregate:
    bundle: str
    label: str
    n_signals: int
    n_evaluated: int
    mean_contribution: float | None
    human_leaning_signals: int
    ai_leaning_signals: int


def per_bundle_aggregates(
    contributions: list[SignalContribution],
) -> list[BundleAggregate]:
    by_bundle: dict[str, list[SignalContribution]] = {}
    for c in contributions:
        by_bundle.setdefault(c.bundle, []).append(c)
    out: list[BundleAggregate] = []
    for bundle in BUNDLE_LABELS:
        sigs = by_bundle.get(bundle, [])
        evaluated = [s for s in sigs if s.contribution is not None]
        mean = (
            sum(s.contribution for s in evaluated) / len(evaluated)
            if evaluated else None
        )
        out.append(BundleAggregate(
            bundle=bundle,
            label=BUNDLE_LABELS[bundle],
            n_signals=len(sigs),
            n_evaluated=len(evaluated),
            mean_contribution=mean,
            human_leaning_signals=sum(1 for s in sigs if s.direction == "human"),
            ai_leaning_signals=sum(1 for s in sigs if s.direction == "ai"),
        ))
    return out


def aggregate_score(contributions: list[SignalContribution]) -> dict[str, Any]:
    evaluated = [c for c in contributions if c.contribution is not None]
    if not evaluated:
        return {
            "score": None,
            "n_signals_evaluated": 0,
            "n_signals_total": len(contributions),
            "verdict_band": "unavailable",
        }
    raw = sum(c.contribution for c in evaluated) / len(evaluated)
    return {
        "score": raw,
        "n_signals_evaluated": len(evaluated),
        "n_signals_total": len(contributions),
        "verdict_band": "uncalibrated",
    }


def compute_pre_flag(contributions: list[SignalContribution]) -> dict[str, Any]:
    """D4: a structured pre-flag DATA hint — a texture observation, never a
    reasoning verdict. True when the anchored arc/mode signals converge on the
    paper's collapse-leaning (LLM-typical) pattern (≥2 of the 3 anchored signals
    on the AI side). The consumer OFFERS a dialectical-clarity run on this hint
    (offer-then-attach); ArgScope itself claims no soundness/warrant verdict."""
    by = {c.signal_key: c for c in contributions}
    arc_keys = ("support_to_proposal_rate", "support_to_support_rate", "argumentation_share")
    ai_leaning = [k for k in arc_keys if by.get(k) and by[k].direction == "ai"]
    informative = len(ai_leaning) >= 2
    if informative:
        basis = (
            "B1 proposal-heavy arc + B2 argumentation-dominant mode mix lean "
            "LLM-typical (" + ", ".join(ai_leaning) + " on the AI side of the "
            "paper's midpoint). A dialectical-clarity run would check whether "
            "the proposal-heavy arc reflects an AT3 uncompared recommendation "
            "(DC rule 2a). This is a texture observation, not a soundness verdict."
        )
    else:
        basis = (
            "The anchored arc/mode signals do not converge on the paper's "
            "collapse-leaning pattern (fewer than 2 of support→proposal, "
            "support→support, argumentation_share on the AI side)."
        )
    return {"dialectical_clarity_informative": informative, "basis": basis}


def register_warnings_for(n_words: int, n_paragraphs: int) -> list[str]:
    warnings: list[str] = []
    if n_words < MIN_ARGUMENT_WORDS:
        warnings.append(
            f"Target is {n_words} words; ArgScope's home register is "
            f"public-debate-forum essays (NYT mean ~{PAPER_OPED_MEAN_WORDS}). "
            f"Below ~{MIN_ARGUMENT_WORDS} words the structural arc is too short "
            f"to read; treat as out-of-register."
        )
    if n_paragraphs < MIN_PARAGRAPHS:
        warnings.append(
            f"Target has {n_paragraphs} paragraph(s); the B1 transition-matrix "
            f"signals (support→proposal, support→support) need a multi-paragraph "
            f"arc (>= {MIN_PARAGRAPHS}). They report null below that."
        )
    return warnings


# ---------- envelope -------------------------------------------------

def build_results_payload(
    *,
    target_words: int,
    n_paragraphs: int,
    judge_result: dict[str, Any],
    paragraph_labels: list[dict[str, Any]],
    validation_warnings: list[str],
    observed: dict[str, float | None],
    contributions: list[SignalContribution],
    bundles: list[BundleAggregate],
    aggregate: dict[str, Any],
    pre_flag: dict[str, Any],
    register_warnings: list[str],
) -> dict[str, Any]:
    return {
        "judge": judge_result,
        "prompt_fingerprint_sha256": fingerprint_prompt(),
        "target": {
            "words": target_words,
            "paragraphs": n_paragraphs,
            "register_match": ["op-ed"],
            "register_warnings": register_warnings,
        },
        "paragraph_labels": paragraph_labels,
        "validation_warnings": validation_warnings,
        "observed_signals": observed,
        "contributions": [
            {
                "signal_key": c.signal_key,
                "label": c.label,
                "bundle": c.bundle,
                "leaning": c.leaning,
                "anchored": c.anchored,
                "paper_human_mean": c.paper_human_mean,
                "paper_ai_mean": c.paper_ai_mean,
                "observed_value": c.observed_value,
                "contribution": c.contribution,
                "direction": c.direction,
            }
            for c in contributions
        ],
        "bundles": [
            {
                "bundle": b.bundle,
                "label": b.label,
                "n_signals": b.n_signals,
                "n_evaluated": b.n_evaluated,
                "mean_contribution": b.mean_contribution,
                "human_leaning_signals": b.human_leaning_signals,
                "ai_leaning_signals": b.ai_leaning_signals,
            }
            for b in bundles
        ],
        "pre_flag": pre_flag,
        "aggregate": {
            **aggregate,
            "thresholds": {"low": None, "high": None},
        },
        "run_timestamp_utc": utc_now(),
    }


def compose_envelope(
    *,
    target_path: Path | None,
    target_words: int,
    results: dict[str, Any],
    licenses_text: str,
    does_not_license_text: str,
) -> dict[str, Any]:
    caveats: list[str] = []
    if results["target"].get("register_warnings"):
        caveats.extend(results["target"]["register_warnings"])
    if results.get("validation_warnings"):
        caveats.append(
            f"Judge output had {len(results['validation_warnings'])} validation "
            f"warning(s); see results.validation_warnings."
        )
    judge_kind = results["judge"]["judge_identity"].get("kind")
    if judge_kind in ("mock", "manifest"):
        caveats.append(
            f"Judge backend is `{judge_kind}` — lower fidelity than a faithful "
            f"LLM labeler. The role/mode labels (and every B1/B2 signal derived "
            f"from them) are only as good as the supplied labels."
        )
    caveats.append(
        "Verdict band is `uncalibrated` and the anchors are register-bound to "
        "public-debate forums (directional reference, not thresholds). No "
        "human / AI label is emitted."
    )

    license_block = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=licenses_text,
        does_not_license=does_not_license_text,
        comparison_set={
            "literature_anchor": (
                "Kim, Chang, Pham & Iyyer 2026 ('Argument Collapse', "
                "arXiv:2606.01736v3) §4.1-4.2 / Tables 26-27 group means, "
                "public-debate-forum essays (NYT Room for Debate + Boston Review)"
            ),
            "judge_kind": judge_kind,
            "judge_model": (
                results["judge"]["judge_identity"].get("model") or "(unspecified)"
            ),
            "prompt_fingerprint_sha256": results["prompt_fingerprint_sha256"],
        },
        length_range_words=(MIN_ARGUMENT_WORDS, 8000),
        register_match=["op-ed"],
        additional_caveats=caveats,
        references=[
            "Kim, Chang, Pham & Iyyer 2026, 'Argument Collapse: LLMs Flatten "
            "Long-Form Public Debate' (arXiv:2606.01736v3)",
        ],
    )

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=None,
        results=results,
        claim_license=license_block,
        available=True,
        warnings=caveats,
    )


def render_markdown(envelope: dict[str, Any]) -> str:
    results = envelope["results"]
    target = envelope["target"]
    agg = results["aggregate"]
    lines: list[str] = [
        f"# Argument-decision audit: `{target.get('path')}`",
        "",
        f"- **Target:** {target.get('words'):,} words, "
        f"{results['target']['paragraphs']} paragraphs",
        f"- **Judge:** `{results['judge']['judge_identity'].get('kind')}` "
        f"({results['judge']['judge_identity'].get('model') or '—'})",
        f"- **Aggregate score:** "
        f"{('%.3f' % agg['score']) if agg.get('score') is not None else 'n/a'} "
        f"(verdict band: `{agg['verdict_band']}`)",
        f"- **Signals evaluated:** {agg['n_signals_evaluated']}/{agg['n_signals_total']}",
        "",
        "Score is in human-z-units: 1.0 = the paper's human mean, 0.0 = its LLM "
        "mean. Anchors are register-bound to public-debate forums (directional, "
        "not thresholds).",
        "",
        "## Signals",
        "",
        "| Signal | Bundle | Observed | H mean | LLM mean | Contribution | Direction |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for c in results["contributions"]:
        ov = c["observed_value"]
        ov_s = f"{ov:.3f}" if ov is not None else "—"
        hm = f"{c['paper_human_mean']:.3f}" if c["paper_human_mean"] is not None else "—"
        am = f"{c['paper_ai_mean']:.3f}" if c["paper_ai_mean"] is not None else "—"
        contrib = c["contribution"]
        contrib_s = f"{contrib:+.3f}" if contrib is not None else "—"
        lines.append(
            f"| {c['label']} | {c['bundle']} | {ov_s} | {hm} | {am} | "
            f"{contrib_s} | {c['direction']} |"
        )
    lines += [
        "",
        f"**Pre-flag (dialectical-clarity informative):** "
        f"{results['pre_flag']['dialectical_clarity_informative']} — "
        f"{results['pre_flag']['basis']}",
        "",
        "## Claim license",
        "",
        envelope["claim_license_rendered"].rstrip(),
        "",
    ]
    return "\n".join(lines)


# ---------- CLI -----------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "ArgScope Layer A: score a public-debate essay's argumentative arc "
            "(B1 paragraph-role transitions) + discourse-mode mix (B2) against "
            "Kim et al. 2026's human/LLM anchors."
        )
    )
    parser.add_argument("target", help="Path to target text file (UTF-8).")
    parser.add_argument(
        "--judge", choices=("manifest", "mock", "anthropic", "openai", "gemini"),
        default="manifest",
        help="Judge backend for the per-paragraph role/mode labels.",
    )
    parser.add_argument("--judge-manifest", type=Path, default=None,
                        help="JSON manifest of pre-computed labels (required for --judge=manifest).")
    parser.add_argument("--judge-model", default=None, help="Model ID for API judges.")
    parser.add_argument("--judge-temperature", type=float, default=0.0)
    parser.add_argument("--judge-max-tokens", type=int, default=4096)
    parser.add_argument("--out", type=Path, default=None,
                        help="JSON output path (default <target>.argument.json).")
    parser.add_argument("--out-md", type=Path, default=None,
                        help="Markdown output path (default <target>.argument.md).")
    parser.add_argument("--json", action="store_true", help="Print the envelope to stdout.")
    parser.add_argument("--licenses", default=DEFAULT_LICENSES)
    parser.add_argument("--does-not-license", default=DEFAULT_DOES_NOT_LICENSE)
    args = parser.parse_args(argv)

    target_path = Path(args.target)
    if not target_path.exists():
        print(f"error: target file not found at {target_path}", file=sys.stderr)
        return 1
    try:
        text = target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        print(f"error: target not valid UTF-8: {exc}", file=sys.stderr)
        return 1

    paragraphs = split_paragraphs(text)
    target_words = count_words(text)

    try:
        judge = build_judge(
            args.judge, manifest_path=args.judge_manifest, model=args.judge_model,
            temperature=args.judge_temperature, max_tokens=args.judge_max_tokens,
        )
    except JudgeError as exc:
        print(f"error: judge construction failed: {exc}", file=sys.stderr)
        return 2
    try:
        judge_result_obj = judge(paragraphs)
    except JudgeError as exc:
        print(f"error: judge execution failed: {exc}", file=sys.stderr)
        return 3

    labels, val_warnings = validate_labels(
        judge_result_obj.values, n_paragraphs=len(paragraphs)
    )
    observed = compute_arc_signals(labels)
    contributions = per_signal_contributions(observed)
    bundles = per_bundle_aggregates(contributions)
    agg = aggregate_score(contributions)
    pre_flag = compute_pre_flag(contributions)
    reg_warnings = register_warnings_for(target_words, len(paragraphs))

    paragraph_labels = [
        {"index": i, "role": labels[i]["role"], "mode": labels[i]["mode"]}
        for i in range(len(labels))
    ]
    results = build_results_payload(
        target_words=target_words,
        n_paragraphs=len(paragraphs),
        judge_result=judge_result_obj.to_dict(),
        paragraph_labels=paragraph_labels,
        validation_warnings=val_warnings,
        observed=observed,
        contributions=contributions,
        bundles=bundles,
        aggregate=agg,
        pre_flag=pre_flag,
        register_warnings=reg_warnings,
    )
    envelope = compose_envelope(
        target_path=target_path, target_words=target_words, results=results,
        licenses_text=args.licenses, does_not_license_text=args.does_not_license,
    )

    out_json_path = (
        args.out if args.out is not None
        else target_path.with_suffix(target_path.suffix + ".argument.json")
    )
    out_md_path = (
        args.out_md if args.out_md is not None
        else target_path.with_suffix(target_path.suffix + ".argument.md")
    )
    out_json_path.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
    out_md_path.write_text(render_markdown(envelope), encoding="utf-8")

    if args.json:
        print(json.dumps(envelope, indent=2, default=str))
    else:
        score = agg.get("score")
        score_s = f"{score:+.3f}" if score is not None else "n/a"
        print(f"JSON written to {out_json_path}")
        print(f"Markdown written to {out_md_path}")
        print(f"Aggregate score: {score_s} (verdict band: {results['aggregate']['verdict_band']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
