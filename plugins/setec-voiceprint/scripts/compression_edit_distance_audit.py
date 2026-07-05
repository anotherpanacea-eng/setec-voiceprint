#!/usr/bin/env python3
"""compression_edit_distance_audit.py — paired-input mechanical edit-magnitude
(spec: compression_edit_distance surface, literature_anchored, stdlib).

A **fully mechanical, deterministic, paired-input** edit-magnitude metric. Given
BOTH a pre-edit draft (``--reference``) and the post-edit version (``TARGET``), it
measures the **informational edit-distance** between them via LZ-family (LZMA2)
compression: how many additional compressed bits it costs to encode the edited
text GIVEN the original. It answers *"how much editing separates these two texts?"*
— NOT "is this text AI-edited?" (that is the single-input, model-based
``edit_magnitude`` surface, spec 13) and NOT "who edited it?" (attribution —
refused).

Anchor: *Assessing Human Editing Effort on LLM-Generated Texts via
Compression-Based Edit Distance* (Devatine & Abraham), **arXiv:2412.17321**
(23 Dec 2024). The paper shows an LZ77 compression edit-distance is **highly
correlated with real human edit time/effort**, is **linear**, and needs **no
model** — it releases code + data under CC-BY-4.0
(github.com/NDV-tiime/CompressionDistance). This module **reimplements** the method
(it is not copyrightable; the released code is a cited reference, not vendored).
The paper's edit-time correlation is `[UNVERIFIED on SETEC's corpus]` — it is a
literature anchor, never a SETEC-measured result (mirror spec 32-gec).

WHY A NEW SURFACE (not an arm of edit_magnitude)
================================================
Spec 13's ``edit_magnitude`` is the *single-input, model-based* case (a RoBERTa
regressor over ONE text, GPU/corpus-gated, ``heuristic`` and blocked). This is the
*paired-input, mechanical* case: the operator HAS both texts, so no model is
needed — the edit magnitude is a direct, glass-box measurement. The input
contract differs fundamentally (paired vs single), so this is its OWN surface with
its own enum string, labels entry, and ``id: compression_edit_distance_audit``.

METHOD (pinned — the one build-time decision, resolved here)
============================================================
DECISION 1 — **stdlib ``lzma`` (raw LZMA2, an LZ77-family factorization), NOT
``zlib``/DEFLATE and NOT a bespoke pure-Python LZ77.** The first build used raw
DEFLATE (``wbits=-15``) — **rejected on review for its 32 KiB sliding window**:
back-references cannot reach further than 32,768 bytes, so for manuscript-scale
inputs (the fleet's primary case — a draft chapter routinely exceeds 32 KiB) the
target text cannot "see" most of the reference and the metric silently inverts
(measured: a verbatim 12 KB excerpt of a 120 KB reference scored
``distance_normalized ≈ 0.90`` — "heavy edit" — under DEFLATE, vs ``≈ 0.01``
under LZMA). The paper's own implementation searches the full prefix, so a
windowed compressor is a *method deviation*, not an optimization. LZMA2 at
``preset 9 | PRESET_EXTREME`` carries a **64 MiB dictionary** — effectively
unbounded for any manuscript — while remaining stdlib, deterministic for the
pinned filter chain, and an LZ match-factorization (the same family the paper's
method rests on). **Raw format** (``FORMAT_RAW``) so the stream carries **no xz
container, no header, no checksum** — the byte count is a pure function of the
input. (Guarded by ``test_long_reference_exceeds_deflate_window`` — the pair that
DEFLATE fails.)

DECISION 2 — **the paper's DIRECTIONAL compression edit-distance, NOT symmetric
NCD.** The metric is::

    C(s)              = len(raw-LZMA2(s))                 # compressed size in bytes
    distance_raw      = max(0, C(reference + target) - C(reference))
    distance_normalized = distance_raw / C(target)        # 0.0 if C(target)==0
    # (the max(0,·) clamp: the combined-stream parse can compress the reference
    #  prefix marginally smaller than alone — a small negative artifact, clamped
    #  and disclosed via ``clamped_negative_artifact``; Codex P2, PR #298)

``distance_raw`` is the **incremental** compressed cost of appending the edited
text to the original: the informational content of ``target`` that the original
does NOT already explain. A near-copy compresses almost entirely against the
reference already present in the window (small ``distance_raw``); an unrelated
rewrite shares little and pays close to its full standalone cost (large
``distance_raw``). ``distance_normalized`` expresses that as a fraction of the
edited text's own standalone compressed cost, so it is comparable across texts of
different length.

This directional form matches the paper's **edit-EFFORT** semantics (effort to
produce the post-edit text GIVEN the pre-edit draft — inherently directional,
pre→post) better than symmetric NCD, which is a clustering *distance metric*. The
choice is asserted in a unit test (``test_metric_is_directional_not_ncd``) so a
silent switch to NCD is caught.

NOTE on the identity floor: appending an *identical* copy still costs a few bits to
signal the long back-reference, so ``distance_raw`` for ``ref == target`` is a
small positive integer, not exactly 0. This is an honest property of compression
distance (not a bug); the metric is descriptive, and the value is small relative to
any real edit. It is pinned in ``test_identical_pair_is_near_zero``.

POSTURE (no verdict, paired-input load-bearing)
===============================================
Descriptive only: the raw + normalized distance and the two compressed/byte sizes.
**No "% AI-edited", no dosage claim, no provenance/authorship claim** — the
``ClaimLicense`` refuses all three. It licenses only "the informational
edit-distance between the two supplied texts." The paired input is load-bearing:
with no ``--reference`` the metric is meaningless, so the CLI **fails loud**
(nonzero exit, message before any JSON) rather than degrade to a single-document
mode — that case is spec 13's, and silently colliding with it would be a posture
leak. ``calibration_status: literature_anchored`` (no shipped model/calibration →
no ``corpus_provenance``, consistent with spec 13).

CLI:

    python3 scripts/compression_edit_distance_audit.py TARGET --reference PRE_PATH \
        [--json] [--out PATH]

Both ``TARGET`` and ``--reference`` are REQUIRED file paths.
"""

from __future__ import annotations

import argparse
import json
import lzma
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore  # noqa: E402
from output_schema import (  # noqa: E402
    build_error_output,
    build_output,
)
from stylometry_core import word_tokens  # type: ignore  # noqa: E402

# NOTE (separation guard): this module imports NOTHING from the SETEC fitness /
# selection / scoring family. The distance is a read-only evidence value reported
# to the operator, never a selection signal.

TASK_SURFACE = "compression_edit_distance"
TOOL_NAME = "compression_edit_distance_audit"
SCRIPT_VERSION = "1.0"

METRIC_NAME = "lzma_compression_edit_distance"
NORMALIZATION_NAME = "directional_over_target_compressed_size"

# The exact message the CLI prints (before any JSON) when --reference is absent.
# Pinned as a module constant so a test asserts the fail-loud contract verbatim.
NO_REFERENCE_MESSAGE = (
    "error: --reference is required (paired-input only; this is not a "
    "single-document detector)"
)

# Word floor below which the compressed byte counts are noisy at a small
# denominator (a handful of words compress to near the compressor's minimum, so small
# integer differences dominate). The surface WARNS rather than refuses — the value
# is still reported, never over-claimed. (Mirrors the gecscore length-floor
# posture; not a calibration threshold.)
LENGTH_FLOOR_WORDS = 50


# ----------------------------------------------------------------------
# Compression / distance math (stdlib lzma, deterministic).
# ----------------------------------------------------------------------


# The pinned LZMA2 filter chain: preset 9 | EXTREME carries a 64 MiB dictionary,
# so back-references span the WHOLE reference for any manuscript-scale input
# (DEFLATE's 32 KiB window was the first build's silent failure mode — see
# DECISION 1). FORMAT_RAW = no container/header/checksum bytes.
LZMA_FILTERS = [{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME}]


def compressed_size(data: bytes) -> int:
    """``C(s)`` — the raw-LZMA2 compressed size of ``data`` in bytes.

    Pinned to the module-level ``LZMA_FILTERS`` chain (LZMA2, preset 9 | EXTREME →
    64 MiB dictionary, full-reference back-reference reach) and **raw format**
    (``FORMAT_RAW`` — no xz container, no header, no checksum), so the count is a
    pure, deterministic function of the input bytes for the pinned chain. LZMA2's
    match-finder is an LZ77-family factorization — the same family the paper's
    compression edit-distance rests on, with the full-prefix reach the paper's own
    implementation has."""
    return len(lzma.compress(data, format=lzma.FORMAT_RAW, filters=LZMA_FILTERS))


def compression_edit_distance(reference: str, target: str) -> dict[str, Any]:
    """The paper's DIRECTIONAL LZ77 compression edit-distance between a pre-edit
    ``reference`` and a post-edit ``target`` (arXiv:2412.17321), reimplemented on
    stdlib ``lzma`` (raw LZMA2, 64 MiB dictionary — full-reference reach).

    Returns a dict with:

    - ``distance_raw`` (int) = ``C(reference + target) - C(reference)`` — the
      incremental compressed cost of encoding ``target`` GIVEN ``reference``.
    - ``distance_normalized`` (float) = ``distance_raw / C(target)`` (0.0 when
      ``C(target) == 0``) — that cost as a fraction of ``target``'s standalone
      compressed size, so it is comparable across text lengths.
    - ``reference_bytes`` / ``target_bytes`` (int) = the raw UTF-8 byte lengths.
    - ``metric`` / ``normalization`` — the declared method strings.

    Deterministic: the same pair always yields the same values."""
    ref_bytes = reference.encode("utf-8")
    tgt_bytes = target.encode("utf-8")

    c_reference = compressed_size(ref_bytes)
    c_target = compressed_size(tgt_bytes)
    c_both = compressed_size(ref_bytes + tgt_bytes)

    # Non-negativity clamp (Codex P2, PR #298): the incremental cost is NOT
    # guaranteed >= 0 — LZMA's optimal parse for the combined stream can make the
    # reference prefix compress a byte or two SMALLER than alone (observed:
    # C(ref)=45, C(ref+tgt)=44 on a 10-word/1-word pair). A negative value is an
    # encoder-parse artifact, not "negative edit effort", and would violate the
    # documented [0, inf) contract — clamp to 0 and DISCLOSE via
    # ``clamped_negative_artifact`` (the identity-floor's sibling honesty note).
    raw = c_both - c_reference
    clamped = raw < 0
    distance_raw = max(0, raw)
    distance_normalized = (distance_raw / c_target) if c_target else 0.0

    return {
        "distance_raw": float(distance_raw),
        "distance_normalized": float(distance_normalized),
        "clamped_negative_artifact": clamped,
        "reference_bytes": len(ref_bytes),
        "target_bytes": len(tgt_bytes),
        "metric": METRIC_NAME,
        "normalization": NORMALIZATION_NAME,
        # Provenance of the compressed sizes the two headline numbers derive from —
        # useful for an operator sanity-checking the directional formula, never a
        # verdict.
        "compressed_sizes": {
            "reference": c_reference,
            "target": c_target,
            "reference_plus_target": c_both,
        },
    }


# ----------------------------------------------------------------------
# Audit (paired input).
# ----------------------------------------------------------------------


class CompressionInputError(ValueError):
    """Raised on an unusable input (no word tokens in a text). The CLI maps this to
    a structured ``build_error_output`` envelope, never a traceback."""


def audit_compression_edit_distance(reference: str, target: str) -> dict[str, Any]:
    """Compute the compression edit-distance ``results`` payload for
    ``build_output``. Both texts must contain at least one word token.

    Raises :class:`CompressionInputError` when either text has no word tokens."""
    if not word_tokens(reference):
        raise CompressionInputError("reference has no countable word tokens")
    if not word_tokens(target):
        raise CompressionInputError("target has no countable word tokens")

    results = compression_edit_distance(reference, target)
    results["assumptions"] = {
        "method": (
            "directional LZ-compression edit-distance "
            "(arXiv:2412.17321, reimplemented on stdlib lzma): "
            "distance_raw = C(reference + target) - C(reference), the incremental "
            "compressed cost of the edited text GIVEN the original; "
            "C(s) = len(raw-LZMA2(s)) at preset 9|EXTREME, FORMAT_RAW "
            "(deterministic for the pinned filter chain, container/header-free)"
        ),
        "window": (
            "the LZMA2 dictionary at preset 9 is 64 MiB, so back-references span "
            "the whole reference for any manuscript-scale input (DEFLATE's 32 KiB "
            "window was rejected: beyond it a verbatim excerpt scores as a heavy "
            "edit)"
        ),
        "normalization": (
            "distance_raw / C(target) — the incremental cost as a fraction of the "
            "target's standalone compressed size, comparable across text lengths"
        ),
        "directional": (
            "measures edit EFFORT pre->post (reference->target), matching the "
            "paper's edit-time semantics; NOT symmetric NCD (a clustering distance)"
        ),
        "non_negativity_clamp": (
            "distance_raw is clamped at 0: LZMA's combined-stream parse can make "
            "the reference prefix compress marginally smaller than alone, yielding "
            "a small negative artifact (never negative edit effort); a clamped "
            "value is disclosed via clamped_negative_artifact=true"
        ),
        "identity_floor": (
            "an identical target still costs a few bits to signal the long "
            "back-reference, so distance_raw for reference==target is a small "
            "positive integer, not exactly 0 — an honest property of compression "
            "distance, not a bug"
        ),
        "literature_anchor": (
            "the paper's edit-time correlation is [UNVERIFIED on SETEC's corpus] — "
            "a literature anchor, never a SETEC-measured result"
        ),
    }
    return results


# ----------------------------------------------------------------------
# Claim license (refuses % AI-edited, dosage, provenance/authorship).
# ----------------------------------------------------------------------

DEFAULT_LICENSES = (
    "the informational edit-distance between the two supplied texts as a "
    "directional LZ-compression edit-distance (arXiv:2412.17321, stdlib raw "
    "LZMA2, 64 MiB dictionary). It "
    "reports distance_raw = max(0, C(reference + target) - C(reference)) (the incremental "
    "compressed bits to encode the edited target GIVEN the original) and "
    "distance_normalized = distance_raw / C(target), plus the two input byte "
    "lengths and the compressed sizes they derive from. A small distance means the "
    "target is largely a near-copy of the reference (little editing separates "
    "them); a large distance means they share little (heavy editing / unrelated "
    "content). It is a mechanical, deterministic MEASUREMENT of how much editing "
    "separates two SPECIFIC texts the operator supplied — not a verdict."
)

DEFAULT_DOES_NOT_LICENSE = (
    "any absolute '% AI-edited' figure, any dosage / amount-of-AI-involvement "
    "claim, and any provenance or authorship inference (who wrote or edited "
    "either text). It does NOT decide whether the target is AI-edited, "
    "AI-generated, or human — that single-input question is the model-based "
    "edit_magnitude surface (spec 13), not this one. There is no is_ai / is_human "
    "/ label / verdict / decision / percent_ai key. The distance is meaningful "
    "ONLY as a paired pre/post measurement: this is NOT a single-document detector "
    "and the CLI refuses (fails loud) when no --reference is supplied rather than "
    "degrade to one. The value does NOT localize edits per sentence and does NOT "
    "generalize across corpora (a distance from one pre/post pair licenses no "
    "cross-corpus claim). The paper's edit-time correlation (arXiv:2412.17321) is "
    "[UNVERIFIED on SETEC's corpus] — a literature anchor, not a SETEC-measured "
    "result; no calibration corpus or operating point is shipped "
    "(calibration_status: literature_anchored, NOT calibrated)."
)


def _claim_license(results: dict[str, Any]) -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=DEFAULT_LICENSES,
        does_not_license=DEFAULT_DOES_NOT_LICENSE,
        comparison_set={
            "mode": "paired_pre_post_uncalibrated",
            "metric": results.get("metric"),
            "normalization": results.get("normalization"),
        },
        additional_caveats=[
            "Paired-input is LOAD-BEARING: the metric is meaningless without a "
            "genuine pre/post pair; the CLI fails loud (nonzero exit) when "
            "--reference is absent rather than degrade to a single-document mode "
            "(that is spec 13's edit_magnitude surface).",
            "Uncalibrated — literature_anchored, no verdict, no shipped operating "
            "point. The paper's edit-time correlation is [UNVERIFIED on SETEC's "
            "corpus].",
            "Directional: distance_raw = C(reference+target) - C(reference) "
            "measures edit effort pre->post; it is NOT a symmetric distance and "
            "NOT normalized like NCD.",
            "An identical target yields a small positive distance_raw (the "
            "back-reference signalling cost), not exactly 0 — an honest property "
            "of compression distance.",
            "Below the length floor (50 words) the compressed byte counts are "
            "noisy at a small denominator; the surface warns and does not "
            "over-claim.",
            "distance is a read-only EVIDENCE value — it never feeds SETEC "
            "fitness / selection / scoring.",
        ],
        references=[
            "Assessing Human Editing Effort on LLM-Generated Texts via "
            "Compression-Based Edit Distance (Devatine & Abraham), "
            "arXiv:2412.17321 (2024; the edit-time correlation is [UNVERIFIED on "
            "SETEC's corpus]) — https://arxiv.org/abs/2412.17321",
            "reference implementation (CC-BY-4.0, reimplemented not vendored): "
            "https://github.com/NDV-tiime/CompressionDistance",
        ],
    )


def compose_envelope(
    *,
    reference_path: Path | str | None,
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
        baseline={
            "path": str(reference_path) if reference_path is not None else None,
            "role": "reference_pre_edit",
        },
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
    baseline = envelope.get("baseline") or {}
    lines: list[str] = [
        "# Compression Edit-Distance Audit (paired input)",
        "",
        f"- **Reference (pre-edit):** `{baseline.get('path')}`",
        f"- **Target (post-edit):** `{target.get('path')}` "
        f"({target.get('words')} words)",
        f"- **Metric:** `{results.get('metric')}` "
        f"(normalization: `{results.get('normalization')}`)",
        "",
        "## Result",
        "",
        f"**distance_raw:** {results.get('distance_raw')}",
        f"**distance_normalized:** {results.get('distance_normalized'):.6f}",
        f"**reference_bytes:** {results.get('reference_bytes')}  "
        f"**target_bytes:** {results.get('target_bytes')}",
        "",
        "_Directional LZ-compression (raw LZMA2) edit-distance "
        "(arXiv:2412.17321): the incremental compressed cost of the edited text "
        "GIVEN the original. A MEASUREMENT of how much editing separates the two "
        "supplied texts — NOT a '% AI-edited', NOT a verdict. Uncalibrated "
        "(literature_anchored); the paper's edit-time correlation is [UNVERIFIED "
        "on SETEC's corpus]._",
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
        prog=TOOL_NAME,
        description=(
            "Paired-input mechanical edit-magnitude: the directional LZ77 "
            "compression edit-distance (arXiv:2412.17321, stdlib) between a "
            "pre-edit reference and a post-edit target. Descriptive, deterministic, "
            "NO verdict. Paired-input is load-bearing: --reference is required; "
            "this is NOT a single-document detector (that is spec 13's "
            "edit_magnitude)."
        ),
    )
    p.add_argument("target", help="Path to the post-edit (TARGET) text file (UTF-8).")
    p.add_argument(
        "--reference", "--pre", dest="reference", required=True, metavar="PRE_PATH",
        help="Path to the pre-edit (reference / original) text file (UTF-8). "
             "REQUIRED — the metric is a paired pre/post measurement.",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit the JSON envelope instead of a markdown report.",
    )
    p.add_argument("--out", default=None, help="Write output to this path instead of stdout.")
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. ``--reference`` is REQUIRED: with it absent the parser fails
    loud (nonzero exit, the NO_REFERENCE_MESSAGE printed to stderr before any JSON)
    — the surface NEVER degrades to a single-document mode (that is spec 13's
    edit_magnitude). A malformed/unreadable path is the softer failure: a
    structured available:false / bad_input envelope."""
    parser = build_arg_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse already printed its own usage/error to stderr and is exiting
        # nonzero. When the failure is SPECIFICALLY the missing required
        # --reference, print the pinned paired-input message too (before any JSON
        # could be emitted), so the fail-loud contract is explicit and greppable.
        # Gate on --reference's actual absence: a different argparse error (a
        # missing TARGET, an unknown flag) must NOT claim --reference was missing.
        supplied = list(argv) if argv is not None else sys.argv[1:]
        if exc.code != 0 and not any(
            a == "--reference" or a == "--pre"
            or a.startswith("--reference=") or a.startswith("--pre=")
            for a in supplied
        ):
            sys.stderr.write(NO_REFERENCE_MESSAGE + "\n")
        raise

    reference_path = Path(args.reference).expanduser()
    target_path = Path(args.target).expanduser()

    try:
        reference_text = reference_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=f"cannot read --reference: {exc}", reason_category="bad_input",
        )
        _emit(envelope, args, as_markdown=False)
        return 3

    try:
        target_text = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=f"cannot read TARGET: {exc}", reason_category="bad_input",
        )
        _emit(envelope, args, as_markdown=False)
        return 3

    ref_words = len(word_tokens(reference_text))
    tgt_words = len(word_tokens(target_text))
    if ref_words == 0 or tgt_words == 0:
        which = "reference" if ref_words == 0 else "target"
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), target_words=tgt_words,
            reason=f"{which} has no countable word tokens",
            reason_category="text_too_short",
        )
        _emit(envelope, args, as_markdown=False)
        return 3

    warnings: list[str] = []
    if ref_words < LENGTH_FLOOR_WORDS or tgt_words < LENGTH_FLOOR_WORDS:
        warnings.append(
            f"reference is {ref_words} words / target is {tgt_words} words; below "
            f"the {LENGTH_FLOOR_WORDS}-word floor the compressed byte counts are "
            "noisy at a small denominator — reported but not over-claimed"
        )

    try:
        results = audit_compression_edit_distance(reference_text, target_text)
    except CompressionInputError as exc:
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), target_words=tgt_words,
            reason=str(exc), reason_category="bad_input",
        )
        _emit(envelope, args, as_markdown=False)
        return 3

    envelope = compose_envelope(
        reference_path=reference_path,
        target_path=target_path,
        target_words=tgt_words,
        results=results,
        warnings=warnings or None,
    )
    _emit(envelope, args, as_markdown=not args.json)
    return 0


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


if __name__ == "__main__":
    sys.exit(main())
