#!/usr/bin/env python3
"""argquality_dimension_profile.py — a theory-based argument-quality dimension
PROFILE (Wachsmuth / GAQCorpus rhetoric / logic / dialectic) for an argument.

ArgScope M1 (spec ``specs/30-gaqcorpus-argquality.md``). Segments an argument-
shaped nonfiction passage into paragraphs, asks a pluggable LLM judge
(``argquality_judge``: mock / manifest / anthropic / openai / gemini) to place,
per the three top-tier Wachsmuth dimensions, a COARSE DESCRIPTIVE band
(``lower`` / ``mid`` / ``higher`` / ``null``) against the GAQCorpus rating
distribution — with paragraph-anchored verbatim span pointers and a per-dimension
rationale. Rooted in Lauscher, Ng, Napoles & Tetreault 2020, *Rhetoric, Logic,
and Dialectic* (arXiv:2006.00843).

POSTURE — load-bearing, non-negotiable
--------------------------------------
This surface emits a PROFILE the human reads, NOT a verdict. The output is
``dimensions`` (each ``{band, evidence_spans, basis}``) + a string
``distribution_reference`` — the "band"/"distributional-placement" framing is
carried in the field NAMES. There is **NO** aggregate, **NO** ``overall`` band,
**NO** ``quality_score`` / ``score`` / ``verdict`` / ``mean_band`` / any
cross-dimension roll-up, and **NO numeric leaf anywhere under ``dimensions``**
(band/spans/basis are all strings). The three dimensions are computed
INDEPENDENTLY and NEVER summed — no function in this module collapses the profile
to one value. ``null`` is a first-class band (the judge declined), NEVER coerced
to ``lower``. A ``lower`` band is frequently appropriate in context. Ships
``uncalibrated``, unconditionally. No band is an AI-vs-human tell.

The honest M1 pitch: **the surface + its posture guards + its CI contract,
exercised entirely through the deterministic ``mock`` judge (torch-free, no API,
no GPU).** There is NO model-free stdlib band computation — the bands come from a
judge; the ``mock`` is a STUB, never an inference source. The real-judge profile
is M2 (model-gated, never GPU-gated).

CLI
---
    python3 plugins/setec-voiceprint/scripts/argquality_dimension_profile.py TARGET \\
        --judge {mock,manifest,anthropic,openai,gemini} \\
        [--judge-manifest PATH] [--judge-model NAME] \\
        [--expect-fingerprint SHA256] [--json] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_error_output, build_output  # type: ignore
from argquality_judge import (  # type: ignore
    DIMENSIONS,
    DISTRIBUTION_REFERENCE,
    JudgeError,
    build_judge,
    fingerprint_prompt,
)

TASK_SURFACE = "argquality_dimension_profile"
TOOL_NAME = "argquality_dimension_profile"
SCRIPT_VERSION = "0.1.0"
METHOD_VERSION = "argquality_dimension_profile_v1"

# Below HARD_MIN_WORDS there is no argument to profile → bad_input. Below
# MIN_WORDS the profile still runs but carries a soft caveat (the judge has
# little to work with). Reuse the ArgScope floor convention (fallacy_scan: 120).
HARD_MIN_WORDS = 25
MIN_WORDS = 120

DEFAULT_LICENSES = (
    "Reports a per-dimension descriptive band (lower / mid / higher, or null) "
    "over the three GAQCorpus / Wachsmuth theory-of-argument-quality dimensions "
    "(logic / rhetoric / dialectic), with paragraph-anchored verbatim span "
    "pointers and a per-dimension rationale, framed against the GAQCorpus rating "
    "distribution — as judge-derived OBSERVATIONS for a human reviewer to "
    "interpret. Bands come from a pluggable LLM judge; read "
    "judge.judge_identity for provenance."
)

DEFAULT_DOES_NOT_LICENSE = (
    "Does NOT license any aggregate 'argument quality' score, any `overall`-"
    "quality band or cross-dimension roll-up, or any 'good / bad / strong / "
    "weak / high-quality / low-quality' argument label — and emits no such "
    "field: there is no overall, score, aggregate, mean_band, or verdict key, "
    "and no numeric leaf under the dimensions block. A band is a DISTRIBUTIONAL "
    "PLACEMENT against the GAQCorpus distribution, not a grade; a `lower` band "
    "is frequently appropriate in context (a one-sided register, a rebuttal, a "
    "polemic). `null` means the judge could not place the dimension, not 'low "
    "quality' — it is never coerced to `lower`. No band is an AI-vs-human tell "
    "(`lower` is not an AI tell, `higher` is not a human tell); the surface "
    "refuses provenance. Ships `uncalibrated`: the GAQCorpus distribution is a "
    "register-bound directional reference (research / legal / policy targets are "
    "`distant`), never a shipped threshold or operating point. The LLM judge is "
    "a prior, not a calibrated quality model (read judge.judge_identity); a "
    "`mock` judge is a test stub, infer nothing from it."
)

_WORD_RE = re.compile(r"[A-Za-z']+")
# Light, honest register heuristic: argument-shaped prose tends to carry
# inferential connectives. Their absence is a SOFT caveat, never a hard abstain
# (there is no register classifier; the fabricated --register gate stays unbuilt,
# per the argument_decision_audit / fallacy_scan precedent).
_ARGUMENT_MARKERS = re.compile(
    r"\b(because|therefore|thus|hence|since|however|moreover|furthermore|"
    r"consequently|nevertheless|whereas|although|so that|in conclusion|"
    r"for example|on the other hand|it follows)\b",
    re.IGNORECASE,
)


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def split_paragraphs(text: str) -> list[str]:
    """Split on blank lines; strip; drop empties. The judge reads exactly these
    paragraphs (evidence_spans anchor into them by verbatim containment)."""
    parts = re.split(r"\n\s*\n", text.strip())
    return [p.strip() for p in parts if p.strip()]


def register_warnings(text: str, n_words: int) -> list[str]:
    """Soft caveats (NOT abstention): a short passage or one with no inferential
    connectives may not be argument-shaped — the bands are then low-confidence."""
    out: list[str] = []
    if n_words < MIN_WORDS:
        out.append(
            f"Passage is short ({n_words} words, below ~{MIN_WORDS}); a dimension "
            f"profile over so little argument is low-confidence."
        )
    if not _ARGUMENT_MARKERS.search(text):
        out.append(
            "No inferential connectives (because/therefore/however/…) detected — "
            "the passage may not be argument-shaped nonfiction; treat any band as "
            "low-confidence and check the register."
        )
    return out


def build_results(
    *,
    dimensions: dict[str, Any],
    judge_dict: dict[str, Any],
    n_paragraphs: int,
    n_words: int,
    reg_warnings: list[str],
    prompt_fp: str,
) -> dict[str, Any]:
    """Assemble the results payload. The WHOLE deliverable is the three
    independent per-dimension bands + their spans + per-dimension basis. There is
    deliberately NO aggregate / overall / score / mean_band / verdict key, and no
    numeric leaf under ``dimensions`` (band/spans/basis are strings)."""
    return {
        "method_version": METHOD_VERSION,
        # The three independent dimension bands — never summed, never rolled up.
        "dimensions": dimensions,
        # A STRING descriptor (no numeric leaf): the band-vs-grade line at the leaf.
        "distribution_reference": DISTRIBUTION_REFERENCE,
        "n_paragraphs": n_paragraphs,
        "n_words": n_words,
        "register_warnings": reg_warnings,
        "calibration_status": "uncalibrated",
        "taxonomy": "wachsmuth_gaqcorpus_rhetoric_logic_dialectic",
        "judge": judge_dict,
        "prompt_fingerprint_sha256": prompt_fp,
    }


def compose_envelope(
    *,
    target_path: Path | None,
    target_words: int,
    results: dict[str, Any],
    licenses_text: str,
    does_not_license_text: str,
) -> dict[str, Any]:
    caveats: list[str] = list(results.get("register_warnings", []))
    judge_kind = results["judge"]["judge_identity"].get("kind")
    if judge_kind == "mock":
        caveats.append(
            "Judge backend is `mock` — a deterministic TEST stub, not a real "
            "reviewer. Do not infer anything about the argument from a mock run."
        )
    elif judge_kind == "manifest":
        caveats.append(
            "Judge backend is `manifest` — the bands are only as good as "
            "whatever produced the manifest, which this surface cannot verify."
        )
    caveats.append(
        "Bands are DESCRIPTIVE distributional placements for human review, not a "
        "verdict. Ships `uncalibrated`: no aggregate 'argument quality' score, "
        "no `overall` band, no good/bad/strong/weak label is emitted. A `lower` "
        "band is frequently appropriate in context; `null` means the judge "
        "declined the dimension (never 'low quality', never coerced to `lower`). "
        "No band is an AI-vs-human tell."
    )

    license_block = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=licenses_text,
        does_not_license=does_not_license_text,
        comparison_set={
            "taxonomy": (
                "Wachsmuth / GAQCorpus rhetoric-logic-dialectic theory dimensions "
                "(Lauscher, Ng, Napoles & Tetreault 2020, 'Rhetoric, Logic, and "
                "Dialectic', arXiv:2006.00843)"
            ),
            "distribution_reference": results["distribution_reference"],
            "judge_kind": judge_kind,
            "judge_model": (
                results["judge"]["judge_identity"].get("model") or "(unspecified)"
            ),
            "prompt_fingerprint_sha256": results["prompt_fingerprint_sha256"],
        },
        length_range_words=(MIN_WORDS, 8000),
        register_match=["argument-shaped nonfiction (op-ed / policy / testimony)"],
        additional_caveats=caveats,
        references=[
            "Lauscher, Ng, Napoles & Tetreault 2020, 'Rhetoric, Logic, and "
            "Dialectic: Advancing Theory-based Argument Quality Assessment in "
            "Natural Language Processing' (arXiv:2006.00843)",
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
    r = envelope["results"]
    dims = r.get("dimensions", {})
    lines = [
        "# Argument-quality dimension profile",
        "",
        "> **Not a verdict.** These are COARSE descriptive bands — distributional "
        "placements against the GAQCorpus rating distribution, not grades. No "
        "aggregate quality score, no `overall` band, no good/bad label is made. "
        "A `lower` band is frequently appropriate in context; `null` means the "
        "judge declined the dimension. The operator adjudicates quality.",
        "",
        f"- **Judge:** `{r['judge']['judge_identity'].get('kind')}` "
        f"({r['judge']['judge_identity'].get('model') or '—'})",
        f"- **Paragraphs:** {r['n_paragraphs']} · **Words:** {r['n_words']} · "
        f"**Calibration:** `{r['calibration_status']}`",
        f"- **Distribution reference:** {r['distribution_reference']}",
        "",
        "## Dimension bands (independent; never summed)",
        "",
    ]
    for d in DIMENSIONS:
        entry = dims.get(d, {})
        band = entry.get("band")
        band_str = f"`{band}`" if band is not None else "`null` (declined)"
        lines.append(f"### {d} — {band_str}")
        if entry.get("basis"):
            lines.append(f"- _why:_ {entry['basis']}")
        spans = entry.get("evidence_spans") or []
        if spans:
            lines.append("- _evidence spans:_")
            for s in spans:
                lines.append(f"  - “{s}”")
        lines.append("")
    if r.get("register_warnings"):
        lines.append("## Register caveats")
        lines.append("")
        for w in r["register_warnings"]:
            lines.append(f"- {w}")
    return "\n".join(lines).rstrip() + "\n"


def _emit(envelope: dict[str, Any], *, out_path: Path, md_path: Path | None,
          to_stdout: bool) -> int:
    try:
        out_path.write_text(json.dumps(envelope, indent=2, default=str),
                            encoding="utf-8")
        if md_path is not None:
            md_path.write_text(render_markdown(envelope), encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write output: {exc}", file=sys.stderr)
        return 1
    if to_stdout:
        print(json.dumps(envelope, indent=2, default=str))
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Profile an argument's three theory-of-quality dimensions "
            "(rhetoric / logic / dialectic) — descriptive bands, no verdict."
        ),
    )
    p.add_argument("target", type=Path, help="UTF-8 prose file to profile")
    p.add_argument("--judge", required=True,
                   choices=["mock", "manifest", "anthropic", "openai", "gemini"],
                   help="judge backend (REQUIRED — no default). `mock` is a deterministic TEST "
                        "stub that FABRICATES bands; choose it only for tests/CI, never for a "
                        "real profile. Real profiles use a manifest or an API judge.")
    p.add_argument("--judge-manifest", type=Path, default=None,
                   help="path to a precomputed judge manifest (--judge manifest)")
    p.add_argument("--judge-model", default=None,
                   help="model id for an API judge (anthropic/openai/gemini)")
    p.add_argument("--judge-temperature", type=float, default=0.0)
    p.add_argument("--judge-max-tokens", type=int, default=4096)
    p.add_argument("--expect-fingerprint", default=None,
                   help="abstain unless the judge prompt fingerprint matches this "
                        "(drift gate: any operator band binding is bound to a fingerprint)")
    p.add_argument("--licenses", default=DEFAULT_LICENSES)
    p.add_argument("--does-not-license", default=DEFAULT_DOES_NOT_LICENSE)
    p.add_argument("--json", action="store_true", help="also print envelope to stdout")
    p.add_argument("--out", type=Path, default=None, help="JSON output path")
    p.add_argument("--out-md", type=Path, default=None, help="Markdown output path")
    return p


def _error_envelope(reason: str, category: str, target: Path | None,
                    words: int = 0) -> dict[str, Any]:
    return build_error_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=target, target_words=words, reason=reason,
        reason_category=category,
    )


def _effective_judge_fingerprint(args: argparse.Namespace, current_fp: str) -> str | None:
    """The prompt fingerprint that PRODUCED the bands. For an API/mock judge that is the current
    code's prompt (``current_fp``); for a ``manifest`` judge it is the fingerprint the manifest was
    generated under (read from its ``judge_identity``), so the drift gate checks the bands' real
    provenance instead of comparing current-vs-current and waving a stale manifest through (Codex P1).
    Returns None when a manifest declares no fingerprint or can't be read — the gate then can't confirm
    the binding (treated as drift); a truly unreadable manifest is surfaced by build_judge."""
    if args.judge == "manifest" and args.judge_manifest is not None:
        try:
            data = json.loads(Path(args.judge_manifest).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        ji = data.get("judge_identity") if isinstance(data, dict) else None
        fp = (ji or {}).get("prompt_fingerprint_sha256") if isinstance(ji, dict) else None
        return fp if isinstance(fp, str) and fp else None
    return current_fp


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    target_path: Path = args.target

    # Drift gate: an operator-supplied expected fingerprint that no longer
    # matches THIS surface's prompt means any band keyed to it is invalid → abstain.
    current_fp = fingerprint_prompt()
    out_json = (args.out if args.out is not None
                else target_path.with_suffix(
                    target_path.suffix + ".argquality_dimension_profile.json"))
    out_md = (args.out_md if args.out_md is not None
              else target_path.with_suffix(
                  target_path.suffix + ".argquality_dimension_profile.md"))

    # Check the operator's expected fingerprint against the prompt that actually PRODUCED the bands —
    # the manifest's own fingerprint for a manifest judge, current_fp for an API/mock judge — so a
    # stale manifest can't pass a current-vs-current comparison (Codex P1).
    effective_fp = _effective_judge_fingerprint(args, current_fp)
    if args.expect_fingerprint and args.expect_fingerprint != effective_fp:
        env = _error_envelope(
            f"judge prompt fingerprint drift: expected {args.expect_fingerprint}, "
            f"judge {effective_fp}. Any operator band bound to the old fingerprint "
            f"is invalid; re-calibrate (or regenerate a stale manifest).",
            "bad_input", target_path,
        )
        return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)

    try:
        text = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:        # invalid UTF-8 is bad input, not a crash
        env = _error_envelope(f"cannot read target: {exc}", "bad_input", target_path)
        return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)

    n_words = count_words(text)
    if n_words < HARD_MIN_WORDS:
        env = _error_envelope(
            f"target too short ({n_words} words, need >= {HARD_MIN_WORDS}) — no "
            f"argument to profile.", "bad_input", target_path, n_words,
        )
        return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)

    paragraphs = split_paragraphs(text)

    # Judge construction: a missing SDK is a missing dependency; a missing
    # model / manifest is bad setup input. Both are fail-loud, never a result.
    try:
        judge = build_judge(
            args.judge, manifest_path=args.judge_manifest, model=args.judge_model,
            temperature=args.judge_temperature, max_tokens=args.judge_max_tokens,
        )
    except JudgeError as exc:
        category = "missing_dependency" if "SDK" in str(exc) else "bad_input"
        env = _error_envelope(f"judge construction failed: {exc}", category,
                              target_path, n_words)
        return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)

    try:
        judge_result = judge(paragraphs)
    except JudgeError as exc:
        print(f"error: judge execution failed: {exc}", file=sys.stderr)
        return 3

    results = build_results(
        dimensions=judge_result.values["dimensions"],
        judge_dict=judge_result.to_dict(),
        n_paragraphs=len(paragraphs),
        n_words=n_words,
        reg_warnings=register_warnings(text, n_words),
        # Report the EFFECTIVE fingerprint that produced the bands (== the drift-gate value): the
        # manifest's own for a manifest judge — None when the manifest declared none — and current_fp
        # for API/mock. The previous `... or current_fp` REBOUND a fingerprint-less manifest to the
        # current code's fingerprint, falsely claiming provenance it doesn't have (Codex P1).
        prompt_fp=effective_fp,
    )
    envelope = compose_envelope(
        target_path=target_path, target_words=n_words, results=results,
        licenses_text=args.licenses, does_not_license_text=args.does_not_license,
    )
    return _emit(envelope, out_path=out_json, md_path=out_md, to_stdout=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
