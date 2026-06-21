#!/usr/bin/env python3
"""diveye_signals.py — DivEye surprisal-DIVERSITY signal aggregation (M1).

Clean-room reimplementation of the four temporal / distribution-shape signals
from DivEye (arXiv:2509.18880, Basani & Chen) that the SETEC arsenal does not
already ship, plus an aggregator that assembles the full nine-signal DivEye
vector from a per-token surprisal series.

  * F5  delta series        — 1st-order finite differences of surprisal.
  * F6  acceleration series — 2nd-order finite differences (delta of delta).
  * F7  histogram entropy   — Shannon entropy (bits) of the surprisal histogram.
  * F8  accel ACF (lag-1)   — autocorrelation of the acceleration series.

F1–F4 (mean / variance / skew / excess-kurtosis) and F9 (surprisal ACF lag-1)
are already shipped in ``surprisal_audit.py``; ``aggregate_diveye_signals``
**reuses** those tested helpers via a lazy import (with a self-contained stdlib
fallback) rather than duplicating the math.

POSTURE — this is descriptive surprisal-diversity evidence, NOT an authorship
verdict. The module is a pure-math helper:

  * ``TASK_SURFACE = None`` — it is not a registered detection surface. It emits
    no ``verdict`` / ``is_ai`` / ``is_human`` / ``band`` / ``calibration_status``
    key, and imports nothing from the fitness / calibration / discrimination /
    validation / loop modules. The eventual ``diveye_audit.py`` registered
    surface (M2, experiment-gated) owns the claim-license framing, the band, and
    the ``build_output`` wiring; this module owns only the signal math.

STDLIB-ONLY — no torch / transformers / scipy / sklearn / xgboost import, at
module level or inside any function. The math runs over a plain ``list[float]``
surprisal series, so the M1 unit tests are model-free and CI-runnable: in M1 the
series is INJECTED; in M2 it is the output of ``SurprisalBackend.score_text``
(the scalar path — no full-vocab distribution, no second forward pass).

DIRECTION / SIGN — silent inversion is the surprisal-detector family's shared
failure mode, so the hypothesised AI-vs-human directions are pinned in the test
suite (``test_aggregate_diveye_signals_direction``). Hypotheses, not verdicts:
AI-like prose tends LOWER surprisal variance / entropy / delta-SD (locally
smooth); human prose tends higher (locally bursty). A restricted-register or
ESL human passage can match the AI-like profile for non-AI reasons — that
failure mode is surfaced in the M2 claim-license, not adjudicated here.

OUTPUT NAMING (output_schema range-check trap, REVIEW C1) — ``output_schema``'s
``_SURPRISAL_RE`` range-checks any key carrying a whole ``surprisal`` /
``perplexity`` / ``entropy`` / ``nll`` token as ``>= 0`` unless a transform/
standardisation guard also matches. Skew, excess kurtosis, and lag-1 ACF are
legitimately SIGNED, so this module emits them under un-prefixed keys
(``skew``, ``excess_kurtosis``, ``acf1``, ``accel_acf1``) that do NOT trip the
gate, mirroring ``surprisal_audit.py``'s ``results.summary`` nesting. The whole
returned vector passes ``validate_results_bounds`` for any finite input,
including negative skew / kurtosis / ACF (pinned by
``test_aggregate_output_passes_output_schema_bounds``).

NON-FINITE DEGRADATION (R4) — for a *non*-finite input (inf / NaN) every signal
degrades gracefully rather than crashing: the moment helpers propagate the value
as a float that ``validate_results_bounds`` rejects cleanly as "not finite", and
``surprisal_histogram_entropy`` returns ``None`` (it cannot bin a non-finite
value — see its guard). So ``aggregate_diveye_signals`` never raises a raw
``ValueError``; the dispatcher reaches its clean ``internal_error`` path. Pinned
by ``test_aggregate_nonfinite_input_does_not_raise``.
"""

from __future__ import annotations

import math
from typing import Sequence

# Not a registered detection surface — a pure math helper. See module docstring.
TASK_SURFACE = None

# Mirror surprisal_audit.MIN_SERIES_FOR_ACF so the surprisal/accel ACF lags use
# the same minimum-length convention across the surprisal family (REVIEW §1.2).
DEFAULT_MIN_ACF_LENGTH = 30

# Following the DivEye paper's histogram approach for F7.
DEFAULT_ENTROPY_BINS = 50


# --------------- New DivEye signals (F5–F8) -------------------------


def delta_series(surprisal: Sequence[float]) -> list[float]:
    """1st-order finite differences: ``d[t] = s[t] - s[t-1]``, t=1..N-1.

    Returns a list of length ``N-1`` (``[]`` for ``N < 2``). DivEye feature F5.
    """
    n = len(surprisal)
    if n < 2:
        return []
    return [float(surprisal[t] - surprisal[t - 1]) for t in range(1, n)]


def accel_series(delta: Sequence[float]) -> list[float]:
    """2nd-order finite differences: ``a[t] = d[t] - d[t-1]``, on the delta
    series (the output of ``delta_series``).

    For a surprisal series of length ``N`` the delta has length ``N-1`` and the
    acceleration has length ``N-2``. Returns ``[]`` when the delta series has
    fewer than 2 elements (i.e. fewer than 3 original tokens). DivEye feature F6.
    """
    n = len(delta)
    if n < 2:
        return []
    return [float(delta[t] - delta[t - 1]) for t in range(1, n)]


def surprisal_histogram_entropy(
    surprisal: Sequence[float],
    n_bins: int = DEFAULT_ENTROPY_BINS,
) -> float | None:
    """Shannon entropy (base 2, bits) of the empirical surprisal histogram.

    Bins the surprisal values into ``n_bins`` equal-width bins over
    ``[min(surprisal), max(surprisal)]`` and returns the entropy of the
    resulting count distribution. DivEye feature F7.

    Returns ``None`` for:
      * a series shorter than 2 values, or
      * a constant series (zero range -> degenerate single-bin histogram), or
      * a series containing any non-finite value (inf / NaN).

    Convention: empty bins contribute 0 to the sum (``0 * log2(0) == 0``).
    Entropy is always ``>= 0`` (so the ``surprisal_entropy`` key is range-safe
    under ``output_schema``'s ``>= 0`` surprisal/entropy check).

    NON-FINITE GUARD (R4 graceful degradation) — an ``inf`` in the series makes
    ``width = hi - lo == inf``, so ``(x - lo) / width == inf / inf == NaN`` and
    ``int(NaN)`` would raise an opaque ``ValueError`` *before* ``build_output``'s
    bounds gate can run. The rest of the DivEye vector (mean / var / skew / ACF)
    propagates a non-finite input as a *float* that ``validate_results_bounds``
    rejects cleanly as "not finite", routing the dispatcher onto its
    ``internal_error`` envelope; this function degrades the same way by returning
    ``None`` here instead of crashing. (Real-backend ``inf`` is low-probability —
    fp32 ``log_softmax`` keeps ``p > 0`` for a sampled token — so this is a
    robustness/contract guard, not a live path on normal corpora.)
    """
    n = len(surprisal)
    if n < 2 or n_bins < 1:
        return None
    if any(not math.isfinite(x) for x in surprisal):
        # Non-finite input (inf / NaN): degrade gracefully rather than letting
        # int((x - lo) / width * n_bins) raise on the inf/inf == NaN trap.
        return None
    lo = min(surprisal)
    hi = max(surprisal)
    width = hi - lo
    if width <= 0.0:
        # Constant series: zero range, degenerate histogram.
        return None
    counts = [0] * n_bins
    for x in surprisal:
        # Map x in [lo, hi] -> bin in [0, n_bins-1]; hi lands in the last bin.
        idx = int((x - lo) / width * n_bins)
        if idx >= n_bins:
            idx = n_bins - 1
        elif idx < 0:
            idx = 0
        counts[idx] += 1
    total = float(sum(counts))
    entropy = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            entropy -= p * math.log2(p)
    # Clamp a -0.0 from floating-point round-off up to 0.0 so the value is a
    # clean non-negative entropy under the output_schema >= 0 gate.
    return float(entropy) if entropy > 0.0 else 0.0


# --------------- Self-contained stdlib fallbacks ---------------------
#
# aggregate_diveye_signals reuses surprisal_audit's tested moment helpers via a
# lazy import. These fallbacks are used only when surprisal_audit is not on the
# path (e.g. the module is vendored standalone). They are byte-for-byte
# equivalent in behaviour to surprisal_audit's helpers for the cases the
# aggregator exercises.


def _fallback_mean(xs: Sequence[float]) -> float:
    if not xs:
        return 0.0
    return float(sum(xs) / len(xs))


def _fallback_sample_variance(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _fallback_mean(xs)
    return float(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _fallback_sample_sd(xs: Sequence[float]) -> float:
    return math.sqrt(_fallback_sample_variance(xs))


def _fallback_skew(xs: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    m = _fallback_mean(xs)
    sd = _fallback_sample_sd(xs)
    if sd == 0.0:
        return None
    return float(sum(((x - m) / sd) ** 3 for x in xs) / n)


def _fallback_excess_kurtosis(xs: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 4:
        return None
    m = _fallback_mean(xs)
    sd = _fallback_sample_sd(xs)
    if sd == 0.0:
        return None
    fourth = sum(((x - m) / sd) ** 4 for x in xs) / n
    return float(fourth - 3.0)


def _fallback_acf_at_lag(
    xs: Sequence[float], lag: int, *, min_length: int,
) -> float | None:
    n = len(xs)
    if n < min_length or lag <= 0 or lag >= n:
        return None
    m = _fallback_mean(xs)
    denom = sum((x - m) ** 2 for x in xs)
    if denom == 0.0:
        return None
    numer = sum((xs[i] - m) * (xs[i + lag] - m) for i in range(n - lag))
    return float(numer / denom)


def _load_helpers(min_acf_length: int):
    """Return (mean, sd, skew, excess_kurtosis, acf_at_lag) — reusing the
    tested ``surprisal_audit`` implementations via a LAZY import (inside this
    function, not at module top, so importing ``diveye_signals`` never triggers
    surprisal_audit's import graph). Falls back to the stdlib equivalents above
    when surprisal_audit is not importable.

    ``surprisal_audit._acf_at_lag`` hard-codes its own ``MIN_SERIES_FOR_ACF``;
    when ``min_acf_length`` differs from that constant we route ACF through the
    fallback so the caller's threshold is honoured exactly.
    """
    try:
        import surprisal_audit as sa  # noqa: PLC0415 (lazy by design)
    except Exception:  # pragma: no cover - exercised only without the dep
        return (
            _fallback_mean,
            _fallback_sample_sd,
            _fallback_skew,
            _fallback_excess_kurtosis,
            lambda xs, lag: _fallback_acf_at_lag(
                xs, lag, min_length=min_acf_length
            ),
        )

    if int(getattr(sa, "MIN_SERIES_FOR_ACF", min_acf_length)) == int(
        min_acf_length
    ):
        acf = sa._acf_at_lag
    else:
        def acf(xs: Sequence[float], lag: int) -> float | None:
            return _fallback_acf_at_lag(xs, lag, min_length=min_acf_length)

    return (sa._mean, sa._sample_sd, sa._skew, sa._excess_kurtosis, acf)


# --------------- The 9-signal DivEye aggregation ---------------------


def aggregate_diveye_signals(
    surprisal: Sequence[float],
    *,
    n_entropy_bins: int = DEFAULT_ENTROPY_BINS,
    min_acf_length: int = DEFAULT_MIN_ACF_LENGTH,
) -> dict[str, float | None]:
    """Assemble the DivEye signal vector from a per-token surprisal series.

    Reuses ``surprisal_audit``'s moment helpers (lazy import; stdlib fallback)
    for F1–F4/F9 and the new helpers above for F5–F8. Operates on an injected
    ``list[float]`` in M1; on ``SurprisalBackend.score_text`` output in M2.

    Returns an 11-key dict (the 9 DivEye signals; the delta/accel pairs each
    expose both mean and SD because the means telescope toward zero and the SDs
    are the discriminative members):

      ``surprisal_mean``    F1 — mean surprisal (bits/token, >= 0)
      ``surprisal_var``     F2 — sample variance (bits^2, >= 0)
      ``skew``              F3 — sample skewness (SIGNED; None for n<3 / constant)
      ``excess_kurtosis``   F4 — excess kurtosis (SIGNED; None for n<4 / constant)
      ``delta_mean``        F5 — mean of the delta series (~0 by telescoping)
      ``delta_sd``          F5b — SD of the delta series (DISCRIMINATIVE; >= 0)
      ``accel_mean``        F6 — mean of the acceleration series (~0)
      ``accel_sd``          F6b — SD of the acceleration series (DISCRIMINATIVE; >= 0)
      ``surprisal_entropy`` F7 — histogram entropy (bits, >= 0; KEY DivEye signal)
      ``accel_acf1``        F8 — lag-1 ACF of acceleration (SIGNED; None if short)
      ``acf1``              F9 — lag-1 ACF of surprisal (SIGNED; None if short)

    Key names are chosen so the entire dict passes
    ``output_schema.validate_results_bounds`` for any finite input — the SIGNED
    moments use un-prefixed keys that do not trip the surprisal/entropy ``>= 0``
    check (REVIEW C1). The discriminative features are ``delta_sd``, ``accel_sd``,
    ``accel_acf1``, and ``surprisal_entropy`` (the means telescope to ~0 and are
    not standalone discriminators).
    """
    series = [float(x) for x in surprisal]
    mean, sd, skew, excess_kurtosis, acf_at_lag = _load_helpers(min_acf_length)

    deltas = delta_series(series)
    accels = accel_series(deltas)

    # _sample_sd returns 0.0 (not None) for n<2, which is the correct, bounded
    # value for a degenerate difference series.
    return {
        "surprisal_mean": mean(series) if series else None,
        "surprisal_var": float(sd(series) ** 2) if len(series) >= 2 else 0.0,
        "skew": skew(series),
        "excess_kurtosis": excess_kurtosis(series),
        "delta_mean": mean(deltas) if deltas else 0.0,
        "delta_sd": sd(deltas),
        "accel_mean": mean(accels) if accels else 0.0,
        "accel_sd": sd(accels),
        "surprisal_entropy": surprisal_histogram_entropy(series, n_entropy_bins),
        "accel_acf1": acf_at_lag(accels, 1),
        "acf1": acf_at_lag(series, 1),
    }
