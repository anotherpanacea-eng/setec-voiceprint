#!/usr/bin/env python3
"""fallacy_judge.py — pluggable LLM judge for candidate rhetorical-move flags.

Mirrors ``argument_judge`` / ``narrative_judge`` (same provider-agnostic
plumbing: manifest / mock / anthropic / openai / gemini; lazy SDK imports via
``judge_backends``; ``JudgeError``; provenance + prompt fingerprint) — but it is
its OWN module with its OWN system preamble, user prompt, fingerprint, and mock,
because the task and the result schema differ from the paragraph-role judge.

Spec ``specs/26-fallacy-warrant-scan.md`` (M1). Implements the Logic 13-type
fallacy taxonomy (Jin et al., *Logical Fallacy Detection*, arXiv:2202.13758) +
Flee-the-Flaw implicit-logic reconstruction (arXiv:2406.12402).

POSTURE — load-bearing
----------------------
The judge surfaces **candidate rhetorical moves a human should examine**, NOT
verdicts. It is told, explicitly, that a flagged move is *frequently legitimate
in context* (an appeal to authority is often valid; a slippery-slope may be a
sound causal chain). It never asserts a span IS a fallacy, never scores
soundness, and emits no aggregate. The result schema carries the framing in its
field names (``candidate_type``), so a consumer cannot read a flag as a ruling.

Result schema (``JudgeResult.values``)
--------------------------------------
``{"flags": [ {"candidate_type": <one Logic type>, "paragraph_index": <int>,
"span_text": <verbatim span>, "reconstruction": <implicit-logic scaffold>} ... ]}``
— a flat, document-ordered list of candidate flags. ``span_text`` is a verbatim
substring (paragraph-anchored, NOT character offsets — judge-fragile).

Fingerprint
-----------
``fingerprint_prompt()`` hashes THIS module's ``_SYSTEM_PREAMBLE`` +
``render_prompt()`` — never ``argument_judge``'s (which would fingerprint the
role/mode prompt and silently defeat any drift gate bound to this surface).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import judge_backends  # type: ignore

PROMPT_VERSION = "fallacy_scan_v1"


# ----------------- taxonomy ---------------------------------------
# The Logic dataset's 13 fallacy classes (Jin et al. 2022, arXiv:2202.13758).
# Names are the surface's stable vocabulary; the parenthetical is the common
# alias the judge is also given so it maps human terms onto our ids.
FALLACY_TYPES: tuple[str, ...] = (
    "faulty_generalization",
    "ad_hominem",
    "ad_populum",
    "false_causality",
    "circular_reasoning",
    "appeal_to_emotion",
    "fallacy_of_relevance",
    "deductive_fallacy",
    "intentional",
    "false_dilemma",
    "equivocation",
    "fallacy_of_extension",
    "fallacy_of_credibility",
)

FALLACY_ALIASES: dict[str, str] = {
    "faulty_generalization": "hasty/sweeping generalization from too little evidence",
    "ad_hominem": "attack on the arguer rather than the argument",
    "ad_populum": "appeal to popularity / bandwagon",
    "false_causality": "post hoc / correlation-as-causation",
    "circular_reasoning": "begging the question — the conclusion assumed in a premise",
    "appeal_to_emotion": "emotion substituted for relevant reasons",
    "fallacy_of_relevance": "red herring / irrelevant premise",
    "deductive_fallacy": "an invalid formal step (affirming the consequent, etc.)",
    "intentional": "misrepresenting intent / loaded framing",
    "false_dilemma": "only two options presented when more exist",
    "equivocation": "a key term shifts meaning across the argument",
    "fallacy_of_extension": "straw man — an exaggerated/distorted version attacked",
    "fallacy_of_credibility": "appeal to an unqualified or irrelevant authority",
}


# ----------------- errors / result --------------------------------
class JudgeError(RuntimeError):
    """Raised when a judge backend cannot produce a valid result."""


@dataclass
class JudgeResult:
    """One judged document. ``values`` carries ``flags``: a document-ordered list
    of candidate rhetorical-move flags (NOT verdicts). ``judge_identity`` is the
    provenance dict (always set)."""

    values: dict[str, Any]
    judge_identity: dict[str, Any]
    raw_response: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": {"flags": list(self.values.get("flags", []))},
            "judge_identity": dict(self.judge_identity),
            "raw_response_truncated": (
                (self.raw_response[:2000] + "…")
                if self.raw_response and len(self.raw_response) > 2000
                else self.raw_response
            ),
        }


JudgeBackend = Callable[[list[str]], JudgeResult]


# ----------------- prompt construction ----------------------------
_SYSTEM_PREAMBLE = (
    "You are a careful rhetoric reviewer assisting a human editor. You will be "
    "shown a short argument-shaped nonfiction passage, split into numbered "
    "paragraphs. Your job is to FLAG CANDIDATE rhetorical moves the editor "
    "should examine — places where a named fallacy PATTERN may be operating. You "
    "are NOT a judge of the argument. A flagged move is frequently LEGITIMATE in "
    "context: an appeal to authority is often valid, a slippery-slope can be a "
    "sound causal chain, an emotional appeal can be apt. Never assert that a span "
    "IS a fallacy, never rate the argument's soundness or quality, and never "
    "summarize an overall judgment. Flag only spans where the pattern is "
    "genuinely visible; when unsure, do not flag. The editor decides."
)


def render_prompt() -> str:
    """Build the user-side flagging prompt (taxonomy legend + framing + output
    format). The system preamble is prepended for API judges."""
    lines: list[str] = []
    lines.append(
        "# Candidate rhetorical-move types — name each flag with exactly one id\n"
    )
    for t in FALLACY_TYPES:
        lines.append(f"- `{t}`: {FALLACY_ALIASES[t]}")
    lines.append(
        "\n# What to return\n"
        "For each span where one of the above PATTERNS is genuinely visible, emit "
        "one flag. A flag is a CANDIDATE for human review, never a ruling. Do NOT "
        "flag a span just because it is forceful, one-sided, or persuasive — those "
        "are normal in argument. Flag only a recognizable instance of a named "
        "pattern. If nothing is clearly visible, return an empty list."
    )
    lines.append(
        "\n# Output format\n"
        "Return a single JSON object with one key, `flags`: an array (possibly "
        "empty) of objects, each "
        '`{"candidate_type": <one type id above>, "paragraph_index": <int, '
        "0-based, the paragraph the span is in>, "
        '"span_text": <the verbatim quoted text of the span, copied exactly>, '
        '"reconstruction": <one sentence stating the IMPLICIT logic of the '
        "flagged move — the unstated step that, IF taken as decisive, would make "
        "the pattern apply; this is your explanation for the editor, NOT a claim "
        "that the move is invalid>}. Output JSON only, no prose."
    )
    return "\n".join(lines)


def _number_paragraphs(paragraphs: list[str]) -> str:
    return "\n\n".join(f"[{i}] {p}" for i, p in enumerate(paragraphs))


def fingerprint_prompt(prompt_text: str = "") -> str:
    """SHA-256 of THIS module's system preamble + canonical prompt — provenance:
    identical fingerprints mean byte-identical prompts. MUST NOT delegate to
    ``argument_judge.fingerprint_prompt`` (different prompt → wrong hash)."""
    body = _SYSTEM_PREAMBLE + "\n" + (prompt_text or render_prompt())
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# ----------------- backend: shared validation ---------------------
def _is_index(idx: Any, n: int) -> bool:
    """True iff ``idx`` is a real paragraph index (int, not bool, in range)."""
    return isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < n


def normalize_flags(raw: Any, n_paragraphs: int) -> list[dict[str, Any]]:
    """Validate + normalize a raw judge ``flags`` list into the result schema.

    Drops any flag whose ``candidate_type`` is not a known Logic type, whose
    ``paragraph_index`` is out of range, or whose ``span_text`` is empty — a
    judge cannot smuggle a free-text verdict through. Keeps document order;
    ``reconstruction`` defaults to "" when absent."""
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        ctype = entry.get("candidate_type")
        idx = entry.get("paragraph_index")
        span = entry.get("span_text")
        if ctype not in FALLACY_TYPES:
            continue
        if not _is_index(idx, n_paragraphs):
            continue
        if not isinstance(span, str) or not span.strip():
            continue
        recon = entry.get("reconstruction")
        out.append(
            {
                "candidate_type": ctype,
                "paragraph_index": idx,
                "span_text": span,
                "reconstruction": recon if isinstance(recon, str) else "",
            }
        )
    out.sort(key=lambda f: f["paragraph_index"])
    return out


# ----------------- backend: manifest ------------------------------
def _manifest_judge(manifest_path: Path) -> JudgeBackend:
    try:
        data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise JudgeError(f"manifest {manifest_path}: cannot read ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise JudgeError(f"manifest {manifest_path}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise JudgeError(
            f"manifest {manifest_path}: top level must be a JSON object, got "
            f"{type(data).__name__}"
        )
    values = data.get("values")
    if not isinstance(values, dict) or "flags" not in values:
        raise JudgeError(f"manifest {manifest_path}: missing 'values.flags' list")
    ji = data.get("judge_identity")
    ji = ji if isinstance(ji, dict) else {}

    def _run(paragraphs: list[str]) -> JudgeResult:
        return JudgeResult(
            values={"flags": normalize_flags(values.get("flags"), len(paragraphs))},
            judge_identity={
                "kind": "manifest",
                "manifest_path": str(manifest_path),
                "model": ji.get("model"),
                "model_revision": ji.get("model_revision"),
                "prompt_version": ji.get("prompt_version"),
            },
            raw_response=None,
        )

    return _run


# ----------------- backend: mock ----------------------------------
def _mock_judge(flag_types: tuple[str, ...] = ("appeal_to_emotion", "false_dilemma")) -> JudgeBackend:
    """Deterministic judge for tests/CI/fixtures: emits one candidate flag per
    paragraph index < len(flag_types), each naming ``flag_types[i]`` and quoting
    that paragraph's leading words as the span. It is a STUB — never infer a real
    rhetorical reading from it (the provenance kind is ``mock``)."""

    def _run(paragraphs: list[str]) -> JudgeResult:
        flags: list[dict[str, Any]] = []
        for i, ctype in enumerate(flag_types):
            if i >= len(paragraphs):
                break
            words = paragraphs[i].split()
            span = " ".join(words[:8]) if words else paragraphs[i][:40]
            if not span.strip():
                continue
            flags.append(
                {
                    "candidate_type": ctype,
                    "paragraph_index": i,
                    "span_text": span,
                    "reconstruction": (
                        f"(mock) the move reads as a candidate {ctype}; an editor "
                        f"should check whether it is legitimate here."
                    ),
                }
            )
        return JudgeResult(
            values={"flags": normalize_flags(flags, len(paragraphs))},
            judge_identity={"kind": "mock", "flag_types": list(flag_types)},
        )

    return _run


# ----------------- backend: API (anthropic/openai/gemini) ---------
def _extract_json(text: str) -> Any:
    """Parse the first JSON object in a model response (tolerates fences/prose)."""
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
    if not isinstance(parsed, dict) or "flags" not in parsed:
        raise JudgeError("judge JSON missing 'flags' list")
    ident = dict(identity)
    ident.setdefault("prompt_version", PROMPT_VERSION)
    ident["prompt_fingerprint_sha256"] = fingerprint_prompt()
    return JudgeResult(
        values={"flags": normalize_flags(parsed.get("flags"), len(paragraphs))},
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
    mock_flag_types: tuple[str, ...] = ("appeal_to_emotion", "false_dilemma"),
) -> JudgeBackend:
    """Construct a judge backend by kind: ``manifest`` (needs manifest_path),
    ``mock`` (deterministic), or ``anthropic``/``openai``/``gemini`` (lazy SDK
    import + credentials in env; need ``model``)."""
    if kind == "manifest":
        if manifest_path is None:
            raise JudgeError("manifest judge requires manifest_path")
        return _manifest_judge(Path(manifest_path))
    if kind == "mock":
        return _mock_judge(mock_flag_types)
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
