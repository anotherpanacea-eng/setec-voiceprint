#!/usr/bin/env python3
"""cross_doc_argument_consistency.py — cross-document argument-consistency map (M1, judge-based).

Given an author's corpus of argument-shaped pieces (a FOCAL document vs the rest
of a supplied pool), produce a **descriptive map of where the corpus's load-bearing
commitments are in tension** across documents: a claim asserted in A that a claim
in B undercuts, a scope condition honored in A but dropped in B, a value premise
that flips. The tensions ARE the read — never a single "consistency score", never
a verdict about the author.

The argument-CONTENT sibling of ``cross_doc_novelty_profile`` (which is the
stylometric sibling). Lineage: the nonfiction-argument analogue of Series
Continuity / world-bible self-consistency — "is this body of work internally
coherent?" applied to an argument corpus, descriptively, with a MECHANICAL
no-verdict firewall.

The firewall is the entire defensibility of this capability and it is MECHANICAL,
not rhetorical:
  * ``FORBIDDEN_RESULT_KEYS`` frozenset + ``FORBIDDEN_SUBSTRINGS`` tuple + a
    recursive ``assert_no_verdict()`` (clone of within_doc_segmentation's
    ``assert_no_authorship``) that raises ``ConsistencyVerdictError`` if the
    artifact ever carries a hypocrisy / dishonesty / bad-faith / self-contradiction
    / who-is-right / author-verdict / winning-document / consistency-score /
    author-score key (or such a value), called IMMEDIATELY before build_output;
    ``main()`` catches it and routes to ``available:false`` / ``policy_refused``.
  * The legitimate-variation FILTER is a required, mechanical stage: every
    surface tension is run through the five defenses in a fixed precedence
    (retraction -> time -> scope -> audience -> genre); the first defense whose
    REQUIRED textual evidence is present in the loci fires -> ``defended_<that>``;
    none -> ``genuine``. Defended tensions APPEAR in the ledger, marked
    ``defended_*`` (showing them is more honest than hiding them).
  * Filter-integrity is mechanical: a ``defended_*`` tension whose ``rationale``
    is empty is a BUILD ERROR (schema.validate_tension_row raises), not a silent
    pass.

M1 = mock-deterministic judge (CI-safe). M2 = anthropic (lazy/fail-loud).
Ships ``calibration_status: heuristic`` — directional, no numeric anchor.
Posture: descriptive / no-verdict / anti-Goodhart.
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
import cross_doc_consistency_judge as cjudge  # noqa: E402
from cross_doc_consistency_schema import (  # noqa: E402
    DEFENSE_ORDER,
    DEFENSE_EVIDENCE,
    SEVERITY_OPTIONS,
    TENSION_RELATIONS,
    is_defended,
    validate_results,
    SchemaError,
)
from cross_doc_novelty_profile import (  # noqa: E402
    _load_reference_dir,
    _load_reference_manifest,
    _content_fingerprint,
)

TASK_SURFACE = "argument_consistency"
TOOL_NAME = "cross_doc_argument_consistency"
SCRIPT_VERSION = "1.0"

# Cap N for M1 (the spec's focal-vs-rest, N<=5). Pool docs beyond the cap are dropped.
DEFAULT_MAX_DOCS = 5
DEFAULT_LENGTH_FLOOR_WORDS = 50

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")


# ---------- Layer 1: the no-verdict firewall (clone of assert_no_authorship) ----

# Exact keys that must never appear at any depth in the results dict. These are
# the verdict / character / score keys the spec forbids: a tension is a property
# of a TEXT PAIR, never of the author's honesty, and the surface picks no winner.
FORBIDDEN_RESULT_KEYS: frozenset[str] = frozenset({
    "hypocrisy", "hypocrite", "dishonest", "dishonesty", "bad_faith",
    "contradicts_self", "self_contradiction", "who_is_right", "author_verdict",
    "winning_document", "winner", "consistency_score", "author_score",
    "honesty_score", "integrity_score", "verdict",
})

# Substring blocklist — applied to KEYS ONLY at any nesting depth (mirroring
# within_doc_segmentation: a blanket key-AND-value walk would raise on the
# surface's own honest caveat text, e.g. does_not_license naming "hypocrisy").
FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "hypocri", "dishonest", "bad_faith", "winning_doc", "author_score",
    "consistency_score", "author_verdict",
)


class ConsistencyVerdictError(RuntimeError):
    """Raised when results carry a forbidden verdict/character/score key or value."""


def assert_no_verdict(results: Any, _key: str = "") -> None:  # noqa: C901
    """Recursively walk results and raise ConsistencyVerdictError on any verdict.

    Clone of within_doc_segmentation.assert_no_authorship:
      1. Any dict KEY in FORBIDDEN_RESULT_KEYS (exact, case-folded) at any depth.
      2. Any string leaf VALUE that exactly equals (case-folded) a member of
         FORBIDDEN_RESULT_KEYS — catches a verdict rendered as a value.
      3. Any dict KEY containing a FORBIDDEN_SUBSTRINGS token (case-folded
         substring, KEY-ONLY — not applied to values, so the honest
         does_not_license / rationale prose passes).
      4. Any value under a "severity" key that is not in the SEVERITY_OPTIONS
         whitelist (an out-of-whitelist severity is a posture breach — a
         smuggled author judgment, not a silent coercion).
      5. Any value under a "relation" key that is not a tension relation
         (consistent/incomparable rows must never reach the ledger).
    """
    if isinstance(results, dict):
        for k, v in results.items():
            k_lower = str(k).lower()
            if k_lower in FORBIDDEN_RESULT_KEYS:
                raise ConsistencyVerdictError(
                    f"Forbidden verdict key {k!r} found in results (policy_refused)"
                )
            for sub in FORBIDDEN_SUBSTRINGS:
                if sub in k_lower:
                    raise ConsistencyVerdictError(
                        f"Key {k!r} contains forbidden verdict substring {sub!r} (policy_refused)"
                    )
            if k_lower == "severity" and isinstance(v, str) and v not in SEVERITY_OPTIONS:
                raise ConsistencyVerdictError(
                    f"Severity value {v!r} is not in the whitelist {SEVERITY_OPTIONS} (policy_refused)"
                )
            if k_lower == "relation" and isinstance(v, str) and v not in TENSION_RELATIONS:
                raise ConsistencyVerdictError(
                    f"Relation value {v!r} is not a ledger tension relation "
                    f"{sorted(TENSION_RELATIONS)} (policy_refused)"
                )
            assert_no_verdict(v, str(k))
        return
    if isinstance(results, (list, tuple)):
        for item in results:
            assert_no_verdict(item, _key)
        return
    if isinstance(results, str):
        if results.lower() in FORBIDDEN_RESULT_KEYS:
            raise ConsistencyVerdictError(
                f"String value {results!r} exactly matches a forbidden verdict key (policy_refused)"
            )
        return
    # int / float / bool / None: nothing to check


# ---------- legitimate-variation decision tree (MECHANICAL; spec P1 #3) --------
# Per-defense evidence detectors. Each returns (fired: bool, evidence: str). The
# detector scans the COMBINED loci text (both quotes + statements) for the
# defense's required textual marker. Order is the AUTHORITATIVE DEFENSE_ORDER:
# retraction -> time -> scope -> audience -> genre. The first detector that fires
# wins -> defended_<that>; none -> genuine.

_RETRACTION_MARKERS = (
    "i no longer", "i was wrong", "i retract", "i withdraw", "on reflection i now",
    "i've since changed", "i have since changed", "correction:", "i now believe",
    "i no longer hold", "i was mistaken", "i recant",
)
_TIME_MARKERS = (
    "since then", "at the time", "we now know", "back then", "in retrospect",
    "later evidence", "subsequent", "earlier i", "years ago", "decades ago",
    "in hindsight", "could not have known", "newly available", "as of",
)
_TIME_DATE_RE = re.compile(r"\b(?:in|by|since|after|before)\s+\d{4}\b|\b(?:19|20)\d{2}\b")
_SCOPE_MARKERS = (
    "in the case of", "except when", "limited to", "only when",
    "for x", "narrowly", "in the narrow", "with respect to", "confined to",
    "applies only", "restricted to", "in the domain of",
)
_AUDIENCE_MARKERS = (
    "for specialists", "to the general reader", "as i told", "for a lay",
    "for experts", "for the committee", "to this audience",
    "for policymakers", "speaking to", "for the public",
)
_GENRE_MARKERS = (
    "in this op-ed", "in the brief", "in the formal", "speaking loosely",
    "strictly speaking", "as an op-ed", "for the record", "in shorthand",
    "compressed", "in a brief", "in the essay", "loosely put",
)


def _detect_retraction(blob: str) -> tuple[bool, str]:
    low = blob.lower()
    for m in _RETRACTION_MARKERS:
        if m in low:
            return True, f"explicit retraction marker present ({m!r})"
    return False, ""


def _detect_time(blob: str) -> tuple[bool, str]:
    low = blob.lower()
    for m in _TIME_MARKERS:
        if m in low:
            return True, f"temporal/hindsight marker present ({m!r})"
    md = _TIME_DATE_RE.search(blob)
    if md:
        return True, f"dated temporal marker present ({md.group(0)!r})"
    return False, ""


def _detect_scope(blob: str) -> tuple[bool, str]:
    low = blob.lower()
    for m in _SCOPE_MARKERS:
        if m in low:
            return True, f"scope/domain-qualifier divergence present ({m!r})"
    return False, ""


def _detect_audience(blob: str) -> tuple[bool, str]:
    low = blob.lower()
    for m in _AUDIENCE_MARKERS:
        if m in low:
            return True, f"audience/addressee divergence present ({m!r})"
    return False, ""


def _detect_genre(blob: str) -> tuple[bool, str]:
    low = blob.lower()
    for m in _GENRE_MARKERS:
        if m in low:
            return True, f"genre/register divergence present ({m!r})"
    return False, ""


_DETECTORS = {
    "retraction": _detect_retraction,
    "time": _detect_time,
    "scope": _detect_scope,
    "audience": _detect_audience,
    "genre": _detect_genre,
}


def classify_legitimate_variation(blob: str) -> tuple[str, str]:
    """Run the defenses in DEFENSE_ORDER over ``blob``; return
    ``(legitimate_variation, rationale)``.

    The first defense whose required textual evidence is present fires ->
    ``("defended_<that>", "<evidence>")``. None fires -> ``("genuine", "<why
    genuine>")``. The rationale is ALWAYS non-empty (so the schema's
    filter-integrity check can never be hit by an under-justified defended row);
    the evidence string IS the rationale for a defended row, and a genuine row
    gets the "no legitimate-variation defense fired" rationale. DEFENSE_ORDER is
    authoritative — its iteration order is the precedence (Python tuple order)."""
    for defense in DEFENSE_ORDER:  # authoritative precedence order
        fired, evidence = _DETECTORS[defense](blob)
        if fired:
            return f"defended_{defense}", (
                f"{evidence}; classified defended_{defense} because the required "
                f"evidence is present: {DEFENSE_EVIDENCE[defense]}"
            )
    return "genuine", (
        "no legitimate-variation defense fired: none of retraction / time / scope "
        "/ audience / genre evidence is present in the aligned loci, so the tension "
        "is reported as genuine (a surface tension, not an author judgment)"
    )


# ---------- severity (descriptive ordinal; spec §3) -----------------------------
# How load-bearing the tension is to each text's spine — NOT a judgment of the
# author and NOT a rank to sort on. direct_conflict on a claim/value_premise is
# Salient; tension on a claim/value_premise is Notable; everything else Minor.
# Whitelist-enforced (the firewall raises on any value outside SEVERITY_OPTIONS).

_SPINE_TYPES = frozenset({"claim", "value_premise", "warrant"})


def _assign_severity(relation: str, ctype_a: str, ctype_b: str) -> str:
    spine = ctype_a in _SPINE_TYPES or ctype_b in _SPINE_TYPES
    if relation == "direct_conflict" and spine:
        return "Salient"
    if relation == "tension" and spine:
        return "Notable"
    return "Minor"


# ---------- resolution class (firewall-safe; spec §3) ---------------------------

def _resolution_class(relation: str, legitimate_variation: str) -> str:
    """A firewall-safe CLASS of resolution — names a move, adjudicates nothing."""
    if is_defended(legitimate_variation):
        defense = legitimate_variation[len("defended_"):]
        return {
            "retraction": "surface the retraction so the change is explicit",
            "time": "date both commitments so the evolution is legible",
            "scope": "name the scope that reconciles the two claims",
            "audience": "note the audience each commitment addresses",
            "genre": "note the register/form difference between the two pieces",
        }.get(defense, "name the variation that reconciles them")
    if relation == "direct_conflict":
        return "distinguish the two senses, or re-scope one claim, so both can hold"
    return "name the scope or sense under which the two commitments can be reconciled"


# ---------- cross-doc pairing + tension building --------------------------------

def build_tensions(
    commitments_by_doc: dict[str, list[cjudge.Commitment]],
    focal_doc: str,
) -> list[dict[str, Any]]:
    """Pair the FOCAL doc's commitments against every OTHER doc's commitments by
    shared ``topic_ref``; classify each pair's relation; keep only tension /
    direct_conflict pairs; run the mechanical legitimate-variation filter; emit a
    validated ledger row per surviving tension.

    Focal-vs-rest (not all-pairs) keeps M1 to O(N) alignments. Each row is built
    to the schema's closed contract; the schema validator (called later) raises
    on any malformed row."""
    focal_commitments = commitments_by_doc.get(focal_doc, [])
    rows: list[dict[str, Any]] = []
    for other_doc, other_commitments in commitments_by_doc.items():
        if other_doc == focal_doc:
            continue
        for ca in focal_commitments:
            for cb in other_commitments:
                if ca.topic_ref != cb.topic_ref:
                    continue
                relation = cjudge.detect_tension(ca, cb)
                if relation not in TENSION_RELATIONS:
                    continue
                blob = f"{ca.quote}\n{ca.statement}\n{cb.quote}\n{cb.statement}"
                legitimate_variation, rationale = classify_legitimate_variation(blob)
                severity = _assign_severity(relation, ca.ctype, cb.ctype)
                rows.append({
                    "loci": [ca.locus(), cb.locus()],
                    "topic_ref": ca.topic_ref,
                    "relation": relation,
                    "legitimate_variation": legitimate_variation,
                    "rationale": rationale,
                    "severity": severity,
                    "resolution_class": _resolution_class(relation, legitimate_variation),
                })
    # Deterministic order: by (topic_ref, focal doc, other doc, start offsets).
    rows.sort(key=lambda r: (
        r["topic_ref"], r["loci"][0]["doc"], r["loci"][1]["doc"],
        r["loci"][0]["start_char"], r["loci"][1]["start_char"],
    ))
    return rows


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_relation: dict[str, int] = {}
    by_variation: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for r in rows:
        by_relation[r["relation"]] = by_relation.get(r["relation"], 0) + 1
        by_variation[r["legitimate_variation"]] = by_variation.get(r["legitimate_variation"], 0) + 1
        by_severity[r["severity"]] = by_severity.get(r["severity"], 0) + 1
    genuine = sum(1 for r in rows if r["legitimate_variation"] == "genuine")
    defended = len(rows) - genuine
    return {
        "n_tensions": len(rows),
        "n_genuine": genuine,
        "n_defended": defended,
        "by_relation": by_relation,
        "by_legitimate_variation": by_variation,
        "by_severity_ordinal": by_severity,
    }


_DOES_NOT_LICENSE = (
    "Consistency is a property of a TEXT PAIR, not of the author's character, "
    "honesty, or good faith. A tension is NOT a finding of hypocrisy, dishonesty, "
    "bad faith, or self-contradiction; a defended tension is NOT an inconsistency; "
    "the surface picks NO 'winning' document and renders NO determination of which "
    "document is correct. The severity ordinal (Salient/Notable/Minor) measures how "
    "load-bearing a tension is to each text's spine — it is descriptive, NOT a rank "
    "to sort the author on and NOT a judgment of the author. There is no top-level "
    "consistency score and no author score. heuristic / no numeric anchor: there is "
    "no measured discrimination. The surface emits no verdict."
)


def compose_results(
    commitments_by_doc: dict[str, list[cjudge.Commitment]],
    focal_doc: str,
    *,
    judge_identity: dict[str, Any],
    n_docs: int,
    warnings: list[str],
) -> dict[str, Any]:
    rows = build_tensions(commitments_by_doc, focal_doc)
    results: dict[str, Any] = {
        "focal_doc": focal_doc,
        "n_docs": n_docs,
        "tensions": rows,
        "summary": _summary(rows),
        "does_not_license": _DOES_NOT_LICENSE,
        "calibration_status": "heuristic",
        "judge": judge_identity,
        "assumptions": {
            "method": (
                "per-document load-bearing commitment extraction (LLM judge), "
                "cross-document alignment by shared topic_ref, deterministic "
                "relation classification, then a mechanical legitimate-variation "
                "filter (retraction -> time -> scope -> audience -> genre)"
            ),
            "intake": "focal document vs the rest of the supplied pool (O(N) alignments; N capped for M1)",
            "legitimate_variation_order": list(DEFENSE_ORDER),
            "legitimate_variation_note": (
                "the first defense whose REQUIRED textual evidence is present in the "
                "aligned loci fires (defended_<that>); none firing -> genuine. Defended "
                "tensions are SHOWN in the ledger, marked defended_*, with the defense named"
            ),
            "severity_note": (
                "Salient/Notable/Minor is a DESCRIPTIVE ordinal of how load-bearing the "
                "tension is to each text's spine; it is NOT a judgment of the author and "
                "NOT a rank to sort on (does_not_license forbids ranking the author on it)"
            ),
            "firewall": (
                "FORBIDDEN_RESULT_KEYS + FORBIDDEN_SUBSTRINGS + a recursive "
                "assert_no_verdict() guard (raises ConsistencyVerdictError) called "
                "immediately before build_output; mechanical, not rhetorical"
            ),
            "calibration_status": "heuristic",
            "confounds": (
                "topic_ref alignment is judge-assigned; a mis-aligned pair can produce a "
                "spurious tension or miss a real one. The mock judge is a deterministic CI "
                "scaffold (marker-driven), NOT a real extraction; M2 (anthropic) is the "
                "real extraction lens. The legitimate-variation filter is keyword-evidence "
                "based; absence of a marker is not proof a variation is genuine"
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
            "A descriptive cross-document map of where an argument corpus's load-bearing "
            "commitments are in tension: per tension, the two verbatim loci (doc + character "
            "span + quote), the aligned topic_ref, the apparent relation (tension / "
            "direct_conflict), the legitimate-variation verdict (genuine or defended_<reason> "
            "with a named defense), a descriptive severity ordinal, and a firewall-safe class "
            "of resolution. The tensions are the read."
        ),
        "does_not_license": _DOES_NOT_LICENSE,
    }


# ---------- intake + envelope (the firewall call-site) --------------------------

def _resolve_docs(
    focal_path: Path,
    pool: list[tuple[str, str, Path | None]],
    *,
    max_docs: int,
    length_floor_words: int,
) -> tuple[str, dict[str, str], list[str]]:
    """Return (focal_label, {doc_label: text}, warnings). Self-excludes any pool
    entry that IS the focal (by resolved path OR normalized content), drops
    below-floor docs, and caps the pool at max_docs-1 (focal + up to max_docs-1
    others = max_docs total)."""
    warnings: list[str] = []
    focal_resolved = focal_path.resolve()
    focal_text = focal_path.read_text(encoding="utf-8", errors="replace")
    focal_label = focal_path.name
    focal_fp = _content_fingerprint(focal_text)

    docs: dict[str, str] = {focal_label: focal_text}
    kept = 0
    for src, text, rpath in pool:
        path_match = rpath is not None and rpath.resolve() == focal_resolved
        content_match = _content_fingerprint(text) == focal_fp
        if path_match or content_match:
            continue  # self-exclusion
        if len(_WORD_RE.findall(text)) < length_floor_words:
            warnings.append(f"pool doc {src!r} below length floor; dropped")
            continue
        label = src if src not in docs else f"{src}#{kept}"
        if kept >= max_docs - 1:
            warnings.append(f"pool doc {src!r} dropped: max_docs={max_docs} cap reached")
            continue
        docs[label] = text
        kept += 1
    return focal_label, docs, warnings


def compose_envelope(
    focal_path: Path,
    pool: list[tuple[str, str, Path | None]],
    *,
    judge_kind: str = "mock",
    judge_model: str | None = None,
    manifest_path: str | None = None,
    max_docs: int = DEFAULT_MAX_DOCS,
    length_floor_words: int = DEFAULT_LENGTH_FLOOR_WORDS,
) -> dict[str, Any]:
    """Run the surface and build the output envelope. Calls validate_results +
    assert_no_verdict IMMEDIATELY before build_output (the firewall call-site)."""
    try:
        focal_label, docs, warnings = _resolve_docs(
            focal_path, pool, max_docs=max_docs, length_floor_words=length_floor_words
        )
    except (OSError, UnicodeDecodeError) as exc:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(focal_path), reason=f"cannot read focal document: {exc}",
            reason_category="bad_input",
        )

    if len(docs) < 2:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(focal_path),
            reason="need at least 2 documents (a focal plus >=1 usable pool doc) to compare",
            reason_category="bad_input",
        )

    focal_words = len(_WORD_RE.findall(docs[focal_label]))
    if focal_words < length_floor_words:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(focal_path), target_words=focal_words,
            reason=f"focal document has {focal_words} word(s) (< floor {length_floor_words})",
            reason_category="text_too_short",
        )

    # Build the judge and extract commitments per doc.
    try:
        judge = cjudge.build_judge(judge_kind, manifest_path=manifest_path, model=judge_model)
    except cjudge.JudgeError as exc:
        cat = "missing_dependency" if judge_kind not in ("mock", "manifest") else "bad_input"
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(focal_path), target_words=focal_words,
            reason=str(exc), reason_category=cat,
        )

    commitments_by_doc: dict[str, list[cjudge.Commitment]] = {}
    judge_identity: dict[str, Any] = {}
    all_warnings: list[str] = list(warnings)
    try:
        for label, text in docs.items():
            jr = judge(label, text)
            commitments_by_doc[label] = jr.commitments
            judge_identity = jr.judge_identity
            all_warnings.extend(jr.warnings)
    except cjudge.JudgeError as exc:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(focal_path), target_words=focal_words,
            reason=f"judge failure: {exc}", reason_category="internal_error",
        )

    results = compose_results(
        commitments_by_doc, focal_label,
        judge_identity=judge_identity, n_docs=len(docs), warnings=all_warnings,
    )

    # Schema validation: closed-key / required-field contract. A defended_* (or
    # genuine) row with an empty rationale is a build error here (filter-integrity).
    validate_results(results)

    # Layer-1 firewall: assert_no_verdict IMMEDIATELY before build_output. Any
    # verdict/character/score key or value, or an out-of-whitelist severity,
    # raises ConsistencyVerdictError -> main() routes to policy_refused.
    assert_no_verdict(results)

    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=str(focal_path), target_words=focal_words,
        baseline={"n_files": len(docs) - 1, "words": sum(
            len(_WORD_RE.findall(t)) for lbl, t in docs.items() if lbl != focal_label
        )},
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
    ap.add_argument("--focal", required=True, help="Path to the focal argument-shaped document.")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--reference-dir", help="Directory of pool documents (.txt/.md, recursive).")
    grp.add_argument("--reference-manifest", help="JSONL manifest of the pool (id + text|text_path).")
    ap.add_argument("--judge", default="mock",
                    choices=["mock", "manifest", "anthropic", "openai", "gemini", "agent_host"],
                    help="Judge backend: mock (M1, deterministic CI) | manifest | anthropic (M2).")
    ap.add_argument("--judge-model", help="Model id for an API judge (required for anthropic/openai/gemini).")
    ap.add_argument("--judge-manifest", help="Path to a commitment manifest (for --judge manifest).")
    ap.add_argument("--max-docs", type=int, default=DEFAULT_MAX_DOCS,
                    help=f"Cap on total docs (focal + pool) for M1 (default {DEFAULT_MAX_DOCS}).")
    ap.add_argument("--length-floor-words", type=int, default=DEFAULT_LENGTH_FLOOR_WORDS,
                    help=f"Minimum word count per doc (default {DEFAULT_LENGTH_FLOOR_WORDS}).")
    ap.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    ap.add_argument("--out", help="Write JSON output to FILE instead of stdout.")
    args = ap.parse_args(argv)

    # Load the pool.
    try:
        if args.reference_dir:
            pool = _load_reference_dir(Path(args.reference_dir))
        else:
            pool = _load_reference_manifest(Path(args.reference_manifest))
    except (OSError, UnicodeDecodeError) as exc:
        which = "--reference-dir" if args.reference_dir else "--reference-manifest"
        env = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=args.focal, reason=f"cannot read {which}: {exc}",
            reason_category="bad_input",
        )
        _emit(env, args)
        return 1

    try:
        env = compose_envelope(
            Path(args.focal), pool,
            judge_kind=args.judge, judge_model=args.judge_model,
            manifest_path=args.judge_manifest,
            max_docs=args.max_docs, length_floor_words=args.length_floor_words,
        )
    except ConsistencyVerdictError as exc:
        env = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=args.focal, reason=str(exc), reason_category="policy_refused",
        )
        _emit(env, args)
        return 3
    except SchemaError as exc:
        env = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=args.focal, reason=f"schema contract violation: {exc}",
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
