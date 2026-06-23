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

import judge_backends  # type: ignore
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
    "validate_doc_level",
    "fingerprint_prompt",
    "utc_now",
    "GUARD_STRENGTH_OPTIONS",
    "OBJECTION_STRENGTH_OPTIONS",
]

# ---- B5 arc-collapse judge fields (additive; per-paragraph + doc-level) ------
# These feed the surface's two heuristic arc-collapse signals (disappearing-guard,
# discounting-straw-men). They are APPENDED to the per-paragraph schema; a
# pre-extension manifest that omits them still validates (missing -> None).
# `null` is a first-class value: the legend instructs the judge to return null
# when uncertain rather than fabricate, and the surface's derivation returns None
# (never a fabricated False) when the evidence is absent.
GUARD_STRENGTH_OPTIONS: tuple[str, ...] = ("strong", "moderate", "weak", "none")
OBJECTION_STRENGTH_OPTIONS: tuple[str, ...] = ("strong", "weak")


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
            "values": {
                "paragraphs": list(self.values.get("paragraphs", [])),
                # B5 doc-level field carried through to the envelope's judge block
                # (additive; present even when null so a consumer sees the schema).
                "strongest_internal_objection_engaged": self.values.get(
                    "strongest_internal_objection_engaged"
                ),
            },
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
    "label each paragraph with one argumentative ROLE and one discourse MODE, "
    "plus a few descriptive arc fields. "
    "Read the whole essay before answering. Do not judge the argument's quality, "
    "correctness, or its likely author (human or AI); report only how each "
    "paragraph functions in the argument. Some paragraphs do more than one "
    "thing — choose the single role/mode that DOMINATES the paragraph. For the "
    "arc fields (guard_strength, claim_ref, objection_strength, and the "
    "document-level strongest_internal_objection_engaged), when you are NOT "
    "confident, return null — never invent a guard level, a claim link, an "
    "objection strength, or a strongest objection that the text does not "
    "actually contain. Return valid JSON exactly in the schema you are shown."
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
    lines.append(
        "\n# GUARD STRENGTH — the hedge level on this paragraph's MAIN claim\n"
    )
    lines.append(
        "How strongly is the paragraph's main claim qualified / hedged? Report "
        "the COMMITMENT hedge level on the claim itself (not the prose's "
        "politeness). Choose one of:"
    )
    lines.append("- `strong`: heavily qualified — scope-limited, conditional, "
                 "explicitly hedged ('may', 'in some cases', 'arguably').")
    lines.append("- `moderate`: somewhat qualified — a partial hedge or a stated "
                 "limit, but the claim is still asserted.")
    lines.append("- `weak`: barely qualified — asserted with only token hedging.")
    lines.append("- `none`: unqualified — asserted flatly as established fact.")
    lines.append("If the paragraph makes no claim of its own, or you cannot tell, "
                 "set guard_strength to null. Do NOT guess.")
    lines.append(
        "\n# CLAIM REF — link paragraphs that argue the SAME claim\n"
    )
    lines.append(
        "Assign a short, stable string id (e.g. \"c1\", \"c2\") to the specific "
        "claim a paragraph advances or guards, and REUSE the same id across "
        "every paragraph that treats that same claim — this lets a downstream "
        "check follow whether one claim's guard changes across the essay. Use the "
        "SAME id for the same claim and DIFFERENT ids for different claims. If a "
        "paragraph advances no trackable claim, or you are unsure which earlier "
        "claim it matches, set claim_ref to null rather than minting a spurious "
        "new id."
    )
    lines.append(
        "\n# OBJECTION STRENGTH — only for counterclaim / rebuttal paragraphs\n"
    )
    lines.append(
        "For a paragraph whose role is `counterclaim` or `rebuttal`, judge how "
        "strong the objection it engages is, relative to the strongest objection "
        "available against the thesis. Choose one of:"
    )
    lines.append("- `strong`: engages a serious, central objection.")
    lines.append("- `weak`: engages a minor / peripheral / easily-dismissed "
                 "objection (a possible decoy).")
    lines.append("Leave objection_strength null for non-objection paragraphs, or "
                 "when you cannot tell. Do NOT invent an objection.")
    lines.append("\n# Output format\n")
    lines.append(
        "Return a single JSON object with two keys. (1) `paragraphs`: an array "
        "with exactly one entry per input paragraph, in order. Each entry is "
        '`{"index": <int>, "role": <one role>, "mode": <one mode>, '
        '"guard_strength": <strong|moderate|weak|none|null>, '
        '"claim_ref": <string|null>, '
        '"objection_strength": <strong|weak|null>}`. Optionally add '
        '`"confidence": <number in [0,1]>` per entry. (2) '
        '`strongest_internal_objection_engaged`: a single document-level '
        'boolean — true if the essay actually engages the STRONGEST objection a '
        'reader could raise against its thesis, false if it engages only weaker '
        'ones, or null if you cannot tell or the essay raises no objections. '
        "Set it to null rather than asserting an objection the essay never names. "
        "Do not include the paragraph text, prose explanations, or any other keys.\n"
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
    ``{"role", "mode", "guard_strength", "claim_ref", "objection_strength"}``
    (each str|None) aligned to document order.

    A missing/extra entry, a role outside ROLE_OPTIONS, or a mode outside
    MODE_OPTIONS becomes None with a warning — never silently coerced (a judge
    emitting the wrong vocabulary is a judge-config problem, not a data-cleaning
    one). The B5 arc fields are validated the same way: an out-of-vocab
    guard_strength/objection_strength becomes None + a warning; a missing field
    defaults to None and is TOLERATED (a pre-extension manifest still loads);
    claim_ref must be a non-empty string or None (anything else -> None +
    warning). Entries are aligned by their declared ``index`` when present, else
    by position."""
    warnings: list[str] = []
    raw = values.get("paragraphs")
    cleaned: list[dict[str, Any]] = [
        {"role": None, "mode": None, "guard_strength": None,
         "claim_ref": None, "objection_strength": None}
        for _ in range(n_paragraphs)
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
        if not _is_index(idx, n_paragraphs):
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
        # ---- B5 arc fields (additive; missing -> None tolerated) ----------
        # A field absent from the entry stays None (a pre-extension manifest is
        # fine). A PRESENT-but-out-of-vocab value is nulled with a warning —
        # never silently coerced. `null` is an explicit, legal value.
        if "guard_strength" in entry:
            gs = entry.get("guard_strength")
            if gs is None or gs in GUARD_STRENGTH_OPTIONS:
                cleaned[idx]["guard_strength"] = gs
            else:
                warnings.append(
                    f"paragraph {idx}: guard_strength {gs!r} not in "
                    f"{list(GUARD_STRENGTH_OPTIONS)}"
                )
        if "objection_strength" in entry:
            os_ = entry.get("objection_strength")
            if os_ is None or os_ in OBJECTION_STRENGTH_OPTIONS:
                cleaned[idx]["objection_strength"] = os_
            else:
                warnings.append(
                    f"paragraph {idx}: objection_strength {os_!r} not in "
                    f"{list(OBJECTION_STRENGTH_OPTIONS)}"
                )
        if "claim_ref" in entry:
            cr = entry.get("claim_ref")
            if cr is None:
                cleaned[idx]["claim_ref"] = None
            elif isinstance(cr, str) and cr.strip():
                cleaned[idx]["claim_ref"] = cr.strip()
            else:
                warnings.append(
                    f"paragraph {idx}: claim_ref {cr!r} is not a non-empty string"
                )
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


def validate_doc_level(values: dict[str, Any]) -> tuple[bool | None, list[str]]:
    """Validate the document-level B5 field
    ``strongest_internal_objection_engaged`` from the judge ``values`` dict.

    Returns ``(value, warnings)`` where ``value`` is True/False/None. Missing is
    tolerated (-> None, no warning: a pre-extension manifest is fine). A present
    value that is neither a bool nor null is nulled with a warning (never coerced
    — `bool` is an `int` subclass, so a stray 0/1 is rejected too)."""
    warnings: list[str] = []
    if "strongest_internal_objection_engaged" not in values:
        return None, warnings
    v = values.get("strongest_internal_objection_engaged")
    if v is None or isinstance(v, bool):
        return v, warnings
    warnings.append(
        f"strongest_internal_objection_engaged {v!r} is not a boolean or null; "
        f"treating as null"
    )
    return None, warnings


# ----------------- judge backends ---------------------------------

JudgeBackend = Callable[[list[str]], JudgeResult]


def _is_index(idx: Any, n: int) -> bool:
    """True iff ``idx`` is a real paragraph index (an int, not a bool, in range).
    ``bool`` is an ``int`` subclass, so guard it explicitly."""
    return isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < n


def _confidences(raw: Any, n: int) -> list[float | None]:
    """Pull optional per-paragraph confidences from a judge's paragraph list,
    aligned to index; default all None. Keeps the FIRST entry per index (so a
    confidence never attaches to a label the keep-first validate path discarded);
    ``bool`` confidences are rejected (a bool is an int subclass)."""
    out: list[float | None] = [None] * n
    if isinstance(raw, list):
        for pos, entry in enumerate(raw):
            if not isinstance(entry, dict):
                continue
            idx = entry.get("index", pos)
            c = entry.get("confidence")
            if (_is_index(idx, n) and out[idx] is None
                    and isinstance(c, (int, float)) and not isinstance(c, bool)):
                out[idx] = float(c)
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
            f"manifest {manifest_path}: top level must be a JSON object, got "
            f"{type(data).__name__}"
        )
    values = data.get("values")
    if not isinstance(values, dict) or "paragraphs" not in values:
        raise JudgeError(
            f"manifest {manifest_path}: missing 'values.paragraphs' list"
        )
    ji = data.get("judge_identity")
    ji = ji if isinstance(ji, dict) else {}

    def _run(paragraphs: list[str]) -> JudgeResult:
        return JudgeResult(
            values={
                "paragraphs": list(values.get("paragraphs", [])),
                "strongest_internal_objection_engaged": values.get(
                    "strongest_internal_objection_engaged"
                ),
            },
            per_paragraph_confidence=_confidences(
                values.get("paragraphs"), len(paragraphs)
            ),
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


def _mock_judge(role_index: int = 1, mode_index: int = 0) -> JudgeBackend:
    """Deterministic judge for tests/fixtures: labels every paragraph with the
    role at ``role_index`` (default 1 = 'support') and the mode at ``mode_index``
    (default 0 = 'argumentation'), clipped to the option lists.

    It also emits deterministic B5 arc fields so CI/the contract golden exercise
    the new judge schema WITHOUT perturbing the role/mode the B1/B2 signals read:
    a single shared ``claim_ref`` ("c0") on every paragraph, a guard that drops
    from ``strong`` (paragraph 0) to ``weak`` (later paragraphs) — a downward
    transition the surface reads as a disappearing-guard — and, since the mock
    emits no counterclaim/rebuttal role, ``objection_strength`` is null on every
    paragraph and the doc-level ``strongest_internal_objection_engaged`` is null
    (so the discounting-straw-men signal derives to None: insufficient evidence,
    never a fabricated False)."""
    role = ROLE_OPTIONS[min(role_index, len(ROLE_OPTIONS) - 1)]
    mode = MODE_OPTIONS[min(mode_index, len(MODE_OPTIONS) - 1)]

    def _run(paragraphs: list[str]) -> JudgeResult:
        return JudgeResult(
            values={
                "paragraphs": [
                    {
                        "index": i, "role": role, "mode": mode,
                        "guard_strength": ("strong" if i == 0 else "weak"),
                        "claim_ref": "c0",
                        "objection_strength": None,
                    }
                    for i in range(len(paragraphs))
                ],
                "strongest_internal_objection_engaged": None,
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


def _build_api_result(
    payload: dict[str, Any], raw_text: str, identity: dict[str, Any], paragraphs: list[str]
) -> JudgeResult:
    return JudgeResult(
        values={
            "paragraphs": payload.get("paragraphs", []),
            "strongest_internal_objection_engaged": payload.get(
                "strongest_internal_objection_engaged"
            ),
        },
        per_paragraph_confidence=_confidences(payload.get("paragraphs"), len(paragraphs)),
        judge_identity=identity,
        raw_response=raw_text,
    )


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction: a bare object or a fenced ```json block.
    Raises ValueError on parse failure OR when the top level is not a JSON
    object (a model that returns a bare ``[...]`` array of paragraph labels is a
    likely failure mode; that must surface as a clean JudgeError, not an
    AttributeError, via the API backends' ``except ValueError`` handlers)."""
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
    if kind in judge_backends.PROVIDERS:
        if kind == "agent_host":
            # The host runtime resolves the model; no --judge-model required.
            model = model or "host-resolved"
        elif not model:
            raise JudgeError(f"{kind} judge requires --judge-model")
        common = dict(model=model, system_preamble=_SYSTEM_PREAMBLE,
                      user_prompt=render_prompt(), temperature=temperature,
                      max_tokens=max_tokens)
        return judge_backends.make_api_judge(
            kind,
            **common,
            build_user_content=_build_user_content,
            build_result=_build_api_result,
            judge_error=JudgeError,
            extract_json=_extract_json,
        )
    raise JudgeError(f"unknown judge kind: {kind!r}")


def fingerprint_prompt(prompt_text: str = "") -> str:
    """SHA-256 of the system preamble + canonical prompt — provenance: identical
    fingerprints mean byte-identical prompts."""
    body = _SYSTEM_PREAMBLE + "\n" + (prompt_text or render_prompt())
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
