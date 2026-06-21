#!/usr/bin/env python3
"""conformal_gate.py — split-conformal abstention over any signal (validation).

Turns "the band reads `uncalibrated`" into "abstain, with a guaranteed error rate
at this operating point." Given operator-supplied calibration **nonconformity**
scores (higher = less like the reference class) and a target score, it emits a
distribution-free, finite-sample **conformal p-value** and a **prediction set** at
coverage 1-alpha.

This is a methodology wrapper, NOT a new detector. It measures nothing about prose;
any existing signal (surprisal, Binoculars ratio, voice delta, KL) can feed it. An
empty prediction set ("unlike the reference at this alpha") and a full prediction
set ("consistent with both classes; the signal can't separate them here") are both
LICENSED outputs — that is the rigor point and the anti-verdict guard made concrete.

The guarantee is marginal and assumes exchangeability of calibration and target; the
p-value is NOT P(AI). One-class mode flags out-of-distribution, not authorship.

Usage:

    python3 scripts/conformal_gate.py --calibration human_scores.txt --score 4.2
    python3 scripts/conformal_gate.py --calibration ref.txt --score 4.2 --alpha 0.05
    python3 scripts/conformal_gate.py --calibration ref.txt --calibration-positive ai.txt \
        --score 4.2 --direction two_sided --json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore

TASK_SURFACE = "validation"
TOOL_NAME = "conformal_gate"
SCRIPT_VERSION = "1.0"

DIRECTIONS = ("higher_is_nonconforming", "lower_is_nonconforming", "two_sided")


def load_scores(path: Path) -> list[float]:
    """Parse a JSON list or newline-delimited floats.

    Raises ``ValueError`` with a clear message on malformed input (a JSON list
    with a non-numeric entry, or a non-numeric line) rather than letting a raw
    conversion traceback escape — main() turns that into a clean exit.
    """
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    # If the file is valid JSON *and* a list, commit to that interpretation —
    # don't fall through to the line parser (which would then try to float()
    # the JSON text itself and raise an uncaught error).
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, list):
        try:
            return [float(x) for x in data]
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{path}: calibration is a JSON list with a non-numeric entry"
            ) from exc
    # Newline-delimited (or a single scalar) fallback.
    out: list[float] = []
    for lineno, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        token = line.split(",")[0]
        try:
            out.append(float(token))
        except ValueError as exc:
            raise ValueError(
                f"{path}:{lineno}: cannot parse calibration score {token!r}"
            ) from exc
    return out


def _nonconformity(values: list[float], direction: str,
                   median: float) -> list[float]:
    if direction == "lower_is_nonconforming":
        return [-v for v in values]
    if direction == "two_sided":
        return [abs(v - median) for v in values]
    return list(values)  # higher_is_nonconforming


def conformal_p(calibration: list[float], score: float, *,
                direction: str) -> float:
    """Split-conformal p-value: (1 + #{cal_nc >= score_nc}) / (n + 1).

    Super-uniform under exchangeability: P(p <= alpha) <= alpha for any
    underlying distribution. Higher nonconformity => smaller p.
    """
    n = len(calibration)
    if n == 0:
        return 1.0
    median = statistics.median(calibration)
    cal_nc = _nonconformity(calibration, direction, median)
    score_nc = _nonconformity([score], direction, median)[0]
    ge = sum(1 for c in cal_nc if c >= score_nc)
    return (1 + ge) / (n + 1)


# Directions that admit a clean one-tailed FPR ceiling. `two_sided` has no
# single tail, so an order-statistic FPR bound is ill-defined there — the
# FPR-bound mode rejects it (review finding P3: direction semantics).
FPR_BOUND_DIRECTIONS = ("higher_is_nonconforming", "lower_is_nonconforming")


def threshold_at_fpr_bound(
    calibration: list[float],
    *,
    fpr_bound: float,
    direction: str,
) -> dict[str, Any]:
    """Conformal threshold whose reference-class false-positive rate is
    provably <= ``fpr_bound`` (= ``q``) under the same finite-sample,
    distribution-free framing as ``conformal_p`` (Multiscaled Conformal
    Prediction, arXiv:2505.05084; v1 ships the single-scale ceiling).

    Concretely: a target is flagged 'out-of-reference' when its
    nonconformity score is >= ``t``. The fraction of calibration
    (reference) scores at or above ``t`` is the empirical reference-class
    false-positive rate. We pick ``t`` as the conformal quantile of the
    calibration nonconformity scores at level ``q`` with the standard +1
    finite-sample correction, so the guaranteed reference-class FPR is
    <= ``q``.

    Pinned to the two ONE-TAILED directions; ``two_sided`` has no single
    tail and is rejected. Pure stdlib; no model. Returns a dict; an empty
    calibration set yields ``available=False``."""
    if direction not in FPR_BOUND_DIRECTIONS:
        return {
            "available": False,
            "reason": (
                f"--fpr-bound requires a one-tailed direction "
                f"({' or '.join(FPR_BOUND_DIRECTIONS)}); got {direction!r}. "
                "A two-sided nonconformity has no single tail, so an "
                "order-statistic FPR ceiling is ill-defined."
            ),
            "mode": "fpr_bound",
            "fpr_bound": fpr_bound,
            "direction": direction,
        }
    n = len(calibration)
    if n == 0:
        return {
            "available": False,
            "reason": "empty calibration set; cannot bound the reference FPR.",
            "mode": "fpr_bound",
            "fpr_bound": fpr_bound,
            "direction": direction,
        }
    median = statistics.median(calibration)
    cal_nc = sorted(_nonconformity(calibration, direction, median))
    # Conformal quantile at level q with the finite-sample +1 correction.
    # We want the smallest threshold t such that #{cal_nc >= t} / n <= q.
    # The (ceil((n+1)*(1-q)))-th order statistic (1-indexed) gives a
    # distribution-free guarantee that at most q of the reference mass is
    # flagged. Clamp the rank into [1, n].
    rank = math.ceil((n + 1) * (1.0 - fpr_bound))
    rank = max(1, min(n, rank))
    threshold = cal_nc[rank - 1]
    # TIE-SAFETY (Codex P1): the flag rule is `score >= threshold`, so ANY calibration scores TIED at
    # the threshold are all flagged. When the tail is tied (e.g. ten identical scores, q=0.1) the
    # order-statistic threshold flags the whole block -> empirical reference FPR 1.0, violating the
    # claimed <= q. Raise the threshold to the smallest calibration value whose at-or-above count
    # keeps the empirical FPR <= q; if no finite value qualifies (the whole tail is tied), flag NOTHING
    # (threshold = +inf, FPR 0). Raising the threshold only ever flags FEWER, so the conformal guarantee
    # for new points is preserved (more conservative), and the reported empirical FPR is honest.
    max_flagged = math.floor(fpr_bound * n + 1e-9)   # FPR <= q  =>  flagged <= floor(q*n)
    def _flagged_at(t: float) -> int:
        return sum(1 for c in cal_nc if c >= t)
    if _flagged_at(threshold) > max_flagged:
        higher = sorted({c for c in cal_nc if c > threshold})
        threshold = next((h for h in higher if _flagged_at(h) <= max_flagged), None)
    if threshold is None:
        # No FINITE threshold keeps the empirical FPR <= q without flagging the whole reference set:
        # the calibration is degenerate (e.g. every score tied), so the only way to honor the bound is
        # to flag nothing — a vacuous gate. Abstain with a reason rather than emit a non-JSON `inf`
        # threshold (Codex P1). Supply calibration with variation to get a usable bound.
        return {
            "available": False,
            "reason": (
                "degenerate calibration: too many tied scores to place a finite threshold that bounds "
                "the reference FPR <= fpr_bound without flagging the whole reference set. Supply "
                "calibration scores with variation."
            ),
            "mode": "fpr_bound",
            "fpr_bound": fpr_bound,
            "direction": direction,
            "n_calibration": n,
        }
    n_flagged = _flagged_at(threshold)
    empirical_fpr = n_flagged / n
    return {
        "available": True,
        "mode": "fpr_bound",
        "fpr_bound": fpr_bound,
        "direction": direction,
        "threshold": threshold,
        "n_calibration": n,
        "order_statistic_rank": rank,
        "empirical_reference_fpr_at_threshold": empirical_fpr,
        "threshold_rule": (
            "a target nonconformity score >= `threshold` is flagged "
            "out-of-reference; the reference-class false-positive rate is "
            "bounded by `fpr_bound` under exchangeability. This is a "
            "reference-class FPR ceiling, NOT P(AI) and NOT a guarantee on "
            "the positive (AI) class."
        ),
    }


def gate_fpr_bound(
    calibration: list[float],
    score: float | None,
    *,
    fpr_bound: float,
    direction: str,
    reference_label: str,
) -> dict[str, Any]:
    """FPR-bound result. ``score`` is optional: with no target the mode
    returns just the bounded threshold; with a target it also reports
    whether the target is inside/outside the bounded reference set."""
    result = threshold_at_fpr_bound(
        calibration, fpr_bound=fpr_bound, direction=direction,
    )
    if not result.get("available"):
        return result
    result["target_score"] = score
    if score is not None:
        median = statistics.median(calibration)
        score_nc = _nonconformity([score], direction, median)[0]
        # Flagged out-of-reference when its nonconformity is >= threshold.
        out_of_reference = score_nc >= result["threshold"]
        result["in_reference_set"] = not out_of_reference
        result["prediction_set"] = [] if out_of_reference else [reference_label]
    return result


def gate_one_class(calibration: list[float], score: float, *, alpha: float,
                   direction: str, reference_label: str) -> dict[str, Any]:
    p = conformal_p(calibration, score, direction=direction)
    in_set = p > alpha
    return {
        "mode": "one_class",
        "alpha": alpha,
        "coverage": round(1 - alpha, 6),
        "direction": direction,
        "target_score": score,
        "n_calibration": len(calibration),
        "p_value": round(p, 6),
        "in_reference_set": in_set,
        "prediction_set": [reference_label] if in_set else [],
    }


def gate_two_class(cal_ref: list[float], cal_pos: list[float], score: float, *,
                   alpha: float, direction: str, reference_label: str,
                   positive_label: str) -> dict[str, Any]:
    p_ref = conformal_p(cal_ref, score, direction=direction)
    p_pos = conformal_p(cal_pos, score, direction=direction)
    pred = []
    if p_ref > alpha:
        pred.append(reference_label)
    if p_pos > alpha:
        pred.append(positive_label)
    return {
        "mode": "two_class",
        "alpha": alpha,
        "coverage": round(1 - alpha, 6),
        "direction": direction,
        "target_score": score,
        "n_calibration": {reference_label: len(cal_ref), positive_label: len(cal_pos)},
        "p_values": {reference_label: round(p_ref, 6), positive_label: round(p_pos, 6)},
        "prediction_set": pred,
    }


def _claim_license() -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "a split-conformal p-value and prediction set for a target "
            "nonconformity score against operator-supplied calibration scores, "
            "with a distribution-free finite-sample coverage guarantee at the "
            "chosen alpha. In --fpr-bound mode, a conformal threshold whose "
            "reference-class false-positive rate is bounded by `q` under "
            "exchangeability (the operator applies the threshold)."
        ),
        does_not_license=(
            "an AI/human verdict. Validity is inherited from the operator's "
            "calibration set and nonconformity score; an empty or full prediction "
            "set is a licensed abstention, not a failure; the guarantee is marginal "
            "and assumes exchangeability of calibration and target — the p-value is "
            "NOT P(AI). The --fpr-bound threshold is a reference-class "
            "false-positive ceiling, NOT P(AI), NOT a guarantee on the positive "
            "(AI) class, and NOT a bound that survives a non-representative "
            "calibration set; it tightens the decision, not the evidence."
        ),
        comparison_set={"mode": "split_conformal"},
        additional_caveats=[
            "Exchangeability assumption: the guarantee holds only if the target is "
            "exchangeable with the calibration scores.",
            "The calibration set must be representative of the reference class; a "
            "biased calibration set gives a biased gate.",
            "One-class mode flags out-of-distribution, not authorship; a low "
            "p-value says 'unlike the reference,' not 'AI.'",
        ],
        references=[
            "plugins/setec-voiceprint/specs/20-conformal-abstention-gate.md",
            "plugins/setec-voiceprint/specs/28-eval-discipline-bundle.md",
            "Multiscaled Conformal Prediction (arXiv:2505.05084) — the "
            "FPR-upper-bound operating mode; v1 ships the single-scale ceiling.",
        ],
    )


def build_payload(results: dict[str, Any], *, target_path: Path | str,
                  available: bool,
                  warnings: list[str] | None = None) -> dict[str, Any]:
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=0,  # non-prose input.
        baseline=None,
        results=results if available else {},
        claim_license=_claim_license() if available else None,
        available=available,
        warnings=warnings,
    )


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        f"# Conformal abstention gate — `{payload['target'].get('path')}`",
        "",
        f"**Task surface:** `{TASK_SURFACE}`  ",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        "",
    ]
    if not payload["available"]:
        lines.append("_No calibration scores — no conformal gate produced._")
        for w in payload.get("warnings", []):
            lines.append(f"- {w}")
        return "\n".join(lines) + "\n"

    r = payload["results"]
    if r.get("mode") == "fpr_bound":
        lines += [
            f"**Mode:** `fpr_bound`  |  **fpr_bound (q):** {r['fpr_bound']}  "
            f"|  **direction:** `{r['direction']}`",
            "",
            "## Conformal FPR-bound threshold",
            "",
            f"- **Threshold:** {r['threshold']}",
            f"- **Empirical reference-class FPR at threshold:** "
            f"{r['empirical_reference_fpr_at_threshold']}",
            f"- **Calibration n:** {r['n_calibration']} "
            f"(order-statistic rank {r['order_statistic_rank']})",
        ]
        if r.get("target_score") is not None:
            lines.append(
                f"- **Target score {r['target_score']}:** in reference set: "
                f"{r.get('in_reference_set')} "
                f"(prediction set {r.get('prediction_set')})"
            )
        lines.append(
            "- _The threshold bounds the reference-class false-positive rate "
            "by q under exchangeability. It is NOT P(AI) and NOT a guarantee "
            "on the positive (AI) class — the operator applies it._"
        )
        lines += ["", payload["claim_license_rendered"] or ""]
        return "\n".join(lines) + "\n"

    lines += [
        f"**Mode:** `{r['mode']}`  |  **alpha:** {r['alpha']} "
        f"(coverage {r['coverage']})  |  **direction:** `{r['direction']}`",
        "",
        "## Conformal decision",
        "",
        f"- **Target score:** {r['target_score']}",
    ]
    if r["mode"] == "one_class":
        lines.append(f"- **p-value:** {r['p_value']}  "
                     f"(in reference set: {r['in_reference_set']})")
    else:
        lines.append(f"- **p-values:** {r['p_values']}")
    lines += [
        f"- **Prediction set (coverage {r['coverage']}):** {r['prediction_set']}",
        "",
        payload["claim_license_rendered"] or "",
    ]
    return "\n".join(lines) + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--calibration", required=True,
                   help="Required: nonconformity scores for the reference class "
                        "(JSON list or newline-delimited floats).")
    # --score is conditionally required: required for the one-class/two-class
    # gate, OPTIONAL when --fpr-bound is supplied (that mode can return just a
    # threshold without a target). Enforced in main() so the default path
    # keeps --score mandatory (review finding P3: CLI contract).
    p.add_argument("--score", required=False, type=float, default=None,
                   help="The target nonconformity score. Required for the "
                        "one-class/two-class gate; optional with --fpr-bound "
                        "(which can emit a threshold without a target).")
    p.add_argument("--calibration-positive",
                   help="Optional: nonconformity scores for a positive class "
                        "(enables two-class prediction-set mode).")
    p.add_argument("--alpha", type=float, default=0.1,
                   help="Miscoverage level (default: 0.1 => coverage 0.9).")
    p.add_argument("--fpr-bound", type=float, default=None,
                   help="Reference-class false-positive ceiling q in (0, 1). "
                        "When supplied, emit the conformal threshold whose "
                        "reference-class FPR is bounded by q under "
                        "exchangeability (Multiscaled Conformal Prediction, "
                        "arXiv:2505.05084) instead of the alpha-coverage gate. "
                        "One-tailed directions only; --score becomes optional. "
                        "NOT P(AI), NOT a guarantee on the AI class.")
    p.add_argument("--direction", choices=DIRECTIONS,
                   default="higher_is_nonconforming",
                   help="Nonconformity direction (default: higher_is_nonconforming).")
    p.add_argument("--reference-label", default="reference",
                   help="Label for the reference class (default: reference).")
    p.add_argument("--positive-label", default="positive",
                   help="Label for the positive class (default: positive).")
    p.add_argument("--json", action="store_true",
                   help="Emit the JSON envelope instead of a markdown report.")
    p.add_argument("--out", help="Write output to this path instead of stdout.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    # The conformal coverage guarantee (coverage = 1 - alpha) is only defined
    # for alpha in the open interval (0, 1); outside it the prediction set and
    # the "coverage" it claims are meaningless (e.g. alpha=2.0 -> coverage=-1.0).
    if not (math.isfinite(args.alpha) and 0.0 < args.alpha < 1.0):
        sys.stderr.write(
            f"--alpha must be a finite value in the open interval (0, 1); "
            f"got {args.alpha}\n"
        )
        return 2
    fpr_bound_mode = args.fpr_bound is not None
    if fpr_bound_mode:
        if not (math.isfinite(args.fpr_bound) and 0.0 < args.fpr_bound < 1.0):
            sys.stderr.write(
                f"--fpr-bound must be a finite value in the open interval "
                f"(0, 1); got {args.fpr_bound}\n"
            )
            return 2
        if args.direction not in FPR_BOUND_DIRECTIONS:
            sys.stderr.write(
                f"--fpr-bound requires a one-tailed --direction "
                f"({' or '.join(FPR_BOUND_DIRECTIONS)}); got "
                f"{args.direction!r}. A two-sided nonconformity has no single "
                f"tail for an FPR ceiling.\n"
            )
            return 2
    # --score is required for the default gate, optional under --fpr-bound.
    if args.score is None and not fpr_bound_mode:
        sys.stderr.write(
            "--score is required for the one-class/two-class gate "
            "(it becomes optional only with --fpr-bound)\n"
        )
        return 2
    if args.score is not None and not math.isfinite(args.score):
        sys.stderr.write(f"--score must be a finite number; got {args.score}\n")
        return 2

    cal_path = Path(args.calibration).expanduser()
    if not cal_path.is_file():
        sys.stderr.write(f"Calibration file not found: {cal_path}\n")
        return 2

    try:
        calibration = load_scores(cal_path)
    except ValueError as exc:
        sys.stderr.write(f"Could not read calibration scores: {exc}\n")
        return 2
    if not calibration:
        payload = build_payload(
            {}, target_path=cal_path, available=False,
            warnings=["Calibration file is empty; cannot compute a conformal gate."],
        )
    elif fpr_bound_mode:
        results = gate_fpr_bound(
            calibration, args.score, fpr_bound=args.fpr_bound,
            direction=args.direction, reference_label=args.reference_label)
        payload = build_payload(
            results, target_path=cal_path, available=bool(results.get("available")),
            warnings=(
                None if results.get("available")
                else [results.get("reason", "FPR-bound mode unavailable")]
            ),
        )
    elif args.calibration_positive:
        pos_path = Path(args.calibration_positive).expanduser()
        if not pos_path.is_file():
            sys.stderr.write(f"Positive calibration file not found: {pos_path}\n")
            return 2
        try:
            cal_pos = load_scores(pos_path)
        except ValueError as exc:
            sys.stderr.write(f"Could not read positive calibration scores: {exc}\n")
            return 2
        if not cal_pos:
            payload = build_payload(
                {}, target_path=cal_path, available=False,
                warnings=["Positive calibration file is empty."],
            )
        else:
            results = gate_two_class(
                calibration, cal_pos, args.score, alpha=args.alpha,
                direction=args.direction, reference_label=args.reference_label,
                positive_label=args.positive_label)
            payload = build_payload(results, target_path=cal_path, available=True)
    else:
        results = gate_one_class(
            calibration, args.score, alpha=args.alpha, direction=args.direction,
            reference_label=args.reference_label)
        payload = build_payload(results, target_path=cal_path, available=True)

    text_out = (json.dumps(payload, indent=2, default=str)
                if args.json else render_report(payload))
    if args.out:
        Path(args.out).write_text(text_out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(text_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
