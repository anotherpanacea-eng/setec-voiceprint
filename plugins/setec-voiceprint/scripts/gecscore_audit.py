#!/usr/bin/env python3
"""gecscore_audit.py — GECScore grammar-error-density signal (spec 32, M1).

A grammar-error-density discrimination signal, structurally orthogonal to both of
SETEC's existing detection surfaces — the *probability* surface (Binoculars /
surprisal / curvature / spectral) and the *distributional* surface (the 13-signal
glass-box stylometry). It asks neither how the model decodes the text nor how the
surface distribution compares to a baseline, but: **how much does a grammar-error
corrector change the text?** AI prose, RLHF-polished to near-zero grammar error, is
changed little (high similarity → high gecscore); human prose retains residual
micro-errors (comma splices, subject-verb-distance mismatches, idiomatic fragments)
and is changed more (lower gecscore). The GECScore lead is arXiv:2405.04286
(2024 preprint; [VERIFIED from the primary source: the paper claims a 98.62% avg
AUROC across XSum + WritingPrompts] — but that is the paper's own claim, NOT
independently reproduced on SETEC's corpus, so it is a LEAD, never a target).
Evidence, not verdict.

Spec: ``specs/32-gec-linguistic-error-axis.md``.

MECHANISM
=========
``gec_sim(s) = SequenceMatcher(None, s, GEC(s), autojunk=False).ratio()`` in [0, 1]
— the stdlib ``difflib`` character-level Gestalt-pattern similarity ratio, where
``ratio() = 2*M / (len(s) + len(corrected))`` (M = total matched characters,
normalized by the SUM of the two lengths — NOT by ``max(len)`` and NOT a
Levenshtein edit distance). Equivalently ``1 - dissimilarity`` where
``dissimilarity = 1 - ratio()``. ``autojunk=False`` is passed so difflib's
length-triggered "popular character" heuristic (which perturbs ``ratio()`` on prose
>200 chars and can flip the band) never fires. ``gec_sim = 1.0`` ⇒ the corrector
changed nothing (zero detected errors). A secondary raw count
``gec_n_corrections`` (distinct correction spans) complements the similarity on
short passages.

DIRECTION (PINNED — silent inversion is the family's shared failure mode)
========================================================================
``GEC_AI_DIRECTION = "gt"``: HIGHER gec_sim ⇒ fewer errors ⇒ the paper's
"more AI-like" DIRECTION. This is a fixed linguistic prior, NOT a tuned parameter,
and it is asserted in a unit test — flipping the sign would flip the band.

INJECTABLE GEC BACKEND (the M1/M2 seam)
=======================================
The grammar corrector is the ONLY load-bearing model/compute dependency, so it is
the seam. ``audit_gecscore`` takes an injectable ``backend`` exposing
``correct(text) -> str`` (and optional ``count_corrections``). M1 default =
``StubGecBackend`` (returns input unchanged → gec_sim 1.0, or a canned fixture
correction) — model-free, CI-runnable, over INJECTED scores. M2 swaps in
``LanguageToolBackend`` (Java on PATH) or ``GecTorBackend`` (torch) behind the SAME
seam — no model is imported at module load or touched in tests.

ESL / DIALECT INVERSION (gated — fold from REVIEW_gec Change 1, CRITICAL)
========================================================================
The ROADMAP gates GECScore behind ``fairness_dialect_guardrails`` because the
surface INVERTS on ESL/dialect prose: a polished non-native author writes low-error
English and scores near 1.0 — the SAME direction as AI. This module co-emits a
``fairness_dialect_guardrails`` caution block (``results.fairness_guardrails``),
detecting code-switching heuristically on the target and routing any declared
background conditions, and surfaces the posture cap as a caveat. The inversion is
named FIRST-CLASS in the claim-license ``does_not_license`` text, not a footnote.

POSTURE (no verdict)
====================
Descriptive only: VALUES (``gecscore`` + ``gec_n_corrections``) + a PROVISIONAL
``band`` over the value's OWN axis (``indeterminate`` / ``low_error_density`` /
``high_error_density``) carrying ``calibration_status: heuristic`` +
``calibration_anchor: user-baseline-required``, and a claim-license that refuses any
AI/human or thresholded verdict. There is NO ``is_ai`` / ``is_human`` / ``label`` /
``verdict`` / ``decision`` key. The band names the MEASURED property (grammar-error
density), never the inference target (authorship). ``gecscore`` is a read-only
evidence column — it never feeds ``fitness`` / ``setec_signals`` / selection /
scoring (a comment-stripped source scan pins this).

CLI:

    python3 scripts/gecscore_audit.py --target TARGET [--declare COND ...] [--json]
    python3 scripts/gecscore_audit.py --batch MANIFEST [--declare COND ...] [--out PATH]
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore  # noqa: E402
from output_schema import (  # noqa: E402
    build_error_output,
    build_output,
)
from stylometry_core import word_tokens  # type: ignore  # noqa: E402

# NOTE (separation guard, AC-5): this module deliberately imports NOTHING from the
# SETEC fitness / selection / scoring family. gecscore is a read-only evidence
# column reported to the operator, never a selection signal. fairness_dialect_
# guardrails is imported lazily inside the co-emit helper (it is a peer evidence
# surface, not a selection signal) so a structural import scan of THIS module's
# top level stays clean and the M1 import is model-free.

TASK_SURFACE = "gecscore_discrimination"
TOOL_NAME = "gecscore_audit"
SCRIPT_VERSION = "1.0"

# PINNED detection direction. HIGHER gec_sim ⇒ fewer grammar errors ⇒ the paper's
# "more AI-like" DIRECTION (arXiv:2405.04286). A fixed linguistic prior, asserted
# in a test — silent sign inversion is the detection family's shared failure mode.
# This is NOT a verdict ("gt" names the direction, not a decision boundary).
GEC_AI_DIRECTION = "gt"

# Word floor (matches rewriting_invariance_audit.py). Below it the normalized edit
# distance is noisy at a small denominator, so the surface WARNS rather than refuses.
LENGTH_FLOOR_WORDS = 50

# PROVISIONAL band thresholds on the gecscore VALUE's own axis. Fixture-derived
# first-reading numbers, NOT a calibrated operating point: calibration_status is
# "heuristic", calibration_anchor is "user-baseline-required". Disjoint from any
# held-out validation corpus (anti-Goodhart); promotion past "heuristic" goes only
# through scripts/calibration/ against a labeled corpus, never by tuning here.
PROVISIONAL_BAND_THRESHOLDS: dict[str, dict[str, float]] = {
    "gecscore": {
        # HIGH gecscore = near-zero error density (paper's "more AI-like"
        # DIRECTION; NOT "is AI"). LOW gecscore = many corrections (human-leaning).
        "low_error_above": 0.97,
        "high_error_below": 0.90,
    },
}


# ----------------------------------------------------------------------
# Injectable GEC backend (the M1/M2 seam).
# ----------------------------------------------------------------------


class GecBackend:
    """Protocol for a grammar-error corrector. One method: ``correct(text) -> str``.

    Optionally exposes ``count_corrections(original, corrected) -> int`` for a
    backend that knows its own span count (LanguageTool reports matches); when
    absent, the audit falls back to a stdlib opcode-based span count over the
    original/corrected pair. ``kind``/``id`` identify the regime in provenance —
    values are NOT comparable across backends.

    ``is_stub`` marks a backend that does NOT run a real grammar corrector (the M1
    identity fixture). The production CLI refuses to report a gecscore from a
    ``is_stub`` backend (a non-run identity correction makes every target read
    ``gecscore=1.0`` / ``low_error_density`` — a fake clean score), emitting an
    ``available: false`` / ``missing_dependency`` abstaining envelope instead.
    Real M2 backends (LanguageTool/GECToR) leave it ``False``."""

    kind: str = "abstract"
    id: str | None = None
    is_stub: bool = False

    def correct(self, text: str) -> str:  # pragma: no cover - abstract
        raise NotImplementedError


class StubGecBackend(GecBackend):
    """The M1 default: model-free, CI-runnable, over INJECTED scores.

    With no ``corrections`` map it returns its input unchanged (zero errors →
    gec_sim 1.0). A ``corrections`` map ``{original: corrected}`` lets a fixture
    inject a canned correction; an optional ``span_counts`` map overrides the
    reported ``gec_n_corrections`` for a fixture (otherwise the opcode-based count
    is used). No model is loaded — this is the seam tests drive. It is a STUB,
    never a real corrector: ``is_stub = True`` so the production CLI abstains
    rather than reporting an identity-correction ``gecscore=1.0`` as a completed
    measurement (the no-run-masquerading-as-clean-score failure mode)."""

    kind = "stub_identity"
    is_stub = True

    def __init__(
        self,
        corrections: dict[str, str] | None = None,
        *,
        span_counts: dict[str, int] | None = None,
    ) -> None:
        self._corrections = dict(corrections or {})
        self._span_counts = dict(span_counts or {})

    def correct(self, text: str) -> str:
        return self._corrections.get(text, text)

    def count_corrections(self, original: str, corrected: str) -> int:
        if original in self._span_counts:
            return self._span_counts[original]
        return count_correction_spans(original, corrected)


# ----------------------------------------------------------------------
# Similarity / span math (stdlib, deterministic).
# ----------------------------------------------------------------------


def sequence_dissimilarity(original: str, corrected: str) -> float:
    """Character-level Gestalt-pattern DISSIMILARITY in [0, 1].

    ``1 - difflib.SequenceMatcher(None, original, corrected, autojunk=False).ratio()``
    (stdlib, deterministic). This is NOT a Levenshtein edit distance and is NOT
    normalized by ``max(len)``: ``ratio() = 2*M / (len(a) + len(b))`` (M = matched
    chars, normalized by the SUM of lengths). 0.0 = identical; 1.0 = maximally
    different. ``autojunk=False`` disables difflib's length-triggered "popular
    character" heuristic, which otherwise perturbs ``ratio()`` on prose >200 chars
    (and can flip the descriptive band) for reasons unrelated to grammar; with it
    off the metric is the plain Gestalt ratio. Empty edge: two empty strings differ
    by 0.0 (nothing to correct → identical); a non-empty string vs. an empty
    correction differs by 1.0 (everything deleted)."""
    if not original and not corrected:
        return 0.0
    if not original or not corrected:
        return 1.0
    ratio = difflib.SequenceMatcher(
        None, original, corrected, autojunk=False
    ).ratio()
    return 1.0 - ratio


# Back-compat alias: the metric was historically (and mis-)named
# ``normalized_edit_distance``. It is a Gestalt-pattern dissimilarity, not a
# max(len)-normalized edit distance; the accurate name is ``sequence_dissimilarity``.
normalized_edit_distance = sequence_dissimilarity


def gec_similarity(original: str, corrected: str) -> float:
    """``gec_sim = difflib.SequenceMatcher(None, s, GEC(s), autojunk=False).ratio()``
    in [0, 1] — equivalently ``1 - sequence_dissimilarity``. 1.0 = the corrector
    changed nothing (zero detected errors); lower = more rewritten (more errors)."""
    return 1.0 - sequence_dissimilarity(original, corrected)


def count_correction_spans(original: str, corrected: str) -> int:
    """Number of distinct edit spans between original and corrected — the count of
    non-``equal`` opcodes from difflib (replace / delete / insert). A raw integer
    (NOT normalized), complementing the similarity on short passages. Identical
    input/correction → 0 spans. ``autojunk=False`` keeps the span count consistent
    with :func:`sequence_dissimilarity` (no length-triggered junk heuristic)."""
    sm = difflib.SequenceMatcher(None, original, corrected, autojunk=False)
    return sum(1 for tag, *_ in sm.get_opcodes() if tag != "equal")


# ----------------------------------------------------------------------
# Provisional band (descriptive, over the value's OWN axis — NOT a verdict).
# ----------------------------------------------------------------------


def _provisional_band(gecscore: float, *, gec_n_corrections: int) -> dict[str, Any]:
    """Descriptive band over the gecscore VALUE's own axis (grammar-error
    density). NEVER over authorship. ``band ∈ {indeterminate, low_error_density,
    high_error_density}`` is the only categorical leaf in the whole envelope.
    Ships ``heuristic`` + ``user-baseline-required`` so it is never read as a
    calibrated decision boundary.

    Orientation: HIGH gecscore ⇒ near-zero error density ⇒ ``low_error_density``
    (the paper's "more-AI-like" DIRECTION, ``GEC_AI_DIRECTION = "gt"``); LOW
    gecscore ⇒ many corrections ⇒ ``high_error_density`` (the human-leaning
    direction). The band names the MEASURED property, never "is AI"."""
    th = PROVISIONAL_BAND_THRESHOLDS["gecscore"]
    band = "indeterminate"
    flags: list[str] = []
    if gecscore > th["low_error_above"]:
        band = "low_error_density"
        flags.append("near_zero_error_density")
    elif gecscore < th["high_error_below"]:
        band = "high_error_density"
        flags.append("residual_errors_present")
    return {
        "band": band,
        "flags": flags,
        "calibration_status": "heuristic",
        "calibration_anchor": "user-baseline-required",
        "thresholds_used": {"gecscore": dict(th)},
        "direction": GEC_AI_DIRECTION,
        "orientation": (
            "HIGH gecscore = near-zero grammar-error density (low_error_density; "
            "the paper's 'more AI-like' DIRECTION, GEC_AI_DIRECTION='gt'); LOW "
            "gecscore = residual errors (high_error_density). NOT 'is AI'"
        ),
        "gec_n_corrections": gec_n_corrections,
    }


# ----------------------------------------------------------------------
# Co-emitted fairness / dialect gate (REVIEW_gec Change 1, CRITICAL).
# ----------------------------------------------------------------------


def co_emit_fairness_guardrails(
    target_text: str | None,
    *,
    declared_conditions: list[str] | None = None,
) -> dict[str, Any]:
    """Run ``fairness_dialect_guardrails`` over the target and return its caution
    report. This is the STRUCTURAL gate the ROADMAP requires (not a prose
    footnote): GECScore inverts on ESL/dialect prose, so the surface co-emits the
    guardrail's per-condition flags + posture cap. Code-switching is detected
    heuristically from ``target_text``; declared background conditions
    (``nonnative_english``, ``dialect_features``, …) are routed through.

    Imported lazily here (a peer evidence surface, not a selection signal) so the
    M1 module-top import stays model-free and the separation-guard scan stays
    clean."""
    import fairness_dialect_guardrails as fdg  # type: ignore

    return fdg.build_caution_report(
        target_text=target_text,
        declared_conditions=list(declared_conditions or []),
        baseline_backgrounds={},  # M1 has no validation baseline → conservative
    )


def _fairness_caveats(report: dict[str, Any]) -> list[str]:
    """Turn the guardrail recommendation into gecscore caveats so the ESL/dialect
    inversion is visible where the operator reads the result."""
    rec = report.get("recommendation", {})
    caveats: list[str] = []
    if rec.get("refuses_evaluative_use"):
        uncovered = rec.get("uncovered_conditions") or []
        caveats.append(
            "FAIRNESS GATE: fairness_dialect_guardrails flags "
            f"{', '.join(uncovered) or 'a linguistic-background condition'} with "
            "no comparable validation baseline; gecscore INVERTS on ESL/dialect "
            "prose (polished non-native English scores near 1.0, the AI "
            f"direction). Posture capped at '{rec.get('posture_cap')}', "
            "evaluative/disciplinary use refused (see results.fairness_guardrails)."
        )
    elif rec.get("n_flags", 0) > 0:
        caveats.append(
            "FAIRNESS NOTE: a linguistic-background condition is present but the "
            "baseline covers it; gecscore can still invert on ESL/dialect prose — "
            "read results.fairness_guardrails before weighting this signal."
        )
    return caveats


# ----------------------------------------------------------------------
# Audit (injectable backend).
# ----------------------------------------------------------------------


class GecScoreInputError(ValueError):
    """Raised by ``audit_gecscore`` on an unusable input (no word tokens). The CLI
    maps this to a structured ``build_error_output`` envelope, never a traceback."""


# The exact prose the production CLI emits when only the M1 identity stub is wired
# (no real grammar corrector). Pinned as a module constant so a test asserts the
# production path NEVER reports a stub identity score as a completed measurement.
NO_REAL_BACKEND_REASON = (
    "no real grammar corrector is wired: the only backend available is the M1 "
    "identity stub (StubGecBackend), whose identity correction makes every target "
    "read gecscore=1.0 / low_error_density without running any corrector — a "
    "non-run masquerading as a clean score. The production CLI ABSTAINS rather "
    "than report a fake measurement. Wire a real backend (the M2 "
    "LanguageTool/GECToR seam) to produce a gecscore; the identity stub is "
    "test-only (inject it explicitly in tests)."
)


def backend_is_real(backend: GecBackend | None) -> bool:
    """A backend is REAL (production-reportable) iff it is present AND not flagged
    ``is_stub``. The M1 default (``None`` → identity stub) and any ``is_stub``
    backend are NOT real: the production CLI must abstain rather than report their
    identity-correction gecscore. Real M2 backends (LanguageTool/GECToR) leave
    ``is_stub`` False, so they pass."""
    if backend is None:
        return False
    return not getattr(backend, "is_stub", False)


def audit_gecscore(
    text: str,
    *,
    backend: GecBackend | None = None,
    declared_conditions: list[str] | None = None,
    include_fairness: bool = True,
) -> dict[str, Any]:
    """Compute the GECScore audit. Returns the ``results`` dict for
    ``build_output``.

    ``backend`` is the INJECTION point: any object with ``correct(text) -> str``
    (and optionally ``count_corrections``). M1 default = :class:`StubGecBackend`
    (model-free, no model loaded). M2 callers inject a LanguageTool/GECToR backend
    behind the SAME seam.

    Raises :class:`GecScoreInputError` on a target with no countable word tokens."""
    tokens = word_tokens(text)
    if not tokens:
        raise GecScoreInputError("target has no countable word tokens")

    be = backend if backend is not None else StubGecBackend()
    corrected = be.correct(text)
    if not isinstance(corrected, str):
        raise GecScoreInputError(
            f"backend.correct returned {type(corrected).__name__}, expected str"
        )

    gecscore = gec_similarity(text, corrected)
    if hasattr(be, "count_corrections"):
        n_corrections = int(be.count_corrections(text, corrected))  # type: ignore[attr-defined]
    else:
        n_corrections = count_correction_spans(text, corrected)

    band = _provisional_band(gecscore, gec_n_corrections=n_corrections)

    backend_block = {
        "kind": getattr(be, "kind", "unknown"),
        "id": getattr(be, "id", None),
        "metric": (
            "difflib.SequenceMatcher(None, s, GEC(s), autojunk=False).ratio() "
            "(Gestalt char similarity = 2*M/(len(s)+len(GEC(s))); NOT a max(len)-"
            "normalized edit distance)"
        ),
    }

    results: dict[str, Any] = {
        "gecscore": gecscore,
        "gec_n_corrections": n_corrections,
        "gec_ai_direction": GEC_AI_DIRECTION,
        "target_tokens": len(tokens),
        "target_chars": len(text),
        "corrected_chars": len(corrected),
        "gec_backend": backend_block,
        "band": band,
        "assumptions": {
            "method": (
                "GECScore grammar-error-density similarity = difflib Gestalt ratio "
                "(autojunk off) between the text and its GEC-corrected form "
                "(arXiv:2405.04286 — the paper's claimed 98.62% avg AUROC is "
                "VERIFIED from the primary source but is the paper's own claim, NOT "
                "reproduced on SETEC's corpus, so it is a LEAD, not a prior)"
            ),
            "orientation": (
                "higher gecscore = fewer grammar errors (corrector changes less) = "
                "the paper's 'more AI-like' DIRECTION; orthogonal axis to the "
                "probability and distributional surfaces, NOT a verdict"
            ),
            "m1_stub": (
                "M1 uses an INJECTED corrector (StubGecBackend); the real "
                "LanguageTool/GECToR backends are the M2 model-CPU seam (Java / "
                "torch). gec_backend records which regime produced the value — "
                "values are NOT comparable across backends"
            ),
            "esl_dialect_inversion": (
                "gecscore INVERTS on ESL/dialect prose: a polished non-native "
                "author writes low-error English and scores near 1.0 (the AI "
                "direction). This is surfaced structurally via the co-emitted "
                "fairness_dialect_guardrails block, not a footnote"
            ),
            "adversarial": (
                "deliberate error injection (typos) trivially moves gecscore "
                "toward the human range — the named adversarial Achilles heel"
            ),
        },
    }

    if include_fairness:
        fairness = co_emit_fairness_guardrails(
            text, declared_conditions=declared_conditions
        )
        results["fairness_guardrails"] = fairness
        results["fairness_caveats"] = _fairness_caveats(fairness)

    return results


# ----------------------------------------------------------------------
# Claim license (refuses any verdict; names the ESL/dialect inversion first-class).
# ----------------------------------------------------------------------

DEFAULT_LICENSES = (
    "the grammar-error density of the text as a GECScore similarity — how little a "
    "grammar-error corrector changes it (GECScore, arXiv:2405.04286). It reports "
    "the scalar gecscore in [0,1] (the difflib Gestalt character-similarity ratio, "
    "autojunk off, between the text and its grammar-corrected form — "
    "2*M/(len(s)+len(corrected)), NOT a max(len)-normalized edit distance), the raw "
    "correction-span count (gec_n_corrections), and a DESCRIPTIVE band over that "
    "value's own axis. In the literature AI text tends to HIGHER gecscore "
    "(near-zero error density) than human text, so the scalar is discrimination "
    "evidence on an axis orthogonal to the probability (Binoculars/surprisal/"
    "curvature) and distributional (glass-box stylometry) surfaces. It is a "
    "measurement, not a verdict. The paper's claimed 98.62% avg AUROC (XSum + "
    "WritingPrompts) is VERIFIED from the primary source but is the paper's own "
    "claim, NOT reproduced on SETEC's corpus — a lead, not a prior asserted here."
)

DEFAULT_DOES_NOT_LICENSE = (
    "any AI/human authorship verdict, label, or thresholded decision. The surface "
    "ships uncalibrated: the band is PROVISIONAL (calibration_status heuristic, "
    "calibration_anchor user-baseline-required) and names the MEASURED property "
    "(grammar-error density), never the inference target (authorship). There is no "
    "is_ai / is_human / classification / prediction / verdict / decision key. "
    "ESL / NON-NATIVE FALSE-POSITIVE FAILURE MODE (load-bearing, not a footnote): "
    "a non-native English author who writes polished, low-error prose scores HIGH "
    "on gecscore — the SAME direction as AI — so the surface INVERTS on ESL and "
    "non-standard-dialect prose; this is why the ROADMAP gates GECScore behind "
    "fairness_dialect_guardrails, whose caution block is co-emitted in "
    "results.fairness_guardrails and must be read before weighting this signal. "
    "Near-zero grammar-error density does NOT prove AI authorship (copy-edited "
    "fiction and professional non-native authors also score near 1.0), and the "
    "signal is NOT robust to adversarial error injection (deliberate typos move "
    "gecscore toward the human range — the named Achilles heel). The M1 value uses "
    "an INJECTED stub corrector (the model-free seam); a real LanguageTool/GECToR "
    "run (M2) is not comparable across backends (gec_backend records the regime). "
    "Below the length floor (50 words) the normalized distance is noisy and the "
    "surface warns. It is one axis among many for the multi-signal evidence pack, "
    "with the human in the loop; promotion of the band past heuristic goes only "
    "through scripts/calibration/ against a labeled corpus, never by tuning on a "
    "held-out set."
)


def _claim_license(results: dict[str, Any]) -> ClaimLicense:
    backend = results.get("gec_backend", {})
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=DEFAULT_LICENSES,
        does_not_license=DEFAULT_DOES_NOT_LICENSE,
        comparison_set={
            "mode": "single_document_uncalibrated",
            "gec_backend": backend.get("kind"),
            "gec_ai_direction": GEC_AI_DIRECTION,
        },
        additional_caveats=[
            "Uncalibrated — provisional band, no verdict, no shipped operating "
            "point (calibration_status heuristic).",
            "ESL/dialect INVERSION is the primary false-positive failure mode and "
            "is surfaced structurally via the co-emitted fairness_dialect_"
            "guardrails block (results.fairness_guardrails), not a footnote — read "
            "it before weighting gecscore on prose of uncertain linguistic "
            "background.",
            "Adversarial error injection (typos) trivially defeats gecscore by "
            "moving it toward the human range — the named Achilles heel.",
            "M1 uses an INJECTED corrector (StubGecBackend); values from the M2 "
            "LanguageTool/GECToR backends are NOT comparable to M1 or to each "
            "other (gec_backend records the regime).",
            "The paper's 98.62% avg AUROC (arXiv:2405.04286, XSum + WritingPrompts) "
            "is VERIFIED from the primary source but is the paper's own claim, NOT "
            "reproduced on SETEC's corpus — a lead, not a prior; no SETEC result "
            "asserts it as a SETEC-measured number.",
            "gecscore is a read-only EVIDENCE column — it never feeds SETEC "
            "fitness / selection / scoring (anti-Goodhart; pinned by a "
            "separation-guard source scan).",
        ],
        references=[
            "GECScore, arXiv:2405.04286 (2024 preprint; the 98.62% avg AUROC claim "
            "is confirmed from the primary source but is [UNVERIFIED on SETEC's "
            "corpus] — not independently reproduced here) — "
            "https://arxiv.org/abs/2405.04286",
            "specs/32-gec-linguistic-error-axis.md",
        ],
    )


def compose_envelope(
    *,
    target_path: Path | str | None,
    target_words: int,
    results: dict[str, Any],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=None,
        results=results,
        claim_license=_claim_license(results),
        available=True,
        warnings=warnings,
    )


# ----------------------------------------------------------------------
# Markdown renderer.
# ----------------------------------------------------------------------


def render_markdown(envelope: dict[str, Any]) -> str:
    results = envelope["results"]
    target = envelope["target"]
    band = results.get("band", {})
    backend = results.get("gec_backend", {})
    fairness = results.get("fairness_guardrails", {})
    rec = fairness.get("recommendation", {}) if fairness else {}
    lines: list[str] = [
        "# GECScore Grammar-Error-Density Audit",
        "",
        f"- **Target:** `{target.get('path')}` ({target.get('words')} words)",
        f"- **GEC backend:** `{backend.get('kind')}` ({backend.get('metric')})",
        f"- **Direction (pinned):** `GEC_AI_DIRECTION = "
        f"{results.get('gec_ai_direction')!r}` "
        "(higher gecscore = fewer errors = the paper's AI-like direction)",
        "",
        "## Result",
        "",
        f"**gecscore:** {results.get('gecscore'):.4f}",
        f"**gec_n_corrections:** {results.get('gec_n_corrections')}",
        f"**Band (DESCRIPTIVE, over the value's own axis):** "
        f"`{band.get('band')}` "
        f"(calibration_status: `{band.get('calibration_status')}`, "
        f"anchor: `{band.get('calibration_anchor')}`)",
        "",
        "_Higher gecscore = near-zero grammar-error density (the paper's "
        "'more AI-like' DIRECTION); NOT 'is AI'. Uncalibrated: the band is "
        "provisional, no verdict, no shipped threshold._",
        "",
        "## Fairness / dialect guardrails (co-emitted — CRITICAL)",
        "",
        f"**Overall:** `{rec.get('overall', 'no_conditions_flagged')}`  "
        f"**Posture cap:** {rec.get('posture_cap') or '(none)'}  "
        f"**Refuses evaluative use:** "
        f"{'**yes**' if rec.get('refuses_evaluative_use') else 'no'}",
        "",
        "_gecscore INVERTS on ESL/dialect prose: polished non-native English "
        "scores near 1.0 (the AI direction). The ROADMAP gates GECScore behind "
        "fairness_dialect_guardrails; read the block above before weighting this "
        "signal._",
        "",
        "## Claim license",
        "",
        (envelope.get("claim_license_rendered") or "").rstrip(),
        "",
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Batch mode (one row per manifest passage — the M2 corpus-run shape).
# ----------------------------------------------------------------------


def _load_batch_manifest(path: Path) -> list[dict[str, Any]]:
    """Load a batch manifest: JSONL (one ``{"id":..., "text":...}`` per line) or
    a JSON list of the same. ``id`` and ``text`` are required per row; a missing
    ``text`` falls back to reading a ``path`` field."""
    raw = path.read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        for lineno, line in enumerate(raw.splitlines(), start=1):
            s = line.strip()
            if not s:
                continue
            obj = json.loads(s)
            if not isinstance(obj, dict):
                raise ValueError(f"manifest line {lineno} is not an object")
            rows.append(obj)
    else:
        data = json.loads(raw)
        entries = data if isinstance(data, list) else data.get("entries", [])
        for obj in entries:
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def run_batch(
    rows: list[dict[str, Any]],
    *,
    backend: GecBackend | None = None,
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Score each manifest row → a feature-column row that carries the calibration
    context so the descriptive ``band`` never travels naked downstream.

    Each scored row is ``{id, gecscore, gec_n_corrections, band, calibration_status,
    gec_ai_direction, words, below_floor[, esl_inversion_caveat]}``. The per-row
    fairness BLOCK is not co-emitted (the guardrail is a corpus-level surface, not a
    per-passage column) — but the row carries ``calibration_status: "heuristic"``,
    the pinned direction, the word count + sub-floor flag (so a consumer can filter
    or down-weight short/uncalibrated passages), and a one-line ESL-inversion
    caveat. The corpus-level fairness obligation is surfaced in the payload header
    by :func:`_main_batch` (``fairness_required`` + the exact guardrail command),
    so a bare ``band`` is never consumed as a calibrated/fairness-cleared
    categorical."""
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        rid = row.get("id", f"row_{i}")
        text = row.get("text")
        if text is None and row.get("path"):
            p = Path(row["path"])
            if base_dir is not None and not p.is_absolute():
                p = base_dir / p
            text = p.read_text(encoding="utf-8", errors="ignore")
        if not text:
            out.append({"id": rid, "gecscore": None, "gec_n_corrections": None,
                        "band": "indeterminate", "calibration_status": "heuristic",
                        "skipped": "empty_text"})
            continue
        try:
            r = audit_gecscore(text, backend=backend, include_fairness=False)
        except GecScoreInputError:
            out.append({"id": rid, "gecscore": None, "gec_n_corrections": None,
                        "band": "indeterminate", "calibration_status": "heuristic",
                        "skipped": "no_word_tokens"})
            continue
        n_words = len(word_tokens(text))
        out.append({
            "id": rid,
            "gecscore": r["gecscore"],
            "gec_n_corrections": r["gec_n_corrections"],
            "band": r["band"]["band"],
            # Carry the calibration context so the band is never read as a
            # calibrated decision boundary downstream (REVIEW: batch is the
            # feature-column ingestion path).
            "calibration_status": r["band"]["calibration_status"],
            "gec_ai_direction": GEC_AI_DIRECTION,
            "words": n_words,
            # LENGTH_FLOOR is enforced on the corpus path too: below it the
            # similarity is noisy at a small denominator, so the row is flagged
            # (not dropped) for a downstream consumer to filter / down-weight.
            "below_floor": n_words < LENGTH_FLOOR_WORDS,
            "esl_inversion_caveat": (
                "gecscore INVERTS on ESL/dialect prose (polished non-native English "
                "scores near 1.0, the AI direction); run fairness_dialect_"
                "guardrails on this corpus before evaluative use"
            ),
        })
    return out


# ----------------------------------------------------------------------
# CLI.
# ----------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "GECScore grammar-error-density audit (M1, stdlib, model-free): how "
            "little a grammar-error corrector changes a text. Descriptive "
            "discrimination evidence on an axis orthogonal to the probability and "
            "distributional surfaces — NO verdict. M1 runs over an injected "
            "corrector (the real LanguageTool/GECToR backends are the M2 seam)."
        ),
    )
    p.add_argument("--target", help="Path to a single target text file (UTF-8).")
    p.add_argument(
        "--batch", metavar="MANIFEST",
        help="Path to a batch manifest (JSONL or JSON list of {id, text|path}); "
             "emits one row per passage (the M2 corpus-run shape).",
    )
    p.add_argument(
        "--declare", action="append", dest="declared", default=[],
        help="Declare a linguistic-background condition for the co-emitted "
             "fairness_dialect_guardrails gate (e.g. nonnative_english, "
             "dialect_features). Repeat for multiple.",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit the JSON envelope instead of a markdown report (single target).",
    )
    p.add_argument("--out", default=None, help="Write output to this path instead of stdout.")
    return p


def main(argv: list[str] | None = None, *, backend: GecBackend | None = None) -> int:
    """CLI entrypoint. ``backend`` is the M2 INJECTION seam: the production CLI
    reports a gecscore only when a REAL corrector is wired (``backend_is_real``).
    With no backend (the M1 default) the only thing available is the identity
    stub, so the CLI ABSTAINS with an available:false / missing_dependency
    envelope — it never reports a stub identity-correction gecscore=1.0 as a
    completed measurement (Codex P1, round 10; #62/#259 posture class)."""
    args = build_arg_parser().parse_args(argv)

    if not args.target and not args.batch:
        sys.stderr.write("[gecscore_audit] one of --target or --batch is required\n")
        return 2
    if args.target and args.batch:
        sys.stderr.write("[gecscore_audit] --target and --batch are mutually exclusive\n")
        return 2

    if args.batch:
        return _main_batch(args, backend=backend)
    return _main_single(args, backend=backend)


def _main_single(args: argparse.Namespace, *, backend: GecBackend | None = None) -> int:
    target_path = Path(args.target).expanduser()
    try:
        target_text = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=f"cannot read --target: {exc}", reason_category="bad_input",
        )
        _emit(envelope, args, as_markdown=False)
        return 3

    # Production guard (Codex P1, round 10; posture class #62/#259): the production
    # CLI must NOT report a gecscore from the M1 identity stub. With no real
    # corrector wired, the identity correction makes EVERY target read
    # gecscore=1.0 / low_error_density — a non-run masquerading as a clean score.
    # Abstain with an available:false / missing_dependency envelope instead. The
    # stub stays test-only (injected explicitly in tests / a future M2 backend).
    if not backend_is_real(backend):
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=NO_REAL_BACKEND_REASON, reason_category="missing_dependency",
        )
        _emit(envelope, args, as_markdown=False)
        return 3

    word_count = len(word_tokens(target_text))
    if word_count == 0:
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), target_words=0,
            reason="target has no countable word tokens",
            reason_category="text_too_short",
        )
        _emit(envelope, args, as_markdown=False)
        return 3

    warnings: list[str] = []
    if word_count < LENGTH_FLOOR_WORDS:
        warnings.append(
            f"target is {word_count} words, below the {LENGTH_FLOOR_WORDS}-word "
            "floor; the normalized edit distance is noisy on short text — "
            "reported but not over-claimed"
        )

    try:
        results = audit_gecscore(
            target_text, backend=backend, declared_conditions=args.declared
        )
    except GecScoreInputError as exc:
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), target_words=word_count,
            reason=str(exc), reason_category="bad_input",
        )
        _emit(envelope, args, as_markdown=False)
        return 3

    # Fold the fairness gate's posture-cap into the envelope warnings so the
    # ESL/dialect inversion is visible at the top level too.
    warnings = warnings + list(results.get("fairness_caveats", []))

    envelope = compose_envelope(
        target_path=target_path,
        target_words=word_count,
        results=results,
        warnings=warnings or None,
    )
    _emit(envelope, args, as_markdown=not args.json)
    return 0


def _main_batch(args: argparse.Namespace, *, backend: GecBackend | None = None) -> int:
    manifest_path = Path(args.batch).expanduser()
    # Production guard (Codex P1, round 10): the batch CLI is the feature-column
    # ingestion path. With no real corrector wired, EVERY row would carry the
    # identity-stub gecscore=1.0 / low_error_density — a whole column of fake clean
    # scores. Abstain before reading the manifest (an available:false /
    # missing_dependency envelope), rather than emit the false column.
    if not backend_is_real(backend):
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(manifest_path),
            reason=NO_REAL_BACKEND_REASON, reason_category="missing_dependency",
        )
        text_out = json.dumps(envelope, indent=2, default=str)
        if args.out:
            Path(args.out).write_text(text_out + "\n", encoding="utf-8")
            sys.stderr.write(f"Wrote output to {args.out}\n")
        else:
            sys.stdout.write(text_out + "\n")
        return 3
    try:
        rows = _load_batch_manifest(manifest_path)
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"[gecscore_audit] --batch manifest error: {exc}\n")
        return 3
    out_rows = run_batch(rows, backend=backend, base_dir=manifest_path.parent)
    n_below_floor = sum(1 for r in out_rows if r.get("below_floor"))
    payload = {
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "task_surface": TASK_SURFACE,
        "mode": "batch",
        "n_rows": len(out_rows),
        # Corpus-level calibration + fairness obligation (the band per row carries
        # calibration_status; this header makes the fairness gate a STRUCTURAL
        # requirement on the corpus path, not only a prose note).
        "calibration_status": "heuristic",
        "gec_ai_direction": GEC_AI_DIRECTION,
        "length_floor_words": LENGTH_FLOOR_WORDS,
        "n_below_floor": n_below_floor,
        "fairness_required": True,
        "fairness_command": (
            "python3 plugins/setec-voiceprint/scripts/fairness_dialect_guardrails.py "
            "(run on this corpus before any evaluative/disciplinary use — gecscore "
            "INVERTS on ESL/dialect prose)"
        ),
        "rows": out_rows,
        "note": (
            "batch mode is the feature-column extraction path. Each row carries "
            "calibration_status='heuristic', the pinned gec_ai_direction, its word "
            "count and a below_floor flag, and an esl_inversion_caveat — so a bare "
            "band is never consumed as calibrated/fairness-cleared. The fairness "
            "guardrail is a corpus-level surface (not a per-row column): "
            "fairness_required=true above; run fairness_dialect_guardrails on the "
            "corpus before weighting these bands evaluatively."
        ),
    }
    text_out = json.dumps(payload, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text_out + "\n", encoding="utf-8")
        sys.stderr.write(f"Wrote {len(out_rows)} rows to {args.out}\n")
    else:
        sys.stdout.write(text_out + "\n")
    return 0


def _emit(envelope: dict[str, Any], args: argparse.Namespace, *, as_markdown: bool) -> None:
    if as_markdown:
        text_out = render_markdown(envelope)
    else:
        text_out = json.dumps(envelope, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(
            text_out + ("\n" if not text_out.endswith("\n") else ""),
            encoding="utf-8",
        )
        sys.stderr.write(f"Wrote output to {args.out}\n")
    if not args.out or args.json:
        sys.stdout.write(text_out + ("\n" if not text_out.endswith("\n") else ""))


if __name__ == "__main__":
    sys.exit(main())
