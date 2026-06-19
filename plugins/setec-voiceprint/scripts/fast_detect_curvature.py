#!/usr/bin/env python3
"""fast_detect_curvature.py — Fast-DetectGPT conditional-curvature audit.

Computes the Fast-DetectGPT conditional-probability **curvature** of a
target text under a single scoring causal LM (Bao et al., Fast-DetectGPT,
ICLR 2024, MIT). The curvature is a zero-shot discrimination-evidence
statistic that is genuinely distinct from Binoculars' cross-perplexity
ratio (``binoculars_audit.py``) and from the DivEye-style per-token
surprisal moments (``surprisal_audit.py``):

  * Binoculars asks "how much more predictable is x under a scorer than
    under an observer?" — it needs a model *pair* and reports the ratio
    of two perplexities.
  * Fast-DetectGPT asks "does x sit at a local *peak* of one model's
    conditional probability surface?" — it needs a single model and
    reports a curvature z-score: how far the actual text's conditional
    log-prob lies above the distribution of conditional log-probs for
    alternative tokens sampled from the model's own per-position
    conditional distributions.

Implements spec ``specs/03-fast-detectgpt-curvature.md``.

Method (per Bao et al. 2024, the sampling estimator)
----------------------------------------------------
For a text x tokenized as (x_1 .. x_N), the scoring model produces, at
each position t, the conditional log-prob distribution ``log p(. | x_<t)``
over the vocabulary. Define the per-position conditional log-prob of the
actual next token as ``lp_t = log p(x_{t+1} | x_<=t)``.

The reference distribution is built by, at each position t, sampling
``n_samples`` alternative tokens x̃ from the model's own conditional
distribution ``p(. | x_<=t)`` and reading off their conditional
log-probs. This gives, per position, an empirical mean ``mu_t`` and
variance ``var_t`` of ``log p(x̃ | x_<=t)`` under the model's own
conditional.

The conditional-curvature discrepancy is the standardized gap between
the actual text's total conditional log-prob and the reference mean,
normalized by the reference standard deviation (a z-score):

    curvature = ( sum_t lp_t  -  sum_t mu_t ) / sqrt( sum_t var_t )

A higher curvature means the actual tokens are *more* probable than the
model's typical alternatives at the same positions — i.e. the text sits
nearer a local maximum of the model's conditional probability surface,
the signal Fast-DetectGPT associates with machine generation. We report
the raw z-score and (optionally) the per-position series; we ship **no**
threshold and therefore emit **no** verdict band.

Orthogonality
-------------
The statistic is computed independently of any Binoculars number and of
any DivEye surprisal field: it reads only this single model's conditional
log-probs (actual + sampled), via ``CurvatureBackend.score_curvature``.
It never references a second model, a cross-perplexity, or a surprisal
mean/sd/acf series. ``test_orthogonal_statistic`` pins this.

Determinism
-----------
The sampling step is seeded (``--seed``; default 0). With a fixed seed
and a deterministic stub backend the curvature score is stable, which
``test_curvature_deterministic_with_seed`` pins. Production sampling
draws from the model's conditional via a seeded ``random.Random`` so a
re-run with the same seed + same model + same text reproduces the score.

Backend
-------
Model loading is delegated entirely to ``surprisal_backend.SurprisalBackend``
(reused, never re-implemented). The real-model scoring path lives in
``score_curvature_with_backend`` and uses
``SurprisalBackend.score_text_with_distributions`` to obtain the
per-position conditional log-prob distributions; the curvature math then
runs over those distributions. Tests inject a ``score_curvature`` callable
(or a stub backend) so no real model loads and no GPU is touched in CI.

CLI
---
    python3 plugins/setec-voiceprint/scripts/fast_detect_curvature.py TARGET \\
        [--model ALIAS] [--n-samples N] \\
        [--surprisal-dtype auto|fp32|fp16|bf16] [--device DEVICE] \\
        [--seed SEED] [--per-position] [--json] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore
from surprisal_backend import (  # type: ignore
    DEFAULT_MODEL,
    SurprisalBackend,
    SurprisalBackendError,
    resolve_model_arg,
)


SCRIPT_VERSION = "0.1.0"
# NEW sibling Surface-5 value, per spec §Contract. NOT folded under a
# shared `discrimination_evidence` surface — that refactor (which would
# touch binoculars_audit's tag) is left as a maintainer decision per the
# spec's Open Question.
TASK_SURFACE = "discrimination_curvature"
TOOL_NAME = "fast_detect_curvature"
SCORE_VERSION = "fast_detectgpt_sampling_curvature_v1"

DEFAULT_N_SAMPLES = 10000
DEFAULT_SEED = 0
# Below this many scored positions the z-score is too noisy to be a
# stable estimate; mirrors binoculars_audit.MIN_STABLE_TOKENS.
MIN_STABLE_TOKENS = 50

# T-Detect (spec 25; arXiv:2507.23577): Student-t tail-aware normalization, opt-in via
# --tail student-t. nu = 5 is the paper's fixed value (robust over 3..7).
DEFAULT_T_DF = 5
# Appended to does_not_license ONLY when the student-t mode runs (so the gaussian-mode claim
# license is byte-identical). The DELIVERABLE is the score curvature_t (the statistic the paper
# and its reference implementation expose); NO p-value is emitted (it would be unsupported).
STUDENT_T_CAVEAT = (
    "Under --tail student-t the deliverable is curvature_t — the T-Detect t-standardized "
    "curvature SCORE 𝒟ₜ (the statistic the paper and reference implementation expose). It is a "
    "global constant rescale of the Gaussian curvature_score (= curvature_score / sqrt(nu/(nu-2))), "
    "so its discrimination RANKING equals the Gaussian z; what T-Detect changes is the reference "
    "scale, not the ranking. NO p-value is emitted: curvature_t is a rescaled (asymptotically "
    "Gaussian) z-score, not a Student-t variate, so a t-survival of it would be an UNSUPPORTED "
    "transform, not a calibrated probability. curvature_t is a value, NOT a verdict and NOT a "
    "shipped threshold; the operator supplies any band."
)


DEFAULT_LICENSES = (
    "Reports the Fast-DetectGPT conditional-probability curvature of the "
    "target text under a single scoring causal LM (Bao et al. 2024): the "
    "standardized gap (a z-score) between the text's conditional "
    "log-probability and the distribution of conditional log-probabilities "
    "for alternative tokens sampled from the model's own per-position "
    "conditional distributions. Higher curvature means the text sits "
    "nearer a local maximum of the model's probability surface. The score "
    "is a numeric measurement against the chosen model M; it is not a "
    "verdict. The statistic is computed independently of Binoculars' "
    "cross-perplexity ratio and of DivEye-style surprisal moments — it is "
    "an orthogonal zero-shot discrimination-evidence signal."
)

DEFAULT_DOES_NOT_LICENSE = (
    "Does not license a binary AI/human authorship verdict. The curvature "
    "is one measurement under one model; operator judgment remains the "
    "load-bearing decision step. Ships WITHOUT a framework-calibrated "
    "threshold by default: there is no shipped operating point, so the "
    "audit reports the raw z-score only and emits no verdict band. Any "
    "AI-likely / human-likely label requires operator-supplied thresholds "
    "calibrated for this model, corpus, and register, applied downstream "
    "(the existing calibration pipeline, exactly like binoculars_audit). "
    "In-distribution caveat: Fast-DetectGPT's published discrimination is "
    "strongest when the text is in-distribution for the scoring model; "
    "out-of-distribution prose (different domain, language, or register "
    "than the model's training data) degrades the signal. Paraphrase "
    "sensitivity: the curvature signal is sensitive to paraphrasing and "
    "other surface rewrites, which can move the score substantially "
    "without changing authorship. Does not control for memorization (text "
    "in the model's training set biases the conditional log-probs). Does "
    "not substitute for Binoculars, DivEye, stylometric, or embedding "
    "audits — it complements them as an additional orthogonal axis."
)


_WORD_RE = re.compile(r"[A-Za-z']+")


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text.lower()))


# =====================================================================
# Curvature math
# =====================================================================
#
# The estimator works over a per-position record produced by the
# scoring backend. Each record is a tuple:
#
#     (actual_log_prob, sampled_log_probs)
#
# where ``actual_log_prob`` is ``log p(x_{t+1} | x_<=t)`` (the conditional
# log-prob of the token actually present, in nats) and
# ``sampled_log_probs`` is a list of conditional log-probs (in nats) read
# off the SAME conditional distribution for ``n_samples`` tokens drawn
# from ``p(. | x_<=t)``. The reference per-position mean / variance are
# the empirical mean / variance of ``sampled_log_probs``.
#
# Keeping the math in this small pure function (no torch, no model) is
# what makes the detector unit-testable against a deterministic stub.


def _mean(xs: Sequence[float]) -> float:
    if not xs:
        return 0.0
    return sum(xs) / len(xs)


def _pop_variance(xs: Sequence[float], mean: float | None = None) -> float:
    """Population variance (divide by N). Sampling-estimator reference
    variance; we want the variance of the empirical sample set, not an
    unbiased estimator of a parent population."""
    if len(xs) < 1:
        return 0.0
    m = _mean(xs) if mean is None else mean
    return sum((x - m) ** 2 for x in xs) / len(xs)


def curvature_from_positions(
    positions: Sequence[tuple[float, Sequence[float]]],
) -> dict[str, Any]:
    """Compute the Fast-DetectGPT sampling curvature z-score.

    ``positions`` is the per-position series described above. Returns a
    dict with the headline ``curvature_score`` (a float, or ``None`` when
    the reference variance is degenerate), the ``n_tokens`` scored, and
    the ``per_position`` discrepancy contributions (the standardized
    per-position gap, useful for a heatmap), plus the raw component sums.

    The headline:

        curvature = ( sum_t lp_t - sum_t mu_t ) / sqrt( sum_t var_t )

    where ``lp_t`` is the actual-token conditional log-prob, ``mu_t`` the
    sampled mean, and ``var_t`` the sampled (population) variance at
    position t. ``None`` when ``sum_t var_t`` is ~0 (e.g. every sampled
    distribution was a point mass), since the z-score is then undefined.
    """
    n = len(positions)
    actual_sum = 0.0
    mu_sum = 0.0
    var_sum = 0.0
    per_position: list[float | None] = []
    for actual_lp, sampled in positions:
        mu_t = _mean(sampled)
        var_t = _pop_variance(sampled, mu_t)
        actual_sum += actual_lp
        mu_sum += mu_t
        var_sum += var_t
        # Per-position standardized contribution (None when the local
        # reference is a point mass — the position carries no curvature
        # information on its own).
        if var_t > 1e-12:
            per_position.append((actual_lp - mu_t) / math.sqrt(var_t))
        else:
            per_position.append(None)

    if var_sum > 1e-12:
        curvature_score: float | None = (actual_sum - mu_sum) / math.sqrt(var_sum)
    else:
        curvature_score = None

    return {
        "curvature_score": curvature_score,
        "n_tokens": n,
        "actual_log_prob_sum_nats": actual_sum,
        "reference_mean_sum_nats": mu_sum,
        "reference_variance_sum_nats2": var_sum,
        "per_position": per_position,
    }


# =====================================================================
# Real-model scoring path (the maintainer's GPU smoke integration point)
# =====================================================================


def score_curvature_with_backend(
    backend: SurprisalBackend,
    text: str,
    *,
    n_samples: int,
    seed: int,
) -> list[tuple[float, list[float]]]:
    """Build the per-position curvature record from a real causal LM.

    THIS is the function the maintainer's GPU/real-model smoke exercises.
    It is the only place that touches the model. It:

      1. calls ``backend.score_text_with_distributions(text)`` to obtain
         ``(surprisal_bits, log_probs_nats, token_ids)`` — the per-position
         conditional log-prob distributions over the vocabulary, in nats
         (``log_probs_nats[t][v] = log p(token_v | x_<=t)``);
      2. for each position t, reads the actual next token's conditional
         log-prob from the aligned per-position surprisal series
         (``-surprisal_bits[t] * ln 2``), which stays correct even when the
         backend chunks long inputs;
      3. samples ``n_samples`` token ids from the categorical
         ``exp(log_probs_nats[t])`` (seeded) and reads their conditional
         log-probs off the same distribution.

    Returns the ``positions`` list consumed by ``curvature_from_positions``.

    Notes for the smoke:
      * No second model is loaded; this is single-model by construction.
      * ``score_text_with_distributions`` already exists on
        ``SurprisalBackend`` (it was added for Binoculars v2) and returns
        plain Python lists, so no torch is required at this layer — the
        sampling uses stdlib ``random``. For very large vocabularies a
        future refinement could push the categorical sampling into torch
        on-device; the current path is correct and deterministic but
        materializes the per-position distributions in Python.
      * The backend chunks inputs longer than the model context window and
        drops one prediction per chunk boundary, so ``log_probs_nats`` has
        length ``total_len - n_chunks`` (equal to ``len(token_ids) - 1``
        only when unchunked). The actual-token log-prob is therefore taken
        from the lockstep ``surprisal_bits`` series, never by indexing
        ``token_ids``.
    """
    surprisal_bits, log_probs_nats, _token_ids = (
        backend.score_text_with_distributions(text)
    )
    # surprisal_bits and log_probs_nats are extended together, one entry
    # per scored position, so they are always the same length. A mismatch
    # would mean a broken backend contract — fail loudly rather than read
    # a misaligned actual-token log-prob.
    if len(surprisal_bits) != len(log_probs_nats):
        raise SurprisalBackendError(
            "score_text_with_distributions returned misaligned series: "
            f"{len(surprisal_bits)} surprisals vs "
            f"{len(log_probs_nats)} distributions"
        )
    rng = random.Random(seed)
    ln2 = math.log(2.0)
    positions: list[tuple[float, list[float]]] = []
    for t, dist_nats in enumerate(log_probs_nats):
        # Actual next-token conditional log-prob (nats). Read from the
        # per-position surprisal series, NOT dist_nats[token_ids[t + 1]]:
        # the backend's chunked path (inputs longer than the model's
        # context window) drops one prediction at each chunk boundary, so
        # len(log_probs_nats) == total_len - n_chunks and token_ids[t + 1]
        # desynchronizes from dist_nats after the first chunk — silently
        # reading the wrong token's log-prob and corrupting the curvature
        # sum. surprisal_bits is built in lockstep with log_probs_nats
        # (both .extend()ed per chunk), so it stays aligned at any length.
        # bits -> nats: log p = -surprisal_bits * ln(2).
        actual_lp = -surprisal_bits[t] * ln2
        # Sample alternative tokens from p(. | x_<=t) = exp(dist_nats).
        weights = [math.exp(lp) for lp in dist_nats]
        sampled_ids = rng.choices(
            range(len(dist_nats)), weights=weights, k=n_samples,
        )
        sampled_lps = [dist_nats[i] for i in sampled_ids]
        positions.append((actual_lp, sampled_lps))
    return positions


# =====================================================================
# Audit
# =====================================================================


def audit(
    target_text: str,
    *,
    model: SurprisalBackend | None = None,
    n_samples: int = DEFAULT_N_SAMPLES,
    seed: int = DEFAULT_SEED,
    score_fn: Callable[..., list[tuple[float, Sequence[float]]]] | None = None,
    tail: str = "gaussian",
    t_df: int = DEFAULT_T_DF,
) -> dict[str, Any]:
    """Run the curvature audit. Returns the ``results`` dict wrapped into
    the ``build_output()`` envelope.

    ``score_fn`` is the test/stub injection point. When supplied it must
    be a callable ``score_fn(model, text, *, n_samples, seed) -> positions``
    returning the per-position ``(actual_log_prob, sampled_log_probs)``
    series. Production callers pass ``score_fn=None`` and the audit uses
    ``score_curvature_with_backend`` against the real backend. This mirrors
    ``binoculars_audit.audit``'s ``score_fn`` testability hook so no real
    model loads in tests.
    """
    caveats: list[str] = []

    if score_fn is None:
        positions = score_curvature_with_backend(
            model, target_text, n_samples=n_samples, seed=seed,
        )
    else:
        positions = score_fn(
            model, target_text, n_samples=n_samples, seed=seed,
        )

    stats = curvature_from_positions(positions)

    n_tokens = stats["n_tokens"]
    if n_tokens < MIN_STABLE_TOKENS:
        caveats.append("target_too_short_for_stable_estimate")
    if stats["curvature_score"] is None:
        caveats.append("reference_variance_degenerate_curvature_unavailable")
    # No thresholds ship; the result is always uncalibrated. Surface that
    # explicitly so a consumer reading the pack knows the absence of a
    # band is intentional, not a bug.
    caveats.append("no_calibrated_thresholds_supplied")

    model_id = model.model_id if model is not None else None
    identifier_block = (
        model.identifier_block() if model is not None else None
    )

    out: dict[str, Any] = {
        "model_id": model_id,
        "identifier_block": identifier_block,
        "curvature_score": stats["curvature_score"],
        "n_samples": n_samples,
        "seed": seed,
        "n_tokens": n_tokens,
        "per_position": stats["per_position"],
        "score_version": SCORE_VERSION,
        "actual_log_prob_sum_nats": stats["actual_log_prob_sum_nats"],
        "reference_mean_sum_nats": stats["reference_mean_sum_nats"],
        "reference_variance_sum_nats2": stats["reference_variance_sum_nats2"],
        "caveats": caveats,
    }
    # T-Detect (spec 25; arXiv:2507.23577): opt-in Student-t tail-aware normalization. The
    # DELIVERABLE is the SCORE curvature_t = d / sqrt((nu/(nu-2)) * V) = 𝒟ₜ — the t-standardized
    # curvature statistic the paper and its reference implementation expose. It is a global constant
    # rescale of the Gaussian curvature_score (nu fixed), so its discrimination RANKING equals the
    # Gaussian z; T-Detect changes the reference scale, not the ranking. We deliberately emit NO
    # p-value: curvature_t is a rescaled (asymptotically Gaussian) z-score, NOT a t-distributed
    # variate, so a Student-t survival of it would be an unsupported transform — not a calibrated
    # probability. Added strictly inside the student-t branch so the gaussian envelope is unchanged.
    if tail not in ("gaussian", "student-t"):
        # #228 P2: a direct caller of audit() bypasses the CLI's choices=; an unknown tail must
        # fail loud, never be silently treated as gaussian.
        raise ValueError(
            f"unknown tail {tail!r} (choices: gaussian, student-t)")
    if tail == "student-t" and stats["curvature_score"] is not None:
        nu = t_df
        if nu <= 2:
            # Direct-caller guard: nu/(nu-2) is undefined (nu==2) or negative (nu<2). The CLI
            # validates this earlier and exits 2; this protects programmatic callers of audit().
            raise ValueError(
                f"t_df must be > 2 for --tail student-t (got {nu}); the Student-t variance "
                f"scale nu/(nu-2) is undefined at nu<=2."
            )
        d = stats["actual_log_prob_sum_nats"] - stats["reference_mean_sum_nats"]
        v = stats["reference_variance_sum_nats2"]
        out["tail"] = "student-t"
        out["t_df"] = nu
        out["curvature_t"] = d / math.sqrt((nu / (nu - 2)) * v)   # 𝒟ₜ — the T-Detect score
    return out


def compose_envelope(
    *,
    target_path: Path | None,
    target_words: int,
    results: dict[str, Any],
    include_per_position: bool = False,
    licenses_text: str = DEFAULT_LICENSES,
    does_not_license_text: str = DEFAULT_DOES_NOT_LICENSE,
) -> dict[str, Any]:
    caveats = list(results.get("caveats", []))

    # T-Detect: name curvature_t in the refusal block ONLY when it is emitted (student-t mode),
    # so the gaussian-mode claim license is unchanged.
    if "curvature_t" in results:
        does_not_license_text = f"{does_not_license_text} {STUDENT_T_CAVEAT}"

    license_block = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=licenses_text,
        does_not_license=does_not_license_text,
        comparison_set={
            "scoring_model": results.get("model_id"),
            "score_version": results.get("score_version"),
            "n_samples": results.get("n_samples"),
            "seed": results.get("seed"),
            "threshold": None,
        },
        additional_caveats=caveats,
        references=[
            "Bao et al. 2024, 'Fast-DetectGPT: Efficient Zero-Shot "
            "Detection of Machine-Generated Text via Conditional "
            "Probability Curvature', ICLR 2024 (arXiv:2310.05130)",
        ],
    )

    # ``per_position`` is optional in the envelope (spec marks it optional);
    # it can be large, so only emit it when explicitly requested.
    results_out = dict(results)
    if not include_per_position:
        results_out.pop("per_position", None)

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=None,
        results=results_out,
        claim_license=license_block,
        available=True,
        warnings=caveats,
    )


def render_markdown(envelope: dict[str, Any]) -> str:
    results = envelope["results"]
    target = envelope["target"]

    lines: list[str] = []
    lines.append("# Fast-DetectGPT Conditional-Curvature Audit")
    lines.append("")
    lines.append(
        f"- **Target:** `{target.get('path')}` ({target.get('words')} words)"
    )
    lines.append(f"- **Scoring model:** `{results.get('model_id')}`")
    lines.append(f"- **Score version:** `{results.get('score_version')}`")
    lines.append(f"- **Samples per position:** {results.get('n_samples')}")
    lines.append(f"- **Seed:** {results.get('seed')}")
    lines.append("")

    lines.append("## Curvature")
    lines.append("")
    score = results.get("curvature_score")
    score_text = f"{score:.4f}" if score is not None else "(unavailable)"
    lines.append(f"**Conditional-curvature z-score:** {score_text}")
    lines.append(f"**Tokens scored:** {results.get('n_tokens')}")
    lines.append(
        "**Verdict band:** none — ships uncalibrated, no threshold."
    )
    lines.append("")

    caveats = results.get("caveats") or []
    lines.append("## Caveats")
    lines.append("")
    if caveats:
        for c in caveats:
            lines.append(f"- {c}")
    else:
        lines.append("(none surfaced)")
    lines.append("")

    lines.append("## Claim license")
    lines.append("")
    lines.append(envelope["claim_license_rendered"].rstrip())
    lines.append("")

    lines.append("## Provenance")
    lines.append("")
    lines.append(f"- **Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}")
    if results.get("identifier_block") is not None:
        lines.append(
            f"- **Model identifier_block:** "
            f"`{json.dumps(results['identifier_block'])}`"
        )
    lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fast-DetectGPT conditional-curvature audit "
            "(Bao et al. 2024) — Surface-5 discrimination evidence."
        )
    )
    parser.add_argument("target", help="Path to target text file (UTF-8).")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Scoring model alias or HF ID (default {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--n-samples", type=int, default=DEFAULT_N_SAMPLES,
        help=(
            "Number of alternative tokens sampled per position to build "
            f"the reference distribution (default {DEFAULT_N_SAMPLES})."
        ),
    )
    parser.add_argument(
        "--surprisal-dtype",
        choices=("auto", "fp32", "fp16", "bf16"),
        default="auto",
        help="Precision for the model load (default auto).",
    )
    parser.add_argument(
        "--device", default=None,
        help=(
            "Explicit torch device for the forward pass (e.g. cuda, "
            "cuda:1, cpu). Default: auto-detect (cuda > mps > cpu)."
        ),
    )
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_SEED,
        help=(
            "Seed for the per-position sampling so the curvature score "
            f"is reproducible (default {DEFAULT_SEED})."
        ),
    )
    parser.add_argument(
        "--tail", choices=("gaussian", "student-t"), default="gaussian",
        help=(
            "Normalization of the curvature. 'gaussian' (default) is the Fast-DetectGPT "
            "z-score, unchanged. 'student-t' adds the T-Detect SCORE curvature_t "
            "(arXiv:2507.23577) — the t-standardized statistic the paper exposes; robust to "
            "adversarial/paraphrased text. No p-value is emitted (it would be unsupported)."
        ),
    )
    parser.add_argument(
        "--t-df", type=int, default=DEFAULT_T_DF,
        help=(
            "Student-t degrees of freedom for --tail student-t (default "
            f"{DEFAULT_T_DF}; must be > 2)."
        ),
    )
    parser.add_argument(
        "--per-position", action="store_true",
        help="Include the per-position curvature series in the output.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Print the JSON envelope to stdout instead of writing files.",
    )
    parser.add_argument(
        "--out", default=None,
        help=(
            "Evidence pack JSON path "
            "(default <target>.fast_detect_curvature.json)."
        ),
    )
    parser.add_argument(
        "--out-md", default=None,
        help=(
            "Evidence pack markdown path "
            "(default <target>.fast_detect_curvature.md)."
        ),
    )
    parser.add_argument("--licenses", default=DEFAULT_LICENSES)
    parser.add_argument("--does-not-license", default=DEFAULT_DOES_NOT_LICENSE)
    args = parser.parse_args(argv)

    if args.tail == "student-t" and args.t_df <= 2:
        print("error: --t-df must be > 2 (the Student-t variance nu/(nu-2) is "
              "undefined at 2 and negative below).", file=sys.stderr)
        return 2

    target_path = Path(args.target)
    if not target_path.exists():
        print(f"error: target file not found at {target_path}", file=sys.stderr)
        return 1

    try:
        target_text = target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        print(f"error: target not valid UTF-8: {exc}", file=sys.stderr)
        return 1

    target_words = count_words(target_text)

    # Fail cleanly when torch (the surprisal tier) is absent — a
    # dependency_check-style install hint, no traceback. We probe before
    # constructing the backend so the message names the missing tier
    # rather than surfacing a deep ImportError.
    try:
        import torch  # type: ignore  # noqa: F401
    except ImportError:
        print(
            "error: Fast-DetectGPT curvature needs the surprisal tier "
            "(transformers + torch), which is not installed.\n"
            "  Install with: pip install -r requirements-surprisal.txt\n"
            "  (opt-in Tier-4 / surprisal dependency layer; the file "
            "documents how to pick the right torch wheel for your "
            "accelerator — ROCm / CUDA / MPS / CPU-only). For the full "
            "decision tree and a smoke test see "
            "scripts/calibration/RUNBOOK_tier4_install.md.",
            file=sys.stderr,
        )
        return 2

    try:
        model = SurprisalBackend(
            model_id=resolve_model_arg(args.model),
            dtype=args.surprisal_dtype,
            device=args.device,
        )
    except SurprisalBackendError as exc:
        print(
            f"error: backend construction failed ({args.model}): {exc}",
            file=sys.stderr,
        )
        return 3

    try:
        results = audit(
            target_text,
            model=model,
            n_samples=args.n_samples,
            seed=args.seed,
            tail=args.tail,
            t_df=args.t_df,
        )
    except SurprisalBackendError as exc:
        print(f"error: scoring failed ({args.model}): {exc}", file=sys.stderr)
        return 3

    envelope = compose_envelope(
        target_path=target_path,
        target_words=target_words,
        results=results,
        include_per_position=args.per_position,
        licenses_text=args.licenses,
        does_not_license_text=args.does_not_license,
    )

    if args.json:
        print(json.dumps(envelope, indent=2, default=str))
        return 0

    markdown = render_markdown(envelope)
    out_json = (
        Path(args.out)
        if args.out
        else target_path.with_suffix(
            target_path.suffix + ".fast_detect_curvature.json"
        )
    )
    out_md = (
        Path(args.out_md)
        if args.out_md
        else target_path.with_suffix(
            target_path.suffix + ".fast_detect_curvature.md"
        )
    )
    out_json.write_text(
        json.dumps(envelope, indent=2, default=str), encoding="utf-8",
    )
    out_md.write_text(markdown, encoding="utf-8")
    print(f"Wrote {out_json} + {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
