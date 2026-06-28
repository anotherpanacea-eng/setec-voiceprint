#!/usr/bin/env python3
"""argument_certainty_calibration.py — per-claim argument certainty calibration (overclaim profile, M1).

For ONE argument-shaped document, profile — **per load-bearing claim** — whether
the claim's EXPRESSED CERTAINTY (hedged ↔ assertive) matches the EVIDENTIAL
SUPPORT it actually carries. Two mismatches are the read: **overclaim**
(asserted hard / thinly supported) and **underclaim** (hedged or tentative
despite strong support). The per-claim ``certainty × support → alignment`` table
IS the read — NEVER a single "overconfidence score", NEVER a verdict that the
author is arrogant / sloppy / dishonest.

The mechanical no-verdict firewall is the entire defensibility of this
capability and it is MECHANICAL, not rhetorical:
  * ``FORBIDDEN_RESULT_KEYS`` frozenset + ``FORBIDDEN_SUBSTRINGS`` tuple + a
    recursive ``assert_no_verdict()`` (an explicit, CERTAINTY-SCOPED rename of
    within_doc_segmentation's ``assert_no_authorship`` — it does NOT reuse the
    authorship keys/substrings) that raises ``CalibrationVerdictError`` if the
    artifact ever carries an overconfident / arrogant / dunning_kruger /
    dishonest / sloppy / unsound / overconfidence_score / calibration_score /
    author_verdict key (or such a value), called IMMEDIATELY before
    build_output; ``main()`` catches it and routes to ``available:false`` /
    ``policy_refused``. The guard also whitelist-enforces the certainty /
    support / alignment / defense / resolution_class enums.
  * The legitimate-strong-claim FILTER ships ONLY the two EVIDENCE-GATED
    defenses (spec P1-5), in this order: ``defended_stipulated`` (an explicit
    stipulation marker — ``assume`` / ``grant`` / ``for the sake of argument`` /
    ``take as given`` — in the claim's quote, ``str.find``-validated) then
    ``defended_elsewhere`` (a REAL in-document supporting locus for the same
    claim, validated ``text[start:end] == quote`` — a FABRICATED cross-ref FAILS
    validation → build error, closing the firewall hole). The judgmental
    defenses (``defended_analytic`` / ``defended_common_ground``) are M2-ONLY and
    NEVER fire in M1.
  * Filter-integrity is mechanical: an ``overclaim`` (or any ``defended_*``) row
    whose ``rationale`` is empty is a BUILD ERROR (schema.validate_claim_row
    raises), not a silent pass.

**Certainty is the deterministic M1 substrate.** Expressed certainty is computed
over each claim's quote span from the FROZEN ``HEDGE_VOCAB`` / ``BOOSTER_VOCAB``
frozensets (multi-word or word-boundary-guarded — NO bare ``"may"``). The M1
lexicon is AUTHORITATIVE for certainty; an M2 judge refinement is a separately
reported lens that never silently overrides. Support is judge-derived (folded
into the one ``extract_claims`` pass).

Boundary vs ``stance_modality_audit``: that surface ships a DOCUMENT-LEVEL
hedge/booster/evidential distribution (how much hedging the prose carries
overall); THIS surface is PER-CLAIM certainty × PER-CLAIM support → the overclaim
PAIRING (a claim-localized mismatch the document-level distribution cannot
produce).

M1 = mock-deterministic judge (CI-safe). M2 = anthropic (lazy/fail-loud).
Ships ``calibration_status: heuristic`` — directional, no numeric anchor.
Posture: descriptive / no-verdict / anti-Goodhart. Single-document scope.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_schema import build_error_output, build_output  # noqa: E402
from claim_license import from_legacy  # noqa: E402
import argument_certainty_judge as cjudge  # noqa: E402
from argument_certainty_calibration_schema import (  # noqa: E402
    is_defended,
    validate_results,
    SchemaError,
)

TASK_SURFACE = "argument_calibration"
TOOL_NAME = "argument_certainty_calibration"
SCRIPT_VERSION = "1.0"

DEFAULT_LENGTH_FLOOR_WORDS = 50

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")


# ---------- Layer 1: the no-verdict firewall (CERTAINTY-SCOPED rename of
#            within_doc_segmentation.assert_no_authorship) -----------------------

# Exact keys that must never appear at any depth in the results dict. These are
# the CERTAINTY-SCOPED verdict / character / score keys the spec forbids (P1-7):
# certainty↔support alignment is a property of a CLAIM PAIRING, never a verdict
# that the author is overconfident, arrogant, a Dunning-Kruger case, dishonest,
# sloppy, or that the argument is unsound; there is no top-level overconfidence
# or calibration score. Deliberately NOT the authorship keys — reusing those
# would over-block legitimate rationale like "the author stipulated this".
FORBIDDEN_RESULT_KEYS: frozenset[str] = frozenset({
    "overconfident", "arrogant", "dunning_kruger", "dishonest", "sloppy",
    "unsound", "overconfidence_score", "calibration_score", "author_verdict",
})

# Substring blocklist — applied to KEYS ONLY at any nesting depth (mirroring
# within_doc_segmentation: a blanket key-AND-value walk would raise on the
# surface's own honest does_not_license / rationale prose, which legitimately
# contains "overclaim" and may say "the author stipulated this"). The substrings
# are CERTAINTY-SCOPED (NOT the authorship substrings).
FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "overconfiden", "arrogan", "dunning_kruger", "dishonest",
    "overconfidence_score", "calibration_score", "author_verdict",
)


class CalibrationVerdictError(RuntimeError):
    """Raised when results carry a forbidden certainty-verdict key or value."""


def assert_no_verdict(results: Any, _key: str = "") -> None:  # noqa: C901
    """Recursively walk results and raise CalibrationVerdictError on any verdict.

    Explicit CERTAINTY-SCOPED rename of within_doc_segmentation.assert_no_authorship
    (NOT a reuse of the authorship keys/substrings):
      1. Any dict KEY in FORBIDDEN_RESULT_KEYS (exact, case-folded) at any depth.
      2. Any string leaf VALUE that exactly equals (case-folded) a member of
         FORBIDDEN_RESULT_KEYS — catches a verdict rendered as a value.
      3. Any dict KEY containing a FORBIDDEN_SUBSTRINGS token (case-folded
         substring, KEY-ONLY — not applied to values, so the honest
         does_not_license / rationale prose passes).
    """
    if isinstance(results, dict):
        for k, v in results.items():
            k_lower = str(k).lower()
            if k_lower in FORBIDDEN_RESULT_KEYS:
                raise CalibrationVerdictError(
                    f"Forbidden certainty-verdict key {k!r} found in results (policy_refused)"
                )
            for sub in FORBIDDEN_SUBSTRINGS:
                if sub in k_lower:
                    raise CalibrationVerdictError(
                        f"Key {k!r} contains forbidden certainty-verdict substring "
                        f"{sub!r} (policy_refused)"
                    )
            assert_no_verdict(v, str(k))
        return
    if isinstance(results, (list, tuple)):
        for item in results:
            assert_no_verdict(item, _key)
        return
    if isinstance(results, str):
        if results.lower() in FORBIDDEN_RESULT_KEYS:
            raise CalibrationVerdictError(
                f"String value {results!r} exactly matches a forbidden certainty-verdict key "
                f"(policy_refused)"
            )
        return
    # int / float / bool / None: nothing to check


# ---------- the frozen certainty lexicon (deterministic M1 substrate; P1-4) -----
# Modeled on within_doc_segmentation's BAND_VOCAB pattern (a frozen vocabulary).
# Multi-word or word-boundary-guarded — NO bare "may" (the month / proper-noun
# false-positive the spec calls out). Each entry is a regex-quoted phrase matched
# with \b word boundaries, case-insensitively, over the claim's quote span only.
HEDGE_VOCAB: frozenset[str] = frozenset({
    "may suggest", "might", "could", "perhaps", "arguably", "seems to",
    "it seems", "appears to", "it appears", "suggests", "in some cases",
    "to some extent", "somewhat", "possibly", "presumably", "plausibly",
    "it is possible", "tends to", "on the whole", "more or less",
    "i suspect", "i think", "we think", "probably", "likely",
})
BOOSTER_VOCAB: frozenset[str] = frozenset({
    "clearly", "obviously", "undeniably", "certainly", "definitely",
    "without doubt", "without question", "always", "never", "proves",
    "establishes", "demonstrates that", "of course", "indisputably",
    "unquestionably", "self-evident", "no one can deny", "it is certain",
    "must be", "cannot be denied", "every", "all",
})


def _compile_vocab(vocab: frozenset[str]) -> list[re.Pattern[str]]:
    """Compile each phrase as a word-boundary-guarded, case-insensitive pattern.
    Multi-word phrases keep their internal whitespace literal (escaped)."""
    pats: list[re.Pattern[str]] = []
    for phrase in sorted(vocab):
        pats.append(re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE))
    return pats


_HEDGE_PATS = _compile_vocab(HEDGE_VOCAB)
_BOOSTER_PATS = _compile_vocab(BOOSTER_VOCAB)


def classify_certainty(quote: str) -> str:
    """Deterministic 3-level certainty over a claim's quote span, from the frozen
    lexicon. Precedence (spec P1-4): any booster AND any hedge in the same claim
    → ``measured`` (mixed); booster only → ``assertive``; hedge only →
    ``tentative``; neither marker (a bare assertion) → ``assertive`` (an
    unhedged flat assertion expresses high certainty). The lexicon is
    AUTHORITATIVE for certainty in M1."""
    has_hedge = any(p.search(quote) for p in _HEDGE_PATS)
    has_booster = any(p.search(quote) for p in _BOOSTER_PATS)
    if has_hedge and has_booster:
        return "measured"
    if has_booster:
        return "assertive"
    if has_hedge:
        return "tentative"
    # No marker at all: a flat, unmarked assertion. Bare assertion = unhedged =
    # high expressed certainty (an unqualified "X is the case").
    return "assertive"


# ---------- alignment: the certainty × support pairing (spec §2 step 4) ---------
# assertive × thin support (none) → overclaim; tentative × strong support
# (substantiated) → underclaim; everything matched → aligned. ``measured`` is the
# balanced rung and never mismatches by itself. ``gestured`` is the middle
# support rung (no mismatch fires on it — only the extremes do).

def classify_alignment(certainty: str, support: str) -> str:
    if certainty == "assertive" and support == "none":
        return "overclaim"
    if certainty == "tentative" and support == "substantiated":
        return "underclaim"
    return "aligned"


# ---------- the legitimate-strong-claim filter (MECHANICAL, evidence-gated) -----
# Ships ONLY the two evidence-gated defenses (spec P1-5), in this fixed order:
#   1. defended_stipulated — an explicit stipulation marker in the claim's own
#      quote (str.find-validated).
#   2. defended_elsewhere — a REAL in-document supporting locus for the same
#      claim, validated text[start:end] == quote. A fabricated cross-ref FAILS
#      validation → build error (CalibrationLocusError), closing the firewall
#      hole. The judgmental defenses are M2-only and never fire in M1.

_STIPULATION_MARKERS: tuple[str, ...] = (
    "assume", "assuming", "grant that", "granting", "let us grant",
    "for the sake of argument", "take as given", "taken as given",
    "stipulate", "by stipulation", "suppose that", "posit that",
)


class CalibrationLocusError(ValueError):
    """Raised when a claimed ``defended_elsewhere`` supporting locus does NOT
    validate against the document (``text[start:end] != quote``). A fabricated
    cross-reference is a BUILD ERROR, never a silent defended_* finding — this is
    the spec's firewall-critical close (P1-5)."""


def _detect_stipulation(quote: str) -> tuple[bool, str]:
    """str.find-validated stipulation-marker scan over the claim's own quote."""
    low = quote.lower()
    for m in _STIPULATION_MARKERS:
        if low.find(m) >= 0:
            return True, f"explicit stipulation marker present ({m!r}) in the claim"
    return False, ""


def _validate_support_locus(text: str, locus: dict[str, Any]) -> str:
    """Validate a candidate supporting locus against the real document. Returns
    the validated quote on success; raises CalibrationLocusError on ANY mismatch
    (missing keys, bad offsets, or text[start:end] != quote — the fabricated
    cross-ref case). This is what makes a smuggled fake locus a build error."""
    if not isinstance(locus, dict):
        raise CalibrationLocusError(
            f"defended_elsewhere support locus must be a dict, got {type(locus).__name__}"
        )
    for k in ("start_char", "end_char", "quote"):
        if k not in locus:
            raise CalibrationLocusError(
                f"defended_elsewhere support locus missing required key {k!r}"
            )
    start, end, quote = locus["start_char"], locus["end_char"], locus["quote"]
    if (
        not isinstance(start, int) or isinstance(start, bool)
        or not isinstance(end, int) or isinstance(end, bool)
        or start < 0 or end < start or end > len(text)
    ):
        raise CalibrationLocusError(
            f"defended_elsewhere support locus has out-of-range offsets "
            f"({start!r},{end!r}) for a {len(text)}-char document"
        )
    if not isinstance(quote, str) or not quote.strip():
        raise CalibrationLocusError(
            "defended_elsewhere support locus has an empty/invalid quote"
        )
    if text[start:end] != quote:
        raise CalibrationLocusError(
            f"defended_elsewhere support locus does NOT validate: "
            f"text[{start}:{end}]={text[start:end]!r} != quote {quote!r} "
            f"(a fabricated cross-reference is a build error, not a defended finding)"
        )
    return quote


def apply_legitimate_strong_claim_filter(
    text: str,
    quote: str,
    support_loci_by_topic: dict[str, list[dict[str, Any]]],
    topic_ref: str,
    claim_start: int,
    claim_end: int,
) -> tuple[str, str]:
    """Run the two evidence-gated M1 defenses over an OVERCLAIM in fixed order;
    return ``(defense, rationale)``.

    1. ``defended_stipulated`` if a stipulation marker is in the claim's quote.
    2. else ``defended_elsewhere`` if a REAL, VALIDATED in-document supporting
       locus exists for the claim's topic_ref. EVERY candidate locus is validated
       against the document; a fabricated one raises CalibrationLocusError (build
       error). Only a locus DISJOINT from the claim's own span counts as
       "elsewhere".
    3. else ``("none", "")`` — the overclaim stands (rationale is filled by the
       caller).

    The rationale of a fired defense is ALWAYS non-empty (so the schema's
    filter-integrity check can never be hit by an under-justified defended row).

    NOTE: this filter only ever returns the two evidence-gated defenses (or
    ``none``). The judgmental ``defended_analytic`` / ``defended_common_ground``
    are reserved for a future M2 lens and are emitted by NO path today; the
    schema enumerates them but the M1 surface never produces them.
    """
    fired, evidence = _detect_stipulation(quote)
    if fired:
        return "defended_stipulated", (
            f"{evidence}; classified defended_stipulated because the claim is "
            f"flagged by the author as granted/assumed, not asserted as established"
        )

    candidates = support_loci_by_topic.get(topic_ref, [])
    for locus in candidates:
        # Validate EVERY candidate against the document — a fabricated locus is a
        # build error, never silently skipped (raises CalibrationLocusError).
        _validate_support_locus(text, locus)
        # "elsewhere" = the supporting locus's SPAN is DISJOINT from the claim's own
        # span (NOT merely different text). An overlapping or self locus is the claim's
        # own words and cannot defend an overclaim against itself. Half-open spans
        # [a,b) and [c,d) are disjoint iff b <= c or d <= a.
        locus_start, locus_end = int(locus["start_char"]), int(locus["end_char"])
        if locus_end <= claim_start or claim_end <= locus_start:
            return "defended_elsewhere", (
                f"a real in-document supporting locus for this claim's topic was found at "
                f"chars [{locus_start}:{locus_end}], DISJOINT from the claim's own span "
                f"[{claim_start}:{claim_end}], and validated (text[start:end]==quote); "
                f"classified defended_elsewhere because the support is present under a "
                f"separate, non-overlapping locus"
            )
    return "none", ""


# ---------- resolution class (firewall-safe; P1-6) ------------------------------

def _resolution_class(alignment: str, defense: str) -> str:
    """A firewall-safe CLASS of resolution — names a move, adjudicates nothing."""
    if defense == "defended_stipulated":
        return "mark_stipulation"
    if defense == "defended_elsewhere":
        return "surface_support_elsewhere"
    if alignment == "overclaim":
        return "hedge_to_match"
    # aligned / underclaim: no overclaim move named.
    return "none"


# ---------- per-claim row building ----------------------------------------------

def build_claim_rows(
    text: str,
    claims: list[cjudge.Claim],
    support_loci_by_topic: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """For each judge-extracted claim: validate its span offset-exactly against
    the document, compute deterministic certainty over its quote, take its
    judge-derived support, pair them into an alignment, run the
    legitimate-strong-claim filter on overclaims, and emit a validated row.

    A claim whose ``text[start:end] != quote`` is DROPPED (the judge's offsets
    must be exact — a fabricated CLAIM span is not trusted). A fabricated
    SUPPORTING (defended_elsewhere) locus, by contrast, raises CalibrationLocusError
    (a build error) — because a smuggled fake cross-ref is the firewall hole the
    spec demands be closed."""
    rows: list[dict[str, Any]] = []
    for c in claims:
        # Offset-exact claim-span validation (drop a non-matching claim).
        if text[c.start_char:c.end_char] != c.quote:
            continue
        certainty = classify_certainty(c.quote)
        support = c.support
        alignment = classify_alignment(certainty, support)

        defense = "none"
        rationale = ""
        if alignment == "overclaim":
            defense, rationale = apply_legitimate_strong_claim_filter(
                text, c.quote, support_loci_by_topic, c.topic_ref,
                c.start_char, c.end_char,
            )
            if is_defended(defense):
                # A defended overclaim is re-labeled: it is no longer reported as
                # a bare overclaim (the defense is shown instead).
                alignment = "aligned"
            else:
                rationale = (
                    f"assertive certainty (from the frozen lexicon over the claim's quote) "
                    f"paired with support='none' (no attached reason in the text), and no "
                    f"evidence-gated defense fired (no stipulation marker in the claim; no real "
                    f"in-document supporting locus): reported as overclaim — a certainty↔support "
                    f"mismatch, NOT a judgment of the author"
                )
        elif alignment == "underclaim":
            rationale = (
                f"tentative certainty (hedged in the claim's quote) paired with "
                f"support='substantiated' (a real attached reason in the text): reported as "
                f"underclaim — a well-supported point buried under hedges, NOT a weakness"
            )

        rows.append({
            "loci": c.loci(),
            "topic_ref": c.topic_ref,
            "certainty": certainty,
            "support": support,
            "alignment": alignment,
            "defense": defense,
            "rationale": rationale,
            "resolution_class": _resolution_class(alignment, defense),
        })
    # Deterministic order: by start offset.
    rows.sort(key=lambda r: (r["loci"]["start_char"], r["loci"]["end_char"]))
    return rows


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_alignment: dict[str, int] = {}
    by_certainty: dict[str, int] = {}
    by_support: dict[str, int] = {}
    by_defense: dict[str, int] = {}
    for r in rows:
        by_alignment[r["alignment"]] = by_alignment.get(r["alignment"], 0) + 1
        by_certainty[r["certainty"]] = by_certainty.get(r["certainty"], 0) + 1
        by_support[r["support"]] = by_support.get(r["support"], 0) + 1
        by_defense[r["defense"]] = by_defense.get(r["defense"], 0) + 1
    return {
        "n_claims": len(rows),
        "n_overclaim": by_alignment.get("overclaim", 0),
        "n_underclaim": by_alignment.get("underclaim", 0),
        "n_aligned": by_alignment.get("aligned", 0),
        "n_defended": sum(1 for r in rows if is_defended(r["defense"])),
        "by_alignment": by_alignment,
        "by_certainty": by_certainty,
        "by_support": by_support,
        "by_defense": by_defense,
    }


_DOES_NOT_LICENSE = (
    "Certainty↔support ALIGNMENT is a property of a CLAIM PAIRING, not of the "
    "author's character. High certainty is NOT arrogance, over-confidence, or "
    "Dunning-Kruger; low certainty is NOT weakness. An overclaim is NOT a finding "
    "that the author is overconfident, dishonest, sloppy, or that the argument is "
    "unsound; a defended claim is NOT an overclaim. This measures certainty↔support "
    "alignment, NOT whether a claim is TRUE, NOR the author's character. No claim "
    "is ranked; there is no top-level overconfidence score and no calibration "
    "score. The two evidence-gated defenses (defended_stipulated / "
    "defended_elsewhere) require validated textual evidence; the judgmental "
    "defenses (defended_analytic / defended_common_ground) are an M2-only LLM "
    "opinion, never a mechanical M1 verdict. heuristic / no numeric anchor: there "
    "is no measured discrimination. The surface emits no verdict."
)


def compose_results(
    text: str,
    claims: list[cjudge.Claim],
    support_loci_by_topic: dict[str, list[dict[str, Any]]],
    *,
    judge_identity: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    rows = build_claim_rows(text, claims, support_loci_by_topic)
    results: dict[str, Any] = {
        "claims": rows,
        "summary": _summary(rows),
        "does_not_license": _DOES_NOT_LICENSE,
        "calibration_status": "heuristic",
        "judge": judge_identity,
        "assumptions": {
            "method": (
                "per-document load-bearing CLAIM extraction + per-claim support (one LLM-judge "
                "pass; argument_judge labels paragraphs, NOT claims — this is a new extract_claims "
                "pass), then DETERMINISTIC certainty over each claim's quote from a frozen "
                "hedge/booster lexicon, then the certainty×support alignment, then a mechanical "
                "evidence-gated legitimate-strong-claim filter on overclaims"
            ),
            "intake": "single document; no --reference / --compare / --manifest cross-doc seam",
            "certainty_substrate": (
                "DETERMINISTIC frozen HEDGE_VOCAB/BOOSTER_VOCAB (multi-word or word-boundary-guarded; "
                "NO bare 'may'); precedence: booster+hedge -> measured, booster -> assertive, hedge -> "
                "tentative, no marker -> assertive (a bare flat assertion). The M1 lexicon is "
                "AUTHORITATIVE for certainty; an M2 judge refinement never silently overrides it"
            ),
            "support_source": "judge-derived per-claim support {none, gestured, substantiated}",
            "alignment_rule": (
                "assertive x none -> overclaim; tentative x substantiated -> underclaim; matched -> "
                "aligned. underclaim ships in M1 (symmetric, cheap)"
            ),
            "legitimate_strong_claim_filter": (
                "M1 ships ONLY the two EVIDENCE-GATED defenses, in order: defended_stipulated (an "
                "explicit stipulation marker in the claim's quote, str.find-validated) then "
                "defended_elsewhere (a REAL in-document supporting locus for the claim, validated "
                "text[start:end]==quote — a fabricated cross-ref is a build error). The judgmental "
                "defenses (defended_analytic / defended_common_ground) are M2-ONLY and NEVER fire in M1"
            ),
            "firewall": (
                "FORBIDDEN_RESULT_KEYS + FORBIDDEN_SUBSTRINGS + a recursive assert_no_verdict() guard "
                "(raises CalibrationVerdictError) called immediately before build_output; a "
                "CERTAINTY-SCOPED rename of within_doc_segmentation.assert_no_authorship (NOT a reuse "
                "of the authorship keys/substrings); mechanical, not rhetorical"
            ),
            "boundary_vs_stance_modality_audit": (
                "stance_modality_audit ships a DOCUMENT-LEVEL hedge/booster/evidential distribution "
                "(how much hedging the prose carries overall, with per-category densities and a "
                "document band); THIS surface is PER-CLAIM certainty x PER-CLAIM support -> the "
                "overclaim PAIRING — a claim-localized certainty↔support mismatch the document-level "
                "distribution cannot produce. The two are complementary, not substitutes"
            ),
            "calibration_status": "heuristic",
            "confounds": (
                "claim extraction + support level are judge-assigned; a mis-extracted claim or a "
                "mis-rated support produces a spurious mismatch or misses a real one. The mock judge "
                "is a deterministic CI scaffold (marker-driven), NOT a real extraction; M2 (anthropic) "
                "is the real extraction lens. The certainty lexicon is explicit-marker based: "
                "idiomatic / ironic / contextual certainty is not caught. A bare assertion is read as "
                "assertive by convention — a genre that asserts flatly by norm (analytic / definitional "
                "claims) is the M2-only defended_analytic case, not an M1 verdict. A defended_elsewhere "
                "locus is validated for IN-DOCUMENT EXISTENCE + topic_ref match (text[start:end]==quote), "
                "NOT topical RELEVANCE: a real but topically-unrelated locus filed under the claim's "
                "topic_ref will defend it — relevance is the judge's / operator's responsibility"
            ),
            "posture": "descriptive / no-verdict / anti-Goodhart",
        },
    }
    if warnings:
        results["assumptions"]["judge_warnings"] = warnings
    return results


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "A descriptive per-claim certainty-calibration profile of ONE argument-shaped "
            "document: for each load-bearing claim, the verbatim locus (character span + exact "
            "quote), the DETERMINISTIC expressed certainty (tentative / measured / assertive) from "
            "the frozen hedge/booster lexicon over the claim's quote, the judge-derived evidential "
            "support (none / gestured / substantiated), the certainty x support alignment (aligned / "
            "overclaim / underclaim), and — on a flagged overclaim — an evidence-gated "
            "legitimate-strong-claim defense (defended_stipulated / defended_elsewhere) with a "
            "firewall-safe class of resolution. The per-claim table IS the read."
        ),
        "does_not_license": _DOES_NOT_LICENSE,
    }


# ---------- intake (single document) + envelope (the firewall call-site) --------

def _read_support_loci_manifest(path: str | None) -> dict[str, list[dict[str, Any]]]:
    """Optional sidecar: candidate ``defended_elsewhere`` supporting loci keyed by
    topic_ref: ``{"support_loci": {"<topic>": [{start_char,end_char,quote}, ...]}}``.
    The surface VALIDATES every candidate against the document (a fabricated one
    is a build error). Absent / empty → no defended_elsewhere candidates."""
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    loci = data.get("support_loci") if isinstance(data, dict) else None
    if not isinstance(loci, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for topic, entries in loci.items():
        if isinstance(entries, list):
            out[str(topic)] = [e for e in entries if isinstance(e, dict)]
    return out


def compose_envelope(
    target_path: Path,
    *,
    judge_kind: str = "mock",
    judge_model: str | None = None,
    manifest_path: str | None = None,
    support_loci_path: str | None = None,
    length_floor_words: int = DEFAULT_LENGTH_FLOOR_WORDS,
) -> dict[str, Any]:
    """Run the surface and build the output envelope. Calls validate_results +
    assert_no_verdict IMMEDIATELY before build_output (the firewall call-site).
    A fabricated defended_elsewhere locus raises CalibrationLocusError before
    build_output (a build error), which main() routes to internal_error."""
    try:
        text = target_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as exc:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), reason=f"cannot read target document: {exc}",
            reason_category="bad_input",
        )

    target_words = len(_WORD_RE.findall(text))
    if target_words < length_floor_words:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), target_words=target_words,
            reason=f"target document has {target_words} word(s) (< floor {length_floor_words})",
            reason_category="text_too_short",
        )

    try:
        support_loci_by_topic = _read_support_loci_manifest(support_loci_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), target_words=target_words,
            reason=f"cannot read --support-loci: {exc}", reason_category="bad_input",
        )

    try:
        judge = cjudge.build_judge(judge_kind, manifest_path=manifest_path, model=judge_model)
    except cjudge.JudgeError as exc:
        cat = "missing_dependency" if judge_kind not in ("mock", "manifest") else "bad_input"
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), target_words=target_words,
            reason=str(exc), reason_category=cat,
        )

    try:
        jr = judge(text)
    except cjudge.JudgeError as exc:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), target_words=target_words,
            reason=f"judge failure: {exc}", reason_category="internal_error",
        )

    results = compose_results(
        text, jr.claims, support_loci_by_topic,
        judge_identity=jr.judge_identity, warnings=jr.warnings,
    )

    # Schema validation: closed-key / required-field contract on the M1 path. An
    # overclaim / defended_* row with an empty rationale, an M2-only defense on
    # M1, or an out-of-whitelist enum is a build error here.
    validate_results(results, m1=(judge_kind in ("mock", "manifest")))

    # Layer-1 firewall: assert_no_verdict IMMEDIATELY before build_output. Any
    # certainty-verdict key or value raises CalibrationVerdictError -> main()
    # routes to policy_refused.
    assert_no_verdict(results)

    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=str(target_path), target_words=target_words,
        baseline=None,  # single input; the document's own claims are the read
        results=results,
        claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        available=True,
        validate_bounds=True,
    )


# ---------- CLI -----------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("target", nargs="?", help="Path to the argument-shaped document (.txt/.md).")
    ap.add_argument("--target", dest="target_flag", help="Path to the target (alternative to positional).")
    ap.add_argument("--judge", default="mock",
                    choices=["mock", "manifest", "anthropic", "openai", "gemini", "agent_host"],
                    help="Judge backend: mock (M1, deterministic CI) | manifest | anthropic (M2).")
    ap.add_argument("--judge-model", help="Model id for an API judge (required for anthropic/openai/gemini).")
    ap.add_argument("--judge-manifest", help="Path to a claim manifest (for --judge manifest).")
    ap.add_argument("--support-loci",
                    help="Optional JSON sidecar of candidate defended_elsewhere supporting loci "
                         "keyed by topic_ref (validated against the document; a fabricated locus is "
                         "a build error).")
    ap.add_argument("--length-floor-words", type=int, default=DEFAULT_LENGTH_FLOOR_WORDS,
                    help=f"Minimum word count for the target (default {DEFAULT_LENGTH_FLOOR_WORDS}).")
    ap.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    ap.add_argument("--out", help="Write JSON output to FILE instead of stdout.")
    args = ap.parse_args(argv)

    target = args.target_flag or args.target
    if not target:
        env = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            reason="a target document path is required", reason_category="bad_input",
        )
        _emit(env, args)
        return 1

    try:
        env = compose_envelope(
            Path(target),
            judge_kind=args.judge, judge_model=args.judge_model,
            manifest_path=args.judge_manifest, support_loci_path=args.support_loci,
            length_floor_words=args.length_floor_words,
        )
    except CalibrationVerdictError as exc:
        env = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=target, reason=str(exc), reason_category="policy_refused",
        )
        _emit(env, args)
        return 3
    except CalibrationLocusError as exc:
        env = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=target,
            reason=f"defended_elsewhere locus validation failed: {exc}",
            reason_category="internal_error",
        )
        _emit(env, args)
        return 1
    except SchemaError as exc:
        env = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=target, reason=f"schema contract violation: {exc}",
            reason_category="internal_error",
        )
        _emit(env, args)
        return 1

    rc = 0 if env.get("available") else 1
    _emit(env, args)
    return rc


def _emit(env: dict[str, Any], args: argparse.Namespace) -> None:
    out = json.dumps(env, indent=2, ensure_ascii=False)
    if getattr(args, "out", None):
        Path(args.out).write_text(out + "\n", encoding="utf-8")
    else:
        sys.stdout.write(out + "\n")


if __name__ == "__main__":
    sys.exit(main())
