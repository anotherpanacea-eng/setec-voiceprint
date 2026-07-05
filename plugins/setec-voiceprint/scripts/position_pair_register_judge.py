#!/usr/bin/env python3
"""position_pair_register_judge.py — the LLM-judge seam for the
``position_pair_register`` surface (stance-consistency PR 1, producer side).

A NEW judge family, modeled on ``cross_doc_consistency_judge.py`` but deliberately
STRIPPED of every relation channel. The sibling's judge emits a ``stance`` polarity
and its surface computes a ``detect_tension`` RELATION; **this judge does neither.**
The model is asked to do ONE content-adjacent thing and nothing else: identify
passages that **address the same question Q** and emit them as pairs under a shared,
neutral interrogative ``question`` label. It never says the passages agree, conflict,
oppose, or which is right — the human owns 100% of that call (see the surface's
``position_pair_register.py`` docstring and the spec's firewall rationale, SPEC.md v3
"The v1 surface").

Per-pair output shape (and nothing else)::

    {"question": <neutral interrogative Q>,
     "a": {"start_char": <int>, "end_char": <int>, "quote": <verbatim>},
     "b": {"start_char": <int>, "end_char": <int>, "quote": <verbatim>}}

There is NO stance, NO polarity, NO relation, NO ``detect_tension`` anywhere. The
locus shape (start_char/end_char/quote) mirrors ``_LOCUS_REQUIRED``
(``cross_doc_consistency_schema.py:149``); ``doc`` is attached by the surface (this
is a single-work surface — one document — so the judge does not carry it).

Backends mirror the sibling: ``manifest`` (pre-computed pairs) / ``mock``
(deterministic, CI-safe) / ``anthropic``/``openai``/``gemini``/``agent_host`` (lazy
SDK import via ``judge_backends``). **M1 = mock/manifest (deterministic, CI-safe);
M2 = live providers (pass-through via ``judge_backends``, untested beyond
registration).**

The mock judge is the deterministic CI contract: it extracts pairs from lightweight
in-text markers the fixtures carry::

    [[q=How does X work? pair=p1 side=a]] <the passage text.>
    [[q=How does X work? pair=p1 side=b]] <the other passage text.>

Two markers sharing a ``pair`` id (one ``side=a``, one ``side=b``) form one emitted
pair, labeled with their shared ``q``. The quote/span are the marked passage. This
is a CI scaffold; a live judge reads raw prose.
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

__all__ = [
    "PositionPair",
    "JudgeResult",
    "JudgeError",
    "build_judge",
    "render_prompt",
    "validate_pairs",
    "fingerprint_prompt",
    "utc_now",
]


class JudgeError(RuntimeError):
    """Raised when a judge backend cannot produce a valid result."""


@dataclass(frozen=True)
class PositionPair:
    """One pair of passages that address the same question ``question``.

    ``question`` is a NEUTRAL interrogative label naming the shared question (never
    a characterization of the answers). ``a`` and ``b`` are the two passage loci,
    each a ``{doc, start_char, end_char, quote}`` dict (``doc`` filled by the
    surface). There is deliberately NO stance / polarity / relation field — the
    judge points at two passages and names their shared question, nothing more."""

    question: str
    a_start_char: int
    a_end_char: int
    a_quote: str
    b_start_char: int
    b_end_char: int
    b_quote: str

    def _locus(self, doc: str, start: int, end: int, quote: str) -> dict[str, Any]:
        return {"doc": doc, "start_char": start, "end_char": end, "quote": quote}

    def a_locus(self, doc: str) -> dict[str, Any]:
        return self._locus(doc, self.a_start_char, self.a_end_char, self.a_quote)

    def b_locus(self, doc: str) -> dict[str, Any]:
        return self._locus(doc, self.b_start_char, self.b_end_char, self.b_quote)


@dataclass
class JudgeResult:
    """The extraction result + provenance for one work."""

    pairs: list[PositionPair]
    judge_identity: dict[str, Any]
    raw_response: str | None = None
    warnings: list[str] = field(default_factory=list)


# ----------------- prompt construction ----------------------------
# The no-verdict discipline lives here (system preamble) AND is re-enforced
# mechanically at the surface (the F4 Q-gate + the F3 banned-key walk). The
# preamble is the FIRST line of defense; the surface's Python is the guarantee.

_SYSTEM_PREAMBLE = (
    "You are a careful reading assistant working on ONE long nonfiction "
    "argument-shaped document. Your ONLY task is to find passages that ADDRESS "
    "THE SAME QUESTION and pair them. For each pair, name the shared question Q "
    "as a NEUTRAL interrogative (a plain question the two passages both speak to), "
    "and return each passage's verbatim character span (start_char, end_char) and "
    "the exact quoted text. "
    "You must NOT decide, state, or imply whether the two passages agree, "
    "conflict, contradict, oppose, are in tension, or which one is correct — that "
    "is a human's job, not yours. Do NOT rank pairs by how much they disagree; do "
    "NOT characterize the answers at all. Q names the QUESTION, never the "
    "relationship between the answers (write 'What does the author hold about "
    "market regulation?', never 'the tension between markets and regulation'). "
    "Emit pairs in the order they appear in the document. When you are unsure two "
    "passages truly share a question, omit the pair rather than invent one. "
    "Return valid JSON in the schema you are shown."
)


def render_prompt() -> str:
    """Build the user-side extraction prompt (the same-question instruction +
    output format). The system preamble is prepended for API judges."""
    lines: list[str] = []
    lines.append("# Task — pair passages that address the SAME QUESTION\n")
    lines.append(
        "Read the document and find pairs of passages that both speak to the SAME "
        "underlying question. For each such pair, give the shared question Q as a "
        "neutral interrogative and quote BOTH passages verbatim with their exact "
        "character offsets."
    )
    lines.append("\n# Q — the shared question label\n")
    lines.append(
        "Q must be a plain interrogative that NAMES the question both passages "
        "address (e.g. \"What is the author's position on X?\", \"How should X be "
        "handled?\"). Q must NOT characterize, compare, or judge the answers — it "
        "never says the passages agree, conflict, oppose, or are in tension, and "
        "it uses no relational vocabulary. You are only labeling the question, not "
        "the relationship."
    )
    lines.append("\n# Output format\n")
    lines.append(
        "Return a single JSON object with one key, `pairs`: an array. Each entry is "
        '`{"question": <neutral interrogative string>, "a": {"start_char": <int>, '
        '"end_char": <int>, "quote": <verbatim text of passage A>}, "b": '
        '{"start_char": <int>, "end_char": <int>, "quote": <verbatim text of '
        'passage B>}}`. Emit pairs in document order (by passage A\'s start_char). '
        "Do not include any other keys, no stance/relation/agreement field, no "
        "prose, and no explanations.\n"
    )
    return "\n".join(lines)


# ----------------- validation -------------------------------------

def validate_pairs(
    payload: dict[str, Any], *, text_len: int
) -> tuple[list[PositionPair], list[str]]:
    """Return ``(pairs, warnings)`` from a judge payload. A pair with a missing
    question, a missing/blank quote, or an out-of-range span is DROPPED with a
    warning — never silently coerced (mirrors
    ``cross_doc_consistency_judge.validate_commitments``' skip-and-warn
    discipline). The Q-VOCABULARY / interrogative-form gate is NOT here — that is
    the surface's F4 posture gate (``position_pair_register.py``), which also
    counts refusals as a disclosure. This validator only enforces the structural
    contract (well-formed loci, present question)."""
    warnings: list[str] = []
    raw = payload.get("pairs")
    out: list[PositionPair] = []
    if not isinstance(raw, list):
        warnings.append(
            f"judge output missing a 'pairs' list (got {type(raw).__name__})"
        )
        return out, warnings
    for pos, entry in enumerate(raw):
        if not isinstance(entry, dict):
            warnings.append(f"pair {pos} is not a mapping; dropped")
            continue
        question = entry.get("question")
        if not isinstance(question, str) or not question.strip():
            warnings.append(f"pair {pos} missing question; dropped")
            continue
        sides = _validate_sides(entry, pos, text_len, warnings)
        if sides is None:
            continue
        (a_start, a_end, a_quote), (b_start, b_end, b_quote) = sides
        out.append(PositionPair(
            question=question.strip(),
            a_start_char=a_start, a_end_char=a_end, a_quote=a_quote,
            b_start_char=b_start, b_end_char=b_end, b_quote=b_quote,
        ))
    return out, warnings


def _validate_sides(
    entry: dict[str, Any], pos: int, text_len: int, warnings: list[str]
) -> tuple[tuple[int, int, str], tuple[int, int, str]] | None:
    """Validate both sides (``a``/``b``) of a candidate pair. Returns
    ``((a_start,a_end,a_quote),(b_start,b_end,b_quote))`` or ``None`` (dropped,
    with a warning appended)."""
    parsed: list[tuple[int, int, str]] = []
    for side in ("a", "b"):
        span = entry.get(side)
        if not isinstance(span, dict):
            warnings.append(f"pair {pos} side {side!r} is not a mapping; dropped")
            return None
        start = span.get("start_char")
        end = span.get("end_char")
        if (
            not isinstance(start, int) or isinstance(start, bool)
            or not isinstance(end, int) or isinstance(end, bool)
            or start < 0 or end < start or end > text_len
        ):
            warnings.append(
                f"pair {pos} side {side!r} span ({start},{end}) out of range "
                f"[0,{text_len}]; dropped"
            )
            return None
        quote = span.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            warnings.append(f"pair {pos} side {side!r} missing quote; dropped")
            return None
        parsed.append((start, end, quote))
    return parsed[0], parsed[1]


# ----------------- mock marker parsing ----------------------------
# The mock judge extracts pairs from lightweight in-text markers the fixtures
# carry, so CI exercises the real machinery deterministically. A marker annotates
# ONE passage:
#   [[q=How does X work? pair=p1 side=a]] <the passage text.>
# Two markers sharing a `pair` id (one side=a, one side=b) form one pair, labeled
# with their shared `q`. The quote/span are the marked passage (text after the
# marker up to the next marker or end). This is a CI scaffold; a live judge reads
# raw prose.
_MARKER_RE = re.compile(r"\[\[\s*(?P<body>[^\]]*?)\s*\]\]")


def _parse_marker_body(body: str) -> dict[str, str]:
    """Parse a marker body of ``pair=<id> side=<a|b> q=<question...>``. ``pair`` and
    ``side`` are single-token keys; ``q`` may contain spaces AND a ``?``, so it
    consumes every token from ``q=`` to the end of the body (order-independent for
    pair/side, but ``q=`` must be the LAST key since it swallows the remainder)."""
    out: dict[str, str] = {}
    tokens = body.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("pair="):
            out["pair"] = tok[len("pair="):].strip()
        elif tok.startswith("side="):
            out["side"] = tok[len("side="):].strip()
        elif tok.startswith("q="):
            # q consumes the rest of the body verbatim (may contain spaces / '?').
            first = tok[len("q="):]
            rest = tokens[i + 1:]
            out["q"] = " ".join([first, *rest]).strip()
            break
        i += 1
    return out


def _mock_extract(text: str) -> list[PositionPair]:
    """Deterministic marker-driven extraction (the mock contract). Groups markers
    by their ``pair`` id, requires one ``side=a`` and one ``side=b`` per group, and
    emits pairs in document order (by side-a's start offset)."""
    markers = list(_MARKER_RE.finditer(text))
    # Collect (pair_id, side) -> (q, start, end, quote).
    groups: dict[str, dict[str, tuple[str, int, int, str]]] = {}
    for i, m in enumerate(markers):
        attrs = _parse_marker_body(m.group("body"))
        pair_id = attrs.get("pair")
        side = attrs.get("side")
        q = attrs.get("q")
        if not pair_id or side not in ("a", "b") or not q:
            continue
        span_start = m.end()
        span_end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        segment = text[span_start:span_end]
        quote = segment.strip()
        if not quote:
            continue
        lead = len(segment) - len(segment.lstrip())
        real_start = span_start + lead
        real_end = real_start + len(quote)
        groups.setdefault(pair_id, {})[side] = (q, real_start, real_end, quote)

    pairs: list[PositionPair] = []
    for pair_id, sides in groups.items():
        if "a" not in sides or "b" not in sides:
            continue
        qa, a_start, a_end, a_quote = sides["a"]
        _qb, b_start, b_end, b_quote = sides["b"]
        # The shared question label comes from side a (the sides carry the same q).
        pairs.append(PositionPair(
            question=qa,
            a_start_char=a_start, a_end_char=a_end, a_quote=a_quote,
            b_start_char=b_start, b_end_char=b_end, b_quote=b_quote,
        ))
    # Document order: by passage A's start offset (the surface re-sorts too, but a
    # deterministic mock order keeps fixtures stable).
    pairs.sort(key=lambda p: (p.a_start_char, p.b_start_char))
    return pairs


# ----------------- judge backends ---------------------------------

JudgeBackend = Callable[[str], JudgeResult]  # (text) -> JudgeResult


def _mock_judge() -> JudgeBackend:
    def _run(text: str) -> JudgeResult:
        return JudgeResult(
            pairs=_mock_extract(text),
            judge_identity={"kind": "mock"},
        )
    return _run


def _manifest_judge(manifest_path: Path) -> JudgeBackend:
    """Read pre-computed pairs from a JSON manifest:
    ``{"pairs": [...], "judge_identity": {...}}`` (validated by
    ``validate_pairs`` against the target's length)."""
    try:
        data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise JudgeError(f"manifest {manifest_path}: cannot read ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise JudgeError(f"manifest {manifest_path}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise JudgeError(f"manifest {manifest_path}: top-level JSON must be an object")
    ji = data.get("judge_identity")
    ji = ji if isinstance(ji, dict) else {}

    def _run(text: str) -> JudgeResult:
        pairs, warns = validate_pairs(data, text_len=len(text))
        return JudgeResult(
            pairs=pairs,
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


def _make_api_judge(
    kind: str, *, model: str | None, temperature: float, max_tokens: int
) -> JudgeBackend:
    """Live-provider backend (M2). Pass-through via ``judge_backends.make_api_judge``
    — untested beyond construction/registration in PR 1; the mock/manifest backends
    are the CI contract."""
    if kind == "agent_host":
        model = model or "host-resolved"
    elif not model:
        raise JudgeError(f"{kind} judge requires --judge-model")

    def _build_result(payload, raw_text, identity, text):  # type: ignore[no-untyped-def]
        # The surface re-parses raw_response with validate_pairs for a known
        # text length; this seam just carries the provenance + raw text.
        return JudgeResult(
            pairs=[],
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

    def _run(text: str) -> JudgeResult:
        result = api(text)
        payload = _extract_json(result.raw_response or "{}")
        pairs, warns = validate_pairs(payload, text_len=len(text))
        return JudgeResult(
            pairs=pairs,
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
    ``manifest`` (needs manifest_path, M1), or ``anthropic``/``openai``/``gemini``/
    ``agent_host`` (lazy SDK import + credentials in env; need ``model`` except
    agent_host; M2)."""
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
