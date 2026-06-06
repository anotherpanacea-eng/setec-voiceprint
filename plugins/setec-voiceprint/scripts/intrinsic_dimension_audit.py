#!/usr/bin/env python3
"""intrinsic_dimension_audit.py — clean-room PHD intrinsic-dimension evidence.

A topological discrimination signal: the **persistent-homology dimension
(PHD)** of a text's contextual-embedding point cloud. Human prose tends to a
*higher* intrinsic dimension than AI prose, and the axis is reported in the
literature as the single most orthogonal to perplexity / lexical signals
(Tulchinskii et al. 2023, "Intrinsic Dimension Estimation for Robust Detection
of AI-Generated Texts", arXiv:2306.04723). Evidence, not verdict.

Spec: ``specs/14-intrinsic-dimension-phd.md``.

CLEAN-ROOM + NO-TDA-DEPENDENCY DECISION
=======================================
The upstream reference implementation (the "GPTID" repo accompanying
Tulchinskii et al.) is **not vendored** — its license was not confirmed
permissive, and the PHD math is published, so this module is a **clean-room
re-implementation from the paper**, not a port of upstream code.

The published PHD estimator rests on the *persistence-homology dimension* of a
point cloud, computed from the total weight of the H0 persistence diagram. A
generic computation would reach for an external topological-data-analysis (TDA)
library (``ripser`` / ``gph``) — but neither is installed here and both carry
license questions, so we **add no TDA dependency**. Instead we exploit a clean
equivalence:

    For a finite point cloud, the H0 (connected-components) persistent-homology
    death-times under the Vietoris-Rips / Cech filtration are EXACTLY the edge
    weights of a Euclidean minimum spanning tree (MST) of the cloud.

(Each MST edge is the threshold at which two previously-separate components
merge — i.e. a 0-dimensional homology class dies. The single never-dying
component contributes no finite death-time.) So the H0 persistence sum

    E_0^alpha(X) = sum over H0-death-times d of d**alpha

equals ``sum(mst_edge_weight ** alpha)``. We compute the MST with
``scipy.sparse.csgraph.minimum_spanning_tree`` (scipy is a core dependency) —
no ``ripser``/``gph``, no vendored upstream code.

PHD itself is then read off the *scaling* of that persistence functional with
sub-sample size. For an intrinsic dimension d, ``E_0^alpha(X_n)`` grows like
``n ** ((d - alpha) / d)``; taking alpha = 1.0 (the published default) gives

    log E_0(n) ~= (1 - 1/d) * log n + const.

Fitting the slope ``s`` of ``log E_0`` against ``log n`` over several seeded
random sub-samples of increasing size yields ``PHD = 1 / (1 - s)``.

INJECTABLE EMBEDDER
===================
The embedder is **injectable** and is never loaded in tests. ``audit()`` takes
an ``embed`` callable ``(texts: list[str]) -> ndarray of shape (N, D)``; tests
pass a deterministic stub. At runtime, ``main()`` constructs an
``EmbeddingBackend`` (Apache/MIT model via ``embedding_backend.py``, honoring
that module's alias convention) and passes ``backend.encode`` as ``embed``. No
model is loaded anywhere at import or during the build/tests.

CLI:

    python3 scripts/intrinsic_dimension_audit.py TARGET [--model ALIAS] \
        [--json] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np  # type: ignore

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore  # noqa: E402
from output_schema import build_output  # type: ignore  # noqa: E402

TASK_SURFACE = "intrinsic_dimension"
TOOL_NAME = "intrinsic_dimension_audit"
SCRIPT_VERSION = "0.1.0"

# Embedding tokens/sentences below this count makes the PHD scaling fit
# unstable: too few points to populate the log-log regression. We warn rather
# than refuse, since the floor is soft and operator-judgment-dependent.
MIN_STABLE_POINTS = 200

# Default exponent for the persistence functional. alpha=1.0 is the published
# default (sum of MST edge weights, the H0 persistence total).
DEFAULT_ALPHA = 1.0

# Default seeded sub-sample schedule (fractions of the full cloud). Each
# fraction is averaged over several seeds for a stable E_0(n) estimate.
DEFAULT_SAMPLE_FRACTIONS: tuple[float, ...] = (0.4, 0.55, 0.7, 0.85, 1.0)
DEFAULT_N_SEEDS = 7
DEFAULT_SEED = 1729

# A point cloud needs at least a few points before an MST is meaningful.
_MIN_POINTS_PER_SAMPLE = 4

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[A-Za-z']+")


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text.lower()))


def split_units(text: str) -> list[str]:
    """Split target text into embedding units (sentences, with a token-ish
    fallback for sentence-poor inputs).

    Sentences are the natural unit for a contextual-embedding cloud: each
    sentence becomes one point. When the text has very few sentence
    boundaries (e.g. a single run-on block) we fall back to whitespace
    word-chunks so the cloud still has enough points to fit the scaling
    law. Pure / deterministic.
    """
    units = [s.strip() for s in _SENT_SPLIT_RE.split(text) if s.strip()]
    if len(units) >= _MIN_POINTS_PER_SAMPLE:
        return units
    # Sentence-poor: fall back to word tokens so we still produce a cloud.
    return [w for w in re.split(r"\s+", text.strip()) if w]


# ----------------------------------------------------------------------
# Clean-room PHD estimator (numpy + scipy MST). No TDA library, no vendored
# upstream code. See module docstring for the H0-persistence == MST-edge-
# weights derivation.
# ----------------------------------------------------------------------


def h0_persistence_sum(points: np.ndarray, *, alpha: float = DEFAULT_ALPHA) -> float:
    """Total H0 persistent-homology weight of a point cloud.

    Equal to ``sum(d ** alpha)`` over the H0 death-times ``d``, which for a
    finite cloud are exactly the Euclidean MST edge weights (see module
    docstring). Returns 0.0 for clouds with fewer than 2 points (no edges).

    Implementation: build the full pairwise Euclidean distance matrix, hand
    it to ``scipy.sparse.csgraph.minimum_spanning_tree``, and sum the
    surviving edge weights raised to ``alpha``. The distance matrix is
    O(n^2) but n here is a sub-sample of an embedding cloud (hundreds to a
    few thousand points), well within memory.
    """
    from scipy.sparse.csgraph import minimum_spanning_tree  # type: ignore

    n = points.shape[0]
    if n < 2:
        return 0.0
    # Pairwise Euclidean distances. ||a-b||^2 = |a|^2 + |b|^2 - 2 a.b.
    sq = np.sum(points * points, axis=1)
    gram = points @ points.T
    d2 = sq[:, None] + sq[None, :] - 2.0 * gram
    np.maximum(d2, 0.0, out=d2)  # guard tiny negatives from float error
    dist = np.sqrt(d2)
    # minimum_spanning_tree treats zero entries as "no edge", so a genuine
    # zero-distance (duplicate points) edge would be dropped. Duplicates
    # contribute a zero death-time anyway (d**alpha == 0 for alpha>0), so
    # dropping them does not change the persistence sum.
    mst = minimum_spanning_tree(dist)
    weights = mst.data  # the (n-1) MST edge weights (nonzero entries)
    if weights.size == 0:
        return 0.0
    if alpha == 1.0:
        return float(np.sum(weights))
    return float(np.sum(np.power(weights, alpha)))


def estimate_phd(
    points: np.ndarray,
    *,
    alpha: float = DEFAULT_ALPHA,
    sample_fractions: Sequence[float] = DEFAULT_SAMPLE_FRACTIONS,
    n_seeds: int = DEFAULT_N_SEEDS,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Estimate the persistent-homology dimension (PHD) of a point cloud.

    Clean-room implementation of the Tulchinskii et al. 2023 PHD estimator.
    For an intrinsic dimension ``d``, the H0 persistence functional scales as
    ``E_0^alpha(X_n) ~ n ** ((d - alpha) / d)``; with ``alpha=1`` that is
    ``log E_0(n) ~= (1 - 1/d) * log n + c``. We fit the slope ``s`` of
    ``log E_0`` vs ``log n`` by ordinary least squares over seeded sub-samples
    of increasing size, then read off ``PHD = 1 / (1 - s)``.

    Fully deterministic given ``seed`` (uses a seeded ``numpy.random.default_
    rng`` per sub-sample size). Returns a dict with the scalar ``phd`` plus
    the fit diagnostics (slope, intercept, R^2, the (n, E_0) samples used).
    ``phd`` is ``None`` when the cloud is too small or the fit is degenerate
    (e.g. slope >= 1, which would imply a non-positive dimension).
    """
    n_points = int(points.shape[0])
    diagnostics: dict[str, Any] = {
        "alpha": alpha,
        "n_points": n_points,
        "sample_fractions": list(sample_fractions),
        "n_seeds": int(n_seeds),
        "seed": int(seed),
        "samples": [],  # list of {"n": int, "mean_E0": float, "log_n", "log_E0"}
        "slope": None,
        "intercept": None,
        "r_squared": None,
        "phd": None,
    }
    if n_points < _MIN_POINTS_PER_SAMPLE:
        return diagnostics

    log_n: list[float] = []
    log_e0: list[float] = []
    for frac in sample_fractions:
        m = max(_MIN_POINTS_PER_SAMPLE, int(round(frac * n_points)))
        m = min(m, n_points)
        e0_vals: list[float] = []
        if m == n_points:
            # Full cloud: deterministic, no sampling needed (one value).
            e0_vals.append(h0_persistence_sum(points, alpha=alpha))
        else:
            for s in range(n_seeds):
                # Seed deterministically from (base seed, target size, seed
                # index) so the whole estimate is reproducible.
                rng = np.random.default_rng(seed + 1000 * m + s)
                idx = rng.choice(n_points, size=m, replace=False)
                e0_vals.append(h0_persistence_sum(points[idx], alpha=alpha))
        mean_e0 = float(np.mean(e0_vals)) if e0_vals else 0.0
        diagnostics["samples"].append(
            {"n": m, "mean_E0": mean_e0, "n_seeds_used": len(e0_vals)}
        )
        if m > 0 and mean_e0 > 0.0:
            log_n.append(math.log(m))
            log_e0.append(math.log(mean_e0))

    # Need at least two distinct (log n) points to fit a slope.
    if len(log_n) < 2 or len(set(log_n)) < 2:
        return diagnostics

    xs = np.asarray(log_n, dtype="float64")
    ys = np.asarray(log_e0, dtype="float64")
    slope, intercept = np.polyfit(xs, ys, 1)
    # R^2 of the linear fit.
    pred = slope * xs + intercept
    ss_res = float(np.sum((ys - pred) ** 2))
    ss_tot = float(np.sum((ys - np.mean(ys)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else None

    diagnostics["slope"] = float(slope)
    diagnostics["intercept"] = float(intercept)
    diagnostics["r_squared"] = r_squared

    # PHD = 1 / (1 - slope). slope in (0, 1) for a sensible positive dimension.
    if slope < 1.0 - 1e-9:
        phd = 1.0 / (1.0 - slope)
        if phd > 0.0 and math.isfinite(phd):
            diagnostics["phd"] = float(phd)
    return diagnostics


# ----------------------------------------------------------------------
# Audit (injectable embedder)
# ----------------------------------------------------------------------


def audit(
    target_text: str,
    *,
    embed: Callable[[list[str]], Any],
    embedding_model_id: str,
    embedding_identifier_block: dict[str, Any] | None = None,
    alpha: float = DEFAULT_ALPHA,
    sample_fractions: Sequence[float] = DEFAULT_SAMPLE_FRACTIONS,
    n_seeds: int = DEFAULT_N_SEEDS,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Run the PHD audit. Returns the ``results`` dict for ``build_output``.

    ``embed`` is the **injection point**: a callable mapping a list of text
    units to a 2-D array-like of shape ``(N, D)``. Production callers pass
    ``EmbeddingBackend.encode``; tests pass a deterministic stub. No model is
    loaded here — ``audit`` only calls ``embed``.

    ``embedding_model_id`` / ``embedding_identifier_block`` record provenance
    for the chosen embedder (the model id is reported in ``results``).

    The audit is **uncalibrated**: it reports the scalar PHD, the point count,
    and the embedding-model id. It emits **no band and no threshold** — there
    is no shipped operating point separating AI from human prose, and the
    claim-license refuses any such verdict.
    """
    caveats: list[str] = []

    units = split_units(target_text)
    raw = embed(units)
    points = np.asarray(raw, dtype="float64")
    if points.ndim == 1:
        # Defensive: a stub/encoder returning a flat vector per call would be
        # ambiguous; treat as (N, 1) so the MST math still runs.
        points = points.reshape(-1, 1)
    n_points = int(points.shape[0]) if points.size else 0

    if n_points < MIN_STABLE_POINTS:
        caveats.append("short_text_phd_estimate_unstable")

    estimate = estimate_phd(
        points,
        alpha=alpha,
        sample_fractions=sample_fractions,
        n_seeds=n_seeds,
        seed=seed,
    )
    phd = estimate["phd"]
    if phd is None:
        caveats.append("phd_estimate_unavailable_degenerate_or_too_small")

    # Uncalibrated discipline: this surface ships no threshold / band. State it
    # explicitly so consumers reading the evidence pack don't infer a verdict.
    caveats.append("uncalibrated_no_threshold_no_band")

    embedding_dim = int(points.shape[1]) if points.ndim == 2 and points.size else 0

    return {
        "phd": phd,
        "n_points": n_points,
        "embedding_model": {
            "id": embedding_model_id,
            "identifier_block": embedding_identifier_block,
        },
        "embedding_dim": embedding_dim,
        "method": {
            "estimator": "persistent_homology_dimension_clean_room",
            "h0_persistence": "scipy_minimum_spanning_tree",
            "alpha": alpha,
            "fit": "log_E0_vs_log_n_least_squares; phd = 1/(1-slope)",
        },
        "fit": {
            "slope": estimate["slope"],
            "intercept": estimate["intercept"],
            "r_squared": estimate["r_squared"],
            "samples": estimate["samples"],
            "sample_fractions": estimate["sample_fractions"],
            "n_seeds": estimate["n_seeds"],
            "seed": estimate["seed"],
        },
        # No "band", no "verdict", no "threshold" keys: by design.
        "caveats": caveats,
    }


DEFAULT_LICENSES = (
    "the intrinsic (PHD) dimension of the text's embedding cloud under "
    "model M — a single scalar describing the persistent-homology fractal "
    "dimension of the per-unit contextual-embedding point cloud. It is a "
    "geometric measurement on one named embedding model; in the literature "
    "human prose tends to a higher intrinsic dimension than AI prose, so the "
    "scalar is discrimination *evidence* on an axis orthogonal to perplexity "
    "and lexical signals. It is a measurement, not a verdict."
)

DEFAULT_DOES_NOT_LICENSE = (
    "any AI/human authorship verdict. The audit ships uncalibrated: it "
    "emits no band and no threshold, because there is no framework-calibrated "
    "operating point separating AI from human prose for this scalar. An "
    "operator who wants a verdict must supply their own thresholds calibrated "
    "on their model + corpus + register and take responsibility for them. The "
    "estimate is unstable on short text (too few embedding units to fit the "
    "scaling law — see the short-text caveat) and is dependent on the chosen "
    "embedding model: PHD values are not comparable across different "
    "embedders, so a result only licenses comparison within one fixed model. "
    "It does not substitute for stylometric, perplexity-ratio, or other "
    "framework audits — it complements them on a distinct topological axis."
)


def _claim_license(results: dict[str, Any]) -> ClaimLicense:
    caveats = list(results.get("caveats", []))
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=DEFAULT_LICENSES,
        does_not_license=DEFAULT_DOES_NOT_LICENSE,
        comparison_set={
            "mode": "single_document_uncalibrated",
            "embedding_model": results["embedding_model"]["id"],
            "estimator": results["method"]["estimator"],
        },
        additional_caveats=caveats
        + [
            "Uncalibrated — no band, no verdict, no shipped threshold.",
            "PHD is embedding-model dependent: values are comparable only "
            "within one fixed model, not across embedders.",
            "Short text destabilizes the estimate (too few embedding units "
            "for a reliable log-log scaling fit).",
        ],
        references=[
            "Tulchinskii et al. 2023, 'Intrinsic Dimension Estimation for "
            "Robust Detection of AI-Generated Texts' (arXiv:2306.04723)",
            "plugins/setec-voiceprint/specs/14-intrinsic-dimension-phd.md",
        ],
    )


def compose_envelope(
    *,
    target_path: Path | str | None,
    target_words: int,
    results: dict[str, Any],
) -> dict[str, Any]:
    caveats = list(results.get("caveats", []))
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=None,
        results=results,
        claim_license=_claim_license(results),
        available=True,
        warnings=caveats,
    )


def render_markdown(envelope: dict[str, Any]) -> str:
    results = envelope["results"]
    target = envelope["target"]
    phd = results.get("phd")
    phd_text = f"{phd:.4f}" if phd is not None else "(unavailable)"
    fit = results.get("fit", {})
    lines: list[str] = [
        "# Intrinsic-Dimension Audit (clean-room PHD)",
        "",
        f"- **Target:** `{target.get('path')}` ({target.get('words')} words)",
        f"- **Embedding model:** `{results['embedding_model']['id']}`",
        f"- **Points (embedding units):** {results.get('n_points')}",
        f"- **Estimator:** `{results['method']['estimator']}` "
        f"(H0 persistence via {results['method']['h0_persistence']})",
        "",
        "## Result",
        "",
        f"**PHD (intrinsic dimension):** {phd_text}",
        f"**Fit:** slope={fit.get('slope')}, R²={fit.get('r_squared')}",
        "",
        "_Uncalibrated: no band, no verdict, no threshold. Discrimination "
        "evidence only._",
        "",
        "## Caveats",
        "",
    ]
    caveats = results.get("caveats") or []
    if caveats:
        for c in caveats:
            lines.append(f"- {c}")
    else:
        lines.append("(none surfaced)")
    lines.append("")
    lines.append("## Claim license")
    lines.append("")
    lines.append((envelope.get("claim_license_rendered") or "").rstrip())
    lines.append("")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Intrinsic-dimension audit (clean-room PHD): persistent-homology "
            "dimension of a text's contextual-embedding cloud. Uncalibrated "
            "discrimination evidence — no verdict."
        ),
    )
    p.add_argument("target", help="Path to target text file (UTF-8).")
    p.add_argument(
        "--model",
        default=None,
        help=(
            "Embedding model alias (e.g. 'mxbai', 'gemma', 'minilm') or full "
            "HuggingFace id. Resolved via embedding_backend; defaults to the "
            "backend's DEFAULT_MODEL. Apache/MIT models preferred."
        ),
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit the JSON envelope instead of a markdown report.",
    )
    p.add_argument(
        "--out", default=None,
        help="Write output to this path instead of stdout.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    target_path = Path(args.target).expanduser()
    if not target_path.is_file():
        sys.stderr.write(f"error: target file not found at {target_path}\n")
        return 1
    try:
        target_text = target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        sys.stderr.write(f"error: target not valid UTF-8: {exc}\n")
        return 1

    target_words = count_words(target_text)

    # Construct the runtime embedder. Imported lazily and inside main() so the
    # module imports (and the test suite) never touch embedding_backend / load
    # a model. EmbeddingBackend.encode is the injected `embed` callable.
    try:
        from embedding_backend import (  # type: ignore
            EmbeddingBackend,
            EmbeddingBackendError,
            resolve_model_arg,
        )
    except ImportError as exc:  # pragma: no cover - dep-gated
        sys.stderr.write(
            f"error: embedding backend unavailable ({exc}). The intrinsic-"
            "dimension audit needs the embedding tier (transformers + torch + "
            "sentence-transformers).\n"
        )
        return 3

    backend = EmbeddingBackend(model_id=resolve_model_arg(args.model))

    try:
        results = audit(
            target_text,
            embed=backend.encode,
            embedding_model_id=backend.model_id,
            embedding_identifier_block=backend.identifier_block(),
        )
    except EmbeddingBackendError as exc:
        sys.stderr.write(f"error: embedding failed: {exc}\n")
        return 3

    envelope = compose_envelope(
        target_path=target_path,
        target_words=target_words,
        results=results,
    )

    text_out = (
        json.dumps(envelope, indent=2, default=str)
        if args.json
        else render_markdown(envelope)
    )
    if args.out:
        Path(args.out).write_text(text_out, encoding="utf-8")
        sys.stderr.write(f"Wrote output to {args.out}\n")
    else:
        sys.stdout.write(text_out + ("\n" if not text_out.endswith("\n") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
