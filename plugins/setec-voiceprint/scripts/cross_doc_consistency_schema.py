#!/usr/bin/env python3
"""cross_doc_consistency_schema.py — the Python output schema (closed keys /
required fields / enums) for the ``cross_doc_argument_consistency`` surface.

Voiceprint schemas are **Python** (closed vocabularies + a required-field
contract validated at build time), not ``.schema.json`` files — modeled on
``argument_feature_schema.py`` (frozen taxonomies + an import-time self-check)
and ``output_schema.py`` (a recursive structural validator that *raises* on a
contract breach rather than silently coercing).

The schema this module pins is the per-tension *ledger row* and its closed
enums, plus a structural validator (``validate_tension_row`` /
``validate_results``) that the surface calls so a malformed row — most
importantly a ``defended_*`` row with an empty ``rationale`` — is a BUILD ERROR,
never a silent finding. Filter-integrity is therefore mechanical (the spec's
"an unfiltered/under-justified tension is a build error, not a finding").

The schema deliberately carries NO top-level score and NO author-keyed field:
``severity`` is a whitelist-enforced *descriptive ordinal* (how load-bearing the
tension is to each text's spine), and ``does_not_license`` forbids ranking the
author on it. The verdict firewall itself lives in
``cross_doc_argument_consistency.py`` (``FORBIDDEN_RESULT_KEYS`` +
``assert_no_verdict``); this module owns the positive structural contract.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "RELATION_OPTIONS",
    "RELATION_DESCRIPTIONS",
    "LEGITIMATE_VARIATION_OPTIONS",
    "DEFENSE_ORDER",
    "DEFENSE_EVIDENCE",
    "SEVERITY_OPTIONS",
    "COMMITMENT_TYPE_OPTIONS",
    "TENSION_RELATIONS",
    "SchemaError",
    "validate_tension_row",
    "validate_results",
    "is_defended",
]


class SchemaError(ValueError):
    """Raised when a results payload violates the closed-key / required-field
    contract (e.g. an unknown relation, an out-of-whitelist severity, or a
    ``defended_*`` row whose ``rationale`` is empty). A ``ValueError`` subclass
    so the surface's ``main()`` can route it to a clean ``bad_input`` /
    ``internal_error`` envelope rather than a raw traceback."""


# ---- the relation taxonomy (closed; spec §2 step 3) ------------------------
# How an ALIGNED pair of commitments (same topic_ref) relates. Descriptive only.
RELATION_OPTIONS: tuple[str, ...] = (
    "consistent",       # the two commitments cohere
    "tension",          # they pull against each other (the read)
    "direct_conflict",  # they cannot both hold as stated
    "incomparable",     # aligned by topic but not actually about the same claim
)

RELATION_DESCRIPTIONS: dict[str, str] = {
    "consistent": "The two commitments cohere; no tension is reported.",
    "tension": "The commitments pull against each other without flatly contradicting.",
    "direct_conflict": "As stated, the two commitments cannot both hold.",
    "incomparable": "Aligned by topic but not actually the same claim; not a tension.",
}

# Relations that DO surface as a tension row (consistent/incomparable do not).
TENSION_RELATIONS: frozenset[str] = frozenset({"tension", "direct_conflict"})


# ---- the legitimate-variation taxonomy (closed; spec §1 / §3) --------------
# ``genuine`` = survived the filter; the five ``defended_*`` values name the
# defense that fired. ``DEFENSE_ORDER`` is the AUTHORITATIVE precedence: the
# first defense whose REQUIRED textual evidence is present wins (spec P1 #3:
# retraction -> time -> scope -> audience -> genre). None firing -> ``genuine``.
DEFENSE_ORDER: tuple[str, ...] = (
    "retraction",
    "time",
    "scope",
    "audience",
    "genre",
)

# What textual evidence each defense REQUIRES to fire. The surface's decision
# tree consults this map; a defense never fires on absent evidence (so a
# defended_* row always has a non-empty rationale, enforced below).
DEFENSE_EVIDENCE: dict[str, str] = {
    "retraction": (
        "an explicit retraction/correction marker on at least one locus "
        "(e.g. 'I no longer hold', 'I was wrong', 'I retract', 'on reflection "
        "I now think', 'correction:')"
    ),
    "time": (
        "a dated or temporal marker on at least one locus, or an explicit "
        "appeal to later/earlier evidence the author could not have had before "
        "(the hindsight rule: 'in 2019', 'at the time', 'since then', 'we now know')"
    ),
    "scope": (
        "an explicit domain/qualifier divergence between the aligned commitments "
        "(one narrowed to a case/condition the other does not share: 'in the case "
        "of', 'for X specifically', 'except when', 'limited to')"
    ),
    "audience": (
        "an explicit addressee/forum divergence between the loci (a concession "
        "made to one reader and withheld from another: 'for specialists', 'to "
        "the general reader', 'as I told the committee')"
    ),
    "genre": (
        "an explicit register/form divergence between the loci (an op-ed's "
        "compression vs a brief's hedging: a stated 'in this op-ed' / 'in the "
        "formal brief' / 'speaking loosely' / 'strictly speaking' contrast)"
    ),
}

LEGITIMATE_VARIATION_OPTIONS: tuple[str, ...] = ("genuine",) + tuple(
    f"defended_{d}" for d in DEFENSE_ORDER
)


def is_defended(legitimate_variation: str) -> bool:
    """True iff the value names a fired defense (``defended_*``), not ``genuine``."""
    return legitimate_variation.startswith("defended_")


# ---- the severity ordinal (closed whitelist; spec §3 / P2) -----------------
# A DESCRIPTIVE ordinal: how load-bearing the tension is to each text's spine.
# NOT a judgment of the author and NOT a rank to sort on (does_not_license
# forbids ranking). Whitelist-enforced so a free-text severity is a build error.
SEVERITY_OPTIONS: tuple[str, ...] = ("Salient", "Notable", "Minor")


# ---- the commitment-type taxonomy (closed; spec §2 step 1) -----------------
# What kind of load-bearing node a commitment is. Carried on each commitment
# the judge extracts; descriptive, never a quality grade.
COMMITMENT_TYPE_OPTIONS: tuple[str, ...] = (
    "claim",
    "warrant",
    "scope_condition",
    "value_premise",
    "empirical_premise",
)


# ---- required-field contracts ----------------------------------------------
# A tension ROW (closed keys). loci is a 2-list of locus dicts.
_LOCUS_REQUIRED: tuple[str, ...] = ("doc", "start_char", "end_char", "quote")
_ROW_REQUIRED: tuple[str, ...] = (
    "loci",
    "topic_ref",
    "relation",
    "legitimate_variation",
    "rationale",
    "severity",
    "resolution_class",
)


def _validate_locus(locus: Any, *, where: str) -> None:
    if not isinstance(locus, dict):
        raise SchemaError(f"{where}: each locus must be a dict, got {type(locus).__name__}")
    missing = [k for k in _LOCUS_REQUIRED if k not in locus]
    if missing:
        raise SchemaError(f"{where}: locus missing required key(s) {missing}")
    if not isinstance(locus["doc"], str) or not locus["doc"]:
        raise SchemaError(f"{where}: locus 'doc' must be a non-empty string")
    for off in ("start_char", "end_char"):
        v = locus[off]
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise SchemaError(f"{where}: locus {off!r} must be a non-negative int, got {v!r}")
    if locus["end_char"] < locus["start_char"]:
        raise SchemaError(
            f"{where}: locus end_char {locus['end_char']} < start_char {locus['start_char']}"
        )
    if not isinstance(locus["quote"], str) or not locus["quote"].strip():
        raise SchemaError(f"{where}: locus 'quote' must be a non-empty verbatim string")


def validate_tension_row(row: Any, *, where: str = "tension") -> None:
    """Raise ``SchemaError`` unless ``row`` is a well-formed tension ledger row.

    The load-bearing checks (the spec's mechanical filter-integrity):
      * closed keys: exactly ``_ROW_REQUIRED`` are required.
      * ``relation`` in ``RELATION_OPTIONS`` and ∈ ``TENSION_RELATIONS`` (a
        ``consistent``/``incomparable`` row is not a tension and must not be in
        the ledger).
      * ``legitimate_variation`` in ``LEGITIMATE_VARIATION_OPTIONS``.
      * ``severity`` in the ``SEVERITY_OPTIONS`` whitelist.
      * **a ``defended_*`` row MUST carry a non-empty ``rationale``** — an empty
        rationale on a fired defense is a BUILD ERROR, never a silent pass
        (filter-integrity is mechanical, spec P1 #2). A ``genuine`` row also
        requires a non-empty rationale (the tension still has to be explained).
      * ``loci`` is exactly two well-formed locus dicts.
    """
    if not isinstance(row, dict):
        raise SchemaError(f"{where}: row must be a dict, got {type(row).__name__}")
    missing = [k for k in _ROW_REQUIRED if k not in row]
    if missing:
        raise SchemaError(f"{where}: row missing required key(s) {missing}")

    relation = row["relation"]
    if relation not in RELATION_OPTIONS:
        raise SchemaError(
            f"{where}: relation {relation!r} not in {list(RELATION_OPTIONS)}"
        )
    if relation not in TENSION_RELATIONS:
        raise SchemaError(
            f"{where}: relation {relation!r} is not a tension relation; only "
            f"{sorted(TENSION_RELATIONS)} rows belong in the ledger"
        )

    lv = row["legitimate_variation"]
    if lv not in LEGITIMATE_VARIATION_OPTIONS:
        raise SchemaError(
            f"{where}: legitimate_variation {lv!r} not in "
            f"{list(LEGITIMATE_VARIATION_OPTIONS)}"
        )

    severity = row["severity"]
    if severity not in SEVERITY_OPTIONS:
        raise SchemaError(
            f"{where}: severity {severity!r} not in the whitelist {list(SEVERITY_OPTIONS)}"
        )

    rationale = row["rationale"]
    if not isinstance(rationale, str) or not rationale.strip():
        # The mechanical filter-integrity check: a defended_* (or genuine) row
        # with no rationale is a build error, not a silent finding.
        kind = "defended" if is_defended(lv) else "genuine"
        raise SchemaError(
            f"{where}: a {kind} tension (legitimate_variation={lv!r}) has an "
            f"empty rationale; an unjustified tension is a build error, not a finding"
        )

    rc = row["resolution_class"]
    if not isinstance(rc, str) or not rc.strip():
        raise SchemaError(f"{where}: resolution_class must be a non-empty string")

    loci = row["loci"]
    if not isinstance(loci, list) or len(loci) != 2:
        raise SchemaError(f"{where}: loci must be a list of exactly two locus dicts")
    for i, locus in enumerate(loci):
        _validate_locus(locus, where=f"{where}.loci[{i}]")


def validate_results(results: dict[str, Any]) -> None:
    """Structurally validate the surface's ``results`` payload (closed contract).

    Requires a ``tensions`` list (each validated by ``validate_tension_row``), a
    ``summary`` dict, an ``assumptions`` dict, and a ``does_not_license`` string.
    Raises ``SchemaError`` on the first breach. Called by the surface BEFORE the
    verdict firewall + ``build_output``.
    """
    if not isinstance(results, dict):
        raise SchemaError(f"results must be a dict, got {type(results).__name__}")
    for key in ("tensions", "summary", "assumptions", "does_not_license"):
        if key not in results:
            raise SchemaError(f"results missing required key {key!r}")
    if not isinstance(results["tensions"], list):
        raise SchemaError("results['tensions'] must be a list")
    for i, row in enumerate(results["tensions"]):
        validate_tension_row(row, where=f"tensions[{i}]")
    if not isinstance(results["summary"], dict):
        raise SchemaError("results['summary'] must be a dict")
    if not isinstance(results["assumptions"], dict):
        raise SchemaError("results['assumptions'] must be a dict")
    if not isinstance(results["does_not_license"], str) or not results["does_not_license"].strip():
        raise SchemaError("results['does_not_license'] must be a non-empty string")


# ---- import-time self-check (catch taxonomy mistakes early) ----------------
def _self_check() -> None:
    if set(RELATION_DESCRIPTIONS) != set(RELATION_OPTIONS):
        raise RuntimeError("RELATION_DESCRIPTIONS must cover exactly RELATION_OPTIONS")
    if not TENSION_RELATIONS.issubset(set(RELATION_OPTIONS)):
        raise RuntimeError("TENSION_RELATIONS must be a subset of RELATION_OPTIONS")
    if set(DEFENSE_EVIDENCE) != set(DEFENSE_ORDER):
        raise RuntimeError("DEFENSE_EVIDENCE must cover exactly DEFENSE_ORDER")
    if LEGITIMATE_VARIATION_OPTIONS[0] != "genuine":
        raise RuntimeError("LEGITIMATE_VARIATION_OPTIONS must start with 'genuine'")
    # The defended_* values must be exactly DEFENSE_ORDER, in order.
    expect = ("genuine",) + tuple(f"defended_{d}" for d in DEFENSE_ORDER)
    if LEGITIMATE_VARIATION_OPTIONS != expect:
        raise RuntimeError("LEGITIMATE_VARIATION_OPTIONS drifted from DEFENSE_ORDER")
    if len(set(SEVERITY_OPTIONS)) != len(SEVERITY_OPTIONS):
        raise RuntimeError("SEVERITY_OPTIONS contains duplicates")


_self_check()
