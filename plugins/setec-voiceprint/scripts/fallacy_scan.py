#!/usr/bin/env python3
"""fallacy_scan.py — candidate rhetorical-move flags for a short argument.

ArgScope M1 (spec ``specs/26-fallacy-warrant-scan.md``). Segments an argument-
shaped nonfiction passage into paragraphs, asks a pluggable LLM judge
(``fallacy_judge``: mock / manifest / anthropic / openai / gemini) to FLAG
candidate rhetorical moves against the Logic 13-type taxonomy (Jin et al.,
arXiv:2202.13758), and emits, per flag, a verbatim span pointer + a
Flee-the-Flaw implicit-logic reconstruction (arXiv:2406.12402).

POSTURE — load-bearing, non-negotiable
--------------------------------------
This surface flags candidates for a human; it NEVER adjudicates. The output is
``rhetorical_move_flags`` (each ``{candidate_type, paragraph_index, span_text,
reconstruction}``) + a ``candidate_pattern_tally`` rollup — the "candidate"/
"flag" framing is carried in the field NAMES, not just prose. There is NO
aggregate, NO soundness/quality score, NO "bad argument" / "fallacious" verdict,
and the results dict carries no ``fallacy_*`` key. A flagged move is frequently
legitimate in context; the operator decides. Ships ``uncalibrated``.

CLI
---
    python3 plugins/setec-voiceprint/scripts/fallacy_scan.py TARGET \\
        [--judge {mock,manifest,anthropic,openai,gemini}] \\
        [--judge-manifest PATH] [--judge-model NAME] \\
        [--expect-fingerprint SHA256] [--json] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_error_output, build_output  # type: ignore
from fallacy_judge import (  # type: ignore
    FALLACY_TYPES,
    JudgeError,
    build_judge,
    fingerprint_prompt,
)

TASK_SURFACE = "argument_pattern_scan"
TOOL_NAME = "fallacy_scan"
SCRIPT_VERSION = "0.1.0"
METHOD_VERSION = "fallacy_scan_candidate_flags_v1"

# Below HARD_MIN_WORDS there is no argument to scan → bad_input. Below MIN_WORDS
# the scan still runs but carries a soft caveat (judge has little to work with).
HARD_MIN_WORDS = 25
MIN_WORDS = 120

DEFAULT_LICENSES = (
    "Flags CANDIDATE rhetorical moves in an argument-shaped nonfiction passage: "
    "for each span where a named pattern from the Logic 13-type fallacy taxonomy "
    "(Jin et al. 2022) may be operating, reports the candidate type, a verbatim "
    "span pointer, and a Flee-the-Flaw implicit-logic reconstruction explaining "
    "why the judge flagged it. The flags are judge-derived priors for a human "
    "reviewer — evidence to examine, never a ruling. Labels come from a pluggable "
    "LLM judge; read judge.judge_identity for provenance."
)

DEFAULT_DOES_NOT_LICENSE = (
    "Does NOT license any 'this is a fallacy', 'the argument is fallacious / "
    "unsound / weak / bad', or soundness / quality determination — and emits no "
    "such label, score, or aggregate. A flagged move may be entirely legitimate "
    "in context (an appeal to authority is often valid; a slippery-slope can be a "
    "sound causal chain); the candidate_pattern_tally is a convenience rollup of "
    "the flags, NOT a fallacy count or a verdict. Ships `uncalibrated`: there is "
    "no shipped threshold and no operating point — the LLM judge is a prior for "
    "human review, never a standalone fallacy detector. Judge labels are only as "
    "reliable as the judge (read judge.judge_identity); a `mock` judge is a test "
    "stub. The taxonomy and span pointers do not substitute for a human reading "
    "of the argument in context."
)

_WORD_RE = re.compile(r"[A-Za-z']+")
# Light, honest register heuristic: argument-shaped prose tends to carry
# inferential connectives. Their absence is a SOFT caveat, never a hard abstain
# (there is no register classifier; see spec — the fabricated --register gate was
# removed in review).
_ARGUMENT_MARKERS = re.compile(
    r"\b(because|therefore|thus|hence|since|however|moreover|furthermore|"
    r"consequently|nevertheless|whereas|although|so that|in conclusion|"
    r"for example|on the other hand|it follows)\b",
    re.IGNORECASE,
)


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def split_paragraphs(text: str) -> list[str]:
    """Split on blank lines; strip; drop empties. The judge flags exactly these
    paragraphs (span paragraph_index aligns by position)."""
    parts = re.split(r"\n\s*\n", text.strip())
    return [p.strip() for p in parts if p.strip()]


def register_warnings(text: str, n_words: int, n_paragraphs: int) -> list[str]:
    """Soft caveats (NOT abstention): a short passage or one with no inferential
    connectives may not be argument-shaped — the flags are then low-confidence."""
    out: list[str] = []
    if n_words < MIN_WORDS:
        out.append(
            f"Passage is short ({n_words} words, below ~{MIN_WORDS}); a candidate "
            f"scan over so little argument is low-confidence."
        )
    if not _ARGUMENT_MARKERS.search(text):
        out.append(
            "No inferential connectives (because/therefore/however/…) detected — "
            "the passage may not be argument-shaped nonfiction; treat any flags as "
            "low-confidence and check the register."
        )
    return out


def candidate_pattern_tally(flags: list[dict[str, Any]]) -> dict[str, int]:
    """A convenience rollup of the flags by candidate_type (present types only).
    NOT a fallacy count, NOT a verdict — a count of FLAGS raised for review."""
    return dict(Counter(f["candidate_type"] for f in flags))


def build_results(
    *,
    flags: list[dict[str, Any]],
    judge_dict: dict[str, Any],
    n_paragraphs: int,
    n_words: int,
    reg_warnings: list[str],
    prompt_fp: str,
) -> dict[str, Any]:
    return {
        "method_version": METHOD_VERSION,
        # The candidate flags + their rollup — the whole deliverable. No aggregate.
        "rhetorical_move_flags": flags,
        "candidate_pattern_tally": candidate_pattern_tally(flags),
        "n_flags": len(flags),
        "n_paragraphs": n_paragraphs,
        "n_words": n_words,
        "register_warnings": reg_warnings,
        "calibration_status": "uncalibrated",
        "taxonomy": "logic_13_fallacy_types",
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
            "Judge backend is `manifest` — the candidate flags are only as good "
            "as whatever produced the manifest, which this surface cannot verify."
        )
    caveats.append(
        "Flags are CANDIDATE rhetorical moves for human review, not a verdict. "
        "Ships `uncalibrated`: no soundness / quality / 'bad argument' label, "
        "score, or aggregate is emitted. A flagged move may be legitimate in "
        "context."
    )

    license_block = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=licenses_text,
        does_not_license=does_not_license_text,
        comparison_set={
            "taxonomy": (
                "Logic 13-type fallacy taxonomy (Jin et al. 2022, "
                "'Logical Fallacy Detection', arXiv:2202.13758)"
            ),
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
            "Jin, Lalwani, Vaidhya, Shen, Ding, Lyu, Sachan, Mihalcea & Schölkopf "
            "2022, 'Logical Fallacy Detection' (arXiv:2202.13758)",
            "Hong, Sung, et al. 2024, 'Flee the Flaw' (arXiv:2406.12402)",
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
    flags = r.get("rhetorical_move_flags", [])
    lines = [
        "# Candidate rhetorical-move flags",
        "",
        "> **Not a verdict.** These are CANDIDATE moves a human editor should "
        "examine — each may be entirely legitimate in context. No soundness or "
        "quality judgment is made.",
        "",
        f"- **Judge:** `{r['judge']['judge_identity'].get('kind')}` "
        f"({r['judge']['judge_identity'].get('model') or '—'})",
        f"- **Paragraphs:** {r['n_paragraphs']} · **Words:** {r['n_words']} · "
        f"**Flags:** {r['n_flags']} · **Calibration:** `{r['calibration_status']}`",
        "",
        "## Candidate-pattern tally (flags raised, not a fallacy count)",
        "",
    ]
    tally = r.get("candidate_pattern_tally", {})
    if tally:
        for k, v in sorted(tally.items()):
            lines.append(f"- `{k}`: {v}")
    else:
        lines.append("- (no candidate moves flagged)")
    lines.append("")
    lines.append("## Flags")
    lines.append("")
    if not flags:
        lines.append("(none)")
    for f in flags:
        lines.append(
            f"- **`{f['candidate_type']}`** — ¶{f['paragraph_index']}: "
            f"“{f['span_text']}”"
        )
        if f.get("reconstruction"):
            lines.append(f"  - _why flagged:_ {f['reconstruction']}")
    if r.get("register_warnings"):
        lines.append("")
        lines.append("## Register caveats")
        lines.append("")
        for w in r["register_warnings"]:
            lines.append(f"- {w}")
    return "\n".join(lines) + "\n"


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
        description="Flag candidate rhetorical moves in an argument (no verdict).",
    )
    p.add_argument("target", type=Path, help="UTF-8 prose file to scan")
    p.add_argument("--judge", required=True,
                   choices=["mock", "manifest", "anthropic", "openai", "gemini"],
                   help="judge backend (REQUIRED — no default). `mock` is a deterministic TEST "
                        "stub that FABRICATES findings; choose it only for tests/CI, never for a "
                        "real scan. Real scans use a manifest or an API judge.")
    p.add_argument("--judge-manifest", type=Path, default=None,
                   help="path to a precomputed judge manifest (--judge manifest)")
    p.add_argument("--judge-model", default=None,
                   help="model id for an API judge (anthropic/openai/gemini)")
    p.add_argument("--judge-temperature", type=float, default=0.0)
    p.add_argument("--judge-max-tokens", type=int, default=4096)
    p.add_argument("--expect-fingerprint", default=None,
                   help="abstain unless the judge prompt fingerprint matches this "
                        "(drift gate: any operator band is bound to a fingerprint)")
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


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    target_path: Path = args.target

    # Drift gate (c): an operator-supplied expected fingerprint that no longer
    # matches THIS surface's prompt means any band keyed to it is invalid → abstain.
    current_fp = fingerprint_prompt()
    out_json = (args.out if args.out is not None
                else target_path.with_suffix(target_path.suffix + ".fallacy_scan.json"))
    out_md = (args.out_md if args.out_md is not None
              else target_path.with_suffix(target_path.suffix + ".fallacy_scan.md"))

    if args.expect_fingerprint and args.expect_fingerprint != current_fp:
        env = _error_envelope(
            f"judge prompt fingerprint drift: expected {args.expect_fingerprint}, "
            f"current {current_fp}. Any operator band bound to the old fingerprint "
            f"is invalid for this prompt; re-calibrate.",
            "bad_input", target_path,
        )
        return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)

    try:
        text = target_path.read_text(encoding="utf-8")
    except OSError as exc:
        env = _error_envelope(f"cannot read target: {exc}", "bad_input", target_path)
        return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)

    n_words = count_words(text)
    if n_words < HARD_MIN_WORDS:
        env = _error_envelope(
            f"target too short ({n_words} words, need >= {HARD_MIN_WORDS}) — no "
            f"argument to scan.", "bad_input", target_path, n_words,
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
        flags=judge_result.values["flags"],
        judge_dict=judge_result.to_dict(),
        n_paragraphs=len(paragraphs),
        n_words=n_words,
        reg_warnings=register_warnings(text, n_words, len(paragraphs)),
        prompt_fp=current_fp,
    )
    envelope = compose_envelope(
        target_path=target_path, target_words=n_words, results=results,
        licenses_text=args.licenses, does_not_license_text=args.does_not_license,
    )
    return _emit(envelope, out_path=out_json, md_path=out_md, to_stdout=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
