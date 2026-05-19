"""Binoculars threshold calibration.

Runs binoculars_audit against a labelled manifest, derives empirical
threshold-low / threshold-high values via ROC analysis, emits a
calibration report. Output thresholds are operator-side; the framework
default (DEFAULT_THRESHOLD_LOW / DEFAULT_THRESHOLD_HIGH = None) is
unchanged. The script is a means of operator calibration discipline,
not a path to framework-default thresholds.

Implements SPEC_binoculars_threshold_calibration.md v0.1.

CLI:
    python3 binoculars_calibrate.py MANIFEST.jsonl \\
        --scorer ALIAS_OR_HF_ID --observer ALIAS_OR_HF_ID \\
        [--positive-statuses CSV] [--negative-statuses CSV] \\
        [--score-version {auto, v1, v2}] \\
        [--fpr-target FLOAT] [--target-tpr FLOAT] \\
        [--max-entries INT] [--max-entries-seed INT] \\
        [--out PATH] [--out-md PATH] \\
        [--surprisal-dtype auto|fp32|fp16|bf16]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore

import binoculars_audit as bin_audit  # type: ignore


SCRIPT_VERSION = "0.1.0"
TASK_SURFACE = "calibration"
TOOL_NAME = "binoculars_calibrate"

DEFAULT_POSITIVE_STATUSES = ("ai_generated",)
DEFAULT_NEGATIVE_STATUSES = ("pre_ai_human", "human")
DEFAULT_FPR_TARGET = 0.01
DEFAULT_TARGET_TPR = 0.5
MIN_SAMPLE_SIZE = 30
MIN_AUC = 0.6


DEFAULT_LICENSES = (
    "Reports empirical threshold-low / threshold-high values derived "
    "by running Binoculars against the operator's labelled manifest. "
    "Threshold-low is set to achieve the operator's chosen FPR target "
    "on the negative class; threshold-high is set to achieve the "
    "operator's chosen TPR target on the positive class. The output is "
    "operator-side calibration data — the framework's Binoculars "
    "defaults remain None and the operator commits these thresholds "
    "to their own subsequent ``binoculars_audit.py`` invocations."
)

DEFAULT_DOES_NOT_LICENSE = (
    "Does not license framework-default thresholds for any operator's "
    "use case beyond the calibration corpus. The derived thresholds "
    "are operator-side and corpus-specific; transferring them to a "
    "different model pair, corpus, or register requires re-calibration. "
    "Does not license a binary AI/human verdict — even with calibrated "
    "thresholds, the verdict band is one signal in one model pair's "
    "perplexity ratio (v1) or cross-perplexity (v2). Does not control "
    "for memorization (if calibration corpus entries are in either "
    "model's training set, the score distributions will be biased). "
    "Single-corpus calibration does not validate transferability; "
    "cross-corpus validation is operator-side discipline. Bootstrap "
    "confidence intervals on AUC / thresholds are deferred; v1 ships "
    "point estimates only."
)


# ============================================================
# Pure helpers (testable without LLM loads)
# ============================================================


def _distributions(scores: list[float]) -> dict[str, float | None]:
    """Compute summary stats for a list of scores."""
    if not scores:
        return {"n": 0, "mean": None, "median": None, "std": None,
                "p05": None, "p25": None, "p50": None, "p75": None, "p95": None,
                "min": None, "max": None}
    s = sorted(scores)
    n = len(s)
    mean = sum(s) / n
    variance = sum((x - mean) ** 2 for x in s) / n if n > 1 else 0.0
    std = variance ** 0.5

    def _pctile(p: float) -> float:
        if n == 1:
            return s[0]
        k = (n - 1) * p
        f = int(k)
        c = min(f + 1, n - 1)
        return s[f] + (s[c] - s[f]) * (k - f)

    return {
        "n": n,
        "mean": mean,
        "median": _pctile(0.5),
        "std": std,
        "p05": _pctile(0.05),
        "p25": _pctile(0.25),
        "p50": _pctile(0.50),
        "p75": _pctile(0.75),
        "p95": _pctile(0.95),
        "min": s[0],
        "max": s[-1],
    }


def _compute_auc(scores_with_labels: list[tuple[float, int]]) -> float | None:
    """Rank-based AUC for the negative-class direction.

    Convention: lower scores mean positive (AI-likely). AUC computed
    as: probability that a random positive has a LOWER score than a
    random negative. This matches the Hans et al. 2024 convention.

    Returns None when the input has fewer than 2 entries of either
    label (AUC is undefined)."""
    pos = [s for s, lab in scores_with_labels if lab == 1]
    neg = [s for s, lab in scores_with_labels if lab == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    # Wilcoxon-Mann-Whitney U statistic divided by n_pos * n_neg.
    # Count pairs (p, n) where p < n; ties contribute 0.5.
    pair_score = 0.0
    for p in pos:
        for n in neg:
            if p < n:
                pair_score += 1.0
            elif p == n:
                pair_score += 0.5
    return pair_score / (len(pos) * len(neg))


def _derive_thresholds(
    scores_with_labels: list[tuple[float, int]],
    *,
    fpr_target: float = DEFAULT_FPR_TARGET,
    target_tpr: float = DEFAULT_TARGET_TPR,
) -> dict[str, Any]:
    """Derive threshold-low / threshold-high from labelled scores.

    Convention: scores below threshold-low are flagged ai_likely;
    above threshold-high are flagged human_likely; between are
    indeterminate.

    threshold-low is set as the FPR-target percentile of the negative
    class — the score below which only ``fpr_target`` fraction of
    negatives fall. (Equivalently: the score below which we'd flag
    only ``fpr_target`` of negatives as AI.)

    threshold-high is set as the score above which the cumulative
    positive-class TPR drops below ``target_tpr`` — i.e., the score
    above which only ``1 - target_tpr`` fraction of positives sit.
    (Equivalently: the score above which calling 'human_likely'
    catches ``1 - target_tpr`` of the positives as false negatives.)

    Returns dict with derived thresholds + diagnostic metrics.
    """
    pos = sorted(s for s, lab in scores_with_labels if lab == 1)
    neg = sorted(s for s, lab in scores_with_labels if lab == 0)

    if not pos or not neg:
        return {
            "low": None,
            "high": None,
            "fpr_target": fpr_target,
            "target_tpr": target_tpr,
            "tpr_at_low": None,
            "fpr_at_high": None,
            "indeterminate_rate": None,
        }

    # threshold-low: fpr_target percentile of negatives.
    # Negatives sorted ascending; the value below which only fpr_target
    # fraction sit is the fpr_target percentile of the negative class.
    k_low = max(0, min(len(neg) - 1, int(len(neg) * fpr_target)))
    threshold_low = neg[k_low]

    # threshold-high: target_tpr percentile of positives.
    # Positives sorted ascending; we want the score above which only
    # (1 - target_tpr) of positives sit, which is the target_tpr
    # percentile of the positive class.
    k_high = max(0, min(len(pos) - 1, int(len(pos) * target_tpr)))
    threshold_high = pos[k_high]

    # If threshold_low >= threshold_high (overlapping distributions),
    # the indeterminate band has degenerated. Clamp them so high >= low
    # but flag in the caller.
    # (We don't clamp here — let the caller surface the gate failure
    # via the indeterminate_rate metric.)

    # Diagnostic metrics: TPR at threshold_low, FPR at threshold_high.
    tpr_at_low = sum(1 for p in pos if p < threshold_low) / len(pos)
    fpr_at_high = sum(1 for n in neg if n > threshold_high) / len(neg)

    # Indeterminate rate: fraction of ALL labelled entries between
    # threshold_low and threshold_high.
    all_scores = pos + neg
    if threshold_low <= threshold_high:
        in_between = sum(
            1 for s in all_scores
            if threshold_low <= s <= threshold_high
        )
    else:
        in_between = 0
    indeterminate_rate = in_between / len(all_scores) if all_scores else 0.0

    return {
        "low": threshold_low,
        "high": threshold_high,
        "fpr_target": fpr_target,
        "target_tpr": target_tpr,
        "tpr_at_low": tpr_at_low,
        "fpr_at_high": fpr_at_high,
        "indeterminate_rate": indeterminate_rate,
    }


def _evaluate_gates(
    *,
    pos_count: int,
    neg_count: int,
    auc: float | None,
    pos_mean: float | None,
    neg_mean: float | None,
) -> dict[str, bool]:
    """Compute the discipline-gate verdicts."""
    polarity_correct = (
        pos_mean is not None
        and neg_mean is not None
        and pos_mean < neg_mean
    )
    return {
        "polarity_correct": bool(polarity_correct),
        "sufficient_positives": pos_count >= MIN_SAMPLE_SIZE,
        "sufficient_negatives": neg_count >= MIN_SAMPLE_SIZE,
        "auc_sufficient": auc is not None and auc >= MIN_AUC,
    }


def _build_caveats(
    *,
    gates: dict[str, bool],
    pos_count: int,
    neg_count: int,
    auc: float | None,
    derived: dict[str, Any],
) -> list[str]:
    caveats: list[str] = []
    if not gates["polarity_correct"]:
        caveats.append(
            "polarity_inverted: positives (AI) should have LOWER scores "
            "than negatives (human); empirical distribution is reversed"
        )
    if not gates["sufficient_positives"]:
        caveats.append(
            f"insufficient_positives: n={pos_count} < {MIN_SAMPLE_SIZE} "
            f"(thresholds derived from this many positives are too noisy "
            f"to be defensible)"
        )
    if not gates["sufficient_negatives"]:
        caveats.append(
            f"insufficient_negatives: n={neg_count} < {MIN_SAMPLE_SIZE}"
        )
    if not gates["auc_sufficient"]:
        caveats.append(
            f"low_auc: {auc:.3f} < {MIN_AUC} (score discrimination too "
            f"weak; thresholds are provisional)"
            if auc is not None
            else "low_auc: AUC undefined"
        )
    if (derived.get("low") is not None and derived.get("high") is not None
            and derived["low"] >= derived["high"]):
        caveats.append(
            f"degenerate_thresholds: threshold_low ({derived['low']:.4f}) "
            f">= threshold_high ({derived['high']:.4f}); distributions "
            f"overlap too much for a meaningful indeterminate band"
        )
    return caveats


# ============================================================
# Manifest loading + scoring
# ============================================================


def _load_manifest(manifest_path: Path) -> list[dict[str, Any]]:
    """Load a JSONL manifest. Skips empty lines and rejects malformed
    JSON with a clear error naming the offending line."""
    entries: list[dict[str, Any]] = []
    with manifest_path.open(encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entries.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"manifest line {i}: malformed JSON: {exc}"
                ) from exc
    return entries


def _label_for_status(
    status: str | None,
    positive: set[str], negative: set[str],
) -> int | None:
    if status in positive:
        return 1
    if status in negative:
        return 0
    return None


def _resolve_use_xppl(score_version: str) -> bool | None:
    if score_version == "auto":
        return None
    if score_version == "v2":
        return True
    return False


def calibrate(
    *,
    manifest_path: Path,
    scorer,
    observer,
    positive_statuses: set[str],
    negative_statuses: set[str],
    score_version: str = "auto",
    fpr_target: float = DEFAULT_FPR_TARGET,
    target_tpr: float = DEFAULT_TARGET_TPR,
    max_entries: int | None = None,
    max_entries_seed: int = 42,
    audit_fn=None,
) -> dict[str, Any]:
    """Run calibration end-to-end. Returns the results dict for
    composition into the build_output() envelope.

    ``audit_fn`` is a test injection point: ``audit_fn(text, scorer,
    observer, score_version) -> {"perplexity_ratio": float | None,
    "score_version": str}``. Production callers pass None and the
    function calls binoculars_audit.audit() directly.
    """
    entries = _load_manifest(manifest_path)

    if max_entries is not None and len(entries) > max_entries:
        rng = random.Random(max_entries_seed)
        rng.shuffle(entries)
        entries = entries[:max_entries]

    use_xppl = _resolve_use_xppl(score_version)
    scores_with_labels: list[tuple[float, int]] = []
    per_entry: list[dict[str, Any]] = []
    n_skipped_unlabelled = 0
    n_skipped_missing_path = 0
    n_skipped_score_failed = 0
    resolved_score_version: str | None = None

    for entry in entries:
        label = _label_for_status(
            entry.get("ai_status"), positive_statuses, negative_statuses,
        )
        if label is None:
            n_skipped_unlabelled += 1
            continue
        path_str = entry.get("path") or entry.get("_resolved_path")
        if not path_str:
            n_skipped_missing_path += 1
            continue
        path = Path(path_str)
        if not path.is_absolute():
            # Resolve relative to the manifest directory (standard
            # framework convention from validation_harness).
            path = manifest_path.parent / path
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            n_skipped_missing_path += 1
            continue

        if audit_fn is None:
            audit_result = bin_audit.audit(
                text, scorer=scorer, observer=observer,
                use_cross_perplexity=use_xppl,
            )
        else:
            audit_result = audit_fn(
                text, scorer, observer, score_version,
            )

        ratio = audit_result.get("perplexity_ratio")
        sv = audit_result.get("score_version")
        if resolved_score_version is None and sv is not None:
            resolved_score_version = sv
        if ratio is None:
            n_skipped_score_failed += 1
            continue
        scores_with_labels.append((float(ratio), label))
        per_entry.append({
            "path": str(path),
            "label": label,
            "score": float(ratio),
            "score_version": sv,
        })

    pos_scores = [s for s, lab in scores_with_labels if lab == 1]
    neg_scores = [s for s, lab in scores_with_labels if lab == 0]

    pos_dist = _distributions(pos_scores)
    neg_dist = _distributions(neg_scores)
    auc = _compute_auc(scores_with_labels)
    derived = _derive_thresholds(
        scores_with_labels,
        fpr_target=fpr_target,
        target_tpr=target_tpr,
    )
    gates = _evaluate_gates(
        pos_count=len(pos_scores),
        neg_count=len(neg_scores),
        auc=auc,
        pos_mean=pos_dist.get("mean"),
        neg_mean=neg_dist.get("mean"),
    )
    caveats = _build_caveats(
        gates=gates,
        pos_count=len(pos_scores),
        neg_count=len(neg_scores),
        auc=auc,
        derived=derived,
    )
    if n_skipped_unlabelled:
        caveats.append(f"skipped_unlabelled_entries:{n_skipped_unlabelled}")
    if n_skipped_missing_path:
        caveats.append(f"skipped_missing_path:{n_skipped_missing_path}")
    if n_skipped_score_failed:
        caveats.append(f"skipped_score_failed:{n_skipped_score_failed}")

    return {
        "scorer": {
            "model_id": getattr(scorer, "model_id", None),
            "identifier_block": (
                scorer.identifier_block()
                if hasattr(scorer, "identifier_block") else None
            ),
        },
        "observer": {
            "model_id": getattr(observer, "model_id", None),
            "identifier_block": (
                observer.identifier_block()
                if hasattr(observer, "identifier_block") else None
            ),
        },
        "score_version": resolved_score_version,
        "n_entries_scored": len(scores_with_labels),
        "n_positives": len(pos_scores),
        "n_negatives": len(neg_scores),
        "positive_statuses": sorted(positive_statuses),
        "negative_statuses": sorted(negative_statuses),
        "distributions": {
            "positive": pos_dist,
            "negative": neg_dist,
        },
        "auc": auc,
        "derived_thresholds": derived,
        "gates": gates,
        "per_entry_scores": per_entry,
        "caveats": caveats,
    }


# ============================================================
# Envelope + markdown
# ============================================================


def compose_envelope(
    *,
    manifest_path: Path,
    results: dict[str, Any],
    licenses_text: str = DEFAULT_LICENSES,
    does_not_license_text: str = DEFAULT_DOES_NOT_LICENSE,
) -> dict[str, Any]:
    caveats = list(results.get("caveats", []))

    license_block = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=licenses_text,
        does_not_license=does_not_license_text,
        comparison_set={
            "scorer_model": (results.get("scorer") or {}).get("model_id"),
            "observer_model": (results.get("observer") or {}).get("model_id"),
            "score_version": results.get("score_version"),
            "n_positives": results.get("n_positives"),
            "n_negatives": results.get("n_negatives"),
            "fpr_target": results.get("derived_thresholds", {}).get("fpr_target"),
            "target_tpr": results.get("derived_thresholds", {}).get("target_tpr"),
        },
        additional_caveats=caveats,
        references=[
            "Hans et al. 2024, 'Spotting LLMs With Binoculars: Zero-Shot Detection of Machine-Generated Text'",
        ],
    )

    n_total = results.get("n_entries_scored", 0)
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=manifest_path,
        target_words=n_total,  # use n_entries as the count for this calibration surface
        baseline=None,
        results=results,
        claim_license=license_block,
        available=n_total > 0,
        warnings=caveats,
    )


def render_markdown(envelope: dict[str, Any]) -> str:
    results = envelope["results"]

    lines: list[str] = []
    lines.append("# Binoculars Threshold Calibration")
    lines.append("")
    lines.append(f"- **Manifest:** `{envelope['target'].get('path')}`")
    lines.append(f"- **Scorer:** `{(results['scorer'] or {}).get('model_id')}`")
    lines.append(f"- **Observer:** `{(results['observer'] or {}).get('model_id')}`")
    lines.append(f"- **Score version:** `{results.get('score_version')}`")
    lines.append(f"- **N entries scored:** {results.get('n_entries_scored')}  (positives: {results.get('n_positives')}, negatives: {results.get('n_negatives')})")
    auc = results.get("auc")
    lines.append(f"- **AUC:** {f'{auc:.3f}' if auc is not None else '(undefined)'}")
    lines.append("")

    # Distributions table.
    lines.append("## Score distributions")
    lines.append("")
    lines.append("| Class | N | Mean | Median | Std | P05 | P95 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for class_name, key in (("Positive (AI)", "positive"), ("Negative (human)", "negative")):
        d = results.get("distributions", {}).get(key, {})
        if d.get("n", 0) == 0:
            lines.append(f"| {class_name} | 0 | — | — | — | — | — |")
        else:
            lines.append(
                f"| {class_name} | {d['n']} | "
                f"{d['mean']:.4f} | {d['median']:.4f} | {d['std']:.4f} | "
                f"{d['p05']:.4f} | {d['p95']:.4f} |"
            )
    lines.append("")

    # Recommended thresholds.
    derived = results.get("derived_thresholds", {}) or {}
    lines.append("## Derived thresholds")
    lines.append("")
    lines.append(f"- **FPR target (operator-chosen):** {derived.get('fpr_target')}")
    lines.append(f"- **Target TPR (operator-chosen):** {derived.get('target_tpr')}")
    low = derived.get("low")
    high = derived.get("high")
    lines.append(f"- **Threshold-low** (score below → ai_likely): {f'{low:.4f}' if low is not None else '(unavailable)'}")
    lines.append(f"- **Threshold-high** (score above → human_likely): {f'{high:.4f}' if high is not None else '(unavailable)'}")
    tpr = derived.get("tpr_at_low")
    fpr = derived.get("fpr_at_high")
    ind = derived.get("indeterminate_rate")
    lines.append(f"- **TPR at threshold-low:** {f'{tpr:.3f}' if tpr is not None else '(unavailable)'}")
    lines.append(f"- **FPR at threshold-high:** {f'{fpr:.3f}' if fpr is not None else '(unavailable)'}")
    lines.append(f"- **Indeterminate rate:** {f'{ind:.3f}' if ind is not None else '(unavailable)'}")
    lines.append("")
    if low is not None and high is not None:
        lines.append("**To use these thresholds in subsequent audits:**")
        lines.append("")
        lines.append("```")
        lines.append(f"python3 binoculars_audit.py TARGET.txt \\")
        lines.append(f"    --scorer {(results['scorer'] or {}).get('model_id')} \\")
        lines.append(f"    --observer {(results['observer'] or {}).get('model_id')} \\")
        lines.append(f"    --threshold-low {low:.4f} \\")
        lines.append(f"    --threshold-high {high:.4f}")
        lines.append("```")
        lines.append("")

    # Gate verdicts.
    gates = results.get("gates", {}) or {}
    lines.append("## Discipline gates")
    lines.append("")
    for name, ok in gates.items():
        marker = "✓" if ok else "✗"
        lines.append(f"- {marker} **{name}:** {ok}")
    lines.append("")

    # Caveats.
    caveats = results.get("caveats", [])
    lines.append("## Caveats")
    lines.append("")
    if caveats:
        for c in caveats:
            lines.append(f"- {c}")
    else:
        lines.append("(none surfaced)")
    lines.append("")

    # Claim license.
    lines.append("## Claim license")
    lines.append("")
    lines.append(envelope["claim_license_rendered"].rstrip())
    lines.append("")

    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================


def _parse_csv(s: str) -> set[str]:
    return {x.strip() for x in s.split(",") if x.strip()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Binoculars threshold calibration against a labelled manifest."
    )
    parser.add_argument("manifest", help="Path to JSONL manifest with ai_status labels.")
    parser.add_argument("--scorer", default=bin_audit.DEFAULT_SCORER)
    parser.add_argument("--observer", default=bin_audit.DEFAULT_OBSERVER)
    parser.add_argument("--scorer-revision", default=None)
    parser.add_argument("--observer-revision", default=None)
    parser.add_argument("--surprisal-dtype", choices=("auto", "fp32", "fp16", "bf16"), default="auto")
    parser.add_argument("--positive-statuses", default=",".join(DEFAULT_POSITIVE_STATUSES))
    parser.add_argument("--negative-statuses", default=",".join(DEFAULT_NEGATIVE_STATUSES))
    parser.add_argument("--score-version", choices=("auto", "v1", "v2"), default="auto")
    parser.add_argument("--fpr-target", type=float, default=DEFAULT_FPR_TARGET)
    parser.add_argument("--target-tpr", type=float, default=DEFAULT_TARGET_TPR)
    parser.add_argument("--max-entries", type=int, default=None)
    parser.add_argument("--max-entries-seed", type=int, default=42)
    parser.add_argument("--out", default=None)
    parser.add_argument("--out-md", default=None)
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"error: manifest not found at {manifest_path}", file=sys.stderr)
        return 1

    try:
        scorer = bin_audit.SurprisalBackend(
            model_id=args.scorer,
            revision=args.scorer_revision,
            dtype=args.surprisal_dtype,
        )
    except bin_audit.SurprisalBackendError as exc:
        print(f"error: scorer backend construction failed ({args.scorer}): {exc}", file=sys.stderr)
        return 3
    try:
        observer = bin_audit.SurprisalBackend(
            model_id=args.observer,
            revision=args.observer_revision,
            dtype=args.surprisal_dtype,
        )
    except bin_audit.SurprisalBackendError as exc:
        print(f"error: observer backend construction failed ({args.observer}): {exc}", file=sys.stderr)
        return 3

    try:
        results = calibrate(
            manifest_path=manifest_path,
            scorer=scorer,
            observer=observer,
            positive_statuses=_parse_csv(args.positive_statuses),
            negative_statuses=_parse_csv(args.negative_statuses),
            score_version=args.score_version,
            fpr_target=args.fpr_target,
            target_tpr=args.target_tpr,
            max_entries=args.max_entries,
            max_entries_seed=args.max_entries_seed,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except bin_audit.SurprisalBackendError as exc:
        print(f"error: scoring failed: {exc}", file=sys.stderr)
        return 3

    envelope = compose_envelope(
        manifest_path=manifest_path,
        results=results,
    )
    markdown = render_markdown(envelope)

    out_json = Path(args.out) if args.out else manifest_path.with_suffix(manifest_path.suffix + ".binoculars_calibration.json")
    out_md = Path(args.out_md) if args.out_md else manifest_path.with_suffix(manifest_path.suffix + ".binoculars_calibration.md")
    out_json.write_text(json.dumps(envelope, indent=2, default=str), encoding="utf-8")
    out_md.write_text(markdown, encoding="utf-8")
    print(f"Wrote {out_json} + {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
