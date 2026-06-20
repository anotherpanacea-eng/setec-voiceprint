#!/usr/bin/env python3
"""specdetect_audit.py — SpecDetect spectral + Lastde diversity-entropy reads.

Two zero-shot, model-free reads of the **sequential / spectral structure** of
the per-token surprisal vector SETEC already pays a forward pass for (the
series ``surprisal_backend.SurprisalBackend.score_text`` returns, in bits).
Where ``surprisal_audit`` summarizes that vector with point moments (mean / SD
/ lag-k ACF / skew / kurtosis) and ``fast_detect_curvature`` reads the
conditional-probability surface, this surface reads the *shape over time* of
how predictability rises and falls — through a DFT magnitude spectrum and a
time-series diversity-entropy.

Implements spec ``specs/30-specdetect-lastde.md`` (M1: the stdlib core, the
SpecDetect surface, the Lastde library functions behind the M2 orthogonality
gate, and the no-verdict / orthogonality / stdlib-import posture guards).

Two reads
---------
* **SpecDetect** — Yang et al., *SpecDetect: Spectral Analysis for Robust and
  Efficient Zero-Shot Detection of LLM-Generated Text* (arXiv:2508.11343). A
  real-input DFT of the (mean-removed) log-probability sequence; machine text
  concentrates spectral energy differently from human text. Read off as
  spectral descriptors: centroid, low-frequency energy fraction, flatness,
  peak bin (+ its normalized magnitude), and the human-readable
  ``dominant_period_tokens``.
* **Lastde / Lastde++** — Xu et al., *Training-free LLM-generated Text
  Detection by Mining Token Probability Sequences* (arXiv:2410.06072). A
  time-series diversity-entropy: embed the token-probability sequence into
  ordered sub-windows, count distinct local ordinal patterns, measure their
  normalized Shannon entropy; ``lastde_multiscale`` aggregates over a pinned
  scale set (the Lastde++ multi-scale aggregate). **M1 ships this as a tested
  library function only — it is NOT wired into the surface output**; whether it
  earns a place beside SpecDetect is decided by the M2 in-tree orthogonality
  gate against the DivEye moments + curvature (the spec's load-bearing
  question). Cut-or-ship is M2's; the math + tests stand either way.

Posture (mirrors ``fast_detect_curvature``)
-------------------------------------------
Every output is a **value + a PROVISIONAL band** with ``provisional: True`` and
``calibration_anchor: user-baseline-required``. There is **no**
``is_ai`` / ``ai_probability`` / ``verdict`` / composite ``score`` field. The
band *values* name the measured spectral property
(``flat-spectrum`` / ``concentrated-spectrum`` / ``indeterminate``) — never a
machine/AI/human class (the ``surprisal_audit`` ``smoothed``/``typical``
discipline; a band that named the inference target would be one rename from a
verdict). The surface ships **no** calibrated threshold; absence of a strong
band on a real run is intentional, surfaced as a caveat.

Orthogonality
-------------
SpecDetect is orthogonal to the DivEye moments by construction: a single lag-k
autocorrelation is one projection of the second-order structure, while the DFT
magnitude spectrum is that structure whole (Wiener–Khinchin). The spectral code
reads only the raw sequence — it references no ``mean_surprisal``,
``sd_surprisal``, ``autocorrelation``/``lag_`` field, ``curvature``, or
Binoculars ratio. ``test_orthogonal_statistic`` pins this with a comment- and
string-literal-stripped source scan, so this docstring may *name* the forbidden
symbols as posture documentation without tripping the guard.

Stdlib import
-------------
``import``ing this module pulls no torch and no numpy. The stdlib ``cmath``
real-DFT is the path CI exercises; ``numpy.rfft`` is an *optional* fast path
chosen at call time when numpy is already importable (a fixture asserts the two
agree to tolerance). The real ``SurprisalBackend`` is touched only in the M2
CLI path, lazily — the M1 ``audit`` entrypoint takes an injected
``score_fn`` / ``series`` and never loads a model.

CLI
---
    python3 plugins/setec-voiceprint/scripts/specdetect_audit.py TARGET \\
        [--model ALIAS] [--low-freq-cutoff F] \\
        [--surprisal-dtype auto|fp32|fp16|bf16] [--device DEVICE] \\
        [--per-bin] [--json] [--out PATH] [--out-md PATH]
"""

from __future__ import annotations

import argparse
import cmath
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore

SCRIPT_VERSION = "0.1.0"
# NEW sibling Surface-5 value, per spec §extend-vs-new. NOT folded under a
# shared `discrimination_evidence` surface — that refactor (which would touch
# binoculars_audit's + curvature's tags) is a maintainer decision per the
# spec's Open Question. The source-of-truth label lives in
# claim_license_surfaces/discrimination_spectral.txt (the drop-in fragment);
# build_output validates this against VALID_TASK_SURFACES.
TASK_SURFACE = "discrimination_spectral"
TOOL_NAME = "specdetect_audit"
SCORE_VERSION = "specdetect_dft_descriptors_v1"

# Spectral-stability floors (mirror surprisal_audit.MIN_SERIES_FOR_ACF = 30 and
# fast_detect_curvature.MIN_STABLE_TOKENS = 50). Below MIN_SERIES_FOR_SPECTRUM
# the descriptors are still reported but flagged as unstable; at or below
# DEGENERATE the spectrum is meaningless and descriptors are None.
MIN_SERIES_FOR_SPECTRUM = 30
# Cap the hand-rolled O(n^2) DFT so a pathological input can't hang CI; longer
# series are truncated (head) and a `truncated` caveat is surfaced.
MAX_SERIES_FOR_DFT = 4096

# The SpecDetect low-frequency discriminating region: fraction of the normalized
# frequency axis (0..0.5 cycles/token for a real DFT) treated as "low". Pinned,
# fixture-derived; the operator recalibrates. NOT a verdict threshold.
DEFAULT_LOW_FREQ_CUTOFF = 0.1

# Lastde ordinal-pattern parameters (the *gated* family; library-only at M1).
LASTDE_ORDER = 3          # window length -> order! = 6 distinct ordinal patterns
LASTDE_SCALES = (1, 2, 3)  # the pinned Lastde++ multi-scale set


# ----------------------------------------------------------------------------
# PROVISIONAL band thresholds — fixture-derived, illustrative, NOT calibrated.
# Named after the measured SPECTRUM PROPERTY, never the inference target: a
# flat (white) spectrum vs. a spectrum whose energy concentrates at low
# frequency. The band VALUES are flat-spectrum / concentrated-spectrum /
# indeterminate (the surprisal_audit smoothed/typical/indeterminate discipline)
# — they never read as machine/AI/human. See `_provisional_band`.
# ----------------------------------------------------------------------------
PROVISIONAL_BAND_THRESHOLDS: dict[str, dict[str, float]] = {
    # A spectrum is "concentrated" when energy piles at low frequency AND the
    # spectrum is peaky (low flatness). "Flat" is the white-noise inverse.
    "low_freq_energy_frac": {"concentrated_above": 0.55, "flat_below": 0.30},
    "spectral_flatness": {"concentrated_below": 0.40, "flat_above": 0.70},
}


DEFAULT_LICENSES = (
    "Reports spectral (DFT) descriptors of the per-token log-probability "
    "sequence of the target under a single scoring causal LM (SpecDetect, "
    "Yang et al., arXiv:2508.11343): the spectral centroid, low-frequency "
    "energy fraction, spectral flatness, peak frequency bin (+ its normalized "
    "magnitude), and the dominant period in tokens. These are a measurement of "
    "the sequential/spectral structure of the predictability signal — the "
    "shape over time of how surprise rises and falls. The descriptors are a "
    "numeric measurement against the chosen model M; they are not a verdict. "
    "The read is orthogonal to Binoculars' cross-perplexity ratio, "
    "Fast-DetectGPT curvature, and the DivEye surprisal moments "
    "(mean/SD/autocorrelation): a single lag-k autocorrelation is one "
    "projection of the second-order structure, whereas the magnitude spectrum "
    "is that structure whole (Wiener–Khinchin). Lastde / Lastde++ "
    "time-series diversity-entropy (Xu et al., arXiv:2410.06072) is computed "
    "as a library function but is NOT emitted by this surface at M1 — it ships "
    "only if the in-tree orthogonality gate against the DivEye moments passes "
    "(M2)."
)

DEFAULT_DOES_NOT_LICENSE = (
    "Does not license a binary AI/human authorship verdict. Ships WITHOUT a "
    "framework-calibrated threshold; the bands are PROVISIONAL and "
    "illustrative-only (calibration_anchor: user-baseline-required), and the "
    "band values name a spectrum property (flat-spectrum / "
    "concentrated-spectrum / indeterminate), never a machine/AI/human class. "
    "Formulaic human prose (legal boilerplate, liturgy, technical specs), "
    "translated text, and ESL prose all produce regular predictability "
    "signals — a concentrated spectrum is not an AI finding, and a flat one is "
    "not a human finding (absence is not evidence; this surface emits no "
    "'human' label any more than an 'AI' one). The spectrum is "
    "tokenizer- and model-bound: it is computed over one model's log-probs "
    "under one tokenizer, and a comparison across runs requires the same model "
    "+ revision (fp16/fp32 and checkpoint changes move absolute surprisals and "
    "therefore move these reads). One orthogonal axis among several; operator "
    "judgment is the load-bearing decision step, applied downstream via the "
    "existing calibration pipeline. Does not substitute for Binoculars, "
    "Fast-DetectGPT, DivEye, stylometric, or embedding audits — it complements "
    "them."
)


_WORD_RE = re.compile(r"[A-Za-z']+")


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text.lower()))


# =====================================================================
# Sequence helpers
# =====================================================================


def _mean(xs: Sequence[float]) -> float:
    if not xs:
        return 0.0
    return sum(xs) / len(xs)


def bits_to_logprobs(bits_series: Sequence[float]) -> list[float]:
    """SpecDetect reads the log-probability sequence. ``score_text`` returns
    surprisal in bits = ``-log2 p``, so log2 p = ``-bits`` (a non-positive
    series). Returns natural-log-free log2 probabilities; the spectrum is
    invariant to the log base up to a global scale, so log2 is fine."""
    return [-float(b) for b in bits_series]


def bits_to_probs(bits_series: Sequence[float]) -> list[float]:
    """Lastde reads the probability sequence p = 2**(-bits)."""
    return [2.0 ** (-float(b)) for b in bits_series]


# =====================================================================
# DFT (stdlib cmath real-DFT; optional numpy fast path)
# =====================================================================


def _rdft_magnitude_stdlib(series: Sequence[float]) -> list[float]:
    """Hand-rolled real-input DFT magnitude spectrum, stdlib ``cmath`` only.

    Returns the magnitudes of bins 0..floor(n/2) of the DFT of ``series``
    (the non-redundant half of a real-input transform). O(n^2) — bounded by
    ``MAX_SERIES_FOR_DFT`` at the call site. This is the path CI exercises so
    the tested core needs no numpy.
    """
    n = len(series)
    if n == 0:
        return []
    half = n // 2
    mags: list[float] = []
    for k in range(half + 1):
        acc = 0j
        ang = -2.0 * math.pi * k / n
        for t, x in enumerate(series):
            acc += x * cmath.exp(1j * ang * t)
        mags.append(abs(acc))
    return mags


def _rdft_magnitude_numpy(series: Sequence[float]) -> list[float]:
    """numpy.rfft fast path (chosen only when numpy is already importable).
    Returns the same bins 0..floor(n/2) magnitudes as the stdlib path."""
    import numpy as np  # local import — never at module load

    arr = np.asarray(series, dtype=float)
    spec = np.fft.rfft(arr)
    return [float(abs(v)) for v in spec]


def rdft_magnitude(
    series: Sequence[float], *, use_numpy: bool | None = None
) -> list[float]:
    """Real-DFT magnitude spectrum (bins 0..floor(n/2)).

    ``use_numpy=None`` (default) auto-selects: numpy if importable, else the
    stdlib path. ``use_numpy=False`` forces the hand-rolled path (the M1 test
    path); ``use_numpy=True`` forces numpy (the parity fixture). The two agree
    to floating tolerance.
    """
    if use_numpy is None:
        try:
            import numpy  # type: ignore  # noqa: F401

            use_numpy = True
        except ImportError:
            use_numpy = False
    if use_numpy:
        return _rdft_magnitude_numpy(series)
    return _rdft_magnitude_stdlib(series)


# =====================================================================
# SpecDetect — spectral descriptors
# =====================================================================


def spectral_descriptors(
    logprob_series: Sequence[float],
    *,
    low_freq_cutoff: float = DEFAULT_LOW_FREQ_CUTOFF,
    use_numpy: bool | None = None,
) -> dict[str, Any]:
    """The SpecDetect core: DFT magnitude spectrum of the mean-removed
    log-probability sequence → a small set of spectral descriptors.

    Returns a dict with:
      * ``spectral_centroid`` — energy-weighted mean normalized frequency
        (cycles/token, in [0, 0.5]).
      * ``low_freq_energy_frac`` — fraction of total (DC-excluded) spectral
        energy below ``low_freq_cutoff`` (the SpecDetect discriminating
        region).
      * ``spectral_flatness`` — geometric-mean / arithmetic-mean of the
        magnitude spectrum (1.0 = white/flat, →0 = peaky).
      * ``peak_frequency_bin`` + ``peak_frequency_norm`` + ``peak_magnitude_norm``
        — the dominant (non-DC) bin, its normalized frequency, and its
        magnitude as a fraction of total energy.
      * ``dominant_period_tokens`` — 1 / peak_frequency_norm, the "surprise
        repeats every ~k tokens" span hint (None when the peak is DC).
      * ``n`` — series length used; ``degenerate`` / ``caveats`` flags.

    Degenerate handling (the ``surprisal_audit`` ``_acf_at_lag`` discipline): a
    constant or near-empty sequence yields descriptors ``None`` +
    ``degenerate=True`` — never a spurious number. A series below
    ``MIN_SERIES_FOR_SPECTRUM`` still reports descriptors but flags
    ``series_too_short_for_stable_spectrum``.
    """
    # A non-finite cutoff makes the `f < low_freq_cutoff` band split meaningless (NaN -> no bin counts;
    # inf -> all bins count -> low_freq_frac always 1.0): a misleading spectral band. Reject it (Codex
    # P2). The cutoff is a normalized frequency in (0, 0.5] cycles/token (0.5 = Nyquist).
    if not (isinstance(low_freq_cutoff, (int, float)) and math.isfinite(low_freq_cutoff)
            and 0.0 < float(low_freq_cutoff) <= 0.5):
        raise ValueError(
            f"low_freq_cutoff must be a finite frequency in (0, 0.5] cycles/token, "
            f"got {low_freq_cutoff!r}")
    caveats: list[str] = []
    raw = [float(x) for x in logprob_series]
    n_in = len(raw)

    if n_in > MAX_SERIES_FOR_DFT:
        raw = raw[:MAX_SERIES_FOR_DFT]
        caveats.append("truncated")
    n = len(raw)

    none_result = {
        "spectral_centroid": None,
        "low_freq_energy_frac": None,
        "spectral_flatness": None,
        "peak_frequency_bin": None,
        "peak_frequency_norm": None,
        "peak_magnitude_norm": None,
        "dominant_period_tokens": None,
        "n": n,
        "degenerate": True,
        "caveats": caveats,
    }

    # A non-finite value (NaN/inf) anywhere in the series corrupts the mean, the DFT magnitudes, and
    # every descriptor — and NaN sails past the constant/zero-energy degeneracy guards below (every
    # NaN comparison is False), so the result would be NaN JSON + a misleading band. Treat a
    # non-finite series as degenerate with an explicit caveat, never a spurious number (Codex P2).
    if not all(math.isfinite(x) for x in raw):
        caveats.append("non_finite_series")
        caveats.append("degenerate")
        return none_result

    # Too short for ANY meaningful spectrum (need at least a couple of bins).
    if n < 4:
        caveats.append("degenerate")
        return none_result

    mean = _mean(raw)
    centered = [x - mean for x in raw]
    # A constant series → all-zero after centering → no spectrum.
    if all(abs(c) < 1e-12 for c in centered):
        caveats.append("degenerate")
        return none_result

    mags = rdft_magnitude(centered, use_numpy=use_numpy)
    half = n // 2  # mags has indices 0..half
    # Bin k maps to normalized frequency k/n (cycles/token), DC (k=0) excluded
    # from the descriptors below — after mean-removal DC magnitude is ~0.
    freqs = [k / n for k in range(len(mags))]
    # Use bins 1..end (drop DC) for energy-based descriptors.
    body_mags = mags[1:]
    body_freqs = freqs[1:]
    energy = [m * m for m in body_mags]
    total_energy = sum(energy)

    if total_energy <= 1e-24:
        caveats.append("degenerate")
        return none_result

    # Centroid: energy-weighted mean frequency.
    centroid = sum(f * e for f, e in zip(body_freqs, energy)) / total_energy

    # Low-frequency energy fraction: share of energy below the cutoff.
    low_energy = sum(
        e for f, e in zip(body_freqs, energy) if f < low_freq_cutoff
    )
    low_freq_frac = low_energy / total_energy

    # Spectral flatness: geometric mean / arithmetic mean of the magnitudes
    # (over the non-DC body). 1.0 = white/flat; →0 = peaky. Guard zeros with a
    # small floor so the geometric mean is defined.
    floored = [max(m, 1e-12) for m in body_mags]
    log_mean = sum(math.log(m) for m in floored) / len(floored)
    geo_mean = math.exp(log_mean)
    arith_mean = sum(floored) / len(floored)
    flatness = geo_mean / arith_mean if arith_mean > 0 else 1.0

    # Peak (non-DC) bin.
    peak_idx_body = max(range(len(body_mags)), key=lambda i: body_mags[i])
    peak_bin = peak_idx_body + 1  # account for the dropped DC bin
    peak_freq_norm = body_freqs[peak_idx_body]
    peak_mag_norm = (
        (body_mags[peak_idx_body] ** 2) / total_energy
        if total_energy > 0 else None
    )
    dominant_period = (
        1.0 / peak_freq_norm if peak_freq_norm > 0 else None
    )

    if n < MIN_SERIES_FOR_SPECTRUM:
        caveats.append("series_too_short_for_stable_spectrum")

    return {
        "spectral_centroid": centroid,
        "low_freq_energy_frac": low_freq_frac,
        "spectral_flatness": flatness,
        "peak_frequency_bin": peak_bin,
        "peak_frequency_norm": peak_freq_norm,
        "peak_magnitude_norm": peak_mag_norm,
        "dominant_period_tokens": dominant_period,
        "n": n,
        "n_half_bins": half,
        "low_freq_cutoff": low_freq_cutoff,
        "degenerate": False,
        "caveats": caveats,
    }


# =====================================================================
# Lastde — diversity-entropy (GATED family: library-only at M1)
# =====================================================================


def _ordinal_pattern(window: Sequence[float]) -> tuple[int, ...]:
    """Map a window to its ordinal pattern: the permutation that sorts it
    (argsort), the permutation-entropy primitive. Ties are broken by index
    (stable), so equal values give a deterministic pattern."""
    return tuple(sorted(range(len(window)), key=lambda i: (window[i], i)))


def diversity_entropy(
    prob_series: Sequence[float],
    *,
    scale: int = 1,
    order: int = LASTDE_ORDER,
) -> float | None:
    """The Lastde core (the *gated* family; NOT wired into the surface at M1).

    Embed ``prob_series`` into ordered sub-windows of length ``order`` at
    stride ``scale``, map each window to its ordinal pattern, count the pattern
    histogram, and return its **normalized Shannon entropy** in [0, 1]
    (normalized by log(order!) so the max attainable diversity entropy is 1.0).

    Returns ``None`` for a degenerate input (series shorter than the embedding
    needs, or a constant series — every window has the same pattern, but we
    surface that as entropy 0, not None; None is reserved for "not enough
    data"). A strictly monotone ramp → one dominant pattern → entropy ≈ 0; an
    i.i.d. shuffle → near-uniform histogram → entropy ≈ 1.
    """
    if order < 2 or scale < 1:
        return None
    series = [float(x) for x in prob_series]
    # Windows are taken with stride 1 over the down-sampled (by `scale`)
    # series, the standard multi-scale permutation-entropy embedding.
    if scale > 1:
        series = series[::scale]
    n = len(series)
    n_windows = n - order + 1
    if n_windows < 1:
        return None
    counts: dict[tuple[int, ...], int] = {}
    for start in range(n_windows):
        pat = _ordinal_pattern(series[start : start + order])
        counts[pat] = counts.get(pat, 0) + 1
    total = sum(counts.values())
    if total <= 1:
        return None
    # Shannon entropy of the pattern histogram, normalized by log(order!).
    shannon = 0.0
    for c in counts.values():
        p = c / total
        shannon -= p * math.log(p)
    max_entropy = math.log(math.factorial(order))
    if max_entropy <= 0:
        return 0.0
    return shannon / max_entropy


def lastde_multiscale(
    prob_series: Sequence[float],
    *,
    scales: Sequence[int] = LASTDE_SCALES,
    order: int = LASTDE_ORDER,
) -> dict[str, Any]:
    """The Lastde++ multi-scale aggregate (gated; library-only at M1).

    Returns ``{"per_scale": [..], "lastde_plus": <agg>, "scales": [...]}``.
    ``per_scale[i]`` is the diversity entropy at ``scales[i]`` (or None when
    degenerate at that scale); ``lastde_plus`` is the mean of the non-None
    per-scale values (None when none are available).
    """
    per_scale = [
        diversity_entropy(prob_series, scale=s, order=order) for s in scales
    ]
    avail = [v for v in per_scale if v is not None]
    lastde_plus = (sum(avail) / len(avail)) if avail else None
    return {
        "per_scale": per_scale,
        "lastde_plus": lastde_plus,
        "scales": list(scales),
        "order": order,
    }


# =====================================================================
# PROVISIONAL band — names the SPECTRUM PROPERTY, never the inference target
# =====================================================================


def _provisional_band(descriptors: dict[str, Any]) -> dict[str, Any]:
    """Return the PROVISIONAL band over the spectral descriptors.

    The band VALUES name the measured spectrum property —
    ``flat-spectrum`` / ``concentrated-spectrum`` / ``indeterminate`` (the
    ``surprisal_audit`` smoothed/typical/indeterminate discipline) — NOT a
    machine/AI/human class. A ≥2-of-N vote so no single descriptor decides.
    Always carries ``provisional: True`` + ``calibration_anchor:
    user-baseline-required`` + ``thresholds_used`` echoed. This is a hint, not
    a verdict; there is no ``is_ai`` / composite score field anywhere.
    """
    band = "indeterminate"
    flags: list[str] = []

    if descriptors.get("degenerate"):
        return {
            "band": "indeterminate",
            "flags": ["degenerate_spectrum"],
            "provisional": True,
            "calibration_anchor": "user-baseline-required",
            "thresholds_used": PROVISIONAL_BAND_THRESHOLDS,
        }

    lf = descriptors.get("low_freq_energy_frac")
    flat = descriptors.get("spectral_flatness")
    lft = PROVISIONAL_BAND_THRESHOLDS["low_freq_energy_frac"]
    ft = PROVISIONAL_BAND_THRESHOLDS["spectral_flatness"]

    concentrated_signals = 0
    flat_signals = 0
    if isinstance(lf, (int, float)):
        if lf > lft["concentrated_above"]:
            concentrated_signals += 1
            flags.append("low_freq_energy_high")
        elif lf < lft["flat_below"]:
            flat_signals += 1
            flags.append("low_freq_energy_low")
    if isinstance(flat, (int, float)):
        if flat < ft["concentrated_below"]:
            concentrated_signals += 1
            flags.append("spectral_flatness_low")
        elif flat > ft["flat_above"]:
            flat_signals += 1
            flags.append("spectral_flatness_high")

    # Two or more signals pointing the same way wins; otherwise indeterminate.
    if concentrated_signals >= 2:
        band = "concentrated-spectrum"
    elif flat_signals >= 2:
        band = "flat-spectrum"

    return {
        "band": band,
        "flags": flags,
        "provisional": True,
        "calibration_anchor": "user-baseline-required",
        "thresholds_used": PROVISIONAL_BAND_THRESHOLDS,
    }


# =====================================================================
# Real-model scoring path (the M2 GPU smoke integration point)
# =====================================================================


def score_series_with_backend(backend: Any, text: str) -> list[float]:
    """Build the per-token surprisal series (bits) from a real causal LM.

    THIS is the only function that touches the model — the M2 surprisal-tier
    smoke. It calls ``backend.score_text(text)`` ONCE (no new backend method,
    no second forward pass — the orthogonality-by-reuse selling point: it
    consumes exactly the series ``surprisal_audit`` already pays for) and
    returns the bits series. The spectral / entropy core then runs over the
    log-prob / prob transforms of this series with no further model contact.
    """
    result = backend.score_text(text)
    # score_text returns a bare list[float] (bits); with return_top_k it would
    # return a tuple, but we never request top-k here.
    if isinstance(result, tuple):
        result = result[0]
    return [float(b) for b in result]


# =====================================================================
# Audit
# =====================================================================


def audit(
    target_text: str,
    *,
    model: Any | None = None,
    series: Sequence[float] | None = None,
    score_fn: Callable[..., Sequence[float]] | None = None,
    low_freq_cutoff: float = DEFAULT_LOW_FREQ_CUTOFF,
    use_numpy: bool | None = None,
) -> dict[str, Any]:
    """Run the SpecDetect spectral audit. Returns the ``results`` dict.

    Injection seams (so the M1 core runs with zero model):
      * ``series=`` — a raw per-token surprisal **bits** series, used directly.
      * ``score_fn=`` — a callable ``score_fn(model, text) -> bits_series``
        (the test/stub hook, mirroring ``fast_detect_curvature``'s ``score_fn``).
      * neither → the real path: ``score_series_with_backend(model, text)``.

    The returned dict carries the spectral descriptors and the PROVISIONAL
    band; it carries **no** ``is_ai`` / ``ai_probability`` / ``verdict`` /
    composite ``score`` field. The Lastde block is deliberately NOT emitted
    (gated to M2). No input is mutated; the M1 path has no model side effect.
    """
    caveats: list[str] = []

    if series is not None:
        bits_series = [float(b) for b in series]
    elif score_fn is not None:
        bits_series = [float(b) for b in score_fn(model, target_text)]
    else:
        bits_series = score_series_with_backend(model, target_text)

    logprobs = bits_to_logprobs(bits_series)
    descriptors = spectral_descriptors(
        logprobs, low_freq_cutoff=low_freq_cutoff, use_numpy=use_numpy,
    )
    caveats.extend(descriptors.get("caveats", []))

    band = _provisional_band(descriptors)

    if descriptors.get("degenerate"):
        caveats.append("spectrum_unavailable_degenerate_series")
    # No thresholds ship; the result is always uncalibrated. Surface that
    # explicitly so a consumer knows the absence of a strong band is intended.
    caveats.append("no_calibrated_thresholds_supplied")
    # Lastde is computed-and-tested but NOT wired into the surface at M1; record
    # the deferral so a reader does not mistake its absence for a bug.
    caveats.append("lastde_block_gated_to_m2_orthogonality_check")

    model_id = getattr(model, "model_id", None) if model is not None else None
    identifier_block = (
        model.identifier_block()
        if model is not None and hasattr(model, "identifier_block")
        else None
    )

    # De-dupe caveats preserving order.
    seen: set[str] = set()
    uniq_caveats = [c for c in caveats if not (c in seen or seen.add(c))]

    return {
        "model_id": model_id,
        "identifier_block": identifier_block,
        "score_version": SCORE_VERSION,
        "n_tokens": len(bits_series),
        "spectral_descriptors": {
            k: v for k, v in descriptors.items() if k != "caveats"
        },
        "band": band,
        "low_freq_cutoff": low_freq_cutoff,
        "caveats": uniq_caveats,
    }


def compose_envelope(
    *,
    target_path: Path | None,
    target_words: int,
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
            "scoring_model": results.get("model_id"),
            "score_version": results.get("score_version"),
            "low_freq_cutoff": results.get("low_freq_cutoff"),
            "threshold": None,
        },
        additional_caveats=caveats,
        references=[
            "Yang et al. 2025, 'SpecDetect: Spectral Analysis for Robust and "
            "Efficient Zero-Shot Detection of LLM-Generated Text' "
            "(arXiv:2508.11343)",
            "Xu et al. 2024, 'Training-free LLM-generated Text Detection by "
            "Mining Token Probability Sequences (Lastde / Lastde++)' "
            "(arXiv:2410.06072)",
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
    desc = results.get("spectral_descriptors") or {}
    band = results.get("band") or {}

    def fmt(v: Any, places: int = 4) -> str:
        if isinstance(v, (int, float)):
            return f"{v:.{places}f}"
        return "(unavailable)"

    lines: list[str] = []
    lines.append("# SpecDetect Spectral Audit")
    lines.append("")
    lines.append(
        f"- **Target:** `{target.get('path')}` ({target.get('words')} words)"
    )
    lines.append(f"- **Scoring model:** `{results.get('model_id')}`")
    lines.append(f"- **Score version:** `{results.get('score_version')}`")
    lines.append(f"- **Tokens scored:** {results.get('n_tokens')}")
    lines.append("")

    lines.append("## Spectral descriptors")
    lines.append("")
    if desc.get("degenerate"):
        lines.append(
            "_Spectrum unavailable — the surprisal series is constant or too "
            "short for a stable spectrum. This read is unavailable; it is not "
            "a human-authorship finding._"
        )
    else:
        lines.append(f"- **Spectral centroid:** {fmt(desc.get('spectral_centroid'))}")
        lines.append(
            f"- **Low-frequency energy fraction:** "
            f"{fmt(desc.get('low_freq_energy_frac'))}"
        )
        lines.append(f"- **Spectral flatness:** {fmt(desc.get('spectral_flatness'))}")
        lines.append(
            f"- **Peak frequency bin:** {desc.get('peak_frequency_bin')} "
            f"(norm {fmt(desc.get('peak_frequency_norm'))})"
        )
        lines.append(
            f"- **Dominant period (tokens):** "
            f"{fmt(desc.get('dominant_period_tokens'), 1)}"
        )
    lines.append("")

    lines.append("## Band (PROVISIONAL)")
    lines.append("")
    lines.append(f"**Spectrum band:** `{band.get('band')}`")
    lines.append(
        f"**Calibration anchor:** {band.get('calibration_anchor')}"
    )
    lines.append(
        "_PROVISIONAL: the band names a spectrum property "
        "(flat-spectrum / concentrated-spectrum / indeterminate), not an "
        "AI/human class. Ships uncalibrated, no threshold; the operator "
        "adjudicates._"
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
            "SpecDetect spectral audit (Yang et al. 2025) — Surface-5 "
            "discrimination evidence, uncalibrated, non-verdict."
        )
    )
    parser.add_argument("target", help="Path to target text file (UTF-8).")
    parser.add_argument(
        "--model", default="gpt2",
        help="Scoring model alias or HF ID (default gpt2).",
    )
    parser.add_argument(
        "--low-freq-cutoff", type=float, default=DEFAULT_LOW_FREQ_CUTOFF,
        help=(
            "Normalized-frequency cutoff for the low-frequency energy "
            f"fraction (default {DEFAULT_LOW_FREQ_CUTOFF})."
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
        help="Explicit torch device (cuda, cuda:1, cpu). Default: auto.",
    )
    parser.add_argument(
        "--per-bin", action="store_true",
        help="(Reserved) include the per-bin magnitude spectrum in output.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Print the JSON envelope to stdout instead of writing files.",
    )
    parser.add_argument(
        "--out", default=None,
        help="Evidence pack JSON path (default <target>.specdetect_audit.json).",
    )
    parser.add_argument(
        "--out-md", default=None,
        help="Evidence pack markdown path (default <target>.specdetect_audit.md).",
    )
    parser.add_argument("--licenses", default=DEFAULT_LICENSES)
    parser.add_argument("--does-not-license", default=DEFAULT_DOES_NOT_LICENSE)
    args = parser.parse_args(argv)

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

    # Fail cleanly when the surprisal tier (torch) is absent — a clean install
    # hint, no traceback (the fast_detect_curvature main() precedent). The real
    # model + backend are touched only here.
    try:
        import torch  # type: ignore  # noqa: F401
    except ImportError:
        print(
            "error: SpecDetect needs the surprisal tier (transformers + "
            "torch), which is not installed.\n"
            "  Install with: pip install -r requirements-surprisal.txt\n"
            "  (opt-in Tier-4 / surprisal dependency layer; see "
            "scripts/calibration/RUNBOOK_tier4_install.md for the wheel "
            "decision tree and a smoke test).",
            file=sys.stderr,
        )
        return 2

    try:
        from surprisal_backend import (  # type: ignore
            SurprisalBackend,
            SurprisalBackendError,
            resolve_model_arg,
        )
    except ImportError as exc:
        print(f"error: surprisal backend unavailable: {exc}", file=sys.stderr)
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
            target_text, model=model, low_freq_cutoff=args.low_freq_cutoff,
        )
    except SurprisalBackendError as exc:
        print(f"error: scoring failed ({args.model}): {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:   # invalid --low-freq-cutoff (non-finite / out of (0, 0.5]) -> bad input
        print(f"error: invalid input: {exc}", file=sys.stderr)
        return 2

    envelope = compose_envelope(
        target_path=target_path,
        target_words=target_words,
        results=results,
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
            target_path.suffix + ".specdetect_audit.json"
        )
    )
    out_md = (
        Path(args.out_md)
        if args.out_md
        else target_path.with_suffix(target_path.suffix + ".specdetect_audit.md")
    )
    out_json.write_text(
        json.dumps(envelope, indent=2, default=str), encoding="utf-8",
    )
    out_md.write_text(markdown, encoding="utf-8")
    print(f"Wrote {out_json} + {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
