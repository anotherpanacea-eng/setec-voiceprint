#!/usr/bin/env python3
"""warrant_probe.py — Toulmin critical-question coverage for an argument.

ArgScope M2 (spec ``specs/26-fallacy-warrant-scan.md``). The sibling of
``fallacy_scan`` under the SAME ``argument_pattern_scan`` surface. Segments an
argument-shaped passage, asks a pluggable judge (``warrant_judge``) to identify
the major claims, and for each reports the COVERAGE of three Toulmin critical
questions — warrant, backing, rebuttal — as present / partial / absent
(Favero et al. 2024, "Critical Questions of Thought", arXiv:2412.15177).

POSTURE — load-bearing, non-negotiable
--------------------------------------
Reports COVERAGE, NEVER soundness. The output is ``warrant_coverage`` (per-claim
critical-question coverage) + a ``coverage_summary`` rollup. There is NO
aggregate, NO soundness / quality / "weak argument" / "unsound" label or score,
and no ``*_score`` key. An ``absent`` warrant is a coverage GAP to examine — many
strong arguments leave a warrant implicit because it is shared with the reader.
Ships ``uncalibrated``.

CLI
---
    python3 plugins/setec-voiceprint/scripts/warrant_probe.py TARGET \\
        [--judge {mock,manifest,anthropic,openai,gemini,agent_host}] \\
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
from warrant_judge import (  # type: ignore
    CQ_STATUSES,
    CRITICAL_QUESTIONS,
    JudgeError,
    build_judge,
    fingerprint_prompt,
)

TASK_SURFACE = "argument_pattern_scan"  # REUSED (sibling of fallacy_scan)
TOOL_NAME = "warrant_probe"
SCRIPT_VERSION = "0.1.0"
METHOD_VERSION = "warrant_probe_cq_coverage_v1"

HARD_MIN_WORDS = 25
MIN_WORDS = 120

DEFAULT_LICENSES = (
    "Reports Toulmin critical-question COVERAGE for an argument-shaped nonfiction "
    "passage: for each major claim a pluggable LLM judge identifies, whether the "
    "warrant (inferential link), the backing for that warrant, and a rebuttal "
    "(counterargument / exception) are present, partial, or absent (Favero et al. "
    "2024). Descriptive coverage for a human reviewer — what questions the text "
    "answers vs leaves open. Labels come from a pluggable LLM judge; read "
    "judge.judge_identity for provenance."
)

DEFAULT_DOES_NOT_LICENSE = (
    "Does NOT license any 'the argument is unsound / weak / bad / fallacious' "
    "determination, nor a soundness / quality label, score, or aggregate — and "
    "emits none. An `absent` warrant, backing, or rebuttal is a COVERAGE GAP to "
    "examine, NOT a flaw verdict: many strong arguments leave a warrant implicit "
    "because it is shared with the reader, and not every claim needs an explicit "
    "rebuttal. The coverage_summary is a descriptive rollup of the per-claim "
    "coverage, never a score. Ships `uncalibrated`: no shipped threshold or "
    "operating point — the LLM judge is a prior for human review, never a "
    "standalone soundness detector (read judge.judge_identity; a `mock` judge is "
    "a test stub). Does not substitute for a human reading the argument in context."
)

_WORD_RE = re.compile(r"[A-Za-z']+")
_ARGUMENT_MARKERS = re.compile(
    r"\b(because|therefore|thus|hence|since|however|moreover|furthermore|"
    r"consequently|nevertheless|whereas|although|so that|in conclusion|"
    r"for example|on the other hand|it follows)\b",
    re.IGNORECASE,
)


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def split_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n", text.strip())
    return [p.strip() for p in parts if p.strip()]


def register_warnings(text: str, n_words: int) -> list[str]:
    out: list[str] = []
    if n_words < MIN_WORDS:
        out.append(
            f"Passage is short ({n_words} words, below ~{MIN_WORDS}); a warrant "
            f"probe over so little argument is low-confidence."
        )
    if not _ARGUMENT_MARKERS.search(text):
        out.append(
            "No inferential connectives (because/therefore/however/…) detected — "
            "the passage may not be argument-shaped nonfiction; treat coverage as "
            "low-confidence and check the register."
        )
    return out


def coverage_summary(claims: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """A descriptive rollup: per critical-question axis, how many claims are
    present / partial / absent. NOT a score, NOT a verdict — a coverage count."""
    summary = {cq: {s: 0 for s in CQ_STATUSES} for cq in CRITICAL_QUESTIONS}
    for c in claims:
        cqs = c.get("critical_questions", {})
        for cq in CRITICAL_QUESTIONS:
            status = cqs.get(cq)
            if status in CQ_STATUSES:
                summary[cq][status] += 1
    return summary


def build_results(
    *,
    claims: list[dict[str, Any]],
    judge_dict: dict[str, Any],
    n_paragraphs: int,
    n_words: int,
    reg_warnings: list[str],
    prompt_fp: str,
) -> dict[str, Any]:
    return {
        "method_version": METHOD_VERSION,
        "warrant_coverage": claims,            # per-claim CQ coverage — the deliverable
        "coverage_summary": coverage_summary(claims),
        "n_claims": len(claims),
        "n_paragraphs": n_paragraphs,
        "n_words": n_words,
        "register_warnings": reg_warnings,
        "calibration_status": "uncalibrated",
        "critical_questions": list(CRITICAL_QUESTIONS),
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
            "Judge backend is `manifest` — the coverage is only as good as "
            "whatever produced the manifest, which this surface cannot verify."
        )
    elif judge_kind == "agent_host":
        caveats.append(
            "Judge backend is `agent_host` — the critical-question coverage was "
            "produced by the HOST runtime's model (see judge.judge_identity.host), "
            "not a pinned API model@revision. The judgment is NON-DETERMINISTIC and "
            "host-version-fluid. The identity is recorded as "
            "agent_host:<host>:<model> so a consumer can assert it is disjoint from "
            "any generator it validates (the consumer's drift gate must enforce "
            "judge model != generator model on holdout/selection surfaces; see "
            "specs/35-host-delegated-judge.md)."
        )
    caveats.append(
        "Reports critical-question COVERAGE for human review, not a verdict. "
        "Ships `uncalibrated`: no soundness / quality / 'unsound' label, score, "
        "or aggregate is emitted. An absent warrant/backing/rebuttal is a gap to "
        "examine, not a flaw."
    )

    license_block = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=licenses_text,
        does_not_license=does_not_license_text,
        comparison_set={
            "critical_question_bank": (
                "Toulmin warrant/backing/rebuttal (Favero et al. 2024, 'Critical "
                "Questions of Thought', arXiv:2412.15177)"
            ),
            "judge_kind": judge_kind,
            "judge_model": (
                results["judge"]["judge_identity"].get("model") or "(unspecified)"
            ),
            # host runtime id for agent_host (the firewall hook: lets a consumer assert
            # judge model != generator model); null for non-delegated backends.
            "judge_host": results["judge"]["judge_identity"].get("host"),
            "prompt_fingerprint_sha256": results["prompt_fingerprint_sha256"],
        },
        length_range_words=(MIN_WORDS, 8000),
        register_match=["argument-shaped nonfiction (op-ed / policy / testimony)"],
        additional_caveats=caveats,
        references=[
            "Favero, Castagna, et al. 2024, 'Critical Questions of Thought' "
            "(arXiv:2412.15177)",
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
    claims = r.get("warrant_coverage", [])
    lines = [
        "# Warrant-coverage probe",
        "",
        "> **Not a verdict.** This reports COVERAGE of the critical questions a "
        "human reviewer would ask — an absent warrant/backing/rebuttal is a gap "
        "to examine, not a flaw. No soundness judgment is made.",
        "",
        f"- **Judge:** `{r['judge']['judge_identity'].get('kind')}` "
        f"({r['judge']['judge_identity'].get('model') or '—'})",
        f"- **Claims:** {r['n_claims']} · **Paragraphs:** {r['n_paragraphs']} · "
        f"**Calibration:** `{r['calibration_status']}`",
        "",
        "## Coverage summary (counts, not a score)",
        "",
    ]
    for cq, counts in r.get("coverage_summary", {}).items():
        parts = ", ".join(f"{s}: {n}" for s, n in counts.items())
        lines.append(f"- `{cq}` — {parts}")
    lines.append("")
    lines.append("## Per-claim coverage")
    lines.append("")
    if not claims:
        lines.append("(no claims identified)")
    for c in claims:
        cqs = c.get("critical_questions", {})
        status = " · ".join(f"{k}={v}" for k, v in cqs.items())
        lines.append(f"- ¶{c['paragraph_index']}: “{c['claim_span']}” — {status}")
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
        description="Toulmin critical-question coverage for an argument (no verdict).",
    )
    p.add_argument("target", type=Path, help="UTF-8 prose file to probe")
    p.add_argument("--judge", required=True,
                   choices=["mock", "manifest", "anthropic", "openai", "gemini", "agent_host"],
                   help="judge backend (REQUIRED — no default). `mock` is a deterministic TEST "
                        "stub that FABRICATES coverage; choose it only for tests/CI, never for a "
                        "real probe. Real probes use a manifest or an API judge.")
    p.add_argument("--judge-manifest", type=Path, default=None)
    p.add_argument("--judge-model", default=None)
    p.add_argument("--judge-temperature", type=float, default=0.0)
    p.add_argument("--judge-max-tokens", type=int, default=4096)
    p.add_argument("--expect-fingerprint", default=None,
                   help="abstain unless the judge prompt fingerprint matches this")
    p.add_argument("--licenses", default=DEFAULT_LICENSES)
    p.add_argument("--does-not-license", default=DEFAULT_DOES_NOT_LICENSE)
    p.add_argument("--json", action="store_true")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--out-md", type=Path, default=None)
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

    current_fp = fingerprint_prompt()
    out_json = (args.out if args.out is not None
                else target_path.with_suffix(target_path.suffix + ".warrant_probe.json"))
    out_md = (args.out_md if args.out_md is not None
              else target_path.with_suffix(target_path.suffix + ".warrant_probe.md"))

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
    except (OSError, UnicodeDecodeError) as exc:        # invalid UTF-8 is bad input, not a crash
        env = _error_envelope(f"cannot read target: {exc}", "bad_input", target_path)
        return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)

    n_words = count_words(text)
    if n_words < HARD_MIN_WORDS:
        env = _error_envelope(
            f"target too short ({n_words} words, need >= {HARD_MIN_WORDS}) — no "
            f"argument to probe.", "bad_input", target_path, n_words,
        )
        return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)

    paragraphs = split_paragraphs(text)

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
        claims=judge_result.values["claims"],
        judge_dict=judge_result.to_dict(),
        n_paragraphs=len(paragraphs),
        n_words=n_words,
        reg_warnings=register_warnings(text, n_words),
        prompt_fp=current_fp,
    )
    envelope = compose_envelope(
        target_path=target_path, target_words=n_words, results=results,
        licenses_text=args.licenses, does_not_license_text=args.does_not_license,
    )
    return _emit(envelope, out_path=out_json, md_path=out_md, to_stdout=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
