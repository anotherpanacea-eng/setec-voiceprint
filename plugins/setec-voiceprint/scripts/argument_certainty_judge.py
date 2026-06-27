#!/usr/bin/env python3
"""argument_certainty_judge.py — the LLM-judge seam for
``argument_certainty_calibration``.

A NEW ``argument_judge``-style judge pass over FREE TEXT. ``argument_judge``
labels PARAGRAPHS with ``{role, mode}`` — it does NOT extract claims. This is a
different shape and a different pass:

  ``extract_claims(text) -> list[Claim]`` — per document, extract the
  load-bearing claims as nodes, each with a verbatim locus (start/end char +
  exact quote), a short normalized statement, and a per-claim
  ``support ∈ {none, gestured, substantiated}`` (does the claim carry an
  attached reason / evidence / warrant in the text). One judge pass = claims +
  support (spec P1-3: no separate support_judge).

Per-claim offsets are validated by ``text[start:end] == quote`` at the surface
(offset-exact, like the cross-doc mock) — a fabricated offset is DROPPED, never
trusted.

**Certainty is NOT judged here.** Expressed certainty
(``tentative/measured/assertive``) is the deterministic frozen-lexicon substrate
computed in the surface over each claim's quote span — the judge supplies the
claim spans + the support level only (spec P1-2/P1-4).

Backends mirror ``argument_judge`` / ``cross_doc_consistency_judge``:
``manifest`` (pre-computed labels) / ``mock`` (deterministic, CI-safe) /
``anthropic`` (lazy SDK import, fail-loud). **M1 = mock-deterministic;
M2 = anthropic.**

The mock judge is the contract that keeps CI dependency-free AND exercises the
real machinery: it extracts claims by reading lightweight in-text markers the
fixtures carry (``[[claim support=none]]`` annotations on a sentence). The
legitimate-strong-claim DEFENSE is NOT decided here — it is a mechanical scan of
the loci text in the surface (``argument_certainty_calibration.py``), so the
firewall's load-bearing filter stays in deterministic Python, not the judge.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import judge_backends  # type: ignore  # noqa: E402
from argument_certainty_calibration_schema import SUPPORT_OPTIONS  # type: ignore  # noqa: E402

__all__ = [
    "Claim",
    "JudgeResult",
    "JudgeError",
    "build_judge",
    "render_prompt",
    "validate_claims",
    "fingerprint_prompt",
    "utc_now",
]


class JudgeError(RuntimeError):
    """Raised when a judge backend cannot produce a valid result."""


@dataclass(frozen=True)
class Claim:
    """One load-bearing claim extracted from a document.

    ``start_char``/``end_char``/``quote`` are the verbatim locus; ``statement``
    is the judge's short neutral paraphrase; ``support`` is one of
    ``SUPPORT_OPTIONS`` (the evidential support the claim carries in the text).
    The expressed CERTAINTY is NOT here — it is computed deterministically in the
    surface from the frozen lexicon over ``quote``."""

    topic_ref: str
    statement: str
    start_char: int
    end_char: int
    quote: str
    support: str

    def loci(self) -> dict[str, Any]:
        return {
            "start_char": self.start_char,
            "end_char": self.end_char,
            "quote": self.quote,
        }


@dataclass
class JudgeResult:
    """Per-document extraction result + provenance."""

    claims: list[Claim]
    judge_identity: dict[str, Any]
    raw_response: str | None = None
    warnings: list[str] = field(default_factory=list)


# ----------------- prompt construction ----------------------------

_SYSTEM_PREAMBLE = (
    "You are a careful argument-structure annotator. You will be shown one "
    "argument-shaped document and asked to extract its LOAD-BEARING CLAIMS: the "
    "assertions the argument actually relies on. For each claim, return a "
    "verbatim character span (start_char, end_char) and the exact quoted text, a "
    "short neutral paraphrase, a stable topic_ref string, and a SUPPORT level — "
    "how much evidence/reason the claim carries IN THE TEXT. Do NOT judge whether "
    "the claim is true, whether the author is overconfident, arrogant, or honest, "
    "or who wrote it. Do NOT score the certainty of the claim — only its support. "
    "When you are unsure whether something is a load-bearing claim, omit it rather "
    "than inventing one. Return valid JSON in the schema you are shown."
)


def render_prompt() -> str:
    """Build the user-side extraction prompt (support legend + output format).
    The system preamble is prepended for API judges."""
    lines: list[str] = []
    lines.append("# SUPPORT — how much evidence/reason the claim carries IN THE TEXT\n")
    lines.append("Choose exactly one per claim:")
    lines.append(
        "- `none`: the claim is asserted with no attached reason, evidence, or warrant."
    )
    lines.append(
        "- `gestured`: a reason is alluded to or named but not actually given "
        "(an appeal, a citation-by-name, a hand-wave)."
    )
    lines.append(
        "- `substantiated`: the claim carries a real attached reason, evidence, "
        "data, or worked warrant in the text."
    )
    lines.append(
        "\n# topic_ref — a stable id for the proposition the claim is about\n"
    )
    lines.append(
        "Assign a short, stable string id (e.g. \"t_min_wage\") to the proposition "
        "a claim is about. This is descriptive only; reuse the same id for claims "
        "about the same proposition. If unsure, mint a per-claim id."
    )
    lines.append("\n# Output format\n")
    lines.append(
        "Return a single JSON object with one key, `claims`: an array. Each entry "
        'is `{"topic_ref": <string>, "statement": <short neutral paraphrase>, '
        '"start_char": <int>, "end_char": <int>, "quote": <verbatim text of the '
        'span>, "support": <"none"|"gestured"|"substantiated">}`. The span MUST be '
        "exact: text[start_char:end_char] must equal quote verbatim. Do not "
        "include the certainty, any verdict, or any other keys.\n"
    )
    return "\n".join(lines)


# ----------------- validation -------------------------------------

def validate_claims(
    payload: dict[str, Any], *, text_len: int
) -> tuple[list[Claim], list[str]]:
    """Return ``(claims, warnings)`` from a judge payload. An entry with a bad
    support value, an out-of-range span, or a missing required field is DROPPED
    with a warning — never silently coerced (a judge emitting the wrong
    vocabulary is a judge-config problem, not a data-cleaning one). Mirrors
    ``cross_doc_consistency_judge.validate_commitments``' skip-and-warn
    discipline.

    NOTE: span↔quote OFFSET EXACTNESS (``text[start:end] == quote``) is NOT
    checked here (this helper does not hold ``text``) — the surface validates it
    against the real document and DROPS a non-matching claim. A fabricated
    cross-reference therefore fails at the surface, never trusted."""
    warnings: list[str] = []
    raw = payload.get("claims")
    out: list[Claim] = []
    if not isinstance(raw, list):
        warnings.append(
            f"judge output missing a 'claims' list (got {type(raw).__name__})"
        )
        return out, warnings
    for pos, entry in enumerate(raw):
        if not isinstance(entry, dict):
            warnings.append(f"claim {pos} is not a mapping; dropped")
            continue
        support = entry.get("support")
        if support not in SUPPORT_OPTIONS:
            warnings.append(f"claim {pos} support {support!r} not in vocab; dropped")
            continue
        topic = entry.get("topic_ref")
        if not isinstance(topic, str) or not topic.strip():
            topic = f"t_{pos}"
        statement = entry.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            warnings.append(f"claim {pos} missing statement; dropped")
            continue
        start = entry.get("start_char")
        end = entry.get("end_char")
        if (
            not isinstance(start, int) or isinstance(start, bool)
            or not isinstance(end, int) or isinstance(end, bool)
            or start < 0 or end < start or end > text_len
        ):
            warnings.append(
                f"claim {pos} span ({start},{end}) out of range [0,{text_len}]; dropped"
            )
            continue
        quote = entry.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            warnings.append(f"claim {pos} missing quote; dropped")
            continue
        out.append(Claim(
            topic_ref=topic.strip(), statement=statement.strip(),
            start_char=start, end_char=end, quote=quote, support=support,
        ))
    return out, warnings


# ----------------- mock marker parsing ----------------------------
# The mock judge extracts claims from lightweight in-text markers the fixtures
# carry, so CI exercises the real certainty/support/alignment machinery
# deterministically. A marker annotates ONE sentence:
#   [[claim support=none]] <the claimed sentence text.>
# The quote/span are the marked sentence (the text after the marker up to the
# next marker or end). This is a CI scaffold; an API judge reads raw prose.
_MARKER_RE = re.compile(r"\[\[\s*claim\b(?P<body>[^\]]*?)\s*\]\]")


def _parse_marker_body(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for tok in body.split():
        if "=" in tok:
            k, _, v = tok.partition("=")
            out[k.strip()] = v.strip()
    return out


def _mock_extract(text: str) -> list[Claim]:
    """Deterministic marker-driven extraction (the mock contract). The span is
    the sentence text AFTER the marker up to the next marker (or end of text),
    with offsets tightened to the stripped quote so ``text[start:end] == quote``
    holds exactly."""
    claims: list[Claim] = []
    markers = list(_MARKER_RE.finditer(text))
    for i, m in enumerate(markers):
        attrs = _parse_marker_body(m.group("body"))
        support = attrs.get("support", "none")
        if support not in SUPPORT_OPTIONS:
            support = "none"
        topic = attrs.get("topic", f"t_{i}")
        span_start = m.end()
        span_end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        chunk = text[span_start:span_end]
        quote = chunk.strip()
        if not quote:
            continue
        lead = len(chunk) - len(chunk.lstrip())
        real_start = span_start + lead
        real_end = real_start + len(quote)
        claims.append(Claim(
            topic_ref=topic, statement=quote[:120],
            start_char=real_start, end_char=real_end, quote=quote, support=support,
        ))
    return claims


# ----------------- judge backends ---------------------------------

JudgeBackend = Callable[[str], JudgeResult]  # (text) -> JudgeResult


def _mock_judge() -> JudgeBackend:
    def _run(text: str) -> JudgeResult:
        return JudgeResult(
            claims=_mock_extract(text),
            judge_identity={"kind": "mock"},
        )
    return _run


def _manifest_judge(manifest_path: Path) -> JudgeBackend:
    """Read a pre-computed claim list from a JSON manifest:
    ``{"claims": [...], "judge_identity": {...}}``."""
    try:
        data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise JudgeError(f"manifest {manifest_path}: cannot read ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise JudgeError(f"manifest {manifest_path}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("claims"), list):
        raise JudgeError(f"manifest {manifest_path}: missing 'claims' list")
    ji = data.get("judge_identity")
    ji = ji if isinstance(ji, dict) else {}

    def _run(text: str) -> JudgeResult:
        claims, warns = validate_claims(data, text_len=len(text))
        return JudgeResult(
            claims=claims,
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
    if kind == "agent_host":
        model = model or "host-resolved"
    elif not model:
        raise JudgeError(f"{kind} judge requires --judge-model")

    def _build_result(payload, raw_text, identity, text):  # type: ignore[no-untyped-def]
        # The surface re-parses raw_response and validates against the same
        # contract via validate_claims for a known text length.
        return JudgeResult(
            claims=[],
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
        claims, warns = validate_claims(payload, text_len=len(text))
        return JudgeResult(
            claims=claims,
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
