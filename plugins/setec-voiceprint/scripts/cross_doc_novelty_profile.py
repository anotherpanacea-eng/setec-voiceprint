#!/usr/bin/env python3
"""cross_doc_novelty_profile.py — per-feature cross-document novelty profile (spec wave-4, M1).

A **descriptive, per-feature cross-document novelty PROFILE**: for ONE target document against a
supplied reference POOL, report — per reused stylometry feature — how far the target's value sits
from the pool's distribution of that feature (a mean/SD z-position) plus the per-feature mean/SD/n
of the pool. The per-feature distribution + the per-feature position table ARE the read — never a
single "novelty score", never a verdict, never a band.

Feature-wise complement to the cluster-wise ``distinct_diversity_audit`` (that one partitions a SET
into equivalence classes; this one asks, for ONE target vs a pool, *which named stylometric features
make it atypical*). M1 is **model-free** (robust z-scores over the stdlib
``extract_features(include_spacy=False)`` families — the ``style_vectorizer`` precedent), so it is
CI-runnable with torch/transformers/spaCy absent.

Clean-room of the per-feature-vs-population position read from **GENIE: Generative Note Information
Extraction** (arXiv:2606.12790) — which decomposes novelty into named task-specific features —
combined with the no-single-scalar / distribution-is-the-read posture from **NoveltyBench:
Evaluating Language Models for Humanlike Diversity** (arXiv:2504.05228).

Surface: ``set_level_diversity`` (the population-aware surface, matching
``corpus_novelty_audit`` / ``originality_audit`` / ``distinct_diversity_audit``).

Posture: descriptive, no-verdict, anti-Goodhart, ``calibration_status: provisional``.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_schema import build_error_output, build_output  # noqa: E402
from claim_license import from_legacy  # noqa: E402
import stylometry_core as sc  # noqa: E402

TASK_SURFACE = "set_level_diversity"
TOOL_NAME = "cross_doc_novelty_profile"
SCRIPT_VERSION = "1.0"

DEFAULT_LENGTH_FLOOR_WORDS = 100
DEFAULT_MIN_POOL = 5

# FIXED, ordered. orientation is descriptive only (never a verdict).
# Each entry: (feature_id, name, orientation).
# Family-level fixed (the 7 stdlib families from extract_features(include_spacy=False)).
# Per-name axes within each family are derived deterministically from select_feature_names(pool).
NOVELTY_FEATURE_SCHEMA: tuple[tuple[str, str, str], ...] = (
    ("function_words",         "function-word rates",            "two_sided"),
    ("punctuation",            "punctuation rates",              "two_sided"),
    ("paragraph_dialogue",     "paragraph/dialogue structure",   "two_sided"),
    ("pronoun_modal_negation", "pronoun/modal/negation rates",   "two_sided"),
    ("char_ngrams_3",          "char trigram rates",             "two_sided"),
    ("char_ngrams_4",          "char 4-gram rates",              "two_sided"),
    ("char_ngrams_5",          "char 5-gram rates",              "two_sided"),
)
NOVELTY_FAMILY_COUNT = len(NOVELTY_FEATURE_SCHEMA)  # == 7; the count invariant (AC-6)

# Family ids in the schema (grep-stable; no spaCy families).
_SCHEMA_FAMILY_IDS = frozenset(family_id for family_id, _, _ in NOVELTY_FEATURE_SCHEMA)

# Orientation descriptions; echoed into assumptions.
_SCHEMA_ORIENTATION: dict[str, str] = {
    family_id: orientation for family_id, _, orientation in NOVELTY_FEATURE_SCHEMA
}


# ---- reference pool loading (clean-room-copied from originality_audit.py:51-91) ----

def _load_reference_dir(root: Path,
                        suffixes: tuple[str, ...] = (".txt", ".md"),
                        ) -> list[tuple[str, str, Path | None]]:
    """(source, text, resolved_path) for every text file under `root` (recursive).

    Clean-room copy of originality_audit._load_reference_dir (originality_audit.py:51-58).
    The 3-tuple contract: source = relative posix path, text = content, resolved_path = resolved.
    """
    out: list[tuple[str, str, Path | None]] = []
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        if p.suffix.lower() in suffixes:
            out.append((p.relative_to(root).as_posix(),
                        p.read_text(encoding="utf-8", errors="replace"), p.resolve()))
    return out


def _load_reference_manifest(path: Path) -> list[tuple[str, str, Path | None]]:
    """(source, text, resolved_path) from a JSONL manifest.

    Each row carries inline ``text`` (path None), or a ``text_path``/``path`` resolved relative to
    the manifest dir. Malformed rows are skipped with a stderr note (skip-and-warn).

    Clean-room copy of originality_audit._load_reference_manifest (originality_audit.py:61-91).
    """
    out: list[tuple[str, str, Path | None]] = []
    base = path.resolve().parent
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"  manifest line {line_no}: {e}; skipping\n")
            continue
        if not isinstance(row, dict):
            sys.stderr.write(f"  manifest line {line_no}: not a JSON object; skipping\n")
            continue
        src = str(row.get("id") or row.get("path") or row.get("text_path") or f"line{line_no}")
        if isinstance(row.get("text"), str):
            out.append((src, row["text"], None))
            continue
        rel = row.get("text_path") or row.get("path")
        if rel:
            fp = base / rel
            if fp.is_file():
                out.append((src, fp.read_text(encoding="utf-8", errors="replace"), fp.resolve()))
            else:
                sys.stderr.write(f"  manifest line {line_no}: {fp} not found; skipping\n")
    return out


# ---- z-position helpers --------------------------------------------------------

def _z_position(value: float, mean: float, sd: float) -> float | None:
    """Mean/SD z-position. Returns None when sd == 0 (degenerate — never NaN).

    Mirrors style_vectorizer.py:135-145: sd == 0 → z = None, no division by zero, never NaN.
    """
    if sd > 0.0:
        return (value - mean) / sd
    return None


# ---- per-family |z| distribution summary (7-key block) -------------------------

def _abs_z_distribution(abs_z_values: list[float]) -> dict[str, Any]:
    """7-key distribution block (n/mean/sd/min/p10/p50/p90) over a list of |z| values.

    Mirrors the homogeneity_audit / corpus_novelty_audit distribution-summary shape.
    None entries (from sd==0 pool features) are dropped before the statistics.
    """
    vals = sorted(abs_z_values)
    n = len(vals)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "min": None, "p10": None, "p50": None, "p90": None}

    def _percentile(data: list[float], p: float) -> float:
        """Linear-interpolation percentile on sorted data."""
        if len(data) == 1:
            return data[0]
        idx = p / 100.0 * (len(data) - 1)
        lo = int(idx)
        hi = min(lo + 1, len(data) - 1)
        frac = idx - lo
        return data[lo] + frac * (data[hi] - data[lo])

    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / n) if n > 1 else 0.0

    return {
        "n": n,
        "mean": mean,
        "sd": sd,
        "min": vals[0],
        "p10": _percentile(vals, 10),
        "p50": _percentile(vals, 50),
        "p90": _percentile(vals, 90),
    }


# ---- core audit logic ----------------------------------------------------------

def audit_novelty_profile(
    target_text: str,
    pool: list[tuple[str, str, Path | None]],
    target_path: Path | None = None,
    *,
    length_floor_words: int = DEFAULT_LENGTH_FLOOR_WORDS,
    min_pool: int = DEFAULT_MIN_POOL,
) -> dict[str, Any]:
    """Compute the per-feature cross-document novelty profile.

    Returns a dict with keys:
      n_pool, target_words, per_feature, per_family_summary,
      target_only_features, assumptions.

    Raises ValueError on bad inputs (empty pool after floor, target below floor, etc.).
    Does NOT include self-exclusion; the caller applies it first.
    """
    # Extract target features (M1: forced include_spacy=False, model-free).
    target_extracted = sc.extract_features(target_text, include_spacy=False)
    target_features: dict[str, dict[str, float]] = target_extracted["features"]
    target_words: int = int(target_extracted["summary"].get("n_words", 0) or 0)

    if target_words < length_floor_words:
        raise ValueError(
            f"target has {target_words} words, below --length-floor-words {length_floor_words}"
        )

    # Filter pool docs by length floor; drop below-floor docs with a stderr note.
    usable_pool: list[tuple[str, str, Path | None]] = []
    for src, text, rpath in pool:
        feat = sc.extract_features(text, include_spacy=False)
        nw = int(feat["summary"].get("n_words", 0) or 0)
        if nw < length_floor_words:
            sys.stderr.write(
                f"  [cross_doc_novelty_profile] pool doc '{src}' has {nw} words "
                f"(below --length-floor-words {length_floor_words}); dropping\n"
            )
        else:
            usable_pool.append((src, text, rpath))

    n_pool = len(usable_pool)
    if n_pool < min_pool:
        raise ValueError(
            f"usable pool has {n_pool} document(s) (after length-floor filter); "
            f"need at least --min-pool {min_pool}"
        )

    # Extract features for all pool docs (forced include_spacy=False).
    pool_extracted: list[dict[str, Any]] = []
    for _src, text, _rpath in usable_pool:
        pool_extracted.append(sc.extract_features(text, include_spacy=False))

    # Fixed axis list from the pool (pool's coordinate system; target placed into it).
    # Only the schema families participate (no spaCy families — they cannot appear since
    # include_spacy=False, but we enforce the schema boundary explicitly).
    selected: dict[str, list[str]] = sc.select_feature_names(pool_extracted)
    # Restrict to schema families only (defensive; include_spacy=False makes this a no-op in
    # practice, but the spec requires the schema boundary to be explicit).
    selected = {fam: names for fam, names in selected.items() if fam in _SCHEMA_FAMILY_IDS}

    # Per-family pool vectors and stats.
    family_stats: dict[str, dict[str, dict[str, float]]] = {}
    for family, names in selected.items():
        vectors = [sc.feature_vector(item, family, names) for item in pool_extracted]
        family_stats[family] = sc.vector_stats(vectors, names)

    # Build per_feature rows (sorted by family then name — NOT by |z|, per spec).
    # The sort is (family, name) alphabetically — deterministic, never |z|-ranked.
    per_feature: list[dict[str, Any]] = []
    for family_id in sorted(selected.keys()):  # alphabetical family order
        if family_id not in _SCHEMA_FAMILY_IDS:
            continue
        names = selected[family_id]
        stats = family_stats.get(family_id, {})
        target_fam = target_features.get(family_id, {})
        for name in sorted(names):  # alphabetical within-family order
            st = stats.get(name, {"mean": 0.0, "sd": 0.0, "n": 0})
            pool_mean = float(st["mean"])
            pool_sd = float(st["sd"])
            value = float(target_fam.get(name, 0.0))
            z = _z_position(value, pool_mean, pool_sd)
            per_feature.append({
                "family": family_id,
                "feature_id": f"{family_id}.{name}",
                "name": name,
                "value": value,
                "pool_mean": pool_mean,
                "pool_sd": pool_sd,
                "z": z,
                "n_pool_obs": int(st["n"]),
            })

    # Build per_family_summary (|z| distribution over each family's rows).
    # Order follows the per_feature alphabetical family order for consistency.
    _schema_info: dict[str, tuple[str, str]] = {
        family_id: (family_name, orientation)
        for family_id, family_name, orientation in NOVELTY_FEATURE_SCHEMA
    }
    per_family_summary: list[dict[str, Any]] = []
    for family_id in sorted(selected.keys()):
        if family_id not in _SCHEMA_FAMILY_IDS:
            continue
        family_name, orientation = _schema_info.get(family_id, (family_id, "two_sided"))
        family_rows = [r for r in per_feature if r["family"] == family_id]
        abs_z_vals = [abs(r["z"]) for r in family_rows if r["z"] is not None]
        per_family_summary.append({
            "family": family_id,
            "family_name": family_name,
            "orientation": orientation,
            "n_axes": len(family_rows),
            "abs_z_distribution": _abs_z_distribution(abs_z_vals),
        })

    # target_only_features: names in target absent from the pool's axis list (never z-scored).
    target_only_features: dict[str, Any] = {}
    for family_id in _SCHEMA_FAMILY_IDS:
        target_fam = target_features.get(family_id, {})
        pool_names = set(selected.get(family_id, []))
        only_names = sorted(k for k in target_fam if k not in pool_names)
        if only_names:
            if family_id not in target_only_features:
                target_only_features[family_id] = {"count": 0, "names": []}
            target_only_features[family_id]["count"] = len(only_names)
            target_only_features[family_id]["names"] = only_names
    target_only_count = sum(v["count"] for v in target_only_features.values())

    assumptions: dict[str, Any] = {
        "method": (
            "GENIE per-feature-vs-population position (arXiv:2606.12790), model-free stylometric lens: "
            "for each named stdlib feature, compute a z-position of the target's value relative to the "
            "pool's mean/SD distribution. The per-feature distribution + position table ARE the read."
        ),
        "lens": "stylometric",
        "include_spacy": False,
        "feature_families": [family_id for family_id, _, _ in NOVELTY_FEATURE_SCHEMA],
        "NOVELTY_FAMILY_COUNT": NOVELTY_FAMILY_COUNT,
        "stat": "mean_sd_z",
        "stat_note": (
            "M1 uses mean/SD z only. Robust median/MAD z is the deferred M2 upgrade (needs "
            "safe_median + mad in stylometry_core under the drift gate) — NOT emitted in M1."
        ),
        "orientation": {
            family_id: orientation for family_id, _, orientation in NOVELTY_FEATURE_SCHEMA
        },
        "orientation_note": (
            "every family is two_sided: a feature far from the pool in either direction is 'atypical'; "
            "there is intentionally no gt = better orientation, because 'more novel' is NOT 'better' "
            "and 'less novel' is NOT 'derivative/AI'"
        ),
        "self_excluded_note": (
            "a doc never positions itself against the pool; any pool entry whose resolved path "
            "equals the target's resolved path is dropped before the distribution is built"
        ),
        "length_floor_words": length_floor_words,
        "min_pool": min_pool,
        "confounds": (
            "a tight or single-source pool inflates apparent atypicality; a broad pool flattens it; "
            "a templated genre or house style lowers apparent novelty legitimately; "
            "ESL/dialect is NOT adjudicated; "
            "the operator owns pool composition and prompt-matching"
        ),
        "pool_dependence": (
            "z-position is pool-relative — a different pool yields different z-scores for the same target"
        ),
        "no_band": (
            "no absolute band is emitted; thresholds are operator-side / PROVISIONAL"
        ),
        "paper_lens_incomparable": (
            "GENIE (arXiv:2606.12790) uses learned task-specific features; this M1 lens uses fixed "
            "stdlib stylometry families (function_words / punctuation / paragraph_dialogue / "
            "pronoun_modal_negation / char_ngrams_3/4/5) — the two lenses are NOT comparable; "
            "GENIE's learned task-feature figures do NOT transfer to this stdlib read"
        ),
        "calibration_status": "provisional",
        "target_only_features_note": (
            f"{target_only_count} named feature(s) are present in the target but absent from the "
            "pool's axis list — they are un-pooled and reported in target_only_features, never z-scored"
        ),
    }

    return {
        "n_pool": n_pool,
        "target_words": target_words,
        "per_feature": per_feature,
        "per_family_summary": per_family_summary,
        "target_only_features": target_only_features,
        "assumptions": assumptions,
    }


def _claim_license() -> dict[str, Any]:
    return {
        "licenses": (
            "the per-feature position of ONE target within a supplied reference pool's distribution, "
            "over the fixed reused stdlib stylometry feature families: per named feature a pool mean/SD "
            "and a z-position, plus a per-family |z| distribution and the un-pooled target-only "
            "features. A descriptive measurement of where the target sits relative to THIS pool."
        ),
        "does_not_license": (
            "Any AI/human/authorship determination — a per-feature z-position is a property of the "
            "target relative to THIS pool, not a provenance call. "
            "Low novelty is not an AI/human/derivative determination: "
            "a tight or single-source pool, a shared genre/register, a templated form, or a house style "
            "all lower apparent novelty legitimately; high novelty is NOT 'human'. "
            "No plagiarism, derivative-work, or copyright claim — this is a stylometric position, not a "
            "legal claim. "
            "No selection / ranking-as-decision of features or documents (the table is read by a human, "
            "never an automated filter; rows are a complete per-axis list, not a pick). "
            "No band and no single 'novelty score' is emitted; thresholds are operator-side / "
            "PROVISIONAL. "
            "The stylometric-lens position is NOT comparable to GENIE's learned task-feature figures, "
            "and an LLM-judge novelty tier is refused. "
            "The surface emits no verdict."
        ),
    }


# ---- M2 seam (lazy-import + fail-loud; NOT in this build) ----------------------

def _embedding_lens_unavailable() -> dict[str, Any]:
    """M2 --lens embedding: this M1 build wires NO real embedding lens, so the embedding lens ALWAYS
    fails loud here — whether or not an embedding module happens to be importable. It NEVER silently
    falls back to the stylometric lens (a silent fallback would change the meaning of "novelty").

    Always returns a non-empty error block in this build (so the ``if err:`` guard always fires for
    ``embedding``). Both branches use ``reason_category: missing_dependency`` — the missing thing is
    a *wired* embedding lens, absent in M1 either way.

    Mirrors distinct_diversity_audit._model_lens_unavailable (the AC-16 pattern).
    """
    try:
        import authorship_embedding  # type: ignore  # noqa: F401
    except ImportError:
        return {
            "reason": (
                "--lens embedding requires an authorship embedding client (authorship_embedding), "
                "which is not installed; the embedding lens fails loud rather than silently falling back "
                "to the stylometric lens (which answers a different question). "
                "Use --lens stylometric (default)."
            ),
            "reason_category": "missing_dependency",
        }
    # authorship_embedding is importable, but M1 has wired no real embedding lens to it.
    # Fail loud rather than fall through to the stylometric lens — see distinct_diversity_audit.py
    # _model_lens_unavailable docstring (the planted false-invariant guard).
    return {
        "reason": (
            "--lens embedding is a POC-gated M2 seam not wired in this build: an authorship_embedding "
            "module is importable, but no embedding lens has been connected to it, so the embedding "
            "lens fails loud rather than silently falling back to the stylometric lens (which answers a "
            "different question). Use --lens stylometric (default)."
        ),
        "reason_category": "missing_dependency",
    }


# ---- CLI run logic -------------------------------------------------------------

def _run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.target:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            reason="--target is required", reason_category="bad_input")

    if not (args.reference_dir or args.reference_manifest):
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            reason="one of --reference-dir or --reference-manifest is required",
            reason_category="bad_input")

    if args.lens == "embedding":
        err = _embedding_lens_unavailable()
        if err:
            return build_error_output(
                task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
                target_path=args.target,
                reason=err["reason"],
                reason_category=err["reason_category"])

    # Load target text.
    target_path = Path(args.target)
    try:
        target_text = target_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as e:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=f"cannot read --target: {e}", reason_category="bad_input")

    target_resolved = target_path.resolve()

    # Load reference pool.
    try:
        if args.reference_dir:
            pool = _load_reference_dir(Path(args.reference_dir))
            pool_ref = args.reference_dir
        else:
            pool = _load_reference_manifest(Path(args.reference_manifest))
            pool_ref = args.reference_manifest
    except (OSError, UnicodeDecodeError) as e:
        which = "--reference-dir" if args.reference_dir else "--reference-manifest"
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=f"cannot read {which}: {e}", reason_category="bad_input")

    # Self-exclusion: drop any pool entry whose resolved_path == target's resolved_path.
    # Apply .resolve() on BOTH sides. Only fires when both paths are non-None
    # (an inline-text pool entry has resolved_path = None and is never self-excluded).
    # Mirrors corpus_novelty_audit.py:118-121 guard.
    self_excluded = 0
    filtered_pool: list[tuple[str, str, Path | None]] = []
    for src, text, rpath in pool:
        if rpath is not None and rpath.resolve() == target_resolved:
            self_excluded += 1
        else:
            filtered_pool.append((src, text, rpath))

    try:
        results = audit_novelty_profile(
            target_text,
            filtered_pool,
            target_path=target_resolved,
            length_floor_words=args.length_floor_words,
            min_pool=args.min_pool,
        )
    except ValueError as e:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=str(e), reason_category="bad_input")

    # Attach self_excluded to assumptions (the honesty pattern).
    results["assumptions"]["self_excluded"] = self_excluded

    # Pool stats for baseline block.
    pool_words = sum(
        len(text.split()) for _, text, _ in filtered_pool
    )

    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=str(target_path),
        target_words=results["target_words"],
        baseline={
            "pool": pool_ref,
            "n_pool": results["n_pool"],
            "n_files": results["n_pool"],
            "words": pool_words,
        },
        results=results,
        claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--target", required=False,
                    help="Path to the single target document to profile.")
    ref_grp = ap.add_mutually_exclusive_group()
    ref_grp.add_argument("--reference-dir",
                         help="Directory of reference pool documents (.txt/.md, recursive).")
    ref_grp.add_argument("--reference-manifest",
                         help="JSONL manifest of the reference pool (id + text|text_path).")
    ap.add_argument("--length-floor-words", type=int, default=DEFAULT_LENGTH_FLOOR_WORDS,
                    help=f"Minimum word count for the target and each pool doc "
                         f"(default {DEFAULT_LENGTH_FLOOR_WORDS}); a documented starting point, "
                         "not a calibrated cut. Pool docs below the floor are dropped.")
    ap.add_argument("--min-pool", type=int, default=DEFAULT_MIN_POOL,
                    help=f"Minimum usable pool size after self-exclusion and length-floor filtering "
                         f"(default {DEFAULT_MIN_POOL}); abstain with bad_input below this.")
    ap.add_argument("--lens", choices=["stylometric", "embedding"], default="stylometric",
                    help="Feature lens. M1 ships only the model-free stylometric lens; "
                         "embedding is a POC-gated M2 seam (fails loud, not in this build).")
    ap.add_argument("--json", action="store_true",
                    help="Emit the JSON envelope to stdout.")
    ap.add_argument("--out",
                    help="Write the JSON envelope to this path.")
    args = ap.parse_args(argv)

    if args.min_pool < 2:
        sys.stderr.write(
            "[cross_doc_novelty_profile] --min-pool must be >= 2 "
            "(a per-feature distribution needs at least two pool documents)\n"
        )
        return 2

    envelope = _run(args)
    text = json.dumps(envelope, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.json or not args.out:
        print(text)
    return 0 if envelope.get("available", True) else 3


if __name__ == "__main__":
    raise SystemExit(main())
