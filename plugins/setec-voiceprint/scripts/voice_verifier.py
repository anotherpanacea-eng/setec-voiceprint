#!/usr/bin/env python3
"""voice_verifier.py — LLM-as-verifier authorship surface: ONE advisory
signal, never a verdict (spec 31, M1: model-free stdlib core).

`voice_verifier` is a pluggable LLM-judge family in the `narrative_judge` /
`argument_judge` mould, specialised to **authorship verification**: it takes
TWO text inputs — a ``query`` text and a ``reference`` text (the AV pair) — and
asks the judge whether they read as *stylometrically consistent*, on a 5-band
ordinal scale, with a CAVE-style decomposed per-feature rationale carrying
verbatim span pointers into the two texts.

It is the **model-based second opinion** that sits beside the stylometric
``general_imposters.py`` harness. The human reads the band, the rationale, and
the stylometric harness together and decides. It is NOT a replacement for the
stylometric harness, and exactly like every detector-flavored surface it is:

  * never a same-author / different-author / AI-vs-human VERDICT — the band
    vocabulary contains no verdict token (the blocklist guard in the tests),
    and the result exposes no probability / score / confidence numeric;
  * never a held-out validator, a voicewright selection / ``SetecFitness``
    signal, a reward, or a training target (no new ``SignalSpec`` ships);
  * uncalibrated by default — the band is a model's ordinal judgement bound to
    a named judge model + prompt fingerprint, not a same-author probability.

Roots (cite in the PR body + the changelog fragment):

  * Huang et al. 2024, "Can Large Language Models Identify Authorship?"
    (arXiv:2403.08213) — the LLM-as-verifier framing (rationale, not yes/no).
  * Hung et al. 2024, "InstructAV" (arXiv:2407.12882) — decision-with-
    explanation; the offline/local-weights variant is the M2 seam we mirror.
  * Hao et al. 2024, "CAVE" (arXiv:2406.16672) — Controllable Authorship
    Verification Explanations: structured decomposed rationales + a
    rationale<->conclusion consistency check.

M1 (this milestone) is model-free at import (lazy SDK like every judge family).
The band/rationale machinery, validation, refusal path, envelope, CLI, and the
posture guards are exercised end-to-end through the deterministic ``mock`` judge
and the ``manifest`` judge — no model loads in CI. M2 wires the real
InstructAV/CAVE extraction behind the ``anthropic`` / ``openai`` / ``gemini``
adapters and a lazy local backend (``skipif``-guarded).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import judge_backends  # type: ignore
from claim_license import ClaimLicense  # type: ignore
from output_schema import build_error_output, build_output  # type: ignore

__all__ = [
    "VERIFIER_BANDS",
    "CENTER_BAND",
    "RATIONALE_FEATURES",
    "Span",
    "VerifierResult",
    "VerifierError",
    "validate_result",
    "render_prompt",
    "fingerprint_prompt",
    "build_verifier",
    "build_user_content",
    "build_claim_license",
]

TASK_SURFACE = "voice_verifier"
TOOL = "voice_verifier"
SCRIPT_VERSION = "0.1.0"  # M1: model-free band/rationale core + mock/manifest

# ----------------------------------------------------------------------------
# Vocabulary — ordinal, NO verdict token. `cannot_determine` is the designed
# center (the general_imposters gray zone), not an error. The token-blocklist
# guard in the tests asserts none of {same_author, different_author, ai, human,
# forgery, plagiar*} appears as a band, a feature key, a field name, or a value.
# ----------------------------------------------------------------------------
VERIFIER_BANDS: tuple[str, ...] = (
    "consistent",
    "leans_consistent",
    "cannot_determine",
    "leans_inconsistent",
    "inconsistent",
)
CENTER_BAND = "cannot_determine"

# The CAVE decomposition: the fixed register of linguistic dimensions the
# rationale reasons over. Each appears as its own sub-judgement (a band + note
# + spans), so the rationale is schema-pinned, not free-text.
RATIONALE_FEATURES: tuple[str, ...] = (
    "lexical_habits",
    "syntactic_constructions",
    "punctuation_cadence",
    "discourse_moves",
    "register_and_tone",
)

_SIDES = ("query", "reference")


class VerifierError(RuntimeError):
    """Raised when a verifier backend cannot produce a valid result.

    Mirrors ``narrative_judge.JudgeError`` / ``argument_judge.JudgeError``:
    a missing/malformed manifest, a missing SDK, bad credentials, or a
    non-JSON model body all surface through this so the entrypoint's
    ``except VerifierError`` contract routes them to the ``available: false``
    R3 refusal envelope instead of a raw traceback.
    """


# ----------------------------------------------------------------------------
# Result shape
# ----------------------------------------------------------------------------
# A Span is a plain dict — {"side", "start", "end", "quote"} — pointing into
# ONE of the two texts. It carries NO p_same_author / score / verdict field.
Span = dict[str, Any]


@dataclass
class VerifierResult:
    """The family's result shape (the JudgeResult analogue).

    Carries the descriptive ordinal ``band``, the CAVE per-feature
    decomposition (``feature_judgements``), the human-readable ``rationale``,
    the ``judge_identity`` provenance dict, and the (truncated) ``raw_response``.

    It deliberately carries NO ``p_same_author`` / ``confidence`` / ``score`` /
    ``verdict`` field: a model confidence used to demote to ``cannot_determine``
    (M2) is consumed internally by the backend, never surfaced here. The
    no-score shape is asserted in the tests.
    """

    band: str
    feature_judgements: dict[str, dict[str, Any]]
    rationale: str
    judge_identity: dict[str, Any]
    raw_response: str | None = None
    # The prompt fingerprint UNDER WHICH this band was produced. A live backend stamps the current
    # code's fingerprint; an IMPORTED manifest carries the fingerprint recorded in the manifest (or
    # None) — never the current code's, which would falsely claim the imported judgment was made under
    # this prompt (Codex P1, mirroring the argquality fingerprint discipline).
    prompt_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "band": self.band,
            "feature_judgements": {
                k: {
                    "band": v.get("band"),
                    "note": v.get("note", ""),
                    "spans": [dict(s) for s in v.get("spans", [])],
                }
                for k, v in self.feature_judgements.items()
            },
            "rationale": self.rationale,
            "judge_identity": dict(self.judge_identity),
            "raw_response_truncated": (
                (self.raw_response[:2000] + "…")
                if self.raw_response and len(self.raw_response) > 2000
                else self.raw_response
            ),
        }


# ----------------------------------------------------------------------------
# Span / band validation (the validate_values analogue). Pure; CI-runnable.
# ----------------------------------------------------------------------------
def _validate_span(span: Any, *, query: str, reference: str) -> tuple[Span | None, str | None]:
    """Return ``(clean_span, warning)``. A span whose ``(start, end)`` does not
    index its named side — or whose ``quote`` does not match the actual text at
    that offset — is dropped (returns ``(None, warning)``) so a confabulated
    quote cannot survive into the envelope as if grounded."""
    if not isinstance(span, dict):
        return None, f"dropping non-dict span {span!r}"
    side = span.get("side")
    if side not in _SIDES:
        return None, f"dropping span with bad side {side!r} (not one of {_SIDES})"
    text = query if side == "query" else reference
    start, end = span.get("start"), span.get("end")
    if not isinstance(start, int) or not isinstance(end, int):
        return None, f"dropping {side} span with non-int offsets ({start!r}, {end!r})"
    if not (0 <= start < end <= len(text)):
        return None, (
            f"dropping {side} span [{start}, {end}) — does not index the "
            f"{side} text (len {len(text)})"
        )
    actual = text[start:end]
    quote = span.get("quote")
    if quote is not None and quote != actual:
        return None, (
            f"dropping {side} span [{start}, {end}) — quote does not match the "
            f"text at that offset (a hallucinated quote)"
        )
    return (
        {"side": side, "start": start, "end": end, "quote": actual},
        None,
    )


def _extremes(band: str) -> bool:
    return band in ("consistent", "inconsistent")


def validate_result(
    result: VerifierResult,
    *,
    query: str,
    reference: str,
) -> tuple[VerifierResult, list[str]]:
    """Return ``(cleaned_result, warnings)``.

    Mirrors ``narrative_judge.validate_values``: a judge that emits the wrong
    vocabulary is a config problem SURFACED, not silently coerced.

      * an out-of-vocabulary top-level ``band`` is nulled to
        ``cannot_determine`` with a warning;
      * each per-feature out-of-vocabulary band is likewise nulled+warned;
      * a missing ``RATIONALE_FEATURES`` key is filled with ``cannot_determine``
        + a warning;
      * a span that does not index its named side (or whose quote does not
        match) is DROPPED with a warning;
      * CAVE consistency: if every per-feature band is at ONE extreme but the
        top-level band is the OPPOSITE extreme, a ``rationale_band_mismatch``
        warning is appended — surfaced, NEVER auto-corrected.
    """
    warnings: list[str] = []

    band = result.band
    if band not in VERIFIER_BANDS:
        warnings.append(
            f"top-level band {band!r} not in VERIFIER_BANDS; nulled to "
            f"{CENTER_BAND!r}"
        )
        band = CENTER_BAND

    clean_features: dict[str, dict[str, Any]] = {}
    for feat in RATIONALE_FEATURES:
        entry = result.feature_judgements.get(feat)
        if not isinstance(entry, dict):
            warnings.append(
                f"feature {feat!r} missing/malformed; defaulting band to "
                f"{CENTER_BAND!r}"
            )
            clean_features[feat] = {"band": CENTER_BAND, "note": "", "spans": []}
            continue
        fband = entry.get("band")
        if fband not in VERIFIER_BANDS:
            warnings.append(
                f"feature {feat!r}: band {fband!r} not in VERIFIER_BANDS; "
                f"nulled to {CENTER_BAND!r}"
            )
            fband = CENTER_BAND
        clean_spans: list[Span] = []
        for span in entry.get("spans", []) or []:
            clean, warn = _validate_span(span, query=query, reference=reference)
            if warn is not None:
                warnings.append(f"feature {feat!r}: {warn}")
            if clean is not None:
                clean_spans.append(clean)
        clean_features[feat] = {
            "band": fband,
            "note": str(entry.get("note", "")),
            "spans": clean_spans,
        }

    # Report any extra keys the judge emitted that are not in the schema.
    extra = sorted(set(result.feature_judgements) - set(RATIONALE_FEATURES))
    if extra:
        warnings.append(
            f"judge emitted {len(extra)} feature key(s) not in "
            f"RATIONALE_FEATURES (ignored): {extra[:5]}"
        )

    # CAVE rationale<->conclusion consistency. If EVERY per-feature band sits at
    # one extreme but the top-level conclusion is the opposite extreme, the
    # decomposition contradicts the conclusion: surface it, do not fix it.
    feature_bands = [v["band"] for v in clean_features.values()]
    if feature_bands and all(_extremes(b) for b in feature_bands):
        uniq = set(feature_bands)
        if len(uniq) == 1:
            only = next(iter(uniq))
            opposite = "inconsistent" if only == "consistent" else "consistent"
            if band == opposite:
                warnings.append(
                    "rationale_band_mismatch: every per-feature band is "
                    f"{only!r} but the top-level band is {band!r} (the opposite "
                    "extreme); surfaced, not auto-corrected"
                )

    cleaned = VerifierResult(
        band=band,
        feature_judgements=clean_features,
        rationale=result.rationale,
        judge_identity=dict(result.judge_identity),
        raw_response=result.raw_response,
        prompt_fingerprint=result.prompt_fingerprint,   # carry the produced-under fingerprint through
    )
    return cleaned, warnings


# ----------------------------------------------------------------------------
# Prompt construction (the narrative_judge.render_prompt pattern)
# ----------------------------------------------------------------------------
_SYSTEM_PREAMBLE = (
    "You are a careful authorship-verification annotator. You will be shown "
    "TWO texts — a 'query' text and a 'reference' text — and asked whether they "
    "read as stylometrically CONSISTENT (the kind of features that tend to "
    "persist across one writer's work), feature by feature. You are reporting "
    "stylometric consistency, NOT authorship. Never output 'same author', "
    "'different author', a person's name, an AI-vs-human determination, or a "
    "probability. The strongest things you may conclude are 'consistent' or "
    "'inconsistent'; when the evidence does not support either, say "
    "'cannot_determine' — that is a valid, designed answer, not a failure. For "
    "each named feature, give a band, a short note, and verbatim spans quoted "
    "from the query or reference text that support your reading. Return only "
    "the pinned JSON keys."
)


def render_prompt(features: tuple[str, ...] = RATIONALE_FEATURES) -> str:
    """Build the consolidated user-side prompt for the judge.

    Pins the band vocabulary, the per-feature decomposition schema, and the
    output JSON shape. The refusal ("stylometric consistency, not authorship;
    never output 'same author' or a probability") lives in the preamble AND is
    structurally enforced by ``validate_result``.
    """
    lines: list[str] = []
    lines.append("# Authorship-verification task\n")
    lines.append(
        "Compare the QUERY text and the REFERENCE text on each linguistic "
        "feature below. For each feature, choose a band from this ordered "
        "vocabulary (no other value is permitted):\n"
    )
    lines.append("```json")
    lines.append(json.dumps(list(VERIFIER_BANDS), indent=2))
    lines.append("```\n")
    lines.append(
        "`cannot_determine` is the center band — use it when the two texts are "
        "too short, too different in topic, or otherwise do not support a "
        "consistency reading. It is a valid answer, never an error.\n"
    )
    lines.append("# Features to decompose (the CAVE rationale)\n")
    lines.append("```json")
    lines.append(json.dumps(list(features), indent=2))
    lines.append("```\n")
    lines.append("# Output format\n")
    lines.append(
        "Return a single JSON object with the exact keys `band`, "
        "`feature_judgements`, and `rationale`.\n"
        "- `band`: one string from the vocabulary above — your overall "
        "stylometric-consistency reading of the pair.\n"
        "- `feature_judgements`: an object whose keys are EXACTLY the feature "
        "names above; each value is an object with `band` (one string from the "
        "vocabulary), `note` (a short explanation), and `spans` (a list of "
        "objects, each `{\"side\": \"query\"|\"reference\", \"start\": int, "
        "\"end\": int, \"quote\": str}`, where start/end are character offsets "
        "into the named text and quote is the verbatim substring).\n"
        "- `rationale`: a short human-readable synthesis.\n"
    )
    lines.append(
        "Do NOT include the texts, prose outside `rationale`, an author name, a "
        "same-author/different-author claim, or a probability/score in your "
        "reply.\n"
    )
    return "\n".join(lines)


def fingerprint_prompt(prompt_text: str = "") -> str:
    """SHA-256 of the canonical preamble + prompt. Recorded as
    ``prompt_fingerprint_sha256`` on every envelope so a band produced under one
    judge/prompt is flagged non-transferable against another (the
    ``argument_judge`` / ``narrative_judge`` provenance precedent)."""
    body = _SYSTEM_PREAMBLE + "\n" + (prompt_text or render_prompt())
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------
# Backends
# ----------------------------------------------------------------------------
# A backend takes the AV pair (query, reference) and returns a VerifierResult.
VerifierBackend = Callable[[str, str], VerifierResult]


def _manifest_backend(manifest_path: Path) -> VerifierBackend:
    """Read a pre-computed VerifierResult from an operator JSON.

    The "ship the methodology, not the model selection" discipline: an operator
    runs whatever judge/weights they like out of band and drops the result in.
    A missing/malformed file, or a payload missing the required ``band`` key, is
    bad SETUP input wrapped as ``VerifierError`` (so it surfaces through the
    entrypoint's refusal contract, not a raw traceback). The missing-``band``
    case is the (non-circular) trigger for the ``available: false`` acceptance.
    """
    try:
        data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise VerifierError(f"manifest {manifest_path}: cannot read ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise VerifierError(f"manifest {manifest_path}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise VerifierError(
            f"manifest {manifest_path}: top level must be a JSON object, got "
            f"{type(data).__name__}"
        )
    if "band" not in data:
        raise VerifierError(
            f"manifest {manifest_path}: missing required 'band' key"
        )
    # Validate feature_judgements UP FRONT (Codex P2): a malformed value would otherwise escape as an
    # AttributeError/ValueError traceback later (compose/to_dict call `.get` on each value, and the
    # old `dict(...)` raised on a non-mapping). Map bad input to the refusal contract instead.
    fj_raw = data.get("feature_judgements", {})
    if fj_raw is None:
        fj_raw = {}
    if not isinstance(fj_raw, dict):
        raise VerifierError(
            f"manifest {manifest_path}: 'feature_judgements' must be a JSON object, got "
            f"{type(fj_raw).__name__}"
        )
    for _k, _v in fj_raw.items():
        if not isinstance(_v, dict):
            raise VerifierError(
                f"manifest {manifest_path}: feature_judgement {_k!r} must be a JSON object, got "
                f"{type(_v).__name__}"
            )
    # Preserve the fingerprint the manifest was PRODUCED under (top-level, else judge_identity), or
    # None — never the current code's fingerprint (Codex P1: an imported band is not transferable to
    # this prompt; rebinding it would falsely certify it under this code's prompt).
    identity_block = data.get("judge_identity") or {}
    manifest_fp = data.get("prompt_fingerprint_sha256")
    if manifest_fp is None:
        manifest_fp = identity_block.get("prompt_fingerprint_sha256")

    def _run(_query: str, _reference: str) -> VerifierResult:
        return VerifierResult(
            band=data.get("band"),
            feature_judgements=dict(fj_raw),
            rationale=str(data.get("rationale", "")),
            judge_identity={
                "kind": "manifest",
                "manifest_path": str(manifest_path),
                "model": identity_block.get("model"),
                "model_revision": identity_block.get("model_revision"),
                "prompt_version": identity_block.get("prompt_version"),
            },
            raw_response=None,
            prompt_fingerprint=manifest_fp,
        )

    return _run


def _mock_backend(mock_band: str = CENTER_BAND) -> VerifierBackend:
    """Deterministic backend for tests/CI. Emits ``mock_band``, a full
    ``RATIONALE_FEATURES`` decomposition all set to ``mock_band``, and one
    synthetic span per side grounded in the actual texts — exercising the whole
    band/rationale/validation/envelope/CLI path with NO model."""
    if mock_band not in VERIFIER_BANDS:
        raise VerifierError(
            f"mock_band {mock_band!r} not in VERIFIER_BANDS {VERIFIER_BANDS}"
        )

    def _run(query: str, reference: str) -> VerifierResult:
        def _first_token_span(text: str, side: str) -> list[Span]:
            stripped = text.lstrip()
            if not stripped:
                return []
            offset = len(text) - len(stripped)
            end = offset
            while end < len(text) and not text[end].isspace():
                end += 1
            if end <= offset:
                return []
            return [
                {"side": side, "start": offset, "end": end, "quote": text[offset:end]}
            ]

        features: dict[str, dict[str, Any]] = {}
        for i, feat in enumerate(RATIONALE_FEATURES):
            side = _SIDES[i % len(_SIDES)]
            spans = _first_token_span(query if side == "query" else reference, side)
            features[feat] = {
                "band": mock_band,
                "note": f"mock judgement for {feat}",
                "spans": spans,
            }
        return VerifierResult(
            band=mock_band,
            feature_judgements=features,
            rationale=(
                f"Mock verifier: overall band {mock_band!r} with a uniform "
                "per-feature decomposition. Deterministic; no model."
            ),
            judge_identity={"kind": "mock", "mock_band": mock_band},
            raw_response=None,
            prompt_fingerprint=fingerprint_prompt(),   # a live this-code run
        )

    return _run


def build_user_content(user_prompt: str, pair: tuple[str, str]) -> str:
    """Pack the AV pair into the user message (the load-bearing difference from
    the single-doc judge families). ``pair`` is ``(query, reference)``."""
    query, reference = pair
    return (
        f"{user_prompt}\n\n# Query text\n\n{query}\n\n# Reference text\n\n{reference}"
    )


def _build_api_result(
    payload: dict[str, Any],
    raw_text: str,
    identity: dict[str, Any],
    judge_input: Any,
) -> VerifierResult:
    """Adapt a parsed model payload into a ``VerifierResult`` (the 4-arg
    ``build_result`` the real ``judge_backends.make_api_judge`` invokes —
    ``(payload, raw_text, identity, judge_input)``). ``judge_input`` is the AV
    pair; it is carried so M2 span re-validation (via ``validate_result``) can
    index the original texts. M2 maps any model decision onto ``VERIFIER_BANDS``
    here; the entrypoint then re-validates."""
    return VerifierResult(
        band=payload.get("band", CENTER_BAND),
        feature_judgements=payload.get("feature_judgements", {}) or {},
        rationale=str(payload.get("rationale", "")),
        judge_identity=identity,
        raw_response=raw_text,
        prompt_fingerprint=fingerprint_prompt(),   # produced under this code's prompt
    )


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction (the ``narrative_judge._extract_json``
    precedent): accept a bare object or a fenced ```json block; raise
    ``ValueError`` on a non-object body so the API backends' ``except
    ValueError`` wraps it as a clean ``VerifierError``."""
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


def build_verifier(
    kind: str,
    *,
    manifest_path: Path | str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    mock_band: str = CENTER_BAND,
) -> VerifierBackend:
    """Construct a verifier backend by kind (the ``build_judge`` factory).

    Kinds:
      ``"manifest"`` — read a pre-computed result from ``manifest_path``.
      ``"mock"``     — deterministic test backend (no model).
      ``"anthropic"`` / ``"openai"`` / ``"gemini"`` — API-backed (M2). Lazy SDK
      import via ``judge_backends.make_api_judge``; require ``--judge-model`` +
      credentials in env.
    """
    if kind == "manifest":
        if manifest_path is None:
            raise VerifierError("manifest verifier requires manifest_path")
        return _manifest_backend(Path(manifest_path))
    if kind == "mock":
        return _mock_backend(mock_band)
    if kind in judge_backends.PROVIDERS:
        if not model:
            raise VerifierError(f"{kind} verifier requires --judge-model")
        api = judge_backends.make_api_judge(
            kind,
            model=model,
            system_preamble=_SYSTEM_PREAMBLE,
            user_prompt=render_prompt(),
            temperature=temperature,
            max_tokens=max_tokens,
            build_user_content=build_user_content,
            build_result=_build_api_result,
            judge_error=VerifierError,
            extract_json=_extract_json,
        )

        def _run(query: str, reference: str) -> VerifierResult:
            # make_api_judge passes the single judge_input straight to
            # build_user_content and build_result; the AV pair IS that input.
            return api((query, reference))

        return _run
    raise VerifierError(f"unknown verifier kind: {kind!r}")


# ----------------------------------------------------------------------------
# Claim license
# ----------------------------------------------------------------------------
DEFAULT_LICENSES = (
    "A single advisory LLM second opinion on whether two texts read as "
    "stylometrically consistent, on a 5-band ordinal scale "
    "(consistent / leans_consistent / cannot_determine / leans_inconsistent / "
    "inconsistent), with a decomposed per-feature rationale and verbatim span "
    "pointers, bound to a named judge model + prompt fingerprint. Uncalibrated: "
    "the band is the judge's ordinal judgement, not a probability."
)
DEFAULT_DOES_NOT_LICENSE = (
    "A same-author or different-author verdict; an AI-vs-human determination; a "
    "probability or score; a stand-alone authorship claim. Stylometric "
    "consistency is not authorship. Corroborate or contrast against "
    "general_imposters / voice_distance; an `inconsistent` band is not evidence "
    "of forgery, AI generation, or a different person, and `cannot_determine` is "
    "the designed gray-zone outcome, not an error."
)


def build_claim_license(_result: VerifierResult | None = None) -> ClaimLicense:
    """Build the ``ClaimLicense`` block. The label text lives in the single
    source ``claim_license_surfaces/voice_verifier.txt`` (resolved by
    ``TASK_SURFACE_LABELS``); ``licenses`` / ``does_not_license`` describe what
    the result entitles."""
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=DEFAULT_LICENSES,
        does_not_license=DEFAULT_DOES_NOT_LICENSE,
        additional_caveats=[
            "Uncalibrated by default: there is no fixed mapping from band to a "
            "same-author likelihood without an operator's labeled calibration "
            "run.",
            "Bound to a specific judge model + prompt fingerprint; a band "
            "produced under a different judge/prompt is non-transferable.",
            "An independent second opinion that sits beside the stylometric "
            "general_imposters harness — not a replacement, and not wired into "
            "any selection / held-out / fitness signal.",
        ],
        references=[
            "Huang et al. 2024, Can LLMs Identify Authorship? (arXiv:2403.08213)",
            "Hung et al. 2024, InstructAV (arXiv:2407.12882)",
            "Hao et al. 2024, CAVE (arXiv:2406.16672)",
        ],
    )


# ----------------------------------------------------------------------------
# Envelope assembly
# ----------------------------------------------------------------------------
def compose_envelope(
    *,
    result: VerifierResult,
    query_path: Path | str | None,
    query_words: int,
    reference_path: Path | str | None,
    warnings: list[str],
) -> dict[str, Any]:
    """Build the schema-1.0 success envelope for a validated result."""
    results = {
        "band": result.band,
        "feature_judgements": result.to_dict()["feature_judgements"],
        "rationale": result.rationale,
        "calibration_status": "uncalibrated",
        "judge_identity": dict(result.judge_identity),
        # The fingerprint the band was PRODUCED under — current code for a live run, the manifest's
        # own recorded fingerprint (or null) for an imported result; never blindly the current code's
        # (Codex P1).
        "prompt_fingerprint_sha256": result.prompt_fingerprint,
        "reference": {
            "path": str(reference_path) if reference_path is not None else None,
        },
    }
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL,
        version=SCRIPT_VERSION,
        target_path=query_path,
        target_words=query_words,
        baseline=None,
        results=results,
        claim_license=build_claim_license(result),
        available=True,
        warnings=warnings,
    )


def _count_words(text: str) -> int:
    return len(text.split())


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------------------
# CLI / `setec run voice_verifier --json` entrypoint
# ----------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "voice_verifier — LLM-as-verifier authorship surface: one advisory "
            "stylometric-consistency band over a query/reference pair, with a "
            "decomposed per-feature rationale. Never a verdict."
        )
    )
    parser.add_argument(
        "--query", type=Path, required=True,
        help="Path to the query text file (UTF-8). One half of the AV pair.",
    )
    parser.add_argument(
        "--reference", type=Path, required=True,
        help="Path to the reference text file (UTF-8). The other half of the "
             "AV pair. Authorship verification is pairwise — both are required.",
    )
    parser.add_argument(
        "--judge", choices=("manifest", "mock", "anthropic", "openai", "gemini"),
        default="manifest",
        help="Verifier backend. mock/manifest are stdlib (M1); the API kinds "
             "read the prose (M2, require an SDK + credentials).",
    )
    parser.add_argument(
        "--manifest", type=Path, default=None,
        help="JSON of a pre-computed VerifierResult (required for "
             "--judge=manifest).",
    )
    parser.add_argument("--judge-model", default=None, help="Model ID for API judges.")
    parser.add_argument("--judge-temperature", type=float, default=0.0)
    parser.add_argument("--judge-max-tokens", type=int, default=4096)
    parser.add_argument(
        "--mock-band", choices=VERIFIER_BANDS, default=CENTER_BAND,
        help="Band the mock judge emits (for --judge=mock).",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Print the schema-1.0 envelope to stdout (the setec_run path).",
    )
    return parser


def _emit(envelope: dict[str, Any]) -> None:
    print(json.dumps(envelope, indent=2, default=str))


def _refuse(reason: str, reason_category: str) -> dict[str, Any]:
    return build_error_output(
        task_surface=TASK_SURFACE,
        tool=TOOL,
        version=SCRIPT_VERSION,
        reason=reason,
        reason_category=reason_category,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Pairwise input is REQUIRED (argparse already enforces both flags exist;
    # here we check the files are readable). A single-document mode is refused
    # by construction — there is no one-text path.
    texts: dict[str, str] = {}
    for label, path in (("query", args.query), ("reference", args.reference)):
        if not path.is_file():
            print(f"error: {label} file not found at {path}", file=sys.stderr)
            return 1
        try:
            texts[label] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"error: cannot read {label} {path}: {exc}", file=sys.stderr)
            return 1

    query, reference = texts["query"], texts["reference"]

    # Build the backend. Construction failures (missing manifest / model / SDK)
    # are bad SETUP input: route through argparse so the emitted "usage:" line
    # lets setec_run categorize the exit-2 as bad_input (parity with
    # argument_decision_audit).
    try:
        backend = build_verifier(
            args.judge,
            manifest_path=args.manifest,
            model=args.judge_model,
            temperature=args.judge_temperature,
            max_tokens=args.judge_max_tokens,
            mock_band=args.mock_band,
        )
    except VerifierError as exc:
        parser.error(f"verifier construction failed: {exc}")

    # Run the judge. An execution failure (incl. a manifest missing the band
    # key, a non-JSON model body, a missing SDK surfaced at call) is the
    # available:false R3 refusal — NOT a fabricated cannot_determine band.
    try:
        raw_result = backend(query, reference)
    except VerifierError as exc:
        envelope = _refuse(
            reason=f"voice_verifier: judge execution failed: {exc}",
            reason_category="internal_error",
        )
        if args.json:
            _emit(envelope)
        else:
            print(f"error: {envelope['reason']}", file=sys.stderr)
        return 3

    result, warnings = validate_result(raw_result, query=query, reference=reference)
    envelope = compose_envelope(
        result=result,
        query_path=args.query,
        query_words=_count_words(query),
        reference_path=args.reference,
        warnings=warnings,
    )

    if args.json:
        _emit(envelope)
    else:
        print(f"band: {result.band}")
        print(f"calibration_status: uncalibrated")
        for feat, fj in result.to_dict()["feature_judgements"].items():
            print(f"  {feat}: {fj['band']}")
        if warnings:
            print(f"warnings: {len(warnings)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
