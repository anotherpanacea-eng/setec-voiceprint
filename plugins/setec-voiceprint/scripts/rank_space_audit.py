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
backend / torch import is lazy (inside ``main`` / the seam), so
``import rank_space_audit`` and the unit tests stay model-free.

Paper: Su, Zhuo, Wang, Nakov, "DetectLLM: Leveraging Log Rank Information for
Zero-Shot Detection of Machine-Generated Text" (arXiv:2306.05540, MBZUAI 2023).
The paper's +1.75 / +3.9 AUC lifts (WritingPrompts, Table 3) are a LEAD requiring
an empirical reproduction (M2) before reliance; they are NOT asserted here. The
signal DIRECTION across registers is the empirical question — this surface only
reports the values and a provisional band.

POSTURE (no verdict)
====================
Descriptive only: VALUES (``log_rank_mean`` / ``log_rank_sd`` / ``log_rank_acf1``
/ ``lrr``) + a PROVISIONAL band over the LRR value's OWN axis
(``indeterminate`` / ``low_lrr`` / ``high_lrr``) carrying
``calibration_status: heuristic`` + ``calibration_anchor: user-baseline-required``,
and a claim-license that refuses any AI/human or thresholded verdict. There is NO
``is_ai`` / ``is_human`` / ``label`` / ``verdict`` / ``decision`` key. The band
names the MEASURED property (rank-space surprisal), never the inference target
(authorship). The known ESL / non-native false-positive failure mode is surfaced
in the claim-license and the assumptions block.

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

# PROVISIONAL band thresholds on the LRR VALUE's own axis. Fixture-derived
# first-reading numbers, NOT a calibrated operating point: calibration_status is
# "heuristic", calibration_anchor "user-baseline-required". Disjoint from any
# held-out validation corpus (anti-Goodhart): promotion past "heuristic" goes
# only through scripts/calibration/ against a labeled corpus, never by tuning
# here. The DIRECTION (which side is "more LLM-like") is itself the empirical
# question (M2) and is NOT asserted — the band names the value's own axis.
PROVISIONAL_BAND_THRESHOLDS: dict[str, dict[str, float]] = {
    "lrr": {
        "high_above": 1.20,
        "low_below": 0.60,
    },
}


class RankSpaceInputError(ValueError):
    """Raised by ``audit_rank_space`` on an unusable input. The CLI maps this to
    a structured ``build_error_output`` envelope, never a traceback."""


def _provisional_band(lrr: float | None, *, n_positions: int) -> dict[str, Any]:
    """Descriptive band over the LRR VALUE's own axis. NEVER over authorship.
    ``band ∈ {indeterminate, low_lrr, high_lrr}`` is the only categorical leaf.
    Ships ``heuristic`` + ``user-baseline-required`` so it is never read as a
    calibrated decision boundary.

    Fails toward NO reading (``indeterminate``) when ``lrr`` is ``None`` (every
    position was rank 0 — a degenerate all-most-probable sequence) rather than
    inventing a direction on a non-event."""
    th = PROVISIONAL_BAND_THRESHOLDS["lrr"]
    band = "indeterminate"
    flags: list[str] = []
    if lrr is None:
        flags.append("lrr_undefined_all_rank0")
    elif lrr > th["high_above"]:
        band = "high_lrr"
        flags.append("lrr_high")
    elif lrr < th["low_below"]:
        band = "low_lrr"
        flags.append("lrr_low")
    return {
        "band": band,
        "flags": flags,
        "calibration_status": "heuristic",
        "calibration_anchor": "user-baseline-required",
        "thresholds_used": {"lrr": dict(PROVISIONAL_BAND_THRESHOLDS["lrr"])},
        "orientation": (
            "band names the MEASURED rank-space property (LRR magnitude), NOT "
            "the inference target (authorship). The 'more LLM-like' DIRECTION "
            "is the empirical question (arXiv:2306.05540, M2) and is NOT "
            "asserted by this band."
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

    try:
        series = rank_series_from_distributions(
            log_probs_nats, token_ids, surprisal_bits
        )
    except (ValueError, IndexError) as exc:
        raise RankSpaceInputError(
            f"rank computation failed on the scorer output: {exc}"
        ) from exc

    agg = aggregate_rank_signals(
        series["log_rank_series"], series["lrr_series"], surprisal_bits
    )

    band = _provisional_band(agg["lrr"], n_positions=agg["n_positions"])

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
        "lrr_excluded_positions": agg["lrr_excluded_positions"],
        "n_positions": agg["n_positions"],
        "scorer_backend": backend_block,
        "band": band,
        "assumptions": {
            "method": (
                "DetectLLM LRR = mean(surprisal_nats / log(rank + 1)) over the "
                "sequence, with per-token log-rank from an argsort of the "
                "scorer's per-position vocab log-prob distribution "
                "(arXiv:2306.05540)"
            ),
            "sign_direction": (
                "rank 0 = most-probable token (descending sort); log_rank(0) = "
                "log(1) = 0. A sign/direction inversion is the rank/surprisal "
                "family's shared silent failure mode — the descending sort and "
                "the rank-0 -> 0 convention are pinned in test_rank_space_signals"
            ),
            "rank0_inf_convention": (
                "lrr_t = surprisal_nats / log(rank + 1) is undefined at rank 0 "
                "(log(1) = 0); those positions are emitted as inf in the series "
                "and EXCLUDED from the lrr mean (count in "
                "lrr_excluded_positions). The aggregate scalars are always "
                "finite — no inf reaches the envelope"
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
    "as values plus a DESCRIPTIVE band over the LRR value's own axis. It is a "
    "measurement, not a verdict."
)

DEFAULT_DOES_NOT_LICENSE = (
    "any AI/human authorship verdict, label, or thresholded decision. The "
    "surface ships uncalibrated: the band is PROVISIONAL (calibration_status "
    "heuristic, calibration_anchor user-baseline-required) and names the "
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
    "are NOT asserted here. Promotion of the band past heuristic goes only "
    "through scripts/calibration/ against a labeled corpus, never by tuning on "
    "a held-out set; LRR is a comparison-baseline, never a held-out audit / "
    "fitness / selection signal."
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
            "Uncalibrated — provisional band, no verdict, no shipped operating "
            "point (calibration_status heuristic).",
            "ESL / non-native: log-rank is higher for unconventional-but-valid "
            "word choices, so non-native human prose looks more 'surprising' in "
            "rank-space; the signal is NOT ESL-invariant and ships no threshold.",
            "Single-model rank signals are proxy-scorer dependent; a "
            "weaker-than-human generator inverts the polarity (generator-"
            "strength inversion). Direction stability across registers is the "
            "M2 empirical question, unverified at M1.",
            "rank-0 (most-probable token) positions give an undefined LRR "
            "(division by log(1) = 0); they are excluded from the LRR mean "
            "(lrr_excluded_positions records the count). No inf reaches the "
            "envelope.",
            "The arXiv:2306.05540 AUC lifts (+1.75 / +3.9) are WritingPrompts-"
            "specific and a LEAD, not a target; no paper number is asserted as "
            "fact here.",
            "LRR is a comparison-baseline / heuristic, never a held-out audit, "
            "fitness, or selection signal (anti-Goodhart). Promotion past "
            "heuristic goes only through scripts/calibration/.",
        ],
        references=[
            "Su, Zhuo, Wang, Nakov 2023, 'DetectLLM: Leveraging Log Rank "
            "Information for Zero-Shot Detection of Machine-Generated Text' "
            "(arXiv:2306.05540)",
            "plugins/setec-voiceprint/specs/32-rank-space-detectllm.md",
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
        f"(rank-0 excluded from LRR: {results.get('lrr_excluded_positions')})",
        "",
        "## Result",
        "",
        f"**LRR (log-likelihood / log-rank ratio):** {_fmt(results.get('lrr'))}",
        f"**log_rank_mean:** {_fmt(results.get('log_rank_mean'))}  "
        f"**log_rank_sd:** {_fmt(results.get('log_rank_sd'))}  "
        f"**log_rank_acf1:** {_fmt(results.get('log_rank_acf1'))}",
        f"**Band (DESCRIPTIVE, over the value's own axis):** "
        f"`{band.get('band')}` "
        f"(calibration_status: `{band.get('calibration_status')}`, "
        f"anchor: `{band.get('calibration_anchor')}`)",
        "",
        "_The band names the MEASURED rank-space property, NOT 'is AI'. The "
        "'more LLM-like' direction is the empirical question (arXiv:2306.05540) "
        "and is not asserted. Uncalibrated: no verdict, no shipped threshold. "
        "ESL / non-native prose looks more 'surprising' in rank-space — not an "
        "AI/human verdict on such text._",
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
            target_text, backend=backend, model_id=model_id, scorer_dtype=dtype
        )
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
