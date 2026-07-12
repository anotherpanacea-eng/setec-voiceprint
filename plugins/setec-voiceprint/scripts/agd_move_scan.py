#!/usr/bin/env python3
"""agd_move_scan.py — located AGD move observations for an argument-shaped passage.

R3B producer seam (fleet spec ``setec-scratch/apo-argument-r3b-agd-seam``; consumer =
apodictic's AGD Move Audit companion). Segments the passage, asks a pluggable judge
(``agd_move_scan_judge``) to inventory the performative argument moves — ASSURING /
GUARDING / DISCOUNTING (Sinnott-Armstrong & Fogelin 9e, ch. 3) — and emits them as
LOCATED OBSERVATIONS: family + verbatim span + paragraph index + surface cue (or
null, the first-class cue-free case).

POSTURE — load-bearing, non-negotiable
--------------------------------------
OBSERVATIONS ONLY. All three families are LEGITIMATE moves; an observation is
never a finding, flaw, code, or score. This surface NEVER adjudicates whether a
move smuggles, never assigns any apodictic diagnostic code, and never aggregates
— the results carry NO counts or tallies (a consumer derives any tally from the
observations list itself; §1a's refusal is mechanical). The consumer audit
(apodictic, R4A ADR D5: the producer observes; the consumer alone assigns codes)
challenges each move and owns every diagnosis. Ships ``heuristic``.

CLI
---
    python3 plugins/setec-voiceprint/scripts/agd_move_scan.py TARGET \\
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

from agd_move_scan_judge import (  # type: ignore
    FAMILIES,
    JudgeError,
    build_judge,
    fingerprint_prompt,
)
from claim_license import ClaimLicense  # type: ignore
from output_schema import build_error_output, build_output  # type: ignore

TASK_SURFACE = "agd_move_scan"
TOOL_NAME = "agd_move_scan"
SCRIPT_VERSION = "0.1.0"
METHOD_VERSION = "agd_move_scan_v1"

HARD_MIN_WORDS = 25
MIN_WORDS = 120

DEFAULT_LICENSES = (
    "Reports LOCATED, verbatim-anchored candidate AGD move observations for an "
    "argument-shaped nonfiction passage: each performative move a pluggable LLM "
    "judge identifies — ASSURING (authority/certainty in place of support), "
    "GUARDING (a claim weakened to shrink its commitment), DISCOUNTING (an "
    "objection anticipated and set aside) — with its family, verbatim span, "
    "paragraph index, and surface cue (null = cue-free). Heuristic, uncalibrated "
    "location data for downstream audit consumption (apodictic's AGD Move Audit "
    "challenges each move and owns every diagnosis). Labels come from a pluggable "
    "LLM judge; read judge.judge_identity for provenance."
)

DEFAULT_DOES_NOT_LICENSE = (
    "Does NOT license assigning any apodictic diagnostic code (WR/DI/OB/FM-A or "
    "any other), adjudicating whether a move smuggles or is load-bearing, or any "
    "soundness / quality label, score, or aggregate — and emits NO aggregate: "
    "no counts, no tallies, no rollups (a consumer derives any tally it needs "
    "from the observations list itself). ALL THREE "
    "move families are LEGITIMATE and ubiquitous: an observation is a LOCATION, "
    "not a finding; observation COUNT is not a quality signal and must never be "
    "read as one. The consumer audit alone challenges moves and assigns codes "
    "(R4A ADR D5: the producer observes, the consumer adjudicates). Ships "
    "`heuristic`: no threshold, no operating point; the LLM judge is a candidate "
    "inventory for the audit's own identification pass, never a standalone "
    "detector (read judge.judge_identity; a `mock` judge is a test stub). Does "
    "not substitute for the audit reading the argument in context."
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
            f"Passage is short ({n_words} words, below ~{MIN_WORDS}); a move scan "
            f"over so little argument is low-confidence."
        )
    if not _ARGUMENT_MARKERS.search(text):
        out.append(
            "No inferential connectives (because/therefore/however/…) detected — "
            "the passage may not be argument-shaped nonfiction; treat the "
            "inventory as low-confidence and check the register."
        )
    return out


def build_results(
    *,
    observations: list[dict[str, Any]],
    judge_dict: dict[str, Any],
    n_paragraphs: int,
    n_words: int,
    reg_warnings: list[str],
    prompt_fp: str | None,
) -> dict[str, Any]:
    return {
        "method_version": METHOD_VERSION,
        # The deliverable: located moves. Deliberately NO tally alongside it
        # (no family counts, no observation count) — the §1a refusal of
        # aggregates is mechanical; a consumer derives len(observations).
        "observations": observations,
        "n_paragraphs": n_paragraphs,
        "n_words": n_words,
        "register_warnings": reg_warnings,
        "calibration_status": "heuristic",
        "families": list(FAMILIES),
        "judge": judge_dict,
        "prompt_fingerprint_sha256": prompt_fp,
    }


def compose_envelope(
    *,
    target_path: Path | None,
    target_words: int,
    results: dict[str, Any],
    drop_warnings: list[str],
    licenses_text: str,
    does_not_license_text: str,
) -> dict[str, Any]:
    caveats: list[str] = list(results.get("register_warnings", []))
    caveats.extend(
        f"Span integrity: {d}" for d in drop_warnings
    )
    judge_kind = results["judge"]["judge_identity"].get("kind")
    if judge_kind == "mock":
        caveats.append(
            "Judge backend is `mock` — a deterministic TEST stub, not a real "
            "reader. Do not infer anything about the passage from a mock run."
        )
    elif judge_kind == "manifest":
        caveats.append(
            "Judge backend is `manifest` — the inventory is only as good as "
            "whatever produced the manifest, which this surface cannot verify."
        )
    elif judge_kind == "agent_host":
        caveats.append(
            "Judge backend is `agent_host` — the inventory was produced by the "
            "HOST runtime's model (see judge.judge_identity.host), not a pinned "
            "API model@revision; NON-DETERMINISTIC and host-version-fluid."
        )
    caveats.append(
        "OBSERVATIONS ONLY: all three move families are legitimate; an "
        "observation is a location for the downstream audit, never a finding, "
        "code, or score. The consumer audit alone adjudicates (R4A ADR D5)."
    )

    license_block = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=licenses_text,
        does_not_license=does_not_license_text,
        comparison_set={
            "move_families": (
                "assuring / guarding / discounting (Sinnott-Armstrong & Fogelin, "
                "Understanding Arguments 9e, ch. 3; identification discipline "
                "aligned with apodictic's AGD Move Audit Layer 1)"
            ),
            "judge_kind": judge_kind,
            "judge_model": (
                results["judge"]["judge_identity"].get("model") or "(unspecified)"
            ),
            "judge_host": results["judge"]["judge_identity"].get("host"),
            "prompt_fingerprint_sha256": results["prompt_fingerprint_sha256"],
        },
        length_range_words=(MIN_WORDS, 8000),
        register_match=["argument-shaped nonfiction (op-ed / policy / testimony)"],
        additional_caveats=caveats,
        references=[
            "Sinnott-Armstrong & Fogelin, Understanding Arguments, 9th ed., ch. 3 "
            "(assuring / guarding / discounting)",
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
    observations = r.get("observations", [])
    lines = [
        "# AGD move scan",
        "",
        "> **Observations only.** All three move families are legitimate; an "
        "observation is a LOCATION for the downstream audit, never a finding, "
        "code, or score. The consumer audit adjudicates.",
        "",
        f"- **Judge:** `{r['judge']['judge_identity'].get('kind')}` "
        f"({r['judge']['judge_identity'].get('model') or '—'})",
        f"- **Observations:** {len(observations)} · **Paragraphs:** "
        f"{r['n_paragraphs']} · **Calibration:** `{r['calibration_status']}`",
        "",
        "## Inventory",
        "",
    ]
    if not observations:
        lines.append("(no moves observed)")
    for o in observations:
        cue = f"cue: “{o['cue']}”" if o.get("cue") else "cue-free"
        lines.append(f"- ¶{o['paragraph_index']} `{o['family']}` — “{o['span']}” ({cue})")
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
        description="Located AGD move observations for an argument (no adjudication).",
    )
    p.add_argument("target", type=Path, help="UTF-8 prose file to scan")
    p.add_argument("--judge", required=True,
                   choices=["mock", "manifest", "anthropic", "openai", "gemini", "agent_host"],
                   help="judge backend (REQUIRED — no default). `mock` is a deterministic TEST "
                        "stub that FABRICATES an inventory; choose it only for tests/CI, never "
                        "for a real scan. Real scans use a manifest or an API judge.")
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


def _effective_judge_fingerprint(args: argparse.Namespace, current_fp: str) -> str | None:
    """The prompt fingerprint that PRODUCED the inventory (the warrant_probe
    convention): the manifest's own for a manifest judge — None when it declared
    none (treated as drift by an --expect-fingerprint gate) — current_fp otherwise.
    Read TOP-LEVEL first (the R3B run-manifest schema the committed Phase-1
    benchmark artifacts use), then the nested ``judge_identity`` fallback shape —
    the same precedence as the manifest judge's provenance."""
    if args.judge == "manifest" and args.judge_manifest is not None:
        try:
            data = json.loads(Path(args.judge_manifest).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        fp = data.get("prompt_fingerprint_sha256")
        if not (isinstance(fp, str) and fp):
            ji = data.get("judge_identity")
            fp = ji.get("prompt_fingerprint_sha256") if isinstance(ji, dict) else None
        return fp if isinstance(fp, str) and fp else None
    return current_fp


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    target_path: Path = args.target

    current_fp = fingerprint_prompt()
    out_json = (args.out if args.out is not None
                else target_path.with_suffix(target_path.suffix + ".agd_move_scan.json"))
    out_md = (args.out_md if args.out_md is not None
              else target_path.with_suffix(target_path.suffix + ".agd_move_scan.md"))

    effective_fp = _effective_judge_fingerprint(args, current_fp)
    if args.expect_fingerprint and args.expect_fingerprint != effective_fp:
        env = _error_envelope(
            f"judge prompt fingerprint drift: expected {args.expect_fingerprint}, "
            f"judge {effective_fp}. Any pin bound to the old fingerprint is "
            f"invalid; re-sync (or regenerate a stale manifest).",
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
            f"argument to scan.", "bad_input", target_path, n_words,
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
        observations=judge_result.values["observations"],
        judge_dict=judge_result.to_dict(),
        n_paragraphs=len(paragraphs),
        n_words=n_words,
        reg_warnings=register_warnings(text, n_words),
        prompt_fp=effective_fp,
    )
    envelope = compose_envelope(
        target_path=target_path, target_words=n_words, results=results,
        drop_warnings=judge_result.drop_warnings,
        licenses_text=args.licenses, does_not_license_text=args.does_not_license,
    )
    return _emit(envelope, out_path=out_json, md_path=out_md, to_stdout=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
