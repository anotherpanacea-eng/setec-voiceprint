#!/usr/bin/env python3
"""rank_space_audit.py — rank-space surprisal detection signal (spec 32, M1).

DetectLLM **LRR** (log-likelihood / log-rank ratio) and the per-token log-rank
moments (mean / SD / lag-1 ACF) as a near-free DERIVED column off the
log-probability distributions a causal LM already materializes in
``SurprisalBackend.score_text_with_distributions`` — the SAME call
``binoculars_audit.py`` v2 makes for cross-perplexity. There is **no second
forward pass** for LRR: log-rank is an ``argsort`` of each position's vocab
log-prob vector. This DEEPENS the existing Tier-4 surprisal family
(``surprisal_audit`` mean/sd/acf_lag1) on the SAME surface
(``binoculars_discrimination``); it is not a new surface.

The rank math lives in ``rank_space_signals.py`` (pure stdlib, no model). This
script is the registered surface: it resolves the score source, calls the rank
helpers, and emits the canonical ``output_schema`` envelope with a DESCRIPTIVE
band over the value's OWN axis and a claim-license that refuses any verdict.

MODEL SEAM (M1 = injectable, model-free; M2 = lazy backend, GPU-gated)
=====================================================================
``audit_rank_space`` takes an **injectable** ``distributions_fn`` callable
``(text) -> (surprisal_bits, log_probs_nats, token_ids)`` — exactly the return
tuple of ``SurprisalBackend.score_text_with_distributions``. M1 tests inject a
deterministic stub (no model, no torch). When no ``distributions_fn`` is given,
the CLI lazily constructs a ``SurprisalBackend`` and uses its
``score_text_with_distributions`` (the M2 path; loads a model only then). The
**model is constructed lazily** (only inside ``main`` / the M2 seam) — no GPU
work and no weight load happens at import. Note, however, that ``import
rank_space_audit`` is NOT torch-free: the module-top ``from stylometry_core
import word_tokens`` (used for the word count in ``main``) pulls
``stylometry_core`` and, transitively, the Tier-4 surprisal stack and torch into
``sys.modules`` — the same import footprint as the ``tocsin_audit`` sibling. The
genuinely stdlib-clean, torch-free helper is ``rank_space_signals`` (guarded by
``test_import_is_stdlib``); the unit tests run the rank math over INJECTED stub
distributions, so no model is loaded or run in M1 even though torch is
importable.

Paper: Su, Zhuo, Wang, Nakov, "DetectLLM: Leveraging Log Rank Information for
Zero-Shot Detection of Machine-Generated Text" (arXiv:2306.05540, MBZUAI 2023).
The paper's +1.75 / +3.9 AUC lifts (WritingPrompts, Table 3) are a LEAD requiring
an empirical reproduction (M2) before reliance; they are NOT asserted here. The
signal DIRECTION across registers is the empirical question — this surface only
reports the values and a provisional band.

POSTURE (no verdict, NO shipped band)
=====================================
Descriptive only: VALUES (``log_rank_mean`` / ``log_rank_sd`` / ``log_rank_acf1``
/ ``lrr``) and NOTHING else — **no verdict band ships** (spec §3.5 / §9: "ships no
verdict band", "no threshold is shipped without calibration"). The surface emits
the raw scalars with ``band: "uncalibrated"`` and ``thresholds: None`` — the
fast_detect_curvature model — rather than a default categorical leaf from
invented numeric cutoffs. An operator may supply ``--threshold-low`` /
``--threshold-high`` explicitly (the binoculars_audit model); only then does a
band appear, carrying ``calibration_status: heuristic``,
``calibration_anchor: user-baseline-required``, and the
``thresholds_operator_supplied_not_framework_calibrated`` caveat. There is NO
``is_ai`` / ``is_human`` / ``label`` / ``verdict`` / ``decision`` key, and no
framework-shipped numeric cutoff. The known ESL / non-native false-positive
failure mode is surfaced in the claim-license and the assumptions block.

CLI:

    python3 scripts/rank_space_audit.py TARGET [--model ALIAS] \
        [--surprisal-dtype auto|fp32|fp16|bf16] [--json] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore  # noqa: E402
from output_schema import (  # noqa: E402
    build_error_output,
    build_output,
)
from rank_space_signals import (  # type: ignore  # noqa: E402
    aggregate_rank_signals,
    rank_series_from_distributions,
)
from stylometry_core import word_tokens  # type: ignore  # noqa: E402

TASK_SURFACE = "binoculars_discrimination"
TOOL_NAME = "rank_space_audit"
SCRIPT_VERSION = "1.0"

# Length floor (words). Below this the rank-space estimate is unstable (short /
# formulaic text), so the surface WARNS rather than refuses — matching
# tocsin_audit / fast_detect_curvature (the discrimination-family siblings).
LENGTH_FLOOR_WORDS = 50

# Soft length CEILING (words). The rank series requires a single scoring window;
# a target above the scorer's context window (~1024 tokens / ~750 words for gpt2)
# is chunked by the backend, which breaks the single-sequence alignment and is
# REFUSED with reason_category text_too_long (see audit_rank_space). This
# conservative word-based pre-check WARNS the operator before the model runs so
# the ceiling is not a surprise. It is intentionally below ~750 (words tokenize
# to more than one token each) and is advisory: the load-bearing guarantee is the
# exact token-window refusal in audit_rank_space, not this heuristic.
LENGTH_CEILING_WARN_WORDS = 700

# LRR band thresholds DEFAULT TO None — NO framework-calibrated operating point
# ships (spec §3.5 / §9: "ships no verdict band", "no threshold is shipped
# without calibration"). Hard-coding numeric defaults here would emit a default
# categorical band leaf from uncalibrated cutoffs — exactly the
# thresholded-decision-by-default the adversarial posture forbids, and the same
# rule binoculars_audit cites for shipping DEFAULT_THRESHOLD_LOW/HIGH = None.
# An operator who has calibrated their own LRR operating point against a labeled
# corpus disjoint from any development corpus may supply --threshold-low /
# --threshold-high explicitly; only then does a band appear, carrying the
# 'thresholds_operator_supplied_not_framework_calibrated' caveat. The DIRECTION
# (which side is "more LLM-like") is itself the empirical question (M2) and is
# NOT asserted; promotion past 'heuristic' goes only through scripts/calibration/.
DEFAULT_THRESHOLD_LOW: float | None = None
DEFAULT_THRESHOLD_HIGH: float | None = None


class RankSpaceInputError(ValueError):
    """Raised by ``audit_rank_space`` on an unusable input. The CLI maps this to
    a structured ``build_error_output`` envelope, never a traceback."""


class RankSpaceTextTooLongError(RankSpaceInputError):
    """Raised when the target exceeds the scorer's context window so the backend
    chunks it (multi-window), breaking the single-sequence length contract the
    rank series requires (``len(token_ids) == len(log_probs) + 1``). The CLI maps
    this to ``reason_category="text_too_long"`` with an actionable message that
    names the real cause (the scorer context window), NOT a generic ``bad_input``
    that blames the scorer."""


def _band(
    lrr: float | None,
    *,
    n_positions: int,
    threshold_low: float | None = None,
    threshold_high: float | None = None,
) -> dict[str, Any]:
    """Descriptive band over the LRR VALUE's own axis. NEVER over authorship.

    Ships NO framework-calibrated cutoff: with both thresholds ``None`` (the
    default — no operator override) the band is ``"uncalibrated"`` and the
    surface reports only the raw scalars (the fast_detect_curvature model). This
    honours spec §3.5 ("ships no verdict band") / §9 ("no threshold is shipped
    without calibration"): a default categorical leaf from invented cutoffs is
    exactly the thresholded-decision-by-default the posture forbids.

    Only when an operator supplies BOTH ``--threshold-low`` and
    ``--threshold-high`` (their own calibrated operating point) does a band
    appear (``low_lrr`` / ``indeterminate`` / ``high_lrr``), carrying
    ``calibration_status: heuristic``, ``calibration_anchor:
    user-baseline-required``, and the
    ``thresholds_operator_supplied_not_framework_calibrated`` caveat (the
    binoculars_audit model). The band names the MEASURED property (LRR
    magnitude), never the inference target (authorship); the "more LLM-like"
    DIRECTION stays the unasserted M2 empirical question."""
    operator_supplied = threshold_low is not None and threshold_high is not None
    flags: list[str] = []
    if not operator_supplied:
        if lrr is None:
            flags.append("lrr_undefined_all_rank0")
        return {
            "band": "uncalibrated",
            "flags": flags,
            "calibration_status": "uncalibrated",
            "calibration_anchor": "user-baseline-required",
            "thresholds": None,
            "orientation": (
                "no verdict band ships: the surface reports the raw rank-space "
                "scalars only. No framework-calibrated LRR cutoff exists (spec "
                "§3.5 / §9); supply --threshold-low and --threshold-high to get "
                "a band over your OWN calibrated operating point. The 'more "
                "LLM-like' DIRECTION is the unasserted M2 empirical question "
                "(arXiv:2306.05540)."
            ),
        }
    band = "indeterminate"
    if lrr is None:
        band = "indeterminate"
        flags.append("lrr_undefined_all_rank0")
    elif lrr > threshold_high:
        band = "high_lrr"
        flags.append("lrr_high")
    elif lrr < threshold_low:
        band = "low_lrr"
        flags.append("lrr_low")
    return {
        "band": band,
        "flags": flags,
        "calibration_status": "heuristic",
        "calibration_anchor": "user-baseline-required",
        "thresholds": {"lrr": {"low_below": threshold_low, "high_above": threshold_high}},
        "caveats": ["thresholds_operator_supplied_not_framework_calibrated"],
        "orientation": (
            "band names the MEASURED rank-space property (LRR magnitude), NOT "
            "the inference target (authorship), and uses the OPERATOR-supplied "
            "thresholds (not a framework-calibrated operating point). The 'more "
            "LLM-like' DIRECTION is the empirical question (arXiv:2306.05540, "
            "M2) and is NOT asserted by this band."
        ),
    }


def audit_rank_space(
    text: str,
    *,
    distributions_fn: Callable[[str], tuple[list[float], list[list[float]], list[int]]]
    | None = None,
    backend: object | None = None,
    model_id: str | None = None,
    scorer_dtype: str | None = None,
    threshold_low: float | None = None,
    threshold_high: float | None = None,
) -> dict[str, Any]:
    """Compute the rank-space (LRR) audit on ``text``. Returns the ``results``
    dict for ``build_output``.

    ``distributions_fn`` is the **injection point**: a callable
    ``(text) -> (surprisal_bits, log_probs_nats, token_ids)`` (the return tuple
    of ``SurprisalBackend.score_text_with_distributions``). M1 tests inject a
    deterministic stub; no model is loaded here. If ``distributions_fn`` is
    ``None``, ``backend.score_text_with_distributions`` is used (the M2 path).

    Raises :class:`RankSpaceInputError` on an empty target or a backend that
    returns an inconsistent distribution shape.
    """
    if not text or not text.strip():
        raise RankSpaceInputError("target has no usable text")

    if distributions_fn is None:
        if backend is None:
            raise RankSpaceInputError(
                "audit_rank_space requires either distributions_fn (the "
                "model-free seam) or a backend with "
                "score_text_with_distributions"
            )
        distributions_fn = backend.score_text_with_distributions  # type: ignore[attr-defined]

    surprisal_bits, log_probs_nats, token_ids = distributions_fn(text)

    if not log_probs_nats:
        # Empty / single-token continuation: the backend returns ([], [], [...])
        # or ([], [], []). No rank series to compute — refuse cleanly.
        raise RankSpaceInputError(
            "scorer produced no per-token distributions (text too short for a "
            "continuation under the scoring model)"
        )

    # Multi-window (chunked) detection. When the target exceeds the scorer's
    # context window, SurprisalBackend.score_text_with_distributions slices it
    # into chunks and EACH chunk forfeits its first prediction, so the flattened
    # log_probs_nats has length N - num_chunks while token_ids keeps length N
    # (i.e. len(token_ids) > len(log_probs_nats) + 1 for num_chunks >= 2).
    # rank_series_from_distributions does a positional lookup token_ids[t + 1]
    # over the FULL contiguous sequence, which would read the WRONG next token
    # after the first chunk boundary (silent rank corruption — the family's
    # shared failure mode). We catch the surplus here and refuse with a SPECIFIC,
    # actionable reason that names the real cause (the scorer context window),
    # rather than letting the generic length guard emit a scorer-blaming "rank
    # computation failed" string. A single-window target satisfies
    # len(token_ids) == len(log_probs_nats) + 1 and flows through normally.
    if len(token_ids) > len(log_probs_nats) + 1:
        n_chunks = len(token_ids) - len(log_probs_nats)
        raise RankSpaceTextTooLongError(
            "target exceeds the scorer context window: the scorer chunked it "
            f"into {n_chunks} windows (token_ids length {len(token_ids)} vs "
            f"{len(log_probs_nats)} scored positions), which breaks the "
            "single-sequence rank alignment. Truncate the target to the "
            "scorer's context window (~1024 tokens / ~750 words for gpt2) or "
            "use a longer-context scorer."
        )

    try:
        series = rank_series_from_distributions(
            log_probs_nats, token_ids, surprisal_bits
        )
    except (ValueError, IndexError) as exc:
        raise RankSpaceInputError(
            f"rank computation failed on the scorer output: {exc}"
        ) from exc

    agg = aggregate_rank_signals(
        series["log_rank_series"], series["surprisal_nats_series"], surprisal_bits
    )

    band = _band(
        agg["lrr"],
        n_positions=agg["n_positions"],
        threshold_low=threshold_low,
        threshold_high=threshold_high,
    )

    backend_block = {
        "kind": "causal_lm_logprob_distributions",
        "model_id": model_id,
        "dtype": scorer_dtype,
        "source": "score_text_with_distributions",
    }

    return {
        # All scalars are finite-or-None (rank-0 inf is excluded upstream), so
        # they pass the R4 output-validity gate. The per-token series (which can
        # carry inf at rank-0 positions) is deliberately NOT placed in results.
        "lrr": agg["lrr"],
        "log_rank_mean": agg["log_rank_mean"],
        "log_rank_sd": agg["log_rank_sd"],
        "log_rank_acf1": agg["log_rank_acf1"],
        "log_rank_zero_positions": agg["log_rank_zero_positions"],
        "n_positions": agg["n_positions"],
        "scorer_backend": backend_block,
        "band": band,
        "assumptions": {
            "method": (
                "DetectLLM LRR = sum(surprisal_nats) / sum(log(rank + 1)) over "
                "the sequence (a ratio of sequence sums, NOT a mean of per-token "
                "ratios), with per-token log-rank from an argsort of the scorer's "
                "per-position vocab log-prob distribution (arXiv:2306.05540)"
            ),
            "sign_direction": (
                "rank 0 = most-probable token (descending sort); log_rank(0) = "
                "log(1) = 0. A sign/direction inversion is the rank/surprisal "
                "family's shared silent failure mode — the descending sort and "
                "the rank-0 -> 0 convention are pinned in test_rank_space_signals"
            ),
            "rank0_convention": (
                "LRR is a ratio of sequence sums: a rank-0 (most-probable) token "
                "contributes its surprisal to the numerator and 0 (= log(1)) to "
                "the denominator — it is NOT dropped. lrr is None only when the "
                "whole-sequence denominator sum(log(rank + 1)) is 0 (every scored "
                "token is rank 0), a refusal not a fabricated value. "
                "log_rank_zero_positions records the rank-0 count. The aggregate "
                "scalars are always finite-or-None — no inf reaches the envelope"
            ),
            "esl_non_native_caveat": (
                "log-rank is HIGHER (token less predictable) for lexically "
                "diverse, unconventional, or non-native (ESL) writing using "
                "unexpected-but-valid word choices, so such human text looks "
                "more 'surprising' in rank-space. The rank signal is NOT "
                "ESL-invariant; a threshold is NOT shipped, and the surface "
                "must not be read as an AI/human verdict on such text"
            ),
            "proxy_scorer_dependence": (
                "single-model rank signals are proxy-scorer dependent; a "
                "weaker-than-human generator can INVERT the polarity "
                "(SETEC's generator-strength inversion). Direction stability "
                "across registers is unverified at M1 and is the M2 empirical "
                "question"
            ),
            "paper_status": (
                "arXiv:2306.05540 reports +1.75 / +3.9 AUC lifts on "
                "WritingPrompts (Table 3); those are a LEAD requiring an "
                "empirical reproduction on SETEC corpora, NOT a target or a "
                "shipped claim"
            ),
            "length_ceiling": (
                "the rank series requires a SINGLE scoring window: a target "
                "above the scorer's context window (~1024 tokens / ~750 words "
                "for gpt2) is chunked by the backend, each chunk forfeits its "
                "first prediction, and the single-sequence alignment breaks. "
                "Such a target is REFUSED with a 'text_too_long' message naming "
                "the scorer window (not silently mis-ranked, not the generic "
                "scorer-blaming 'rank computation failed' string); truncate it "
                "or use a longer-context scorer"
            ),
        },
    }


# ----------------------------------------------------------------------
# Claim license (refuses any verdict; surfaces the ESL failure mode).
# ----------------------------------------------------------------------

DEFAULT_LICENSES = (
    "the rank-space surprisal statistics of the target text under the named "
    "causal LM: the DetectLLM LRR (log-likelihood / log-rank ratio, "
    "arXiv:2306.05540) and the per-token log-rank moments (mean / SD / lag-1 "
    "ACF), derived from the model's per-position vocab log-prob distributions "
    "(the same forward pass Binoculars v2 uses — no second pass). These are "
    "discrimination EVIDENCE on the rank axis of the surprisal family, reported "
    "as the raw values only. It is a measurement, not a verdict."
)

DEFAULT_DOES_NOT_LICENSE = (
    "any AI/human authorship verdict, label, or thresholded decision. The "
    "surface ships uncalibrated and NO verdict band: by default band is "
    "'uncalibrated' with thresholds None — no framework-calibrated LRR operating "
    "point exists, and none is invented (spec §3.5 / §9). A band appears only if "
    "the operator supplies their OWN --threshold-low / --threshold-high, and it "
    "then carries calibration_status heuristic, calibration_anchor "
    "user-baseline-required, and the "
    "thresholds_operator_supplied_not_framework_calibrated caveat — it names the "
    "MEASURED property (rank-space surprisal), never the inference target "
    "(authorship). There is no is_ai / is_human / classification / prediction / "
    "verdict key. ESL / NON-NATIVE FAILURE MODE: log-rank is higher for "
    "lexically diverse or non-native prose that uses unexpected-but-valid word "
    "choices, so a human ESL writer can score more 'surprising' in rank-space — "
    "the signal is NOT ESL-invariant and must not be read as a verdict on such "
    "text. Single-model rank signals are PROXY-SCORER DEPENDENT: a "
    "weaker-than-human generator can invert the polarity, so the direction is "
    "not portable across scorers/registers without an empirical check. The "
    "paper's +1.75 / +3.9 AUC lifts are WritingPrompts-specific (Table 3) and "
    "are NOT asserted here. Promotion of an LRR operating point to a "
    "framework-calibrated default goes only through scripts/calibration/ against "
    "a labeled corpus, never by tuning on a held-out set; LRR is a "
    "comparison-baseline, never a held-out audit / fitness / selection signal."
)


def _claim_license(results: dict[str, Any]) -> ClaimLicense:
    backend = results.get("scorer_backend", {})
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=DEFAULT_LICENSES,
        does_not_license=DEFAULT_DOES_NOT_LICENSE,
        comparison_set={
            "mode": "single_document_uncalibrated",
            "scorer_model": backend.get("model_id"),
            "scorer_dtype": backend.get("dtype"),
        },
        additional_caveats=[
            "Uncalibrated — NO verdict band ships by default (band 'uncalibrated', "
            "thresholds None); no framework-calibrated operating point exists and "
            "none is invented. A band appears only with operator-supplied "
            "--threshold-low / --threshold-high.",
            "Length ceiling: a target above the scorer context window (~1024 "
            "tokens / ~750 words for gpt2) is chunked by the backend and REFUSED "
            "with a 'text_too_long' message naming the scorer window, not "
            "silently mis-ranked. Truncate or use a longer-context scorer.",
            "ESL / non-native: log-rank is higher for unconventional-but-valid "
            "word choices, so non-native human prose looks more 'surprising' in "
            "rank-space; the signal is NOT ESL-invariant and ships no threshold.",
            "Single-model rank signals are proxy-scorer dependent; a "
            "weaker-than-human generator inverts the polarity (generator-"
            "strength inversion). Direction stability across registers is the "
            "M2 empirical question, unverified at M1.",
            "LRR = sum(surprisal_nats) / sum(log(rank + 1)) is a ratio of "
            "sequence sums; a rank-0 (most-probable) token feeds the numerator "
            "but adds 0 to the denominator (it is NOT dropped). LRR is undefined "
            "(None) only when every scored token is rank 0, i.e. the sequence "
            "denominator is 0 (log_rank_zero_positions records the count). No inf "
            "reaches the envelope.",
            "The arXiv:2306.05540 AUC lifts (+1.75 / +3.9) are WritingPrompts-"
            "specific and a LEAD, not a target; no paper number is asserted as "
            "fact here.",
            "LRR is a comparison-baseline / heuristic, never a held-out audit, "
            "fitness, or selection signal (anti-Goodhart). Promotion to a "
            "framework-calibrated operating point goes only through "
            "scripts/calibration/.",
        ],
        references=[
            "Su, Zhuo, Wang, Nakov 2023, 'DetectLLM: Leveraging Log Rank "
            "Information for Zero-Shot Detection of Machine-Generated Text' "
            "(arXiv:2306.05540)",
            "specs/32-rank-space-detectllm.md",
        ],
    )


def compose_envelope(
    *,
    target_path: Path | str | None,
    target_words: int,
    results: dict[str, Any],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=None,
        results=results,
        claim_license=_claim_license(results),
        available=True,
        warnings=warnings,
    )


# ----------------------------------------------------------------------
# Markdown renderer.
# ----------------------------------------------------------------------


def _fmt(x: float | None) -> str:
    return f"{x:.4f}" if isinstance(x, (int, float)) else "n/a"


def render_markdown(envelope: dict[str, Any]) -> str:
    results = envelope["results"]
    target = envelope["target"]
    band = results.get("band", {})
    backend = results.get("scorer_backend", {})
    lines: list[str] = [
        "# Rank-Space Surprisal Audit (DetectLLM LRR)",
        "",
        f"- **Target:** `{target.get('path')}` ({target.get('words')} words)",
        f"- **Scorer:** `{backend.get('model_id')}` "
        f"(dtype: {backend.get('dtype')}, {backend.get('source')})",
        f"- **Positions scored:** {results.get('n_positions')} "
        f"(rank-0 positions, in LRR numerator only: "
        f"{results.get('log_rank_zero_positions')})",
        "",
        "## Result",
        "",
        f"**LRR (log-likelihood / log-rank ratio):** {_fmt(results.get('lrr'))}",
        f"**log_rank_mean:** {_fmt(results.get('log_rank_mean'))}  "
        f"**log_rank_sd:** {_fmt(results.get('log_rank_sd'))}  "
        f"**log_rank_acf1:** {_fmt(results.get('log_rank_acf1'))}",
        f"**Band:** `{band.get('band')}` "
        f"(calibration_status: `{band.get('calibration_status')}`, "
        f"anchor: `{band.get('calibration_anchor')}`)",
        "",
        "_No verdict band ships: by default the band is `uncalibrated` and the "
        "surface reports the raw scalars only — there is no framework-calibrated "
        "LRR threshold, and none is invented. A band appears only over "
        "operator-supplied --threshold-low / --threshold-high, and even then "
        "names the MEASURED rank-space property, NOT 'is AI'. The 'more LLM-like' "
        "direction is the unasserted empirical question (arXiv:2306.05540). ESL / "
        "non-native prose looks more 'surprising' in rank-space — not an AI/human "
        "verdict on such text._",
        "",
        "## Claim license",
        "",
        (envelope.get("claim_license_rendered") or "").rstrip(),
        "",
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------------
# CLI.
# ----------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Rank-space surprisal audit (DetectLLM LRR, spec 32, M1): per-token "
            "log-rank moments + the LRR statistic, derived from a causal LM's "
            "log-prob distributions (no second forward pass). Descriptive "
            "discrimination evidence on the rank axis of the surprisal family — "
            "NO verdict."
        ),
    )
    p.add_argument("target", help="Path to the target text file (UTF-8).")
    p.add_argument(
        "--model", default=None,
        help="Scoring model alias or HF id (default: the SurprisalBackend default).",
    )
    p.add_argument(
        "--surprisal-dtype", default=None,
        choices=["auto", "fp32", "fp16", "bf16"],
        help="dtype for the scoring model (default: backend 'auto').",
    )
    p.add_argument(
        "--threshold-low", type=float, default=DEFAULT_THRESHOLD_LOW,
        help=(
            "Below this LRR value the band is low_lrr. No framework-calibrated "
            "default; without BOTH this and --threshold-high the band is "
            "'uncalibrated' and no band is emitted. Operator-supplied thresholds "
            "carry the thresholds_operator_supplied_not_framework_calibrated "
            "caveat — they are NOT an AI/human verdict."
        ),
    )
    p.add_argument(
        "--threshold-high", type=float, default=DEFAULT_THRESHOLD_HIGH,
        help="Above this LRR value the band is high_lrr. See --threshold-low on calibration.",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit the JSON envelope instead of a markdown report.",
    )
    p.add_argument("--out", default=None, help="Write output to this path instead of stdout.")
    return p


def _emit(envelope: dict[str, Any], args: argparse.Namespace, *, as_markdown: bool) -> None:
    if as_markdown:
        text_out = render_markdown(envelope)
    else:
        text_out = json.dumps(envelope, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(
            text_out + ("\n" if not text_out.endswith("\n") else ""),
            encoding="utf-8",
        )
        sys.stderr.write(f"Wrote output to {args.out}\n")
    if not args.out or args.json:
        sys.stdout.write(text_out + ("\n" if not text_out.endswith("\n") else ""))


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    target_path = Path(args.target).expanduser()
    try:
        target_text = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=f"cannot read target: {exc}", reason_category="bad_input",
        )
        _emit(envelope, args, as_markdown=False)
        return 3

    word_count = len(word_tokens(target_text))
    if word_count == 0:
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), target_words=0,
            reason="target has no countable word tokens",
            reason_category="text_too_short",
        )
        _emit(envelope, args, as_markdown=False)
        return 3

    warnings: list[str] = []
    if word_count < LENGTH_FLOOR_WORDS:
        warnings.append(
            f"target is {word_count} words, below the {LENGTH_FLOOR_WORDS}-word "
            "floor; the rank-space estimate is unstable on short text — "
            "reported but not over-claimed"
        )
    if word_count > LENGTH_CEILING_WARN_WORDS:
        warnings.append(
            f"target is {word_count} words, near or above the scorer's "
            "context-window ceiling (~1024 tokens / ~750 words for gpt2); a "
            "target that exceeds the window is chunked by the backend and "
            "REFUSED with reason_category text_too_long (the rank series needs a "
            "single scoring window). Truncate it or use a longer-context scorer."
        )

    # Lazy model construction: torch / transformers are imported ONLY here (the
    # M2 path), so import rank_space_audit and the unit tests stay model-free.
    try:
        from surprisal_backend import (  # type: ignore  # noqa: PLC0415
            SurprisalBackend,
            SurprisalBackendError,
            resolve_model_arg,
        )
    except Exception as exc:  # pragma: no cover - dependency-absent path
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), target_words=word_count,
            reason=(
                "the surprisal tier (transformers + torch) is required to score "
                f"a target from the CLI: {exc}"
            ),
            reason_category="missing_dependency",
        )
        _emit(envelope, args, as_markdown=False)
        return 3

    model_id = resolve_model_arg(args.model)
    dtype = args.surprisal_dtype or "auto"
    try:
        backend = SurprisalBackend(model_id=model_id, dtype=dtype)
        results = audit_rank_space(
            target_text, backend=backend, model_id=model_id, scorer_dtype=dtype,
            threshold_low=args.threshold_low, threshold_high=args.threshold_high,
        )
    except RankSpaceTextTooLongError as exc:
        # Caught explicitly (before RankSpaceInputError, its base class) so the
        # message NAMES the real cause — the scorer context window — instead of
        # the scorer-blaming "rank computation failed on the scorer output"
        # string the generic length guard would emit. reason_category stays
        # bad_input (the shared output_schema.REASON_CATEGORIES enum has no
        # text_too_long member; adding one is a cross-consumer schema change out
        # of scope here), but the actionable message is the fix: it tells the
        # operator to truncate or use a longer-context scorer, with the explicit
        # text_too_long marker carried in the message + warnings.
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), target_words=word_count,
            reason=f"text_too_long: {exc}", reason_category="bad_input",
        )
        _emit(envelope, args, as_markdown=False)
        return 3
    except RankSpaceInputError as exc:
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), target_words=word_count,
            reason=str(exc), reason_category="bad_input",
        )
        _emit(envelope, args, as_markdown=False)
        return 3
    except SurprisalBackendError as exc:
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), target_words=word_count,
            reason=f"scoring backend error: {exc}",
            reason_category="missing_dependency",
        )
        _emit(envelope, args, as_markdown=False)
        return 3

    envelope = compose_envelope(
        target_path=target_path,
        target_words=word_count,
        results=results,
        warnings=warnings or None,
    )
    _emit(envelope, args, as_markdown=not args.json)
    return 0 if envelope.get("available", True) else 3


if __name__ == "__main__":
    raise SystemExit(main())
