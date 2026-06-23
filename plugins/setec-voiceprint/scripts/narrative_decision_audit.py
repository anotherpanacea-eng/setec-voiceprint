#!/usr/bin/env python3
"""narrative_decision_audit.py — Surface 6 audit.

Apply the 30 core narrative-decision features from Russell et al. 2026
("StoryScope") to a single target prose document, compute per-feature
deviations from the paper's reported human and AI group means, and
emit a literature-anchored signed score with a `claim_license` block.

This audit ships *uncalibrated* by default — the score is reported
but no verdict band is emitted until the operator supplies thresholds
from their own cross-corpus calibration. See
`narrative-decision-audit-spec.md` and the polarity-check workflow at
`scripts/calibration/narrative_polarity_audit.py`.

What this audit reports

  * Per-signal `target_value`, `paper_human_mean`, `paper_ai_mean`,
    `contribution` (signed, in human-z-units relative to the paper
    means), and `direction` (human-leaning / AI-leaning / neutral).
  * Per-bundle aggregates over the 7 interpretive themes from the
    paper's Table 12.
  * An aggregate signed score: mean per-signal contribution. Positive
    = more human-like than the paper's mean-of-means; negative =
    more AI-like.

What this audit does NOT report

  * A binary AI/human verdict. The literature-anchored aggregate is a
    measurement against a single paper's reported group means on
    long-form fiction; per-corpus thresholds are operator-side and
    require the polarity-check workflow to validate that the
    direction holds on the operator's prose.

CLI

    python3 narrative_decision_audit.py target.txt \\
        --judge manifest --judge-manifest features.json

    python3 narrative_decision_audit.py target.txt \\
        --judge anthropic --judge-model claude-sonnet-4-6

    python3 narrative_decision_audit.py target.txt \\
        --judge mock --out target.narrative.json

The audit makes no assumption about which model produced the
feature values; the judge interface accepts a JSON manifest of
pre-computed assignments so operators can use any model + pipeline
they prefer. The reference API adapters (anthropic / openai /
gemini) exist as a convenience for single-doc spot-checks.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claim_license import ClaimLicense  # type: ignore
from narrative_feature_schema import (  # type: ignore
    BUNDLE_LABELS,
    CORE_FEATURES,
    CoreFeature,
    FeatureSignal,
)
from narrative_judge import (  # type: ignore
    JudgeError,
    build_judge,
    fingerprint_prompt,
    utc_now,
    validate_values,
)
from output_schema import build_output  # type: ignore

TASK_SURFACE = "narrative_decision_audit"
TOOL_NAME = "narrative_decision_audit"
SCRIPT_VERSION = "0.1.0"

MIN_FICTION_WORDS = 2000
PAPER_CORPUS_MEAN_WORDS = 4753

DEFAULT_LICENSES = (
    "Reports per-feature deviations from the human / AI group means "
    "Russell et al. 2026 (StoryScope, arXiv:2604.03136v4) reported "
    "for 30 core narrative-decision features over a parallel corpus "
    "of 61,608 short stories (mean 4,753 words). Each of the 33 "
    "signal contributions is signed: positive = the target's value "
    "is on the paper's human side of the midpoint between the "
    "reported means; negative = on the paper's AI side. The "
    "aggregate score is the mean per-signal contribution, expressed "
    "in human-z-units (1.0 = mean of paper's human stories; 0.0 = "
    "mean of paper's AI stories)."
)

DEFAULT_DOES_NOT_LICENSE = (
    "Does not license a binary AI / human authorship verdict. The "
    "score is a literature-anchored measurement against the "
    "Russell et al. 2026 long-form-fiction means; the framework "
    "ships no per-corpus thresholds as defaults and the verdict "
    "band is 'uncalibrated' until an operator supplies "
    "--threshold-low / --threshold-high derived from their own "
    "polarity check (see narrative_polarity_audit.py). Does not "
    "generalize to non-fiction registers without operator-side "
    "validation — several features (subplot integration, anachrony, "
    "frame narratives, sensory density) are not well-defined on "
    "essays, op-eds, or short-form prose. Does not substitute for "
    "Tier-1 variance, AIC-7/8/9 craft-pattern, or Binoculars "
    "audits; the paper's framing is that narrative-decision tells "
    "are *complementary* to stylistic tells, surviving LAMP-style "
    "rewriting that scrubs surface artifacts."
)


# ---------- encoding -------------------------------------------------

def encode_value(feature: CoreFeature, value: Any) -> float | None:
    """Convert a judge-emitted value to its numeric encoding.

    For scale features, the integer 1..5. For ordinal features, the
    0-based index into ``response_options``. For binary features,
    "yes"=1, "no"=0. For categorical features the encoding is per
    signal (see ``signal_target_value``); this helper returns None
    for categorical/multi inputs because there is no single numeric
    for the feature as a whole.
    """
    if value is None:
        return None
    if feature.feature_type == "scale":
        try:
            iv = int(value)
        except (TypeError, ValueError):
            return None
        return float(iv)
    if feature.feature_type == "ordinal":
        try:
            return float(feature.response_options.index(value))
        except ValueError:
            return None
    if feature.feature_type == "binary":
        if value == "yes":
            return 1.0
        if value == "no":
            return 0.0
        return None
    return None


def signal_target_value(
    feature: CoreFeature,
    signal: FeatureSignal,
    value: Any,
) -> float | None:
    """Per-signal target value in the same units as the paper's mean.

    For scale/ordinal/binary signals (option is None) this is the
    encoded numeric value. For categorical/multi signals (option is
    a string) this is 1.0 if the target selected that option (or
    if the option is in the multi-select list) and 0.0 otherwise.
    Returns None when the judge emitted no value for the feature.
    """
    if value is None:
        return None
    if signal.option is None:
        return encode_value(feature, value)
    if feature.feature_type == "multi":
        if not isinstance(value, list):
            return None
        return 1.0 if signal.option in value else 0.0
    return 1.0 if value == signal.option else 0.0


@dataclass
class SignalContribution:
    feature_key: str
    feature_label: str
    dimension: str
    bundle: str
    option: str | None
    leaning: str
    paper_human_mean: float
    paper_ai_mean: float
    target_value: float | None
    contribution: float | None
    direction: str  # "human" | "ai" | "neutral" | "unavailable"


def per_signal_contributions(
    cleaned_values: dict[str, Any],
) -> list[SignalContribution]:
    out: list[SignalContribution] = []
    for f in CORE_FEATURES:
        v = cleaned_values.get(f.key)
        for s in f.signals:
            tv = signal_target_value(f, s, v)
            if tv is None:
                out.append(SignalContribution(
                    feature_key=f.key,
                    feature_label=f.label,
                    dimension=f.dimension,
                    bundle=s.bundle,
                    option=s.option,
                    leaning=s.leaning,
                    paper_human_mean=s.human_mean,
                    paper_ai_mean=s.ai_mean,
                    target_value=None,
                    contribution=None,
                    direction="unavailable",
                ))
                continue
            denom = s.human_mean - s.ai_mean
            if denom == 0:
                contribution = 0.0
            else:
                contribution = (tv - s.ai_mean) / denom
            midpoint = (s.human_mean + s.ai_mean) / 2
            if abs(tv - midpoint) < 1e-9:
                direction = "neutral"
            elif (tv > midpoint) == (s.human_mean > s.ai_mean):
                direction = "human"
            else:
                direction = "ai"
            out.append(SignalContribution(
                feature_key=f.key,
                feature_label=f.label,
                dimension=f.dimension,
                bundle=s.bundle,
                option=s.option,
                leaning=s.leaning,
                paper_human_mean=s.human_mean,
                paper_ai_mean=s.ai_mean,
                target_value=tv,
                contribution=contribution,
                direction=direction,
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
    neutral_signals: int


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
            human_leaning_signals=sum(
                1 for s in sigs if s.direction == "human"
            ),
            ai_leaning_signals=sum(
                1 for s in sigs if s.direction == "ai"
            ),
            neutral_signals=sum(
                1 for s in sigs if s.direction == "neutral"
            ),
        ))
    return out


# ---------- aggregate scorer ----------------------------------------

def aggregate_score(
    contributions: list[SignalContribution],
) -> dict[str, Any]:
    evaluated = [
        c for c in contributions if c.contribution is not None
    ]
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


def verdict_band_from_thresholds(
    score: float | None,
    *,
    threshold_low: float | None,
    threshold_high: float | None,
) -> str:
    if score is None:
        return "unavailable"
    if threshold_low is None or threshold_high is None:
        return "uncalibrated"
    if score < threshold_low:
        return "ai_likely"
    if score > threshold_high:
        return "human_likely"
    return "indeterminate"


# ---------- IO + envelope -------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z']+")


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def build_results_payload(
    *,
    target_words: int,
    judge_result: dict[str, Any],
    cleaned_values: dict[str, Any],
    validation_warnings: list[str],
    contributions: list[SignalContribution],
    bundles: list[BundleAggregate],
    aggregate: dict[str, Any],
    threshold_low: float | None,
    threshold_high: float | None,
    register_warnings: list[str],
) -> dict[str, Any]:
    verdict_band = verdict_band_from_thresholds(
        aggregate.get("score"),
        threshold_low=threshold_low,
        threshold_high=threshold_high,
    )
    return {
        "judge": judge_result,
        "prompt_fingerprint_sha256": fingerprint_prompt(),
        "target": {
            "words": target_words,
            "register_warnings": register_warnings,
        },
        "values": cleaned_values,
        "validation_warnings": validation_warnings,
        "contributions": [
            {
                "feature_key": c.feature_key,
                "feature_label": c.feature_label,
                "dimension": c.dimension,
                "bundle": c.bundle,
                "option": c.option,
                "leaning": c.leaning,
                "paper_human_mean": c.paper_human_mean,
                "paper_ai_mean": c.paper_ai_mean,
                "target_value": c.target_value,
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
                "neutral_signals": b.neutral_signals,
            }
            for b in bundles
        ],
        "aggregate": {
            **aggregate,
            "verdict_band": verdict_band,
            "thresholds": {
                "low": threshold_low,
                "high": threshold_high,
            },
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
    caveats = []
    if results["target"].get("register_warnings"):
        caveats.extend(results["target"]["register_warnings"])
    if results.get("validation_warnings"):
        n = len(results["validation_warnings"])
        caveats.append(
            f"Judge output had {n} validation warning(s); see "
            f"results.validation_warnings for the full list."
        )
    if results["judge"]["judge_identity"].get("kind") == "agent_host":
        caveats.append(
            "Judge backend is `agent_host` — the labels were produced by the HOST "
            "runtime's model (see judge.judge_identity.host), not a pinned API "
            "model@revision; the judgment is NON-DETERMINISTIC and host-version-fluid. "
            "Identity is recorded as agent_host:<host>:<model> so a consumer can assert "
            "disjointness from any generator it validates (consumer drift gate enforces "
            "judge model != generator model; see specs/35-host-delegated-judge.md)."
        )
    if results["aggregate"]["verdict_band"] == "uncalibrated":
        caveats.append(
            "Verdict band is `uncalibrated`. The aggregate score is "
            "reported but no human / AI label is emitted. Supply "
            "operator-calibrated thresholds via --threshold-low / "
            "--threshold-high to surface a band; thresholds derived "
            "on a different register or judge model do not transfer."
        )

    license_block = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=licenses_text,
        does_not_license=does_not_license_text,
        comparison_set={
            "literature_anchor": (
                "Russell et al. 2026 (StoryScope, arXiv:2604.03136v4) "
                "Table 12 group means, n=61,608 stories, mean 4,753 "
                "words, 6 sources"
            ),
            "judge_kind": results["judge"]["judge_identity"].get("kind"),
            "judge_model": (
                results["judge"]["judge_identity"].get("model")
                or "(unspecified)"
            ),
            # host runtime id for agent_host (firewall hook); null otherwise.
            "judge_host": results["judge"]["judge_identity"].get("host"),
            "prompt_fingerprint_sha256": results["prompt_fingerprint_sha256"],
        },
        length_range_words=(MIN_FICTION_WORDS, 25_000),
        register_match=["long_form_fiction"],
        additional_caveats=caveats,
        references=[
            "Russell et al. 2026, 'StoryScope: Narrative-Level "
            "Detection of AI-Generated Fiction' (arXiv:2604.03136v4)",
            "Hamilton et al. 2025, NarraBench (cited as feature "
            "taxonomy source)",
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
    lines: list[str] = []
    lines.append(f"# Narrative-decision audit: `{target.get('path')}`")
    lines.append("")
    lines.append(
        f"- **Target words:** {target.get('words'):,}"
    )
    lines.append(
        f"- **Judge:** "
        f"`{results['judge']['judge_identity'].get('kind')}` "
        f"({results['judge']['judge_identity'].get('model') or '—'})"
    )
    lines.append(
        f"- **Aggregate score:** "
        f"{('%.3f' % agg['score']) if agg.get('score') is not None else 'n/a'} "
        f"(verdict band: `{agg['verdict_band']}`)"
    )
    lines.append(
        f"- **Signals evaluated:** "
        f"{agg['n_signals_evaluated']} / {agg['n_signals_total']}"
    )
    lines.append("")
    lines.append("## Per-bundle aggregates")
    lines.append("")
    lines.append(
        "Score is the mean per-signal contribution within the "
        "bundle, in human-z-units (1.0 = paper's human mean; "
        "0.0 = paper's AI mean; negative = beyond AI mean toward "
        "AI-leaning region)."
    )
    lines.append("")
    lines.append("| Bundle | Signals | Mean | Human-leaning | AI-leaning |")
    lines.append("|---|---:|---:|---:|---:|")
    for b in results["bundles"]:
        mc = b["mean_contribution"]
        mc_s = f"{mc:+.3f}" if mc is not None else "—"
        lines.append(
            f"| {b['label']} | {b['n_evaluated']}/{b['n_signals']} | "
            f"{mc_s} | {b['human_leaning_signals']} | "
            f"{b['ai_leaning_signals']} |"
        )
    lines.append("")
    lines.append("## Per-signal contributions")
    lines.append("")
    lines.append(
        "| Feature | Option | Leaning | Target | H mean | AI mean "
        "| Contribution | Direction |"
    )
    lines.append("|---|---|---|---:|---:|---:|---:|---|")
    for c in results["contributions"]:
        opt = c["option"] or "(numeric)"
        tv = c["target_value"]
        tv_s = f"{tv:.2f}" if tv is not None else "—"
        contrib = c["contribution"]
        contrib_s = (
            f"{contrib:+.3f}" if contrib is not None else "—"
        )
        lines.append(
            f"| {c['feature_label']} | `{opt}` | {c['leaning']} | "
            f"{tv_s} | {c['paper_human_mean']:.2f} | "
            f"{c['paper_ai_mean']:.2f} | {contrib_s} | "
            f"{c['direction']} |"
        )
    lines.append("")
    lines.append("## Claim license")
    lines.append("")
    lines.append(envelope["claim_license_rendered"].rstrip())
    lines.append("")
    return "\n".join(lines)


# ---------- register gating ----------------------------------------

def register_warnings_for(text: str, target_words: int) -> list[str]:
    warnings: list[str] = []
    if target_words < MIN_FICTION_WORDS:
        warnings.append(
            f"Target is {target_words} words; paper's home register "
            f"is long-form fiction (corpus mean "
            f"{PAPER_CORPUS_MEAN_WORDS:,}). Features like subplot "
            f"integration, anachrony, and frame narratives degrade "
            f"silently on prose under "
            f"~{MIN_FICTION_WORDS:,} words. Treat the result as "
            f"out-of-register and rely on the operator-side "
            f"polarity check before drawing inference."
        )
    if not re.search(r"['\"“”]", text):
        warnings.append(
            "Target appears to contain no dialogue (no quote marks "
            "detected). Several features assume narrative prose "
            "with at least some character speech; if this is an "
            "essay or op-ed the audit's home register doesn't "
            "match."
        )
    return warnings


# ---------- CLI -----------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply Russell et al. 2026's 30 core narrative-decision "
            "features to a target prose document."
        )
    )
    parser.add_argument(
        "target",
        help="Path to target text file (UTF-8).",
    )
    parser.add_argument(
        "--judge",
        choices=("manifest", "mock", "anthropic", "openai", "gemini", "agent_host"),
        default="manifest",
        help=(
            "Judge backend. 'manifest' (default) reads "
            "pre-computed feature assignments from --judge-manifest. "
            "'mock' emits constants for testing. "
            "'anthropic'/'openai'/'gemini' call the corresponding "
            "API (SDK + credentials required)."
        ),
    )
    parser.add_argument(
        "--judge-manifest",
        type=Path,
        default=None,
        help=(
            "Path to a JSON manifest of pre-computed feature "
            "values; required when --judge=manifest."
        ),
    )
    parser.add_argument(
        "--judge-model",
        default=None,
        help=(
            "Model ID for API judges (e.g., "
            "claude-sonnet-4-6, gpt-5.4, gemini-3-flash)."
        ),
    )
    parser.add_argument(
        "--judge-temperature",
        type=float,
        default=0.0,
        help="Sampling temperature for API judges (default 0.0).",
    )
    parser.add_argument(
        "--judge-max-tokens",
        type=int,
        default=4096,
        help="Max output tokens for API judges (default 4096).",
    )
    parser.add_argument(
        "--threshold-low",
        type=float,
        default=None,
        help=(
            "Below this aggregate score, surface 'ai_likely' band. "
            "No framework-calibrated default; without this the "
            "verdict band is 'uncalibrated'."
        ),
    )
    parser.add_argument(
        "--threshold-high",
        type=float,
        default=None,
        help=(
            "Above this aggregate score, surface 'human_likely' "
            "band. See --threshold-low note."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Evidence-pack JSON output path (default: "
            "<target>.narrative.json next to the target file)."
        ),
    )
    parser.add_argument(
        "--out-md",
        type=Path,
        default=None,
        help=(
            "Evidence-pack markdown output path (default: "
            "<target>.narrative.md next to the target file)."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the JSON envelope to stdout.",
    )
    parser.add_argument(
        "--licenses",
        default=DEFAULT_LICENSES,
        help="Override the claim_license.licenses text.",
    )
    parser.add_argument(
        "--does-not-license",
        default=DEFAULT_DOES_NOT_LICENSE,
        help="Override the claim_license.does_not_license text.",
    )
    args = parser.parse_args(argv)

    # Threshold-pair validation. The verdict-band logic checks
    # `score < threshold_low` first, so a swapped pair would silently
    # mislabel most scores as ai_likely. Require both thresholds
    # together so the band is well-defined and require low < high so
    # the band ordering matches the semantics in --help.
    if (args.threshold_low is None) != (args.threshold_high is None):
        print(
            "error: --threshold-low and --threshold-high must be "
            "supplied together (or both omitted, in which case the "
            "verdict band stays 'uncalibrated').",
            file=sys.stderr,
        )
        return 1
    if (
        args.threshold_low is not None
        and args.threshold_high is not None
        and args.threshold_low >= args.threshold_high
    ):
        print(
            f"error: --threshold-low ({args.threshold_low}) must be "
            f"strictly less than --threshold-high "
            f"({args.threshold_high}). Scores below threshold_low "
            f"surface 'ai_likely'; above threshold_high "
            f"'human_likely'.",
            file=sys.stderr,
        )
        return 1

    target_path = Path(args.target)
    if not target_path.exists():
        print(
            f"error: target file not found at {target_path}",
            file=sys.stderr,
        )
        return 1
    try:
        text = target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        print(
            f"error: target not valid UTF-8: {exc}",
            file=sys.stderr,
        )
        return 1
    target_words = count_words(text)

    try:
        judge = build_judge(
            args.judge,
            manifest_path=args.judge_manifest,
            model=args.judge_model,
            temperature=args.judge_temperature,
            max_tokens=args.judge_max_tokens,
        )
    except JudgeError as exc:
        # Judge construction failures are bad SETUP input (missing manifest /
        # model / API key), not a privacy-policy refusal. Route them through
        # argparse so the emitted "usage:" line lets setec_run categorize the
        # exit-2 as bad_input rather than the policy_refused bucket that a bare
        # exit-2 falls into (the privacy ratchet). See setec_run._wrap_script_failure.
        parser.error(f"judge construction failed: {exc}")

    try:
        judge_result_obj = judge(text)
    except JudgeError as exc:
        print(f"error: judge execution failed: {exc}", file=sys.stderr)
        return 3

    cleaned, val_warnings = validate_values(judge_result_obj.values)
    contributions = per_signal_contributions(cleaned)
    bundles = per_bundle_aggregates(contributions)
    agg = aggregate_score(contributions)
    reg_warnings = register_warnings_for(text, target_words)

    results = build_results_payload(
        target_words=target_words,
        judge_result=judge_result_obj.to_dict(),
        cleaned_values=cleaned,
        validation_warnings=val_warnings,
        contributions=contributions,
        bundles=bundles,
        aggregate=agg,
        threshold_low=args.threshold_low,
        threshold_high=args.threshold_high,
        register_warnings=reg_warnings,
    )
    envelope = compose_envelope(
        target_path=target_path,
        target_words=target_words,
        results=results,
        licenses_text=args.licenses,
        does_not_license_text=args.does_not_license,
    )

    out_json_path = (
        args.out
        if args.out is not None
        else target_path.with_suffix(target_path.suffix + ".narrative.json")
    )
    out_md_path = (
        args.out_md
        if args.out_md is not None
        else target_path.with_suffix(target_path.suffix + ".narrative.md")
    )
    out_json_path.write_text(
        json.dumps(envelope, indent=2, default=str),
        encoding="utf-8",
    )
    out_md_path.write_text(render_markdown(envelope), encoding="utf-8")

    if args.json:
        print(json.dumps(envelope, indent=2, default=str))
    else:
        print(f"JSON written to {out_json_path}")
        print(f"Markdown written to {out_md_path}")
        score = agg.get("score")
        score_s = f"{score:+.3f}" if score is not None else "n/a"
        print(
            f"Aggregate score: {score_s} (verdict band: "
            f"{results['aggregate']['verdict_band']})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
