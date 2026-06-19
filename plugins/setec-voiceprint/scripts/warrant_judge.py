#!/usr/bin/env python3
"""warrant_judge.py — pluggable LLM judge for Toulmin warrant-coverage.

ArgScope M2 (spec ``specs/26-fallacy-warrant-scan.md``). A sibling of
``fallacy_judge`` — same provider-agnostic plumbing (manifest / mock / anthropic
/ openai / gemini via ``judge_backends``; ``JudgeError``; provenance + OWN prompt
fingerprint) — but a different task and result schema: for each major claim it
identifies, it answers the Critical-Questions-of-Thought bank (Favero et al.
2024, arXiv:2412.15177) for the claim's WARRANT, BACKING, and REBUTTAL, reporting
per-question COVERAGE (``present`` / ``partial`` / ``absent``).

POSTURE — load-bearing
----------------------
This reports COVERAGE of the critical questions a human reviewer would ask; it
NEVER concludes the argument is unsound, weak, or bad. An ``absent`` warrant is a
*coverage gap to examine*, not a verdict — many strong arguments leave a warrant
implicit because it is shared with the reader. No aggregate, no soundness score.

Result schema (``JudgeResult.values``)
--------------------------------------
``{"claims": [ {"claim_span": <verbatim>, "paragraph_index": <int>,
"critical_questions": {"warrant": <status>, "backing": <status>,
"rebuttal": <status>}} ... ]}`` — status ∈ {present, partial, absent}.

Fingerprint
-----------
``fingerprint_prompt()`` hashes THIS module's own preamble + prompt (never
``fallacy_judge``'s or ``argument_judge``'s) so a drift gate keyed to this prompt
stays honest.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import judge_backends  # type: ignore

PROMPT_VERSION = "warrant_probe_v1"

# The three Toulmin critical-question axes probed per claim.
CRITICAL_QUESTIONS: tuple[str, ...] = ("warrant", "backing", "rebuttal")
CQ_STATUSES: tuple[str, ...] = ("present", "partial", "absent")
CQ_DESCRIPTIONS: dict[str, str] = {
    "warrant": "Is the inferential link from the grounds to the claim stated or "
               "clearly identifiable (not just the claim and data side by side)?",
    "backing": "Is there backing — support/authority/evidence FOR the warrant "
               "itself, beyond the grounds for the claim?",
    "rebuttal": "Is a counterargument, exception, or condition of rebuttal "
                "acknowledged (rather than the claim asserted as unconditional)?",
}


class JudgeError(RuntimeError):
    """Raised when a judge backend cannot produce a valid result."""


@dataclass
class JudgeResult:
    values: dict[str, Any]
    judge_identity: dict[str, Any]
    raw_response: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": {"claims": list(self.values.get("claims", []))},
            "judge_identity": dict(self.judge_identity),
            "raw_response_truncated": (
                (self.raw_response[:2000] + "…")
                if self.raw_response and len(self.raw_response) > 2000
                else self.raw_response
            ),
        }


JudgeBackend = Callable[[list[str]], JudgeResult]


_SYSTEM_PREAMBLE = (
    "You are a careful argument reviewer assisting a human editor. You will be "
    "shown a short argument-shaped nonfiction passage, split into numbered "
    "paragraphs. Identify the major CLAIMS, and for each, answer three Toulmin "
    "critical questions about its support — is the WARRANT (inferential link) "
    "present, is there BACKING for that warrant, and is a REBUTTAL "
    "(counterargument / exception) acknowledged. You report COVERAGE only: "
    "whether each is present, partial, or absent. You are NOT judging the "
    "argument. An absent warrant or rebuttal is a coverage GAP a human should "
    "examine — many strong arguments leave a warrant implicit because it is "
    "shared with the reader. Never conclude the argument is unsound, weak, or "
    "bad, and never score it. The editor decides what the gaps mean."
)


def render_prompt() -> str:
    lines: list[str] = ["# Critical questions — answer each per claim\n"]
    for cq in CRITICAL_QUESTIONS:
        lines.append(f"- `{cq}`: {CQ_DESCRIPTIONS[cq]}")
    lines.append(
        "\n# Status values (coverage, NOT quality)\n"
        "- `present`: clearly addressed in the text.\n"
        "- `partial`: gestured at but incomplete / implicit.\n"
        "- `absent`: not addressed (a coverage gap to examine — NOT a flaw verdict)."
    )
    lines.append(
        "\n# Output format\n"
        "Return a single JSON object with one key, `claims`: an array (possibly "
        "empty) of objects, each "
        '`{"claim_span": <the verbatim quoted text of the claim, copied exactly>, '
        '"paragraph_index": <int, 0-based>, "critical_questions": '
        '{"warrant": <status>, "backing": <status>, "rebuttal": <status>}}. '
        "Identify only genuine major claims; if none, return an empty list. "
        "Output JSON only, no prose."
    )
    return "\n".join(lines)


def _number_paragraphs(paragraphs: list[str]) -> str:
    return "\n\n".join(f"[{i}] {p}" for i, p in enumerate(paragraphs))


def fingerprint_prompt(prompt_text: str = "") -> str:
    """SHA-256 of THIS module's preamble + prompt — never fallacy_judge's /
    argument_judge's (different prompt → wrong hash → broken drift gate)."""
    body = _SYSTEM_PREAMBLE + "\n" + (prompt_text or render_prompt())
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _is_index(idx: Any, n: int) -> bool:
    return isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < n


def _normws(s: str) -> str:
    """Whitespace-normalized form for a tolerant verbatim-containment check."""
    return " ".join(s.split())


def normalize_claims(raw: Any, paragraphs: list[str]) -> list[dict[str, Any]]:
    """Validate + normalize a raw judge ``claims`` list. Drops a claim with an
    out-of-range index, an empty span, a ``claim_span`` that is NOT a verbatim
    (whitespace-normalized) substring of the paragraph it is attributed to (#229:
    a hallucinated claim the judge did not actually quote is rejected), or
    critical_questions that is not a dict of the three axes with valid statuses
    (an unknown status → ``absent``, the conservative no-coverage default; never
    a fabricated ``present``)."""
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    n = len(paragraphs)
    norm_paras = [_normws(p) for p in paragraphs]
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("paragraph_index")
        span = entry.get("claim_span")
        cqs = entry.get("critical_questions")
        if not _is_index(idx, n):
            continue
        if not isinstance(span, str) or not span.strip():
            continue
        if _normws(span) not in norm_paras[idx]:
            continue   # hallucinated claim — not actually present in the cited paragraph
        if not isinstance(cqs, dict):
            continue
        norm_cq = {
            cq: (cqs.get(cq) if cqs.get(cq) in CQ_STATUSES else "absent")
            for cq in CRITICAL_QUESTIONS
        }
        out.append(
            {
                "claim_span": span,
                "paragraph_index": idx,
                "critical_questions": norm_cq,
            }
        )
    out.sort(key=lambda c: c["paragraph_index"])
    return out


def _manifest_judge(manifest_path: Path) -> JudgeBackend:
    try:
        data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise JudgeError(f"manifest {manifest_path}: cannot read ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise JudgeError(f"manifest {manifest_path}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise JudgeError(
            f"manifest {manifest_path}: top level must be a JSON object")
    values = data.get("values")
    if not isinstance(values, dict) or "claims" not in values:
        raise JudgeError(f"manifest {manifest_path}: missing 'values.claims' list")
    ji = data.get("judge_identity")
    ji = ji if isinstance(ji, dict) else {}

    def _run(paragraphs: list[str]) -> JudgeResult:
        return JudgeResult(
            values={"claims": normalize_claims(values.get("claims"), paragraphs)},
            judge_identity={
                "kind": "manifest", "manifest_path": str(manifest_path),
                "model": ji.get("model"), "model_revision": ji.get("model_revision"),
                "prompt_version": ji.get("prompt_version"),
            },
            raw_response=None,
        )

    return _run


def _mock_judge(
    pattern: tuple[dict[str, str], ...] = (
        {"warrant": "present", "backing": "partial", "rebuttal": "absent"},
        {"warrant": "partial", "backing": "absent", "rebuttal": "absent"},
    ),
) -> JudgeBackend:
    """Deterministic judge for tests/CI: one claim per paragraph index <
    len(pattern), quoting that paragraph's leading words, with the fixed CQ
    coverage at ``pattern[i]``. A STUB — never infer a real reading from it."""

    def _run(paragraphs: list[str]) -> JudgeResult:
        claims: list[dict[str, Any]] = []
        for i, cqs in enumerate(pattern):
            if i >= len(paragraphs):
                break
            words = paragraphs[i].split()
            span = " ".join(words[:8]) if words else paragraphs[i][:40]
            if not span.strip():
                continue
            claims.append(
                {"claim_span": span, "paragraph_index": i,
                 "critical_questions": dict(cqs)}
            )
        return JudgeResult(
            values={"claims": normalize_claims(claims, paragraphs)},
            judge_identity={"kind": "mock", "pattern_len": len(pattern)},
        )

    return _run


def _extract_json(text: str) -> Any:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s
        if s.lstrip().startswith("json"):
            s = s.lstrip()[4:]
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise JudgeError("judge response contained no JSON object")
    return json.loads(s[start : end + 1])


def _build_user_content(user_prompt: str, paragraphs: list[str]) -> str:
    return (
        f"{user_prompt}\n\n# Passage (numbered paragraphs)\n\n"
        f"{_number_paragraphs(paragraphs)}"
    )


# make_api_judge invokes this as build_result(payload, raw_text, identity, judge_input).
def _build_api_result(parsed: Any, raw: str, identity: dict[str, Any],
                      paragraphs: list[str]) -> JudgeResult:
    if not isinstance(parsed, dict) or "claims" not in parsed:
        raise JudgeError("judge JSON missing 'claims' list")
    ident = dict(identity)
    ident.setdefault("prompt_version", PROMPT_VERSION)
    ident["prompt_fingerprint_sha256"] = fingerprint_prompt()
    return JudgeResult(
        values={"claims": normalize_claims(parsed.get("claims"), paragraphs)},
        judge_identity=ident,
        raw_response=raw,
    )


def build_judge(
    kind: str,
    *,
    manifest_path: Path | str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
) -> JudgeBackend:
    if kind == "manifest":
        if manifest_path is None:
            raise JudgeError("manifest judge requires manifest_path")
        return _manifest_judge(Path(manifest_path))
    if kind == "mock":
        return _mock_judge()
    if kind in ("anthropic", "openai", "gemini"):
        if not model:
            raise JudgeError(f"{kind} judge requires --judge-model")
        return judge_backends.make_api_judge(
            kind,
            model=model,
            system_preamble=_SYSTEM_PREAMBLE,
            user_prompt=render_prompt(),
            temperature=temperature,
            max_tokens=max_tokens,
            build_user_content=_build_user_content,
            build_result=_build_api_result,
            judge_error=JudgeError,
            extract_json=_extract_json,
        )
    raise JudgeError(f"unknown judge kind: {kind!r}")
