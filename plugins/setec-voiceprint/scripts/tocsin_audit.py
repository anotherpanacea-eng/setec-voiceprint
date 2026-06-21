#!/usr/bin/env python3
"""tocsin_audit.py — TOCSIN token-cohesiveness detection signal (spec 31, M1).

A black-box, detector-flavored discrimination signal: the **token cohesiveness**
of a text — how stable its meaning is under repeated random token deletion. The
TOCSIN finding (Wang, Cheng, et al., "Zero-Shot Detection of LLM-Generated Text
using Token Cohesiveness", arXiv:2409.16914) is that LLM-generated text exhibits
*higher* token cohesiveness than human text (its semantics degrade less when
tokens are randomly dropped). The paper reports cohesiveness as an axis
**orthogonal to surprisal/curvature** — a plug-and-play second channel, not a
re-derivation of perplexity. Evidence, not verdict.

Spec: ``specs/31-tocsin-token-cohesiveness.md``.

MECHANISM
=========
(1) draw ``n_perturbations`` random-token-deletion perturbations of the target
(delete a fraction ``deletion_fraction`` of token positions), (2) measure the
**semantic difference** between each perturbation and the original, (3) summarize
into ``token_cohesiveness = 1 - mean(semantic_diff)``, reported with its
dispersion (SD across perturbations).

INJECTABLE SEMANTIC DIFFERENCE (the M1/M2 seam)
===============================================
The semantic-difference step is the only load-bearing model dependency, so it is
the seam. ``audit_tocsin`` takes an **injectable** ``semantic_diff`` callable
``(original_tokens: list[str], perturbed_tokens: list[str]) -> float in [0, 1]``
(mirrors ``intrinsic_dimension_audit``'s injectable ``embed`` and
``surprisal_audit``'s injectable ``score_fn``). M1 default = a stdlib token-set
Jaccard distance (``1 - jaccard``, deterministic, no model). M2 swaps in
``1 - cosine(embed(original), embed(perturbed))`` behind the SAME seam — no model
is imported at module load or touched in tests.

POSTURE (no verdict)
====================
Descriptive only: VALUES (``token_cohesiveness`` + SD + raw mean semantic diff) +
a PROVISIONAL band over the value's OWN axis (``indeterminate`` /
``low_cohesiveness`` / ``high_cohesiveness``) carrying
``calibration_status: heuristic`` + ``calibration_anchor: user-baseline-required``
+ a claim-license that refuses any AI/human or thresholded verdict. There is NO
``is_ai`` / ``is_human`` / ``label`` / ``verdict`` / ``decision`` key. The band
names the MEASURED property (cohesiveness), never the inference target
(authorship).

CLI:

    python3 scripts/tocsin_audit.py --target TARGET [--n-perturbations N] \
        [--deletion-fraction F] [--seed S] [--json] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import math
import random
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
from stylometry_core import word_tokens  # type: ignore  # noqa: E402

TASK_SURFACE = "token_cohesiveness"
TOOL_NAME = "tocsin_audit"
SCRIPT_VERSION = "1.0"

# Defaults. seed=1729 matches intrinsic_dimension_audit's seed convention.
DEFAULT_N_PERTURBATIONS = 30
DEFAULT_DELETION_FRACTION = 0.10
DEFAULT_SEED = 1729

# Length floor (words). Below this the lexical-overlap proxy is unstable
# (short / formulaic text inflates apparent stability), so the surface WARNS
# rather than refuses. Matches rank_turbulence_audit (the stdlib-discrimination
# sibling) and §9 of the spec.
LENGTH_FLOOR_WORDS = 200

# PROVISIONAL band thresholds on the cohesiveness VALUE's own axis. These are
# fixture-derived first-reading numbers, NOT a calibrated operating point:
# calibration_status is "heuristic" and calibration_anchor is
# "user-baseline-required". They are disjoint from any held-out validation
# corpus (anti-Goodhart): promotion past "heuristic" goes only through
# scripts/calibration/ against a labeled corpus, never by tuning here.
PROVISIONAL_BAND_THRESHOLDS: dict[str, dict[str, float]] = {
    "token_cohesiveness": {
        # Higher cohesiveness = meaning survives deletion (paper's "more
        # LLM-like" DIRECTION; NOT "is AI").
        "high_above": 0.85,
        "low_below": 0.65,
    },
}


# ----------------------------------------------------------------------
# Stdlib semantic-difference proxy (M1). Token-set Jaccard distance.
# ----------------------------------------------------------------------


def jaccard_distance(original_tokens: list[str], perturbed_tokens: list[str]) -> float:
    """Token-set Jaccard distance in [0, 1]: ``1 - |A∩B| / |A∪B|``.

    0 = identical token sets (meaning preserved); 1 = disjoint. Deterministic,
    pure-Python, no model. This is the M1 stdlib PROXY for the paper's embedding
    semantic difference (recorded in ``semantic_diff_backend.metric``).

    Bounds hold on every input: two empty inputs give a union of size 0, which
    we define as distance 0.0 (no tokens differ — the empty text is identical to
    its empty deletion). Deleting all of a non-empty text leaves a perturbed set
    that is a subset of the original, so the distance is well-defined in [0, 1].
    """
    a = set(original_tokens)
    b = set(perturbed_tokens)
    union = a | b
    if not union:
        # Both empty: nothing differs.
        return 0.0
    inter = a & b
    return 1.0 - (len(inter) / len(union))


# ----------------------------------------------------------------------
# Perturbation engine (seeded random token deletion).
# ----------------------------------------------------------------------


def delete_tokens(
    tokens: list[str], deletion_fraction: float, rng: random.Random
) -> list[str]:
    """Return a copy of ``tokens`` with ``floor(deletion_fraction * len)`` token
    positions deleted at random (drawn from ``rng``, in original order).

    Deterministic given ``rng``'s state. Deletes at least 0 positions; never
    deletes all positions unless ``deletion_fraction >= 1`` (the caller bounds
    the fraction to (0, 1), so at least one token always survives a non-empty
    input)."""
    n = len(tokens)
    if n == 0:
        return []
    k = int(math.floor(deletion_fraction * n))
    if k <= 0:
        return list(tokens)
    if k >= n:
        return []
    drop = set(rng.sample(range(n), k))
    return [t for i, t in enumerate(tokens) if i not in drop]


# ----------------------------------------------------------------------
# Provisional band (descriptive, over the value's OWN axis — NOT a verdict).
# ----------------------------------------------------------------------


def _provisional_band(token_cohesiveness: float) -> dict[str, Any]:
    """Descriptive band over the cohesiveness VALUE's own axis. NEVER over
    authorship. ``band ∈ {indeterminate, low_cohesiveness, high_cohesiveness}``
    is the only categorical leaf in the whole envelope. Ships ``heuristic`` +
    ``user-baseline-required`` so it is never read as a calibrated decision
    boundary."""
    th = PROVISIONAL_BAND_THRESHOLDS["token_cohesiveness"]
    band = "indeterminate"
    flags: list[str] = []
    if token_cohesiveness > th["high_above"]:
        band = "high_cohesiveness"
        flags.append("cohesiveness_high")
    elif token_cohesiveness < th["low_below"]:
        band = "low_cohesiveness"
        flags.append("cohesiveness_low")
    return {
        "band": band,
        "flags": flags,
        "calibration_status": "heuristic",
        "calibration_anchor": "user-baseline-required",
        "thresholds_used": {
            "token_cohesiveness": dict(PROVISIONAL_BAND_THRESHOLDS["token_cohesiveness"])
        },
        "orientation": (
            "high cohesiveness = meaning survives deletion (paper's "
            "'more LLM-like' DIRECTION); NOT 'is AI'"
        ),
    }


# ----------------------------------------------------------------------
# Audit (injectable semantic_diff).
# ----------------------------------------------------------------------


class TocsinInputError(ValueError):
    """Raised by ``audit_tocsin`` on an unusable input (too-short text, an
    out-of-range deletion fraction). The CLI maps this to a structured
    ``build_error_output`` envelope, never a traceback."""


def audit_tocsin(
    text: str,
    *,
    semantic_diff: Callable[[list[str], list[str]], float] | None = None,
    n_perturbations: int = DEFAULT_N_PERTURBATIONS,
    deletion_fraction: float = DEFAULT_DELETION_FRACTION,
    seed: int = DEFAULT_SEED,
    semantic_diff_backend: dict[str, Any] | None = None,
    deletion_unit: str = "word_token",
) -> dict[str, Any]:
    """Compute the TOCSIN token-cohesiveness audit. Returns the ``results`` dict
    for ``build_output``.

    ``semantic_diff`` is the **injection point**: a callable
    ``(original_tokens, perturbed_tokens) -> float in [0, 1]``. M1 default =
    :func:`jaccard_distance` (stdlib, no model); M2 callers inject an
    embedding-backed distance behind the SAME seam. No model is loaded here.

    Determinism: all deletion randomness flows from a single
    ``random.Random(seed)``; identical input + seed ⇒ byte-identical results.

    Raises :class:`TocsinInputError` on an empty target or a
    ``deletion_fraction`` outside ``(0, 1)``."""
    if not (0.0 < deletion_fraction < 1.0):
        raise TocsinInputError(
            f"deletion_fraction must be in (0, 1); got {deletion_fraction!r}"
        )
    if n_perturbations < 1:
        raise TocsinInputError(
            f"n_perturbations must be >= 1; got {n_perturbations!r}"
        )

    tokens = word_tokens(text)
    if not tokens:
        raise TocsinInputError("target has no countable word tokens")

    diff_fn = semantic_diff if semantic_diff is not None else jaccard_distance
    backend_block = semantic_diff_backend or {
        "kind": "lexical_overlap_stdlib",
        "id": None,
        "metric": "1 - jaccard(token_sets)",
    }

    rng = random.Random(seed)
    diffs: list[float] = []
    for _ in range(n_perturbations):
        perturbed = delete_tokens(tokens, deletion_fraction, rng)
        d = float(diff_fn(tokens, perturbed))
        diffs.append(d)

    mean_diff = sum(diffs) / len(diffs)
    cohesiveness_vals = [1.0 - d for d in diffs]
    token_cohesiveness = 1.0 - mean_diff  # == mean(cohesiveness_vals)
    # Population SD of the per-perturbation cohesiveness (== SD of the diffs).
    if len(cohesiveness_vals) > 1:
        mean_c = token_cohesiveness
        variance = sum((c - mean_c) ** 2 for c in cohesiveness_vals) / len(
            cohesiveness_vals
        )
        cohesiveness_sd = math.sqrt(variance)
    else:
        cohesiveness_sd = 0.0

    band = _provisional_band(token_cohesiveness)

    return {
        "token_cohesiveness": token_cohesiveness,
        "cohesiveness_sd": cohesiveness_sd,
        "mean_semantic_diff": mean_diff,
        "n_perturbations": int(n_perturbations),
        "deletion_fraction": float(deletion_fraction),
        "deletion_unit": deletion_unit,
        "seed": int(seed),
        "target_tokens": len(tokens),
        "semantic_diff_backend": backend_block,
        "band": band,
        "assumptions": {
            "method": (
                "TOCSIN random-token-deletion cohesiveness (arXiv:2409.16914)"
            ),
            "orientation": (
                "higher token_cohesiveness = more stable under deletion; "
                "orthogonal axis to surprisal/curvature, NOT a verdict"
            ),
            "m1_proxy": (
                "M1 semantic difference is a stdlib token-set Jaccard distance, "
                "a PROXY for the paper's embedding semantic difference; the "
                "value is not comparable to an embedding-backed run "
                "(deletion_unit + semantic_diff_backend record which regime "
                "produced it)"
            ),
            "corpus_dependence": (
                "cohesiveness is register- and length-dependent; thresholds "
                "are PROVISIONAL / operator-side"
            ),
        },
    }


# ----------------------------------------------------------------------
# Claim license (refuses any verdict).
# ----------------------------------------------------------------------

DEFAULT_LICENSES = (
    "the token cohesiveness of the text — how stable its token set is under "
    "repeated random token deletion (TOCSIN, arXiv:2409.16914). It reports the "
    "scalar token_cohesiveness in [0,1] (1 - mean semantic difference under "
    "deletion), its dispersion, and a DESCRIPTIVE band over that value's own "
    "axis. In the literature LLM text tends to HIGHER cohesiveness than human "
    "text, so the scalar is discrimination evidence on an axis the paper "
    "reports as orthogonal to surprisal / curvature. It is a measurement, not "
    "a verdict."
)

DEFAULT_DOES_NOT_LICENSE = (
    "any AI/human authorship verdict, label, or thresholded decision. The "
    "surface ships uncalibrated: the band is PROVISIONAL (calibration_status "
    "heuristic, calibration_anchor user-baseline-required) and names the "
    "MEASURED property (cohesiveness), never the inference target (authorship). "
    "There is no is_ai / is_human / classification / prediction / verdict key. "
    "The M1 value uses a stdlib token-overlap PROXY for the paper's embedding "
    "semantic difference, so values are NOT comparable across semantic-diff "
    "backends (M1 proxy != M2 embedding) — semantic_diff_backend and "
    "deletion_unit record the regime. Cohesiveness is register- and "
    "length-dependent (short / formulaic text inflates apparent stability), so "
    "below the length floor the surface warns. It is one axis among many for "
    "the multi-signal evidence pack, with the human in the loop; promotion of "
    "the band past heuristic goes only through scripts/calibration/ against a "
    "labeled corpus, never by tuning on a held-out set."
)


def _claim_license(results: dict[str, Any]) -> ClaimLicense:
    backend = results.get("semantic_diff_backend", {})
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=DEFAULT_LICENSES,
        does_not_license=DEFAULT_DOES_NOT_LICENSE,
        comparison_set={
            "mode": "single_document_uncalibrated",
            "semantic_diff_backend": backend.get("kind"),
            "deletion_unit": results.get("deletion_unit"),
        },
        additional_caveats=[
            "Uncalibrated — provisional band, no verdict, no shipped "
            "operating point (calibration_status heuristic).",
            "M1 uses a stdlib token-set Jaccard PROXY for the paper's "
            "embedding semantic difference; values are comparable only within "
            "one semantic_diff backend, not across M1 proxy and M2 embedding.",
            "M1 deletes word tokens; the paper deletes model sub-word tokens "
            "(deletion_unit records which). Direction is preserved; magnitudes "
            "differ.",
            "Cohesiveness is register- and length-dependent; below the length "
            "floor (200 words) the surface warns rather than over-claims.",
        ],
        references=[
            "Wang, Cheng, et al. 2024, 'Zero-Shot Detection of LLM-Generated "
            "Text using Token Cohesiveness' (TOCSIN, arXiv:2409.16914)",
            "plugins/setec-voiceprint/specs/31-tocsin-token-cohesiveness.md",
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


def render_markdown(envelope: dict[str, Any]) -> str:
    results = envelope["results"]
    target = envelope["target"]
    band = results.get("band", {})
    backend = results.get("semantic_diff_backend", {})
    lines: list[str] = [
        "# Token-Cohesiveness Audit (TOCSIN)",
        "",
        f"- **Target:** `{target.get('path')}` ({target.get('words')} words)",
        f"- **Tokens:** {results.get('target_tokens')} "
        f"(deletion unit: {results.get('deletion_unit')})",
        f"- **Perturbations:** {results.get('n_perturbations')} "
        f"@ deletion_fraction={results.get('deletion_fraction')}, "
        f"seed={results.get('seed')}",
        f"- **Semantic-diff backend:** `{backend.get('kind')}` "
        f"({backend.get('metric')})",
        "",
        "## Result",
        "",
        f"**token_cohesiveness:** {results.get('token_cohesiveness'):.4f} "
        f"(SD {results.get('cohesiveness_sd'):.4f})",
        f"**mean semantic difference under deletion:** "
        f"{results.get('mean_semantic_diff'):.4f}",
        f"**Band (DESCRIPTIVE, over the value's own axis):** "
        f"`{band.get('band')}` "
        f"(calibration_status: `{band.get('calibration_status')}`, "
        f"anchor: `{band.get('calibration_anchor')}`)",
        "",
        "_Higher cohesiveness = meaning survives deletion (the paper's "
        "'more LLM-like' DIRECTION); NOT 'is AI'. Uncalibrated: the band is "
        "provisional, no verdict, no shipped threshold._",
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
            "TOCSIN token-cohesiveness audit (M1, stdlib): how stable a text's "
            "token set is under repeated random token deletion. Descriptive "
            "discrimination evidence on an axis the paper reports as orthogonal "
            "to surprisal/curvature — NO verdict."
        ),
    )
    p.add_argument("--target", required=True, help="Path to the target text file (UTF-8).")
    p.add_argument(
        "--n-perturbations", type=int, default=DEFAULT_N_PERTURBATIONS,
        help=f"Number of random-deletion perturbations (default {DEFAULT_N_PERTURBATIONS}).",
    )
    p.add_argument(
        "--deletion-fraction", type=float, default=DEFAULT_DELETION_FRACTION,
        help=(
            f"Fraction of token positions to delete per perturbation, in (0, 1) "
            f"(default {DEFAULT_DELETION_FRACTION})."
        ),
    )
    p.add_argument(
        "--seed", type=int, default=DEFAULT_SEED,
        help=f"Determinism seed (default {DEFAULT_SEED}).",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit the JSON envelope instead of a markdown report.",
    )
    p.add_argument("--out", default=None, help="Write output to this path instead of stdout.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    # Usage errors (exit 2) before any I/O — argparse-adjacent validation.
    if args.n_perturbations < 1:
        sys.stderr.write("[tocsin_audit] --n-perturbations must be >= 1\n")
        return 2
    if not (0.0 < args.deletion_fraction < 1.0):
        sys.stderr.write("[tocsin_audit] --deletion-fraction must be in (0, 1)\n")
        return 2

    target_path = Path(args.target).expanduser()
    try:
        target_text = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=f"cannot read --target: {exc}", reason_category="bad_input",
        )
        _emit(envelope, args, as_markdown=False)
        return 3

    # Empty / whitespace-only target → text_too_short (below any usable floor).
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
            "floor; the cohesiveness estimate is unstable on short text "
            "(register/length-dependent) — reported but not over-claimed"
        )

    try:
        results = audit_tocsin(
            target_text,
            n_perturbations=args.n_perturbations,
            deletion_fraction=args.deletion_fraction,
            seed=args.seed,
        )
    except TocsinInputError as exc:
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), target_words=word_count,
            reason=str(exc), reason_category="bad_input",
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


def _emit(envelope: dict[str, Any], args: argparse.Namespace, *, as_markdown: bool) -> None:
    """Write the envelope (JSON) or a markdown report to --out and/or stdout."""
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


if __name__ == "__main__":
    sys.exit(main())
