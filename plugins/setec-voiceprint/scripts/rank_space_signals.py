#!/usr/bin/env python3
"""rank_space_signals.py — rank-space surprisal helpers (DetectLLM LRR, spec 32, M1).

Pure-Python, **stdlib-only** signal-computation helpers that derive the
*per-token log-rank* series — and the DetectLLM **LRR** (log-likelihood /
log-rank ratio) statistic — from the log-probability distributions a causal LM
*already* materializes in
``SurprisalBackend.score_text_with_distributions``. There is **no second forward
pass** and **no model load**: log-rank at a position is just an ``argsort`` of
that position's vocab log-prob vector, so LRR is a near-free DERIVED column off
the same distributions ``binoculars_audit.py`` v2 computes. This DEEPENS the
existing Tier-4 surprisal family (``surprisal_audit`` mean/sd/acf_lag1); it is
not a new surface and not a new model.

Paper: Su, Zhuo, Wang, Nakov, "DetectLLM: Leveraging Log Rank Information for
Zero-Shot Detection of Machine-Generated Text" (arXiv:2306.05540, MBZUAI 2023).
The paper's specific AUC lifts (+1.75 / +3.9 on WritingPrompts, Table 3) are a
LEAD, not a target, and are **not asserted** anywhere in this module — they
require an empirical reproduction (M2) before any reliance. The signal DIRECTION
is the empirical question; this module only computes the values.

SIGN / DIRECTION (load-bearing — the family's shared silent failure mode)
=========================================================================
Rank is taken over the vocab log-prob vector sorted **DESCENDING**:
``rank 0 = the highest-log-prob (most probable) token``. ``numpy.argsort``
defaults to ASCENDING, which would invert the rank order and silently flip every
downstream signal; this module sorts descending explicitly (pure-Python
``sorted(..., reverse=...)`` over (log_prob, vocab_id) — no numpy), and the
``test_rank_series_fixture`` test pins that the most-probable token gets
``log_rank = log(0 + 1) = 0.0``, NOT a large value. A sign/direction inversion
here is the shared failure mode of the whole surprisal/rank family, so it is
pinned in a test rather than left to prose.

THE rank-0 → inf CONVENTION (pinned, stable across runs)
========================================================
``log_rank_t = log(rank_t + 1)`` — the DetectLLM add-1 convention (per
2306.05540), so the most-probable token (rank 0) gives ``log(1) = 0.0``, a
*finite* value (good for the log-rank series). But ``lrr_t = surprisal_nats_t /
log_rank_t`` then divides by 0 at rank 0, which is mathematically undefined. The
fixed convention: **``lrr_t`` is emitted as ``math.inf`` at rank-0 positions, and
those positions are EXCLUDED from the ``lrr`` mean** (``aggregate_rank_signals``
filters non-finite ``lrr_series`` entries before reducing). The count excluded is
returned as ``lrr_excluded_positions`` so a caller never silently averages over a
different denominator than it thinks. A raw ``inf`` must never reach the output
envelope's ``results`` (the R4 finiteness gate rejects it), so the *aggregate*
scalars are always finite; the per-token ``lrr_series`` (which may carry ``inf``)
is a helper return value, not an envelope field.

This module imports NOTHING from torch / transformers / numpy / scipy, and
nothing from the fitness / calibration / binoculars / validation / loop surfaces.
It is a signal-computation helper, not a fitness or selection surface, and
exposes no ``verdict`` / ``calibration_status`` / ``band`` key — all posture
framing lives in the calling surface (``rank_space_audit.py``).
"""

from __future__ import annotations

import math
from typing import Sequence

# bits → nats: surprisal_bits / log2(e) = surprisal_nats. log2(e) = 1/ln(2).
_LOG2E = 1.0 / math.log(2.0)

# Minimum series length for a meaningful lag-1 ACF on the rank series. The
# surprisal_audit ACF uses a 30-token floor (MIN_SERIES_FOR_ACF) tuned for raw
# surprisal autocorrelation; the rank-series analogue here uses the structural
# minimum (3) so the helper matches its documented contract ("None if < 3
# tokens") and so a short injected fixture still exercises the formula. The
# calling surface carries the register/length caveat; this helper just refuses a
# vacuous estimate below the structural floor.
_MIN_SERIES_FOR_ACF = 3


def _mean(xs: Sequence[float]) -> float:
    if not xs:
        return 0.0
    return float(sum(xs) / len(xs))


def _pstdev(xs: Sequence[float]) -> float | None:
    """Population SD. ``None`` for fewer than 2 points (consistent with
    ``surprisal_sd`` returning ``None`` for a single token)."""
    n = len(xs)
    if n < 2:
        return None
    m = _mean(xs)
    return float(math.sqrt(sum((x - m) ** 2 for x in xs) / n))


def _acf_lag1(xs: Sequence[float]) -> float | None:
    """Biased lag-1 autocorrelation: ``sum((x_i - m)(x_{i+1} - m)) /
    sum((x_i - m)^2)`` — the same estimator as ``surprisal_audit._acf_at_lag``
    at lag 1 (Pearson on the lag-paired series). ``None`` for a series below the
    structural floor or a constant series (zero denominator)."""
    n = len(xs)
    if n < _MIN_SERIES_FOR_ACF:
        return None
    m = _mean(xs)
    denom = sum((x - m) ** 2 for x in xs)
    if denom == 0.0:
        return None
    numer = sum((xs[i] - m) * (xs[i + 1] - m) for i in range(n - 1))
    return float(numer / denom)


def _rank_of_token(log_prob_vector: Sequence[float], token_id: int) -> int:
    """0-indexed rank of ``token_id`` in ``log_prob_vector`` sorted DESCENDING
    by log-prob (rank 0 = most probable). Ties broken by ascending vocab id so
    the rank is deterministic across runs (a saturated/tie distribution must not
    produce a run-dependent rank). Pure-Python; no numpy.

    Raises ``IndexError`` if ``token_id`` is out of range for the vector — a
    tokenization mismatch the caller should surface, not silently rank as 0.
    """
    if not (0 <= token_id < len(log_prob_vector)):
        raise IndexError(
            f"token_id {token_id} out of range for vocab of size "
            f"{len(log_prob_vector)}"
        )
    target_lp = log_prob_vector[token_id]
    # Rank = how many tokens are STRICTLY more probable, plus how many equally
    # probable tokens sort ahead under the (lp desc, id asc) tie-break. This
    # avoids materializing a full argsort: O(V) instead of O(V log V), and the
    # tie-break is explicit so a saturated distribution is deterministic.
    rank = 0
    for vid, lp in enumerate(log_prob_vector):
        if lp > target_lp:
            rank += 1
        elif lp == target_lp and vid < token_id:
            rank += 1
    return rank


def rank_series_from_distributions(
    log_probs_nats: list[list[float]],
    token_ids: list[int],
    surprisal_bits: list[float],
) -> dict[str, list[float]]:
    """Compute the per-token log-rank and LRR series from a PRE-COMPUTED
    distribution. **No model is called here** — this is the M1 core, run over
    the Python-list output of ``SurprisalBackend.score_text_with_distributions``
    (or an injected stub of the same shape).

    Parameters (matching ``score_text_with_distributions``'s return tuple):

    - ``log_probs_nats``: list of vocab-sized log-prob vectors (nats),
      length ``N - 1``. ``log_probs_nats[t]`` is the model's distribution over
      the vocab at position ``t``; the actual next token is ``token_ids[t + 1]``.
    - ``token_ids``: full tokenized sequence, length ``N``.
    - ``surprisal_bits``: per-token surprisal series in bits, length ``N - 1``.

    Returns a dict with two equal-length (``N - 1``) series::

        {
          "log_rank_series": [log(rank_t + 1), ...],   # finite; rank 0 -> 0.0
          "lrr_series":      [surprisal_nats_t / log_rank_t, ...],  # inf at rank 0
        }

    Sort direction is DESCENDING (rank 0 = highest log-prob). ``lrr_series[t]``
    is ``math.inf`` exactly when ``rank_t == 0`` (``log(1) == 0`` in the
    denominator); ``aggregate_rank_signals`` excludes those positions from the
    ``lrr`` mean. See the module docstring for the full convention.

    Raises ``ValueError`` if the input lengths are inconsistent (a tokenization
    or wiring bug the caller must see, not paper over).
    """
    n_positions = len(log_probs_nats)
    if len(surprisal_bits) != n_positions:
        raise ValueError(
            f"surprisal_bits length {len(surprisal_bits)} != log_probs_nats "
            f"length {n_positions}"
        )
    if len(token_ids) != n_positions + 1:
        raise ValueError(
            f"token_ids length {len(token_ids)} != log_probs_nats length + 1 "
            f"({n_positions + 1}) — the distribution at position t predicts "
            f"token_ids[t + 1], so there is one more token than position"
        )

    log_rank_series: list[float] = []
    lrr_series: list[float] = []
    for t in range(n_positions):
        actual_token = token_ids[t + 1]
        rank_t = _rank_of_token(log_probs_nats[t], actual_token)
        log_rank_t = math.log(rank_t + 1)  # add-1 convention; rank 0 -> 0.0
        log_rank_series.append(log_rank_t)
        surprisal_nats_t = surprisal_bits[t] / _LOG2E
        if log_rank_t == 0.0:
            # rank 0 (most-probable token): surprisal_nats / log(1) is
            # undefined. Emit inf per the pinned convention; the aggregate
            # excludes it from the LRR mean. A surprisal of exactly 0 at a
            # rank-0 position (0/0) is also emitted as inf for a stable,
            # documented sentinel rather than a NaN that the R4 gate rejects.
            lrr_series.append(math.inf)
        else:
            lrr_series.append(surprisal_nats_t / log_rank_t)

    return {"log_rank_series": log_rank_series, "lrr_series": lrr_series}


def aggregate_rank_signals(
    log_rank_series: list[float],
    lrr_series: list[float],
    surprisal_bits: list[float],
) -> dict[str, float | int | None]:
    """Aggregate the per-token rank series into scalar detection signals.

    Returns (all finite or ``None`` — never ``inf`` / ``NaN``, so the scalars
    are safe to place under the R4 output-validity gate)::

        {
          "log_rank_mean":          mean of log_rank_series,
          "log_rank_sd":            population SD (None if < 2 positions),
          "log_rank_acf1":          lag-1 ACF (None if < 3 positions / constant),
          "lrr":                    mean of the FINITE lrr_series entries
                                    (the DetectLLM LRR statistic),
          "lrr_excluded_positions": count of non-finite (rank-0) lrr positions
                                    excluded from the lrr mean,
          "n_positions":            len(log_rank_series),
        }

    The ``lrr`` mean is taken over FINITE ``lrr_series`` entries only: rank-0
    positions contribute ``inf`` (see the module docstring) and are excluded.
    When every position is rank 0 (a degenerate all-most-probable sequence) the
    finite set is empty and ``lrr`` is ``None`` — a refusal, not a fabricated 0.
    ``surprisal_bits`` is accepted for signature parity with the upstream tuple
    (and so a future moment can be added) but is not currently reduced here.
    """
    n_positions = len(log_rank_series)
    finite_lrr = [x for x in lrr_series if math.isfinite(x)]
    excluded = len(lrr_series) - len(finite_lrr)
    return {
        "log_rank_mean": _mean(log_rank_series) if log_rank_series else None,
        "log_rank_sd": _pstdev(log_rank_series),
        "log_rank_acf1": _acf_lag1(log_rank_series),
        "lrr": _mean(finite_lrr) if finite_lrr else None,
        "lrr_excluded_positions": excluded,
        "n_positions": n_positions,
    }


def npr_rank_score(
    text: str,
    backend: object,
    *,
    T: int = 25,
    seed: int = 0,
    mask_probability: float = 0.15,
) -> dict[str, float | None]:
    """NPR (normalized perturbed log-rank) entrypoint — **NOT built in M1**.

    NPR (DetectLLM, arXiv:2306.05540) adds a T5-class mask-fill perturbation loop
    on top of the rank scoring: generate ``T`` masked variants of ``text``,
    re-score each with ``backend.score_text_with_distributions``, and report the
    normalized gap between the original log-rank and the perturbed-variant
    log-ranks. That is the Tier-2 / M2 (GPU-gated) path — it requires a new
    perturbation-model dependency and a second-pass scoring cost, both out of
    scope for this stdlib-only M1.

    This stub exists so the public surface of the rank-space family is named and
    the gating is explicit; calling it raises so the GPU/M2 boundary fails loud
    rather than silently returning a stub number.
    """
    raise NotImplementedError(
        "npr_rank_score is the GPU-gated M2 (Tier-2) NPR perturbation path and "
        "is not built in this stdlib-only M1. Build it behind the model seam "
        "only after the LRR empirical run (M2 Tier-1) shows the rank axis has "
        "signal (spec 32, §3.4)."
    )
