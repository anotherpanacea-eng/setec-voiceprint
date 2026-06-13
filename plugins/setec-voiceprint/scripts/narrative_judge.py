#!/usr/bin/env python3
"""narrative_judge.py — pluggable LLM-judge interface for the 30
core narrative-decision features.

This module exposes a thin abstraction so the narrative-decision
audit script can run without baking in a particular model provider.
Operators choose how feature values are produced:

  1. ``manifest`` (default for production): read pre-computed
     per-feature values from a JSON file. The operator runs whatever
     model they want — Claude, GPT, Gemini, a local Llama, an
     ensemble — outside the audit, drops the results into a JSON
     manifest, and the audit consumes those values. This matches the
     Stylometry-to-the-people discipline: the framework ships the
     methodology, not the model selection.

  2. ``mock``: emit fixed values for testing.

  3. ``anthropic`` / ``openai`` / ``gemini``: API-backed reference
     adapters. Lazy-imported on first use; the relevant SDK and
     credentials must be present in the environment. These exist so
     operators can spot-check a single document end-to-end without
     wiring their own pipeline, but are not the recommended path
     for cross-corpus calibration runs (cost and reproducibility
     concerns; see ``narrative-decision-audit-spec.md``).

The judge interface returns a ``JudgeResult`` per target carrying:

  - ``values``: a dict mapping each feature key to an emitted value.
    For scale/ordinal/binary features the value is a single string
    drawn from ``response_options``. For multi features the value
    is a list of strings, each from ``response_options``.
  - ``per_feature_confidence``: optional float in [0, 1] per feature
    when the judge reports it; missing keys are treated as None.
  - ``judge_identity``: provenance dict for the model that produced
    the values. Always populated.

The audit script handles all encoding, scoring, and license-block
construction; the judge's sole job is to map a story-text to
feature values.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from narrative_feature_schema import (  # type: ignore
    CORE_FEATURES,
    CoreFeature,
)

__all__ = [
    "JudgeResult",
    "JudgeError",
    "build_judge",
    "render_prompt",
    "validate_values",
]


class JudgeError(RuntimeError):
    """Raised when a judge backend cannot produce a valid result."""


@dataclass
class JudgeResult:
    values: dict[str, Any]
    judge_identity: dict[str, Any]
    per_feature_confidence: dict[str, float] = field(default_factory=dict)
    raw_response: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": dict(self.values),
            "per_feature_confidence": dict(self.per_feature_confidence),
            "judge_identity": dict(self.judge_identity),
            "raw_response_truncated": (
                (self.raw_response[:2000] + "…")
                if self.raw_response and len(self.raw_response) > 2000
                else self.raw_response
            ),
        }


# ----------------- prompt construction ----------------------------

_SYSTEM_PREAMBLE = (
    "You are a careful literary-feature annotator. You will be "
    "shown a complete short story and asked to assign values to a "
    "small set of narrative-decision features drawn from the "
    "NarraBench taxonomy. Read the entire story before answering. "
    "Do not reason about the story's quality or its likely author "
    "(human or AI); your only job is to report what is on the page. "
    "Where a feature asks about prevalence (e.g., dialogue function "
    "or sensory modalities), choose all values that are clearly "
    "present, not values that are only marginally hinted at. Return "
    "valid JSON with one key per feature_id, matching the schema "
    "you are shown."
)


def render_prompt(features: Iterable[CoreFeature] = CORE_FEATURES) -> str:
    """Build the consolidated user-side prompt for the judge.

    The paper's pipeline used 10 aspect-prompts (one per NarraBench
    dimension) for production extraction. This single-prompt form is
    a cheaper baseline for single-doc audits; operators running
    cross-corpus calibration are encouraged to swap in a per-dimension
    pipeline that mirrors the paper's setup (see
    ``narrative-decision-audit-spec.md``).
    """
    lines: list[str] = []
    lines.append("# Feature schema\n")
    lines.append(
        "For each feature_id below, report a value drawn from "
        "`options`. Single-select features ('scale', 'ordinal', "
        "'categorical', 'binary') must return one string from the "
        "options list as the value. Multi-select features ('multi') "
        "must return an array of strings (zero or more), each from "
        "the options list.\n"
    )
    lines.append("```json")
    schema_rows: list[dict[str, Any]] = []
    for f in features:
        schema_rows.append({
            "feature_id": f.key,
            "type": f.feature_type,
            "dimension": f.dimension,
            "options": list(f.response_options),
            "question": f.question,
        })
    lines.append(json.dumps(schema_rows, indent=2))
    lines.append("```\n")
    lines.append("# Output format\n")
    lines.append(
        "Return a single JSON object with the exact keys "
        "`values` and `per_feature_confidence`. `values` maps each "
        "feature_id to the chosen value (string for single-select, "
        "array of strings for multi). `per_feature_confidence` "
        "maps each feature_id to a number in [0, 1] indicating how "
        "confident you are in the assignment; omit a key if you "
        "have no confidence estimate.\n"
    )
    lines.append(
        "Do not include the story text, prose explanations, or "
        "any keys other than `values` and `per_feature_confidence` "
        "in your reply.\n"
    )
    return "\n".join(lines)


# ----------------- validation -------------------------------------

def validate_values(
    values: dict[str, Any],
    *,
    features: Iterable[CoreFeature] = CORE_FEATURES,
) -> tuple[dict[str, Any], list[str]]:
    """Return ``(cleaned_values, warnings)``.

    Missing keys become None with a warning. Out-of-range values are
    nulled with a warning; we deliberately do not silently coerce
    (a judge that emits the wrong vocabulary is a judge-config
    problem, not a data-cleaning problem).
    """
    cleaned: dict[str, Any] = {}
    warnings: list[str] = []
    feats = {f.key: f for f in features}
    for key, feat in feats.items():
        if key not in values:
            cleaned[key] = None
            warnings.append(
                f"feature {key!r} missing from judge output"
            )
            continue
        v = values[key]
        if feat.feature_type == "multi":
            if not isinstance(v, list):
                cleaned[key] = None
                warnings.append(
                    f"feature {key!r}: expected list for "
                    f"multi-select, got {type(v).__name__}"
                )
                continue
            valid = [o for o in v if o in feat.response_options]
            invalid = [o for o in v if o not in feat.response_options]
            cleaned[key] = valid
            if invalid:
                warnings.append(
                    f"feature {key!r}: dropping invalid options "
                    f"{invalid}"
                )
        else:
            if not isinstance(v, str):
                cleaned[key] = None
                warnings.append(
                    f"feature {key!r}: expected string, got "
                    f"{type(v).__name__}"
                )
                continue
            if v not in feat.response_options:
                cleaned[key] = None
                warnings.append(
                    f"feature {key!r}: value {v!r} not in "
                    f"options {list(feat.response_options)}"
                )
                continue
            cleaned[key] = v
    extra = sorted(set(values) - set(feats))
    if extra:
        warnings.append(
            f"judge emitted {len(extra)} keys not in schema "
            f"(ignored): {extra[:5]}{'…' if len(extra) > 5 else ''}"
        )
    return cleaned, warnings


# ----------------- judge backends ---------------------------------

JudgeBackend = Callable[[str], JudgeResult]


def _manifest_judge(manifest_path: Path) -> JudgeBackend:
    # A missing or malformed manifest is bad SETUP input, not an internal
    # fault: wrap the read/parse in JudgeError so it surfaces through the
    # entrypoint's `except JudgeError` contract instead of escaping as a raw
    # FileNotFoundError / JSONDecodeError traceback (parity with
    # argument_judge._manifest_judge).
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
    if "values" not in data or not isinstance(data["values"], dict):
        raise JudgeError(
            f"manifest {manifest_path}: missing 'values' object"
        )

    def _run(_story_text: str) -> JudgeResult:
        return JudgeResult(
            values=dict(data["values"]),
            per_feature_confidence=dict(
                data.get("per_feature_confidence", {}) or {}
            ),
            judge_identity={
                "kind": "manifest",
                "manifest_path": str(manifest_path),
                "model": data.get("judge_identity", {}).get("model"),
                "model_revision": data.get(
                    "judge_identity", {}
                ).get("model_revision"),
                "prompt_version": data.get(
                    "judge_identity", {}
                ).get("prompt_version"),
            },
            raw_response=None,
        )

    return _run


def _mock_judge(option_index: int = 0) -> JudgeBackend:
    """Deterministic judge for tests.

    Returns the option at ``option_index`` for every single-select
    feature (clipped to the available options) and the empty list
    for multi-select features.
    """
    def _run(_story_text: str) -> JudgeResult:
        values: dict[str, Any] = {}
        for feat in CORE_FEATURES:
            if feat.feature_type == "multi":
                values[feat.key] = []
                continue
            idx = min(option_index, len(feat.response_options) - 1)
            values[feat.key] = feat.response_options[idx]
        return JudgeResult(
            values=values,
            judge_identity={
                "kind": "mock",
                "option_index": option_index,
            },
        )

    return _run


def _api_judge_anthropic(
    *,
    model: str,
    system_preamble: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
) -> JudgeBackend:
    try:
        import anthropic  # type: ignore
    except ImportError as exc:
        raise JudgeError(
            "anthropic backend requires the `anthropic` SDK; "
            "`pip install anthropic` first."
        ) from exc

    try:
        client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from env
    except Exception as exc:  # noqa: BLE001
        raise JudgeError(
            f"anthropic client construction failed: {exc}"
        ) from exc

    def _run(story_text: str) -> JudgeResult:
        try:
            msg = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_preamble,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"{user_prompt}\n\n# Story text\n\n"
                            f"{story_text}"
                        ),
                    },
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise JudgeError(
                f"anthropic provider call failed: {exc}"
            ) from exc
        text = "".join(
            block.text
            for block in msg.content
            if getattr(block, "type", None) == "text"
        )
        try:
            payload = _extract_json(text)
        except ValueError as exc:
            raise JudgeError(
                f"anthropic judge returned non-JSON: {exc}"
            ) from exc
        return JudgeResult(
            values=payload.get("values", {}),
            per_feature_confidence=payload.get(
                "per_feature_confidence", {}
            ) or {},
            judge_identity={
                "kind": "anthropic",
                "model": model,
                "stop_reason": getattr(msg, "stop_reason", None),
            },
            raw_response=text,
        )

    return _run


def _api_judge_openai(
    *,
    model: str,
    system_preamble: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
) -> JudgeBackend:
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise JudgeError(
            "openai backend requires the `openai` SDK; "
            "`pip install openai` first."
        ) from exc

    try:
        client = OpenAI()  # OPENAI_API_KEY from env
    except Exception as exc:  # noqa: BLE001
        raise JudgeError(
            f"openai client construction failed: {exc}"
        ) from exc

    def _run(story_text: str) -> JudgeResult:
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_preamble},
                    {
                        "role": "user",
                        "content": (
                            f"{user_prompt}\n\n# Story text\n\n"
                            f"{story_text}"
                        ),
                    },
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise JudgeError(
                f"openai provider call failed: {exc}"
            ) from exc
        text = resp.choices[0].message.content or ""
        try:
            payload = _extract_json(text)
        except ValueError as exc:
            raise JudgeError(
                f"openai judge returned non-JSON: {exc}"
            ) from exc
        return JudgeResult(
            values=payload.get("values", {}),
            per_feature_confidence=payload.get(
                "per_feature_confidence", {}
            ) or {},
            judge_identity={
                "kind": "openai",
                "model": model,
                "finish_reason": resp.choices[0].finish_reason,
            },
            raw_response=text,
        )

    return _run


def _api_judge_gemini(
    *,
    model: str,
    system_preamble: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
) -> JudgeBackend:
    try:
        from google import genai  # type: ignore
    except ImportError as exc:
        raise JudgeError(
            "gemini backend requires the `google-genai` SDK; "
            "`pip install google-genai` first."
        ) from exc

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get(
        "GEMINI_API_KEY"
    )
    if not api_key:
        raise JudgeError(
            "gemini backend requires GOOGLE_API_KEY or "
            "GEMINI_API_KEY in the environment."
        )
    try:
        client = genai.Client(api_key=api_key)
    except Exception as exc:  # noqa: BLE001
        raise JudgeError(
            f"gemini client construction failed: {exc}"
        ) from exc

    def _run(story_text: str) -> JudgeResult:
        try:
            resp = client.models.generate_content(
                model=model,
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    f"{system_preamble}\n\n"
                                    f"{user_prompt}\n\n"
                                    f"# Story text\n\n{story_text}"
                                ),
                            },
                        ],
                    },
                ],
                config={
                    "temperature": temperature,
                    "max_output_tokens": max_tokens,
                    "response_mime_type": "application/json",
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise JudgeError(
                f"gemini provider call failed: {exc}"
            ) from exc
        text = resp.text or ""
        try:
            payload = _extract_json(text)
        except ValueError as exc:
            raise JudgeError(
                f"gemini judge returned non-JSON: {exc}"
            ) from exc
        return JudgeResult(
            values=payload.get("values", {}),
            per_feature_confidence=payload.get(
                "per_feature_confidence", {}
            ) or {},
            judge_identity={
                "kind": "gemini",
                "model": model,
            },
            raw_response=text,
        )

    return _run


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction.

    Accepts a bare JSON object or a fenced ```json ... ``` block. Raises
    ValueError on parse failure OR when the top level is not a JSON object (a
    model that returns a bare ``[...]`` array is a likely failure mode; that
    must surface as a clean JudgeError via the API backends' ``except
    ValueError`` handlers, not slip through as a non-dict — parity with
    argument_judge._extract_json).
    """
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
            raise ValueError(
                f"no JSON object found in {text[:200]!r}"
            )
        obj = json.loads(stripped[start: end + 1])
    if not isinstance(obj, dict):
        raise ValueError(f"top-level JSON is {type(obj).__name__}, not an object")
    return obj


# ----------------- factory ---------------------------------------

def build_judge(
    kind: str,
    *,
    manifest_path: Path | str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    mock_option_index: int = 0,
) -> JudgeBackend:
    """Construct a judge backend by kind.

    Kinds:
      ``"manifest"`` — read pre-computed values from
      ``manifest_path``. Required argument.
      ``"mock"`` — deterministic test judge.
      ``"anthropic"`` / ``"openai"`` / ``"gemini"`` — API-backed
      judges. Lazy-import the SDK; require credentials in env.
    """
    if kind == "manifest":
        if manifest_path is None:
            raise JudgeError(
                "manifest judge requires manifest_path"
            )
        return _manifest_judge(Path(manifest_path))
    if kind == "mock":
        return _mock_judge(mock_option_index)
    if kind in ("anthropic", "openai", "gemini"):
        if not model:
            raise JudgeError(
                f"{kind} judge requires --judge-model"
            )
        user_prompt = render_prompt()
        common = dict(
            model=model,
            system_preamble=_SYSTEM_PREAMBLE,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if kind == "anthropic":
            return _api_judge_anthropic(**common)
        if kind == "openai":
            return _api_judge_openai(**common)
        return _api_judge_gemini(**common)
    raise JudgeError(f"unknown judge kind: {kind!r}")


def fingerprint_prompt(prompt_text: str = "") -> str:
    """SHA-256 of the canonical prompt + system preamble.

    Used for provenance: if two audit runs share the same prompt
    fingerprint, the prompts they showed their judges were
    byte-identical. Operators changing the prompt should expect
    this to change.
    """
    body = _SYSTEM_PREAMBLE + "\n" + (prompt_text or render_prompt())
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
