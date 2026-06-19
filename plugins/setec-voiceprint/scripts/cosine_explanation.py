#!/usr/bin/env python3
"""cosine_explanation.py — a named side-by-side for the LUAR cosine.

ArgScope-adjacent interpretability (spec ``specs/27-embedding-explanation.md``,
M1). Makes the opaque ``voice_fingerprint`` / ``authorship_embedding`` LUAR
cosine human-checkable by placing it side by side with SETEC's already-named
stylometric features (burstiness, MATTR, MTLD, function-word ratio, dependency
distance) and marking, per feature, whether the named lens **tracks** or
**diverges** from the embedding. The divergences are where the operator must look
at the neural signal on its own terms.

POSTURE — load-bearing
----------------------
A side-by-side, NOT a verdict and NOT a fabricated partition. You cannot split a
single neural cosine scalar into "explained + residual" without a fit (the
Residualized Similarity method, arXiv:2510.05362, IS a fitted method), so v1
emits **no** ``explained_fraction`` / ``residual_fraction`` — only the cosine,
each feature's per-pair similarity, and ``agreement ∈ {tracks, diverges}`` (a
defined rule, not a cosine partition). A numeric explained/residual split exists
ONLY under ``--fit-baseline`` (OLS R² over an operator corpus, corpus-relative
provenance). ``divergent_features`` is a qualitative inspection pointer, never a
suspicion score; divergence does NOT measure authenticity or AI-ness. Inherits
``authorship_embedding``'s refusals (no same-author / different-author / AI).

CLI
---
    python3 plugins/setec-voiceprint/scripts/cosine_explanation.py TARGET \\
        --comparison FILE [--fit-baseline CORPUS.json] [--inputs-json F] \\
        [--json] [--out F]

``--inputs-json`` supplies a precomputed ``{cosine, features:{name:[t,c]}}`` (an
explicit injected path → ``inputs_source: "injected"``, refused as production).
The default path computes via ``compute_inputs`` (loads LUAR — the style-embedding
tier, NOT available in CI; the tests monkeypatch this seam).
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

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_error_output, build_output  # type: ignore

TASK_SURFACE = "embedding_explanation"
TOOL_NAME = "cosine_explanation"
SCRIPT_VERSION = "0.1.0"
METHOD_VERSION = "cosine_side_by_side_v1"

# The curated, human-legible named feature set (spec open-Q3 default). Each carries
# a PROVISIONAL reference SCALE — the |target−comparison| gap that maps to
# feature_similarity 0.0 (identical → 1.0). Scales are rough register-general
# defaults, NOT calibrated thresholds; they normalize the side-by-side, nothing more.
NAMED_FEATURES: tuple[tuple[str, float], ...] = (
    ("burstiness_B", 0.30),
    ("mattr", 0.15),
    ("mtld", 40.0),
    ("function_word_ratio", 0.10),
    ("mean_dependency_distance", 0.80),
)
# Neutral midpoint that splits the [0,1] range into "reads similar" vs "reads
# different" for BOTH the feature similarity and the cosine. A side-by-side
# reference point, explicitly NOT an authorship operating threshold.
NEUTRAL_MIDPOINT = 0.5

DEFAULT_LICENSES = (
    "Places the authorship_embedding LUAR cosine (from voice_fingerprint) side by "
    "side with SETEC's named interpretable stylometric features (burstiness, "
    "MATTR, MTLD, function-word ratio, dependency distance), reporting per feature "
    "the pair's value on each, a per-feature similarity, and whether the named "
    "lens tracks or diverges from the embedding. An interpretation aid that makes "
    "the opaque cosine inspectable; the embedding and feature values are sourced "
    "from existing audits (read results.provenance)."
)

DEFAULT_DOES_NOT_LICENSE = (
    "Does NOT license any same-author / different-author / AI-vs-human "
    "determination — it inherits and re-states authorship_embedding's refusals and "
    "adds none. A diverging feature does NOT measure authenticity or AI-ness and is "
    "NOT a suspicion score; it is an inspection pointer — the named lens and the "
    "embedding disagree there, look closer. The named feature set is a chosen lens, "
    "not ground truth. v1 emits no explained/residual fraction: you cannot "
    "partition a neural cosine without a fit, and any such number would be "
    "fabricated; a corpus-relative OLS split appears only under --fit-baseline. An "
    "inputs_source 'injected' run (precomputed --inputs-json) is NOT a production "
    "interpretation — it carries no text and rides no privacy gate. Ships "
    "`uncalibrated`: no threshold; the operator reads the side-by-side."
)


def feature_similarity(target: float, comparison: float, scale: float) -> float:
    """1.0 when identical, falling to 0.0 as |target−comparison| reaches one
    reference SCALE. Clamped to [0, 1]."""
    if scale <= 0:
        return 0.0
    return max(0.0, 1.0 - min(1.0, abs(target - comparison) / scale))


def _side(value: float) -> bool:
    """Which side of the neutral midpoint a [0,1] reading falls on."""
    return value >= NEUTRAL_MIDPOINT


def agreement(feat_sim: float, cosine: float) -> str:
    """`tracks` iff the feature similarity and the cosine fall on the SAME side of
    the neutral midpoint (both read 'similar' or both read 'different'); else
    `diverges`. A defined side-by-side rule — NOT a partition of the cosine."""
    return "tracks" if _side(feat_sim) == _side(cosine) else "diverges"


def build_comparison(cosine: float, features: dict[str, Any]) -> list[dict[str, Any]]:
    """Per named feature, the side-by-side row. `features` maps name → [target,
    comparison]. Features absent from `features` are skipped (provenance notes it)."""
    rows: list[dict[str, Any]] = []
    for name, scale in NAMED_FEATURES:
        pair = features.get(name)
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            continue
        try:                                    # #231: a non-numeric pair must not traceback here
            t, c = float(pair[0]), float(pair[1])
        except (TypeError, ValueError):
            continue
        sim = feature_similarity(t, c, scale)
        rows.append(
            {
                "feature": name,
                "target_value": t,
                "comparison_value": c,
                "feature_similarity": round(sim, 4),
                "agreement": agreement(sim, cosine),
            }
        )
    return rows


def fit_residual(corpus: list[dict[str, Any]]) -> dict[str, Any] | None:
    """`--fit-baseline`: OLS of cosine on the named features over a corpus of
    {cosine, features:{name:[t,c]}} rows → R² + residual = 1−R², CORPUS-relative
    provenance (never a per-pair scalar). Returns None if numpy/rows insufficient."""
    try:
        import numpy as np  # type: ignore
    except ImportError:
        return None
    xs, ys = [], []
    for row in corpus:
        cos = row.get("cosine")
        feats = row.get("features", {})
        if not isinstance(cos, (int, float)):
            continue
        vec = []
        ok = True
        for name, scale in NAMED_FEATURES:
            pair = feats.get(name)
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                ok = False
                break
            vec.append(feature_similarity(float(pair[0]), float(pair[1]), scale))
        if ok:
            xs.append(vec)
            ys.append(float(cos))
    if len(ys) < len(NAMED_FEATURES) + 2:  # need more rows than features
        return None
    X = np.column_stack([np.ones(len(xs)), np.asarray(xs)])
    y = np.asarray(ys)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ beta
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {
        "fit_r2": round(r2, 4),
        "fit_residual": round(1.0 - r2, 4),
        "n_corpus_rows": len(ys),
        "note": ("CORPUS-relative OLS of cosine on named-feature similarities; "
                 "an explained/residual split for THIS corpus, never a per-pair "
                 "authorship operating point."),
    }


def _named_features(text: str) -> dict[str, float]:
    """Compute the curated named features for one text via the existing audits.
    `mean_dependency_distance` is included only when the spaCy parser tier is
    available (mdd_stats returns None otherwise — it is then simply absent)."""
    import variance_audit as va  # type: ignore
    words = va.split_words(text)
    sents = va.split_sentences(text)
    feats: dict[str, float] = {
        "burstiness_B": float(va.sentence_length_stats(sents)["burstiness_B"]),
        "mattr": float(va.mattr(words)),
        "mtld": float(va.mtld(words)),
        "function_word_ratio": float(va.function_word_fingerprint(words)["function_word_ratio"]),
    }
    mdd = va.mdd_stats(text)
    if mdd is not None:
        feats["mean_dependency_distance"] = float(mdd["mean"])
    return feats


def compute_inputs(target: Path, comparison: Path) -> tuple[float, dict[str, Any]]:
    """Real path: the LUAR document cosine (voice_fingerprint) + named feature values
    (variance_audit), for the target vs the comparison. Raises ``RuntimeError`` when the
    style-embedding tier (transformers + torch / numpy) is absent — surfaced upstream as
    ``missing_dependency`` — and ``ValueError`` on unreadable input (bad_input). The LUAR
    weights are not CI-available, so the tests exercise this via ``--inputs-json`` or by
    monkeypatching this seam; the production path here computes for real when the tier exists."""
    try:
        target_text = target.read_text(encoding="utf-8")
        comparison_text = comparison.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read input text: {exc}")

    import voice_fingerprint as vf  # type: ignore
    try:
        import numpy as np  # type: ignore
        from semantic_trajectory_audit import split_windows  # type: ignore
        encoder = vf._load_encoder(vf.DEFAULT_MODEL)

        def _doc_vec(text: str) -> Any:
            # Window + centroid exactly as voice_fingerprint does — LUAR-MUD truncates long
            # text, so encoding a whole document under-represents it. Fall back to the whole
            # text when it is too short to window.
            windows = split_windows(text, "paragraph", window_size=200) or [text]
            return vf._centroid(np.asarray(encoder.encode(windows)))

        tvec = _doc_vec(target_text)
        cvec = _doc_vec(comparison_text)
    except (ImportError, vf.VoiceFingerprintError) as exc:
        raise RuntimeError(
            "cosine_explanation real-input path requires the style-embedding tier "
            f"(transformers + torch + numpy) for the LUAR cosine: {exc}. Install it, or "
            "pass --inputs-json with a precomputed {cosine, features}."
        )
    cosine = vf._cosine(tvec, cvec)

    tf, cf = _named_features(target_text), _named_features(comparison_text)
    features = {name: [tf[name], cf[name]] for name in tf if name in cf}
    return float(cosine), features


def load_injected(path: Path) -> tuple[float, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "cosine" not in data or "features" not in data:
        raise ValueError("--inputs-json must be a JSON object with 'cosine' + 'features'")
    if not isinstance(data["cosine"], (int, float)) or isinstance(data["cosine"], bool):
        raise ValueError("--inputs-json 'cosine' must be a number")
    feats = data["features"]
    if not isinstance(feats, dict):
        raise ValueError("--inputs-json 'features' must be an object mapping name -> [target, comparison]")
    # #231 P2: validate each feature value up front so a malformed injected feature is a clean
    # bad_input error, not a downstream traceback (and not silently dropped).
    for name, pair in feats.items():
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2
                and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in pair)):
            raise ValueError(f"--inputs-json feature {name!r} must be [target_number, comparison_number]")
    return float(data["cosine"]), dict(feats)


def build_results(
    *,
    cosine: float,
    rows: list[dict[str, Any]],
    inputs_source: str,
    fit: dict[str, Any] | None,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    results: dict[str, Any] = {
        "method_version": METHOD_VERSION,
        "luar_cosine": cosine,
        "named_feature_comparison": rows,
        "divergent_features": [r["feature"] for r in rows if r["agreement"] == "diverges"],
        "n_features": len(rows),
        "inputs_source": inputs_source,
        "calibration_status": "uncalibrated",
        "provenance": provenance,
    }
    if fit is not None:
        results["fit_baseline"] = fit  # corpus-relative; only when --fit-baseline given
    return results


def compose_envelope(
    *,
    target_path: Path | None,
    target_words: int,
    results: dict[str, Any],
    licenses_text: str,
    does_not_license_text: str,
) -> dict[str, Any]:
    caveats: list[str] = []
    if results["inputs_source"] == "injected":
        caveats.append(
            "inputs_source is `injected` (precomputed --inputs-json): this run "
            "carries no text, rides no privacy gate, and is NOT a production "
            "interpretation."
        )
    caveats.append(
        "A side-by-side for human review, not a verdict. Diverging features are "
        "inspection pointers (the named lens and the embedding disagree there), "
        "NOT a measure of authenticity / AI-ness and NOT a score. v1 emits no "
        "explained/residual fraction (none is definable without a fit). Ships "
        "`uncalibrated`; inherits authorship_embedding's no-author/no-AI refusals."
    )

    license_block = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=licenses_text,
        does_not_license=does_not_license_text,
        comparison_set={
            "embedding": "LUAR cosine via voice_fingerprint (authorship_embedding)",
            "named_features": [n for n, _ in NAMED_FEATURES],
            "inputs_source": results["inputs_source"],
        },
        length_range_words=(0, 100000),
        register_match=["register-general (the named scales are provisional)"],
        additional_caveats=caveats,
        references=[
            "Zhu & Jurgens 2025, 'Residualized Similarity' (arXiv:2510.05362)",
            "Patel, Rao, et al. 2024, 'Latent-Space Interpretation for Stylometry' "
            "(arXiv:2409.07072)",
        ],
    )

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=None,
        results=results,
        claim_license=license_block,
        available=True,
        warnings=caveats,
    )


def render_markdown(envelope: dict[str, Any]) -> str:
    r = envelope["results"]
    lines = [
        "# LUAR cosine — named side-by-side",
        "",
        "> **Not a verdict.** This places the embedding cosine next to named "
        "stylometric features and marks where they agree. A diverging feature is "
        "an inspection pointer, not a measure of authenticity or AI-ness.",
        "",
        f"- **LUAR cosine:** `{r['luar_cosine']}`  ·  **inputs:** "
        f"`{r['inputs_source']}`  ·  **calibration:** `{r['calibration_status']}`",
        "",
        "## Side-by-side",
        "",
        "| feature | target | comparison | similarity | agreement |",
        "|---|---|---|---|---|",
    ]
    for row in r["named_feature_comparison"]:
        lines.append(
            f"| `{row['feature']}` | {row['target_value']} | "
            f"{row['comparison_value']} | {row['feature_similarity']} | "
            f"{row['agreement']} |"
        )
    lines.append("")
    div = r.get("divergent_features", [])
    lines.append(f"**Diverging features (inspect):** {', '.join(div) if div else '(none)'}")
    if "fit_baseline" in r:
        fb = r["fit_baseline"]
        lines.append("")
        lines.append(f"**--fit-baseline (corpus-relative):** R²={fb['fit_r2']}, "
                     f"residual={fb['fit_residual']} over {fb['n_corpus_rows']} rows.")
    return "\n".join(lines) + "\n"


def _emit(envelope: dict[str, Any], *, out_path: Path, md_path: Path | None,
          to_stdout: bool) -> int:
    try:
        out_path.write_text(json.dumps(envelope, indent=2, default=str),
                            encoding="utf-8")
        if md_path is not None:
            md_path.write_text(render_markdown(envelope), encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write output: {exc}", file=sys.stderr)
        return 1
    if to_stdout:
        print(json.dumps(envelope, indent=2, default=str))
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Named side-by-side for the LUAR cosine (no verdict).",
    )
    p.add_argument("target", type=Path, help="UTF-8 prose file (the target)")
    p.add_argument("--comparison", type=Path, default=None,
                   help="comparison prose file (real path); not needed with --inputs-json")
    p.add_argument("--inputs-json", type=Path, default=None,
                   help="precomputed {cosine, features} (injected path, non-production)")
    p.add_argument("--fit-baseline", type=Path, default=None,
                   help="corpus JSON of {cosine, features} rows → OLS R²/residual")
    p.add_argument("--licenses", default=DEFAULT_LICENSES)
    p.add_argument("--does-not-license", default=DEFAULT_DOES_NOT_LICENSE)
    p.add_argument("--json", action="store_true")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--out-md", type=Path, default=None)
    return p


def _error_envelope(reason: str, category: str, target: Path | None) -> dict[str, Any]:
    return build_error_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=target, target_words=0, reason=reason, reason_category=category,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    target_path: Path = args.target
    out_json = (args.out if args.out is not None
                else target_path.with_suffix(target_path.suffix + ".cosine_explanation.json"))
    out_md = (args.out_md if args.out_md is not None
              else target_path.with_suffix(target_path.suffix + ".cosine_explanation.md"))

    # Resolve inputs: injected (cached, non-production) or computed (gated, LUAR).
    if args.inputs_json is not None:
        try:
            cosine, features = load_injected(args.inputs_json)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            env = _error_envelope(f"--inputs-json: {exc}", "bad_input", target_path)
            return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)
        inputs_source = "injected"
        provenance = {"inputs_source": "injected", "inputs_json": str(args.inputs_json)}
    else:
        if args.comparison is None:
            env = _error_envelope(
                "need --comparison (real path) or --inputs-json (precomputed).",
                "bad_input", target_path)
            return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)
        try:
            cosine, features = compute_inputs(target_path, args.comparison)
        except RuntimeError as exc:                          # absent style-embedding tier
            env = _error_envelope(str(exc), "missing_dependency", target_path)
            return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)
        except (ValueError, OSError) as exc:                 # unreadable/invalid input, not a crash
            env = _error_envelope(f"--comparison: {exc}", "bad_input", target_path)
            return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)
        inputs_source = "computed"
        provenance = {"inputs_source": "computed",
                      "embedding": "voice_fingerprint LUAR",
                      "comparison": str(args.comparison)}

    rows = build_comparison(cosine, features)
    if not rows:
        env = _error_envelope(
            "no named features present in inputs — nothing to place side by side.",
            "bad_input", target_path)
        return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)

    fit = None
    fit_warning = None
    if args.fit_baseline is not None:
        # #231 P2: a REQUESTED --fit-baseline that cannot be read/parsed is bad input (the operator
        # asked for it), not a silent no-op; an unusable-but-parseable corpus (too few rows / no
        # numpy) surfaces a warning rather than vanishing.
        try:
            raw_corpus = args.fit_baseline.read_text(encoding="utf-8")
            corpus = json.loads(raw_corpus)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            env = _error_envelope(f"--fit-baseline: cannot read/parse corpus: {exc}",
                                  "bad_input", target_path)
            return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)
        if not isinstance(corpus, list):
            env = _error_envelope("--fit-baseline: corpus must be a JSON array of "
                                  "{cosine, features} rows.", "bad_input", target_path)
            return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)
        fit = fit_residual(corpus)
        if fit is None:
            fit_warning = ("--fit-baseline corpus was valid JSON but unusable for an OLS fit "
                           "(fewer rows than named features + 2, or numpy unavailable); no "
                           "explained/residual split was computed.")

    results = build_results(cosine=cosine, rows=rows, inputs_source=inputs_source,
                            fit=fit, provenance=provenance)
    if fit_warning:
        results["fit_baseline_warning"] = fit_warning
    envelope = compose_envelope(
        target_path=target_path, target_words=0, results=results,
        licenses_text=args.licenses, does_not_license_text=args.does_not_license,
    )
    return _emit(envelope, out_path=out_json, md_path=out_md, to_stdout=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
