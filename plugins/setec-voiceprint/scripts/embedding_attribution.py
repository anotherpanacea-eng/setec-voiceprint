#!/usr/bin/env python3
"""embedding_attribution.py — a faithful glass-box ATTRIBUTION of the LUAR cosine.

The HIATUS glass-box explanation layer on ``voice_fingerprint`` /
``authorship_embedding`` (spec ``explainable-embedding-attribution``, M1). Where
``cosine_explanation`` (spec 27) places the opaque LUAR cosine *side by side*
with SETEC's named stylometric features and flags ``tracks``/``diverges``, this
surface goes one step further and ATTRIBUTES the cosine: it reports, per named
feature, a **signed contribution** to the modeled similarity (Latent-Space
Interpretation, arXiv:2409.07072) and decomposes the cosine into an
interpretable-feature-**explained** part and a **faithful residual** — the slice
the named features genuinely do not capture (Residualized Similarity,
arXiv:2510.05362). Program lineage: IARPA **HIATUS** (Human Interpretable
Attribution of Text Using Underlying Structure) / the AUTHOR consortium; the
fleet already vendors LUAR (Rivera-Soto et al., EMNLP 2021) as the
``voice_fingerprint`` encoder, so this layer attaches to the same frozen
manifold.

POSTURE — load-bearing
----------------------
DESCRIPTIVE only. It EXPLAINS ``voice_fingerprint``; it must NEVER become or
replace a verdict, and it leaves ``authorship_embedding``'s no-verdict /
calibration posture intact. It invents NO new authorship number — it decomposes
the EXISTING cosine. Concretely:

  * No verdict / selection scalar anywhere in ``results`` (pinned by a RECURSIVE
    no-verdict walk over the whole tree, AC-9).
  * The **residual is a coverage quantity, not authenticity**. ``residual_*``
    names the part of the cosine the named features don't model; it is NEVER an
    "AI residual" or a suspicion score, and it is never compared to a threshold.
  * Signed ``contribution`` is an explanation weight, not authorship pressure; a
    negative contribution means the named feature reads as *divergent* for this
    pair, not "less likely same author".
  * ``coverage_band`` names the MEASURED property — how much of the cosine the
    named features account for — never the inference target.

The explained/residual numeric split (spec-27 Open-Q1) is shipped, but
DISCIPLINED: by operator decision this CONSCIOUSLY EXTENDS spec 27's caution.
The **default / headline** view is the spec-27 side-by-side + per-feature
``agreement`` (``tracks``/``diverges``); the numeric ``explained_fraction`` is
carried but surfaced as *calibrated* only when ``calibration_status`` warrants
it (it ships ``uncalibrated`` → ``fraction_calibrated: false``, and the headline
the operator reads is the agreement table, not the number). This reconciles BOTH
the HIATUS method (the fit is the whole point of arXiv:2510.05362) and spec 27's
refusal to ship a fabricated partition: the number is honest about its own
provenance and gated behind the calibration discipline; the framing forbids
reading it as a verdict.

M1 vs M2
--------
M1 (this file's tested path) runs the attribution + decomposition over INJECTED
LUAR embeddings / named-feature vectors — pure Python + numpy, CI-runnable, no
torch / transformers / network / model weights. The live-LUAR path
(``compute_inputs``) is a lazy-import + ``skipif`` M2 seam, exactly like
``cosine_explanation.compute_inputs`` (spec 27) and ``voice_fingerprint._load_encoder``
(spec 02). The learned latent-direction anchors of arXiv:2409.07072 (LUAR-embedding
a labeled attribute corpus) are POC-gated M2 — they cannot run in CI.

CLI
---
    python3 plugins/setec-voiceprint/scripts/embedding_attribution.py TARGET \\
        --comparison FILE [--inputs-json F] [--json] [--out F]

``--inputs-json`` supplies a precomputed
``{cosine, features:{name:[t,c]}, attribution_model?}`` (an explicit injected
path → ``inputs_source: "injected"``, refused as production). The default path
computes via ``compute_inputs`` (loads LUAR — the style-embedding tier, NOT
available in CI; the tests monkeypatch this seam).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_error_output, build_output  # type: ignore

# Reuse spec-27's curated, human-legible named feature set and its #231-hardened
# input guards verbatim — the anti-Goodhart disjointness (AC-10) DEPENDS on the
# attribution basis being exactly the existing named-feature audits (the
# interpretable-scalar axis), never re-derived from the LUAR embedding itself.
import cosine_explanation as ce  # type: ignore

TASK_SURFACE = "embedding_attribution"
TOOL_NAME = "embedding_attribution"
SCRIPT_VERSION = "0.1.0"
METHOD_VERSION = "residualized_attribution_v1"

# The named basis IS cosine_explanation's curated five (D4 ≡ AC-10). Reused,
# never reinvented; sourced from the interpretable-scalar audits
# (smoothing_diagnosis / voice_coherence), orthogonal by construction to the
# LUAR manifold. The (name, scale) reference scales are spec-27's, used here to
# turn a (target, comparison) pair into a per-feature similarity in [0, 1].
NAMED_FEATURES = ce.NAMED_FEATURES
NAMED_FEATURE_NAMES: tuple[str, ...] = tuple(n for n, _ in NAMED_FEATURES)

# coverage_band cutoffs over residual_fraction (§3.1). Provisional,
# register-general defaults (like spec 27's reference scales), NOT calibrated
# thresholds — they name the COVERAGE of the explanation, never an inference
# target. The HRS study may justify per-genre edges (future work).
WELL_NAMED_MAX_RESIDUAL = 0.25
MOSTLY_NAMED_MAX_RESIDUAL = 0.60

# A |cosine| below this is the single-pair degeneracy: explained / |cosine| is
# numerically unstable (a near-zero denominator), so the fraction is ABSTAINED
# (the band goes `indeterminate`) rather than reporting a fabricated split.
MIN_COSINE_MAGNITUDE_FOR_FRACTION = 1e-3

DEFAULT_LICENSES = (
    "A faithful decomposition of the authorship_embedding LUAR cosine (from "
    "voice_fingerprint) into named interpretable-feature contributions "
    "(Latent-Space Interpretation, arXiv:2409.07072) and an interpretable "
    "residual (Residualized Similarity, arXiv:2510.05362). Reports, per named "
    "feature, its signed contribution to the modeled similarity and whether the "
    "pair reads as shared/divergent on it; and the explained vs residual split "
    "of the cosine with explicit fit provenance. The DEFAULT/headline view is "
    "the side-by-side + per-feature agreement; the numeric explained_fraction is "
    "carried but surfaced as calibrated only when calibration_status warrants it. "
    "An interpretation aid that makes the opaque cosine inspectable; the "
    "embedding and feature values are sourced from existing audits (read "
    "results.provenance)."
)

DEFAULT_DOES_NOT_LICENSE = (
    "Does NOT license any same-author / different-author / AI-generated or "
    "human-written determination — it inherits and re-states "
    "authorship_embedding's refusals and adds none. The residual does not "
    "measure authenticity or AI-ness and is not a suspicion score: the residual "
    "and the `largely-unnamed` band are an inspection pointer (the neural signal "
    "does work the named lens does not capture, inspect it on its own terms), "
    "never a verdict. A negative signed contribution means the named feature "
    "reads as divergent for this pair, NOT 'less likely same author'. The named "
    "feature set is a chosen lens, not ground truth. There is no threshold; the "
    "operator reads the decomposition. The numeric explained/residual fraction is "
    "honest about its fit provenance but is NOT a shipped per-pair operating "
    "point — it is surfaced as calibrated only when calibration_status warrants "
    "it (ships `uncalibrated`). An inputs_source `injected` run (precomputed "
    "--inputs-json) is NOT a production interpretation — it carries no text and "
    "rides no privacy gate. This consciously extends spec 27 Open-Q1: it ships "
    "the explained_fraction the side-by-side withheld, reconciled by the "
    "calibration-gate + side-by-side-default (the number is honest, the framing "
    "forbids reading it as a verdict)."
)


# --------------------------------------------------------------------------
# (A) Latent-Space Interpretation — signed named contributions (arXiv:2409.07072)
# --------------------------------------------------------------------------

def _feature_vector(features: dict[str, Any]) -> tuple[list[str], list[float]]:
    """The per-feature similarity in [0, 1] for each named feature actually
    present in ``features`` (a malformed/non-finite pair is skipped via spec-27's
    ``_finite_pair`` guard, never a traceback). Returns the parallel
    (names, sims). The ``direction`` shared/divergent is read off the sim vs the
    neutral midpoint (spec 27's ``_side``)."""
    names: list[str] = []
    sims: list[float] = []
    for name, scale in NAMED_FEATURES:
        tc = ce._finite_pair(features.get(name))
        if tc is None:
            continue
        names.append(name)
        sims.append(ce.feature_similarity(tc[0], tc[1], scale))
    return names, sims


def _weight_vector(
    names: list[str], model: dict[str, Any] | None
) -> tuple[list[float], str, dict[str, Any]]:
    """The per-feature linear weight that turns a per-feature similarity into a
    signed contribution (the fit's slope), plus the ``fit_source`` it came from
    and a ``fit_meta`` dict carrying the fit's intercept / R² where they exist.

    Three provenances (D3), in priority order:
      * ``injected_model``: the injected attribution_model carries an explicit
        ``weights`` map name -> float. Used as-is. This is the declared-input fit
        (resolves "you cannot partition without a fit" by making the fit an
        INPUT, never an invented number). Its intercept (if any) is honoured from
        the model's explicit ``intercept`` field downstream.
      * ``corpus_fit``: the model carries a ``corpus`` of {cosine, features} rows
        → a pure-numpy OLS of cosine on the per-feature similarities (reuses the
        Residualized-Similarity linear residualization; CI-friendly). The SAME
        single OLS produces the slopes, the intercept, AND the reported R² — they
        are threaded together so the explained part and the reported fit quality
        describe one fit (BUILD-PREFLIGHT mode-5 single-source-of-truth: the two
        paths cannot diverge).
      * ``unfit``: no usable model → no weights. The decomposition abstains
        (``coverage_band: indeterminate``), never fabricates a split.
    """
    if isinstance(model, dict) and isinstance(model.get("weights"), dict):
        wmap = model["weights"]
        weights = [float(wmap[n]) for n in names
                   if isinstance(wmap.get(n), (int, float)) and not isinstance(wmap.get(n), bool)
                   and math.isfinite(float(wmap[n]))]
        if len(weights) == len(names) and names:
            return weights, "injected_model", {}
    if isinstance(model, dict) and isinstance(model.get("corpus"), list):
        fit = _fit_weights(names, model["corpus"])
        if fit is not None:
            slopes, intercept, r2 = fit
            return slopes, "corpus_fit", {"intercept": intercept, "r2": r2}
    return [], "unfit", {}


def _fit_weights(
    names: list[str], corpus: list[Any]
) -> tuple[list[float], float, float | None] | None:
    """ONE OLS of cosine on the per-feature similarities over a corpus of
    {cosine, features} rows → ``(slopes, intercept, r2)`` from a single fit: one
    slope weight per named feature, PLUS the fitted intercept (the constant term)
    and the fit's R². The intercept is part of the explained part — it must NOT be
    dropped, or the reported R² (intercept-inclusive) would describe a different
    fit than ``explained = intercept + Σ slope×sim`` (the P2 faithfulness defect).
    Pure numpy. Returns None when numpy is absent or the corpus has too few usable
    rows (the spec-27 ``fit_residual`` ``return None`` degradation pattern — the
    caller then abstains rather than crashing)."""
    try:
        import numpy as np  # type: ignore
    except ImportError:
        return None
    scales = {n: s for n, s in NAMED_FEATURES}
    xs: list[list[float]] = []
    ys: list[float] = []
    for row in corpus:
        if not isinstance(row, dict):
            continue
        cos = row.get("cosine")
        feats = row.get("features", {})
        if (not isinstance(cos, (int, float)) or isinstance(cos, bool)
                or not math.isfinite(cos) or not isinstance(feats, dict)):
            continue
        vec: list[float] = []
        ok = True
        for name in names:
            tc = ce._finite_pair(feats.get(name))
            if tc is None:
                ok = False
                break
            vec.append(ce.feature_similarity(tc[0], tc[1], scales[name]))
        if ok:
            xs.append(vec)
            ys.append(float(cos))
    if len(ys) < len(names) + 2:  # need more rows than features (+ intercept)
        return None
    X = np.column_stack([np.ones(len(xs)), np.asarray(xs, dtype=float)])
    y = np.asarray(ys, dtype=float)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    intercept = float(beta[0])
    slopes = [float(b) for b in beta[1:]]
    if not math.isfinite(intercept) or not all(math.isfinite(s) for s in slopes):
        return None
    # R² of the SAME fit (intercept-inclusive), so the reported fit quality and the
    # explained part describe one regression.
    pred = X @ beta
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2: float | None = None if ss_tot <= 0 else 1.0 - ss_res / ss_tot
    if r2 is not None and not math.isfinite(r2):
        r2 = None
    return slopes, intercept, r2


def feature_attribution(
    names: list[str], sims: list[float], weights: list[float]
) -> list[dict[str, Any]]:
    """Per named feature, its signed contribution to the MODELED similarity
    (weight × per-feature similarity) and a descriptive ``direction``. When there
    are no weights (unfit), ``contribution`` is None and the row still carries the
    measured values + direction (the side-by-side survives an unfit run)."""
    rows: list[dict[str, Any]] = []
    have_weights = len(weights) == len(names)
    for i, name in enumerate(names):
        sim = sims[i]
        contribution = round(weights[i] * sim, 6) if have_weights else None
        rows.append({
            "feature": name,
            "feature_similarity": round(sim, 4),
            # SIGNED: how much this named feature moves the MODELED similarity for
            # this pair (None when unfit). NOT authorship pressure.
            "contribution": contribution,
            # Descriptive per-feature property: do the two passages read alike on
            # this named feature (sim on the 'similar' side of the midpoint)?
            "direction": "shared" if ce._side(sim) else "divergent",
        })
    return rows


# --------------------------------------------------------------------------
# (B) Residualized Similarity — the faithful decomposition (arXiv:2510.05362)
# --------------------------------------------------------------------------

def decompose(
    cosine: float,
    rows: list[dict[str, Any]],
    fit_source: str,
    model: dict[str, Any] | None,
    fit_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """explained = the fit's intercept + the fitted linear combination of
    named-feature similarities (intercept + Σ contribution); ``residual = cosine
    - explained`` (the honest remainder).

    The intercept is the SAME constant term as the OLS whose R² is reported
    (``fit_meta['intercept']`` for a corpus_fit, the model's explicit
    ``intercept`` for an injected_model). Dropping it would make the reported R²
    describe a different fit than ``explained`` — the P2 faithfulness defect.

    Fractions are ``explained / cosine`` (the SIGNED cosine, so a negative cosine
    the model reproduces is correctly 100% explained, not 0%) clamped to [0, 1].
    TWO abstentions (never a fabricated split):
      * ``unfit`` (no usable model) → ``coverage_band: indeterminate``, no
        fraction.
      * single-pair near-zero-cosine degeneracy (``|cosine| < 1e-3``) →
        ``coverage_band: indeterminate``, no fraction (the denominator is
        unstable; reporting a ratio there would be a fabricated number — the
        spec's clamp/abstain decision).
    """
    fit_meta = fit_meta or {}
    intercept = _fit_intercept(rows, model, fit_source, fit_meta)
    fit_provenance: dict[str, Any] = {
        "fit_source": fit_source,
        "model_id": "rrivera1849/LUAR-MUD",
        "named_features": [r["feature"] for r in rows],
    }
    if fit_source == "corpus_fit" and isinstance(model, dict):
        corpus = model.get("corpus")
        if isinstance(corpus, list):
            fit_provenance["n_corpus_rows"] = sum(1 for r in corpus if isinstance(r, dict))
        # The R² is threaded from the SAME single OLS that produced the slopes
        # and the intercept used in ``explained`` above — one fit, one R², no
        # second regression that could silently disagree.
        r2 = fit_meta.get("r2")
        if isinstance(r2, (int, float)) and not isinstance(r2, bool) and math.isfinite(r2):
            fit_provenance["r2"] = round(float(r2), 4)

    if fit_source == "unfit":
        return {
            "explained_similarity": None,
            "residual_similarity": None,
            "explained_fraction": None,
            "residual_fraction": None,
            "fit_provenance": fit_provenance,
        }

    explained = intercept + sum(r["contribution"] for r in rows)
    residual = cosine - explained
    deco: dict[str, Any] = {
        "explained_similarity": round(explained, 6),
        "residual_similarity": round(residual, 6),
        "fit_provenance": fit_provenance,
    }
    if abs(cosine) < MIN_COSINE_MAGNITUDE_FOR_FRACTION:
        # Single-pair near-zero-cosine degeneracy: abstain on the ratio.
        deco["explained_fraction"] = None
        deco["residual_fraction"] = None
        deco["fraction_abstained"] = "near-zero |cosine| — explained/|cosine| is unstable"
    else:
        # SIGNED denominator: cosine = explained + residual, so the explained share is
        # explained / cosine (not explained / |cosine|). With the magnitude denominator a
        # NEGATIVE cosine the model reproduces exactly (explained == cosine < 0) gave
        # explained/|cosine| == -1 → clamped to 0, falsely reporting 0% explained / 100%
        # residual on a pair it explains perfectly. Dividing by the signed cosine yields 1.0
        # there; an explained part pointing the WRONG way is still < 0 → clamped to 0 (0%
        # explained), and over-explaining clamps to 1.0 — the [0,1] presentation is kept.
        exp_frac = min(1.0, max(0.0, explained / cosine))
        deco["explained_fraction"] = round(exp_frac, 6)
        deco["residual_fraction"] = round(1.0 - exp_frac, 6)
    return deco


def _fit_intercept(
    rows: list[dict[str, Any]],
    model: dict[str, Any] | None,
    fit_source: str,
    fit_meta: dict[str, Any] | None = None,
) -> float:
    """The fit's intercept (the OLS constant term), applied to ``explained`` so
    the explained part matches the very regression whose R² is reported.

      * ``corpus_fit``: the intercept from the SINGLE OLS that also produced the
        slopes and R² (threaded in ``fit_meta['intercept']``). Dropping it was the
        P2 defect — it made ``explained`` an intercept-free fit while the reported
        R² was intercept-inclusive, inflating the residual on high-cosine pairs.
      * ``injected_model``: an explicit ``intercept`` field is honoured.
      * otherwise 0.0.
    """
    fit_meta = fit_meta or {}
    if fit_source == "corpus_fit":
        ic = fit_meta.get("intercept")
        if isinstance(ic, (int, float)) and not isinstance(ic, bool) and math.isfinite(ic):
            return float(ic)
        return 0.0
    if isinstance(model, dict):
        ic = model.get("intercept")
        if isinstance(ic, (int, float)) and not isinstance(ic, bool) and math.isfinite(ic):
            return float(ic)
    return 0.0


# --------------------------------------------------------------------------
# (C) The property-naming band
# --------------------------------------------------------------------------

def coverage_band(decomposition: dict[str, Any]) -> str:
    """Names the MEASURED property — how much of the cosine the named features
    account for — derived from ``residual_fraction`` (§3.1). NEVER an inference
    target (no `ai`, no `suspicious`, no `authentic`). ``indeterminate`` on a
    degenerate / unfit input (no fraction)."""
    rf = decomposition.get("residual_fraction")
    if rf is None:
        return "indeterminate"
    if rf <= WELL_NAMED_MAX_RESIDUAL:
        return "well-named"
    if rf <= MOSTLY_NAMED_MAX_RESIDUAL:
        return "mostly-named"
    return "largely-unnamed"


# --------------------------------------------------------------------------
# Input providers — M1 injected (CI) and M2 live-LUAR (skipif seam)
# --------------------------------------------------------------------------

def load_injected(path: Path) -> tuple[float, dict[str, Any], dict[str, Any] | None]:
    """The injected M1 path. Accepts ``{cosine, features:{name:[t,c]},
    attribution_model?}``. Reuses spec-27's ``load_injected`` validation (finite
    cosine, each feature pair finite) verbatim, then validates the optional
    attribution_model shape. ``inputs_source: "injected"``."""
    cosine, features = ce.load_injected(path)  # finite-cosine + finite-pair guards (#231)
    data = json.loads(path.read_text(encoding="utf-8"))
    model = data.get("attribution_model")
    if model is not None and not isinstance(model, dict):
        raise ValueError("--inputs-json 'attribution_model' must be an object when present")
    return cosine, features, model


def compute_inputs(
    target: Path, comparison: Path
) -> tuple[float, dict[str, Any], dict[str, Any] | None]:
    """M2 live path: the LUAR cosine + named features for target vs comparison,
    computed exactly as ``cosine_explanation.compute_inputs`` does (window →
    encoder.encode → vf._centroid → vf._cosine). Raises ``RuntimeError`` →
    ``missing_dependency`` when the style-embedding tier is absent. No
    attribution_model is produced on the live path (the operator supplies the fit
    out of band or runs ``unfit``); the learned latent-direction anchors are
    POC-gated M2."""
    cosine, features = ce.compute_inputs(target, comparison)
    return cosine, features, None


# --------------------------------------------------------------------------
# Envelope assembly
# --------------------------------------------------------------------------

def build_results(
    *,
    cosine: float,
    attribution_rows: list[dict[str, Any]],
    agreement_rows: list[dict[str, Any]],
    decomposition: dict[str, Any],
    band: str,
    inputs_source: str,
    fraction_calibrated: bool,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    return {
        "method_version": METHOD_VERSION,
        "luar_cosine": cosine,
        "inputs_source": inputs_source,
        "calibration_status": "uncalibrated",
        # The DEFAULT / headline view (operator decision): the spec-27
        # side-by-side + per-feature agreement is what the operator reads first.
        "named_feature_comparison": agreement_rows,
        "divergent_features": [r["feature"] for r in agreement_rows
                               if r["agreement"] == "diverges"],
        # (A) Latent-Space Interpretation: signed named contributions.
        "feature_attribution": attribution_rows,
        # (B) Residualized Similarity: the faithful decomposition. The numeric
        # explained_fraction is CARRIED but gated: surfaced as calibrated only
        # when fraction_calibrated is True (ships uncalibrated → False).
        "decomposition": decomposition,
        "fraction_calibrated": fraction_calibrated,
        # (C) The property-naming band (coverage of the explanation).
        "coverage_band": band,
        "provenance": provenance,
    }


def compose_envelope(
    *,
    target_path: Path | None,
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
        "An ATTRIBUTION for human review, not a verdict. The DEFAULT view is the "
        "side-by-side + per-feature agreement; the numeric explained_fraction is "
        "surfaced as calibrated only when calibration_status warrants it (ships "
        "`uncalibrated`). The residual is a COVERAGE quantity (the part the named "
        "lens does not capture), NOT a measure of authenticity / AI-ness and NOT "
        "a suspicion score. The named feature set is a chosen lens, not ground "
        "truth. Inherits authorship_embedding's no-author/no-AI refusals. "
        "Consciously extends spec 27 Open-Q1 (ships the withheld explained_fraction, "
        "reconciled by the calibration-gate + side-by-side-default)."
    )

    license_block = ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=licenses_text,
        does_not_license=does_not_license_text,
        comparison_set={
            "embedding": "LUAR cosine via voice_fingerprint (authorship_embedding)",
            "named_features": list(NAMED_FEATURE_NAMES),
            "inputs_source": results["inputs_source"],
            "fit_source": results["decomposition"]["fit_provenance"]["fit_source"],
        },
        length_range_words=(0, 100000),
        register_match=["register-general (the named scales are provisional)"],
        additional_caveats=caveats,
        references=[
            "Alshomary, Ri, Apidianaki, Patel, Muresan & McKeown 2024, "
            "'Latent Space Interpretation for Stylistic Analysis and Explainable "
            "Authorship Attribution' (arXiv:2409.07072, IARPA HIATUS / AUTHOR "
            "consortium)",
            "Zeng, Alipoormolabashi, Mun, Dey, Soni, Balasubramanian, Rambow & "
            "Schwartz 2025, 'Residualized Similarity for Faithfully Explainable "
            "Authorship Verification' (arXiv:2510.05362, EMNLP 2025 Findings)",
            "Rivera-Soto et al. 2021, LUAR (EMNLP 2021, rrivera1849/LUAR-MUD)",
            "specs/02 (voice_fingerprint / authorship_embedding)",
            "specs/27-embedding-explanation.md (cosine_explanation)",
            "spec: explainable-embedding-attribution",
        ],
    )

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=0,
        baseline=None,
        results=results,
        claim_license=license_block,
        available=True,
        warnings=caveats,
    )


def render_markdown(envelope: dict[str, Any]) -> str:
    r = envelope["results"]
    deco = r["decomposition"]
    lines = [
        "# LUAR cosine — faithful attribution (glass-box explanation)",
        "",
        "> **Not a verdict.** This decomposes the embedding cosine into named "
        "feature contributions + a faithful residual. The residual is a "
        "*coverage* quantity (what the named lens does not capture), not a "
        "measure of authenticity or AI-ness. The headline is the agreement "
        "table; the explained_fraction is gated behind calibration_status.",
        "",
        f"- **LUAR cosine:** `{r['luar_cosine']}`  ·  **inputs:** "
        f"`{r['inputs_source']}`  ·  **calibration:** `{r['calibration_status']}`",
        f"- **coverage band:** `{r['coverage_band']}`  ·  **fit:** "
        f"`{deco['fit_provenance']['fit_source']}`  ·  "
        f"**fraction calibrated:** `{r['fraction_calibrated']}`",
        "",
        "## Headline — side-by-side agreement (default view)",
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
    div = r.get("divergent_features", [])
    lines += [
        "",
        f"**Diverging features (inspect):** {', '.join(div) if div else '(none)'}",
        "",
        "## Signed contributions (Latent-Space Interpretation)",
        "",
        "| feature | similarity | contribution | direction |",
        "|---|---|---|---|",
    ]
    for row in r["feature_attribution"]:
        contrib = "—" if row["contribution"] is None else row["contribution"]
        lines.append(
            f"| `{row['feature']}` | {row['feature_similarity']} | "
            f"{contrib} | {row['direction']} |"
        )
    lines += [
        "",
        "## Decomposition (Residualized Similarity)",
        "",
    ]
    if deco["explained_similarity"] is None:
        lines.append("> Decomposition abstained (no usable fit, or near-zero "
                     "cosine): `coverage_band: indeterminate`.")
    else:
        ef = deco["explained_fraction"]
        rf = deco["residual_fraction"]
        frac = ("(abstained — near-zero cosine)" if ef is None
                else f"explained_fraction={ef}, residual_fraction={rf}")
        lines.append(
            f"- explained_similarity={deco['explained_similarity']}, "
            f"residual_similarity={deco['residual_similarity']}"
        )
        lines.append(f"- {frac}")
        if not r["fraction_calibrated"]:
            lines.append("- _The explained_fraction is uncalibrated provenance, "
                         "NOT a shipped operating point — read the agreement "
                         "table as the headline._")
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
        description="Faithful attribution of the LUAR cosine (no verdict).",
    )
    p.add_argument("target", type=Path, help="UTF-8 prose file (the target)")
    p.add_argument("--comparison", type=Path, default=None,
                   help="comparison prose file (real path); not needed with --inputs-json")
    p.add_argument("--inputs-json", type=Path, default=None,
                   help="precomputed {cosine, features, attribution_model?} "
                        "(injected path, non-production)")
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
                else target_path.with_suffix(target_path.suffix + ".embedding_attribution.json"))
    out_md = (args.out_md if args.out_md is not None
              else target_path.with_suffix(target_path.suffix + ".embedding_attribution.md"))

    model: dict[str, Any] | None
    if args.inputs_json is not None:
        try:
            cosine, features, model = load_injected(args.inputs_json)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            env = _error_envelope(f"--inputs-json: {exc}", "bad_input", target_path)
            return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)
        inputs_source = "injected"
        provenance = {
            "embedding": "voice_fingerprint LUAR (authorship_embedding)",
            "named_features_source": "variance_audit",
            "inputs_source": "injected",
            "inputs_json": str(args.inputs_json),
        }
    else:
        if args.comparison is None:
            env = _error_envelope(
                "need --comparison (real path) or --inputs-json (precomputed).",
                "bad_input", target_path)
            return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)
        try:
            cosine, features, model = compute_inputs(target_path, args.comparison)
        except RuntimeError as exc:                          # absent style-embedding tier
            env = _error_envelope(str(exc), "missing_dependency", target_path)
            return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)
        except (ValueError, OSError) as exc:                 # unreadable/invalid input
            env = _error_envelope(f"--comparison: {exc}", "bad_input", target_path)
            return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)
        inputs_source = "computed"
        provenance = {
            "embedding": "voice_fingerprint LUAR (authorship_embedding)",
            "named_features_source": "variance_audit",
            "inputs_source": "computed",
            "comparison": str(args.comparison),
        }

    names, sims = _feature_vector(features)
    if not names:
        env = _error_envelope(
            "no named features present in inputs — nothing to attribute.",
            "bad_input", target_path)
        return _emit(env, out_path=out_json, md_path=None, to_stdout=args.json)

    weights, fit_source, fit_meta = _weight_vector(names, model)
    attribution_rows = feature_attribution(names, sims, weights)
    decomposition = decompose(cosine, attribution_rows, fit_source, model, fit_meta)
    band = coverage_band(decomposition)
    # The agreement (side-by-side) headline reuses spec-27's defined rule verbatim
    # over the SAME named features — the default view the operator reads first.
    agreement_rows = ce.build_comparison(cosine, features)

    # fraction_calibrated is FALSE until the surface is promoted on HRS (it ships
    # `uncalibrated`); the operator decision keeps the side-by-side as the
    # headline and gates the numeric fraction behind this flag.
    fraction_calibrated = False

    results = build_results(
        cosine=cosine,
        attribution_rows=attribution_rows,
        agreement_rows=agreement_rows,
        decomposition=decomposition,
        band=band,
        inputs_source=inputs_source,
        fraction_calibrated=fraction_calibrated,
        provenance=provenance,
    )
    envelope = compose_envelope(
        target_path=target_path, results=results,
        licenses_text=args.licenses, does_not_license_text=args.does_not_license,
    )
    return _emit(envelope, out_path=out_json, md_path=out_md, to_stdout=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
