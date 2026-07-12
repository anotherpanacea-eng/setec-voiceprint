#!/usr/bin/env python3
"""agd_move_scan_judge.py — pluggable LLM judge for located AGD move observations.

R3B producer seam (fleet spec ``setec-scratch/apo-argument-r3b-agd-seam``, apodictic
consumer). A sibling of ``warrant_judge`` — same provider-agnostic plumbing
(manifest / mock / anthropic / openai / gemini / agent_host via ``judge_backends``;
``JudgeError``; provenance + OWN prompt fingerprint) — but a different task and
result schema: it inventories the passage's PERFORMATIVE ARGUMENT MOVES —
ASSURING / GUARDING / DISCOUNTING (Sinnott-Armstrong & Fogelin 9e, ch. 3) — as
LOCATED OBSERVATIONS: family + verbatim span + paragraph index + surface cue (or
null for a cue-free move).

POSTURE — load-bearing
----------------------
OBSERVATIONS ONLY. All three move families are LEGITIMATE and ubiquitous; an
observation is never a finding, a flaw, or a code. The consumer (apodictic's AGD
Move Audit) challenges each move and alone assigns any diagnosis — this judge
never adjudicates smuggling, never scores, never aggregates.

Identification discipline (verbatim-aligned with the consumer's audit doc —
``apodictic: plugins/apodictic/skills/specialized-audits/references/craft/
argument-agd-audit.md`` Layer 1; if that doc's family definitions change, this
prompt must be re-synced and PROMPT_VERSION bumped — the cross-repo drift note):
a move is identified FUNCTIONALLY at a transition, and identification requires an
independently identifiable span. Cues are evidence, never criteria: a cue-free
move must be reported (cue = null), and a cue word without the function is not a
move.

Result schema (``JudgeResult.values``)
--------------------------------------
``{"observations": [ {"family": <ASSURING|GUARDING|DISCOUNTING>,
"span": <verbatim>, "paragraph_index": <int>, "cue": <str|null>} ... ]}``

Span integrity (the ``warrant_judge.normalize_claims`` discipline — per-paragraph,
NOT argquality's document-wide ``_normalize_spans``): an observation is DROPPED
unless its ``paragraph_index`` is in range and its whitespace-normalized span is
contained in THAT exact paragraph. Each drop is reported so the surface can
append it to the envelope's warnings.

Fingerprint: ``fingerprint_prompt()`` hashes THIS module's own preamble + prompt.
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

PROMPT_VERSION = "agd_move_scan_v1"

FAMILIES: tuple[str, ...] = ("ASSURING", "GUARDING", "DISCOUNTING")
# Verbatim-aligned with the consumer audit doc's Layer-1 family table (drift note above).
FAMILY_DESCRIPTIONS: dict[str, str] = {
    "ASSURING": (
        "authority or certainty supplied in place of support — there must be a "
        "strippable span (a cited-authority phrase, a credential appositive, a "
        "stated-as-known basis). No strippable span means it is NOT an assuring "
        "move. Canonical cues (evidence, never criteria): 'studies show', "
        "'clearly', 'everyone knows', 'no one disputes'."
    ),
    "GUARDING": (
        "a claim weakened to shrink its commitment. Canonical cues: 'some', "
        "'may', 'tends to', 'suggests', 'arguably'."
    ),
    "DISCOUNTING": (
        "an objection anticipated and set aside — INCLUDING structural dismissal: "
        "an objection surfaced in a subordinate or narrative clause and proceeded "
        "past, with no concessive marker (report cue = null for that case). "
        "Canonical cues: 'although', 'admittedly … but', 'to be sure', 'of course … yet'."
    ),
}


class JudgeError(RuntimeError):
    """Raised when a judge backend cannot produce a valid result."""


@dataclass
class JudgeResult:
    values: dict[str, Any]
    judge_identity: dict[str, Any]
    drop_warnings: list[str]
    raw_response: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": {"observations": list(self.values.get("observations", []))},
            "judge_identity": dict(self.judge_identity),
            "raw_response_truncated": (
                (self.raw_response[:2000] + "…")
                if self.raw_response and len(self.raw_response) > 2000
                else self.raw_response
            ),
        }


JudgeBackend = Callable[[list[str]], JudgeResult]


_SYSTEM_PREAMBLE = (
    "You are a careful argument reader assisting a human editor. You will be "
    "shown an argument-shaped nonfiction passage, split into numbered "
    "paragraphs. Inventory its PERFORMATIVE ARGUMENT MOVES — assuring, "
    "guarding, discounting. All three are LEGITIMATE and ubiquitous: an "
    "observation is NOT a criticism, a finding, or a flaw, and you must not "
    "treat one as such. Identify each move FUNCTIONALLY at its transition, not "
    "by cue words: cue words are evidence, never criteria — report a cue-free "
    "move with cue = null, and never report a cue word that is not performing "
    "the function. Quote each move's span VERBATIM from its paragraph. You "
    "observe and locate; a downstream audit does all evaluation. Never "
    "adjudicate whether a move is load-bearing or deceptive, never score, "
    "never aggregate."
)


def render_prompt() -> str:
    lines: list[str] = ["# Move families — identify each functionally\n"]
    for fam in FAMILIES:
        lines.append(f"- `{fam}`: {FAMILY_DESCRIPTIONS[fam]}")
    lines.append(
        "\n# Output format\n"
        "Return a single JSON object with one key, `observations`: an array "
        "(possibly empty) of objects, each "
        '`{"family": <ASSURING|GUARDING|DISCOUNTING>, '
        '"span": <the verbatim quoted text of the move, copied exactly from its '
        "paragraph>, "
        '"paragraph_index": <int, 0-based>, '
        '"cue": <the surface cue word/phrase, or null for a cue-free move>}`. '
        "Report only genuine functional moves; if none, return an empty list. "
        "Output JSON only, no prose."
    )
    return "\n".join(lines)


def _number_paragraphs(paragraphs: list[str]) -> str:
    return "\n\n".join(f"[{i}] {p}" for i, p in enumerate(paragraphs))


def fingerprint_prompt(prompt_text: str = "") -> str:
    """SHA-256 of THIS module's preamble + prompt (never a sibling judge's)."""
    body = _SYSTEM_PREAMBLE + "\n" + (prompt_text or render_prompt())
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _is_index(idx: Any, n: int) -> bool:
    return isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < n


def _normws(s: str) -> str:
    """Whitespace-normalized form for a tolerant verbatim-containment check."""
    return " ".join(s.split())


def normalize_observations(
    raw: Any, paragraphs: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate + normalize a raw judge ``observations`` list. Returns
    ``(observations, drop_warnings)``.

    The per-paragraph discipline (``warrant_judge.normalize_claims``, NOT the
    document-wide ``_normalize_spans``): an observation is DROPPED — with a
    warning string describing the drop — when its ``family`` is not one of the
    three, its ``paragraph_index`` is out of range, its ``span`` is empty or not
    a verbatim (whitespace-normalized) substring of THAT exact paragraph (a span
    that appears only in a different paragraph is a wrong-locus attach and is
    dropped, not relocated), or its ``cue`` is neither a non-empty string nor
    null. Nothing is ever coerced or relocated — a dropped observation is judge
    output the surface cannot vouch for."""
    out: list[dict[str, Any]] = []
    drops: list[str] = []
    if not isinstance(raw, list):
        return out, (["judge output: 'observations' was not a list — all dropped"]
                     if raw is not None else [])
    n = len(paragraphs)
    norm_paras = [_normws(p) for p in paragraphs]
    for i, entry in enumerate(raw):
        where = f"observations[{i}]"
        if not isinstance(entry, dict):
            drops.append(f"{where}: not an object — dropped")
            continue
        fam = entry.get("family")
        span = entry.get("span")
        idx = entry.get("paragraph_index")
        cue = entry.get("cue")
        if fam not in FAMILIES:
            drops.append(f"{where}: family {fam!r} not in {list(FAMILIES)} — dropped")
            continue
        if not _is_index(idx, n):
            drops.append(f"{where}: paragraph_index {idx!r} out of range [0, {n}) — dropped")
            continue
        if not isinstance(span, str) or not span.strip():
            drops.append(f"{where}: empty or non-string span — dropped")
            continue
        if _normws(span) not in norm_paras[idx]:
            drops.append(
                f"{where}: span not verbatim-contained in paragraph {idx} "
                f"(wrong-locus or hallucinated) — dropped"
            )
            continue
        if cue is not None and (not isinstance(cue, str) or not cue.strip()):
            drops.append(f"{where}: cue must be a non-empty string or null — dropped")
            continue
        out.append(
            {"family": fam, "span": span, "paragraph_index": idx,
             "cue": cue if cue is None else cue.strip()}
        )
    out.sort(key=lambda o: o["paragraph_index"])
    return out, drops


def _manifest_judge(manifest_path: Path) -> JudgeBackend:
    """Offline judge from a stored manifest:
    ``{"values": {"observations": [...]}, "judge_identity": {...}}`` — this
    surface's OWN manifest schema (keyed on ``values.observations``; NOT the
    sibling judges' ``values.paragraphs`` / ``values.claims``)."""
    try:
        data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise JudgeError(f"manifest {manifest_path}: cannot read ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise JudgeError(f"manifest {manifest_path}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise JudgeError(f"manifest {manifest_path}: top level must be a JSON object")
    values = data.get("values")
    if not isinstance(values, dict) or "observations" not in values:
        raise JudgeError(
            f"manifest {manifest_path}: missing 'values.observations' list")
    ji = data.get("judge_identity")
    ji = ji if isinstance(ji, dict) else {}

    def _run(paragraphs: list[str]) -> JudgeResult:
        obs, drops = normalize_observations(values.get("observations"), paragraphs)
        return JudgeResult(
            values={"observations": obs},
            judge_identity={
                "kind": "manifest", "manifest_path": str(manifest_path),
                "model": ji.get("model"), "model_revision": ji.get("model_revision"),
                "prompt_version": ji.get("prompt_version"),
                # Propagate the manifest's OWN prompt fingerprint (the observations were
                # produced under THAT prompt); None when the manifest declared none.
                "prompt_fingerprint_sha256": ji.get("prompt_fingerprint_sha256"),
            },
            drop_warnings=drops,
            raw_response=None,
        )

    return _run


def _mock_judge(
    pattern: tuple[tuple[str, str | None], ...] = (
        ("GUARDING", "may"),
        ("DISCOUNTING", None),
    ),
) -> JudgeBackend:
    """Deterministic judge for tests/CI: for each paragraph index <
    len(pattern), one observation of the fixed family/cue quoting that
    paragraph's leading words. A STUB — never infer a real inventory from it."""

    def _run(paragraphs: list[str]) -> JudgeResult:
        raw: list[dict[str, Any]] = []
        for i, (fam, cue) in enumerate(pattern):
            if i >= len(paragraphs):
                break
            words = paragraphs[i].split()
            span = " ".join(words[:8]) if words else paragraphs[i][:40]
            if not span.strip():
                continue
            raw.append({"family": fam, "span": span, "paragraph_index": i, "cue": cue})
        obs, drops = normalize_observations(raw, paragraphs)
        return JudgeResult(
            values={"observations": obs},
            judge_identity={"kind": "mock", "pattern_len": len(pattern)},
            drop_warnings=drops,
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
    if not isinstance(parsed, dict) or "observations" not in parsed:
        raise JudgeError("judge JSON missing 'observations' list")
    ident = dict(identity)
    ident.setdefault("prompt_version", PROMPT_VERSION)
    ident["prompt_fingerprint_sha256"] = fingerprint_prompt()
    obs, drops = normalize_observations(parsed.get("observations"), paragraphs)
    return JudgeResult(
        values={"observations": obs},
        judge_identity=ident,
        drop_warnings=drops,
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
    """Construct a judge backend by kind: ``manifest`` (needs manifest_path),
    ``mock`` (deterministic), ``anthropic``/``openai``/``gemini`` (lazy SDK
    import + credentials in env; need ``model``), or ``agent_host`` (spec 35 —
    key-free, host-model-resolved)."""
    if kind == "manifest":
        if manifest_path is None:
            raise JudgeError("manifest judge requires manifest_path")
        return _manifest_judge(Path(manifest_path))
    if kind == "mock":
        return _mock_judge()
    if kind in judge_backends.PROVIDERS:
        if kind == "agent_host":
            model = model or "host-resolved"
        elif not model:
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
