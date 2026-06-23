#!/usr/bin/env python3
"""house_style_decomposition.py — nested-baseline idiolect-vs-house attribution (M1 stdlib).

A writer's prose carries two superimposed styles: their own **idiolect**, and the **house /
publication style** their publisher/employer/venue imposes.  This surface runs the target's
stylometric feature vector against a small ordered ladder of curated NESTED baselines and reports,
per feature family, **which baseline level the target's value tracks** — a descriptive
attribution-of-variation profile.

This is NEVER:
  * a verdict (same_author / different_author / AI / human)
  * an authorship call
  * a "this is the real you" certificate
  * a probability, score, or population band

M1 = pure-stdlib Burrows-Delta over nested baselines (CI-runnable, torch/transformers/spaCy absent).
M2 (optional, lazy-import seam) = a model-based dimensionality-reduction lens behind
``available:false`` / ``missing_dependency``; NOT in this build.

Posture: descriptive / no-verdict / anti-Goodhart / calibration PROVISIONAL.

References:
  * Burrows (2002) — "'Delta': a Measure of Stylistic Difference and a Guide to Likely
    Authorship", *Computers and the Humanities* 37(3). The per-family Burrows-Delta engine
    is ``stylometry_core.compare_to_baseline(..., include_spacy=False)``.
  * Biber (1988) — register variation as the framing context for the idiolect / house partition.
  * This spec: setec-scratch/spec-wave-4/tier4a-house-style-decomposition.md
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_schema import build_error_output, build_output  # noqa: E402
from claim_license import ClaimLicense  # noqa: E402
from stylometry_core import compare_to_baseline, word_tokens  # noqa: E402

TASK_SURFACE = "house_style_decomposition"
TOOL_NAME = "house_style_decomposition"
SCRIPT_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Closed, ordered vocabularies — each has a len() count-invariant in the tests.
# ---------------------------------------------------------------------------

BASELINE_LEVELS: tuple[str, ...] = (
    "same_author_same_org",       # writer's OTHER work through THIS house
    "different_context",          # writer's work in a DIFFERENT venue (idiolect-isolating)
    "different_authors_same_org", # OTHER writers through THIS house (house-style-isolating)
    "same_genre_outside_org",     # same genre, OTHER houses (genre-vs-house)
    "broad_reference",            # broad reference corpus (outermost shell)
)
assert len(BASELINE_LEVELS) == 5  # count invariant

# The two isolating levels that define the contrast.  Both MUST be present.
_ISOLATING_LEVEL_IDIOLECT = "different_context"
_ISOLATING_LEVEL_HOUSE = "different_authors_same_org"
_REQUIRED_LEVELS = frozenset({_ISOLATING_LEVEL_IDIOLECT, _ISOLATING_LEVEL_HOUSE})
# Levels that MUST NOT contain the target author (leakage guard).
_AUTHOR_LEAK_CHECKED_LEVELS = frozenset({
    "different_authors_same_org",
    "same_genre_outside_org",
    "broad_reference",
})

# Per-level IDENTITY-MEMBERSHIP rules, bound to the target writer + house.
# Each present level must structurally MATCH the partition it claims to represent;
# otherwise a mislabeled corpus silently confounds the idiolect-vs-house read (and a
# de-anonymization-shaped ladder slips the leakage guard). Validated fail-CLOSED at load:
# every entry's author_id/org_id is checked against its level's required membership, and
# any mismatch RAISES HouseStyleError -> bad_input. Derived from the spec's level
# semantics (setec-scratch/spec-wave-4/tier4a-house-style-decomposition.md:112-117,207):
#   same_author_same_org       = the writer's OTHER work through THIS house
#                                  -> author == target, org == target_org
#   different_context          = the writer's work in a DIFFERENT venue (idiolect level)
#                                  -> author == target, org != target_org (a different venue)
#   different_authors_same_org = OTHER writers through THIS house (house-style level)
#                                  -> author != target, org == target_org
#   same_genre_outside_org     = same genre, OTHER houses
#                                  -> author != target, org != target_org
#   broad_reference            = broad reference shell (org unconstrained; null allowed)
#                                  -> author != target (already in _AUTHOR_LEAK_CHECKED_LEVELS)
# Author membership: True => entry author MUST equal target author; False => MUST differ;
# None => unconstrained (no per-author rule beyond the leak guard).
_LEVEL_AUTHOR_IS_TARGET: dict[str, bool] = {
    "same_author_same_org": True,
    "different_context": True,
    "different_authors_same_org": False,
    "same_genre_outside_org": False,
    "broad_reference": False,
}
# Org membership: True => entry org MUST equal target org; False => MUST differ from it;
# None => unconstrained (broad_reference — null org allowed).
_LEVEL_ORG_IS_TARGET: dict[str, bool | None] = {
    "same_author_same_org": True,
    "different_context": False,
    "different_authors_same_org": True,
    "same_genre_outside_org": False,
    "broad_reference": None,
}

# M1 model-free families (include_spacy=False omits pos_trigrams + dependency_ngrams).
M1_FAMILIES: tuple[str, ...] = (
    "function_words",
    "char_ngrams_3",
    "char_ngrams_4",
    "char_ngrams_5",
    "punctuation",
    "paragraph_dialogue",
    "pronoun_modal_negation",
)
assert len(M1_FAMILIES) == 7  # count invariant

ATTRIBUTION_BANDS: tuple[str, ...] = (
    "idiolect_borne",        # idiolect_proximity >= +margin
    "house_borne",           # idiolect_proximity <= -margin
    "shared_or_indistinct",  # |idiolect_proximity| < margin
)
assert len(ATTRIBUTION_BANDS) == 3  # count invariant

CENTER_ATTRIBUTION = "shared_or_indistinct"

# Sign convention: positive = idiolect-borne.
# idiolect_proximity = D[different_authors_same_org] − D[different_context]
# positive ⇒ target tracks its own cross-house idiolect MORE than the house's other authors.
ORIENTATIONS: tuple[str, ...] = (
    "positive_idiolect_borne",
)
assert len(ORIENTATIONS) == 1  # count invariant
ORIENTATION = "positive_idiolect_borne"  # the only M1 orientation

DEFAULT_MARGIN = 0.15      # operator knob on the Delta scale
DEFAULT_MIN_AUTHORS = 3    # floor for multi-author levels
DEFAULT_MIN_WORDS = 2000   # per-level total-word floor
DEFAULT_MIN_VARIANCE_DOCS = 2  # per multi-author level doc floor
TARGET_FLOOR_WORDS = 300   # target document minimum


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

class HouseStyleError(ValueError):
    """Raised on curation / leakage / load failures.  Maps to bad_input."""


# ---------------------------------------------------------------------------
# Manifest entry
# ---------------------------------------------------------------------------

@dataclass
class BaselineEntry:
    """One baseline entry post-load.

    ``resolved_path`` is set for every entry (inline text is FORBIDDEN — the
    leakage guard precondition).  If a path cannot be resolved the loader
    raises ``HouseStyleError``.
    """
    id: str
    text: str
    level: str
    author_id: str
    org_id: str | None
    resolved_path: Path


# ---------------------------------------------------------------------------
# Manifest / directory loader
# ---------------------------------------------------------------------------

def _load_manifest_entries(
    manifest_path: Path,
    *,
    valid_levels: frozenset[str] | None = None,
) -> list[BaselineEntry]:
    """Read a JSONL manifest, enforcing the text_path-only rule.

    Malformed JSON lines are skipped with a stderr warning (mirrors
    ``general_imposters._load_manifest`` skip-and-warn).
    """
    if valid_levels is None:
        valid_levels = frozenset(BASELINE_LEVELS)

    base = manifest_path.parent
    entries: list[BaselineEntry] = []
    for line_no, raw in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            sys.stderr.write(
                f"  house_style_decomposition manifest line {line_no}: {exc}; skipping\n"
            )
            continue

        entry_id = str(row.get("id") or f"line_{line_no}")

        # Gate 0 — inline text FORBIDDEN; text_path required.
        if "text" in row:
            raise HouseStyleError(
                f"entry {entry_id!r} carries inline 'text'; inline text is forbidden — "
                f"use 'text_path' (file path) instead"
            )
        if not row.get("text_path"):
            raise HouseStyleError(
                f"entry {entry_id!r} is missing 'text_path'; every baseline entry must "
                f"supply a file path via 'text_path'"
            )

        text_path_str = row["text_path"]
        text_path = Path(text_path_str)
        if not text_path.is_absolute():
            text_path = (base / text_path).resolve()

        if not text_path.is_file():
            sys.stderr.write(
                f"  house_style_decomposition manifest line {line_no}: "
                f"{text_path} not found; skipping\n"
            )
            continue

        try:
            text = text_path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            sys.stderr.write(
                f"  read failed for {text_path}: {exc}; skipping\n"
            )
            continue

        level = str(row.get("level") or "")
        if level not in valid_levels:
            raise HouseStyleError(
                f"entry {entry_id!r} has level={level!r}; "
                f"must be one of {sorted(valid_levels)}"
            )

        author_id = str(row.get("author_id") or "")
        if not author_id:
            sys.stderr.write(
                f"  house_style_decomposition manifest line {line_no}: "
                f"missing author_id; skipping\n"
            )
            continue

        org_id = row.get("org_id") or None

        entries.append(BaselineEntry(
            id=entry_id,
            text=text,
            level=level,
            author_id=author_id,
            org_id=str(org_id) if org_id is not None else None,
            resolved_path=text_path,
        ))

    return entries


def _load_dir_entries(baseline_dir: Path) -> list[BaselineEntry]:
    """Load from a directory tree: DIR/<level>/<author_id>/*.txt|*.md

    org_id is read from DIR/<level>/_org.txt if present (optional).
    """
    entries: list[BaselineEntry] = []
    valid_levels = frozenset(BASELINE_LEVELS)
    for level_dir in sorted(baseline_dir.iterdir()):
        if not level_dir.is_dir():
            continue
        level = level_dir.name
        if level not in valid_levels:
            continue
        # Optional org_id file
        org_id_path = level_dir / "_org.txt"
        org_id: str | None = None
        if org_id_path.is_file():
            org_id = org_id_path.read_text(encoding="utf-8").strip() or None

        for author_dir in sorted(level_dir.iterdir()):
            if not author_dir.is_dir():
                continue
            author_id = author_dir.name
            for text_file in sorted(author_dir.rglob("*")):
                if text_file.suffix.lower() not in {".txt", ".md"}:
                    continue
                if not text_file.is_file():
                    continue
                try:
                    text = text_file.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                entry_id = f"{level}/{author_id}/{text_file.name}"
                entries.append(BaselineEntry(
                    id=entry_id,
                    text=text,
                    level=level,
                    author_id=author_id,
                    org_id=org_id,
                    resolved_path=text_file.resolve(),
                ))
    return entries


# ---------------------------------------------------------------------------
# Curation / leakage gate
# ---------------------------------------------------------------------------

def _validate_baseline_set(
    entries: list[BaselineEntry],
    target_path: Path | None,
    target_author: str,
    target_org: str,
    *,
    min_authors: int = DEFAULT_MIN_AUTHORS,
    min_words: int = DEFAULT_MIN_WORDS,
    min_variance_docs: int = DEFAULT_MIN_VARIANCE_DOCS,
) -> None:
    """Enforce all acceptance gates.  Raises ``HouseStyleError`` on violation.

    ``target_author`` and ``target_org`` are the target writer's and target house's
    identities.  BOTH are REQUIRED (fail-loud): the leakage guard and the per-level
    identity-membership check are inert / undefined without them, so an empty value is
    a ``HouseStyleError`` (``bad_input``), not a silently-skipped check.  Each present
    level's entries are then validated against ``_LEVEL_AUTHOR_IS_TARGET`` /
    ``_LEVEL_ORG_IS_TARGET`` so a mislabeled corpus (a different author filed under a
    same-author level, or a wrong-house entry filed under a house level) is REFUSED.
    """
    # Gate -1 — target identity REQUIRED (no inert empty default; fail-CLOSED).
    if not target_author or not target_author.strip():
        raise HouseStyleError(
            "target author identity is required (--target-author); without it the "
            "leakage guard and per-level author-membership check are inert"
        )
    if not target_org or not target_org.strip():
        raise HouseStyleError(
            "target organization identity is required (--target-org); without it the "
            "per-level house-membership (org) check is inert and a wrong-house entry "
            "could masquerade as the target house"
        )

    by_level: dict[str, list[BaselineEntry]] = {}
    for e in entries:
        by_level.setdefault(e.level, []).append(e)

    # Gate 1 — both isolating levels present.
    for required in _REQUIRED_LEVELS:
        if not by_level.get(required):
            raise HouseStyleError(
                f"required isolating level '{required}' is absent; "
                f"both '{_ISOLATING_LEVEL_IDIOLECT}' and '{_ISOLATING_LEVEL_HOUSE}' must "
                f"have at least one entry (the idiolect-vs-house contrast is undefined without them)"
            )

    for level, level_entries in by_level.items():
        # Gate 0b — leakage: content path-identity check (applies to ALL levels).
        # Gate 0 guarantees every entry has a resolved_path.
        if target_path is not None:
            try:
                t_resolved = target_path.resolve()
            except OSError:
                t_resolved = None
            if t_resolved is not None:
                for e in level_entries:
                    try:
                        if e.resolved_path.resolve() == t_resolved:
                            raise HouseStyleError(
                                f"leakage: target file {target_path} appears as baseline entry "
                                f"'{e.id}' at level '{level}'; the decomposition cannot compare "
                                f"a document to itself"
                            )
                    except OSError:
                        pass

        # Author-id leakage for the non-author levels.
        if level in _AUTHOR_LEAK_CHECKED_LEVELS:
            for e in level_entries:
                if e.author_id == target_author:
                    raise HouseStyleError(
                        f"leakage: target author '{target_author}' appears in "
                        f"'{level}' baseline (entry '{e.id}'); "
                        f"the house-style baseline must not contain the target writer"
                    )

        # Gate 1b — per-level IDENTITY MEMBERSHIP (fail-CLOSED).
        # Bind every entry's author_id / org_id to the partition its level claims, so a
        # mislabeled corpus cannot quietly confound the read.  (Superset of the author-leak
        # check above: the leak check guards the three non-author levels; this binds ALL
        # five levels, including the same-author levels that the leak check exempts.)
        author_rule = _LEVEL_AUTHOR_IS_TARGET.get(level)
        org_rule = _LEVEL_ORG_IS_TARGET.get(level)
        for e in level_entries:
            if author_rule is True and e.author_id != target_author:
                raise HouseStyleError(
                    f"membership: level '{level}' must contain ONLY the target author "
                    f"'{target_author}', but entry '{e.id}' has author_id "
                    f"'{e.author_id}'; the {level} level is the target writer's own work"
                )
            if author_rule is False and e.author_id == target_author:
                raise HouseStyleError(
                    f"membership: level '{level}' must NOT contain the target author "
                    f"'{target_author}', but entry '{e.id}' does; the {level} level "
                    f"must isolate the house / reference, not the target writer"
                )
            if org_rule is True and e.org_id != target_org:
                raise HouseStyleError(
                    f"membership: level '{level}' must belong to the target house "
                    f"'{target_org}', but entry '{e.id}' has org_id {e.org_id!r}; "
                    f"the {level} level must represent the target's own house"
                )
            if org_rule is False and e.org_id is not None and e.org_id == target_org:
                raise HouseStyleError(
                    f"membership: level '{level}' must NOT belong to the target house "
                    f"'{target_org}', but entry '{e.id}' does; the {level} level must "
                    f"isolate a DIFFERENT venue / house from the target's"
                )

        # Gate 2 — min authors for multi-author levels.
        if level in {"different_authors_same_org", "same_genre_outside_org"}:
            distinct_authors = len({e.author_id for e in level_entries})
            if distinct_authors < min_authors:
                raise HouseStyleError(
                    f"level '{level}' has only {distinct_authors} distinct author_id(s); "
                    f"at least {min_authors} are required (a single-author 'house' baseline "
                    f"confounds the partition)"
                )

        # Gate 3 — min words per level.
        total_words = sum(len(word_tokens(e.text)) for e in level_entries)
        if total_words < min_words:
            raise HouseStyleError(
                f"level '{level}' has only ~{total_words} words; "
                f"at least {min_words} required for stable Delta"
            )

        # Gate 4 — variance floor.
        if level in {"different_authors_same_org", "same_genre_outside_org", "broad_reference"}:
            if len(level_entries) < min_variance_docs:
                raise HouseStyleError(
                    f"level '{level}' has {len(level_entries)} doc(s); "
                    f"at least {min_variance_docs} required per multi-author level"
                )


# ---------------------------------------------------------------------------
# Decomposition algorithm
# ---------------------------------------------------------------------------

def _entries_to_stylometry(entries: list[BaselineEntry]) -> list[dict[str, Any]]:
    """Convert BaselineEntry list to the format compare_to_baseline expects."""
    return [
        {"id": e.id, "path": str(e.resolved_path), "text": e.text, "metadata": {}}
        for e in entries
    ]


def decompose(
    target_text: str,
    entries: list[BaselineEntry],
    *,
    margin: float = DEFAULT_MARGIN,
) -> dict[str, Any]:
    """Core M1 decomposition over the nested baseline ladder.

    Returns a ``results`` dict ready for ``build_output``.
    """
    by_level: dict[str, list[BaselineEntry]] = {}
    for e in entries:
        by_level.setdefault(e.level, []).append(e)

    levels_present = [lvl for lvl in BASELINE_LEVELS if lvl in by_level]

    # Per-level per-family Burrows-Delta.
    per_level_family_delta: dict[str, dict[str, float]] = {}
    level_stats: dict[str, dict[str, Any]] = {}

    for level in levels_present:
        level_entries = by_level[level]
        baseline = _entries_to_stylometry(level_entries)
        try:
            result = compare_to_baseline(
                target_text,
                baseline,
                include_spacy=False,
                include_clusters=False,
            )
        except ValueError:
            # Empty baseline or other issue — skip level (shouldn't happen after gate).
            continue

        families = result.get("families", {})
        level_deltas: dict[str, float] = {}
        for fam in M1_FAMILIES:
            if fam in families:
                level_deltas[fam] = float(families[fam].get("burrows_delta", 0.0))

        per_level_family_delta[level] = level_deltas

        # Level stats for the baseline block.
        level_stats[level] = {
            "n_docs": len(level_entries),
            "n_authors": len({e.author_id for e in level_entries}),
            "words": sum(len(word_tokens(e.text)) for e in level_entries),
        }

    # Idiolect-vs-house contrast (signed).
    # idiolect_proximity = D[different_authors_same_org] - D[different_context]
    # POSITIVE ⇒ idiolect-borne; NEGATIVE ⇒ house-borne.
    idiolect_house_contrast: dict[str, float] = {}
    attribution: dict[str, str] = {}

    house_deltas = per_level_family_delta.get(_ISOLATING_LEVEL_HOUSE, {})
    idiom_deltas = per_level_family_delta.get(_ISOLATING_LEVEL_IDIOLECT, {})

    for fam in M1_FAMILIES:
        d_house = house_deltas.get(fam)
        d_idiom = idiom_deltas.get(fam)
        if d_house is not None and d_idiom is not None:
            contrast = d_house - d_idiom
            idiolect_house_contrast[fam] = contrast
            if contrast >= margin:
                attribution[fam] = "idiolect_borne"
            elif contrast <= -margin:
                attribution[fam] = "house_borne"
            else:
                attribution[fam] = CENTER_ATTRIBUTION
        else:
            idiolect_house_contrast[fam] = 0.0
            attribution[fam] = CENTER_ATTRIBUTION

    # Attribution summary — grouping by band.
    attribution_summary: dict[str, list[str]] = {
        band: [] for band in ATTRIBUTION_BANDS
    }
    for fam, band in attribution.items():
        attribution_summary[band].append(fam)

    return {
        "levels_present": levels_present,
        "per_level_family_delta": per_level_family_delta,
        "idiolect_house_contrast": idiolect_house_contrast,
        "attribution": attribution,
        "attribution_summary": attribution_summary,
        "calibration_status": "provisional",
        "assumptions": {
            "method": (
                "nested-baseline Burrows-Delta (Burrows 2002, 'Delta': "
                "a Measure of Stylistic Difference, Computers and the Humanities 37(3))"
            ),
            "lens": "delta",
            "orientation": ORIENTATION,
            "margin": margin,
            "m1_families": list(M1_FAMILIES),
            "confounds": [
                "topic mismatch between target and a level inflates that level's delta "
                "with no style cause",
                "a thin per-level pool destabilizes Delta",
                "copyedit conventions are not the same as idiolect (register tightening "
                "may be a third layer)",
            ],
            "no_band": (
                "no absolute band; attribution labels are within-this-ladder "
                "descriptive contrasts, not population cuts"
            ),
            "leakage_guard": {
                "levels_checked": sorted(_AUTHOR_LEAK_CHECKED_LEVELS),
                "rule": (
                    "entries at these levels must not carry the target author_id; "
                    "every entry's resolved_path must differ from the target file"
                ),
            },
            "calibration": {
                "status": "provisional",
                "margin": margin,
                "what_would_calibrate": (
                    "a labeled multi-author multi-house corpus where the "
                    "house-imposed vs author-borne provenance of each feature family "
                    "is known (e.g. a documented copyedit ledger), recorded as a "
                    "PROVENANCE entry"
                ),
                "what_ships_uncalibrated": (
                    "the attribution labels and the margin are descriptive "
                    "within-ladder contrasts, not population-calibrated; "
                    "the band-to-truth mapping is unestablished"
                ),
            },
        },
        "_level_stats": level_stats,  # pulled out into baseline block by caller
    }


# ---------------------------------------------------------------------------
# Claim license
# ---------------------------------------------------------------------------

def _build_claim_license() -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "For the supplied target and its operator-curated nested baseline ladder, "
            "the target's per-feature-family Burrows-Delta to EACH baseline level, "
            "the signed idiolect-vs-house contrast, and a descriptive per-family "
            "attribution label (idiolect_borne / house_borne / shared_or_indistinct) "
            "relative to THIS ladder, under the named lens."
        ),
        does_not_license=(
            "Does not license: any authorship or same-author / different-author "
            "determination; any ai or human provenance call; any claim that a "
            "feature is the writer's 'real voice' or 'true voice' or that the house "
            "'erased' it; any probability, score, or population band; comparability "
            "across different baseline ladders; any use as a selection, fitness, "
            "held-out, or training-conditioning signal; de-anonymization of writers "
            "across pseudonymous venues; any style-stripping or forgery recipe. "
            "House attribution is a within-ladder descriptive contrast, not a fact "
            "about the writer or the publisher; a thin or topic-mismatched level "
            "confounds it; shared_or_indistinct is the designed center, not an error."
        ),
        additional_caveats=[
            "This surface does NOT enter voicewright CONSUMED_SURFACES / SetecFitness / "
            "any training-conditioning or selection objective (anti-Goodhart boundary).",
            "The attribution labels are descriptive layer indicators, NOT ground truth "
            "about the writer or publisher; they are only valid relative to this specific "
            "curated ladder.",
        ],
        references=[
            "Burrows (2002) 'Delta': a Measure of Stylistic Difference and a Guide to "
            "Likely Authorship, Computers and the Humanities 37(3)",
            "setec-scratch/spec-wave-4/tier4a-house-style-decomposition.md",
        ],
    )


# ---------------------------------------------------------------------------
# Check-all fixture
# ---------------------------------------------------------------------------

def _build_worked_example_fixture(tmp_dir: Path | None = None) -> dict[str, Any]:
    """Build the §Method worked-example fixture deterministically for golden pinning.

    Design: punctuation contrast via DASH FREQUENCY (not dash type, which is undetectable).

    Writer J: idiolect uses first-person + MANY dashes (dash-heavy prose style).
    House A copyedit: converts to semicolons / strips dashes; does NOT strip first-person.
    Result:
      Target = J's House-A chapter: FIRST-PERSON + FEW dashes/semicolons
      Blog (different_context) = J's blog: FIRST-PERSON + MANY dashes
      House K/L/M (different_authors_same_org): THIRD-PERSON + FEW dashes/semicolons

    Expected attributions:
      pronoun_modal_negation → idiolect_borne:
        target=FP, blog=FP → D_blog near 0; house=TP → D_house large; contrast > 0
      punctuation → house_borne:
        target=few-dashes, house=few-dashes → D_house near 0;
        blog=many-dashes → D_blog large; contrast < 0
      char_ngrams_3 → shared_or_indistinct (contrast within margin)

    Variance is introduced within each level group so Burrows-Delta z-scores are non-zero:
    House K = pure TP; L = ~90% TP + ~10% FP mix; M = ~95% TP + ~5% FP mix.
    Blog entries have slight sentence-count truncation for non-zero char-ngram sd.
    """
    import tempfile

    # --- core sentence templates ---

    # First-person + MANY dashes (J's natural dash-heavy blog style)
    _FP_DASHY = (
        "I think—therefore I write—and I find I cannot stop. "
        "My approach—deeply personal—involves me and my own perspective. "
        "I write—often and freely—without restraint or fear. "
        "I—like many writers—find myself drawn to the form of the essay. "
    )
    # First-person + FEW dashes (J's House-A chapters: house convention, semicolons replace dashes)
    _FP_SEMI = (
        "I think; therefore I write; and I find I cannot stop. "
        "My approach; deeply personal; involves me and my own perspective. "
        "I write; often and freely; without restraint or fear. "
        "I; like many writers; find myself drawn to the form of the essay. "
    )
    # Third-person + FEW dashes (House-A K/L/M default style: semicolons, third-person)
    _TP_SEMI = (
        "She thinks; therefore she writes; and she finds she cannot stop. "
        "Her approach; deeply professional; involves her own perspective. "
        "She writes; often and carefully; without distraction or haste. "
        "She; like many professionals; finds herself drawn to the precise form. "
    )
    # Third-person + MANY dashes (outside-org variant)
    _TP_DASHY = (
        "She thinks—therefore she writes—and she finds she cannot stop. "
        "Her approach—deeply professional—involves her own perspective. "
        "She writes—often and carefully—without distraction or haste. "
        "She—like many professionals—finds herself drawn to the precise form. "
    )

    def _fp_dashy(n: int) -> str: return _FP_DASHY * n
    def _fp_semi(n: int) -> str: return _FP_SEMI * n
    def _tp_semi(n: int) -> str: return _TP_SEMI * n
    def _tp_dashy(n: int) -> str: return _TP_DASHY * n

    # House-A other authors need VARIANCE in pronoun rates so Delta z-scores > 0.
    # K = pure third-person; L = 90% TP + 10% FP mix; M = 95% TP + 5% FP mix.
    def _house_k(n: int) -> str:
        return _TP_SEMI * n

    def _house_l(n: int) -> str:
        tp = max(1, int(n * 0.90))
        fp = n - tp
        return _TP_SEMI * tp + _FP_SEMI * fp

    def _house_m(n: int) -> str:
        tp = max(1, int(n * 0.95))
        fp = n - tp
        return _TP_SEMI * tp + _FP_SEMI * fp

    # J's blog entries (FIRST-PERSON + MANY dashes, with slight variation for non-zero sd)
    def _blog_a(n: int) -> str: return _FP_DASHY * n
    def _blog_b(n: int) -> str: return _FP_DASHY * (n - 2) + _FP_DASHY[:len(_FP_DASHY) // 2] * 2
    def _blog_c(n: int) -> str: return _FP_DASHY * (n - 4) + _FP_DASHY[:len(_FP_DASHY) // 3] * 4

    # Target = J's House-A chapter: FIRST-PERSON + FEW dashes (semicolons)
    target_text = _fp_semi(35)

    # same_author_same_org: J's other House-A chapters
    j_house_a = _fp_semi(30)
    j_house_b = _fp_semi(28)

    # different_context: J's blog posts (FIRST-PERSON + MANY dashes)
    j_blog_a = _blog_a(30)
    j_blog_b = _blog_b(28)
    j_blog_c = _blog_c(26)

    # different_authors_same_org: K (pure TP), L (~90% TP), M (~95% TP)
    k_text = _house_k(30)
    l_text = _house_l(28)
    m_text = _house_m(26)

    # same_genre_outside_org: 4 outside-org authors with mixed styles
    out_a = _fp_dashy(25)
    out_b = _tp_semi(25)
    out_c = _fp_semi(25)
    out_d = _tp_dashy(25)

    # broad_reference: 20 docs, 5 virtual authors, alternating styles
    broad_texts = []
    for i in range(20):
        if i % 4 == 0:
            broad_texts.append(_fp_dashy(20))
        elif i % 4 == 1:
            broad_texts.append(_tp_semi(20))
        elif i % 4 == 2:
            broad_texts.append(_fp_semi(20))
        else:
            broad_texts.append(_tp_dashy(20))

    if tmp_dir is None:
        tmp_obj = tempfile.TemporaryDirectory()
        tmp_dir = Path(tmp_obj.name)
    else:
        tmp_obj = None  # type: ignore[assignment]

    def _write(fname: str, text: str) -> Path:
        p = tmp_dir / fname
        p.write_text(text, encoding="utf-8")
        return p

    target_path = _write("target_j_house_chapter.txt", target_text)

    entries: list[BaselineEntry] = [
        BaselineEntry("j_house_a", j_house_a, "same_author_same_org", "writer:j", "house:a",
                      _write("j_house_a.txt", j_house_a)),
        BaselineEntry("j_house_b", j_house_b, "same_author_same_org", "writer:j", "house:a",
                      _write("j_house_b.txt", j_house_b)),
        BaselineEntry("j_blog_a", j_blog_a, "different_context", "writer:j", None,
                      _write("j_blog_a.txt", j_blog_a)),
        BaselineEntry("j_blog_b", j_blog_b, "different_context", "writer:j", None,
                      _write("j_blog_b.txt", j_blog_b)),
        BaselineEntry("j_blog_c", j_blog_c, "different_context", "writer:j", None,
                      _write("j_blog_c.txt", j_blog_c)),
        BaselineEntry("k_house", k_text, "different_authors_same_org", "writer:k", "house:a",
                      _write("k_house.txt", k_text)),
        BaselineEntry("l_house", l_text, "different_authors_same_org", "writer:l", "house:a",
                      _write("l_house.txt", l_text)),
        BaselineEntry("m_house", m_text, "different_authors_same_org", "writer:m", "house:a",
                      _write("m_house.txt", m_text)),
        BaselineEntry("out_a", out_a, "same_genre_outside_org", "writer:out_a", "house:x",
                      _write("out_a.txt", out_a)),
        BaselineEntry("out_b", out_b, "same_genre_outside_org", "writer:out_b", "house:y",
                      _write("out_b.txt", out_b)),
        BaselineEntry("out_c", out_c, "same_genre_outside_org", "writer:out_c", "house:z",
                      _write("out_c.txt", out_c)),
        BaselineEntry("out_d", out_d, "same_genre_outside_org", "writer:out_d", "house:w",
                      _write("out_d.txt", out_d)),
    ] + [
        BaselineEntry(
            f"broad_{i}", broad_texts[i], "broad_reference",
            f"writer:broad_{i % 5}", None,
            _write(f"broad_{i}.txt", broad_texts[i]),
        )
        for i in range(20)
    ]

    return {
        "target_text": target_text,
        "target_path": target_path,
        "entries": entries,
        "_tmp_obj": tmp_obj,
    }


def run_check_all() -> int:
    """Run against the worked-example fixture; assert golden pins. Return 0 on pass."""
    fixture = _build_worked_example_fixture()
    target_text = fixture["target_text"]
    entries = fixture["entries"]

    try:
        _validate_baseline_set(
            entries,
            fixture["target_path"],
            "writer:j",
            "house:a",
            min_authors=DEFAULT_MIN_AUTHORS,
            min_words=DEFAULT_MIN_WORDS,
        )
    except HouseStyleError as exc:
        print(f"FAIL: acceptance gate rejected the worked example: {exc}", file=sys.stderr)
        return 1

    results = decompose(target_text, entries, margin=DEFAULT_MARGIN)

    attr = results["attribution"]
    problems = []

    # Pin: pronoun_modal_negation → idiolect_borne
    # (target=FP, blog=FP → D_blog near-0; house K/L/M=TP → D_house large; contrast > 0)
    if attr.get("pronoun_modal_negation") != "idiolect_borne":
        problems.append(
            f"pronoun_modal_negation: expected 'idiolect_borne', "
            f"got {attr.get('pronoun_modal_negation')!r}"
        )

    # Pin: punctuation → house_borne
    # (target=few-dashes, house=few-dashes → D_house near-0;
    #  blog=many-dashes → D_blog large; contrast < 0)
    if attr.get("punctuation") != "house_borne":
        problems.append(
            f"punctuation: expected 'house_borne', "
            f"got {attr.get('punctuation')!r}"
        )

    if problems:
        for p in problems:
            print(f"FAIL: {p}", file=sys.stderr)
        return 1

    print("house_style_decomposition --check-all: PASSED")
    return 0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--target", required=False, help="Path to target text (UTF-8).")
    parser.add_argument(
        "--target-author",
        required=False,
        default="",
        help=(
            "Author ID of the target writer. REQUIRED for a real run (validated "
            "fail-loud): drives both the leakage guard and the per-level "
            "author-membership check."
        ),
    )
    parser.add_argument(
        "--target-org",
        required=False,
        default="",
        help=(
            "Organization / house ID of the target writer's house. REQUIRED for a "
            "real run (validated fail-loud): binds the house levels "
            "(same_author_same_org / different_authors_same_org) to the target's own "
            "house so a wrong-house entry cannot masquerade as the target house."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--baseline-set",
        help="Directory: <level>/<author_id>/*.txt|*.md",
    )
    group.add_argument(
        "--baseline-manifest",
        help="JSONL manifest with level + author_id + text_path per entry.",
    )
    parser.add_argument(
        "--min-authors", type=int, default=DEFAULT_MIN_AUTHORS,
        help=f"Min distinct author_ids for multi-author levels (default {DEFAULT_MIN_AUTHORS}).",
    )
    parser.add_argument(
        "--min-words", type=int, default=DEFAULT_MIN_WORDS,
        help=f"Min words per level (default {DEFAULT_MIN_WORDS}).",
    )
    parser.add_argument(
        "--margin", type=float, default=DEFAULT_MARGIN,
        help=f"Attribution margin on the Delta scale (default {DEFAULT_MARGIN}).",
    )
    parser.add_argument(
        "--lens", choices=["delta", "embedding"], default="delta",
        help="Decomposition lens (default: delta / M1). 'embedding' is M2 (unavailable in M1).",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON envelope to stdout.")
    parser.add_argument("--out", help="Write JSON envelope to file.")
    parser.add_argument(
        "--check-all", action="store_true",
        help="Run against the built-in worked-example fixture and verify golden pins.",
    )
    args = parser.parse_args(argv)

    if args.check_all:
        return run_check_all()

    # M2 seam — fail loud whether or not torch is importable.
    if args.lens == "embedding":
        err = build_error_output(
            task_surface=TASK_SURFACE,
            tool=TOOL_NAME,
            version=SCRIPT_VERSION,
            reason=(
                "--lens embedding is the M2 embedding-decomposition lens and is NOT available "
                "in M1. M1 wires no embedding subspace; use --lens delta (the default). "
                "The M2 seam refuses whether or not torch/transformers are importable — "
                "a present torch does NOT enable the embedding decomposition in M1."
            ),
            reason_category="missing_dependency",
        )
        if args.json or args.out:
            out_str = json.dumps(err, indent=2, default=str)
            if args.out:
                Path(args.out).write_text(out_str, encoding="utf-8")
            else:
                print(out_str)
        else:
            print(
                "ERROR: --lens embedding is unavailable in M1; use --lens delta.",
                file=sys.stderr,
            )
        return 1

    if not args.target:
        parser.error("--target is required (or use --check-all)")

    if not args.baseline_set and not args.baseline_manifest:
        err = build_error_output(
            task_surface=TASK_SURFACE,
            tool=TOOL_NAME,
            version=SCRIPT_VERSION,
            reason="Provide either --baseline-set DIR or --baseline-manifest JSONL.",
            reason_category="bad_input",
        )
        out_str = json.dumps(err, indent=2, default=str)
        if args.json or args.out:
            if args.out:
                Path(args.out).write_text(out_str, encoding="utf-8")
            else:
                print(out_str)
        else:
            print("ERROR: Provide either --baseline-set or --baseline-manifest.", file=sys.stderr)
        return 1

    target_path = Path(args.target)
    if not target_path.is_file():
        err = build_error_output(
            task_surface=TASK_SURFACE,
            tool=TOOL_NAME,
            version=SCRIPT_VERSION,
            reason=f"Target file not found: {target_path}",
            reason_category="bad_input",
            target_path=target_path,
        )
        _emit(err, args)
        return 1

    try:
        target_text = target_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        err = build_error_output(
            task_surface=TASK_SURFACE,
            tool=TOOL_NAME,
            version=SCRIPT_VERSION,
            reason=f"Cannot read target: {exc}",
            reason_category="bad_input",
            target_path=target_path,
        )
        _emit(err, args)
        return 1

    target_words = len(word_tokens(target_text))
    if target_words < TARGET_FLOOR_WORDS:
        err = build_error_output(
            task_surface=TASK_SURFACE,
            tool=TOOL_NAME,
            version=SCRIPT_VERSION,
            reason=(
                f"Target has only ~{target_words} words; "
                f"minimum {TARGET_FLOOR_WORDS} required for stable Delta."
            ),
            reason_category="bad_input",
            target_path=target_path,
            target_words=target_words,
        )
        _emit(err, args)
        return 1

    # Load baseline entries.
    try:
        if args.baseline_manifest:
            entries = _load_manifest_entries(Path(args.baseline_manifest))
        else:
            entries = _load_dir_entries(Path(args.baseline_set))
    except HouseStyleError as exc:
        err = build_error_output(
            task_surface=TASK_SURFACE,
            tool=TOOL_NAME,
            version=SCRIPT_VERSION,
            reason=str(exc),
            reason_category="bad_input",
            target_path=target_path,
            target_words=target_words,
        )
        _emit(err, args)
        return 1

    # Acceptance + leakage + identity-membership gates.
    try:
        _validate_baseline_set(
            entries,
            target_path,
            args.target_author,
            args.target_org,
            min_authors=args.min_authors,
            min_words=args.min_words,
        )
    except HouseStyleError as exc:
        err = build_error_output(
            task_surface=TASK_SURFACE,
            tool=TOOL_NAME,
            version=SCRIPT_VERSION,
            reason=str(exc),
            reason_category="bad_input",
            target_path=target_path,
            target_words=target_words,
        )
        _emit(err, args)
        return 1

    # Decompose.
    results = decompose(target_text, entries, margin=args.margin)

    # Build baseline metadata for the envelope.
    level_stats: dict[str, Any] = results.pop("_level_stats", {})
    baseline_meta = {
        "levels": level_stats,
        "leakage_checked": True,
    }

    envelope = build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=baseline_meta,
        results=results,
        claim_license=_build_claim_license(),
    )

    _emit(envelope, args)
    return 0


def _emit(envelope: dict[str, Any], args: argparse.Namespace) -> None:
    out_str = json.dumps(envelope, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(out_str, encoding="utf-8")
    elif args.json:
        print(out_str)
    else:
        print(out_str)


if __name__ == "__main__":
    sys.exit(main())
