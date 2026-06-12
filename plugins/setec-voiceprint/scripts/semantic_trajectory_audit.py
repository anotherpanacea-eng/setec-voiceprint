#!/usr/bin/env python3
"""semantic_trajectory_audit.py — paired-release Release 12.

Measures how the *meaning* of a prose draft moves across its
length: paragraph by paragraph (or sentence by sentence, or
fixed-token-window by fixed-token-window), embed each window with a
sentence-transformers model and compute the trajectory of pairwise
cosine similarities. The framework's prior cohesion signal
(`tier3.adjacent_cosine` inside `variance_audit.py`) measures the
same shape at sentence-level, single-purpose, for smoothing
diagnosis. This script extends that observation to the
voice-coherence surface, with paragraph-level windowing as the
default, more trajectory statistics, and an optional baseline-
comparison mode.

Why a separate tool: the question voice-coherence asks
("how does the *thread* of a writer's meaning move across a draft?")
is different from the question smoothing-diagnosis asks ("how
tightly do adjacent sentences cling?"). The same math
(SBERT cosine over consecutive units) answers both questions at
different scales, but the licensure rules differ. Smoothing
diagnosis is `revision_only` posture; semantic trajectory is
`voice_coherence` posture. Routing through one task-surface tag
keeps consumers honest.

Trajectory statistics computed for every run:

  * **adjacent_cosines** — series of cosines between consecutive
    windows. Mean = trajectory tightness; sd = trajectory burstiness.
  * **drift** — first-to-last cosine and a per-window linear
    regression of cosine over position. Negative slope = monotonic
    semantic drift; flat slope = consistent return to the same
    territory.
  * **autocorrelation** — lag 1 / 2 / 3 / 5 of the adjacent-cosine
    series. High autocorrelation = the writer's trajectory has
    momentum (each step's similarity carries over); low = each
    transition is independent.
  * **flatness** — counts of windows whose adjacent cosine exceeds
    0.85 / 0.9 / 0.95, plus the longest consecutive run above 0.9.
    The flatness vector is the load-bearing signal for "this prose
    has been smoothed into a single semantic register."

The script ships PROVISIONAL thresholds under the "Stylometry to
the people" policy (see `scripts/calibration/PROVENANCE.md`).
Numbers in the output are absolute measurements; banding into
"flat / typical / drifting" is illustrative only, and the
claim-license block names that explicitly. Users wanting anchored
thresholds run the §6.4 fixture suite against their own baseline.

Usage::

    # Single-text trajectory:
    python3 scripts/semantic_trajectory_audit.py path/to/draft.txt

    # With explicit embedding model + JSON output:
    python3 scripts/semantic_trajectory_audit.py path/to/draft.txt \\
        --model gemma --json --out trajectory.json

    # Sentence-level windowing instead of paragraphs:
    python3 scripts/semantic_trajectory_audit.py path/to/draft.txt \\
        --window-strategy sentence

    # Fixed-token windows:
    python3 scripts/semantic_trajectory_audit.py path/to/draft.txt \\
        --window-strategy fixed-token --window-size 200

    # Baseline comparison (loads a prior run's JSON):
    python3 scripts/semantic_trajectory_audit.py path/to/draft.txt \\
        --baseline path/to/baseline_trajectory.json

task_surface: voice_coherence. Refuses authorship verdicts;
explicitly refuses any "this prose is AI-generated" claim based on
trajectory shape alone. Reports what the math measures, with
PROVISIONAL banding the user can ignore.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore
from embedding_backend import (  # type: ignore
    DEFAULT_MODEL,
    EmbeddingBackend,
    EmbeddingBackendError,
    MODEL_ALIASES,
    resolve_model_arg,
)

TASK_SURFACE = "voice_coherence"
TOOL_NAME = "semantic_trajectory_audit"
SCRIPT_VERSION = "1.0"

# Reasonable defaults for paragraph-level windowing. A paragraph
# shorter than MIN_PARA_TOKENS is glued onto its neighbor (avoids
# noisy embeddings from single-line dialogue tags or chapter
# headings). MAX_PARA_TOKENS caps a long paragraph by splitting at
# sentence boundaries so window-to-window embedding-mass stays
# comparable.
MIN_PARA_TOKENS = 25
MAX_PARA_TOKENS = 600


# --------------- Windowing strategies -----------------------------


def _approx_token_count(text: str) -> int:
    """Whitespace-token approximation. Embeddings are token-level
    but the script doesn't need exact tokenizer counts — paragraph
    sizing thresholds are heuristic, not load-bearing. Avoids a
    hard tokenizer dependency on the windowing path."""
    return len(text.split())


def _split_paragraphs(text: str) -> list[str]:
    """Split on blank lines, then coalesce short paragraphs into
    their neighbors and split overly long ones at sentence
    boundaries."""
    raw = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not raw:
        return []
    # Coalesce short paragraphs forward.
    coalesced: list[str] = []
    buf = ""
    for p in raw:
        candidate = (buf + "\n\n" + p).strip() if buf else p
        if _approx_token_count(candidate) < MIN_PARA_TOKENS:
            buf = candidate
            continue
        coalesced.append(candidate)
        buf = ""
    if buf:
        if coalesced:
            coalesced[-1] = (coalesced[-1] + "\n\n" + buf).strip()
        else:
            coalesced.append(buf)
    # Split overly long paragraphs at sentence boundaries.
    final: list[str] = []
    for p in coalesced:
        if _approx_token_count(p) <= MAX_PARA_TOKENS:
            final.append(p)
            continue
        # Sentence-split and accumulate up to MAX_PARA_TOKENS.
        sents = re.split(r"(?<=[.!?])\s+", p)
        cur = ""
        for s in sents:
            if not s.strip():
                continue
            candidate = (cur + " " + s).strip() if cur else s
            if _approx_token_count(candidate) > MAX_PARA_TOKENS and cur:
                final.append(cur)
                cur = s
            else:
                cur = candidate
        if cur:
            final.append(cur)
    return final


def _split_sentences(text: str) -> list[str]:
    """Best-effort sentence splitter. Mirrors `variance_audit.split_
    sentences` if available, falls back to a regex split otherwise."""
    try:
        from variance_audit import split_sentences as _vs  # type: ignore
        return _vs(text)
    except ImportError:
        return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _split_fixed_token(text: str, window_size: int) -> list[str]:
    """Naive whitespace-token window. Not tokenizer-aware; suitable
    when the user wants a uniform window size for trajectory math
    and is willing to break across sentence boundaries.
    """
    tokens = text.split()
    if not tokens:
        return []
    windows = []
    for i in range(0, len(tokens), window_size):
        chunk = tokens[i:i + window_size]
        if chunk:
            windows.append(" ".join(chunk))
    return windows


def split_windows(
    text: str, strategy: str, *, window_size: int = 200,
) -> list[str]:
    """Public windowing entry point.

    Strategies:
      * ``paragraph`` — split on blank lines, coalesce short, split
        long. Best default for prose drafts where paragraph
        structure is meaningful.
      * ``sentence`` — one window per sentence. Matches the existing
        `tier3.adjacent_cosine` signal; useful for comparing R12
        output to variance_audit's smoothing-diagnosis output.
      * ``fixed-token`` — uniform N-token windows ignoring prose
        structure. Useful for register-invariant trajectory
        comparison.
    """
    if strategy == "paragraph":
        return _split_paragraphs(text)
    if strategy == "sentence":
        return _split_sentences(text)
    if strategy == "fixed-token":
        return _split_fixed_token(text, window_size)
    raise ValueError(
        f"Unknown window strategy {strategy!r}; "
        f"expected 'paragraph', 'sentence', or 'fixed-token'."
    )


# --------------- Cosine + trajectory stats -----------------------


def _cosine(a: Any, b: Any) -> float:
    """Plain cosine similarity over two numpy vectors. Returns 0.0
    when either vector is the zero vector (avoids division-by-zero
    blow-ups when an encoder happens to emit a degenerate
    embedding). Clamped to [-1, 1] at the source: float-epsilon in
    ``np.dot/(‖a‖‖b‖)`` can otherwise emit e.g. 1.0000000002, which
    would ship as a spurious out-of-range value downstream."""
    import numpy as np  # type: ignore
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return max(-1.0, min(1.0, float(np.dot(a, b) / (na * nb))))


def adjacent_cosine_series(embeddings: Any) -> list[float]:
    """Cosines between window i and window i+1, in order. Returns
    an empty list when there's nothing to compare (fewer than two
    windows). The series length is always ``len(embeddings) - 1``
    when at least two windows are present."""
    n = len(embeddings) if embeddings is not None else 0
    if n < 2:
        return []
    return [_cosine(embeddings[i], embeddings[i + 1]) for i in range(n - 1)]


def _linear_regression_slope(
    xs: list[float], ys: list[float],
) -> dict[str, float]:
    """Simple OLS slope + intercept + R-squared. Inlined rather
    than pulling scipy: the regression is on at most a few hundred
    windows and the math is small."""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return {"slope": 0.0, "intercept": 0.0, "r_squared": 0.0}
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0.0:
        return {"slope": 0.0, "intercept": mean_y, "r_squared": 0.0}
    slope = num / den
    intercept = mean_y - slope * mean_x
    # R-squared.
    ss_res = sum(
        (ys[i] - (slope * xs[i] + intercept)) ** 2 for i in range(n)
    )
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    return {
        "slope": slope,
        "intercept": intercept,
        "r_squared": r_squared,
    }


def autocorrelation(series: list[float], lag: int) -> float:
    """Sample autocorrelation at integer lag. Returns 0.0 when the
    series is too short or constant (no meaningful correlation to
    report)."""
    n = len(series)
    if n <= lag or lag < 1:
        return 0.0
    mean = sum(series) / n
    var = sum((s - mean) ** 2 for s in series) / n
    if var == 0.0:
        return 0.0
    cov = sum(
        (series[i] - mean) * (series[i + lag] - mean)
        for i in range(n - lag)
    ) / n
    return cov / var


def flatness_summary(
    series: list[float], *, thresholds: tuple[float, ...] = (0.85, 0.9, 0.95),
) -> dict[str, Any]:
    """Count adjacent-cosines above various thresholds plus the
    longest consecutive run above the middle threshold. The "longest
    run" is the most diagnostically useful single number for mode-
    collapse: a draft where 10/40 adjacent cosines exceed 0.9 is
    different from a draft where 10 in a row exceed 0.9.
    """
    if not series:
        return {
            "thresholds": list(thresholds),
            "counts_above": {f"{t:.2f}": 0 for t in thresholds},
            "fraction_above": {f"{t:.2f}": 0.0 for t in thresholds},
            "longest_run_above_0.9": 0,
        }
    counts = {f"{t:.2f}": sum(1 for s in series if s >= t) for t in thresholds}
    fracs = {f"{t:.2f}": counts[f"{t:.2f}"] / len(series) for t in thresholds}
    # Longest run above 0.9 specifically.
    longest = current = 0
    for s in series:
        if s >= 0.9:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return {
        "thresholds": list(thresholds),
        "counts_above": counts,
        "fraction_above": fracs,
        "longest_run_above_0.9": longest,
    }


def compute_trajectory(
    embeddings: Any, window_token_counts: list[int],
) -> dict[str, Any]:
    """Compute the full trajectory block from embeddings + window
    sizes. Pure function, no I/O — used by both the CLI path and
    the baseline-comparison path."""
    series = adjacent_cosine_series(embeddings)
    n_pairs = len(series)
    if n_pairs == 0:
        return {
            "n_windows": len(window_token_counts),
            "adjacent_cosines": {
                "n_pairs": 0,
                "values": [],
                "mean": None, "sd": None, "min": None, "max": None,
            },
            "drift": {
                "first_to_last_cosine": None,
                "regression": {"slope": 0.0, "intercept": 0.0, "r_squared": 0.0},
            },
            "autocorrelation": {f"lag_{k}": 0.0 for k in (1, 2, 3, 5)},
            "flatness": flatness_summary([]),
            "window_token_stats": _window_token_stats(window_token_counts),
        }
    mean = statistics.mean(series)
    sd = statistics.stdev(series) if n_pairs >= 2 else 0.0
    # First-to-last cosine compares the very ends — diagnostic for
    # "how far does the draft travel semantically end-to-end."
    f2l = None
    if embeddings is not None and len(embeddings) >= 2:
        f2l = _cosine(embeddings[0], embeddings[-1])
    xs = [float(i) for i in range(n_pairs)]
    reg = _linear_regression_slope(xs, series)
    autocorr = {f"lag_{k}": autocorrelation(series, k) for k in (1, 2, 3, 5)}
    return {
        "n_windows": len(window_token_counts),
        "adjacent_cosines": {
            "n_pairs": n_pairs,
            "values": series,
            "mean": mean,
            "sd": sd,
            "min": min(series),
            "max": max(series),
        },
        "drift": {
            "first_to_last_cosine": f2l,
            "regression": reg,
        },
        "autocorrelation": autocorr,
        "flatness": flatness_summary(series),
        "window_token_stats": _window_token_stats(window_token_counts),
    }


def _window_token_stats(counts: list[int]) -> dict[str, Any]:
    if not counts:
        return {"n": 0, "mean": None, "min": None, "max": None}
    return {
        "n": len(counts),
        "mean": sum(counts) / len(counts),
        "min": min(counts),
        "max": max(counts),
    }


# --------------- PROVISIONAL banding -----------------------------


# Illustrative bands. These are NOT calibrated against any labeled
# corpus and are surfaced only so a reader can place a number in a
# rough range. The claim-license block names this explicitly. Users
# wanting load-bearing thresholds run the §6.4 fixture suite against
# their own baseline.
PROVISIONAL_BANDS = {
    "mean_adjacent_cosine": {
        "very_tight_lt": 0.95,  # rare in natural prose; mode-collapse candidate
        "tight_lt": 0.9,        # tight cohesion; can be earned in dense argument
        "typical_lt": 0.85,     # most prose drafts
        # below 0.85 = drifting / fragmented
    },
    "longest_high_run_alert_ge": 5,  # 5+ paragraphs above 0.9 = inspect
    "drift_slope_abs_alert_ge": 0.01,  # non-trivial trajectory drift
}


def provisional_banding(trajectory: dict[str, Any]) -> dict[str, Any]:
    """Place trajectory numbers into rough bands. Strictly
    illustrative; the claim-license block stamps these as
    user-baseline-required."""
    adj = trajectory.get("adjacent_cosines") or {}
    mean = adj.get("mean")
    flatness = trajectory.get("flatness") or {}
    longest = flatness.get("longest_run_above_0.9", 0)
    drift = trajectory.get("drift") or {}
    slope = (drift.get("regression") or {}).get("slope", 0.0)

    band = "insufficient_data"
    if mean is not None:
        if mean >= PROVISIONAL_BANDS["mean_adjacent_cosine"]["very_tight_lt"]:
            band = "very_tight"
        elif mean >= PROVISIONAL_BANDS["mean_adjacent_cosine"]["tight_lt"]:
            band = "tight"
        elif mean >= PROVISIONAL_BANDS["mean_adjacent_cosine"]["typical_lt"]:
            band = "typical"
        else:
            band = "drifting"

    alerts: list[str] = []
    if longest >= PROVISIONAL_BANDS["longest_high_run_alert_ge"]:
        alerts.append(
            f"longest run of paragraphs with adjacent cosine ≥ 0.9 "
            f"is {longest} windows; this can be earned content but "
            f"is a flatness candidate worth a reader pass."
        )
    if abs(slope) >= PROVISIONAL_BANDS["drift_slope_abs_alert_ge"]:
        direction = "drifting (later windows less similar to earlier)" if slope < 0 else "converging (later windows more similar to earlier)"
        alerts.append(
            f"adjacent-cosine drift slope {slope:+.4f} per window — "
            f"{direction}. Earned in transitional prose; suspicious "
            f"in stable-register drafts."
        )

    return {
        "band": band,
        "alerts": alerts,
        "bands_definition": PROVISIONAL_BANDS,
        "calibration_anchor": "user-baseline-required",
        "provisional": True,
    }


# --------------- Baseline comparison -----------------------------


def compare_to_baseline(
    current: dict[str, Any], baseline_path: Path,
) -> dict[str, Any]:
    """Read a prior trajectory JSON and report side-by-side numbers.

    Comparison is descriptive, not inferential: we don't run a
    statistical test (KS, Mann-Whitney) on the adjacent-cosine
    series. The user reads the deltas and decides. A future PR can
    add the test layer behind a `--statistical-comparison` flag if
    deltas alone prove insufficient.
    """
    try:
        with baseline_path.open("r", encoding="utf-8") as fh:
            baseline = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "error": f"could not read baseline {baseline_path}: {exc}",
        }
    b_traj = baseline.get("trajectory") or {}
    c_traj = current.get("trajectory") or {}

    def _safe_get(d: dict[str, Any], path: tuple[str, ...]) -> Any:
        cur: Any = d
        for k in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(k)
        return cur

    def _delta(c: Any, b: Any) -> Any:
        if c is None or b is None:
            return None
        try:
            return float(c) - float(b)
        except (TypeError, ValueError):
            return None

    fields = [
        ("mean_adjacent_cosine", ("adjacent_cosines", "mean")),
        ("sd_adjacent_cosine", ("adjacent_cosines", "sd")),
        ("first_to_last_cosine", ("drift", "first_to_last_cosine")),
        ("drift_slope", ("drift", "regression", "slope")),
        ("autocorr_lag_1", ("autocorrelation", "lag_1")),
        ("longest_run_above_0.9", ("flatness", "longest_run_above_0.9")),
    ]
    rows = []
    for label, path in fields:
        c_val = _safe_get(c_traj, path)
        b_val = _safe_get(b_traj, path)
        rows.append({
            "field": label,
            "current": c_val,
            "baseline": b_val,
            "delta": _delta(c_val, b_val),
        })
    return {
        "baseline_path": str(baseline_path),
        "baseline_model_id": (baseline.get("model") or {}).get("id"),
        "baseline_window_strategy": (
            baseline.get("windowing") or {}
        ).get("strategy"),
        "deltas": rows,
        "note": (
            "Descriptive deltas only. The framework does not assert "
            "any delta as significant; users should treat this as a "
            "side-by-side numerical comparison and interpret the "
            "magnitude in the context of their own baseline distribution."
        ),
    }


# --------------- Output assembly ---------------------------------


def _claim_license() -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Reports paragraph-level semantic-trajectory statistics "
            "(adjacent-cosine mean, variance, drift, autocorrelation, "
            "flatness). Surfaces flatness candidates a reader pass "
            "can audit."
        ),
        does_not_license=(
            "Authorship verdicts. Cross-register generalization. "
            "Threshold-based 'this is AI prose' classification. "
            "Quality judgments about the prose itself."
        ),
        comparison_set={
            "anchor": "PROVISIONAL bands derived from author-baseline "
                      "heuristics; not calibrated against any labeled "
                      "corpus.",
            "calibration_status": "user-baseline-required",
        },
        additional_caveats=[
            "Embedding-derived statistics depend on the model. Bands "
            "in this output assume mxbai-embed-large-v1; running with "
            "a different model produces numbers that should not be "
            "directly compared against these bands.",
            "Sentence-level windowing produces tighter cosines on "
            "average than paragraph-level windowing. Compare like to "
            "like.",
            "Long high-cosine runs can be earned content (dense "
            "argument, repetitive lyric mode, stable-register essay) "
            "OR flattened content (LLM smoothing, mode collapse). "
            "The audit reports the shape; the reader decides which "
            "story explains it.",
        ],
        references=[
            "internal/SPEC_embedding_model_choice.md (gitignored) — "
            "co-primary candidates and §6.4 fixture-test protocol.",
            "scripts/calibration/PROVENANCE.md — Stylometry-to-the-"
            "people policy on why this audit does not ship anchored "
            "thresholds.",
        ],
    )


def _claim_license_block() -> dict[str, Any]:
    """Legacy alias preserved for any internal caller; returns the
    structured to_dict() shape.
    """
    return _claim_license().to_dict()


def build_audit_payload(audit: dict[str, Any]) -> dict[str, Any]:
    """Wrap assemble_output's return in the schema_version 1.0
    envelope per ``internal/SPEC_output_schema_unification.md``. The
    legacy `tool_version` field renames to `version`; per-script
    payload lives under results.
    """
    available = audit.get("trajectory") is not None
    windowing = audit.get("windowing") or {}
    target_words = 0
    token_stats = windowing.get("window_token_stats") or {}
    if isinstance(token_stats, dict) and token_stats.get("n"):
        # Approximate target_words as n_windows × mean tokens-per-window.
        mean_tokens = token_stats.get("mean") or 0
        target_words = int(windowing.get("n_windows", 0) * mean_tokens)

    target_path = audit.get("source")
    target_extra: dict[str, Any] = {}
    if windowing:
        target_extra["windowing"] = windowing

    warnings: list[str] = []
    if "warning" in audit:
        warnings.append(audit["warning"])

    results: dict[str, Any] = {}
    if audit.get("model") is not None:
        results["model"] = audit["model"]
    if audit.get("trajectory") is not None:
        results["trajectory"] = audit["trajectory"]
    if audit.get("provisional_banding") is not None:
        results["provisional_banding"] = audit["provisional_banding"]
    if audit.get("baseline_comparison") is not None:
        results["baseline_comparison"] = audit["baseline_comparison"]

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=None,
        results=results,
        claim_license=_claim_license() if available else None,
        available=available,
        warnings=warnings,
        target_extra=target_extra or None,
    )


def assemble_output(
    text: str,
    *,
    backend: EmbeddingBackend,
    window_strategy: str,
    window_size: int,
    baseline_path: Path | None,
    source_path: Path | None,
) -> dict[str, Any]:
    """Build the full JSON output for one audit run."""
    windows = split_windows(text, window_strategy, window_size=window_size)
    token_counts = [_approx_token_count(w) for w in windows]
    if len(windows) < 2:
        # Honest-failure path: not enough material to compute a
        # trajectory. We still emit a well-formed result so
        # downstream consumers (orchestrators, validation harnesses)
        # parse it cleanly.
        return {
            "task_surface": TASK_SURFACE,
            "tool": TOOL_NAME,
            "tool_version": SCRIPT_VERSION,
            "warning": (
                f"only {len(windows)} window(s) produced; need at least "
                f"2 to compute a trajectory. Try a finer window strategy "
                f"or a longer text."
            ),
            "source": str(source_path) if source_path else None,
            "model": backend.identifier_block(),
            "windowing": {
                "strategy": window_strategy,
                "window_size": (
                    window_size if window_strategy == "fixed-token" else None
                ),
                "n_windows": len(windows),
                "window_token_stats": _window_token_stats(token_counts),
            },
            "trajectory": None,
            "provisional_banding": None,
            "baseline_comparison": None,
            "claim_license": _claim_license_block(),
        }
    embeddings = backend.encode(windows)
    trajectory = compute_trajectory(embeddings, token_counts)
    banding = provisional_banding(trajectory)
    baseline_block = (
        compare_to_baseline(
            {"trajectory": trajectory}, baseline_path,
        )
        if baseline_path is not None
        else None
    )
    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "tool_version": SCRIPT_VERSION,
        "source": str(source_path) if source_path else None,
        "model": backend.identifier_block(),
        "windowing": {
            "strategy": window_strategy,
            "window_size": (
                window_size if window_strategy == "fixed-token" else None
            ),
            "n_windows": len(windows),
            "window_token_stats": _window_token_stats(token_counts),
        },
        "trajectory": trajectory,
        "provisional_banding": banding,
        "baseline_comparison": baseline_block,
        "claim_license": _claim_license_block(),
    }


# --------------- Markdown rendering ------------------------------


def _render_claim_license_section(cl: dict[str, Any]) -> list[str]:
    """Format the ``claim_license`` dict as a markdown section.

    Mirrors the structure ``claim_license.ClaimLicense.render_block()``
    produces for the other surfacing harnesses (validation_harness,
    voice_validation_harness, general_imposters, sliding_window_
    heatmap). Keeping the same shape across surfaces lets readers
    parse the licensure block uniformly regardless of which audit
    produced the report.

    Empty / null fields are skipped rather than rendered as
    "not applicable" — the goal is a section the reader can scan
    quickly, not a complete schema dump.
    """
    if not cl:
        return []
    lines: list[str] = []
    lines.append("## Claim license")
    lines.append("")
    lines.append(
        "> The framework refuses claims the evidence does not "
        "license. The block below names what this audit's result "
        "entitles and what it does not."
    )
    lines.append("")
    if cl.get("licenses"):
        lines.append("**Licenses:**")
        lines.append("")
        lines.append(cl["licenses"])
        lines.append("")
    if cl.get("does_not_license"):
        lines.append("**Does NOT license:**")
        lines.append("")
        lines.append(cl["does_not_license"])
        lines.append("")
    cs = cl.get("comparison_set") or {}
    if cs:
        lines.append("**Comparison set:**")
        lines.append("")
        for k, v in cs.items():
            lines.append(f"- **{k}:** {v}")
        lines.append("")
    caveats = cl.get("additional_caveats") or []
    if caveats:
        lines.append("**Additional caveats:**")
        lines.append("")
        for c in caveats:
            lines.append(f"- {c}")
        lines.append("")
    refs = cl.get("references") or []
    if refs:
        lines.append("**References:**")
        lines.append("")
        for r in refs:
            lines.append(f"- {r}")
        lines.append("")
    return lines


def render_markdown(payload: dict[str, Any]) -> str:
    """Render the JSON payload as a human-readable markdown report.

    Pattern matches the rest of the framework: numerical sections
    filled in programmatically, interpretation marked with
    ``{TODO: interpret}`` for the LLM/human pass that follows the
    audit. The author-facing voice-insights report convention.

    The ``claim_license`` block is rendered at the end of the report
    on both the normal path and the warning-short-circuit path. Since
    R12 ships under the Stylometry-to-the-people policy with
    explicit no-anchored-thresholds, the claim-license is the
    load-bearing licensure surface and must appear in every report,
    not just the JSON sidecar.
    """
    lines: list[str] = []
    lines.append("# Semantic trajectory audit")
    lines.append("")
    lines.append(
        f"**Task surface:** `{payload['task_surface']}` &nbsp;|&nbsp; "
        f"**Tool:** `{payload['tool']}` v{payload['tool_version']}"
    )
    if payload.get("source"):
        lines.append(f"**Source:** `{payload['source']}`")
    lines.append("")
    if payload.get("warning"):
        lines.append(f"> **Warning:** {payload['warning']}")
        lines.append("")
        # Even on the warning path the claim_license block is
        # load-bearing — the reader needs to know what the (absent)
        # result does NOT license, especially when the audit produced
        # too few windows to compute trajectory stats.
        lines.extend(_render_claim_license_section(
            payload.get("claim_license") or {}
        ))
        return "\n".join(lines)
    # Model
    m = payload.get("model") or {}
    lines.append("## Embedding model")
    lines.append("")
    lines.append(f"- **id:** `{m.get('id')}`")
    if m.get("alias"):
        lines.append(f"- **alias:** `{m.get('alias')}`")
    lines.append(f"- **revision:** `{m.get('revision') or 'unpinned'}`")
    lines.append(f"- **method:** {m.get('method')}")
    lines.append(f"- **deterministic mode:** {m.get('deterministic_mode')}")
    lines.append("")
    # Windowing
    w = payload.get("windowing") or {}
    lines.append("## Windowing")
    lines.append("")
    lines.append(f"- **strategy:** {w.get('strategy')}")
    if w.get("window_size"):
        lines.append(f"- **window size:** {w.get('window_size')} tokens")
    lines.append(f"- **n_windows:** {w.get('n_windows')}")
    wts = w.get("window_token_stats") or {}
    if wts.get("n"):
        lines.append(
            f"- **window token stats:** "
            f"mean {wts.get('mean'):.1f}, "
            f"min {wts.get('min')}, max {wts.get('max')}"
        )
    lines.append("")
    # Trajectory
    t = payload.get("trajectory") or {}
    adj = t.get("adjacent_cosines") or {}
    drift = t.get("drift") or {}
    reg = drift.get("regression") or {}
    autocorr = t.get("autocorrelation") or {}
    flat = t.get("flatness") or {}
    lines.append("## Trajectory statistics")
    lines.append("")
    lines.append("### Adjacent cosines")
    lines.append("")
    lines.append(f"- **n_pairs:** {adj.get('n_pairs')}")
    if adj.get("mean") is not None:
        lines.append(f"- **mean:** {adj['mean']:.4f}")
        lines.append(f"- **sd:** {adj['sd']:.4f}")
        lines.append(f"- **min:** {adj['min']:.4f}")
        lines.append(f"- **max:** {adj['max']:.4f}")
    lines.append("")
    lines.append("### Drift")
    lines.append("")
    f2l = drift.get("first_to_last_cosine")
    if f2l is not None:
        lines.append(f"- **first-to-last cosine:** {f2l:.4f}")
    lines.append(
        f"- **slope (per window):** {reg.get('slope', 0.0):+.6f}"
    )
    lines.append(
        f"- **slope R²:** {reg.get('r_squared', 0.0):.4f}"
    )
    lines.append("")
    lines.append("### Autocorrelation")
    lines.append("")
    for k in (1, 2, 3, 5):
        v = autocorr.get(f"lag_{k}", 0.0)
        lines.append(f"- **lag {k}:** {v:+.4f}")
    lines.append("")
    lines.append("### Flatness")
    lines.append("")
    counts = flat.get("counts_above") or {}
    fracs = flat.get("fraction_above") or {}
    for thr in flat.get("thresholds", []):
        key = f"{thr:.2f}"
        c = counts.get(key, 0)
        f = fracs.get(key, 0.0)
        lines.append(f"- **windows ≥ {key}:** {c} ({f * 100:.1f}%)")
    lines.append(
        f"- **longest run ≥ 0.9:** {flat.get('longest_run_above_0.9', 0)}"
    )
    lines.append("")
    # PROVISIONAL banding
    band = payload.get("provisional_banding") or {}
    lines.append("## PROVISIONAL banding")
    lines.append("")
    lines.append("> **Note.** Bands are illustrative; the framework does NOT")
    lines.append("> ship anchored thresholds for this audit. Run the §6.4")
    lines.append("> fixture suite against your own baseline to anchor them")
    lines.append("> to your register mix.")
    lines.append("")
    lines.append(f"- **mean-adjacent band:** `{band.get('band')}`")
    if band.get("alerts"):
        lines.append("- **alerts:**")
        for a in band["alerts"]:
            lines.append(f"  - {a}")
    lines.append("")
    # Baseline comparison
    bc = payload.get("baseline_comparison")
    if bc:
        lines.append("## Baseline comparison")
        lines.append("")
        if bc.get("error"):
            lines.append(f"> **Error:** {bc['error']}")
        else:
            lines.append(f"- **baseline path:** `{bc.get('baseline_path')}`")
            lines.append(
                f"- **baseline model:** `{bc.get('baseline_model_id')}`"
            )
            lines.append(
                f"- **baseline windowing:** "
                f"{bc.get('baseline_window_strategy')}"
            )
            lines.append("")
            lines.append("| Field | Current | Baseline | Δ |")
            lines.append("|---|---:|---:|---:|")
            for r in bc.get("deltas") or []:
                cur = r["current"]
                bsl = r["baseline"]
                dlt = r["delta"]
                cur_str = f"{cur:.4f}" if isinstance(cur, float) else str(cur)
                bsl_str = f"{bsl:.4f}" if isinstance(bsl, float) else str(bsl)
                dlt_str = (
                    f"{dlt:+.4f}" if isinstance(dlt, float) else "—"
                )
                lines.append(
                    f"| `{r['field']}` | {cur_str} | {bsl_str} | {dlt_str} |"
                )
            lines.append("")
            lines.append(f"> {bc.get('note')}")
        lines.append("")
    # Interpretation TODO
    lines.append("## Interpretation")
    lines.append("")
    lines.append(
        "{TODO: interpret} — Place these numbers in the context of "
        "the writer's register, the draft's purpose, and any "
        "available baseline. The audit reports the math; the "
        "interpretation is the writer's or editor's pass."
    )
    lines.append("")
    # Claim license block at the end of the report. Same content as
    # the JSON sidecar's `claim_license` field, formatted for human
    # reading. Load-bearing under the Stylometry-to-the-people policy.
    lines.extend(_render_claim_license_section(
        payload.get("claim_license") or {}
    ))
    return "\n".join(lines)


# --------------- CLI ---------------------------------------------


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="semantic_trajectory_audit",
        description=(
            "Paragraph-level semantic trajectory audit for prose. "
            "Computes adjacent-cosine, drift, autocorrelation, and "
            "flatness statistics over an embedding trajectory. Ships "
            "PROVISIONAL banding under SETEC's Stylometry-to-the-"
            "people policy; no anchored thresholds."
        ),
    )
    p.add_argument(
        "source",
        type=str,
        help="path to a UTF-8 text file to audit",
    )
    p.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=(
            "embedding model. Either an alias "
            f"({', '.join(sorted(MODEL_ALIASES))}) or a full "
            "HuggingFace identifier. Default: %(default)s."
        ),
    )
    p.add_argument(
        "--revision",
        type=str,
        default=None,
        help=(
            "pin a specific HuggingFace commit SHA for reproducibility. "
            "Unpinned runs surface the missing pin in PROVENANCE."
        ),
    )
    p.add_argument(
        "--dtype",
        choices=("auto", "fp32", "fp16", "bf16"),
        default="auto",
        help=(
            "Precision for embedding-model inference. ``auto`` picks "
            "bf16 on supporting cuda (Ampere+ / Hopper / Ada), fp16 "
            "on older cuda, fp32 on CPU / MPS. Added 1.96.0."
        ),
    )
    p.add_argument(
        "--device",
        default=None,
        help=(
            "Explicit device for the embedding model (e.g., "
            "``cuda:1``). Default: defer to sentence-transformers' "
            "auto-device pick."
        ),
    )
    p.add_argument(
        "--window-strategy",
        type=str,
        choices=("paragraph", "sentence", "fixed-token"),
        default="paragraph",
        help="how to split the source text into windows (default: %(default)s)",
    )
    p.add_argument(
        "--window-size",
        type=int,
        default=200,
        help=(
            "token count for --window-strategy fixed-token "
            "(ignored otherwise; default: %(default)s)"
        ),
    )
    p.add_argument(
        "--baseline",
        type=str,
        default=None,
        help=(
            "path to a prior run's JSON output to compare against. "
            "Comparison is descriptive (side-by-side numbers + "
            "deltas), not inferential."
        ),
    )
    p.add_argument(
        "--out",
        type=str,
        default=None,
        help="write JSON output to this path (defaults to stdout)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of markdown",
    )
    p.add_argument(
        "--markdown-out",
        type=str,
        default=None,
        help="write the markdown report to this path (in addition to JSON)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    source = Path(args.source).expanduser()
    if not source.exists():
        sys.stderr.write(f"Source path not found: {source}\n")
        return 2
    text = _read_text(source)
    backend = EmbeddingBackend(
        model_id=resolve_model_arg(args.model),
        revision=args.revision,
        dtype=getattr(args, "dtype", "auto"),
        device=getattr(args, "device", None),
    )
    baseline_path = (
        Path(args.baseline).expanduser() if args.baseline else None
    )
    try:
        payload = assemble_output(
            text,
            backend=backend,
            window_strategy=args.window_strategy,
            window_size=args.window_size,
            baseline_path=baseline_path,
            source_path=source,
        )
    except EmbeddingBackendError as exc:
        sys.stderr.write(f"Embedding backend error: {exc}\n")
        return 3
    envelope = build_audit_payload(payload)
    rendered_json = json.dumps(envelope, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).expanduser().write_text(rendered_json + "\n", encoding="utf-8")
    if args.markdown_out:
        Path(args.markdown_out).expanduser().write_text(
            render_markdown(payload), encoding="utf-8",
        )
    if args.json or args.out:
        if not args.out:
            print(rendered_json)
    else:
        print(render_markdown(payload))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
