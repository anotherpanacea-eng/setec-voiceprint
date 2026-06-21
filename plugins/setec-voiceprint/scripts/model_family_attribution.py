#!/usr/bin/env python3
"""model_family_attribution.py — abstention-first, raw per-family similarity ranking (spec 34, M1).

"Which model family does this prose read most like?" — answered as a **raw, un-normalized,
abstention-gated similarity ranking** of a `--target` against operator-supplied per-family reference
corpora, over a small set of STANDARDIZED interpretable stylometric features. **Not a verdict.** No
normalized posterior (nothing sums to 1 over families — that manufactures a P(family) reading), no
"produced by <family>" attribution, no AI-vs-human ruling. Raw ranked evidence for a human, under heavy,
*real* abstention. Pure stdlib (spaCy only gates the optional `mdd` feature); deterministic; no model.

Method (spec 34 §M1):
  1. Resolve the named feature set ONCE (intersection across target + all references) — burstiness_B /
     MATTR / MTLD / function_word_ratio / mean_dependency_distance. `mdd` needs spaCy; if spaCy is absent
     it is dropped for EVERYONE (the comparison subspace is fixed at run start, never per-doc).
  2. Standardize: robust centre+scale (median + MAD) over the POOLED reference docs; every doc → robust-z
     (non-commensurate raw features would let MTLD's scale dominate a raw mean).
  3. Per-family centroid = median standardized vector of that family's docs; within-scatter = median
     distance of the family's own docs to their centroid (the RELATIVE-OOD baseline).
  4. similarity = 1 / (1 + dist) in standardized space — raw and un-normalized.
  5. Gate → attribution_available + reason: <2 families; thin family (< MIN_DOCS_PER_FAMILY); relative
     OOD (dist_to_top > k·within_scatter_top); top-2 margin below threshold; `human` would be top. The
     ranking is still emitted as raw evidence when not attributable, flagged.

Upstream / prior art (advisory; M1 is deliberately weaker than any of them):
  - Biber-feature separation of LLM families — arXiv:2410.16107 (interpretable features separate families,
    but with ~96 features over large corpora; M1 uses ~5 named signals over operator corpora).
  - From Text to Source — arXiv:2309.13322 (the attribution task).
  - OpenTuringBench — arXiv:2504.11369 (the open-set / OOD framing the relative gate addresses).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_schema import build_error_output, build_output  # noqa: E402
from claim_license import ClaimLicense  # noqa: E402
from variance_audit import (  # noqa: E402
    HAS_SPACY,
    function_word_fingerprint,
    mattr,
    mdd_stats,
    mtld,
    sentence_length_stats,
    split_sentences,
    split_words,
)

TASK_SURFACE = "model_family_attribution"
TOOL_NAME = "model_family_attribution"
SCRIPT_VERSION = "1.0"

# PROVISIONAL thresholds — uncalibrated. A labelled attribution corpus would calibrate the *abstention*
# thresholds only (reported as provenance), never a shipped "attributed to X" operating point.
DEFAULT_OOD_K = 2.0          # relative-OOD: abstain when dist_to_top > k * within_scatter_top
DEFAULT_MARGIN = 0.05        # ambiguity: abstain when top-2 similarity margin < this
MIN_DOCS_PER_FAMILY = 5      # hard floor; a family thinner than this forces abstention
DEFAULT_MIN_WORDS = 50       # per-doc length floor; docs below it are dropped

# The named, model-free feature set. `mean_dependency_distance` is spaCy-gated; the others are stdlib.
# Order is fixed and deterministic. `_extract_features` resolves which are AVAILABLE for a given doc; the
# run then intersects availability across the target + every reference doc so the subspace is fixed once.
_STDLIB_FEATURES = ("burstiness_B", "mattr", "mtld", "function_word_ratio")
_SPACY_FEATURES = ("mean_dependency_distance",)
ALL_FEATURES = _STDLIB_FEATURES + _SPACY_FEATURES

# A `human`-class label is a permitted reference LABEL but may NEVER occupy the reported top slot (the
# AI/human axis belongs to the discrimination surfaces, which also refuse it). A human-top case abstains.
# The gate is the single load-bearing red line for an attribution surface, so it must NOT be defeatable by
# a one-character relabel: the comparison is case/space-insensitive AND matches a small reserved synonym
# set, not one exact string. `_is_human_label` is the single chokepoint every check routes through.
HUMAN_LABEL = "human"  # canonical label (back-compat / docs)
RESERVED_HUMAN_LABELS = frozenset({
    "human", "humans", "human_writers", "human-writers", "humanwritten", "human_written",
    "human-written", "people", "person", "organic", "non_ai", "non-ai", "nonai", "not_ai", "not-ai",
})


def _is_human_label(family: str) -> bool:
    """True when `family` names the reserved human class. Casefolded + stripped + inner whitespace/hyphen
    collapsed, then matched against RESERVED_HUMAN_LABELS, so neither case (`Human`) nor a near-synonym
    (`humans`, `human_writers`, `people`) can route a 'reads most like HUMAN' ruling around the gate."""
    norm = "_".join(family.strip().casefold().replace("-", "_").split())
    return norm in RESERVED_HUMAN_LABELS


# ---- feature extraction (named, model-free) ----------------------------------

def _extract_features(text: str) -> dict[str, float]:
    """Map a document to the named feature set. Only AVAILABLE features are present in the dict — an
    absent value (e.g. `mean_dependency_distance` when spaCy is missing) is simply not a key, so the
    run-level intersection can fix the subspace. Stdlib features are always present."""
    words = split_words(text)
    sentences = split_sentences(text)
    feats: dict[str, float] = {
        "burstiness_B": float(sentence_length_stats(sentences).get("burstiness_B", 0.0)),
        "mattr": float(mattr(words)),
        "mtld": float(mtld(words)),
        "function_word_ratio": float(
            function_word_fingerprint(words).get("function_word_ratio", 0.0)
        ),
    }
    if HAS_SPACY:
        mdd = mdd_stats(text)
        # mdd_stats returns None only when spaCy is absent (guarded above) — but be defensive: a
        # degenerate doc yields {n_sentences, mean, sd}. Use the mean dependency distance.
        if mdd is not None:
            feats["mean_dependency_distance"] = float(mdd.get("mean", 0.0))
    return feats


def _resolve_feature_set(
    target_feats: dict[str, float],
    ref_feats: list[dict[str, float]],
) -> list[str]:
    """Resolve the comparison subspace ONCE: the features present for the target AND every reference doc,
    in the fixed ALL_FEATURES order. A spaCy-gated feature is uniformly in (every doc has it) or out (any
    doc lacks it) — never per-doc, so centroids never compare different subspaces (spec P1)."""
    available = set(target_feats)
    for f in ref_feats:
        available &= set(f)
    return [name for name in ALL_FEATURES if name in available]


# ---- robust standardization (median / MAD over the pooled reference) ---------

def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _mad(values: list[float], centre: float) -> float:
    """Median absolute deviation about `centre`. Returns 0.0 for a degenerate (constant) feature; the
    caller floors the scale so a zero-MAD feature contributes 0 (not a divide-by-zero) after centering."""
    if not values:
        return 0.0
    return statistics.median([abs(v - centre) for v in values])


def _robust_scalers(
    pooled: list[dict[str, float]],
    feature_set: list[str],
) -> dict[str, tuple[float, float]]:
    """Per-feature (median, scale) over the POOLED reference docs. `scale` = 1.4826*MAD (the MAD→sd
    consistency constant) so a robust-z is comparable in magnitude to a classic z. A zero/degenerate
    scale is recorded as 0.0; `_standardize` then maps that feature to exactly 0.0 for every doc (a
    constant feature carries no discriminative signal and must not blow up to ±inf)."""
    scalers: dict[str, tuple[float, float]] = {}
    for name in feature_set:
        col = [d[name] for d in pooled if name in d]
        centre = _median(col)
        scale = 1.4826 * _mad(col, centre)
        scalers[name] = (centre, scale)
    return scalers


def _standardize(
    feats: dict[str, float],
    feature_set: list[str],
    scalers: dict[str, tuple[float, float]],
) -> list[float]:
    """Map a doc's features to a robust-z VECTOR in `feature_set` order. A feature whose pooled scale is
    0 (constant across the reference) maps to 0.0 — it is non-discriminative, not infinite."""
    vec: list[float] = []
    for name in feature_set:
        centre, scale = scalers[name]
        if scale <= 0.0:
            vec.append(0.0)
        else:
            vec.append((feats[name] - centre) / scale)
    return vec


def _euclidean(a: list[float], b: list[float]) -> float:
    return statistics.fsum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _vector_median(vectors: list[list[float]], dim: int) -> list[float]:
    """Coordinate-wise median (the centroid). Robust to outlier docs; well-defined for n>=1."""
    return [statistics.median([v[i] for v in vectors]) for i in range(dim)]


# ---- the ranking ------------------------------------------------------------

def rank_families(
    target_feats: dict[str, float],
    families: dict[str, list[dict[str, float]]],
    *,
    ood_k: float = DEFAULT_OOD_K,
    margin_threshold: float = DEFAULT_MARGIN,
    min_docs: int = MIN_DOCS_PER_FAMILY,
) -> dict[str, Any]:
    """Compute the raw, un-normalized per-family similarity ranking + the abstention gate.

    Pure function over already-extracted features (the CLI handles I/O + length filtering). `families`
    maps a family label to its list of per-doc feature dicts. Returns the value-level results payload:
    `family_ranking` (raw similarities, per-family within_scatter + n_docs), `top_margin`,
    `out_of_distribution`, `attribution_available`, `reason`, `n_families`, `feature_set`, plus the
    standardization provenance and the weak/low-dim assumptions. Raises ValueError on an unusable input
    (no families, or a resolved empty subspace) so the caller can map it to bad_input.

    Abstention is the DEFAULT: the ranking is always emitted as raw evidence, but `attribution_available`
    is True only when none of the gates trip.

    `min_docs` is a HARD floor: a caller may RAISE it but never lower it below MIN_DOCS_PER_FAMILY — a
    smaller value is clamped up here so the small-n protection cannot be opted out of (the over-claim
    axis the spec's P2 rework refuses), at the function level and not only at the CLI.
    """
    min_docs = max(MIN_DOCS_PER_FAMILY, min_docs)
    if not families:
        raise ValueError("no reference families supplied")

    ref_feats = [f for docs in families.values() for f in docs]
    if not ref_feats:
        raise ValueError("reference families contain no documents")

    feature_set = _resolve_feature_set(target_feats, ref_feats)
    if not feature_set:
        raise ValueError(
            "no shared feature is available across the target and every reference doc "
            "(resolved comparison subspace is empty)"
        )
    dim = len(feature_set)

    scalers = _robust_scalers(ref_feats, feature_set)
    target_vec = _standardize(target_feats, feature_set, scalers)

    # Per-family centroid (median standardized vector) + within-scatter (median distance of the family's
    # own docs to their centroid) — the relative-OOD baseline.
    ranking: list[dict[str, Any]] = []
    thin_families: list[str] = []
    for family, docs in families.items():
        n_docs = len(docs)
        if n_docs < min_docs:
            thin_families.append(family)
        vecs = [_standardize(d, feature_set, scalers) for d in docs]
        centroid = _vector_median(vecs, dim)
        within = [_euclidean(v, centroid) for v in vecs]
        within_scatter = _median(within)
        dist = _euclidean(target_vec, centroid)
        ranking.append({
            "family": family,
            "distance": round(dist, 6),
            "similarity": round(1.0 / (1.0 + dist), 6),  # raw, un-normalized, in (0, 1]
            "within_scatter": round(within_scatter, 6),
            "n_docs": n_docs,
        })

    # Deterministic order: similarity desc, then family name asc (stable tie-break).
    ranking.sort(key=lambda r: (-r["similarity"], r["family"]))

    n_families = len(families)
    top = ranking[0]
    runner_up = ranking[1] if len(ranking) > 1 else None
    top_margin = (
        round(top["similarity"] - runner_up["similarity"], 6)
        if runner_up is not None
        else None
    )

    # Relative-OOD gate (computed against the TOP family's own scatter). When the family is degenerate
    # (within_scatter == 0 — e.g. all its docs collapse to one point), any nonzero distance is OOD; an
    # exact-zero distance to a zero-scatter family is not.
    ws_top = top["within_scatter"]
    if ws_top > 0.0:
        out_of_distribution = top["distance"] > ood_k * ws_top
    else:
        out_of_distribution = top["distance"] > 0.0

    # The abstention gates (the ranking is still raw evidence either way). Order is fixed so `reason`
    # names the FIRST tripped gate deterministically.
    reasons: list[str] = []
    if n_families < 2:
        reasons.append(
            f"fewer than 2 reference families ({n_families}) — a ranking needs at least two "
            "labels to rank between"
        )
    if thin_families:
        reasons.append(
            f"family/families below the {min_docs}-doc floor: "
            f"{', '.join(sorted(thin_families))} — too thin for a stable centroid"
        )
    if out_of_distribution:
        reasons.append(
            f"relative out-of-distribution: the target's distance to the top family "
            f"({top['family']}, {top['distance']}) exceeds {ood_k}x that family's own within-scatter "
            f"({ws_top}) — the target is an outlier even relative to the family's members, so the "
            "true source is plausibly absent"
        )
    if top_margin is not None and top_margin < margin_threshold:
        reasons.append(
            f"top-2 margin {top_margin} below the ambiguity threshold {margin_threshold} — the top "
            f"two families ({top['family']}, {runner_up['family']}) are too close to separate"
        )
    if _is_human_label(top["family"]):
        reasons.append(
            f"a reserved human-class label (`{top['family']}`) would occupy the reported top slot — a "
            "human-class label may never be the top (a high `human` similarity is not a human "
            "certificate; the AI/human axis belongs to the discrimination surfaces, which also refuse "
            "it). The match is case/synonym-normalized, so a relabel cannot route around it."
        )

    attribution_available = not reasons
    reason = (
        "the target's nearest family by raw standardized-feature similarity is "
        f"{top['family']}, and no abstention gate tripped — this is weak, low-dimensional advisory "
        "evidence, NOT an attribution verdict"
        if attribution_available
        else "; ".join(reasons)
    )

    standardization = {
        "method": "robust-z (median + 1.4826*MAD over the pooled reference docs)",
        "per_feature": {
            name: {"median": round(c, 6), "scale": round(s, 6)}
            for name, (c, s) in scalers.items()
        },
        "note": (
            "features are non-commensurate (MTLD spans ~[10, 200+] while burstiness_B is ~[-1, 1]); "
            "standardizing before aggregation stops a large-scale feature from dominating the ranking. "
            "A constant (zero-scale) feature maps to 0 for every doc."
        ),
    }

    return {
        "family_ranking": ranking,
        "top_margin": top_margin,
        "out_of_distribution": out_of_distribution,
        "attribution_available": attribution_available,
        "reason": reason,
        "n_families": n_families,
        "feature_set": feature_set,
        "ood_k": ood_k,
        "margin_threshold": margin_threshold,
        "min_docs_per_family": min_docs,
        "calibration_status": "uncalibrated",
        "standardization": standardization,
        "assumptions": {
            "evidence_strength": (
                "WEAK, LOW-DIMENSIONAL evidence: ~5 named stylometric features over operator-supplied "
                "corpora, NOT the Biber paper's ~96 features over large corpora (arXiv:2410.16107). "
                "Treat as a candidate-surfacing similarity ranking, not a classifier."
            ),
            "no_normalized_posterior": (
                "raw per-family similarities only — nothing sums to 1 over families. A normalized "
                "posterior would manufacture a P(family) reading that looks confident even when every "
                "family fits badly."
            ),
            "open_world": (
                "the true source family may be ABSENT from the references; the relative-OOD gate exists "
                "for exactly that case and a missing family is never named. Self-excluding: the target "
                "is dropped from its own family corpus when it sits there."
            ),
            "corpus_dependence": (
                "the ranking is only as good as the operator's reference corpora — register/genre "
                "mismatch, thin pools, and ESL/dialect text are not adjudicated here. Thresholds "
                "(ood_k, margin, min_docs) are PROVISIONAL / uncalibrated."
            ),
        },
    }


def _claim_license() -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "A raw, abstention-gated per-family similarity RANKING of the target against "
            "operator-supplied reference corpora, over standardized named stylometric features — weak, "
            "low-dimensional advisory evidence about which supplied family the target reads most like."
        ),
        does_not_license=(
            "Any 'produced by <family>' attribution verdict — this is a similarity ranking, not a "
            "source determination. Any AI-vs-human ruling — a high `human` similarity is not a human "
            "certificate, and `human` may never be the reported top. Any calibrated probability or "
            "normalized posterior — the similarities are raw and un-normalized; nothing sums to 1 over "
            "families. Naming a family ABSENT from the supplied references — the surface names only "
            "supplied families and never invents one. This is weak, low-dimensional evidence "
            "(~5 features over operator corpora); thresholds are PROVISIONAL and the surface ships "
            "uncalibrated, emitting no verdict."
        ),
        additional_caveats=[
            "Abstention-first: attribution_available is False by default — it is True only when every "
            "gate (>=2 families, min docs/family, relative-OOD, top-2 margin, human-not-top) passes.",
            "The relative-OOD gate uses each family's OWN within-scatter, not a fixed floor, so it can "
            "tell 'true source absent' from 'register mismatch' rather than always picking a nearest "
            "centroid.",
        ],
        references=[
            "arXiv:2410.16107 (Biber-feature separation of LLM families)",
            "arXiv:2309.13322 (From Text to Source — the attribution task)",
            "arXiv:2504.11369 (OpenTuringBench — open-set / OOD framing)",
        ],
    )


# ---- self-exclusion content key ---------------------------------------------

def _content_key(text: str) -> str:
    """Stable content fingerprint for self-exclusion. Whitespace-normalized exact text (leading/trailing
    stripped, internal runs collapsed to single spaces) then SHA-256 hashed. Normalization (not a raw
    byte compare) so a file copy of the target — which `read_text` may give a trailing newline or CRLF
    that inline `text` lacks — still matches the same target; it is deliberately conservative (only
    collapses whitespace) so genuinely distinct family docs are never coincidentally excluded."""
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ---- reference loading -------------------------------------------------------

def _load_family_dir(root: Path) -> dict[str, list[tuple[str, Path]]]:
    """Group `root/<family>/<files>` into {family: [(text, resolved_path), ...]}.

    The flat idiolect_detector.directory_entries loader does NOT group by subdir, so this is a NEW
    loader (spec P3): each immediate subdirectory of `root` is a family; every .txt/.md file under it
    (recursive) is a doc of that family. Raises FileNotFoundError when `root` is not a directory (the
    caller maps that to bad_input). A non-UTF-8 / unreadable doc raises UnicodeDecodeError/OSError,
    which the caller also maps to bad_input rather than tracebacking (#225/#226)."""
    if not root.is_dir():
        raise FileNotFoundError(f"--reference-dir is not a directory: {root}")
    families: dict[str, list[tuple[str, Path]]] = {}
    for fam_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        docs: list[tuple[str, Path]] = []
        for fp in sorted(x for x in fam_dir.rglob("*") if x.is_file()):
            if fp.suffix.lower() not in (".txt", ".md"):
                continue
            text = fp.read_text(encoding="utf-8")  # strict — non-UTF-8 -> UnicodeDecodeError -> bad_input
            docs.append((text, fp.resolve()))
        if docs:
            families[fam_dir.name] = docs
    return families


def _load_family_manifest(path: Path) -> dict[str, list[tuple[str, Path | None]]]:
    """Group a JSONL manifest `{family, text|text_path}` into {family: [(text, resolved_path|None)]}.

    Inline `text` carries a None path (path-based self-exclusion cannot apply, but the caller still
    excludes it by CONTENT — see `_content_key` / the self-exclusion loop in `_run`); `text_path`/`path`
    is resolved relative to the manifest's directory. Robust loading: a blank line is skipped; a malformed-JSON row
    or a valid-JSON-but-non-object row raises ValueError (mapped to bad_input — a structurally broken
    manifest is bad input, not a partial run); a referenced file that is missing/non-UTF-8 raises
    FileNotFoundError/UnicodeDecodeError (bad_input). A row missing `family` raises ValueError."""
    families: dict[str, list[tuple[str, Path | None]]] = {}
    base = path.resolve().parent
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"manifest line {line_no}: invalid JSON ({e})") from e
        if not isinstance(row, dict):
            raise ValueError(f"manifest line {line_no}: row is not a JSON object")
        family = row.get("family")
        if not isinstance(family, str) or not family:
            raise ValueError(f"manifest line {line_no}: missing/empty 'family'")
        if isinstance(row.get("text"), str):
            families.setdefault(family, []).append((row["text"], None))
            continue
        rel = row.get("text_path") or row.get("path")
        if not rel:
            raise ValueError(
                f"manifest line {line_no}: row has neither inline 'text' nor 'text_path'/'path'"
            )
        if not isinstance(rel, str):
            # Guard the file-pointer field exactly like 'family'/'text': a truthy non-string
            # 'text_path'/'path' (int/list/dict/bool) would make `base / rel` raise TypeError,
            # which is NOT caught by _run's (OSError, UnicodeDecodeError, ValueError) handler and
            # would escape as a traceback. A structurally broken pointer is bad input.
            raise ValueError(
                f"manifest line {line_no}: 'text_path'/'path' must be a string, got "
                f"{type(rel).__name__}"
            )
        fp = (base / rel)
        text = fp.read_text(encoding="utf-8")  # missing -> FileNotFoundError; non-UTF-8 -> UnicodeDecodeError
        families.setdefault(family, []).append((text, fp.resolve()))
    return families


# ---- CLI --------------------------------------------------------------------

def _run(args: argparse.Namespace) -> dict[str, Any]:
    target_path = Path(args.target)
    try:
        target_text = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=f"cannot read --target: {e}", reason_category="bad_input",
        )

    try:
        if args.reference_dir:
            loaded = _load_family_dir(Path(args.reference_dir))
        else:
            loaded = _load_family_manifest(Path(args.reference_manifest))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        which = "--reference-dir" if args.reference_dir else "--reference-manifest"
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=f"cannot load {which}: {e}", reason_category="bad_input",
        )

    if not loaded:
        which = "--reference-dir" if args.reference_dir else "--reference-manifest"
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=f"{which} produced no reference families/documents", reason_category="bad_input",
        )

    # Self-exclusion (general_imposters pattern): the target must never match against itself. This holds
    # for BOTH manifest input forms:
    #   - a reference doc whose resolved PATH is the target file (a file copy / the target listed in its
    #     own family), and
    #   - a reference doc whose CONTENT is the target (a `text_path` to a different file holding an exact
    #     copy, OR — the case the path-only check missed — an inline `text` row, which carries path=None
    #     and would otherwise ALWAYS be retained; repeating the exact target inline N times under one
    #     family yields a zero-distance centroid and could flip attribution_available=True, defeating the
    #     self-exclusion guarantee, #255 P1).
    # Content match uses a whitespace-normalized SHA-256 of the target, so a path drop and a content drop
    # of the same doc are not double-counted (path is checked first).
    try:
        target_abs = target_path.resolve()
    except OSError:
        target_abs = None
    target_key = _content_key(target_text)
    n_dropped_self = 0
    families_kept: dict[str, list[tuple[str, Path | None]]] = {}
    for family, docs in loaded.items():
        kept: list[tuple[str, Path | None]] = []
        for text, pth in docs:
            if target_abs is not None and pth is not None and pth == target_abs:
                n_dropped_self += 1
                continue
            if _content_key(text) == target_key:
                n_dropped_self += 1
                continue
            kept.append((text, pth))
        if kept:
            families_kept[family] = kept

    # Per-doc length floor: drop docs below --min-words; a doc this short has unstable stylometry.
    warnings: list[str] = []
    n_dropped_short = 0
    families_features: dict[str, list[dict[str, float]]] = {}
    for family, docs in families_kept.items():
        feats_list: list[dict[str, float]] = []
        for text, _pth in docs:
            if len(split_words(text)) < args.min_words:
                n_dropped_short += 1
                continue
            feats_list.append(_extract_features(text))
        if feats_list:
            families_features[family] = feats_list

    if not families_features:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=(
                "no reference family retained any document after self-exclusion and the "
                f"--min-words {args.min_words} length floor"
            ),
            reason_category="bad_input",
        )

    target_feats = _extract_features(target_text)
    target_words = len(split_words(target_text))

    try:
        results = rank_families(
            target_feats, families_features,
            ood_k=args.ood_k, margin_threshold=args.margin, min_docs=args.min_docs,
        )
    except ValueError as e:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=str(e), reason_category="bad_input",
        )

    results["target_words"] = target_words
    results["spacy_available"] = HAS_SPACY

    # Target length floor: the SAME --min-words floor applied to reference docs guards the TARGET too.
    # Stylometric features (MATTR/MTLD/burstiness/fwr) on a sub-floor target are unstable, so a too-short
    # target can NEVER reach attribution_available=True — it is forced to abstain with an explicit reason
    # (the ranking stays as raw evidence, mirroring the abstention-first posture). The advertised
    # length_floor_words guards the input being judged, not only the references.
    if target_words < args.min_words:
        too_short = (
            f"the --target is {target_words} words, below the --min-words {args.min_words} length floor "
            "— stylometric features are unstable on so short a target, so attribution is withheld and the "
            "ranking is raw evidence only"
        )
        if results["attribution_available"]:
            results["attribution_available"] = False
            results["reason"] = too_short
        else:
            results["reason"] = f"{too_short}; {results['reason']}"
        results["target_below_min_words"] = True
        warnings.append(too_short)

    if n_dropped_self:
        warnings.append(
            f"dropped {n_dropped_self} reference doc(s) matching --target by path or exact "
            "(whitespace-normalized) content (self-exclusion); the target does not match against itself, "
            "including inline-text copies"
        )
    if n_dropped_short:
        warnings.append(
            f"dropped {n_dropped_short} reference doc(s) below the --min-words {args.min_words} floor"
        )
    if not HAS_SPACY:
        warnings.append(
            "spaCy is unavailable — mean_dependency_distance is dropped for EVERY doc (target + all "
            "references); the comparison subspace is the stdlib features only"
        )
    if not results["attribution_available"]:
        warnings.append(
            "attribution_available is False — the family_ranking is raw evidence only, NOT an "
            f"attribution: {results['reason']}"
        )

    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=str(target_path), target_words=target_words,
        baseline={
            "reference": args.reference_dir or args.reference_manifest,
            "n_families": results["n_families"],
            "n_reference_docs": sum(len(v) for v in families_features.values()),
        },
        results=results, claim_license=_claim_license(),
        warnings=warnings or None,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--target", required=True, help="Path to the target text (UTF-8).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--reference-manifest",
        help="JSONL manifest of the reference pool: one object per line, {family, text|text_path}.",
    )
    g.add_argument(
        "--reference-dir",
        help="Directory whose immediate subdirs are families: <family>/<files>.txt|.md (recursive).",
    )
    ap.add_argument(
        "--ood-k", type=float, default=DEFAULT_OOD_K,
        help=(
            f"Relative-OOD multiplier: abstain when dist_to_top > k * within_scatter_top "
            f"(PROVISIONAL, default {DEFAULT_OOD_K})."
        ),
    )
    ap.add_argument(
        "--margin", type=float, default=DEFAULT_MARGIN,
        help=f"Top-2 similarity margin below which the ranking is ambiguous (PROVISIONAL, default {DEFAULT_MARGIN}).",
    )
    ap.add_argument(
        "--min-docs", type=int, default=MIN_DOCS_PER_FAMILY,
        help=(
            f"Minimum docs per family; a thinner family forces abstention (default {MIN_DOCS_PER_FAMILY}). "
            f"This is a HARD floor: an operator may RAISE it but never lower it below "
            f"{MIN_DOCS_PER_FAMILY} (the small-n over-claim protection cannot be opted out of); a smaller "
            f"value is clamped up to {MIN_DOCS_PER_FAMILY}."
        ),
    )
    ap.add_argument(
        "--min-words", type=int, default=DEFAULT_MIN_WORDS,
        help=f"Per-doc word-count floor; shorter reference docs are dropped (default {DEFAULT_MIN_WORDS}).",
    )
    ap.add_argument("--json", action="store_true", help="Emit the JSON envelope to stdout.")
    ap.add_argument("--out", help="Write the JSON envelope to this path.")
    args = ap.parse_args(argv)

    if args.ood_k <= 0:
        sys.stderr.write("[model_family_attribution] --ood-k must be > 0\n")
        return 2
    if args.min_docs < 1:
        sys.stderr.write("[model_family_attribution] --min-docs must be >= 1\n")
        return 2
    # The MIN_DOCS_PER_FAMILY floor is HARD: the operator may only RAISE it. A smaller value would let a
    # 3-doc family return attribution_available=True, defeating the small-n over-claim protection the
    # spec's P2 rework added — exactly the over-claim axis this surface is built to refuse. Clamp up.
    if args.min_docs < MIN_DOCS_PER_FAMILY:
        sys.stderr.write(
            f"[model_family_attribution] --min-docs {args.min_docs} is below the hard floor "
            f"{MIN_DOCS_PER_FAMILY}; clamping up to {MIN_DOCS_PER_FAMILY}\n"
        )
        args.min_docs = MIN_DOCS_PER_FAMILY

    envelope = _run(args)
    text = json.dumps(envelope, indent=2, sort_keys=True, default=str)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.json or not args.out:
        print(text)
    return 0 if envelope.get("available", True) else 3


if __name__ == "__main__":
    raise SystemExit(main())
