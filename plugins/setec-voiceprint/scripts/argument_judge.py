#!/usr/bin/env python3
"""argument_judge.py — pluggable LLM-judge for ArgScope's per-paragraph
argumentative role (B1) + discourse mode (B2) labeling.

Mirrors ``narrative_judge`` (same provider-agnostic plumbing: manifest / mock /
anthropic / openai / gemini; lazy SDK imports; JudgeError; provenance +
fingerprint), with ONE structural difference: where StoryScope judges the whole
story once into a flat feature dict, ArgScope labels a SEQUENCE of paragraphs.
The judge receives the document's paragraphs (in order) and returns one
``{role, mode}`` label per paragraph; the audit surface computes the anchored
signals (``support→proposal`` / ``support→support`` transition rates, the
``argumentation`` mode share) from that label sequence.

Operators choose how labels are produced:

  1. ``manifest`` (default for production): read a pre-computed label sequence
     from a JSON file — run whatever model/ensemble you want outside the audit,
     drop the per-paragraph labels into a manifest, the audit consumes them.
     (Stylometry-to-the-people: the framework ships the methodology, not the
     model selection.)
  2. ``mock``: a deterministic label sequence for tests/CI.
  3. ``anthropic`` / ``openai`` / ``gemini``: API-backed reference adapters,
     lazy-imported; for single-doc spot-checks, not cross-corpus calibration.

D1 (the labelers are a judge, not a regex): argumentative role and discourse
mode are genuine in-context classification tasks (the AGD substitution-test
caveat — `since` causal-vs-temporal etc.); marker lexicons are at most few-shot
priors, never a standalone detector. The judge provenance + prompt fingerprint
are carried in the envelope so a consumer can tell a faithful LLM run from a
mock/heuristic one.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from argument_feature_schema import (  # type: ignore
    MODE_DESCRIPTIONS,
    MODE_OPTIONS,
    ROLE_DESCRIPTIONS,
    ROLE_OPTIONS,
)

__all__ = [
    "JudgeResult",
    "JudgeError",
    "build_judge",
    "render_prompt",
    "validate_labels",
    "fingerprint_prompt",
    "utc_now",
]


class JudgeError(RuntimeError):
    """Raised when a judge backend cannot produce a valid result."""


@dataclass
class JudgeResult:
    """One judged document. ``values`` carries the per-paragraph label sequence
    under the ``paragraphs`` key: a list of ``{"role": str, "mode": str}`` in
    document order. ``judge_identity`` is the provenance dict (always set)."""

    values: dict[str, Any]
    judge_identity: dict[str, Any]
    per_paragraph_confidence: list[float | None] = field(default_factory=list)
    raw_response: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": {"paragraphs": list(self.values.get("paragraphs", []))},
            "per_paragraph_confidence": list(self.per_paragraph_confidence),
            "judge_identity": dict(self.judge_identity),
            "raw_response_truncated": (
                (self.raw_response[:2000] + "…")
                if self.raw_response and len(self.raw_response) > 2000
                else self.raw_response
            ),
        }


# ----------------- prompt construction ----------------------------

_SYSTEM_PREAMBLE = (
    "You are a careful argument-structure annotator. You will be shown a "
    "complete public-debate essay, split into numbered paragraphs, and asked to "
    "label each paragraph with one argumentative ROLE and one discourse MODE. "
    "Read the whole essay before answering. Do not judge the argument's quality, "
    "correctness, or its likely author (human or AI); report only how each "
    "paragraph functions in the argument. Some paragraphs do more than one "
    "thing — choose the single role/mode that DOMINATES the paragraph. Return "
    "valid JSON exactly in the schema you are shown."
)


def render_prompt() -> str:
    """Build the consolidated user-side labeling prompt (role + mode legends +
    output format). The system preamble is prepended for API judges."""
    lines: list[str] = []
    lines.append("# Argumentative ROLE — choose exactly one per paragraph\n")
    for role in ROLE_OPTIONS:
        lines.append(f"- `{role}`: {ROLE_DESCRIPTIONS[role]}")
    lines.append("\n# Discourse MODE — choose exactly one per paragraph\n")
    for mode in MODE_OPTIONS:
        lines.append(f"- `{mode}`: {MODE_DESCRIPTIONS[mode]}")
    lines.append("\n# Output format\n")
    lines.append(
        "Return a single JSON object with one key, `paragraphs`: an array with "
        "exactly one entry per input paragraph, in order. Each entry is "
        '`{"index": <int>, "role": <one role>, "mode": <one mode>}`. Optionally '
        'add `"confidence": <number in [0,1]>` per entry. Do not include the '
        "paragraph text, prose explanations, or any other keys.\n"
    )
    return "\n".join(lines)


def _number_paragraphs(paragraphs: list[str]) -> str:
    """Render the numbered-paragraph block shown to an API judge."""
    return "\n\n".join(f"[{i}] {p}" for i, p in enumerate(paragraphs))


# ----------------- validation -------------------------------------

def validate_labels(
    values: dict[str, Any], *, n_paragraphs: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return ``(cleaned, warnings)``: a length-``n_paragraphs`` list of
    ``{"role": str|None, "mode": str|None}`` aligned to document order.

    A missing/extra entry, a role outside ROLE_OPTIONS, or a mode outside
    MODE_OPTIONS becomes None with a warning — never silently coerced (a judge
    emitting the wrong vocabulary is a judge-config problem, not a data-cleaning
    one). Entries are aligned by their declared ``index`` when present, else by
    position."""
    warnings: list[str] = []
    raw = values.get("paragraphs")
    cleaned: list[dict[str, Any]] = [
        {"role": None, "mode": None} for _ in range(n_paragraphs)
    ]
    if not isinstance(raw, list):
        warnings.append(
            f"judge output missing a 'paragraphs' list "
            f"(got {type(raw).__name__}); all {n_paragraphs} labels null"
        )
        return cleaned, warnings

    seen: set[int] = set()
    for pos, entry in enumerate(raw):
        if not isinstance(entry, dict):
            warnings.append(f"paragraph entry at position {pos} is not a mapping")
            continue
        idx = entry.get("index", pos)
        if not isinstance(idx, int) or not (0 <= idx < n_paragraphs):
            warnings.append(
                f"paragraph entry at position {pos} has out-of-range index "
                f"{idx!r} (n_paragraphs={n_paragraphs})"
            )
            continue
        if idx in seen:
            warnings.append(f"duplicate label for paragraph index {idx}; keeping first")
            continue
        seen.add(idx)
        role = entry.get("role")
        mode = entry.get("mode")
        if role in ROLE_OPTIONS:
            cleaned[idx]["role"] = role
        else:
            warnings.append(f"paragraph {idx}: role {role!r} not in {list(ROLE_OPTIONS)}")
        if mode in MODE_OPTIONS:
            cleaned[idx]["mode"] = mode
        else:
            warnings.append(f"paragraph {idx}: mode {mode!r} not in {list(MODE_OPTIONS)}")
    missing = [i for i in range(n_paragraphs) if i not in seen]
    if missing:
        warnings.append(
            f"judge labeled {len(seen)}/{n_paragraphs} paragraphs; missing "
            f"indices {missing[:10]}{'…' if len(missing) > 10 else ''}"
        )
    if len(raw) > n_paragraphs:
        warnings.append(
            f"judge emitted {len(raw)} entries for {n_paragraphs} paragraphs "
            f"(extras ignored)"
        )
    return cleaned, warnings


# ----------------- judge backends ---------------------------------

JudgeBackend = Callable[[list[str]], JudgeResult]


def _confidences(raw: Any, n: int) -> list[float | None]:
    """Pull optional per-paragraph confidences from a judge's paragraph list,
    aligned to index; default all None."""
    out: list[float | None] = [None] * n
    if isinstance(raw, list):
        for pos, entry in enumerate(raw):
            if not isinstance(entry, dict):
                continue
            idx = entry.get("index", pos)
            c = entry.get("confidence")
            if isinstance(idx, int) and 0 <= idx < n and isinstance(c, (int, float)):
                out[idx] = float(c)
    return out


def _manifest_judge(manifest_path: Path) -> JudgeBackend:
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    values = data.get("values")
    if not isinstance(values, dict) or "paragraphs" not in values:
        raise JudgeError(
            f"manifest {manifest_path}: missing 'values.paragraphs' list"
        )

    def _run(paragraphs: list[str]) -> JudgeResult:
        return JudgeResult(
            values={"paragraphs": list(values.get("paragraphs", []))},
            per_paragraph_confidence=_confidences(
                values.get("paragraphs"), len(paragraphs)
            ),
            judge_identity={
                "kind": "manifest",
                "manifest_path": str(manifest_path),
                "model": data.get("judge_identity", {}).get("model"),
                "model_revision": data.get("judge_identity", {}).get("model_revision"),
                "prompt_version": data.get("judge_identity", {}).get("prompt_version"),
            },
            raw_response=None,
        )

    return _run


def _mock_judge(role_index: int = 1, mode_index: int = 0) -> JudgeBackend:
    """Deterministic judge for tests/fixtures: labels every paragraph with the
    role at ``role_index`` (default 1 = 'support') and the mode at ``mode_index``
    (default 0 = 'argumentation'), clipped to the option lists."""
    role = ROLE_OPTIONS[min(role_index, len(ROLE_OPTIONS) - 1)]
    mode = MODE_OPTIONS[min(mode_index, len(MODE_OPTIONS) - 1)]

    def _run(paragraphs: list[str]) -> JudgeResult:
        return JudgeResult(
            values={
                "paragraphs": [
                    {"index": i, "role": role, "mode": mode}
                    for i in range(len(paragraphs))
                ]
            },
            judge_identity={
                "kind": "mock",
                "role_index": role_index,
                "mode_index": mode_index,
            },
        )

    return _run


def _build_user_content(user_prompt: str, paragraphs: list[str]) -> str:
    return f"{user_prompt}\n\n# Essay (numbered paragraphs)\n\n{_number_paragraphs(paragraphs)}"


def _api_judge_anthropic(*, model, system_preamble, user_prompt, temperature, max_tokens) -> JudgeBackend:
    try:
        import anthropic  # type: ignore
    except ImportError as exc:
        raise JudgeError(
            "anthropic backend requires the `anthropic` SDK; `pip install anthropic` first."
        ) from exc
    try:
        client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from env
    except Exception as exc:  # noqa: BLE001
        raise JudgeError(f"anthropic client construction failed: {exc}") from exc

    def _run(paragraphs: list[str]) -> JudgeResult:
        try:
            msg = client.messages.create(
                model=model, max_tokens=max_tokens, temperature=temperature,
                system=system_preamble,
                messages=[{"role": "user", "content": _build_user_content(user_prompt, paragraphs)}],
            )
        except Exception as exc:  # noqa: BLE001
            raise JudgeError(f"anthropic provider call failed: {exc}") from exc
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        try:
            payload = _extract_json(text)
        except ValueError as exc:
            raise JudgeError(f"anthropic judge returned non-JSON: {exc}") from exc
        return JudgeResult(
            values={"paragraphs": payload.get("paragraphs", [])},
            per_paragraph_confidence=_confidences(payload.get("paragraphs"), len(paragraphs)),
            judge_identity={"kind": "anthropic", "model": model,
                            "stop_reason": getattr(msg, "stop_reason", None)},
            raw_response=text,
        )

    return _run


def _api_judge_openai(*, model, system_preamble, user_prompt, temperature, max_tokens) -> JudgeBackend:
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise JudgeError(
            "openai backend requires the `openai` SDK; `pip install openai` first."
        ) from exc
    try:
        client = OpenAI()  # OPENAI_API_KEY from env
    except Exception as exc:  # noqa: BLE001
        raise JudgeError(f"openai client construction failed: {exc}") from exc

    def _run(paragraphs: list[str]) -> JudgeResult:
        try:
            resp = client.chat.completions.create(
                model=model, temperature=temperature, max_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_preamble},
                    {"role": "user", "content": _build_user_content(user_prompt, paragraphs)},
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise JudgeError(f"openai provider call failed: {exc}") from exc
        text = resp.choices[0].message.content or ""
        try:
            payload = _extract_json(text)
        except ValueError as exc:
            raise JudgeError(f"openai judge returned non-JSON: {exc}") from exc
        return JudgeResult(
            values={"paragraphs": payload.get("paragraphs", [])},
            per_paragraph_confidence=_confidences(payload.get("paragraphs"), len(paragraphs)),
            judge_identity={"kind": "openai", "model": model,
                            "finish_reason": resp.choices[0].finish_reason},
            raw_response=text,
        )

    return _run


def _api_judge_gemini(*, model, system_preamble, user_prompt, temperature, max_tokens) -> JudgeBackend:
    try:
        from google import genai  # type: ignore
    except ImportError as exc:
        raise JudgeError(
            "gemini backend requires the `google-genai` SDK; `pip install google-genai` first."
        ) from exc
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise JudgeError("gemini backend requires GOOGLE_API_KEY or GEMINI_API_KEY in the environment.")
    try:
        client = genai.Client(api_key=api_key)
    except Exception as exc:  # noqa: BLE001
        raise JudgeError(f"gemini client construction failed: {exc}") from exc

    def _run(paragraphs: list[str]) -> JudgeResult:
        try:
            resp = client.models.generate_content(
                model=model,
                contents=[{"role": "user", "parts": [{"text": (
                    f"{system_preamble}\n\n{_build_user_content(user_prompt, paragraphs)}"
                )}]}],
                config={"temperature": temperature, "max_output_tokens": max_tokens,
                        "response_mime_type": "application/json"},
            )
        except Exception as exc:  # noqa: BLE001
            raise JudgeError(f"gemini provider call failed: {exc}") from exc
        text = resp.text or ""
        try:
            payload = _extract_json(text)
        except ValueError as exc:
            raise JudgeError(f"gemini judge returned non-JSON: {exc}") from exc
        return JudgeResult(
            values={"paragraphs": payload.get("paragraphs", [])},
            per_paragraph_confidence=_confidences(payload.get("paragraphs"), len(paragraphs)),
            judge_identity={"kind": "gemini", "model": model},
            raw_response=text,
        )

    return _run


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction: a bare object or a fenced ```json block.
    Raises ValueError on parse failure."""
    stripped = text.strip()
    if stripped.startswith("```"):
        fence_close = stripped.find("```", 3)
        if fence_close != -1:
            stripped = stripped[stripped.find("\n") + 1: fence_close]
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"no JSON object found in {text[:200]!r}")
        return json.loads(stripped[start: end + 1])


# ----------------- factory ---------------------------------------

def build_judge(
    kind: str,
    *,
    manifest_path: Path | str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    mock_role_index: int = 1,
    mock_mode_index: int = 0,
) -> JudgeBackend:
    """Construct a judge backend by kind: ``manifest`` (needs manifest_path),
    ``mock`` (deterministic), or ``anthropic``/``openai``/``gemini`` (lazy SDK
    import + credentials in env; need ``model``)."""
    if kind == "manifest":
        if manifest_path is None:
            raise JudgeError("manifest judge requires manifest_path")
        return _manifest_judge(Path(manifest_path))
    if kind == "mock":
        return _mock_judge(mock_role_index, mock_mode_index)
    if kind in ("anthropic", "openai", "gemini"):
        if not model:
            raise JudgeError(f"{kind} judge requires --judge-model")
        common = dict(model=model, system_preamble=_SYSTEM_PREAMBLE,
                      user_prompt=render_prompt(), temperature=temperature,
                      max_tokens=max_tokens)
        if kind == "anthropic":
            return _api_judge_anthropic(**common)
        if kind == "openai":
            return _api_judge_openai(**common)
        return _api_judge_gemini(**common)
    raise JudgeError(f"unknown judge kind: {kind!r}")


def fingerprint_prompt(prompt_text: str = "") -> str:
    """SHA-256 of the system preamble + canonical prompt — provenance: identical
    fingerprints mean byte-identical prompts."""
    body = _SYSTEM_PREAMBLE + "\n" + (prompt_text or render_prompt())
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
