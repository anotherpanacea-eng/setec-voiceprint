#!/usr/bin/env python3
"""cross_doc_consistency_judge.py — the LLM-judge seam for
``cross_doc_argument_consistency``.

A NEW ``argument_judge``-style judge pass over FREE TEXT (NOT ``argument_spine``
parsing of authored Argument_State blocks, and NOT ``argument_judge``'s
per-paragraph role/mode taxonomy — that is the wrong shape). Two passes:

  1. ``extract_commitments(text) -> list[Commitment]`` — per document, extract
     the load-bearing commitments as typed nodes (claim / warrant /
     scope_condition / value_premise / empirical_premise), each with a verbatim
     locus (start/end char + quote), a short normalized statement, and a stable
     ``topic_ref`` (so only commitments about the same proposition are compared).
  2. ``detect_tension(a, b) -> Relation`` — for an aligned pair of commitments
     (same ``topic_ref``, different docs), classify the relation (consistent /
     tension / direct_conflict / incomparable).

Backends mirror ``argument_judge``: ``manifest`` (pre-computed labels) /
``mock`` (deterministic, CI-safe) / ``anthropic`` (lazy SDK import, fail-loud).
**M1 = mock-deterministic; M2 = anthropic.**

The mock judge is the contract that keeps CI dependency-free AND exercises the
real machinery: it extracts commitments by reading lightweight in-text markers
the fixtures carry (``[[topic=...]]`` and ``[[type=...]]`` annotations on a
sentence), and classifies a pair's relation from a ``[[stance=for|against]]``
marker. The legitimate-variation DEFENSE is NOT decided here — it is a mechanical
scan of the loci text in the surface (``cross_doc_argument_consistency.py``), so
the firewall's load-bearing filter stays in deterministic Python, not the judge.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
import sys
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import judge_backends  # type: ignore  # noqa: E402
from cross_doc_consistency_schema import (  # noqa: E402
    COMMITMENT_TYPE_OPTIONS,
    RELATION_OPTIONS,
)

__all__ = [
    "Commitment",
    "JudgeResult",
    "JudgeError",
    "build_judge",
    "render_prompt",
    "validate_commitments",
    "fingerprint_prompt",
    "utc_now",
]


class JudgeError(RuntimeError):
    """Raised when a judge backend cannot produce a valid result."""


@dataclass(frozen=True)
class Commitment:
    """One load-bearing commitment extracted from a document.

    ``doc`` is the document label (focal/pool source id); ``start_char``/
    ``end_char``/``quote`` are the verbatim locus; ``statement`` is the judge's
    short normalized paraphrase; ``topic_ref`` is the stable cross-doc alignment
    key; ``ctype`` is one of ``COMMITMENT_TYPE_OPTIONS``; ``stance`` is an
    optional ``for``/``against`` polarity the tension pass uses."""

    doc: str
    topic_ref: str
    ctype: str
    statement: str
    start_char: int
    end_char: int
    quote: str
    stance: str | None = None

    def locus(self) -> dict[str, Any]:
        return {
            "doc": self.doc,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "quote": self.quote,
        }


@dataclass
class JudgeResult:
    """Per-document extraction result + provenance."""

    commitments: list[Commitment]
    judge_identity: dict[str, Any]
    raw_response: str | None = None
    warnings: list[str] = field(default_factory=list)


# ----------------- prompt construction ----------------------------

_SYSTEM_PREAMBLE = (
    "You are a careful argument-structure annotator. You will be shown one "
    "argument-shaped document and asked to extract its LOAD-BEARING "
    "commitments: the claims, warrants, scope conditions, value premises, and "
    "empirical premises the argument actually relies on. For each, return a "
    "verbatim character span (start_char, end_char) and the exact quoted text, "
    "a short neutral paraphrase, a typed role, and a stable topic_ref string "
    "that you REUSE across every commitment about the same proposition (so the "
    "same proposition can be aligned across documents). Do NOT judge whether the "
    "argument is correct, honest, or who wrote it; do NOT decide whether two "
    "documents contradict each other; do NOT score the author. Report only what "
    "each document commits to. When you are unsure of a commitment's type or "
    "topic, omit it rather than inventing one. Return valid JSON in the schema "
    "you are shown."
)


def render_prompt() -> str:
    """Build the user-side extraction prompt (commitment-type legend + output
    format). The system preamble is prepended for API judges."""
    lines: list[str] = []
    lines.append("# Commitment TYPE — choose exactly one per commitment\n")
    descriptions = {
        "claim": "A load-bearing assertion the argument advances.",
        "warrant": "An inference rule / bridge the argument relies on to move from evidence to claim.",
        "scope_condition": "A qualifier limiting where/when a claim holds.",
        "value_premise": "A normative premise (what matters / what is good).",
        "empirical_premise": "A factual premise the argument treats as given.",
    }
    for ctype in COMMITMENT_TYPE_OPTIONS:
        lines.append(f"- `{ctype}`: {descriptions[ctype]}")
    lines.append(
        "\n# topic_ref — align the SAME proposition across documents\n"
    )
    lines.append(
        "Assign a short, stable string id (e.g. \"t_min_wage\", \"t_free_speech\") "
        "to the proposition a commitment is about, and REUSE the SAME id for every "
        "commitment — in this document or another — about that same proposition. "
        "Use DIFFERENT ids for different propositions. This is the only way the "
        "downstream alignment can compare like with like."
    )
    lines.append("\n# Output format\n")
    lines.append(
        "Return a single JSON object with one key, `commitments`: an array. Each "
        'entry is `{"topic_ref": <string>, "type": <one type>, "statement": '
        '<short neutral paraphrase>, "start_char": <int>, "end_char": <int>, '
        '"quote": <verbatim text of the span>, "stance": <"for"|"against"|null>}`. '
        "`stance` is the commitment's polarity TOWARD its topic_ref proposition "
        "(for / against), or null when it has no clear polarity. Do not include "
        "any other keys, prose, or explanations.\n"
    )
    return "\n".join(lines)


# ----------------- validation -------------------------------------

def validate_commitments(
    payload: dict[str, Any], *, doc: str, text_len: int
) -> tuple[list[Commitment], list[str]]:
    """Return ``(commitments, warnings)`` from a judge payload. An entry with a
    bad type, an out-of-range span, or a missing required field is DROPPED with a
    warning — never silently coerced (a judge emitting the wrong vocabulary is a
    judge-config problem, not a data-cleaning one). Mirrors
    ``argument_judge.validate_labels``' skip-and-warn discipline."""
    warnings: list[str] = []
    raw = payload.get("commitments")
    out: list[Commitment] = []
    if not isinstance(raw, list):
        warnings.append(
            f"doc {doc!r}: judge output missing a 'commitments' list "
            f"(got {type(raw).__name__})"
        )
        return out, warnings
    for pos, entry in enumerate(raw):
        if not isinstance(entry, dict):
            warnings.append(f"doc {doc!r}: commitment {pos} is not a mapping")
            continue
        ctype = entry.get("type")
        if ctype not in COMMITMENT_TYPE_OPTIONS:
            warnings.append(f"doc {doc!r}: commitment {pos} type {ctype!r} not in vocab; dropped")
            continue
        topic = entry.get("topic_ref")
        if not isinstance(topic, str) or not topic.strip():
            warnings.append(f"doc {doc!r}: commitment {pos} missing topic_ref; dropped")
            continue
        statement = entry.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            warnings.append(f"doc {doc!r}: commitment {pos} missing statement; dropped")
            continue
        start = entry.get("start_char")
        end = entry.get("end_char")
        if (
            not isinstance(start, int) or isinstance(start, bool)
            or not isinstance(end, int) or isinstance(end, bool)
            or start < 0 or end < start or end > text_len
        ):
            warnings.append(
                f"doc {doc!r}: commitment {pos} span ({start},{end}) out of range "
                f"[0,{text_len}]; dropped"
            )
            continue
        quote = entry.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            warnings.append(f"doc {doc!r}: commitment {pos} missing quote; dropped")
            continue
        stance = entry.get("stance")
        if stance not in (None, "for", "against"):
            warnings.append(f"doc {doc!r}: commitment {pos} stance {stance!r} invalid; nulled")
            stance = None
        out.append(Commitment(
            doc=doc, topic_ref=topic.strip(), ctype=ctype, statement=statement.strip(),
            start_char=start, end_char=end, quote=quote, stance=stance,
        ))
    return out, warnings


# ----------------- relation detection -----------------------------

def detect_tension(a: Commitment, b: Commitment) -> str:
    """Classify the relation between two ALIGNED commitments (same topic_ref,
    different docs). Deterministic over the commitments' ``stance`` polarity:
    opposing stances on the same proposition -> ``direct_conflict``; one stance
    present + one absent -> ``tension``; agreeing stances -> ``consistent``;
    different topic_ref -> ``incomparable``.

    This is the M1 (mock/deterministic) relation rule. An API judge backend can
    override it (the ``anthropic`` seam classifies relations in-context), but the
    mock contract is this stance-polarity rule so CI is dependency-free and the
    relation is reproducible. Returns a value in ``RELATION_OPTIONS``."""
    if a.topic_ref != b.topic_ref:
        return "incomparable"
    sa, sb = a.stance, b.stance
    if sa is not None and sb is not None:
        if sa != sb:
            return "direct_conflict"
        return "consistent"
    if (sa is None) != (sb is None):
        # one polar, one neutral on the same proposition -> a (weaker) tension
        return "tension"
    return "incomparable"  # both neutral: aligned by topic but no comparable polarity


# ----------------- mock marker parsing ----------------------------
# The mock judge extracts commitments from lightweight in-text markers the
# fixtures carry, so CI exercises the real cross-doc alignment + relation machinery
# deterministically. A marker annotates ONE sentence:
#   [[topic=t_id type=claim stance=for]] <the committed sentence text.>
# The quote/span are the marked sentence (the text after the marker up to the
# next marker or end). This is a CI scaffold; an API judge reads raw prose.
_MARKER_RE = re.compile(
    r"\[\[\s*(?P<body>[^\]]*?)\s*\]\]",
)


def _parse_marker_body(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for tok in body.split():
        if "=" in tok:
            k, _, v = tok.partition("=")
            out[k.strip()] = v.strip()
    return out


def _mock_extract(doc: str, text: str) -> list[Commitment]:
    """Deterministic marker-driven extraction (the mock contract)."""
    commitments: list[Commitment] = []
    markers = list(_MARKER_RE.finditer(text))
    for i, m in enumerate(markers):
        attrs = _parse_marker_body(m.group("body"))
        topic = attrs.get("topic")
        ctype = attrs.get("type")
        if not topic or ctype not in COMMITMENT_TYPE_OPTIONS:
            continue
        stance = attrs.get("stance")
        if stance not in ("for", "against"):
            stance = None
        # The committed span is the sentence text AFTER this marker up to the
        # next marker (or end of text).
        span_start = m.end()
        span_end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        quote = text[span_start:span_end].strip()
        if not quote:
            continue
        # Tighten the span to the stripped quote's actual offsets.
        lead = len(text[span_start:span_end]) - len(text[span_start:span_end].lstrip())
        real_start = span_start + lead
        real_end = real_start + len(quote)
        commitments.append(Commitment(
            doc=doc, topic_ref=topic, ctype=ctype,
            statement=quote[:120], start_char=real_start, end_char=real_end,
            quote=quote, stance=stance,
        ))
    return commitments


# ----------------- judge backends ---------------------------------

JudgeBackend = Callable[[str, str], JudgeResult]  # (doc_label, text) -> JudgeResult


def _mock_judge() -> JudgeBackend:
    def _run(doc: str, text: str) -> JudgeResult:
        return JudgeResult(
            commitments=_mock_extract(doc, text),
            judge_identity={"kind": "mock"},
        )
    return _run


def _manifest_judge(manifest_path: Path) -> JudgeBackend:
    """Read pre-computed per-doc commitment lists from a JSON manifest keyed by
    doc label: ``{"docs": {"<doc>": {"commitments": [...]}}, "judge_identity": {...}}``."""
    try:
        data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise JudgeError(f"manifest {manifest_path}: cannot read ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise JudgeError(f"manifest {manifest_path}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("docs"), dict):
        raise JudgeError(f"manifest {manifest_path}: missing 'docs' object")
    docs = data["docs"]
    ji = data.get("judge_identity")
    ji = ji if isinstance(ji, dict) else {}

    def _run(doc: str, text: str) -> JudgeResult:
        entry = docs.get(doc)
        if not isinstance(entry, dict):
            return JudgeResult(
                commitments=[],
                judge_identity={"kind": "manifest", "manifest_path": str(manifest_path)},
                warnings=[f"manifest has no entry for doc {doc!r}"],
            )
        commitments, warns = validate_commitments(entry, doc=doc, text_len=len(text))
        return JudgeResult(
            commitments=commitments,
            judge_identity={
                "kind": "manifest",
                "manifest_path": str(manifest_path),
                "model": ji.get("model"),
                "prompt_version": ji.get("prompt_version"),
            },
            warnings=warns,
        )
    return _run


def _build_user_content(user_prompt: str, text: str) -> str:
    return f"{user_prompt}\n\n# Document\n\n{text}"


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        fence_close = stripped.find("```", 3)
        if fence_close != -1:
            stripped = stripped[stripped.find("\n") + 1: fence_close]
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"no JSON object found in {text[:200]!r}")
        obj = json.loads(stripped[start: end + 1])
    if not isinstance(obj, dict):
        raise ValueError(f"top-level JSON is {type(obj).__name__}, not an object")
    return obj


def _make_api_judge(kind: str, *, model: str | None, temperature: float, max_tokens: int) -> JudgeBackend:
    if kind == "agent_host":
        model = model or "host-resolved"
    elif not model:
        raise JudgeError(f"{kind} judge requires --judge-model")

    def _build_result(payload, raw_text, identity, text):  # type: ignore[no-untyped-def]
        # The API path validates against the same contract; we defer span/type
        # validation to validate_commitments at the surface for a known doc label.
        return JudgeResult(
            commitments=[],  # filled by the surface via validate_commitments(payload)
            judge_identity=identity,
            raw_response=raw_text,
        )

    api = judge_backends.make_api_judge(
        kind,
        model=model,
        system_preamble=_SYSTEM_PREAMBLE,
        user_prompt=render_prompt(),
        temperature=temperature,
        max_tokens=max_tokens,
        build_user_content=_build_user_content,
        build_result=_build_result,
        judge_error=JudgeError,
        extract_json=_extract_json,
    )

    def _run(doc: str, text: str) -> JudgeResult:
        result = api(text)
        payload = _extract_json(result.raw_response or "{}")
        commitments, warns = validate_commitments(payload, doc=doc, text_len=len(text))
        return JudgeResult(
            commitments=commitments,
            judge_identity=result.judge_identity,
            raw_response=result.raw_response,
            warnings=warns,
        )
    return _run


def build_judge(
    kind: str,
    *,
    manifest_path: Path | str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
) -> JudgeBackend:
    """Construct a judge backend by kind: ``mock`` (deterministic, M1),
    ``manifest`` (needs manifest_path), or ``anthropic``/``openai``/``gemini``
    (lazy SDK import + credentials in env; need ``model``; M2)."""
    if kind == "mock":
        return _mock_judge()
    if kind == "manifest":
        if manifest_path is None:
            raise JudgeError("manifest judge requires manifest_path")
        return _manifest_judge(Path(manifest_path))
    if kind in judge_backends.PROVIDERS:
        return _make_api_judge(kind, model=model, temperature=temperature, max_tokens=max_tokens)
    raise JudgeError(f"unknown judge kind: {kind!r}")


def fingerprint_prompt(prompt_text: str = "") -> str:
    """SHA-256 of the system preamble + canonical prompt (provenance)."""
    body = _SYSTEM_PREAMBLE + "\n" + (prompt_text or render_prompt())
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
