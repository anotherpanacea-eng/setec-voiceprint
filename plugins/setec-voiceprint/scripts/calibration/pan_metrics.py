#!/usr/bin/env python3
"""pan_metrics.py — the official PAN@CLEF Generative-AI Authorship-
Verification metric suite, re-implemented in stdlib.

This is the scorer half of the Voight-Kampff benchmark harness (spec
``voight-kampff-benchmark-harness``). It re-implements the five PAN
metrics — ``roc_auc``, ``brier``, ``c_at_1``, ``f1``, ``f05u`` — and
their arithmetic ``pan_mean``, **anchored verbatim** to the Apache-2.0
TIRA evaluator:

    pan-webis-de/pan25-generative-ai-authorship-verification
    └─ pan25_genai_detection/evaluator/evaluator.py

The PAN convention (pinned against that reference, not reconstructed
from the metric names):

  * Predictions are in ``[0, 1]``; **higher = more confident the text is
    machine/AI** (the PAN positive class is label ``1``).
  * A prediction of **exactly ``0.5`` is a NON-RESPONSE** (an abstention,
    not a guess). c@1 *rewards* leaving hard cases unanswered, and F0.5u
    treats unanswered cases as the PAN convention requires. This 0.5
    abstention value is the label-free non-response zone — in this
    harness it is wired to the detector's existing two-threshold band
    (e.g. Binoculars' ``indeterminate`` band between ``threshold_low``
    and ``threshold_high``), NOT swept against the PAN labels.
  * ``brier`` here is PAN's **complement** of the Brier loss
    (``1 - brier_loss``), so that — like the other four — higher is
    better. PAN clips predictions to ``[0, 1]`` before scoring.
  * ``roc_auc`` / ``f1`` return ``None`` when undefined (one gold class,
    or a degenerate confusion); ``pan_mean`` counts a ``None`` metric as
    ``0.0`` (PAN: ``np.mean([v or 0.0 for v in results.values()])``).

ANTI-GOODHART: every function here is a pure ``(labels, predictions) ->
score`` read. Nothing in this module fits a threshold, selects an
operating point, or writes any artifact. PAN's own evaluator ships an
``--optimize-score`` operating-point sweep; this harness deliberately
**does not** re-implement it, because sweeping an operating point
against the PAN gold labels is the out-of-bounds move the harness
structurally prevents. The probability transform that maps a detector's
raw score into ``[0, 1]`` lives in the runner and is declared, fixed,
and label-free — never fitted to a PAN metric.

Pure stdlib (``math``); no numpy / sklearn. ``roc_auc`` delegates to
``validation_harness.fallback_roc_auc`` (reuse, not a divergent
reimplementation — AC-9).

References:
  - PAN@CLEF 2025 Generative AI Authorship Verification, Subtask 1
    (Voight-Kampff). Dataset: Zenodo record 14962653.
  - TIRA evaluator (Apache-2.0):
    https://github.com/pan-webis-de/pan25-generative-ai-authorship-verification
  - Peñas & Rodrigo 2011 (c@1). Bevendorff et al. NAACL 2019 (F0.5u).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Sequence

# The scorer reuses validation_harness's ROC-AUC + bootstrap-CI helpers
# verbatim. validation_harness lives one dir up (scripts/), which the
# calibration adapters add to sys.path the same way (see
# mage_to_manifest.py).
SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from validation_harness import (  # noqa: E402
    fallback_roc_auc,
    paired_bootstrap_ci,
)

# The PAN non-response sentinel. A prediction exactly equal to this value
# is an abstention (counted as "unanswered" by c@1 and F0.5u), not a
# guess. Pinned to the TIRA evaluator's ``missing_default`` / the
# ``y_pred == 0.5`` checks in c_at_1 / f05u.
NON_RESPONSE = 0.5

# The five PAN metric keys, in the order PAN's ``evaluate_all`` reports
# them (the order matters for ``pan_mean``, which means over exactly
# these five).
PAN_METRIC_KEYS = ("roc_auc", "brier", "c_at_1", "f1", "f05u")


def _as_floats(labels: Sequence[Any], preds: Sequence[Any]) -> tuple[list[float], list[float]]:
    if len(labels) != len(preds):
        raise ValueError(
            f"labels/preds length mismatch: {len(labels)} != {len(preds)}"
        )
    return [float(y) for y in labels], [float(p) for p in preds]


def roc_auc(labels: Sequence[int], preds: Sequence[float]) -> float | None:
    """ROC-AUC. Delegates to ``validation_harness.fallback_roc_auc``
    (Mann-Whitney U form, no sklearn). Returns ``None`` when the gold
    labels are single-class (PAN: ``len(np.unique(y_true)) != 2``)."""
    return fallback_roc_auc(labels, preds)


def brier(labels: Sequence[int], preds: Sequence[float]) -> float | None:
    """PAN's Brier score: the **complement** of the Brier loss, so higher
    is better. ``1 - mean((clip(p, 0, 1) - y) ** 2)``.

    Verbatim to the TIRA ``brier_score``:
        ``1 - brier_score_loss(y_true, np.clip(y_pred, 0.0, 1.0))``
    (brier_score_loss is the mean squared error of the probability vs the
    binary label)."""
    ys, ps = _as_floats(labels, preds)
    if not ys:
        return None
    loss = 0.0
    for y, p in zip(ys, ps):
        clipped = 0.0 if p < 0.0 else (1.0 if p > 1.0 else p)
        loss += (clipped - y) ** 2
    loss /= len(ys)
    return 1.0 - loss


def c_at_1(labels: Sequence[int], preds: Sequence[float]) -> float | None:
    """PAN's c@1 (Peñas & Rodrigo 2011): rewards leaving hard cases
    unanswered (prediction == 0.5) rather than guessing.

    Verbatim to TIRA ``c_at_1`` (here ``labels`` is PAN's ``y_true`` and
    ``preds`` is PAN's ``y_pred``):

        nu = (y_pred == 0.5)                # non-responses
        nc = sum over ANSWERED of (y_true == (y_pred > 0.5))
        nu = sum(nu)                        # count of non-responses
        return (1/n) * (nc + (nu * nc / n))
    """
    ys, ps = _as_floats(labels, preds)
    n = len(ys)
    if n == 0:
        return None
    nc = 0
    nu = 0
    for y, p in zip(ys, ps):
        if p == NON_RESPONSE:
            nu += 1
            continue
        # answered: predicted-positive iff p > 0.5; correct iff that
        # matches the (boolean) gold label.
        predicted_positive = p > NON_RESPONSE
        gold_positive = y == 1.0
        if predicted_positive == gold_positive:
            nc += 1
    return (1.0 / n) * (nc + (nu * nc / n))


def f1(labels: Sequence[int], preds: Sequence[float]) -> float | None:
    """Standard binary F1 with PAN's decision rule ``pred > 0.5 ==>
    positive``. Returns ``None`` when F1 is undefined (no predicted
    positives AND no actual positives — PAN's ``zero_division=np.nan``).

    Verbatim to TIRA ``f1`` (which calls sklearn ``f1_score(y_true,
    y_pred > 0.5, zero_division=np.nan)`` and returns None on nan).
    """
    ys, ps = _as_floats(labels, preds)
    tp = fp = fn = 0
    for y, p in zip(ys, ps):
        predicted_positive = p > NON_RESPONSE
        gold_positive = y == 1.0
        if predicted_positive and gold_positive:
            tp += 1
        elif predicted_positive and not gold_positive:
            fp += 1
        elif (not predicted_positive) and gold_positive:
            fn += 1
    denom = 2 * tp + fp + fn
    if denom == 0:
        # No positives predicted and none in gold: sklearn's
        # zero_division=np.nan path -> PAN returns None.
        return None
    return (2.0 * tp) / denom


def f05u(labels: Sequence[int], preds: Sequence[float]) -> float | None:
    """PAN's F0.5u (Bevendorff et al. NAACL 2019): an F0.5 variant that
    treats **unanswered** problems (pred == 0.5) as in the PAN
    convention (folded into the denominator alongside false negatives).

    Verbatim to TIRA ``f05u``:

        n_tp = sum(y_true * (y_pred > 0.5))
        n_fn = sum(y_true * (y_pred < 0.5))
        n_fp = sum((1 - y_true) * (y_pred > 0.5))
        n_u  = sum(y_pred == 0.5)
        denom = 1.25*n_tp + 0.25*(n_fn + n_u) + n_fp
        return (1.25*n_tp) / denom        # None if denom == 0
    """
    ys, ps = _as_floats(labels, preds)
    n_tp = n_fn = n_fp = n_u = 0
    for y, p in zip(ys, ps):
        if p == NON_RESPONSE:
            n_u += 1
            continue
        if p > NON_RESPONSE:
            if y == 1.0:
                n_tp += 1
            else:
                n_fp += 1
        elif p < NON_RESPONSE:
            if y == 1.0:
                n_fn += 1
            # (1 - y) * (p < 0.5) is not a PAN term; human-and-negative
            # predictions are true negatives, uncounted, by design.
    denom = 1.25 * n_tp + 0.25 * (n_fn + n_u) + n_fp
    if denom == 0.0:
        return None
    return (1.25 * n_tp) / denom


# The metric-function registry, keyed by the report metric name. Each is
# a ``(labels, preds) -> float | None`` callable — the exact signature
# ``paired_bootstrap_ci`` expects for ``metric_fn`` (verified against
# validation_harness.paired_bootstrap_ci).
METRIC_FUNCS: dict[str, Callable[[Sequence[int], Sequence[float]], float | None]] = {
    "roc_auc": roc_auc,
    "brier": brier,
    "c_at_1": c_at_1,
    "f1": f1,
    "f05u": f05u,
}

# Which metrics need a decision threshold / operating point (i.e. they
# read ``pred > 0.5``). When no operating point is supplied these are
# reported as ``null`` rather than computed against an operating point
# fitted to the PAN labels (anti-Goodhart). ``roc_auc`` and ``brier``
# are rank/probability metrics that need no threshold.
THRESHOLDED_METRICS = ("c_at_1", "f1", "f05u")
RANK_METRICS = ("roc_auc", "brier")


def pan_mean(values: dict[str, float | None]) -> float:
    """The PAN aggregate: arithmetic mean of the five metrics, where a
    ``None`` metric counts as ``0.0``.

    Verbatim to TIRA ``evaluate_all``:
        ``float(np.mean([v or 0.0 for v in results.values()]))``
    over exactly the five metric values.
    """
    nums = [
        (values.get(k) if values.get(k) is not None else 0.0)
        for k in PAN_METRIC_KEYS
    ]
    return sum(float(v) for v in nums) / len(PAN_METRIC_KEYS)


def score_metric_with_ci(
    metric_name: str,
    labels: Sequence[int],
    preds: Sequence[float],
    *,
    n_resamples: int,
    confidence_level: float,
    seed: int | None,
) -> dict[str, Any]:
    """Compute one PAN metric plus a percentile-bootstrap CI.

    The CI uses ``validation_harness.paired_bootstrap_ci`` verbatim
    (which skips single-class resamples — AC-11). Returns a cell dict
    ``{value, ci_low, ci_high, ci_method | ci_reason}``; ``value`` may be
    ``None`` when the metric is undefined on the full sample, in which
    case the CI is reported absent with a reason.
    """
    func = METRIC_FUNCS[metric_name]
    value = func(labels, preds)
    cell: dict[str, Any] = {"value": value}
    if value is None:
        cell["ci_low"] = None
        cell["ci_high"] = None
        cell["ci_method"] = None
        cell["ci_reason"] = "metric_undefined_on_full_sample"
        return cell
    ci = paired_bootstrap_ci(
        labels,
        preds,
        func,
        n_resamples=n_resamples,
        confidence_level=confidence_level,
        seed=seed,
    )
    if ci.get("available"):
        cell["ci_low"] = ci["ci_low"]
        cell["ci_high"] = ci["ci_high"]
        cell["ci_method"] = ci["method"]
        cell["ci_n_valid_resamples"] = ci["n_valid_resamples"]
    else:
        cell["ci_low"] = None
        cell["ci_high"] = None
        cell["ci_method"] = ci.get("method")
        cell["ci_reason"] = ci.get("reason")
    return cell


def score_all(
    labels: Sequence[int],
    preds: Sequence[float],
    *,
    has_operating_point: bool,
    n_resamples: int,
    confidence_level: float,
    seed: int | None,
) -> dict[str, Any]:
    """Score the full PAN metric suite for one detector's
    ``(labels, predictions)`` rows.

    When ``has_operating_point`` is False the thresholded metrics
    (``c_at_1`` / ``f1`` / ``f05u``) are reported as ``null`` with reason
    ``"no_operating_point_without_fitting_to_pan"`` — the harness reports
    ONLY the rank metrics (``roc_auc`` / ``brier``) and never fits a
    threshold to the PAN labels (AC-17, anti-Goodhart). When True, the
    ``preds`` are assumed already thresholded into ``{< 0.5, 0.5, > 0.5}``
    by the runner (via the operator/detector operating point); this
    scorer never derives the operating point itself.
    """
    metrics: dict[str, Any] = {}
    value_for_mean: dict[str, float | None] = {}
    for name in PAN_METRIC_KEYS:
        if name in THRESHOLDED_METRICS and not has_operating_point:
            metrics[name] = {
                "value": None,
                "ci_low": None,
                "ci_high": None,
                "ci_method": None,
                "reason": "no_operating_point_without_fitting_to_pan",
            }
            value_for_mean[name] = None
            continue
        cell = score_metric_with_ci(
            name,
            labels,
            preds,
            n_resamples=n_resamples,
            confidence_level=confidence_level,
            seed=seed,
        )
        metrics[name] = cell
        value_for_mean[name] = cell["value"]
    metrics["pan_mean"] = {"value": pan_mean(value_for_mean)}
    return metrics
