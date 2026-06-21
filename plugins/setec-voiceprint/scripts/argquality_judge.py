#!/usr/bin/env python3
"""argquality_judge.py — pluggable LLM judge for a theory-based argument-quality
dimension PROFILE (Wachsmuth / GAQCorpus rhetoric / logic / dialectic).

Mirrors ``fallacy_judge`` / ``argument_judge`` / ``narrative_judge`` (same
provider-agnostic plumbing: manifest / mock / anthropic / openai / gemini; lazy
SDK imports via ``judge_backends``; ``JudgeError``; provenance + prompt
fingerprint) — but it is its OWN module with its OWN system preamble, user
prompt, ``fingerprint_prompt``, and ``_mock_judge``, because the task and the
result schema differ from the fallacy-flag and paragraph-role judges. Reusing
another module's ``fingerprint_prompt`` would fingerprint the WRONG prompt and
silently defeat any drift gate bound to this surface (the spec-26 P1).

Spec ``specs/30-gaqcorpus-argquality.md`` (M1). Implements the three top-tier
Wachsmuth / GAQCorpus argument-quality dimensions (Lauscher, Ng, Napoles &
Tetreault 2020, *Rhetoric, Logic, and Dialectic: Advancing Theory-based Argument
Quality Assessment in Natural Language Processing*, arXiv:2006.00843).

POSTURE — load-bearing, non-negotiable
--------------------------------------
The judge places, per dimension, a COARSE DESCRIPTIVE band — *where the GAQCorpus
rating distribution would fall* — NOT a grade. It is told, explicitly:

  * place each dimension's band as a distributional placement, which is NOT a
    judgment that the argument is good or bad;
  * a ``lower`` placement is FREQUENTLY appropriate in context (a one-sided
    register, a rebuttal, a polemic);
  * return ``null`` when you cannot place a dimension — never invent a band;
  * do NOT rate the argument good/bad, do NOT emit an ``overall`` judgment, do
    NOT roll the three dimensions into one number.

The result schema carries the framing in its field names (``band`` +
``distribution_reference``), so a consumer cannot read a band as a ruling. There
is NO aggregate, NO ``overall`` band, NO numeric quality field — by design.

Result schema (``JudgeResult.values``)
--------------------------------------
``{"dimensions": {"logic": {"band": <lower|mid|higher|null>, "evidence_spans":
[<verbatim paragraph-anchored span> ...], "basis": <short rationale str>},
"rhetoric": {...}, "dialectic": {...}}}`` — exactly the three top-tier
dimensions, each placed INDEPENDENTLY. ``band`` is ``None`` (JSON ``null``) when
the judge declined the dimension (a first-class value, NEVER coerced to
``lower``). ``evidence_spans`` are verbatim substrings (paragraph-anchored, NOT
character offsets — judge-fragile, the spec-26 resolution).

Fingerprint
-----------
``fingerprint_prompt()`` hashes THIS module's ``_SYSTEM_PREAMBLE`` +
``render_prompt()`` — never ``fallacy_judge``'s / ``argument_judge``'s. Asserted
to differ from both in the test suite.
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

PROMPT_VERSION = "argquality_dimension_profile_v1"


# ----------------- taxonomy ---------------------------------------
# The three top-tier Wachsmuth / GAQCorpus argument-quality dimensions
# (Lauscher et al. 2020, arXiv:2006.00843). These are the surface's STABLE
# vocabulary; the legend names each dimension's sub-criteria so the band is
# grounded in the taxonomy, not an ad-hoc gestalt.
DIMENSIONS: tuple[str, ...] = ("logic", "rhetoric", "dialectic")

DIMENSION_LEGEND: dict[str, str] = {
    "logic": (
        "cogency — local relevance, local sufficiency, and acceptability of "
        "premises: does each step follow and rest on acceptable grounds"
    ),
    "rhetoric": (
        "effectiveness — arrangement, appropriateness, clarity, credibility, "
        "and emotional appeal: is the case made effectively for its audience"
    ),
    "dialectic": (
        "reasonableness — global relevance, global sufficiency, global "
        "acceptability, and engaging the opposing case: does the whole argument "
        "hold up as a reasonable contribution to the debate"
    ),
}

# Bands are DISTRIBUTIONAL PLACEMENTS against the GAQCorpus rating distribution,
# never grades. ``None`` (JSON null) is a first-class band: the judge declined.
BANDS: tuple[str, ...] = ("lower", "mid", "higher")

# A register-bound directional descriptor of the GAQCorpus rating distribution.
# A STRING with NO numeric leaf (the spec-30 P3 leaf-level guard): the band-vs-
# grade line must hold at the leaf, not just the key.
DISTRIBUTION_REFERENCE = (
    "lower / mid / higher = lower / middle / upper tercile of the GAQCorpus "
    "rating distribution for this dimension over its annotated forums "
    "(online-debate, Q&A-forum, review text). Register-bound directional "
    "reference, NOT a shipped threshold or operating point; a `lower` placement "
    "is frequently appropriate in context."
)


# ----------------- errors / result --------------------------------
class JudgeError(RuntimeError):
    """Raised when a judge backend cannot produce a valid result."""


@dataclass
class JudgeResult:
    """One judged document. ``values`` carries ``dimensions``: a per-dimension
    ``{band, evidence_spans, basis}`` map over exactly the three top-tier
    Wachsmuth dimensions (NO aggregate, NO overall). ``judge_identity`` is the
    provenance dict (always set)."""

    values: dict[str, Any]
    judge_identity: dict[str, Any]
    raw_response: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": {"dimensions": dict(self.values.get("dimensions", {}))},
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
    "You are a careful rhetoric-and-composition reviewer assisting a human "
    "editor. You will be shown a short argument-shaped nonfiction passage, split "
    "into numbered paragraphs. For each of THREE theory-of-argument-quality "
    "dimensions — logic (cogency), rhetoric (effectiveness), and dialectic "
    "(reasonableness) — place a COARSE band that says WHERE THE GAQCORPUS RATING "
    "DISTRIBUTION WOULD FALL for this dimension. This is a DISTRIBUTIONAL "
    "PLACEMENT, not a grade: a band is NOT a judgment that the argument is good "
    "or bad. A `lower` placement is FREQUENTLY appropriate in context — a "
    "deliberately one-sided polemic, a rebuttal piece, or a register where "
    "engaging the other side is out of scope can all sit `lower` on dialectic "
    "and be entirely fine. You are NOT grading the argument. Never assert the "
    "argument IS high- or low-quality, never combine the three dimensions into "
    "an overall judgment or a number, and never produce a roll-up. When you "
    "cannot place a dimension (too short, no discernible argument, low "
    "confidence), return null for that dimension's band — do NOT invent a band, "
    "and do NOT default a declined dimension to `lower`. The editor adjudicates "
    "quality; you supply a theory-structured set of observations."
)


def render_prompt() -> str:
    """Build the user-side dimension-profiling prompt (taxonomy legend + framing
    + output format). The system preamble is prepended for API judges."""
    lines: list[str] = []
    lines.append(
        "# Argument-quality dimensions — place a band for each, independently\n"
    )
    for d in DIMENSIONS:
        lines.append(f"- `{d}`: {DIMENSION_LEGEND[d]}")
    lines.append(
        "\n# Bands (distributional placement, NOT a grade)\n"
        "For each dimension, choose ONE band — `lower`, `mid`, or `higher` — as "
        "WHERE THE GAQCORPUS RATING DISTRIBUTION WOULD PLACE THIS DIMENSION "
        "(lower / middle / upper tercile), or `null` if you cannot place it. A "
        "band is a distributional placement, NOT a claim that the argument is "
        "good or bad. A `lower` band is frequently appropriate in context. "
        "Place each dimension INDEPENDENTLY; do NOT average, sum, or roll the "
        "three into an overall band or score — there is no overall judgment."
    )
    lines.append(
        "\n# Output format\n"
        "Return a single JSON object with one key, `dimensions`, whose value is "
        "an object with EXACTLY the three keys `logic`, `rhetoric`, `dialectic`. "
        "Each maps to an object "
        '`{"band": <one of "lower" / "mid" / "higher", or null>, '
        '"evidence_spans": [<verbatim quoted spans, copied exactly from the '
        "passage, that you read as evidence for this band — each a substring of "
        'one paragraph>], "basis": <one short sentence stating WHY this band, '
        "as an observation for the editor — never an assertion the argument is "
        "good or bad>}. Use null for `band` when you cannot place the dimension; "
        "leave `evidence_spans` an empty list then. Do NOT add any other key "
        "(no overall, no score, no quality, no aggregate). Output JSON only, no "
        "prose."
    )
    return "\n".join(lines)


def _number_paragraphs(paragraphs: list[str]) -> str:
    return "\n\n".join(f"[{i}] {p}" for i, p in enumerate(paragraphs))


def fingerprint_prompt(prompt_text: str = "") -> str:
    """SHA-256 of THIS module's system preamble + canonical prompt — provenance:
    identical fingerprints mean byte-identical prompts. MUST NOT delegate to
    ``fallacy_judge`` / ``argument_judge`` (different prompt → wrong hash → a
    drift gate keyed here would silently hash the wrong prompt; spec-26 P1)."""
    body = _SYSTEM_PREAMBLE + "\n" + (prompt_text or render_prompt())
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# ----------------- backend: shared validation ---------------------
def _normws(s: str) -> str:
    """Whitespace-normalized form for a tolerant verbatim-containment check."""
    return " ".join(s.split())


def _normalize_spans(raw: Any, paragraphs: list[str]) -> list[str]:
    """Keep only spans that are a verbatim (whitespace-normalized) substring of
    SOME paragraph — a hallucinated span the judge did not actually quote is
    dropped (the spec-26 #229 lesson). Order preserved; non-strings / empties
    dropped. Spans are paragraph-anchored verbatim text, NOT character offsets."""
    out: list[str] = []
    if not isinstance(raw, list):
        return out
    norm_paras = [_normws(p) for p in paragraphs]
    for span in raw:
        if not isinstance(span, str) or not span.strip():
            continue
        ns = _normws(span)
        if any(ns in p for p in norm_paras):
            out.append(span)
    return out


def normalize_dimensions(raw: Any, paragraphs: list[str]) -> dict[str, Any]:
    """Validate + normalize a raw judge ``dimensions`` map into the result
    schema. Returns EXACTLY the three dimension keys (`logic` / `rhetoric` /
    `dialectic`), each ``{band, evidence_spans, basis}``.

    Discipline:
      * a missing / malformed dimension, an unrecognized band, or a non-dict
        entry yields ``band: None`` (declined) — NEVER fabricated, NEVER coerced
        to ``lower``. ``null`` is first-class.
      * spans are filtered to verbatim paragraph-anchored substrings
        (hallucinated spans dropped); a declined dimension carries an empty
        ``evidence_spans`` list.
      * ``basis`` defaults to "" when absent / non-string.
    The output is always the three keys, so the surface's data shape is stable
    regardless of what the judge returned (no extra/aggregate keys can leak)."""
    raw_map = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    for d in DIMENSIONS:
        entry = raw_map.get(d)
        if not isinstance(entry, dict):
            out[d] = {"band": None, "evidence_spans": [], "basis": ""}
            continue
        band = entry.get("band")
        if band not in BANDS:
            # Anything that is not one of the three valid bands (including a
            # literal null, a typo, or a number) becomes a first-class decline.
            band = None
        spans = _normalize_spans(entry.get("evidence_spans"), paragraphs)
        # A declined dimension carries no evidence spans (absence ≠ evidence).
        if band is None:
            spans = []
        basis = entry.get("basis")
        out[d] = {
            "band": band,
            "evidence_spans": spans,
            "basis": basis if isinstance(basis, str) else "",
        }
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
    if not isinstance(values, dict) or "dimensions" not in values:
        raise JudgeError(
            f"manifest {manifest_path}: missing 'values.dimensions' object"
        )
    ji = data.get("judge_identity")
    ji = ji if isinstance(ji, dict) else {}

    def _run(paragraphs: list[str]) -> JudgeResult:
        return JudgeResult(
            values={
                "dimensions": normalize_dimensions(
                    values.get("dimensions"), paragraphs
                )
            },
            judge_identity={
                "kind": "manifest",
                "manifest_path": str(manifest_path),
                "model": ji.get("model"),
                "model_revision": ji.get("model_revision"),
                "prompt_version": ji.get("prompt_version"),
                # Propagate the manifest's OWN prompt fingerprint, never discard it: the bands were
                # produced under THAT prompt, so the drift gate must see it (else a stale manifest
                # passes a current-vs-current check). None when the manifest declared none (Codex P1).
                "prompt_fingerprint_sha256": ji.get("prompt_fingerprint_sha256"),
            },
            raw_response=None,
        )

    return _run


# ----------------- backend: mock ----------------------------------
def _mock_judge(
    mock_bands: dict[str, str | None] | None = None,
) -> JudgeBackend:
    """Deterministic judge for tests/CI/fixtures. Returns a FIXED three-band
    result: by default ``logic: higher``, ``rhetoric: mid``, ``dialectic:
    null`` (a declined dimension — exercises the null-discipline). Each placed
    dimension quotes that dimension's anchor paragraph's leading words as its
    single evidence span; a declined dimension carries an empty span list.

    It is a STUB — never infer a real dimension reading from it (the provenance
    kind is ``mock``). The fixed bands are chosen to exercise the data-shape:
    one placed-high, one mid, one DECLINED (so the null-is-first-class /
    not-coerced-to-lower test bites)."""
    bands: dict[str, str | None] = (
        dict(mock_bands)
        if mock_bands is not None
        else {"logic": "higher", "rhetoric": "mid", "dialectic": None}
    )

    def _run(paragraphs: list[str]) -> JudgeResult:
        dims: dict[str, Any] = {}
        for i, d in enumerate(DIMENSIONS):
            band = bands.get(d)
            if band not in BANDS:
                band = None
            spans: list[str] = []
            basis = ""
            if band is not None and paragraphs:
                # Anchor each placed dimension to a paragraph (cyclically) and
                # quote its leading words as a verbatim span.
                para = paragraphs[i % len(paragraphs)]
                words = para.split()
                span = " ".join(words[:8]) if words else para[:40]
                if span.strip():
                    spans = [span]
                basis = (
                    f"(mock) the GAQCorpus distribution would place {d} at "
                    f"`{band}` here; an editor should read this as an "
                    f"observation, not a grade."
                )
            else:
                basis = (
                    f"(mock) could not place {d}; band is null (declined, not "
                    f"`lower`)."
                )
            dims[d] = {"band": band, "evidence_spans": spans, "basis": basis}
        # Route through normalize_dimensions so the mock obeys the same span /
        # band discipline as a real judge (defensive; identity for valid input).
        return JudgeResult(
            values={"dimensions": normalize_dimensions(dims, paragraphs)},
            judge_identity={"kind": "mock", "bands": dict(bands)},
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


# make_api_judge invokes this as build_result(parsed, raw_text, identity, paragraphs).
def _build_api_result(
    parsed: Any, raw: str, identity: dict[str, Any], paragraphs: list[str]
) -> JudgeResult:
    if not isinstance(parsed, dict) or "dimensions" not in parsed:
        raise JudgeError("judge JSON missing 'dimensions' object")
    ident = dict(identity)
    ident.setdefault("prompt_version", PROMPT_VERSION)
    ident["prompt_fingerprint_sha256"] = fingerprint_prompt()
    return JudgeResult(
        values={
            "dimensions": normalize_dimensions(parsed.get("dimensions"), paragraphs)
        },
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
    mock_bands: dict[str, str | None] | None = None,
) -> JudgeBackend:
    """Construct a judge backend by kind. THIS function owns the mock/manifest
    dispatch (spec-30 P3): ``manifest`` → own ``_manifest_judge``; ``mock`` →
    own ``_mock_judge``; only the three API kinds (``anthropic`` / ``openai`` /
    ``gemini``) delegate to ``judge_backends.make_api_judge``. ``mock`` /
    ``manifest`` never route into ``make_api_judge``."""
    if kind == "manifest":
        if manifest_path is None:
            raise JudgeError("manifest judge requires manifest_path")
        return _manifest_judge(Path(manifest_path))
    if kind == "mock":
        return _mock_judge(mock_bands)
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
