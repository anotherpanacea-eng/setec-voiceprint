#!/usr/bin/env python3
"""surprisal_audit.py — standalone causal-LM surprisal audit.

Computes the per-token surprisal series of a prose draft against a
causal language model (default: TinyLlama; see ``--model``) and
summarises the series with the statistics pinned in
``internal/SPEC_surprisal_signal.md`` §2.2 / §2.3:

  * **Mean surprisal** — average predictability (bits per token).
    AI prose tends LOWER (the LM samples near its own mode).
  * **Surprisal SD / variance** — DivEye's load-bearing signal. AI
    prose tends LOWER (more uniform surprise).
  * **Lag-k autocorrelation** at lags 1, 2, 3, 5, 10. AI prose
    tends HIGHER at small lags (smooth predictability across nearby
    positions).
  * **Skew / kurtosis** of the distribution. Human prose tends
    toward positive skew + higher kurtosis (clustered high-surprise
    moments).
  * **Position of max surprisal**.
  * **Top-k most-surprising tokens** (k=20 by default) — reader-
    facing diagnostic with decoded token text + position.

Sliding-window mode (``--sliding-window``, per SPEC §2.4):
token-indexed (not word-indexed; surprisal's native unit is tokens).
Default W=200, S=100. Per-window stats: mean, sd, lag-1 ACF. The
heatmap renderer (``sliding_window_heatmap.py``) can consume the
trajectory.

PROVISIONAL bands ship as illustrative-only per the
"Stylometry to the people" policy (SPEC §3.5): the ClaimLicense
block names ``calibration_anchor: user-baseline-required``
explicitly so consumers reading the markdown see immediately that
the band call is not load-bearing. Users wanting anchored
thresholds run their own calibration per
``scripts/calibration/PROVENANCE.md``.

Task surface: ``smoothing_diagnosis``. The audit's evidence is
about predictability uniformity, not authorship. The ClaimLicense
block does NOT license an AI-provenance verdict.

Usage::

    # Default: TinyLlama, JSON + markdown to stdout.
    python3 scripts/surprisal_audit.py path/to/draft.txt

    # Different model, JSON output to file:
    python3 scripts/surprisal_audit.py path/to/draft.txt \\
        --model olmo2_1b --json --out audit.json

    # Sliding-window mode (token windows):
    python3 scripts/surprisal_audit.py path/to/draft.txt \\
        --sliding-window --window-size 200 --stride 100

C.3 is the standalone-audit half of the Phase C plan; the variance-
audit Tier 4 integration ships separately (C.4) so each phase
reviews independently.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
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
    MODEL_ALIASES,
    SurprisalBackend,
    SurprisalBackendError,
    resolve_model_arg,
)

TASK_SURFACE = "smoothing_diagnosis"
TOOL_NAME = "surprisal_audit"
SCRIPT_VERSION = "1.0"

# Defaults for sliding-window mode (token windows, per SPEC §2.4).
DEFAULT_WINDOW_SIZE = 200
DEFAULT_STRIDE = 100
# Lags for autocorrelation reporting (SPEC §2.2).
ACF_LAGS: tuple[int, ...] = (1, 2, 3, 5, 10)
# Top-k surprising tokens to surface as diagnostic.
DEFAULT_TOP_K = 20
# Minimum series length for autocorrelation to be defined at a given
# lag. Below this, the lag is reported as None and the JSON output
# includes a `degenerate=true` flag rather than a numeric value.
MIN_SERIES_FOR_ACF = 30


# --------------- Pure math helpers ----------------------------


def _mean(xs: Sequence[float]) -> float:
    if not xs:
        return 0.0
    return float(sum(xs) / len(xs))


def _sample_variance(xs: Sequence[float]) -> float:
    """Sample variance (Bessel-corrected). Returns 0.0 for n < 2."""
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return float(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _sample_sd(xs: Sequence[float]) -> float:
    return math.sqrt(_sample_variance(xs))


def _acf_at_lag(xs: Sequence[float], lag: int) -> float | None:
    """Sample autocorrelation at the given lag.

    Returns ``None`` when the series is too short for the lag to be
    meaningfully estimated (length below ``MIN_SERIES_FOR_ACF`` or
    length ≤ lag) — the caller surfaces this in the JSON as a
    degenerate-series flag rather than emitting a spurious number.
    Also returns ``None`` for a constant series (zero denominator).
    """
    n = len(xs)
    if n < MIN_SERIES_FOR_ACF or lag <= 0 or lag >= n:
        return None
    m = _mean(xs)
    denom = sum((x - m) ** 2 for x in xs)
    if denom == 0.0:
        return None
    numer = sum((xs[i] - m) * (xs[i + lag] - m) for i in range(n - lag))
    return float(numer / denom)


def _skew(xs: Sequence[float]) -> float | None:
    """Sample skewness (Fisher-Pearson, no bias correction).

    Returns ``None`` when the series has fewer than 3 points or is
    constant (zero SD)."""
    n = len(xs)
    if n < 3:
        return None
    m = _mean(xs)
    sd = _sample_sd(xs)
    if sd == 0.0:
        return None
    return float(sum(((x - m) / sd) ** 3 for x in xs) / n)


def _excess_kurtosis(xs: Sequence[float]) -> float | None:
    """Excess kurtosis (kurtosis - 3). Same degenerate cases as skew."""
    n = len(xs)
    if n < 4:
        return None
    m = _mean(xs)
    sd = _sample_sd(xs)
    if sd == 0.0:
        return None
    fourth = sum(((x - m) / sd) ** 4 for x in xs) / n
    return float(fourth - 3.0)


def _position_of_max(xs: Sequence[float]) -> int | None:
    """Index of the maximum value in ``xs``, or ``None`` for empty."""
    if not xs:
        return None
    return int(max(range(len(xs)), key=lambda i: xs[i]))


# --------------- Sliding-window helper -----------------------


def _sliding_windows(
    series: Sequence[float], *, window_size: int, stride: int,
) -> list[dict[str, Any]]:
    """Slide a window of ``window_size`` across ``series`` with
    ``stride`` step. Per-window stats: mean, sd, lag-1 ACF (or None
    when the window is too short for ACF).

    Each returned dict has ``start_index``, ``end_index`` (exclusive),
    ``length``, ``mean``, ``sd``, ``acf_lag1`` (nullable). The last
    window may be shorter than ``window_size`` if ``len(series)``
    isn't divisible by ``stride`` — callers can decide to drop it
    if they need uniform-sized windows.

    Returns ``[]`` for empty series or window_size <= 0 to keep the
    contract safe-by-default. The caller surfaces this as
    ``available=False`` in the audit dict.
    """
    n = len(series)
    if n == 0 or window_size <= 0 or stride <= 0:
        return []
    windows: list[dict[str, Any]] = []
    i = 0
    while i < n:
        end = min(i + window_size, n)
        window = list(series[i:end])
        if not window:
            break
        windows.append({
            "start_index": i,
            "end_index": end,
            "length": end - i,
            "mean": _mean(window),
            "sd": _sample_sd(window),
            "acf_lag1": _acf_at_lag(window, 1),
        })
        if end >= n:
            break
        i += stride
    return windows


# --------------- Provisional banding ------------------------
#
# Per SPEC §3.5: ship PROVISIONAL bands as illustrative-only so the
# operator gets immediate orientation, but the ClaimLicense block
# names `calibration_anchor: user-baseline-required` explicitly so
# the band call is never read as load-bearing. The numbers below
# come from the fixture-derived heuristics in the companion
# Phase C.5 plan (not yet run on the AMD desktop); they are
# documented here as PROVISIONAL and the CLI's banding output
# carries `provisional=True`.
#
# An anchored calibration replaces these with values from the
# operator's own corpus calibration; the framework's posture is
# that thresholds are user-specific and the bands here exist only
# to give the operator a first reading.

PROVISIONAL_BAND_THRESHOLDS: dict[str, dict[str, float]] = {
    "mean_surprisal_bits": {
        # AI < human: low mean = highly predictable prose.
        "flat_below": 3.5,
        "typical_above": 5.0,
    },
    "sd_surprisal_bits": {
        # AI < human: low SD = uniform surprise.
        "flat_below": 1.5,
        "typical_above": 2.5,
    },
    "acf_lag1": {
        # AI > human: high ACF = smooth local predictability.
        "smoothed_above": 0.30,
        "typical_below": 0.10,
    },
}


def _provisional_band(stats: dict[str, Any]) -> dict[str, Any]:
    """Return the PROVISIONAL band call + an explicit
    provisional=True marker. The caller must surface the marker
    prominently in the rendered output."""
    band = "indeterminate"
    flags: list[str] = []
    mean_bits = stats.get("mean_surprisal_bits")
    sd_bits = stats.get("sd_surprisal_bits")
    acf1 = stats.get("autocorrelation", {}).get("lag_1")
    mt = PROVISIONAL_BAND_THRESHOLDS["mean_surprisal_bits"]
    st = PROVISIONAL_BAND_THRESHOLDS["sd_surprisal_bits"]
    at = PROVISIONAL_BAND_THRESHOLDS["acf_lag1"]
    flat_signals = 0
    typical_signals = 0
    if isinstance(mean_bits, (int, float)):
        if mean_bits < mt["flat_below"]:
            flat_signals += 1
            flags.append("mean_surprisal_low")
        elif mean_bits > mt["typical_above"]:
            typical_signals += 1
    if isinstance(sd_bits, (int, float)):
        if sd_bits < st["flat_below"]:
            flat_signals += 1
            flags.append("sd_surprisal_low")
        elif sd_bits > st["typical_above"]:
            typical_signals += 1
    if isinstance(acf1, (int, float)):
        if acf1 > at["smoothed_above"]:
            flat_signals += 1
            flags.append("acf_lag1_high")
        elif acf1 < at["typical_below"]:
            typical_signals += 1
    # Two or more signals pointing the same way wins; otherwise
    # indeterminate.
    if flat_signals >= 2:
        band = "smoothed"
    elif typical_signals >= 2:
        band = "typical"
    return {
        "band": band,
        "flags": flags,
        "provisional": True,
        "calibration_anchor": "user-baseline-required",
        "thresholds_used": PROVISIONAL_BAND_THRESHOLDS,
    }


# --------------- Main audit ---------------------------------


def audit_surprisal(
    text: str,
    *,
    backend: SurprisalBackend | None = None,
    score_fn: Callable[..., Any] | None = None,
    sliding_window: bool = False,
    window_size: int = DEFAULT_WINDOW_SIZE,
    stride: int = DEFAULT_STRIDE,
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, Any]:
    """Compute the surprisal audit on ``text``.

    Either ``backend`` (a ``SurprisalBackend`` instance) or
    ``score_fn`` (a callable matching the backend's
    ``score_text(text, return_top_k=...) -> (series, top_k_tokens)``
    or ``score_text(text, return_top_k=0) -> series`` signature)
    must be supplied. The ``score_fn`` override is the test-friendly
    path: pass a stub that returns deterministic synthetic
    surprisals without loading a real causal LM.

    Returns a structured dict (JSON-serializable) suitable for the
    markdown renderer or direct JSON dump. Layout::

        {
          "task_surface": "smoothing_diagnosis",
          "tool": "surprisal_audit",
          "version": "1.0",
          "available": True,
          "n_tokens": ...,
          "series_length": ...,
          "summary": {
            "mean_surprisal_bits": ...,
            "sd_surprisal_bits": ...,
            "variance_surprisal_bits": ...,
            "min": ..., "max": ...,
            "autocorrelation": {"lag_1": ..., "lag_2": ..., ...},
            "skew": ..., "excess_kurtosis": ...,
            "position_of_max": ...,
          },
          "top_k_tokens": [...],
          "sliding_window": {"enabled": False} OR {...trajectory...},
          "band": {...provisional...},
        }
    """
    if not text or not text.strip():
        return {
            "task_surface": TASK_SURFACE,
            "tool": TOOL_NAME,
            "version": SCRIPT_VERSION,
            "available": False,
            "reason": "empty text",
        }
    if backend is None and score_fn is None:
        raise ValueError(
            "audit_surprisal requires either backend or score_fn."
        )

    if score_fn is not None:
        scorer = score_fn
    else:
        scorer = backend.score_text  # type: ignore[union-attr]

    # We ask for top-k tokens directly from the scorer; the SPEC
    # backend supports this in one pass so we don't pay scoring cost
    # twice.
    #
    # Reviewer P2 (2026-05-14): scoring-time exceptions other than
    # SurprisalBackendError (e.g., RuntimeError on context-window
    # overflow, IndexError on tokenizer surprises, MemoryError on
    # too-large inputs) used to escape audit_surprisal and produce a
    # traceback. They're now caught here and converted to an
    # ``available=False`` return value with the typed exception name
    # surfaced as the reason. SurprisalBackendError still passes
    # through unchanged so callers that want backend-typed handling
    # (the CLI's main(), variance Tier 4's helper) keep their
    # existing behavior. The variance Tier 4 path was already
    # catching broad exceptions; this aligns audit_surprisal with
    # that posture so all callers see the same clean-failure shape.
    try:
        if top_k > 0:
            result = scorer(text, return_top_k=top_k)
            if isinstance(result, tuple):
                series, top_tokens = result
            else:  # pragma: no cover — defensive
                series, top_tokens = result, []
        else:
            result = scorer(text, return_top_k=0)
            series = (
                result if not isinstance(result, tuple) else result[0]
            )
            top_tokens = []
    except SurprisalBackendError:
        # Backend-typed errors pass through; CLI main() catches.
        raise
    except (
        MemoryError, RuntimeError, IndexError, ValueError, OSError,
    ) as exc:
        return {
            "task_surface": TASK_SURFACE,
            "tool": TOOL_NAME,
            "version": SCRIPT_VERSION,
            "available": False,
            "reason": (
                f"surprisal scoring failed at backend "
                f"({type(exc).__name__}: {exc}). Common causes: "
                f"input exceeded the model's context window, the "
                f"tokenizer produced an unexpected shape, or the "
                f"device ran out of memory. See SPEC §3.3 for the "
                f"chunking contract that addresses context-window "
                f"overflows."
            ),
        }

    series_list = [float(x) for x in (series or [])]
    n_series = len(series_list)
    if n_series == 0:
        return {
            "task_surface": TASK_SURFACE,
            "tool": TOOL_NAME,
            "version": SCRIPT_VERSION,
            "available": False,
            "reason": "surprisal series empty (input too short)",
        }

    summary = {
        "mean_surprisal_bits": _mean(series_list),
        "sd_surprisal_bits": _sample_sd(series_list),
        "variance_surprisal_bits": _sample_variance(series_list),
        "min": float(min(series_list)),
        "max": float(max(series_list)),
        "autocorrelation": {
            f"lag_{k}": _acf_at_lag(series_list, k) for k in ACF_LAGS
        },
        "skew": _skew(series_list),
        "excess_kurtosis": _excess_kurtosis(series_list),
        "position_of_max": _position_of_max(series_list),
        "series_too_short_for_acf": n_series < MIN_SERIES_FOR_ACF,
    }

    sw_block: dict[str, Any]
    if sliding_window:
        windows = _sliding_windows(
            series_list, window_size=window_size, stride=stride,
        )
        sw_block = {
            "enabled": True,
            "window_size_tokens": window_size,
            "stride_tokens": stride,
            "n_windows": len(windows),
            "trajectory": windows,
        }
    else:
        sw_block = {"enabled": False}

    band_block = _provisional_band(summary)

    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "available": True,
        "n_tokens_scored": n_series + 1,
        "series_length": n_series,
        "summary": summary,
        "top_k_tokens": top_tokens,
        "sliding_window": sw_block,
        "band": band_block,
    }


# --------------- Claim-license block ------------------------


def _claim_license(audit: dict[str, Any]) -> ClaimLicense:
    """Build the ClaimLicense. PROVISIONAL bands are named
    explicitly via ``calibration_anchor: user-baseline-required``."""
    summary = audit.get("summary", {})
    band = audit.get("band", {})
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Per-token surprisal series of the input against a "
            "causal language model, summarised as mean / SD / "
            "autocorrelation / skew / kurtosis / top-k surprising "
            "tokens. Surfaces *predictability uniformity*: how "
            "evenly the LM's surprise is distributed across the "
            "draft."
        ),
        does_not_license=(
            "An AI-provenance verdict. Predictability uniformity "
            "is one signal among many; AI-edited prose, "
            "institutional-voice prose, and formulaic genres all "
            "produce comparable patterns. The audit does NOT "
            "license a claim that a draft is AI-generated based "
            "on surprisal stats alone."
        ),
        comparison_set={
            "model": audit.get("backend", {}).get("id"),
            "revision": audit.get("backend", {}).get("revision"),
            "n_tokens_scored": audit.get("n_tokens_scored"),
            "series_length": audit.get("series_length"),
            "band": band.get("band"),
        },
        additional_caveats=[
            "Bands are PROVISIONAL: thresholds are fixture-derived "
            "heuristics, not anchored to any labeled corpus. "
            "calibration_anchor: user-baseline-required. Users "
            "wanting load-bearing thresholds run the §6.4 fixture "
            "suite against their own baseline.",
            "Surprisal values depend on tokenizer choice and model "
            "checkpoint. Comparisons across runs require the same "
            "model + revision; the backend identifier block records "
            "both for reproducibility.",
            "The model's training data may have been contaminated "
            "by AI-generated web content (especially modern LMs). "
            "Older / smaller candidates (gpt2, tinyllama) are "
            "less likely to be contaminated; the operator chooses "
            "the trade-off via --model.",
        ],
    )


def _claim_license_block(audit: dict[str, Any]) -> str:
    return _claim_license(audit).render_block().rstrip()


_RESULTS_KEYS = (
    "n_tokens_scored", "series_length", "summary",
    "top_k_tokens", "sliding_window", "band", "backend",
)


def build_audit_payload(
    audit: dict[str, Any],
    *,
    target_path: Any,
) -> dict[str, Any]:
    """Wrap the surprisal audit dict in the schema_version 1.0
    envelope per ``internal/SPEC_output_schema_unification.md``.
    """
    available = bool(audit.get("available", True))
    target_words = int(audit.get("n_tokens_scored", 0) or 0)
    target_extra: dict[str, Any] = {}
    if "preprocessing" in audit:
        target_extra["preprocessing"] = audit["preprocessing"]

    results: dict[str, Any] = {}
    if available:
        for k in _RESULTS_KEYS:
            if k in audit:
                results[k] = audit[k]

    warnings: list[str] = []
    if not available and "reason" in audit:
        warnings.append(audit["reason"])

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=None,
        results=results,
        claim_license=_claim_license(audit) if available else None,
        available=available,
        warnings=warnings,
        target_extra=target_extra or None,
    )


# --------------- Markdown renderer --------------------------


def render_markdown(audit: dict[str, Any]) -> str:
    """Render a human-readable markdown report from the audit dict.
    Mirrors the layout of ``semantic_trajectory_audit.py`` so
    consumers reading both audits in sequence see consistent
    structure."""
    if not audit.get("available"):
        return (
            "# Surprisal audit\n\n"
            f"_Unavailable: {audit.get('reason', 'unknown')}._\n"
        )
    summary = audit["summary"]
    band = audit.get("band", {})
    lines: list[str] = [
        "# Surprisal audit",
        "",
        f"**Task surface:** `{audit['task_surface']}`",
        f"**Tool:** `{audit['tool']}` (v{audit['version']})",
        f"**Tokens scored:** {audit['n_tokens_scored']:,}",
        f"**Series length:** {audit['series_length']:,}",
        "",
        "## Distribution summary",
        "",
        f"- **Mean surprisal:** {summary['mean_surprisal_bits']:.3f} bits/token",
        f"- **SD:** {summary['sd_surprisal_bits']:.3f} bits/token",
        f"- **Min / max:** {summary['min']:.3f} / {summary['max']:.3f} bits",
    ]
    if summary.get("skew") is not None:
        lines.append(f"- **Skew:** {summary['skew']:.3f}")
    if summary.get("excess_kurtosis") is not None:
        lines.append(
            f"- **Excess kurtosis:** {summary['excess_kurtosis']:.3f}"
        )
    if summary.get("position_of_max") is not None:
        lines.append(
            f"- **Position of max surprisal:** index "
            f"{summary['position_of_max']:,}"
        )
    lines += ["", "## Autocorrelation", ""]
    acf = summary["autocorrelation"]
    for lag in ACF_LAGS:
        val = acf.get(f"lag_{lag}")
        rendered = (
            f"{val:.3f}" if isinstance(val, (int, float)) else "n/a"
        )
        lines.append(f"- lag-{lag}: {rendered}")
    if summary.get("series_too_short_for_acf"):
        lines += [
            "",
            "_Series is too short for stable ACF estimates "
            f"(< {MIN_SERIES_FOR_ACF} tokens); lags reported as `n/a`._",
        ]

    sw = audit.get("sliding_window", {})
    if sw.get("enabled"):
        lines += [
            "",
            "## Sliding-window trajectory",
            "",
            f"- Window size: {sw['window_size_tokens']} tokens",
            f"- Stride: {sw['stride_tokens']} tokens",
            f"- N windows: {sw['n_windows']}",
        ]
        traj = sw.get("trajectory", [])
        if traj:
            lines += [
                "",
                "| start | end | mean | sd | acf_lag1 |",
                "|---:|---:|---:|---:|---:|",
            ]
            for w in traj:
                acf1 = w.get("acf_lag1")
                acf1_s = (
                    f"{acf1:.3f}" if isinstance(acf1, (int, float))
                    else "n/a"
                )
                lines.append(
                    f"| {w['start_index']} | {w['end_index']} | "
                    f"{w['mean']:.3f} | {w['sd']:.3f} | {acf1_s} |"
                )

    top = audit.get("top_k_tokens") or []
    if top:
        lines += [
            "",
            f"## Top-{len(top)} surprising tokens",
            "",
            "| rank | position | token | surprisal (bits) |",
            "|---:|---:|---|---:|",
        ]
        for rank, t in enumerate(top, 1):
            tok = t.get("token_text", "")
            # Escape pipes inside the token text so the markdown
            # table doesn't get torn apart by tokens that contain |.
            tok_safe = tok.replace("|", "\\|") if isinstance(tok, str) else tok
            lines.append(
                f"| {rank} | {t.get('position', '?')} | "
                f"`{tok_safe!r}` | {t.get('surprisal_bits', 0.0):.3f} |"
            )

    lines += [
        "",
        "## Band (PROVISIONAL)",
        "",
        f"- **Band call:** `{band.get('band', 'indeterminate')}`",
        f"- **Provisional:** {band.get('provisional')}",
        f"- **Calibration anchor:** `{band.get('calibration_anchor')}`",
    ]
    flags = band.get("flags") or []
    if flags:
        lines.append("- **Flags:** " + ", ".join(f"`{f}`" for f in flags))

    # Append the ClaimLicense block as the final section.
    lines += ["", _claim_license_block(audit)]
    return "\n".join(lines).rstrip() + "\n"


# --------------- CLI ----------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="surprisal_audit.py",
        description=(
            "Standalone causal-LM surprisal audit. Computes the "
            "per-token surprisal series of a draft against a "
            "causal LM and reports predictability uniformity "
            "statistics. PROVISIONAL bands per Stylometry-to-"
            "the-people policy. See SPEC_surprisal_signal.md."
        ),
    )
    p.add_argument("input", help="Path to .txt or .md target file.")
    p.add_argument(
        "--model", default=None,
        help=(
            f"Causal LM alias or HuggingFace id. Aliases: "
            f"{', '.join(sorted(MODEL_ALIASES))}. "
            f"Default: {DEFAULT_MODEL}."
        ),
    )
    p.add_argument(
        "--revision", default=None,
        help="Pin a HuggingFace commit SHA for reproducibility.",
    )
    p.add_argument(
        "--surprisal-dtype",
        choices=("auto", "fp32", "fp16", "bf16"),
        default="auto",
        help=(
            "Precision for causal-LM inference. ``auto`` picks bf16 "
            "on supporting cuda (Ampere+ / Hopper / Ada), fp16 on "
            "older cuda (V100 / T4), fp32 on CPU / MPS. Explicit "
            "values override the auto resolution. The log_softmax "
            "step is always computed in fp32 so the surprisal-"
            "series numerical contract is stable across dtype "
            "choices (1.93.0+)."
        ),
    )
    p.add_argument(
        "--sliding-window", action="store_true",
        help=(
            "Compute per-window stats (mean, sd, lag-1 ACF) over a "
            f"token window. Default window {DEFAULT_WINDOW_SIZE}, "
            f"stride {DEFAULT_STRIDE}."
        ),
    )
    p.add_argument(
        "--window-size", type=int, default=DEFAULT_WINDOW_SIZE,
        help=(
            f"Sliding-window size in tokens (default "
            f"{DEFAULT_WINDOW_SIZE})."
        ),
    )
    p.add_argument(
        "--stride", type=int, default=DEFAULT_STRIDE,
        help=f"Sliding-window stride (default {DEFAULT_STRIDE}).",
    )
    p.add_argument(
        "--top-k", type=int, default=DEFAULT_TOP_K,
        help=(
            f"Surface the k most-surprising tokens (default "
            f"{DEFAULT_TOP_K}). Pass 0 to skip the diagnostic."
        ),
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit JSON instead of markdown.",
    )
    p.add_argument("--out", help="Write output to this path.")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    target_path = Path(args.input).expanduser()
    if not target_path.is_file():
        sys.stderr.write(f"Input not found: {target_path}\n")
        return 2
    text = target_path.read_text(encoding="utf-8", errors="ignore")
    model_id = resolve_model_arg(args.model)
    try:
        backend = SurprisalBackend(
            model_id=model_id, revision=args.revision,
            dtype=args.surprisal_dtype,
        )
    except SurprisalBackendError as exc:
        sys.stderr.write(f"Backend construction failed: {exc}\n")
        return 3
    try:
        audit = audit_surprisal(
            text,
            backend=backend,
            sliding_window=args.sliding_window,
            window_size=args.window_size,
            stride=args.stride,
            top_k=args.top_k,
        )
    except SurprisalBackendError as exc:
        # Backend-typed failures keep the existing rc=3 path so
        # ops scripts depending on the distinction (load failed
        # vs scored cleanly) continue to work.
        sys.stderr.write(f"Surprisal scoring failed: {exc}\n")
        return 3
    # audit_surprisal converts non-typed runtime errors
    # (RuntimeError, IndexError, MemoryError, etc.) into an
    # ``available=False`` result rather than propagating a
    # traceback, so the CLI doesn't need a separate catch for
    # those — we just render the unavailable audit cleanly below.
    # Attach the backend identifier block so PROVENANCE is captured
    # in the audit's JSON output and surfaced in the ClaimLicense.
    audit["backend"] = backend.identifier_block()
    if args.json:
        payload = build_audit_payload(audit, target_path=target_path)
        out = json.dumps(payload, indent=2, default=str, sort_keys=True)
    else:
        out = render_markdown(audit)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
