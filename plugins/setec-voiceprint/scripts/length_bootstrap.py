"""Length-matched bootstrap helpers (Phase 1 step 3 of the validation
spine).

The classical baseline z-score path in ``variance_audit.compare_to_baseline``
compares a single target document of length N against the mean and SD of
the same statistic computed across full baseline files (which may be much
longer than N). At small target N or with a small baseline file count the
SD estimate is noisy, and the resulting z-scores frequently land in the
unreliable-signal regime even for register-matched native prose.

The length-matched bootstrap replaces noisy z-scores with empirical
percentiles drawn from many length-N windows of the baseline corpus:

  1. For each baseline text, sample windows of width n_words = target_N
     (with replacement on start positions; same word-boundary slicing
     used by the sliding-window mode).
  2. Pool windows across all baseline files into an empirical
     distribution of "what statistic value does this writer produce in
     length-N chunks."
  3. Compute the target's percentile in that empirical distribution.
  4. Bootstrap-resample the per-window statistic array via
     ``scipy.stats.bootstrap`` to put a confidence interval on the
     percentile estimate; the CI captures uncertainty introduced by the
     finite window count.

Output is a dict with the empirical quantiles of the baseline
distribution at the target's length, the target's percentile, the CI on
that percentile, and the resample count used. The variance audit
consumes this dict and replaces the z-score block when ``--bootstrap``
is requested.

This module owns the window sampler and the percentile computation;
SciPy owns the resampling and interval methods. The decision rule from
``references/implementation-survey.md`` was: borrow the resampling
machinery, keep the comparison-design logic local. SciPy is a required
runtime dependency (``requirements.txt``) for the bootstrap path; the
calling script must check availability before invoking these helpers.
"""

from __future__ import annotations

import math
import random
import re
import statistics
from typing import Any, Callable, Sequence


try:
    from scipy import stats as scipy_stats  # type: ignore
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


# Sentinel surface tag. The bootstrap helpers themselves are not a task
# surface; they're called by variance_audit, voice_distance, and the
# future validation harness, each of which carries its own surface tag.
TASK_SURFACE = "smoothing_diagnosis"


_WORD_BOUNDARY = re.compile(r"\S+")


def word_boundary_slice(text: str, start_word: int, n_words: int) -> str:
    """Return the slice of ``text`` covering ``n_words`` whitespace-
    delimited tokens starting at the ``start_word``-th token. Preserves
    in-window punctuation, paragraph breaks, and quoted spans because the
    slice is taken from the original string between word boundaries
    rather than from a re-joined token list.

    Returns the empty string if ``start_word`` is past the end of the
    text or ``n_words`` is non-positive.
    """
    if n_words <= 0:
        return ""
    matches = list(_WORD_BOUNDARY.finditer(text))
    if start_word >= len(matches):
        return ""
    end_word = min(start_word + n_words, len(matches))
    s = matches[start_word].start()
    e = matches[end_word - 1].end()
    return text[s:e]


def sample_window_slices(
    text: str,
    n_words: int,
    n_windows: int,
    *,
    seed: int | None = None,
) -> list[str]:
    """Sample ``n_windows`` random length-``n_words`` slices of ``text``.

    If ``text`` has fewer than ``n_words`` tokens, returns a single
    slice covering the whole text (no resampling possible at this
    length). Otherwise samples ``n_windows`` start positions uniformly
    in ``[0, total_words - n_words]`` with replacement.
    """
    if n_words <= 0 or n_windows <= 0:
        return []
    matches = list(_WORD_BOUNDARY.finditer(text))
    total = len(matches)
    if total <= n_words:
        return [text]
    rng = random.Random(seed)
    max_start = total - n_words
    starts = [rng.randint(0, max_start) for _ in range(n_windows)]
    out: list[str] = []
    for s in starts:
        end_word = s + n_words
        out.append(text[matches[s].start():matches[end_word - 1].end()])
    return out


def collect_window_statistic(
    baseline_texts: Sequence[str],
    statistic_fn: Callable[[str], float | None],
    target_n_words: int,
    *,
    n_windows_per_file: int = 50,
    max_total_windows: int = 500,
    seed: int | None = None,
) -> list[float]:
    """Apply ``statistic_fn`` to length-matched windows across baseline
    texts and return the pooled (non-None) values.

    ``n_windows_per_file`` is a hint, not a hard target: the per-file
    sample is capped at ``max_total_windows / len(baseline_texts)`` so
    long corpora do not dominate the pool. Files shorter than
    ``target_n_words`` contribute one whole-file sample.

    The seed is deterministic across files: each file gets a derived
    sub-seed so the same overall seed reproduces the same windows.
    """
    if not baseline_texts or target_n_words <= 0:
        return []
    per_file_cap = max(1, max_total_windows // max(1, len(baseline_texts)))
    target_per_file = min(n_windows_per_file, per_file_cap)
    out: list[float] = []
    for i, text in enumerate(baseline_texts):
        sub_seed = None if seed is None else seed + i
        windows = sample_window_slices(
            text, target_n_words, target_per_file, seed=sub_seed,
        )
        for w in windows:
            v = statistic_fn(w)
            if v is None:
                continue
            if isinstance(v, float) and not math.isfinite(v):
                continue
            out.append(float(v))
    return out


def empirical_percentile(sample: Sequence[float], target: float) -> float:
    """Fraction of ``sample`` strictly less than ``target`` plus half the
    fraction equal to ``target``. This is the mid-rank percentile, a
    convention that handles ties without bias (R's ``ecdf`` plus a
    half-tie correction; sometimes called the "average rank" percentile).
    Returns 0.5 for an empty sample because no data means no preference.
    """
    if not sample:
        return 0.5
    n = len(sample)
    less = sum(1 for v in sample if v < target)
    equal = sum(1 for v in sample if v == target)
    return (less + 0.5 * equal) / n


def summarize_distribution(
    sample: Sequence[float],
    *,
    quantiles: tuple[float, ...] = (0.05, 0.25, 0.5, 0.75, 0.95),
) -> dict[str, Any]:
    """Empirical summary of the baseline window distribution at the
    target's length: quantiles, sample size, mean, SD."""
    if not sample:
        return {"n": 0, "quantiles": {}, "mean": None, "sd": None}
    sorted_sample = sorted(sample)
    qs: dict[str, float] = {}
    for q in quantiles:
        # Linear interpolation between order statistics.
        if not 0.0 <= q <= 1.0:
            continue
        if len(sorted_sample) == 1:
            qs[f"p{int(round(q * 100))}"] = sorted_sample[0]
            continue
        idx = q * (len(sorted_sample) - 1)
        lo = int(math.floor(idx))
        hi = int(math.ceil(idx))
        frac = idx - lo
        qs[f"p{int(round(q * 100))}"] = (
            sorted_sample[lo] * (1 - frac) + sorted_sample[hi] * frac
        )
    sd = statistics.stdev(sorted_sample) if len(sorted_sample) > 1 else 0.0
    return {
        "n": len(sorted_sample),
        "quantiles": qs,
        "mean": statistics.mean(sorted_sample),
        "sd": sd,
    }


def bootstrap_percentile(
    sample: Sequence[float],
    target: float,
    *,
    n_resamples: int = 9999,
    confidence_level: float = 0.95,
    method: str = "BCa",
    seed: int | None = None,
) -> dict[str, Any]:
    """Empirical percentile of ``target`` in ``sample`` plus a CI on
    that percentile via ``scipy.stats.bootstrap``.

    Method defaults to BCa (bias-corrected accelerated). Falls back to
    'percentile' if BCa fails on degenerate input (constant sample,
    fewer than two unique values).

    Returns a dict with keys ``percentile`` (point estimate),
    ``ci_low`` / ``ci_high`` (BCa confidence interval), ``method``
    actually used, and ``n_resamples``. Returns ``None`` for the CI
    fields if the sample is empty or scipy is unavailable.
    """
    point = empirical_percentile(sample, target)
    if not sample or not HAS_SCIPY:
        return {
            "percentile": point,
            "ci_low": None,
            "ci_high": None,
            "method": "none",
            "n_resamples": 0,
            "n_baseline_windows": len(sample),
        }
    if len(sample) < 2:
        # BCa needs >= 2 samples; bias correction undefined for n=1.
        return {
            "percentile": point,
            "ci_low": None,
            "ci_high": None,
            "method": "insufficient_sample",
            "n_resamples": 0,
            "n_baseline_windows": len(sample),
        }
    # Degenerate case: target is strictly past the extreme of the
    # sample, so the statistic is 0 or 1 on every resample regardless
    # of which subset is drawn. The "true" CI here is [point, point]:
    # no uncertainty in the percentile estimate from the resampling.
    # We detect this before calling scipy because BCa's bias correction
    # divides by zero on a constant statistic and returns garbage CIs.
    sample_min = min(sample)
    sample_max = max(sample)
    if target < sample_min or target > sample_max:
        return {
            "percentile": point,
            "ci_low": point,
            "ci_high": point,
            "method": "degenerate_no_ci",
            "n_resamples": 0,
            "n_baseline_windows": len(sample),
        }
    arr = list(sample)

    def _stat(x: Any, axis: int = -1) -> Any:
        # scipy.stats.bootstrap passes a numpy array; we use a portable
        # implementation that operates on the last axis.
        try:
            import numpy as np  # type: ignore
            x_arr = np.asarray(x)
            return np.mean((x_arr < target).astype(float) + 0.5 * (x_arr == target).astype(float), axis=axis)
        except ImportError:
            # scipy depends on numpy, so this branch should never fire
            # in practice; included for defensive completeness.
            pass
        return point

    chosen_method = method
    try:
        rng = None if seed is None else seed
        result = scipy_stats.bootstrap(
            (arr,),
            statistic=_stat,
            n_resamples=n_resamples,
            confidence_level=confidence_level,
            method=method,
            random_state=rng,
        )
        ci_low = float(result.confidence_interval.low)
        ci_high = float(result.confidence_interval.high)
    except Exception:
        # BCa can raise on degenerate samples (zero variance after
        # jackknife). Fall back to the simple percentile method.
        try:
            chosen_method = "percentile"
            result = scipy_stats.bootstrap(
                (arr,),
                statistic=_stat,
                n_resamples=n_resamples,
                confidence_level=confidence_level,
                method="percentile",
                random_state=(None if seed is None else seed),
            )
            ci_low = float(result.confidence_interval.low)
            ci_high = float(result.confidence_interval.high)
        except Exception:
            return {
                "percentile": point,
                "ci_low": None,
                "ci_high": None,
                "method": "failed",
                "n_resamples": 0,
                "n_baseline_windows": len(sample),
            }
    # Clamp degenerate edge cases: percentile is a probability and CI
    # bounds should respect the [0, 1] interval.
    ci_low = max(0.0, min(1.0, ci_low))
    ci_high = max(0.0, min(1.0, ci_high))
    return {
        "percentile": point,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "method": chosen_method,
        "n_resamples": n_resamples,
        "n_baseline_windows": len(sample),
    }


def length_matched_bootstrap(
    baseline_texts: Sequence[str],
    statistic_fn: Callable[[str], float | None],
    target_value: float | None,
    target_n_words: int,
    *,
    n_windows_per_file: int = 50,
    max_total_windows: int = 500,
    n_resamples: int = 9999,
    confidence_level: float = 0.95,
    seed: int | None = None,
) -> dict[str, Any]:
    """End-to-end length-matched bootstrap for one statistic.

    Builds the empirical baseline distribution at the target's length,
    summarizes its quantiles, and reports the target's percentile in it
    with a bootstrap CI. Returns a dict suitable for inclusion in the
    variance audit's JSON output.
    """
    if target_value is None or target_n_words <= 0:
        return {
            "target_value": target_value,
            "target_n_words": target_n_words,
            "available": False,
            "reason": "target value or length missing",
        }
    sample = collect_window_statistic(
        baseline_texts,
        statistic_fn,
        target_n_words,
        n_windows_per_file=n_windows_per_file,
        max_total_windows=max_total_windows,
        seed=seed,
    )
    if not sample:
        return {
            "target_value": float(target_value),
            "target_n_words": target_n_words,
            "available": False,
            "reason": "no baseline windows produced a value",
        }
    summary = summarize_distribution(sample)
    boot = bootstrap_percentile(
        sample,
        float(target_value),
        n_resamples=n_resamples,
        confidence_level=confidence_level,
        seed=seed,
    )
    return {
        "target_value": float(target_value),
        "target_n_words": target_n_words,
        "available": True,
        "baseline_distribution": summary,
        "bootstrap": boot,
    }
