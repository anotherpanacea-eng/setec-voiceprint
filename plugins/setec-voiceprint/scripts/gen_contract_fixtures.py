#!/usr/bin/env python3
"""gen_contract_fixtures.py — deterministic golden-envelope generator (R5).

Implements R5 of ``references/setec-normalized-entrypoint-spec.md`` §6.

For every consumer surface (a fragment in ``capabilities.d/`` that carries
``min_setec_version`` and lists at least one consumer — the nine apodictic
surfaces plus the four setec-voicewright fitness surfaces) this module
produces ONE canonical ``schema_version: 1.0`` envelope and writes it to
``references/contract_fixtures/<surface>.json``.

Faithfulness contract
----------------------
The golden for each surface is NOT hand-written JSON. Each surface's REAL
envelope-assembly path is imported from its own script and fed a canonical
fixture input that mirrors the actual internal ``result`` / ``audit`` /
``output`` dict the surface emits at runtime:

    * envelope keys, key order, and nesting come from
      ``output_schema.build_output`` (the same call the script makes);
    * ``claim_license`` text comes from each script's own
      ``_claim_license(...)`` builder + the per-surface fragment registry
      in ``claim_license_surfaces/`` (never typed out here, so it
      auto-updates when a surface's license changes);
    * ``task_surface`` is the surface's own ``TASK_SURFACE`` constant
      (= the fragment's ``surface`` field);
    * the ``results`` payload uses representative VALUES but the REAL
      top-level + nested KEYS captured from each script's ``build_output``
      call site.

No heavy audit is run (no spaCy / torch / scipy / sentence-transformers).
We construct the envelope directly from the canonical fixture input, so
generation is deterministic and dependency-free.

Volatile fields (script version, target path, timestamps, run ids,
prompt fingerprints) are replaced with sentinels by :func:`normalize`
before writing/comparing, so regeneration is byte-stable across releases.
See :data:`NORMALIZATION_DOC`.

CLI
---
    python3 gen_contract_fixtures.py --write    # (re)write all goldens
    python3 gen_contract_fixtures.py --check    # nonzero exit on drift
    python3 gen_contract_fixtures.py --list     # list surfaces

Importable: the drift checker (``tools/check_capabilities_drift.py``)
reuses :func:`regenerate_surface`, :func:`normalize`, :func:`load_golden`,
and :func:`SURFACE_BUILDERS` so the gate and the generator can never
disagree about what a golden should contain.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

SCRIPTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPTS_DIR.parent
FIXTURES_DIR = PLUGIN_ROOT / "references" / "contract_fixtures"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# --------------------------------------------------------------------------
# Normalization — the single set of volatile fields, applied identically by
# the writer and by --check / the drift gate so regeneration is byte-stable.
# --------------------------------------------------------------------------

VERSION_SENTINEL = "<fixture>"
PATH_SENTINEL = "<fixture>"
SHA_SENTINEL = "<fixture-sha256>"
TIMESTAMP_SENTINEL = "<fixture-timestamp>"

#: Human-readable description of the normalization set (kept in sync with the
#: ``contract_fixtures/README.md`` table). Volatile fields would otherwise
#: change every release / run and make the goldens un-pinnable.
NORMALIZATION_DOC = """\
Normalized (volatile) fields, replaced with sentinels before write/compare:
  * version                              -> "<fixture>"   (SCRIPT_VERSION; bumps per release)
  * target.path (when non-null)          -> "<fixture>"   (absolute input path)
  * baseline.path                        -> "<fixture>"   (idiolect reference path)
  * baseline.files[].path                -> "<fixture>"   (per-file absolute paths)
  * results.*.files[].path               -> "<fixture>"   (corpus_summary file paths)
  * results.inputs.manifest              -> "<fixture>"   (pov_voice_profile manifest path)
  * results.run_timestamp_utc            -> "<fixture-timestamp>"
  * results.prompt_fingerprint_sha256    -> "<fixture-sha256>"
"""


def normalize(envelope: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of *envelope* with volatile fields sentinelized.

    Pure; never mutates the input. Applied identically by the writer and
    the drift check so a regenerated envelope compares byte-for-byte
    against the committed golden after normalization.
    """
    env = json.loads(json.dumps(envelope))  # deep copy, JSON-roundtrip-safe

    # version (SCRIPT_VERSION) — changes per release.
    if "version" in env:
        env["version"] = VERSION_SENTINEL

    # target.path — absolute input path.
    target = env.get("target")
    if isinstance(target, dict) and target.get("path") is not None:
        target["path"] = PATH_SENTINEL

    # baseline.path + baseline.files[].path (idiolect/voice surfaces).
    baseline = env.get("baseline")
    if isinstance(baseline, dict):
        if "path" in baseline:
            baseline["path"] = PATH_SENTINEL
        _sentinelize_file_paths(baseline.get("files"))

    # results — surface-specific volatile fields.
    results = env.get("results")
    if isinstance(results, dict):
        if "run_timestamp_utc" in results:
            results["run_timestamp_utc"] = TIMESTAMP_SENTINEL
        if "prompt_fingerprint_sha256" in results:
            results["prompt_fingerprint_sha256"] = SHA_SENTINEL
        inputs = results.get("inputs")
        if isinstance(inputs, dict) and inputs.get("manifest") is not None:
            inputs["manifest"] = PATH_SENTINEL
        # Any nested corpus_summary-style block carrying files[].path.
        for value in results.values():
            if isinstance(value, dict):
                _sentinelize_file_paths(value.get("files"))

    return env


def _sentinelize_file_paths(files: Any) -> None:
    if not isinstance(files, list):
        return
    for entry in files:
        if isinstance(entry, dict) and "path" in entry:
            entry["path"] = PATH_SENTINEL


# --------------------------------------------------------------------------
# Per-surface canonical-fixture builders.
#
# Each builder imports the surface's OWN envelope-assembly path and feeds it
# a representative fixture input mirroring the real internal dict captured
# from that script's build_output(...) call site. The function returns the
# raw (un-normalized) envelope.
# --------------------------------------------------------------------------


def _build_variance_audit() -> dict[str, Any]:
    import variance_audit as m  # type: ignore

    # Mirrors audit_text() output (no-baseline path): summary + tier1.
    audit = {
        "preprocessing": {"applied": False, "rules_active": []},
        "summary": {
            "n_words": 2480,
            "n_words_original": 2480,
            "n_sentences": 124,
            "reliable": True,
            "preprocessing_applied": False,
        },
        "tier1": {
            "sentence_length": {"mean": 20.0, "sd": 7.4, "min": 4, "max": 41, "n": 124},
            "mattr": {"window": 50, "value": 0.78},
            "mtld": 92.5,
            "yules_k": 96.3,
            "shannon_entropy_bits": 9.81,
            "fkgl": {"mean": 9.2, "sd": 2.6},
            "connective_density": {"per_1000_tokens": 31.0, "count": 77},
            "function_words": {"function_word_ratio": 0.46},
        },
        "tier2": {"available": False, "pos_bigrams": None, "mdd": None},
        "tier3": {"available": False, "adjacent_cosine": None},
    }
    compression = {
        "band": "Within typical range",
        "weighted_score": 1.2,
        "available_weight": 6.0,
        "compression_fraction": 0.2,
        "flagged_signals": [],
        "available_signals": ["sentence_length_sd", "mattr", "mtld"],
        "skipped_signals": [],
        "n_flagged": 0,
        "notes": {},
        "thresholds_used": {},
        "calibration_status": {
            "n_calibrated": 0,
            "n_provisional": 6,
            "n_total": 6,
            "calibrated_signals": [],
            "provisional_signals": ["mattr", "mtld"],
        },
    }
    output = {
        "task_surface": m.TASK_SURFACE,
        "preprocessing": audit["preprocessing"],
        "audit": audit,
        "compression": compression,
        "baseline": None,
    }
    return m.build_audit_payload(output, target_path="<fixture>")


def _build_manuscript_audit() -> dict[str, Any]:
    import manuscript_audit as m  # type: ignore

    # Mirrors run()'s result dict (no-baseline path): per-chapter audit
    # entries with the {label, text_hash, n_words, audit, compression}
    # shape; baseline_stats empty without a baseline.
    chapter_audit = {
        "preprocessing": {"applied": False},
        "summary": {"n_words": 1240, "n_sentences": 62, "reliable": True},
        "tier1": {
            "mattr": {"window": 50, "value": 0.79},
            "mtld": 88.0,
            "yules_k": 99.1,
        },
    }
    chapter_compression = {
        "band": "Within typical range",
        "compression_fraction": 0.17,
        "flagged_signals": [],
        "n_flagged": 0,
    }
    result = {
        "task_surface": m.TASK_SURFACE,
        "preprocessing": {"chapters": {"applied": False}, "baseline": None},
        "n_chapters": 1,
        "n_baseline_files": 0,
        "chapters": [
            {
                "label": "Chapter 1",
                "text_hash": "<fixture-hash>",
                "n_words": 1240,
                "audit": chapter_audit,
                "compression": chapter_compression,
            }
        ],
        "baseline_stats": {},
    }
    return m.build_audit_payload(result, target_path="<fixture>")


def _build_repetition_audit() -> dict[str, Any]:
    import repetition_audit as m  # type: ignore

    # repetition_audit requires --baseline-dir; build_audit_payload takes
    # the candidate list + baseline file lists directly. Candidate keys
    # mirror find_repetitions()'s emitted shape.
    candidates = [
        {
            "word": "liminal",
            "count": 11,
            "per_1000": 4.4,
            "baseline_per_1000": 0.3,
            "ratio": 14.7,
            "cluster_max": 4,
            "cluster_window": 500,
        },
        {
            "word": "tapestry",
            "count": 7,
            "per_1000": 2.8,
            "baseline_per_1000": 0.4,
            "ratio": 7.0,
            "cluster_max": 2,
            "cluster_window": 500,
        },
    ]
    return m.build_audit_payload(
        target_path="<fixture>",
        target_words=2500,
        candidates=candidates,
        baseline_files_loaded=["<fixture>/baseline_a.md", "<fixture>/baseline_b.md"],
        baseline_files_skipped=[],
        baseline_tokens=42000,
    )


def _build_voice_distance() -> dict[str, Any]:
    import voice_distance as m  # type: ignore

    # Mirrors stylometry_core.compare_to_baseline() + main()'s additions
    # (task_surface, register_match). One representative family entry with
    # family_distance()'s full key shape.
    family = {
        "n_features": 50,
        "burrows_delta": 1.42,
        "cosine_distance_to_centroid": 0.18,
        "cosine_distance_to_baseline_mean": 0.21,
        "cosine_distance_to_baseline_min": 0.09,
        "top_deviations": [
            {
                "feature": "the",
                "value": 0.061,
                "baseline_mean": 0.052,
                "baseline_sd": 0.004,
                "z": 2.25,
                "abs_z": 2.25,
            }
        ],
        "overall_delta_contribution_cap": 3.0,
        "capped_in_overall": False,
    }
    result = {
        "task_surface": m.TASK_SURFACE,
        "preprocessing": {"target": {"applied": False}, "baseline": {"applied": False}},
        "target_summary": {"n_words": 2500, "n_sentences": 120},
        "baseline_summary": {
            "n_files": 6,
            "total_words": 48000,
            "mean_words": 8000.0,
            "min_words": 5000,
            "max_words": 12000,
            "registers": ["literary_fiction"],
            "personas": [],
            "privacy_values": [],
            "files": [
                {"id": "prior_a", "path": "<fixture>/prior_a.md", "n_words": 8000}
            ],
        },
        "selected_features": {"function_words": 50, "char_ngrams": 100},
        "families": {"function_words": family},
        "warnings": [],
        "overall": {
            "weighted_delta": 1.31,
            "band": "Moderate drift",
            "interpretation": "Target sits a moderate distance from the baseline voice.",
            "threshold_note": "Provisional bands; calibration pending.",
        },
        "register_match": {
            "target_classification": {
                "primary": "literary_fiction",
                "confidence": 0.71,
                "secondary": "blog_essay",
            },
            "match": {"verdict": "match", "baseline_registers": ["literary_fiction"]},
        },
    }
    return m.build_audit_payload(result, target_path="<fixture>")


def _build_voice_profile() -> dict[str, Any]:
    import voice_profile as m  # type: ignore

    # Mirrors stylometry_core.build_profile() output + main()'s
    # task_surface assignment. families[family] = {n_features,
    # top_features, most_stable_features}.
    family = {
        "n_features": 50,
        "top_features": [
            {"feature": "the", "mean": 0.052, "sd": 0.004, "cv": 0.077},
            {"feature": "and", "mean": 0.031, "sd": 0.003, "cv": 0.097},
        ],
        "most_stable_features": [
            {"feature": "the", "mean": 0.052, "sd": 0.004, "cv": 0.077},
        ],
    }
    profile = {
        "task_surface": m.TASK_SURFACE,
        "privacy": "PRIVATE - DO NOT SHARE. A voice profile is a voice-cloning input.",
        "preprocessing": {"applied": False},
        "baseline_summary": {
            "n_files": 6,
            "total_words": 48000,
            "mean_words": 8000.0,
            "min_words": 5000,
            "max_words": 12000,
            "registers": ["literary_fiction"],
            "personas": [],
            "privacy_values": [],
            "files": [
                {"id": "prior_a", "path": "<fixture>/prior_a.md", "n_words": 8000}
            ],
        },
        "selected_features": {"function_words": 50, "char_ngrams": 100},
        "warnings": [],
        "families": {"function_words": family},
    }
    return m.build_audit_payload(profile, target_path="<fixture>")


def _build_pov_voice_profile() -> dict[str, Any]:
    import pov_voice_profile as m  # type: ignore

    # pov_voice_profile assembles + serializes the envelope in
    # render_json(); call it directly with representative inputs mirroring
    # main()'s computed structures. POVProfile is a small dataclass.
    profiles = {
        "Ada": m.POVProfile(
            label="Ada", n_docs=3, n_words=9000,
            feature_items=[], pov_centroids={},
        ),
        "Jordan": m.POVProfile(
            label="Jordan", n_docs=3, n_words=8200,
            feature_items=[], pov_centroids={},
        ),
    }
    family_distances = {
        "function_words": {
            ("Ada", "Jordan"): {"burrows_delta": 1.05, "cosine_distance": 0.14},
        }
    }
    weighted = {
        ("Ada", "Jordan"): {"burrows_delta": 0.92, "cosine_distance": 0.12},
    }
    pov_vs_mean = {
        "Ada": {"burrows_delta": 0.61, "cosine_distance": 0.08},
        "Jordan": {"burrows_delta": 0.58, "cosine_distance": 0.07},
    }
    distinguishing = {
        "Ada": {"function_words": [{"feature": "but", "z": 1.9}]},
        "Jordan": {"function_words": [{"feature": "and", "z": 1.7}]},
    }
    collapse_verdict = [
        {
            "pov_a": "Ada",
            "pov_b": "Jordan",
            "burrows_delta": 0.92,
            "cosine_distance": 0.12,
            "verdict": "potentially_collapsed",
            "threshold": 1.0,
        }
    ]
    inputs = {
        "manifest": "<fixture>",
        "use": None,
        "min_docs_per_pov": 2,
        "collapse_threshold": 1.0,
    }
    json_str = m.render_json(
        profiles=profiles,
        family_distances=family_distances,
        weighted_distances=weighted,
        pov_vs_mean=pov_vs_mean,
        distinguishing=distinguishing,
        collapse_verdict=collapse_verdict,
        dropped_povs=[],
        inputs=inputs,
    )
    return json.loads(json_str)


def _build_punctuation_cadence_audit() -> dict[str, Any]:
    import punctuation_cadence_audit as m  # type: ignore

    # Mirrors audit_punctuation_cadence() output (no-baseline path).
    audit = {
        "task_surface": m.TASK_SURFACE,
        "tool": m.TOOL_NAME,
        "version": m.SCRIPT_VERSION,
        "available": True,
        "preprocessing": {"applied": False},
        "n_words": 2500,
        "n_sentence_final": 120,
        "raw_counts": {
            "comma": 180,
            "semicolon": 3,
            "colon": 5,
            "em_dash": 12,
            "en_dash": 0,
            "parenthesis": 6,
            "bracket": 0,
            "ellipsis": 2,
        },
        "densities_per_1k": {
            "comma_per_1k": 72.0,
            "semicolon_per_1k": 1.2,
            "colon_per_1k": 2.0,
            "em_dash_per_1k": 4.8,
            "en_dash_per_1k": 0.0,
            "parenthesis_per_1k": 2.4,
            "bracket_per_1k": 0.0,
            "ellipsis_per_1k": 0.8,
        },
        "sentence_final_distribution": {
            "period": 0.9,
            "question": 0.06,
            "exclamation": 0.04,
        },
        "interruption_grammar": {
            "parenthetical_per_1k": 2.4,
            "em_dash_aside_per_1k": 4.8,
            "comma_appositive_per_1k": 6.0,
            "total_interruption_per_1k": 13.2,
        },
        "punctuation_bigrams": {",—": 4, "—,": 2},
        "comma_period_share": 0.86,
        "compression": {
            "band": "Lightly regularized",
            "compression_fraction": 0.167,
            "flagged_signals": ["low_semicolon_density"],
            "n_flagged": 1,
            "n_signals": 6,
        },
    }
    return m.build_audit_payload(
        audit,
        target_path="<fixture>",
        baseline_block=None,
        baseline_comparison=None,
    )


def _build_idiolect_detector() -> dict[str, Any]:
    import idiolect_detector as m  # type: ignore

    # Mirrors detect_idiolect()'s result dict. Ranking rows mirror the
    # full per-phrase key shape; preservation_list reuses that shape.
    row = {
        "phrase": "the liminal hush",
        "display": "the liminal hush",
        "n": 3,
        "target_count": 6,
        "reference_count": 0,
        "target_per_1000": 0.24,
        "reference_per_1000": 0.0,
        "log2_ratio": 4.2,
        "score": 18.7,
        "score_name": "log_likelihood_g2",
        "p_value": 0.0001,
        "collocation_method": "log_ratio",
        "collocation_lr": 5.1,
        "collocation_pmi": 3.8,
    }
    result = {
        "task_surface": m.TASK_SURFACE,
        "privacy": "PRIVATE - DO NOT SHARE. A preservation list is a voice-cloning input.",
        "target_summary": {
            "label": "target",
            "n_files": 4,
            "n_tokens": 52000,
            "files": [
                {"id": "draft_a", "path": "<fixture>/draft_a.md", "metadata": {}}
            ],
        },
        "reference_summary": {
            "label": "reference",
            "n_files": 8,
            "n_tokens": 120000,
            "files": [
                {"id": "ref_a", "path": "<fixture>/ref_a.md", "metadata": {}}
            ],
        },
        "method": {
            "keyness": "log_likelihood",
            "n_values": [1, 2, 3],
            "smoothing_alpha": 0.5,
            "min_target_count": 3,
            "min_reference_count": 0,
            "min_total_count": 3,
            "include_function_words": False,
            "collocation_filter": True,
            "min_collocation_lr": 2.0,
            "min_collocation_pmi": 1.0,
        },
        "preprocessing": {"target": {"applied": False}, "reference": {"applied": False}},
        "rankings": {
            "3": {"idiolectic": [row], "anti_idiolectic": []},
        },
        "preservation_list": [row],
    }
    return m.build_audit_payload(
        result, target_path="<fixture>", reference_path="<fixture>"
    )


def _build_narrative_decision_audit() -> dict[str, Any]:
    import narrative_decision_audit as m  # type: ignore
    from narrative_judge import _mock_judge  # type: ignore

    # narrative_decision_audit's results dict is built by
    # build_results_payload(). We drive it through the surface's OWN
    # deterministic, dependency-free mock judge (no LLM, no API) so the
    # feature keys, contributions, bundles, and aggregate are the REAL
    # ones the script emits. compose_envelope() then assembles the
    # schema_version 1.0 envelope exactly as the CLI does.
    judge = _mock_judge(option_index=0)
    judge_result = judge("(canonical fixture story text)")
    cleaned, val_warnings = m.validate_values(judge_result.values)
    contributions = m.per_signal_contributions(cleaned)
    bundles = m.per_bundle_aggregates(contributions)
    aggregate = m.aggregate_score(contributions)
    results = m.build_results_payload(
        target_words=4753,
        judge_result=judge_result.to_dict(),
        cleaned_values=cleaned,
        validation_warnings=val_warnings,
        contributions=contributions,
        bundles=bundles,
        aggregate=aggregate,
        threshold_low=None,
        threshold_high=None,
        register_warnings=[],
    )
    return m.compose_envelope(
        target_path=Path("<fixture>"),
        target_words=4753,
        results=results,
        licenses_text=m.DEFAULT_LICENSES,
        does_not_license_text=m.DEFAULT_DOES_NOT_LICENSE,
    )


def _build_voice_fingerprint() -> dict[str, Any]:
    import voice_fingerprint as m  # type: ignore

    # Mirrors assemble_output()'s build_output call site for single mode.
    # The real encode path needs numpy + a downloaded style encoder, so the
    # builder carries representative VALUES with run_single()'s REAL key
    # shape (mode / n_windows / cosine_distribution / per_window) plus the
    # model_id + windowing additions assemble_output() makes.
    results = {
        "mode": "single",
        "n_windows": 4,
        "cosine_distribution": {
            "n": 6,
            "mean": 0.84,
            "sd": 0.05,
            "min": 0.74,
            "p10": 0.76,
            "p50": 0.85,
            "p90": 0.9,
        },
        "per_window": [0.84, 0.81, 0.9, 0.74, 0.88, 0.86],
        "model_id": "rrivera1849/LUAR-MUD",
        "windowing": {"strategy": "paragraph", "window_size": None},
    }
    lic = m._claim_license(model_id="rrivera1849/LUAR-MUD", mode="single")
    return m.build_output(
        task_surface=m.TASK_SURFACE,
        tool=m.TOOL_NAME,
        version=m.SCRIPT_VERSION,
        target_path="<fixture>",
        target_words=1800,
        baseline=None,
        results=results,
        claim_license=lic,
    )


def _build_mimicry_cosplay_audit() -> dict[str, Any]:
    import mimicry_cosplay_audit as m  # type: ignore

    # The audit path is pure stdlib, so the golden runs the REAL
    # audit_cosplay() over a canonical target + the upstream-JSON shapes it
    # reads (an idiolect_detector preservation_list + a voice_distance
    # overall block), then wraps with the script's own build_audit_payload().
    target_text = (
        "The harbor light went out before the harbor light came back, "
        "as it happens, and nobody on the quay said a word about it. "
        "As it happens, the ferryman counted his coins twice, the way "
        "he always did, and the harbor light blinked its slow yellow "
        "blink over the breakwater. Nobody said a word."
    )
    idiolect = {
        "preservation_list": [
            {"phrase": "as it happens"},
            {"phrase": "the harbor light"},
            {"phrase": "nobody said a word"},
            {"phrase": "the long wet street"},
        ],
    }
    voice_distance = {"overall": {"weighted_delta": 1.62}}
    audit = m.audit_cosplay(
        target_text=target_text,
        idiolect=idiolect,
        voice_distance=voice_distance,
        variance=None,
    )
    return m.build_audit_payload(
        audit, target_path="<fixture>", target_text=target_text,
    )


def _build_general_imposters() -> dict[str, Any]:
    import general_imposters as m  # type: ignore

    # GIResult -> _build_envelope() is the exact runtime assembly path
    # (legacy to_dict() preserved under results, structured claim_license
    # built from the legacy 3-key form). Representative non-refusal run in
    # the trustworthy high band.
    result = m.GIResult(
        target_id="<fixture>",
        candidate_persona="blog",
        candidate_n_docs=6,
        n_impostors=8,
        impostor_personas=[
            "imp_essayist_a", "imp_essayist_b", "imp_critic_a",
            "imp_critic_b", "imp_blogger_a", "imp_blogger_b",
            "imp_novelist_a", "imp_novelist_b",
        ],
        iterations=100,
        feature_fraction=0.5,
        top_n_features=50,
        wins=87,
        losses=13,
        proportion=0.87,
        proportion_ci_95=(0.79, 0.93),
        refused=False,
        refusal_reason="",
        decision="consistent",
    )
    return m._build_envelope(result)


def _build_binoculars_audit() -> dict[str, Any]:
    import binoculars_audit as m  # type: ignore

    # Mirrors audit()'s return shape (v1 perplexity-ratio path with no
    # operator thresholds -> uncalibrated band) fed through the script's
    # own compose_envelope(). The real path needs transformers + torch.
    results = {
        "scorer": {
            "model_id": "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
            "revision": "<fixture>",
            "identifier_block": "<fixture-identifier>",
        },
        "observer": {
            "model_id": "gpt2",
            "revision": "<fixture>",
            "identifier_block": "<fixture-identifier>",
        },
        "scorer_log_perplexity_bits": 4.21,
        "observer_log_perplexity_bits": 4.87,
        "perplexity_ratio": 0.864476,
        "score_version": "v1",
        "cross_perplexity_log_nats": None,
        "scorer_log_perplexity_nats": 2.918,
        "tokenizers_compatible": False,
        "thresholds": {"low": None, "high": None},
        "verdict_band": "uncalibrated",
        "scorer_series_length": 512,
        "observer_series_length": 512,
        "caveats": ["no_calibrated_thresholds_supplied"],
    }
    return m.compose_envelope(
        target_path=Path("<fixture>"),
        target_words=850,
        results=results,
        licenses_text=m.DEFAULT_LICENSES,
        does_not_license_text=m.DEFAULT_DOES_NOT_LICENSE,
    )


def _build_argument_decision_audit() -> dict[str, Any]:
    import argument_decision_audit as m  # type: ignore
    from argument_judge import build_judge  # type: ignore

    # ArgScope's surface labels a per-paragraph sequence via the judge, then
    # computes B1/B2 signals from it. Drive the surface's OWN deterministic,
    # dependency-free mock judge over a canonical paragraph set (no LLM/API) so
    # the observed signals, contributions, bundles, and aggregate are the REAL
    # ones the script emits; compose_envelope assembles the 1.0 envelope as the
    # CLI does. The mock labels every paragraph (support, argumentation).
    from argument_judge import validate_doc_level  # type: ignore

    paragraphs = [f"Canonical fixture paragraph {i}." for i in range(6)]
    judge_result = build_judge("mock")(paragraphs)
    labels, val_warnings = m.validate_labels(
        judge_result.values, n_paragraphs=len(paragraphs)
    )
    # B5: drive the doc-level field + the collapse-dynamics derivation through the
    # SAME mock path the CLI uses, so the golden carries the real B5 contributions
    # (disappearing_guard_flag True via the mock's strong->weak guard on a shared
    # claim_ref; discounting_straw_men_flag None — no objection role, doc-level
    # field null) and the new per-paragraph guard_strength/claim_ref/objection
    # fields. The aggregate stays byte-identical (B5 contribution=null).
    strongest_obj_engaged, doc_warnings = validate_doc_level(judge_result.values)
    val_warnings = val_warnings + doc_warnings
    observed = m.compute_arc_signals(labels)
    observed.update(m.compute_collapse_dynamics(labels, strongest_obj_engaged))
    contributions = m.per_signal_contributions(observed)
    bundles = m.per_bundle_aggregates(contributions)
    aggregate = m.aggregate_score(contributions)
    pre_flag = m.compute_pre_flag(contributions)
    paragraph_labels = [
        {
            "index": i,
            "role": labels[i]["role"],
            "mode": labels[i]["mode"],
            "guard_strength": labels[i].get("guard_strength"),
            "claim_ref": labels[i].get("claim_ref"),
            "objection_strength": labels[i].get("objection_strength"),
        }
        for i in range(len(labels))
    ]
    # reused_signals (B3/B4 + AGD) is canonical here — fed with argmove_vector's
    # real key shape + fixed values rather than computed, so the golden is
    # byte-stable regardless of whether the Brysbaert concreteness data file is
    # installed in the gen env (the one env-variant signal). Mirrors how the
    # voice_fingerprint / binoculars builders feed representative values.
    reused_signals = {
        "available": True,
        "calibration_status": "heuristic",
        "n_words": 620,
        "signals": {
            "stance.hedge": 3.2, "stance.booster": 1.1, "stance.evidential": 0.8,
            "stance.deontic_modality": 0.5, "stance.epistemic_modality": 1.0,
            "stance.first_person_stance": 2.0, "stance.refusal": 0.0,
            "stance.hedge_booster_ratio": 2.9, "stance.entropy_bits": 1.4,
            "agency.nominalization_per_1k": 12.0,
            "agency.generic_institutional_per_1k": 4.0,
            "agency.concrete_detail_per_1k": 6.0,
            "agency.action_verb_per_1k": 30.0,
            "agency.agentless_passive_per_1k": 1.5,
            "agency.light_verb_per_1k": 2.0, "agency.proper_noun_per_1k": 5.0,
            "agency.entity_to_action_ratio": 0.4,
            "abstraction.mean_concreteness": 2.85,
            "agd.discounting_per_1k": 5.0, "agd.argument_marker_per_1k": 8.0,
            "agd.reason_to_conclusion_ratio": 1.5, "agd.abusive_assuring_per_1k": 0.5,
        },
        "note": (
            "B3 abstraction + B4 stance + AGD marker densities (deterministic, "
            "`heuristic` — descriptive only, NO anchor, not in the aggregate). "
            "No numeric anchor by design (D5): marker density is a different "
            "construct from the paper's judge-rated per-essay stance strength."
        ),
    }
    results = m.build_results_payload(
        target_words=620,
        n_paragraphs=len(paragraphs),
        judge_result=judge_result.to_dict(),
        paragraph_labels=paragraph_labels,
        validation_warnings=val_warnings,
        observed=observed,
        reused_signals=reused_signals,
        contributions=contributions,
        bundles=bundles,
        aggregate=aggregate,
        pre_flag=pre_flag,
        register_warnings=[],
        strongest_internal_objection_engaged=strongest_obj_engaged,
    )
    return m.compose_envelope(
        target_path=Path("<fixture>"),
        target_words=620,
        results=results,
        licenses_text=m.DEFAULT_LICENSES,
        does_not_license_text=m.DEFAULT_DOES_NOT_LICENSE,
    )


def _build_position_pair_register() -> dict[str, Any]:
    import position_pair_register as m  # type: ignore
    import position_pair_register_judge as ppj  # type: ignore

    # The position-pair surface pairs passages that address the SAME question via
    # its judge, applies the F4 Q-gate (refuse + disclose) and the F2 caps, then
    # composes the 1.0 envelope through the SAME compose_envelope() the CLI calls
    # (which runs the F3 runtime banned-key gate before returning). Drive the
    # surface's OWN deterministic mock judge over a marker-annotated fixture text
    # (marker format `[[pair=<id> side=<a|b> q=<question ending with ?>]]`, q= LAST
    # so it swallows the rest of the marker body) so the observed pairs, refusal /
    # cap disclosures, and claim license are the REAL ones the script emits. Two
    # clean same-question pairs, no refusals, no caps — the canonical happy path.
    # Volatile fields (run_timestamp_utc, prompt_fingerprint_sha256) are
    # sentinelized by normalize() so the golden is byte-stable across releases.
    text = (
        "[[pair=p1 side=a q=What is the author's position on carbon pricing?]] "
        "A well-designed carbon price is the most efficient lever available to a "
        "modern state. "
        "[[pair=p1 side=b q=What is the author's position on carbon pricing?]] "
        "Pricing carbon directly is the single most effective policy a government "
        "can adopt. "
        "[[pair=p2 side=a q=How should new transit capacity be funded?]] "
        "Dedicated fuel levies should underwrite the bulk of new transit capacity. "
        "[[pair=p2 side=b q=How should new transit capacity be funded?]] "
        "General revenue, not user fees, ought to carry the cost of expanding "
        "transit."
    )
    judge_result = ppj.build_judge("mock")(text)
    results, warnings = m.build_results(
        judge_result,
        text_len=len(text),
        cap_per_question=m.DEFAULT_CAP_PER_QUESTION,
        cap_per_work=m.DEFAULT_CAP_PER_WORK,
        prompt_fingerprint=ppj.fingerprint_prompt(),
    )
    return m.compose_envelope(
        target_path=Path("<fixture>"),
        target_words=len(m.word_tokens(text)),
        results=results,
        warnings=warnings or None,
    )


def _build_author_corpus_export() -> dict[str, Any]:
    import author_corpus_export as m  # type: ignore
    from output_schema import build_output  # type: ignore

    sha = "sha256:" + "0" * 64
    source_fp = "src:hmac-sha256:" + "1" * 64
    group = "grp:hmac-sha256:" + "2" * 64
    receipt = {
        "schema": m.RECEIPT_SCHEMA,
        "surface": m.TOOL_NAME,
        "surface_version": m.SURFACE_VERSION,
        "producer_revision": "0" * 40,
        "source_snapshot_sha256": sha,
        "document_map_hash": None,
        "document_attestation_hash": None,
        "hmac_key_id": sha,
        "register_map": {"gmail_sent:personal": "email.personal"},
        "allowed_ai_status": ["pre_ai_human"],
        "entries": [{
            "source_entry_fingerprint": source_fp,
            "source_group": group,
            "record_id": sha,
        }],
        "record_ids": [sha],
        "package_hash": sha,
        "counts": {
            "records": 1,
            "by_register": {"email.personal": 1},
            "by_ai_status": {"pre_ai_human": 1},
            "by_source_kind": {"gmail_sent": 1},
            "by_era": {"pre_chatgpt": 1},
        },
        "record_atomic_degraded": False,
    }
    return build_output(
        task_surface=m.TASK_SURFACE, tool=m.TOOL_NAME, version=m.SCRIPT_VERSION,
        target_path=None, target_words=0, baseline=None,
        results={"producer_receipt": receipt}, claim_license=m._claim_license(receipt),
        warnings=[], ai_status=None,
    )


#: surface id -> raw-envelope builder. The id matches the
#: ``capabilities.d/<id>.yaml`` fragment stem and the golden filename stem.
SURFACE_BUILDERS: dict[str, Callable[[], dict[str, Any]]] = {
    "author_corpus_export": _build_author_corpus_export,
    "variance_audit": _build_variance_audit,
    "manuscript_audit": _build_manuscript_audit,
    "repetition_audit": _build_repetition_audit,
    "voice_distance": _build_voice_distance,
    "voice_profile": _build_voice_profile,
    "pov_voice_profile": _build_pov_voice_profile,
    "punctuation_cadence_audit": _build_punctuation_cadence_audit,
    "idiolect_detector": _build_idiolect_detector,
    "narrative_decision_audit": _build_narrative_decision_audit,
    "voice_fingerprint": _build_voice_fingerprint,
    "mimicry_cosplay_audit": _build_mimicry_cosplay_audit,
    "general_imposters": _build_general_imposters,
    "binoculars_audit": _build_binoculars_audit,
    "argument_decision_audit": _build_argument_decision_audit,
    "position_pair_register": _build_position_pair_register,
}


# --------------------------------------------------------------------------
# Generator API (importable; reused by the drift gate).
# --------------------------------------------------------------------------


def surfaces() -> list[str]:
    """Return the sorted list of surfaces that have a golden builder."""
    return sorted(SURFACE_BUILDERS)


def golden_path(surface: str) -> Path:
    return FIXTURES_DIR / f"{surface}.json"


def regenerate_surface(surface: str) -> dict[str, Any]:
    """Build and normalize the canonical envelope for *surface*.

    Returns the normalized envelope dict (the exact thing written to /
    compared against the committed golden). Raises ``KeyError`` for an
    unknown surface.
    """
    builder = SURFACE_BUILDERS[surface]
    return normalize(builder())


def serialize(envelope: dict[str, Any]) -> str:
    """Deterministic pretty JSON: 2-space indent, sorted keys, trailing
    newline. ``sort_keys`` guarantees byte-stable output regardless of the
    insertion order the builders happen to use."""
    return json.dumps(envelope, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def load_golden(surface: str) -> dict[str, Any] | None:
    """Load the committed golden for *surface*, or ``None`` if absent."""
    path = golden_path(surface)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_all() -> list[str]:
    """(Re)write every golden. Returns the list of surfaces written."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for surface in surfaces():
        envelope = regenerate_surface(surface)
        golden_path(surface).write_text(serialize(envelope), encoding="utf-8")
        written.append(surface)
    return written


def check_all() -> list[str]:
    """Regenerate in-memory and compare against committed goldens.

    Returns a list of human-readable drift messages (empty == clean). A
    missing golden, a parse failure, or a post-normalization mismatch each
    produces a message.
    """
    problems: list[str] = []
    for surface in surfaces():
        try:
            regenerated = regenerate_surface(surface)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{surface}: regeneration failed: {exc!r}")
            continue
        committed = load_golden(surface)
        if committed is None:
            problems.append(
                f"{surface}: no committed golden at "
                f"{golden_path(surface).relative_to(PLUGIN_ROOT)} "
                f"(run --write)"
            )
            continue
        # Compare the serialized, normalized forms so the message can point
        # at the exact divergence and the comparison is order-insensitive
        # (both sides go through sort_keys).
        if serialize(regenerated) != serialize(committed):
            problems.append(
                f"{surface}: regenerated envelope differs from committed "
                f"golden (post-normalization). Run "
                f"`python3 scripts/gen_contract_fixtures.py --write` and "
                f"review the diff."
            )
    return problems


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate / verify the R5 golden contract-envelope fixtures."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--write", action="store_true",
        help="Regenerate and overwrite every golden under contract_fixtures/.",
    )
    group.add_argument(
        "--check", action="store_true",
        help="Regenerate in-memory; exit nonzero if any golden drifted.",
    )
    group.add_argument(
        "--list", action="store_true",
        help="List the surfaces that have a golden builder.",
    )
    args = parser.parse_args(argv)

    if args.list:
        for surface in surfaces():
            print(surface)
        return 0

    if args.write:
        written = write_all()
        print(
            f"Wrote {len(written)} golden(s) to "
            f"{FIXTURES_DIR.relative_to(PLUGIN_ROOT)}/:"
        )
        for surface in written:
            print(f"  {surface}.json")
        return 0

    # --check
    problems = check_all()
    if not problems:
        print(
            f"Contract fixtures consistent with build_output: "
            f"{len(surfaces())} surface(s) checked. ✔"
        )
        return 0
    print(f"Contract-fixture drift ({len(problems)}):\n")
    for p in problems:
        print(f"  {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
