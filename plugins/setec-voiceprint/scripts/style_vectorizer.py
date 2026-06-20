#!/usr/bin/env python3
"""style_vectorizer.py — an interpretable (glass-box) document vectorizer (spec 30).

gram2vec (arXiv:2406.12131) is a document vectorizer in which **every dimension is a
human-named stylometric feature** — function words, character n-grams, punctuation,
paragraph/dialogue shape, pronoun/modal/negation rates. The vector is *readable*: you can
point at a coordinate and say "this is the rate of the function word *the*". This surface
emits that named-feature vector as a first-class, descriptive artifact, with a deterministic
family-then-name ordering so two documents land in the same coordinate space and a human can
read the difference dimension by dimension.

It is **a vectorizer, not a classifier**. There is no aggregate scalar at all (no overall
distance, no score) — so there is structurally nothing to threshold or rank on. The optional
``--baseline-dir`` / ``--manifest`` mode adds a per-dimension reference distribution and a
PROVISIONAL descriptive band (mean ± k·sd); it is illustrative, not a verdict, and the
claim-license refuses the authorship / same-author / quality / classifier-target readings.

M1 (this surface): the six stdlib feature families, computed via
``stylometry_core.extract_features(include_spacy=False)`` — reused verbatim, no new feature
math. ``include_spacy=False`` is forced so the default invocation is model-free even when
spaCy is installed (CI-runnable). M2 (follow-up) adds the two spaCy-gated families
(``pos_trigrams`` / ``dependency_ngrams``) behind a ``--with-spacy`` flag.

Posture (no verdict): not authorship/AI, not same-author, not a quality/readability score,
and not endorsed as a classifier/selection/training target. Held-out disjoint: a baseline
must not contain the target (the surface warns if it does).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from output_schema import build_error_output, build_output  # noqa: E402
from claim_license import from_legacy  # noqa: E402
import stylometry_core as sc  # noqa: E402

TASK_SURFACE = "voice_coherence"
TOOL_NAME = "style_vectorizer"
SCRIPT_VERSION = "1.0"

#: Provisional band half-width (mean ± k·sd). Illustrative only — pinned by a future
#: per-register calibration study, never read as a verdict (spec §3 point 3).
DEFAULT_K_SD = 2.0

#: The flat-dim join. ``::`` is collision-free against the family-internal ``:`` that
#: char-ngram feature names already carry (e.g. ``char_ngrams_3::ch3:th``) because no
#: family prefix contains ``::``.
FLAT_DELIM = "::"


def _flat_dim(family: str, name: str) -> str:
    return f"{family}{FLAT_DELIM}{name}"


def _single_mode_feature_space(features: dict[str, dict[str, float]]) -> dict[str, list[str]]:
    """In single mode the axis list is the FULL family inventory of the target itself —
    every named feature each family produces, with NO frequency cap. This preserves the
    glass-box promise (all 135 function words appear, not the top-100 ``select_feature_names``
    would keep). Returns {family: sorted(names)}; empty families are dropped."""
    selected: dict[str, list[str]] = {}
    for family in sorted(features):
        names = sorted(features[family].keys())
        if names:
            selected[family] = names
    return selected


def _build_feature_space(selected: dict[str, list[str]]) -> dict[str, Any]:
    families = {
        family: {"n": len(names), "names": list(names)}
        for family, names in sorted(selected.items())
    }
    total = sum(len(names) for names in selected.values())
    return {
        "families": families,
        "total_dimensions": total,
        "ordering": "family then feature name, both sorted — deterministic",
    }


def _build_vector(
    features: dict[str, dict[str, float]], selected: dict[str, list[str]],
) -> tuple[dict[str, dict[str, float]], list[dict[str, Any]]]:
    """The document's coordinate, BY NAME (per family) and flattened in the deterministic
    family-then-name order. A name absent from the target's own features reads 0.0 (only
    possible in baseline_relative mode, where the axis list comes from the baseline)."""
    vector: dict[str, dict[str, float]] = {}
    flat: list[dict[str, Any]] = []
    for family in sorted(selected):
        names = selected[family]
        fam_feats = features.get(family, {})
        vector[family] = {name: float(fam_feats.get(name, 0.0)) for name in names}
        for name in names:  # names already sorted in `selected`
            flat.append({"dim": _flat_dim(family, name), "value": float(fam_feats.get(name, 0.0))})
    return vector, flat


def _baseline_reference(
    target_features: dict[str, dict[str, float]],
    selected: dict[str, list[str]],
    baseline_features: list[dict[str, Any]],
    *,
    k_sd: float,
    n_baseline_files: int,
    n_baseline_words: int,
) -> dict[str, Any]:
    """Per-dimension reference distribution (mean/sd/n) over the SAME named axes, plus a
    signed standardized position ``z`` and a PROVISIONAL descriptive band. ``sd==0`` yields
    ``z=None`` (no division by zero, never ``nan``) and ``band="within"`` (a degenerate
    reference can't place the value outside itself). The band is mean ± k·sd; it is
    illustrative, not a verdict (spec §3 point 3)."""
    rows: list[dict[str, Any]] = []
    for family in sorted(selected):
        names = selected[family]
        fam_target = target_features.get(family, {})
        # vector_stats reuses stylometry_core's mean/sd helpers over the baseline vectors
        # of this family, restricted to the shared axis names.
        baseline_vectors = [
            sc.feature_vector(item, family, names) for item in baseline_features
        ]
        stats = sc.vector_stats(baseline_vectors, names)
        for name in names:
            value = float(fam_target.get(name, 0.0))
            st = stats.get(name, {"mean": 0.0, "sd": 0.0, "n": 0})
            mean = float(st["mean"])
            sd = float(st["sd"])
            if sd > 0.0:
                z: float | None = (value - mean) / sd
                if value > mean + k_sd * sd:
                    band = "above"
                elif value < mean - k_sd * sd:
                    band = "below"
                else:
                    band = "within"
            else:
                z = None
                band = "within"
            rows.append({
                "dim": _flat_dim(family, name),
                "value": value,
                "baseline_mean": mean,
                "baseline_sd": sd,
                "z": z,
                "band": band,
                "band_note": sc.PROVISIONAL_BAND_NOTE,
            })
    # Sort by |z| desc for readability; None (sd==0) last. Pure ordering — carries no
    # selection semantics (the rows are a complete per-dimension list, not a pick).
    rows.sort(key=lambda r: (r["z"] is None, -abs(r["z"]) if r["z"] is not None else 0.0))
    return {
        "per_dimension": rows,
        "calibration_status": "provisional",
        "k_sd": float(k_sd),
        "n_baseline_files": int(n_baseline_files),
        "n_baseline_words": int(n_baseline_words),
    }


def vectorize(
    text: str,
    *,
    baseline_dir: str | None = None,
    manifest: str | None = None,
    k_sd: float = DEFAULT_K_SD,
    target_path: str | None = None,
) -> tuple[dict[str, Any], list[str], int]:
    """Vectorize ``text`` into the named-feature envelope. Returns (results, warnings,
    target_words).

    M1 forces ``include_spacy=False`` so the vector is the six stdlib families regardless of
    whether spaCy is installed. With a baseline, the axis list is fixed by
    ``select_feature_names`` over the baseline (shared coordinate space + DEFAULT_LIMITS caps)
    and a per-dimension reference distribution + provisional band is added; without one, the
    axis list is the target's FULL family inventory (no cap)."""
    target_extracted = sc.extract_features(text, include_spacy=False)
    target_features: dict[str, dict[str, float]] = target_extracted["features"]
    target_summary = target_extracted["summary"]
    target_words = int(target_summary.get("n_words", 0) or 0)
    warnings: list[str] = []

    if baseline_dir or manifest:
        mode = "baseline_relative"
        baseline_entries = sc.load_entries(baseline_dir=baseline_dir, manifest=manifest)
        baseline_features = sc.extract_entry_features(baseline_entries, include_spacy=False)
        # Held-out disjoint guard (anti-Goodhart): the target must not be a member of the
        # baseline corpus, or the band would be a self-comparison.
        warnings.extend(_held_out_warnings(target_path, baseline_features))
        # Shared axis list fixed by the baseline (DEFAULT_LIMITS caps apply here on purpose).
        selected = sc.select_feature_names(baseline_features)
        warnings.extend(
            sc.comparison_warnings(target_summary, baseline_entries, baseline_features)
        )
        feature_space = _build_feature_space(selected)
        vector, vector_flat = _build_vector(target_features, selected)
        n_baseline_words = sum(
            int(item.get("summary", {}).get("n_words", 0) or 0) for item in baseline_features
        )
        baseline_reference = _baseline_reference(
            target_features, selected, baseline_features,
            k_sd=k_sd,
            n_baseline_files=len(baseline_features),
            n_baseline_words=n_baseline_words,
        )
    else:
        mode = "single"
        # Full target inventory — no cap (glass-box promise; spec §2 + §6.6).
        selected = _single_mode_feature_space(target_features)
        warnings.extend(_length_warnings(target_summary))
        feature_space = _build_feature_space(selected)
        vector, vector_flat = _build_vector(target_features, selected)
        baseline_reference = None

    results: dict[str, Any] = {
        "mode": mode,
        "feature_space": feature_space,
        "vector": vector,
        "vector_flat": vector_flat,
        "assumptions": {
            "method": "interpretable named-feature vectorization (gram2vec; arXiv:2406.12131)",
            "feature_builders": "reused verbatim from stylometry_core.extract_features "
                                "(stdlib families in M1; include_spacy=False)",
            "no_verdict": "descriptive coordinate; no authorship/AI label, no threshold "
                          "crossing, no selection scalar, no aggregate distance",
            "feature_space_source": (
                "single mode = full target inventory (no cap); baseline_relative = "
                "select_feature_names over the baseline (DEFAULT_LIMITS caps apply)"
            ),
            "length_sensitivity": "char-ngram and function-word rates are length-sensitive "
                                  "below ~1000 words",
            "ordering": "family-then-name sorted; identical across documents compared in one run",
        },
    }
    if baseline_reference is not None:
        results["baseline_reference"] = baseline_reference
    return results, warnings, target_words


def _length_warnings(target_summary: dict[str, Any]) -> list[str]:
    """Reuse the comparison_warnings length thresholds (500 / 1000) for single mode, where
    there is no baseline to feed comparison_warnings directly."""
    words = int(target_summary.get("n_words", 0) or 0)
    if words < 500:
        return ["Target below 500 words; named-feature rates (function words, char n-grams) "
                "are unstable. Read coordinates as description, not verdicts."]
    if words < 1000:
        return ["Target below 1,000 words; character n-grams and function-word rates remain "
                "length-sensitive."]
    return []


def _held_out_warnings(
    target_path: str | None, baseline_features: list[dict[str, Any]],
) -> list[str]:
    """Warn if the target path resolves to a file inside the baseline corpus — the band must
    be read against a HELD-OUT reference, not a self-comparison (spec §6.10)."""
    if not target_path:
        return []
    try:
        target_real = Path(target_path).resolve()
    except OSError:
        return []
    for item in baseline_features:
        bp = item.get("path")
        if not bp:
            continue
        try:
            if Path(bp).resolve() == target_real:
                return ["Target is also a member of the baseline corpus "
                        f"({target_real}); the per-dimension band would be a "
                        "self-comparison. Use a HELD-OUT baseline that excludes the target."]
        except OSError:
            continue
    return []


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "The target document's named stylometric coordinate — the per-family, per-name "
            "vector of function-word / character-n-gram / punctuation / paragraph-dialogue / "
            "pronoun-modal-negation features (every dimension a human-named, directly-counted "
            "feature). With a baseline, a per-dimension reference distribution (mean/sd/n) and "
            "a PROVISIONAL descriptive band (mean ± k·sd). A glass-box description, not a score."
        ),
        "does_not_license": (
            "Any AI/human or authorship verdict; any same-author claim; any writing-quality or "
            "readability judgment; and use as a classifier / selection / training target — the "
            "vector may be CONSUMED by downstream ML, but this surface emits it as description "
            "and does not endorse it as a label or objective. There is no aggregate scalar, so "
            "there is nothing to threshold or rank on. The per-dimension band is illustrative "
            "(calibration_status: provisional), not a decision; thresholds operator-side. "
            "English-tuned axes (non-English text vectorizes but the named axes are not "
            "meaningful); a high-dimensional named vector of a personal corpus is "
            "re-identifying — keep baselines private."
        ),
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    target_path = Path(args.target)
    try:
        text = target_path.read_text(encoding="utf-8")
    except OSError as e:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=f"cannot read target: {e}", reason_category="bad_input")

    try:
        results, warnings, target_words = vectorize(
            text,
            baseline_dir=args.baseline_dir,
            manifest=args.manifest,
            k_sd=args.k_sd,
            target_path=str(target_path),
        )
    except (ValueError, OSError) as e:
        return build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), reason=str(e), reason_category="bad_input")

    baseline_meta = None
    if results.get("mode") == "baseline_relative":
        ref = results["baseline_reference"]
        baseline_meta = {
            "n_files": ref["n_baseline_files"],
            "words": ref["n_baseline_words"],
        }

    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=str(target_path), target_words=target_words,
        baseline=baseline_meta, results=results,
        claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        warnings=warnings or None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="Path to the target text (UTF-8 prose/markdown).")
    ap.add_argument("--baseline-dir", help="Directory of baseline .txt/.md files "
                                           "(enables baseline_relative mode).")
    ap.add_argument("--manifest", help="Corpus manifest (JSONL) for the baseline "
                                       "(enables baseline_relative mode).")
    ap.add_argument("--k-sd", type=float, default=DEFAULT_K_SD,
                    help=f"Provisional band half-width (mean ± k·sd; default {DEFAULT_K_SD}).")
    ap.add_argument("--json", action="store_true", help="Emit the JSON envelope to stdout.")
    ap.add_argument("--out", help="Write the JSON envelope to this path.")
    args = ap.parse_args(argv)

    if args.k_sd <= 0:
        sys.stderr.write("[style_vectorizer] --k-sd must be > 0\n")
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
