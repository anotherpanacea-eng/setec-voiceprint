#!/usr/bin/env python3
"""aitdna_benchmark.py — the AITDNA external-validation benchmark harness.

Runs setec-voiceprint's **existing** detectors over the AITDNA benchmark
(*'Your AI Text is not Mine': Redefining and Evaluating AI-generated Text
Detection under Realistic Assumptions*; Dycke, Sakharova, Daheim, Gurevych
— **arXiv:2606.04906**; HF ``datasets/UKPLab/AITDNA``, CC-BY-SA-4.0) — a
harder, more realistic distribution than PAN because the text is genuinely
human-AI **co-written**. This is a **held-out external-validation eval
HARNESS** — a sibling of ``pan_voight_kampff_benchmark.py``. It is NOT a new
detection surface, NOT a ``capabilities.d/`` drop-in, and it emits NO
``_golden_capabilities/`` fragment and NO ``claim_license_surfaces/`` file.

Pipeline: AITDNA release --(adapter)--> SETEC manifest (+ human-only
reference slice) --(runner)--> per-instance ``(detector, id,
oriented_score, label)`` rows --(scorer)--> per-notion metric vectors
--(assembler)--> one JSON benchmark report.

--------------------------------------------------------------------------
ANTI-GOODHART (load-bearing; the benchmark is external validation, never a
tuning / calibration / selection target — mirrors the PAN harness):

  * The harness **writes only** a report (+ optional per-instance sidecar
    + the operator's --text-dir). It writes NOTHING to any threshold file,
    ``capabilities.d/`` entry, ``claim_license_surfaces/`` file,
    ``_golden_capabilities/`` fragment, or calibration-readiness matrix.
    (AC-12) — enforced structurally: this module has no code path that
    opens any of those for writing.
  * It imports NO threshold-fitting / calibration-fitting symbol. It may
    *read* an already-calibrated threshold to report a thresholded metric;
    it never calls a fitter. (AC-13)
  * AITDNA labels flow ONE WAY: AITDNA -> adapter -> runner -> scorer ->
    report. No function consumes the computed metrics to emit an operating
    point, a "best detector" selection, or a calibration parameter; the
    report is a terminal artifact. (AC-14)
  * The Brier probability transform is DECLARED, FIXED, and LABEL-FREE
    (min-max over the run's answered scores) — never fitted to an AITDNA
    metric. (D7)
  * Thresholded cells are ``null`` with reason
    ``"no_operating_point_without_fitting_to_aitdna"`` when no operator
    operating-point is supplied. The harness NEVER sweeps a threshold
    against the AITDNA labels. (AC-17/D8)
  * The notion parameters (τ=0.5 / co-written / n=4 / p=5 / tokenizer) are
    FIXED module constants, never read from a sweep or config. The report
    emits no ``swept_parameter`` / ``optimal_*`` field. Guarded by
    ``test_notion_parameters_fixed`` (peer of PAN's
    ``test_no_aggregate_score``).
  * The report carries an explicit ``anti_goodhart`` block (AC-15) and an
    honest-gap ``notion_coverage`` block (AC-16): per notion, whether
    voiceprint addresses / partially-addresses / does-not-address it —
    so a notion voiceprint never claimed (boundary/sentence/intent/content)
    reports a status, not a misleadingly-low number.
--------------------------------------------------------------------------

Usage:
    python3 scripts/calibration/aitdna_benchmark.py \
        --manifest .aitdna_manifest.jsonl \
        --reference-manifest .aitdna_reference.jsonl \
        --detectors membership_novelty,length_ratio_standin \
        --json --out report.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

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

# Reuse the PAN harness's detector plumbing verbatim (same orientation
# registry, runner, probability transform, operating-point resolution) so
# the discrimination detectors behave identically across the two harnesses.
import pan_voight_kampff_benchmark as pan_bench  # noqa: E402

# The FIXED, never-swept notion constants live in the adapter (single
# source of truth) and are re-exported here for the guard + report.
import aitdna_to_manifest as adapter  # noqa: E402

HARNESS_VERSION = "0.1.0"
REPORT_SCHEMA_VERSION = "1.0"
REPORT_KIND = "aitdna_benchmark"

# SETEC binary-label convention (matches manifest_validator / the adapter):
# AI/machine = positive (1); human = negative (0).
POSITIVE_STATUSES = {"ai_generated", "ai_generated_from_outline"}
NEGATIVE_STATUSES = {"pre_ai_human"}

# The thresholded-cell reason when no operator operating point is in force.
NO_OP_REASON = "no_operating_point_without_fitting_to_aitdna"

ANTI_GOODHART_STATEMENT = (
    "These scores are external validation only. Do NOT fit detector "
    "thresholds, calibration parameters, or surface selection to this "
    "benchmark. The harness writes no threshold/calibration/registry "
    "artifact and reads no AITDNA-derived score back into detector "
    "behavior. The notion parameters (τ=0.5, co-written rule, membership "
    "n=4 / p=5) are fixed and never swept against AITDNA labels."
)

# =========================================================================
# The 7-notion taxonomy (verbatim from the spec §2). status is honest:
#   addressed      — a voiceprint surface targets this notion; scored.
#   partial        — voiceprint is document/corpus-level, not a span/
#                    sentence-boundary detector; the gap is DECLARED, the
#                    surface's distribution reported, no fabricated F1.
#   not_applicable — a semantic/policy notion outside stylometry entirely.
# =========================================================================
NOTION_TAXONOMY: list[dict[str, Any]] = [
    {
        "notion": "document_level",
        "aitdna_definition": "whole doc; flags if AI-content > τ",
        "voiceprint_surface": (
            "discrimination detectors (Binoculars-family, rank/likelihood) "
            "+ membership novelty"
        ),
        "status": "addressed",
        "note": "document-level τ=0.5; the harness's primary scored notion.",
    },
    {
        "notion": "boundary_level",
        "aitdna_definition": "β alternating human/AI passages; find boundaries",
        "voiceprint_surface": None,
        "status": "partial",
        "note": (
            "voiceprint is document/corpus-level, not a span-boundary "
            "detector — honest gap; no boundary F1 fabricated."
        ),
    },
    {
        "notion": "sentence_level",
        "aitdna_definition": "per-sentence AI-token ratio label",
        "voiceprint_surface": None,
        "status": "partial",
        "note": (
            "same document/corpus-level gap; the SETEC-standard sentence "
            "tokenizer exists but no per-sentence AI verdict surface does."
        ),
    },
    {
        "notion": "intent_based",
        "aitdna_definition": "AI sentence whose prompt violates an intent policy",
        "voiceprint_surface": None,
        "status": "not_applicable",
        "note": "policy/semantic notion, not a stylometric one.",
    },
    {
        "notion": "content_based",
        "aitdna_definition": "AI sentence answering the prompt / carrying key ideas",
        "voiceprint_surface": None,
        "status": "not_applicable",
        "note": "semantic notion, not a stylometric one.",
    },
    {
        "notion": "membership_based",
        "aitdna_definition": (
            "flags text not matching n-grams of a human reference corpus"
        ),
        "voiceprint_surface": "originality_audit / corpus_novelty_audit",
        "status": "addressed",
        "note": (
            "near-exact match to voiceprint's population posture; scored "
            "via DJ-Search coverage against the human-only reference "
            "(n=4-gram, p=5th percentile fixed constants)."
        ),
    },
    {
        "notion": "authorship_id_based",
        "aitdna_definition": (
            "reference = author-specific corpus; AITD as authorship ID"
        ),
        "voiceprint_surface": "voice_verifier / AV surfaces",
        "status": "partial",
        "note": (
            "voiceprint's core stylometric thesis, but voice_verifier is an "
            "LLM-judge pairwise surface with no per-doc binary label without "
            "an operator operating point / author profile — reported as a "
            "declared gap, not a fabricated per-notion F1 (M2 seam)."
        ),
    },
]


# =========================================================================
# Membership-novelty detector (the membership-based notion, addressed)
# =========================================================================
# Wraps ``originality_audit.audit_originality`` (DJ-Search coverage of a
# target against a reference pool). The reference pool is AITDNA's fixed
# human-only subset. A target is MORE membership-flagged (more likely
# "not human reference material") the LOWER its coverage — so ``coverage``
# is the raw score with polarity ``lower_is_ai`` (low coverage of the
# human reference = more AI-ish under the membership notion). The n-gram
# unit is the FIXED ``MEMBERSHIP_NGRAM`` constant, never swept.
# =========================================================================


def make_membership_novelty_scorer(
    reference: list[tuple[str, str]],
    *,
    min_ngram: int = adapter.MEMBERSHIP_NGRAM,
):
    """Build a per-instance scorer over ``audit_originality``.

    Returns ``{available, score, band, reason}`` per instance. Headline
    scalar = ``coverage`` (fraction of the target reconstructible from the
    human-only reference); raw polarity ``lower_is_ai`` (low coverage of
    the human reference = more membership-flagged), sign-flipped by the
    runner. ``band`` is always ``uncalibrated`` — this surface reports a
    distribution, not a per-doc verdict without an operator operating
    point (the honest-gap posture)."""
    import originality_audit

    def score(text: str) -> dict[str, Any]:
        if not text or not text.strip():
            return {
                "available": False,
                "score": None,
                "band": None,
                "reason": "empty_text",
            }
        if not reference:
            return {
                "available": False,
                "score": None,
                "band": None,
                "reason": "empty_reference_corpus",
            }
        try:
            out = originality_audit.audit_originality(
                text, reference, min_ngram=min_ngram
            )
        except ValueError as exc:
            return {
                "available": False,
                "score": None,
                "band": None,
                "reason": f"not_scorable:{exc}",
            }
        return {
            "available": True,
            "score": float(out["coverage"]),
            "band": "uncalibrated",
            "reason": None,
        }

    return score


def build_detector_registry(
    detector_ids: list[str],
    *,
    reference: list[tuple[str, str]] | None = None,
    binoculars_kwargs: dict[str, Any] | None = None,
    standin_kwargs: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return the orientation registry for the requested detectors.

    M1 wires three CPU-clean detectors:
      - ``membership_novelty`` (originality_audit DJ-Search coverage vs the
        human-only reference — the membership-based notion, stdlib);
      - ``binoculars_audit`` (real PAN baseline #3; via its injected
        ``score_fn`` for deterministic, model-free tests) — shared with
        the PAN harness;
      - ``length_ratio_standin`` (the stdlib stand-in) — shared with PAN.

    The model-tier discrimination surfaces (curvature / spectral / TOCSIN
    / intrinsic-dimension / rewriting-invariance / external-mirror) and the
    ``voice_verifier`` authorship-ID surface (LLM-judge dep) are real
    surfaces that need model/network deps — an out-of-M1 seam. They are
    intentionally NOT registered here so M1 stays CPU-clean.
    """
    binoculars_kwargs = binoculars_kwargs or {}
    standin_kwargs = standin_kwargs or {}
    available: dict[str, dict[str, Any]] = {
        "membership_novelty": {
            "task_surface": "set_level_diversity",
            "score_name": "reference_coverage",
            "orientation": "lower_is_ai",
            "orientation_basis": (
                "membership_based notion: DJ-Search coverage of the target "
                "by the human-only reference pool; LOW coverage = less "
                "reconstructible from human reference material = more AI-ish "
                "under the membership notion. Sign-flipped so higher = "
                "more-AI for the metrics. NOT a 'more human' verdict — a "
                "thin reference inflates apparent novelty (declared)."
            ),
            "scorer": make_membership_novelty_scorer(reference or []),
        },
        "binoculars_audit": {
            "task_surface": "binoculars_discrimination",
            "score_name": "perplexity_ratio",
            "orientation": "lower_is_ai",
            "orientation_basis": (
                "binoculars_discrimination surface: ratio < threshold_low "
                "= ai_likely (lower ratio = more AI); sign-flipped so "
                "higher = more-AI for the metrics."
            ),
            "scorer": pan_bench.make_binoculars_scorer(**binoculars_kwargs),
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
            "scorer": pan_bench.make_length_ratio_standin_scorer(**standin_kwargs),
        },
    }
    registry: dict[str, dict[str, Any]] = {}
    for did in detector_ids:
        if did not in available:
            raise SystemExit(
                f"Unknown / out-of-M1 detector {did!r}. M1 wires: "
                f"{sorted(available)}. The model-tier discrimination "
                "surfaces and the voice_verifier authorship-ID surface are "
                "a named out-of-M1 seam (need model/judge deps)."
            )
        registry[did] = available[did]
    return registry


# =========================================================================
# Reference-corpus loading (the human-only subset)
# =========================================================================


def load_reference_corpus(
    reference_manifest: str | Path | None,
) -> list[tuple[str, str]]:
    """Load the fixed human-only reference corpus as ``[(source, text)]``
    for ``audit_originality``. Reads a JSONL where each row carries an
    inline ``text`` or a ``text_path``/``path`` resolved relative to the
    manifest's dir (the shape ``aitdna_to_manifest.py`` writes). Returns
    ``[]`` when no reference manifest is supplied (membership_novelty then
    reports unavailable, honestly)."""
    if not reference_manifest:
        return []
    path = Path(reference_manifest).expanduser().resolve()
    if not path.is_file():
        return []
    base = path.parent
    out: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        src = str(row.get("id") or row.get("source") or "ref")
        if isinstance(row.get("text"), str) and row["text"].strip():
            out.append((src, row["text"]))
            continue
        rel = row.get("text_path") or row.get("path")
        if rel:
            fp = base / rel
            if fp.is_file():
                out.append((src, fp.read_text(encoding="utf-8", errors="replace")))
    return out


# =========================================================================
# Report assembly
# =========================================================================


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _co_written_fpr(
    rows: list[dict[str, Any]],
    entries_by_id: dict[str, dict[str, Any]],
    *,
    op_in_force: bool,
) -> dict[str, Any]:
    """The AITDNA headline hard case: false-positive rate on the
    **co-written, human-labeled** subset — the docs generic detectors
    flag worst. A false positive here = a doc labeled human (label 0) that
    is co-written yet predicted AI. Reported as a first-class cell.

    Without an operating point in force there are no per-doc predictions,
    so the cell is null with a reason (the honest no-op posture) — the FPR
    is never fabricated by sweeping a threshold against AITDNA labels."""
    co_written_human = [
        r for r in rows
        if r["label"] == 0
        and entries_by_id.get(r["id"], {}).get("notes", {}).get("co_written")
    ]
    n = len(co_written_human)
    if not op_in_force:
        return {
            "value": None,
            "n_co_written_human": n,
            "reason": NO_OP_REASON,
        }
    if n == 0:
        return {
            "value": None,
            "n_co_written_human": 0,
            "reason": "no_co_written_human_docs_in_split",
        }
    n_fp = sum(
        1 for r in co_written_human
        if r.get("band") == "ai_likely"
    )
    return {
        "value": n_fp / n,
        "n_co_written_human": n,
        "n_false_positive": n_fp,
        "reason": None,
    }


def assemble_report(
    *,
    manifest_path: str,
    reference_manifest: str | None,
    reference: list[tuple[str, str]],
    detector_registry: dict[str, dict[str, Any]],
    manifest_entries: list[dict[str, Any]],
    has_operating_point: bool,
    operator_thresholds_supplied: bool,
    n_resamples: int,
    confidence_level: float,
    seed: int | None,
    per_instance_sink: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Orchestrate runner -> scorer -> the report shape."""
    entries_by_id = {e.get("id"): e for e in manifest_entries}

    def _label(e: dict[str, Any]) -> int | None:
        return label_for_status(
            e.get("ai_status"), POSITIVE_STATUSES, NEGATIVE_STATUSES
        )

    n_human = sum(1 for e in manifest_entries if _label(e) == 0)
    n_machine = sum(1 for e in manifest_entries if _label(e) == 1)
    n_co_written = sum(
        1 for e in manifest_entries
        if isinstance(e.get("notes"), dict) and e["notes"].get("co_written")
    )

    detectors_out: list[dict[str, Any]] = []
    for detector_id, spec in detector_registry.items():
        run = pan_bench.run_detector_over_manifest(
            detector_id, spec, manifest_entries
        )
        rows = run["rows"]
        labels = [r["label"] for r in rows]
        oriented = [r["oriented_score"] for r in rows]
        probabilities = pan_bench.oriented_score_to_probability(oriented)

        rank_metrics = pan_metrics.score_all(
            labels,
            probabilities,
            has_operating_point=False,
            n_resamples=n_resamples,
            confidence_level=confidence_level,
            seed=seed,
        )
        metrics: dict[str, Any] = {
            k: rank_metrics[k] for k in pan_metrics.RANK_METRICS
        }

        op = pan_bench.resolve_operating_point(
            rows,
            operating_point_requested=has_operating_point,
            operator_thresholds_supplied=operator_thresholds_supplied,
        )
        # The harness's own null reason names AITDNA, not PAN.
        if op["reason"] == "no_operating_point_without_fitting_to_pan":
            op["reason"] = NO_OP_REASON
        if op["in_force"]:
            preds = pan_bench.predictions_with_operating_point(
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
                    "reason": op["reason"],
                }

        # aitdna_mean is the headline only when all five constituents are
        # real. Otherwise null with a partial marker — never a deflated
        # scalar (mirror PAN finding 3).
        present = [
            k for k in pan_metrics.PAN_METRIC_KEYS
            if metrics[k]["value"] is not None
        ]
        if len(present) == len(pan_metrics.PAN_METRIC_KEYS):
            metrics["aitdna_mean"] = {
                "value": pan_metrics.pan_mean(
                    {k: metrics[k]["value"] for k in pan_metrics.PAN_METRIC_KEYS}
                ),
                "partial": False,
                "n_metrics_present": len(present),
            }
        else:
            metrics["aitdna_mean"] = {
                "value": None,
                "partial": True,
                "n_metrics_present": len(present),
                "reason": (
                    "partial_suite_no_operating_point"
                    if not op["in_force"]
                    else "partial_suite_metric_undefined"
                ),
            }

        # The AITDNA headline hard-case cell: co-written human FPR.
        co_written_fpr = _co_written_fpr(
            rows, entries_by_id, op_in_force=op["in_force"]
        )

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
            "probability_transform": pan_bench.PROBABILITY_TRANSFORM_NOTE,
            "metrics": metrics,
            "co_written_human_fpr": co_written_fpr,
            "operating_point": {
                "source": op["source"],
                "in_force": op["in_force"],
                "threshold": None,
                "note": (
                    "Thresholded metrics + co-written FPR require an "
                    "operating point supplied via the detector's own "
                    "two-threshold band; if none is in force they are null "
                    "and only the rank metrics (roc_auc/brier) are reported. "
                    "source is the ACTUAL provenance (operator_supplied | "
                    "detector_calibrated | none) — never fabricated from a "
                    "bare flag. The harness NEVER fits a threshold to the "
                    "AITDNA labels."
                ),
            },
        })

    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "report_kind": REPORT_KIND,
        "generated_utc": _utc_now(),
        "dataset": {
            "name": "aitdna",
            "hf_repo_id": "UKPLab/AITDNA",
            "license": "CC-BY-SA-4.0",
            "arxiv": "2606.04906",
            "n_instances": n_human + n_machine,
            "n_human": n_human,
            "n_machine": n_machine,
            "n_co_written": n_co_written,
            "manifest_path": manifest_path,
            "provenance_note": (
                "Local-only; CC-BY-SA-4.0 (share-alike). NOT vendored. See "
                "NOTICE.md next to the staged text."
            ),
        },
        "reference_provenance": adapter.reference_provenance(),
        "reference_manifest": reference_manifest,
        "n_reference_docs": len(reference),
        "detectors": detectors_out,
        "notion_coverage": {
            "notions": NOTION_TAXONOMY,
            "note": (
                "Per-notion honesty (spec §2): 'addressed' notions are "
                "scored above; 'partial' notions are a declared "
                "document/corpus-level gap (no fabricated per-notion F1); "
                "'not_applicable' notions are semantic/policy, outside "
                "stylometry. voiceprint's glass-box population/authorship-ID "
                "posture IS the membership/authorship notion generic "
                "document-level detectors underperform on co-written text."
            ),
        },
        "anti_goodhart": {
            "role": "external_held_out_validation",
            "is_tuning_target": False,
            "is_calibration_target": False,
            "is_selection_target": False,
            "notion_parameters_fixed": {
                "doc_tau": adapter.DOC_TAU,
                "co_written_min_each_side": adapter.CO_WRITTEN_MIN,
                "membership_ngram_n": adapter.MEMBERSHIP_NGRAM,
                "membership_percentile_p": adapter.MEMBERSHIP_PERCENTILE,
            },
            "statement": ANTI_GOODHART_STATEMENT,
        },
        "claim_note": (
            "Reports discrimination of named voiceprint detectors against "
            "AITDNA's realistic human-AI co-written labels, per notion. Not "
            "a per-document verdict; not a calibration result. The "
            "co-written-human FPR is the AITDNA-foregrounded hard case."
        ),
        "harness_version": HARNESS_VERSION,
        "reproduce": {
            "cmd": (
                "python3 scripts/calibration/aitdna_benchmark.py "
                "--manifest .aitdna_manifest.jsonl --reference-manifest "
                ".aitdna_reference.jsonl --detectors "
                "membership_novelty --json --out report.json"
            )
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    ds = report["dataset"]
    lines = [
        "# AITDNA Benchmark Report",
        "",
        f"- Dataset: `{ds['name']}` (HF `{ds['hf_repo_id']}`, "
        f"{ds['license']}, arXiv:{ds['arxiv']})",
        f"- Instances: {ds['n_instances']} "
        f"({ds['n_human']} human, {ds['n_machine']} AI, "
        f"{ds['n_co_written']} co-written)",
        f"- Reference (human-only) docs: {report['n_reference_docs']}",
        f"- Harness version: `{report['harness_version']}`",
        "",
        "## Detectors",
        "",
        "| detector | roc_auc | brier | c@1 | f1 | f0.5u | "
        "co-written FPR |",
        "|---|---|---|---|---|---|---|",
    ]
    for d in report["detectors"]:
        m = d["metrics"]

        def cell(k: str) -> str:
            v = m.get(k, {}).get("value")
            return "—" if v is None else f"{v:.3f}"

        fpr = d["co_written_human_fpr"].get("value")
        fpr_cell = "—" if fpr is None else f"{fpr:.3f}"
        lines.append(
            f"| `{d['detector']}` | {cell('roc_auc')} | {cell('brier')} | "
            f"{cell('c_at_1')} | {cell('f1')} | {cell('f05u')} | "
            f"{fpr_cell} |"
        )
    lines += ["", "## Notion coverage", ""]
    for nrow in report["notion_coverage"]["notions"]:
        lines.append(f"- **{nrow['notion']}** — `{nrow['status']}`: {nrow['note']}")
    lines += [
        "",
        f"> {report['anti_goodhart']['statement']}",
        "",
    ]
    return "\n".join(lines)


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    """Public entry: load manifest + reference, build registry, assemble
    report. Kept import-friendly (no I/O beyond reading the
    manifest/reference/text) so tests can call it directly."""
    manifest_entries = load_manifest_entries(args.manifest)
    detector_ids = [d.strip() for d in args.detectors.split(",") if d.strip()]

    reference = load_reference_corpus(getattr(args, "reference_manifest", None))

    binoculars_kwargs = dict(getattr(args, "_binoculars_kwargs", {}) or {})
    standin_kwargs = dict(getattr(args, "_standin_kwargs", {}) or {})

    cli_low = getattr(args, "threshold_low", None)
    cli_high = getattr(args, "threshold_high", None)
    cli_thresholds_supplied = cli_low is not None and cli_high is not None
    if cli_thresholds_supplied:
        binoculars_kwargs.setdefault("threshold_low", cli_low)
        binoculars_kwargs.setdefault("threshold_high", cli_high)
        standin_kwargs.setdefault("threshold_low", cli_low)
        standin_kwargs.setdefault("threshold_high", cli_high)

    def _has_two(kw: dict[str, Any]) -> bool:
        return kw.get("threshold_low") is not None and kw.get("threshold_high") is not None

    operator_thresholds_supplied = (
        cli_thresholds_supplied
        or _has_two(binoculars_kwargs)
        or _has_two(standin_kwargs)
    )

    registry = build_detector_registry(
        detector_ids,
        reference=reference,
        binoculars_kwargs=binoculars_kwargs,
        standin_kwargs=standin_kwargs,
    )

    per_instance_sink: list[dict[str, Any]] | None = (
        [] if getattr(args, "per_instance", None) else None
    )

    report = assemble_report(
        manifest_path=str(args.manifest),
        reference_manifest=(
            str(args.reference_manifest)
            if getattr(args, "reference_manifest", None) else None
        ),
        reference=reference,
        detector_registry=registry,
        manifest_entries=manifest_entries,
        has_operating_point=bool(getattr(args, "operating_point", False)),
        operator_thresholds_supplied=operator_thresholds_supplied,
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
            "AITDNA benchmark harness: score voiceprint detectors against "
            "AITDNA's realistic human-AI co-written labels, per notion. "
            "External held-out validation; writes only a report "
            "(anti-Goodhart). Notion parameters are fixed, never swept."
        )
    )
    parser.add_argument(
        "--manifest", required=True,
        help="SETEC manifest JSONL (from aitdna_to_manifest.py).",
    )
    parser.add_argument(
        "--reference-manifest", default=None, dest="reference_manifest",
        help=(
            "Human-only reference-corpus manifest (from aitdna_to_manifest.py) "
            "for the membership-based notion. Without it, membership_novelty "
            "reports unavailable."
        ),
    )
    parser.add_argument(
        "--detectors", default="length_ratio_standin",
        help=(
            "Comma-separated detector ids. M1: membership_novelty, "
            "binoculars_audit, length_ratio_standin. Default: "
            "length_ratio_standin (CPU-clean, no model)."
        ),
    )
    parser.add_argument(
        "--operating-point", action="store_true", dest="operating_point",
        help=(
            "Use the detector's own two-threshold band as the operating "
            "point for the thresholded metrics + co-written FPR. Requires a "
            "reachable two-threshold band (supply --threshold-low/"
            "--threshold-high, or a detector that carries its own calibrated "
            "thresholds). WITHOUT one the thresholded cells stay null and "
            "source stays \"none\" — the harness never fits a threshold to "
            "the AITDNA labels."
        ),
    )
    parser.add_argument(
        "--threshold-low", type=float, default=None, dest="threshold_low",
        help="Operator-supplied lower threshold (only with --operating-point).",
    )
    parser.add_argument(
        "--threshold-high", type=float, default=None, dest="threshold_high",
        help="Operator-supplied upper threshold (only with --operating-point).",
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
