#!/usr/bin/env python3
"""argument_certainty_calibration_schema.py — the Python output schema (closed
keys / required fields / enums) for the ``argument_certainty_calibration``
surface.

Voiceprint schemas are **Python** (closed vocabularies + a required-field
contract validated at build time), not ``.schema.json`` files — modeled on
``cross_doc_consistency_schema.py`` (frozen taxonomies + an import-time
self-check) and ``output_schema.py`` (a recursive structural validator that
*raises* on a contract breach rather than silently coercing).

The schema pins the per-claim *calibration row* and its closed enums, plus a
structural validator (``validate_claim_row`` / ``validate_results``) that the
surface calls so a malformed row — most importantly an ``overclaim`` or a
``defended_*`` row with an empty ``rationale`` — is a BUILD ERROR, never a
silent finding. Filter-integrity is therefore mechanical (the spec's "an
overclaim or defended_* with an empty rationale is a build error, not a
finding").

The schema deliberately carries **NO top-level score** and NO author-keyed
field: the per-claim ``certainty × support → alignment`` table IS the read.
``does_not_license`` forbids reading high certainty as arrogance or low
certainty as weakness. The no-verdict firewall itself lives in
``argument_certainty_calibration.py`` (``FORBIDDEN_RESULT_KEYS`` +
``assert_no_verdict``); this module owns the positive structural contract.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "CERTAINTY_OPTIONS",
    "SUPPORT_OPTIONS",
    "ALIGNMENT_OPTIONS",
    "DEFENSE_OPTIONS",
    "M1_DEFENSE_OPTIONS",
    "RESOLUTION_CLASS_OPTIONS",
    "SchemaError",
    "validate_claim_row",
    "validate_results",
    "is_defended",
]


class SchemaError(ValueError):
    """Raised when a results payload violates the closed-key / required-field
    contract (e.g. an unknown certainty/support/alignment/defense value, or an
    ``overclaim``/``defended_*`` row whose ``rationale`` is empty). A
    ``ValueError`` subclass so the surface's ``main()`` can route it to a clean
    ``bad_input`` / ``internal_error`` envelope rather than a raw traceback."""


# ---- the certainty ordinal (closed; spec §2 step 2 / P1-4) -----------------
# The EXPRESSED certainty of a claim, computed deterministically over the
# claim's quote span from the frozen hedge/booster lexicon. Three ordinal rungs.
# This is the deterministic M1 substrate (auditable); M2 refines but never
# silently overrides (the M1 lexicon is AUTHORITATIVE for certainty).
CERTAINTY_OPTIONS: tuple[str, ...] = ("tentative", "measured", "assertive")


# ---- the support ordinal (closed; spec §2 step 3 / P1-3) -------------------
# The evidential support a claim carries IN THE TEXT — judge-derived (folded
# into the same extract_claims pass). Does the claim carry an attached reason?
SUPPORT_OPTIONS: tuple[str, ...] = ("none", "gestured", "substantiated")


# ---- the alignment taxonomy (closed; spec §2 step 4) -----------------------
# The certainty × support PAIRING — the read. assertive × thin support →
# overclaim; tentative × strong support → underclaim; matched → aligned.
ALIGNMENT_OPTIONS: tuple[str, ...] = ("aligned", "overclaim", "underclaim")


# ---- the defense taxonomy (closed; spec §1 / P1-5) -------------------------
# ``none`` = no legitimate-strong-claim defense fired. The two EVIDENCE-GATED
# defenses are the ONLY ones that may fire in M1 (each requires real, validated
# textual evidence). The two JUDGMENTAL defenses (defended_analytic /
# defended_common_ground) are M2-ONLY — reported as "LLM opinion, not a
# mechanical M1 verdict" and NEVER produced by M1.
DEFENSE_OPTIONS: tuple[str, ...] = (
    "none",
    "defended_stipulated",      # M1: explicit stipulation marker (str.find-validated)
    "defended_elsewhere",       # M1: a REAL in-document supporting locus (text[start:end]==quote)
    "defended_analytic",        # M2-ONLY (judgmental)
    "defended_common_ground",   # M2-ONLY (judgmental)
)

# The defenses that may fire in M1. The surface MUST NOT emit an M2-only defense
# on the mock/M1 path; the schema enforces it (an M2-only defense on an M1
# envelope is a build error).
M1_DEFENSE_OPTIONS: frozenset[str] = frozenset(
    {"none", "defended_stipulated", "defended_elsewhere"}
)


def is_defended(defense: str) -> bool:
    """True iff the value names a fired defense (``defended_*``), not ``none``."""
    return defense.startswith("defended_")


# ---- the resolution-class taxonomy (closed; P1-6) --------------------------
# A firewall-safe CLASS of resolution — names a move, adjudicates nothing.
# ``none`` for an aligned claim (nothing to resolve).
RESOLUTION_CLASS_OPTIONS: tuple[str, ...] = (
    "hedge_to_match",            # overclaim: hedge the claim to match the evidence
    "surface_support_elsewhere",  # overclaim defended_elsewhere: surface the support that exists
    "mark_stipulation",          # overclaim defended_stipulated: mark the stipulation explicitly
    "none",                      # aligned / underclaim (no overclaim move named)
)


# ---- required-field contracts ----------------------------------------------
# A claim ROW (closed keys). loci is a single locus dict (one claim, one span).
_LOCI_REQUIRED: tuple[str, ...] = ("start_char", "end_char", "quote")
_ROW_REQUIRED: tuple[str, ...] = (
    "loci",
    "certainty",
    "support",
    "alignment",
    "defense",
    "rationale",
    "resolution_class",
)


def _validate_loci(loci: Any, *, where: str) -> None:
    if not isinstance(loci, dict):
        raise SchemaError(f"{where}: loci must be a dict, got {type(loci).__name__}")
    missing = [k for k in _LOCI_REQUIRED if k not in loci]
    if missing:
        raise SchemaError(f"{where}: loci missing required key(s) {missing}")
    for off in ("start_char", "end_char"):
        v = loci[off]
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise SchemaError(f"{where}: loci {off!r} must be a non-negative int, got {v!r}")
    if loci["end_char"] < loci["start_char"]:
        raise SchemaError(
            f"{where}: loci end_char {loci['end_char']} < start_char {loci['start_char']}"
        )
    if not isinstance(loci["quote"], str) or not loci["quote"].strip():
        raise SchemaError(f"{where}: loci 'quote' must be a non-empty verbatim string")


def validate_claim_row(row: Any, *, m1: bool = True, where: str = "claim") -> None:
    """Raise ``SchemaError`` unless ``row`` is a well-formed calibration row.

    The load-bearing checks (the spec's mechanical filter-integrity):
      * closed keys: exactly ``_ROW_REQUIRED`` are required.
      * ``certainty`` ∈ ``CERTAINTY_OPTIONS``, ``support`` ∈ ``SUPPORT_OPTIONS``,
        ``alignment`` ∈ ``ALIGNMENT_OPTIONS``, ``defense`` ∈ ``DEFENSE_OPTIONS``,
        ``resolution_class`` ∈ ``RESOLUTION_CLASS_OPTIONS`` — an out-of-whitelist
        enum is a build error (never silently coerced).
      * on the M1 path (``m1=True``), ``defense`` must be in
        ``M1_DEFENSE_OPTIONS`` — an M2-only judgmental defense
        (``defended_analytic`` / ``defended_common_ground``) reaching an M1
        envelope is a build error (the judgmental defenses NEVER fire in M1).
      * **an ``overclaim`` row, or ANY ``defended_*`` row, MUST carry a
        non-empty ``rationale``** — an empty rationale on a flagged overclaim or
        a fired defense is a BUILD ERROR, never a silent pass (filter-integrity
        is mechanical, spec §4 / P1-5).
      * ``loci`` is one well-formed locus dict.
    """
    if not isinstance(row, dict):
        raise SchemaError(f"{where}: row must be a dict, got {type(row).__name__}")
    missing = [k for k in _ROW_REQUIRED if k not in row]
    if missing:
        raise SchemaError(f"{where}: row missing required key(s) {missing}")

    certainty = row["certainty"]
    if certainty not in CERTAINTY_OPTIONS:
        raise SchemaError(f"{where}: certainty {certainty!r} not in {list(CERTAINTY_OPTIONS)}")

    support = row["support"]
    if support not in SUPPORT_OPTIONS:
        raise SchemaError(f"{where}: support {support!r} not in {list(SUPPORT_OPTIONS)}")

    alignment = row["alignment"]
    if alignment not in ALIGNMENT_OPTIONS:
        raise SchemaError(f"{where}: alignment {alignment!r} not in {list(ALIGNMENT_OPTIONS)}")

    defense = row["defense"]
    if defense not in DEFENSE_OPTIONS:
        raise SchemaError(f"{where}: defense {defense!r} not in {list(DEFENSE_OPTIONS)}")
    if m1 and defense not in M1_DEFENSE_OPTIONS:
        raise SchemaError(
            f"{where}: defense {defense!r} is an M2-only judgmental defense that must NOT "
            f"fire on the M1 path; only {sorted(M1_DEFENSE_OPTIONS)} are mechanical M1 defenses"
        )

    rc = row["resolution_class"]
    if rc not in RESOLUTION_CLASS_OPTIONS:
        raise SchemaError(
            f"{where}: resolution_class {rc!r} not in {list(RESOLUTION_CLASS_OPTIONS)}"
        )

    rationale = row["rationale"]
    rationale_required = alignment == "overclaim" or is_defended(defense)
    if rationale_required and (not isinstance(rationale, str) or not rationale.strip()):
        kind = "defended" if is_defended(defense) else "overclaim"
        raise SchemaError(
            f"{where}: a {kind} claim (alignment={alignment!r}, defense={defense!r}) has an "
            f"empty rationale; an unjustified overclaim/defense is a build error, not a finding"
        )
    if not isinstance(rationale, str):
        raise SchemaError(f"{where}: rationale must be a string, got {type(rationale).__name__}")

    _validate_loci(row["loci"], where=f"{where}.loci")


def validate_results(results: dict[str, Any], *, m1: bool = True) -> None:
    """Structurally validate the surface's ``results`` payload (closed contract).

    Requires a ``claims`` list (each validated by ``validate_claim_row``), a
    ``summary`` dict, an ``assumptions`` dict, and a ``does_not_license``
    string. Raises ``SchemaError`` on the first breach. Called by the surface
    BEFORE the no-verdict firewall + ``build_output``.
    """
    if not isinstance(results, dict):
        raise SchemaError(f"results must be a dict, got {type(results).__name__}")
    for key in ("claims", "summary", "assumptions", "does_not_license"):
        if key not in results:
            raise SchemaError(f"results missing required key {key!r}")
    if not isinstance(results["claims"], list):
        raise SchemaError("results['claims'] must be a list")
    for i, row in enumerate(results["claims"]):
        validate_claim_row(row, m1=m1, where=f"claims[{i}]")
    if not isinstance(results["summary"], dict):
        raise SchemaError("results['summary'] must be a dict")
    if not isinstance(results["assumptions"], dict):
        raise SchemaError("results['assumptions'] must be a dict")
    if not isinstance(results["does_not_license"], str) or not results["does_not_license"].strip():
        raise SchemaError("results['does_not_license'] must be a non-empty string")


# ---- import-time self-check (catch taxonomy mistakes early) ----------------
def _self_check() -> None:
    if M1_DEFENSE_OPTIONS.issubset(set(DEFENSE_OPTIONS)) is False:
        raise RuntimeError("M1_DEFENSE_OPTIONS must be a subset of DEFENSE_OPTIONS")
    if "none" not in DEFENSE_OPTIONS or DEFENSE_OPTIONS[0] != "none":
        raise RuntimeError("DEFENSE_OPTIONS must start with 'none'")
    # The two evidence-gated M1 defenses must be exactly the non-none members of M1.
    if M1_DEFENSE_OPTIONS != frozenset({"none", "defended_stipulated", "defended_elsewhere"}):
        raise RuntimeError("M1_DEFENSE_OPTIONS drifted from the two evidence-gated defenses")
    for tup in (CERTAINTY_OPTIONS, SUPPORT_OPTIONS, ALIGNMENT_OPTIONS,
                DEFENSE_OPTIONS, RESOLUTION_CLASS_OPTIONS):
        if len(set(tup)) != len(tup):
            raise RuntimeError(f"a taxonomy tuple contains duplicates: {tup}")


_self_check()
