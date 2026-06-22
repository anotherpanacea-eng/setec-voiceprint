#!/usr/bin/env python3
"""pan_voight_kampff_benchmark.py — the Voight-Kampff benchmark harness.

Runs setec-voiceprint's **existing** discrimination detectors over the
PAN@CLEF Voight-Kampff Subtask-1 dataset (binary human-vs-AI, where the
LLM was instructed to MIMIC a specific human author) and scores them with
the **official PAN metric suite** (ROC-AUC, Brier, c@1, F1, F0.5u, plus
their mean), alongside the three official PAN baselines. This is a
**held-out external-validation eval HARNESS** — NOT a new detection
surface, NOT a ``capabilities.d/`` drop-in, and it emits NO
``_golden_capabilities/`` fragment and NO ``claim_license_surfaces/``
file.

Pipeline: PAN release --(adapter)--> SETEC manifest --(runner)-->
per-instance ``(detector, id, oriented_score, label)`` rows --(scorer)-->
PAN metric vectors --(assembler)--> one JSON benchmark report.

--------------------------------------------------------------------------
ANTI-GOODHART (load-bearing; the benchmark is external validation, never a
tuning / calibration / selection target):

  * The harness **writes only** a report (+ optional per-instance sidecar
    + the operator's --text-dir). It writes NOTHING to any threshold file,
    ``capabilities.d/`` entry, ``claim_license_surfaces/`` file,
    ``_golden_capabilities/`` fragment, or calibration-readiness matrix.
    (AC-12) — enforced structurally: this module has no code path that
    opens any of those for writing.
  * It imports NO threshold-fitting / calibration-fitting symbol. It may
    *read* a detector's already-calibrated threshold to report a
    thresholded metric; it never calls a fitter. (AC-13)
  * PAN labels flow ONE WAY: PAN -> adapter -> runner -> scorer -> report.
    No function consumes the computed metrics to emit an operating point,
    a "best detector" selection, or a calibration parameter; the report
    is a terminal artifact. (AC-14)
  * The Brier probability transform is DECLARED, FIXED, and LABEL-FREE
    (see ``oriented_score_to_probability``) — never fitted to a PAN
    metric. (D7)
  * Thresholded cells (c@1 / F1 / F0.5u) are ``null`` with reason
    ``"no_operating_point_without_fitting_to_pan"`` when no operator
    operating-point is supplied. The harness NEVER sweeps a threshold
    against the PAN labels (PAN's own ``--optimize-score`` sweep is
    deliberately not re-implemented). (AC-17, D8)
  * The report carries an explicit ``anti_goodhart`` block. (AC-15)
--------------------------------------------------------------------------

Upstream / prior art (cited in PR + changelog):
  - PAN@CLEF 2025 Generative AI Authorship Verification, Subtask 1
    (Voight-Kampff). Task page:
    https://pan.webis.de/clef25/pan25-web/generated-content-analysis.html
  - Dataset: Zenodo record 14962653 (reused by the 2026 edition).
  - Official code + TIRA evaluator (Apache-2.0):
    https://github.com/pan-webis-de/pan25-generative-ai-authorship-verification
  - Binoculars baseline: Hans et al. 2024, arXiv:2401.12070.

Usage:
    python3 scripts/calibration/pan_voight_kampff_benchmark.py \
        --manifest .pan_vk_manifest.jsonl \
        --detectors binoculars_audit,length_ratio_standin \
        --split validation --json --out report.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any, Callable

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
CALIB = Path(__file__).resolve().parent
if str(CALIB) not in sys.path:
    sys.path.insert(0, str(CALIB))

from validation_harness import (  # noqa: E402
    label_for_status,
    load_manifest_entries,
)
import pan_metrics  # noqa: E402

HARNESS_VERSION = "0.1.0"
REPORT_SCHEMA_VERSION = "1.0"
REPORT_KIND = "voight_kampff_benchmark"

# SETEC binary-label convention (matches manifest_validator / the adapter):
# AI/machine = positive (1); human = negative (0).
POSITIVE_STATUSES = {"ai_generated", "ai_generated_from_outline"}
NEGATIVE_STATUSES = {"pre_ai_human"}

# PAN-published baseline numbers for the two baselines this harness does
# NOT recompute in M1 (recomputing TF-IDF+SVM / PPMd needs the Apache-2.0
# PAN baseline code — an out-of-M1 seam). Cited, not recomputed.
PAN_PUBLISHED_BASELINES = ("tf_idf_svm", "ppmd")

ANTI_GOODHART_STATEMENT = (
    "These scores are external validation only. Do NOT fit detector "
    "thresholds, calibration parameters, or surface selection to this "
    "benchmark. The harness writes no threshold/calibration/registry "
    "artifact and reads no PAN-derived score back into detector behavior."
)


# =========================================================================
# Detector orientation registry
# =========================================================================
# One row per detector. Adding a detector = one row (no shared-dict edit).
#
#   detector_id -> {
#       "task_surface": <surface label for the report>,
#       "score_name":   <headline scalar name>,
#       "orientation":  "lower_is_ai" | "higher_is_ai",
#       "orientation_basis": <human-readable polarity justification>,
#       "scorer":       a callable returning a normalized per-instance
#                       dict {available, score, band, reason},
#   }
#
# ``orientation`` declares the detector's RAW polarity; the runner flips
# raw scores so that *higher = more-AI* before metrics, and records the
# orientation it applied. No label-derived orientation (that would leak
# labels — D6).
# =========================================================================


def _band_to_non_response(band: str | None) -> bool:
    """The detector's existing two-threshold band IS the label-free
    abstention zone. A band of ``indeterminate`` / ``uncalibrated`` /
    ``unavailable`` means the detector declines to answer this instance
    (mapped to PAN's 0.5 non-response by the scorer). ``ai_likely`` /
    ``human_likely`` are answered."""
    return band not in ("ai_likely", "human_likely")


def make_binoculars_scorer(
    *,
    score_fn: Callable[[Any, str], list[float]] | None = None,
    scorer_backend: Any = None,
    observer_backend: Any = None,
    threshold_low: float | None = None,
    threshold_high: float | None = None,
):
    """Build a per-instance scorer that import-and-calls
    ``binoculars_audit.audit`` (deterministic; matches how
    validation_harness calls audit functions). ``score_fn`` is the
    binoculars test-injection hook (``score_fn(backend, text) ->
    surprisal series``), keeping CI CPU-only and model-free.

    Returns ``{available, score, band, reason}`` per instance. The
    headline scalar is ``perplexity_ratio`` (verified key); its raw
    polarity is **lower = more AI** (binoculars' ``ratio < low ->
    ai_likely``), so the runner sign-flips for metrics.
    """
    import binoculars_audit as bin_audit

    def score(text: str) -> dict[str, Any]:
        if scorer_backend is None or observer_backend is None:
            # No backend wired (no model, no injected stub): the detector
            # is unavailable for this run. Mirrors an error envelope.
            return {
                "available": False,
                "score": None,
                "band": None,
                "reason": "binoculars_backend_unavailable",
            }
        results = bin_audit.audit(
            text,
            scorer=scorer_backend,
            observer=observer_backend,
            threshold_low=threshold_low,
            threshold_high=threshold_high,
            score_fn=score_fn,
        )
        ratio = results.get("perplexity_ratio")
        if ratio is None:
            return {
                "available": False,
                "score": None,
                "band": results.get("verdict_band"),
                "reason": "perplexity_ratio_unavailable",
            }
        return {
            "available": True,
            "score": float(ratio),
            "band": results.get("verdict_band"),
            "reason": None,
        }

    return score


def make_length_ratio_standin_scorer(
    *,
    threshold_low: float | None = None,
    threshold_high: float | None = None,
):
    """A trivial, deterministic, stdlib stand-in detector so the ENTIRE
    adapter->runner->scorer->report pipeline runs end-to-end on CPU with
    ZERO model loads (AC-6).

    Score = the fraction of alphabetic characters in the text (a stable
    char-ratio in [0, 1]). This is NOT a real AI detector and licenses no
    discrimination claim; it exists only to exercise the plumbing. Its
    declared raw polarity is ``higher_is_ai`` (arbitrary but fixed).

    If a two-threshold band is supplied, scores inside ``[low, high]`` are
    the label-free non-response zone, exactly like the model detectors.
    """

    def score(text: str) -> dict[str, Any]:
        if not text:
            return {
                "available": False,
                "score": None,
                "band": None,
                "reason": "empty_text",
            }
        alpha = sum(1 for c in text if c.isalpha())
        ratio = alpha / len(text)
        band: str | None
        if threshold_low is None or threshold_high is None:
            band = "uncalibrated"
        elif ratio < threshold_low:
            band = "human_likely"  # higher_is_ai => low score = human
        elif ratio > threshold_high:
            band = "ai_likely"
        else:
            band = "indeterminate"
        return {"available": True, "score": float(ratio), "band": band, "reason": None}

    return score


def build_detector_registry(
    detector_ids: list[str],
    *,
    binoculars_kwargs: dict[str, Any] | None = None,
    standin_kwargs: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return the orientation registry for the requested detectors.

    M1 wires two CPU-clean detectors:
      - ``binoculars_audit`` (real first-party PAN baseline #3; via its
        injected ``score_fn`` for deterministic, model-free tests);
      - ``length_ratio_standin`` (the stdlib stand-in).

    The other discrimination surfaces (curvature / spectral / TOCSIN /
    intrinsic-dimension / rewriting-invariance / external-mirror) are
    real surfaces but need model/network deps — an out-of-M1 seam. They
    are intentionally NOT registered here so M1 stays CPU-clean; adding
    one later is a single new row + flipping its deps on.
    """
    binoculars_kwargs = binoculars_kwargs or {}
    standin_kwargs = standin_kwargs or {}
    available: dict[str, dict[str, Any]] = {
        "binoculars_audit": {
            "task_surface": "binoculars_discrimination",
            "score_name": "perplexity_ratio",
            "orientation": "lower_is_ai",
            "orientation_basis": (
                "binoculars_discrimination surface: ratio < threshold_low "
                "= ai_likely (lower ratio = more AI); sign-flipped so "
                "higher = more-AI for the PAN metrics."
            ),
            "scorer": make_binoculars_scorer(**binoculars_kwargs),
        },
        "length_ratio_standin": {
            "task_surface": "none_eval_standin",
            "score_name": "alpha_char_ratio",
            "orientation": "higher_is_ai",
            "orientation_basis": (
                "stdlib stand-in detector (NOT a real AI detector): "
                "declared raw polarity higher = more-AI; no sign flip. "
                "Exists only to exercise the pipeline on CPU."
            ),
            "scorer": make_length_ratio_standin_scorer(**standin_kwargs),
        },
    }
    registry: dict[str, dict[str, Any]] = {}
    for did in detector_ids:
        if did not in available:
            raise SystemExit(
                f"Unknown / out-of-M1 detector {did!r}. M1 wires: "
                f"{sorted(available)}. The model-tier discrimination "
                "surfaces are a named out-of-M1 seam (need model deps)."
            )
        registry[did] = available[did]
    return registry


def oriented_score_to_probability(
    oriented_scores: list[float],
) -> list[float]:
    """Map oriented raw scores (higher = more-AI) into ``[0, 1]`` for
    Brier, via a DECLARED, FIXED, LABEL-FREE transform: min-max over the
    run's answered scores.

    This is a monotone, label-free rescale — it reads ONLY the scores,
    never the gold labels, and is NEVER fitted to maximize a PAN metric
    (D7, anti-Goodhart). A degenerate all-equal run maps to a constant
    0.5. The transform is recorded in the report so a consumer can see
    exactly what was applied.
    """
    if not oriented_scores:
        return []
    lo = min(oriented_scores)
    hi = max(oriented_scores)
    if hi - lo < 1e-12:
        return [0.5 for _ in oriented_scores]
    return [(s - lo) / (hi - lo) for s in oriented_scores]


PROBABILITY_TRANSFORM_NOTE = (
    "min_max_over_run_answered_scores: a monotone, label-free rescale of "
    "the oriented (higher=AI) scores into [0,1]; degenerate all-equal runs "
    "map to constant 0.5. NOT a calibrated probability and NEVER fitted to "
    "a PAN metric."
)


# =========================================================================
# Runner
# =========================================================================


def run_detector_over_manifest(
    detector_id: str,
    spec: dict[str, Any],
    manifest_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run one detector over the manifest, streaming one text at a time.

    Returns a result dict with the oriented ``(id, score, label, band)``
    rows plus skip accounting. Bounded memory: reads each entry's text
    file on demand and never holds the whole corpus of texts at once
    (only the small per-row score tuples accumulate).
    """
    orientation = spec["orientation"]
    score = spec["scorer"]
    rows: list[dict[str, Any]] = []
    n_skipped = 0
    skipped_reasons: dict[str, int] = {}

    for entry in manifest_entries:
        label = label_for_status(
            entry.get("ai_status"), POSITIVE_STATUSES, NEGATIVE_STATUSES
        )
        if label is None:
            n_skipped += 1
            skipped_reasons["no_binary_label"] = (
                skipped_reasons.get("no_binary_label", 0) + 1
            )
            continue
        text_path = entry.get("_resolved_path") or entry.get("path")
        try:
            text = Path(text_path).read_text(encoding="utf-8")
        except (OSError, TypeError) as exc:  # missing text file
            n_skipped += 1
            reason = f"text_unreadable:{type(exc).__name__}"
            skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
            continue

        out = score(text)
        if not out.get("available"):
            n_skipped += 1
            reason = str(out.get("reason") or "detector_unavailable")
            skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
            continue

        raw = float(out["score"])
        # Orientation flip: ensure higher = more-AI for the metrics.
        oriented = -raw if orientation == "lower_is_ai" else raw
        rows.append({
            "id": entry.get("id"),
            "label": int(label),
            "raw_score": raw,
            "oriented_score": oriented,
            "band": out.get("band"),
        })

    return {
        "orientation_applied": orientation,
        "rows": rows,
        "n_scored": len(rows),
        "n_skipped": n_skipped,
        "skipped_reasons": skipped_reasons,
    }


def predictions_with_operating_point(
    rows: list[dict[str, Any]],
    *,
    has_operating_point: bool,
) -> list[float]:
    """Turn oriented scores into PAN predictions in ``{<0.5, 0.5, >0.5}``
    for the thresholded metrics, using the detector's EXISTING
    two-threshold band as the abstention model:

      * band == ``ai_likely``     -> 1.0  (answered: machine)
      * band == ``human_likely``  -> 0.0  (answered: human)
      * any other band (indeterminate / uncalibrated / unavailable / None)
        -> 0.5 (NON-RESPONSE, the label-free abstention zone)

    The band comes from the detector's own operator/operator-supplied
    operating point — NEVER from peeking at the PAN labels. When no
    operating point is supplied (``has_operating_point`` False), this
    function is not used (the scorer reports thresholded cells as null).
    """
    preds: list[float] = []
    for r in rows:
        if not has_operating_point:
            preds.append(pan_metrics.NON_RESPONSE)
            continue
        band = r.get("band")
        if band == "ai_likely":
            preds.append(1.0)
        elif band == "human_likely":
            preds.append(0.0)
        else:
            preds.append(pan_metrics.NON_RESPONSE)
    return preds


# =========================================================================
# Report assembly
# =========================================================================


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def assemble_report(
    *,
    manifest_path: str,
    split: str,
    detector_registry: dict[str, dict[str, Any]],
    manifest_entries: list[dict[str, Any]],
    has_operating_point: bool,
    n_resamples: int,
    confidence_level: float,
    seed: int | None,
    per_instance_sink: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Orchestrate runner -> scorer -> the §4 report shape."""
    n_human = sum(
        1 for e in manifest_entries
        if label_for_status(e.get("ai_status"), POSITIVE_STATUSES, NEGATIVE_STATUSES) == 0
    )
    n_machine = sum(
        1 for e in manifest_entries
        if label_for_status(e.get("ai_status"), POSITIVE_STATUSES, NEGATIVE_STATUSES) == 1
    )

    detectors_out: list[dict[str, Any]] = []
    for detector_id, spec in detector_registry.items():
        run = run_detector_over_manifest(detector_id, spec, manifest_entries)
        rows = run["rows"]
        labels = [r["label"] for r in rows]
        oriented = [r["oriented_score"] for r in rows]
        probabilities = oriented_score_to_probability(oriented)

        # Rank/probability metrics (roc_auc, brier) score on the
        # label-free probability transform of the oriented scores.
        # Thresholded metrics (c@1, f1, f05u) score on the band-derived
        # predictions; null without an operating point.
        rank_metrics = pan_metrics.score_all(
            labels,
            probabilities,
            has_operating_point=False,  # always emit rank cells only here
            n_resamples=n_resamples,
            confidence_level=confidence_level,
            seed=seed,
        )
        metrics: dict[str, Any] = {
            k: rank_metrics[k] for k in pan_metrics.RANK_METRICS
        }
        if has_operating_point:
            preds = predictions_with_operating_point(
                rows, has_operating_point=True
            )
            thresholded = pan_metrics.score_all(
                labels,
                preds,
                has_operating_point=True,
                n_resamples=n_resamples,
                confidence_level=confidence_level,
                seed=seed,
            )
            for k in pan_metrics.THRESHOLDED_METRICS:
                metrics[k] = thresholded[k]
        else:
            for k in pan_metrics.THRESHOLDED_METRICS:
                metrics[k] = {
                    "value": None,
                    "ci_low": None,
                    "ci_high": None,
                    "ci_method": None,
                    "reason": "no_operating_point_without_fitting_to_pan",
                }
        metrics["pan_mean"] = {
            "value": pan_metrics.pan_mean(
                {k: metrics[k]["value"] for k in pan_metrics.PAN_METRIC_KEYS}
            )
        }

        if per_instance_sink is not None:
            for r in rows:
                per_instance_sink.append({"detector": detector_id, **r})

        detectors_out.append({
            "detector": detector_id,
            "task_surface": spec["task_surface"],
            "score_name": spec["score_name"],
            "orientation": run["orientation_applied"],
            "orientation_basis": spec["orientation_basis"],
            "n_scored": run["n_scored"],
            "n_skipped": run["n_skipped"],
            "skipped_reasons": run["skipped_reasons"],
            "probability_transform": PROBABILITY_TRANSFORM_NOTE,
            "metrics": metrics,
            "operating_point": {
                "source": "operator_supplied" if has_operating_point else "none",
                "threshold": None,
                "note": (
                    "Thresholded metrics (c@1/f1/f05u) require an operating "
                    "point supplied via the detector's own two-threshold "
                    "band; if none is supplied they are null and only the "
                    "rank metrics (roc_auc) are reported. The harness NEVER "
                    "fits a threshold to the PAN labels."
                ),
            },
        })

    official_baselines: list[dict[str, Any]] = []
    for b in PAN_PUBLISHED_BASELINES:
        official_baselines.append({
            "baseline": b,
            "source": "pan_published",
            "metrics": None,
            "note": (
                "PAN-published numbers (cited, not recomputed in M1; "
                "recomputing needs the Apache-2.0 PAN baseline code — an "
                "out-of-M1 seam)."
            ),
        })
    binoculars_first_party = "binoculars_audit" in detector_registry
    official_baselines.append({
        "baseline": "binoculars",
        "source": "first_party" if binoculars_first_party else "pan_published",
        "maps_to_detector": "binoculars_audit" if binoculars_first_party else None,
        "note": (
            "voiceprint ships this PAN baseline as a detector; the "
            "first-party row IS the recomputed PAN-baseline-#3 result."
            if binoculars_first_party else
            "binoculars not in this run's detector set; PAN-published."
        ),
    })

    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "report_kind": REPORT_KIND,
        "generated_utc": _utc_now(),
        "dataset": {
            "name": "pan25-voight-kampff-subtask1",
            "zenodo_record": "14962653",
            "split": split,
            "n_instances": n_human + n_machine,
            "n_human": n_human,
            "n_machine": n_machine,
            "manifest_path": manifest_path,
            "provenance_note": (
                "Local-only; PAN redistribution-gated. NOT vendored. See "
                "NOTICE.md next to the staged text."
            ),
            "edition": "pan25 (dataset reused by pan26)",
        },
        "detectors": detectors_out,
        "official_baselines": official_baselines,
        "anti_goodhart": {
            "role": "external_held_out_validation",
            "is_tuning_target": False,
            "is_calibration_target": False,
            "is_selection_target": False,
            "statement": ANTI_GOODHART_STATEMENT,
        },
        "claim_note": (
            "Reports PAN-metric discrimination of named voiceprint "
            "detectors against the PAN Voight-Kampff Subtask-1 labels. Not "
            "a per-document verdict; not a calibration result."
        ),
        "harness_version": HARNESS_VERSION,
        "reproduce": {
            "cmd": (
                "python3 scripts/calibration/pan_voight_kampff_benchmark.py "
                "--manifest .pan_vk_manifest.jsonl --detectors "
                "binoculars_audit --split validation --json --out report.json"
            )
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    """A minimal markdown rendering of the report (optional)."""
    ds = report["dataset"]
    lines = [
        f"# Voight-Kampff Benchmark Report",
        "",
        f"- Dataset: `{ds['name']}` (Zenodo {ds['zenodo_record']}), "
        f"split `{ds['split']}`",
        f"- Instances: {ds['n_instances']} "
        f"({ds['n_human']} human, {ds['n_machine']} machine)",
        f"- Harness version: `{report['harness_version']}`",
        "",
        "## Detectors",
        "",
        "| detector | roc_auc | brier | c@1 | f1 | f0.5u | pan_mean |",
        "|---|---|---|---|---|---|---|",
    ]
    for d in report["detectors"]:
        m = d["metrics"]

        def cell(k: str) -> str:
            v = m.get(k, {}).get("value")
            return "—" if v is None else f"{v:.3f}"

        lines.append(
            f"| `{d['detector']}` | {cell('roc_auc')} | {cell('brier')} | "
            f"{cell('c_at_1')} | {cell('f1')} | {cell('f05u')} | "
            f"{cell('pan_mean')} |"
        )
    lines += [
        "",
        f"> {report['anti_goodhart']['statement']}",
        "",
    ]
    return "\n".join(lines)


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    """Public entry: load manifest, build registry, assemble report.

    Kept import-friendly (no I/O side effects beyond reading the
    manifest/text) so tests can call it directly and assert the report
    shape + the anti-Goodhart no-write invariant.
    """
    manifest_entries = load_manifest_entries(args.manifest)
    detector_ids = [d.strip() for d in args.detectors.split(",") if d.strip()]

    binoculars_kwargs = getattr(args, "_binoculars_kwargs", {}) or {}
    standin_kwargs = getattr(args, "_standin_kwargs", {}) or {}
    registry = build_detector_registry(
        detector_ids,
        binoculars_kwargs=binoculars_kwargs,
        standin_kwargs=standin_kwargs,
    )

    per_instance_sink: list[dict[str, Any]] | None = (
        [] if getattr(args, "per_instance", None) else None
    )

    report = assemble_report(
        manifest_path=str(args.manifest),
        split=args.split,
        detector_registry=registry,
        manifest_entries=manifest_entries,
        has_operating_point=bool(getattr(args, "operating_point", False)),
        n_resamples=args.n_resamples,
        confidence_level=args.confidence_level,
        seed=args.seed,
        per_instance_sink=per_instance_sink,
    )
    report["_per_instance"] = per_instance_sink
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Voight-Kampff benchmark harness: score voiceprint "
            "discrimination detectors against PAN VK Subtask-1 labels with "
            "the official PAN metric suite. External held-out validation; "
            "writes only a report (anti-Goodhart)."
        )
    )
    parser.add_argument(
        "--manifest", required=True,
        help="SETEC manifest JSONL (from pan_voight_kampff_to_manifest.py).",
    )
    parser.add_argument(
        "--detectors", default="length_ratio_standin",
        help=(
            "Comma-separated detector ids. M1: binoculars_audit, "
            "length_ratio_standin. Default: length_ratio_standin "
            "(CPU-clean, no model)."
        ),
    )
    parser.add_argument("--split", default="validation", help="Split tag.")
    parser.add_argument(
        "--operating-point", action="store_true", dest="operating_point",
        help=(
            "Use the detector's own two-threshold band as the operating "
            "point for the thresholded metrics (c@1/f1/f05u). WITHOUT this, "
            "thresholded cells are null (the harness never fits a threshold "
            "to the PAN labels)."
        ),
    )
    parser.add_argument(
        "--n-resamples", type=int, default=1000, dest="n_resamples",
        help="Bootstrap resamples per metric CI (default 1000; 0 disables).",
    )
    parser.add_argument(
        "--confidence-level", type=float, default=0.95, dest="confidence_level",
        help="Bootstrap CI confidence level (default 0.95).",
    )
    parser.add_argument(
        "--seed", type=int, default=12345, help="Bootstrap RNG seed.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    parser.add_argument("--out", default=None, help="Write the JSON report to PATH.")
    parser.add_argument(
        "--per-instance", default=None,
        help="Write per-instance (detector,id,score,label) rows to PATH (JSONL).",
    )
    parser.add_argument(
        "--markdown", default=None, help="Write a markdown rendering to PATH.",
    )
    args = parser.parse_args(argv)

    report = run_benchmark(args)
    per_instance = report.pop("_per_instance", None)

    if args.per_instance and per_instance is not None:
        with Path(args.per_instance).open("w", encoding="utf-8") as fh:
            for row in per_instance:
                fh.write(json.dumps(row, default=str) + "\n")

    payload = json.dumps(report, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(render_markdown(report), encoding="utf-8")
    if args.json or not args.out:
        sys.stdout.write(payload + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
