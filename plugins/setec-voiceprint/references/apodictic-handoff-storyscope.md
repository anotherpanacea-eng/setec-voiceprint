# APODICTIC handoff: StoryScope / narrative-decision integration

**Filed:** 28 May 2026
**For:** APODICTIC's consumer-list expansion to cover Surface 6 (narrative-decision audit) and its calibration sidecar.
**Triggered by:** Russell et al. 2026 *StoryScope* (arXiv:2604.03136v4) integration into SETEC v1.107.0+ (PRs #128, #129). The framework now ships a sixth task surface with the same `schema_version: "1.0"` envelope as the existing five.

**Status:** integration spec. Documents the consumer-pinning contract for `narrative_decision_audit` (currently `handoff: experimental` in `capabilities.yaml`); not yet a formal commitment from APODICTIC's side. SETEC has stabilized the envelope and the per-signal contributions shape — the experimental flag covers the aggregate-score math (whose design questions are noted in §C.1) and the option to swap in a 10-aspect-prompt judge pipeline in v0.2. The canonical query for APODICTIC's pinned surface is `capabilities.py list --consumer apodictic`.

---

## Summary

PR #128 added a new SETEC task surface — `narrative_decision_audit` — that scores prose against the 30 *core narrative-decision features* from Russell et al. 2026's StoryScope paper. PRs #129/#130 added a capabilities manifest and a per-PR review-cleanup cycle. The full StoryScope work consists of:

  - a single-doc audit (`narrative_decision_audit.py`) that emits a schema_version 1.0 envelope under the `narrative_decision_audit` task_surface,
  - a calibration-side polarity audit (`narrative_polarity_audit.py`) that emits a structured JSON + markdown findings document parallel to the 2026-05-10 EditLens / 2026-05-11 MAGE reports,
  - an importable Python data layer (`narrative_feature_schema.py`) carrying the 30 features, 7 bundles, 10 NarraBench dimensions, and per-signal paper-reported human/AI group means,
  - a replication scaffold (`scripts/replication/`) for L1/L2/L3 StoryScope replication (not in scope for APODICTIC integration).

This document splits that surface into **three integration tiers** for APODICTIC and proposes which to pin against.

## Tier A — runtime envelope endpoints (recommended consumer list)

Two new envelopes are eligible for APODICTIC's `schema_version: "1.0"` consumer list. Both ship with the same `target/baseline/results/claim_license` shape APODICTIC already parses for variance_audit, voice_distance, etc.

### A.1 `narrative_decision_audit`

  - **task_surface:** `narrative_decision_audit`
  - **tool:** `narrative_decision_audit`
  - **script:** `plugins/setec-voiceprint/scripts/narrative_decision_audit.py`
  - **schema_version:** `1.0`
  - **calibration status:** `literature_anchored` (anchored to Russell et al. 2026 group means)
  - **ships uncalibrated:** verdict band defaults to `uncalibrated`; framework does not ship `--threshold-low` / `--threshold-high`

**Results-block shape (the keys APODICTIC will parse):**

```jsonc
"results": {
  "judge": {                       // provenance: which model produced the values
    "values": {...},
    "per_feature_confidence": {...},
    "judge_identity": {
      "kind": "manifest|mock|anthropic|openai|gemini",
      "model": "...", "model_revision": "...",
      "prompt_version": "..."
    },
    "raw_response_truncated": "..."
  },
  "prompt_fingerprint_sha256": "...",  // SHA-256 of system preamble + user prompt
  "target": {
    "words": 5023,
    "register_warnings": [
      "Target is 1450 words; paper's home register is long-form fiction..."
    ]
  },
  "values": { "<feature_key>": "<value or [values]>" },
  "validation_warnings": [...],
  "contributions": [                // 33 entries, one per signal
    {
      "feature_key": "agency_in_resolution",
      "feature_label": "Agency in Resolution",
      "dimension": "PLT",
      "bundle": "structural_streamlining",
      "option": "protagonist_choice",  // null for scale/ordinal/binary
      "leaning": "ai",                 // or "human"
      "paper_human_mean": 0.46,
      "paper_ai_mean": 0.69,
      "target_value": 1.0,             // null if unavailable
      "contribution": -1.348,          // null if unavailable
      "direction": "ai"                // ai | human | neutral | unavailable
    }, ...
  ],
  "bundles": [                      // 7 entries
    {
      "bundle": "structural_streamlining",
      "label": "AI-elevated: Structural streamlining",
      "n_signals": 8,
      "n_evaluated": 8,
      "mean_contribution": -0.842,
      "human_leaning_signals": 1,
      "ai_leaning_signals": 7,
      "neutral_signals": 0
    }, ...
  ],
  "aggregate": {
    "score": -1.234,                  // mean per-signal contribution
    "n_signals_evaluated": 33,
    "n_signals_total": 33,
    "verdict_band": "uncalibrated",   // or ai_likely | human_likely | indeterminate | unavailable
    "thresholds": {"low": null, "high": null}
  },
  "run_timestamp_utc": "2026-05-28T14:33:21+00:00"
}
```

**claim_license-block fields APODICTIC should surface:**

  - `task_surface = "narrative_decision_audit"`
  - `licenses` text (default 95-word block describing what the score *is*)
  - `does_not_license` text (the anti-verdict discipline — narrative-decision evidence does not entitle binary AI/human verdicts)
  - `comparison_set.literature_anchor` = the paper citation string
  - `comparison_set.judge_kind` / `judge_model`
  - `comparison_set.prompt_fingerprint_sha256` (parity-check value for cross-run comparison)
  - `length_range_words = [2000, 25000]`
  - `register_match = ["long_form_fiction"]`
  - `additional_caveats` (register warnings + uncalibrated-band caveat surfaced into the block)

**APODICTIC-side decision:** pin against this surface only if APODICTIC plans to surface narrative-level evidence to operators. The per-signal `contributions` block is the load-bearing payload; per-bundle aggregates are a convenience layer.

### A.2 `narrative_polarity_audit` — non-envelope calibration sidecar

  - **tool:** `plugins/setec-voiceprint/scripts/calibration/narrative_polarity_audit.py`
  - **NOT envelope-compatible.** The script does not declare a `TASK_SURFACE` constant, does not call `output_schema.build_output()`, and does not emit a `schema_version: "1.0"` envelope. It emits a free-form *calibration-findings* JSON document modeled on `calibration-findings-2026-05-10.md` / `-2026-05-11-mage.md` for Tier-1 variance signals.
  - **NOT in the capabilities manifest.** No entry currently exists for it. The script is an operator-side calibration utility consumed by SETEC's own findings-doc workflow, not by APODICTIC's runtime envelope-pinning machinery.
  - **Not on the consumer-list contract.** Treat A.2 as informational rather than as a pin target; if APODICTIC wants this output stably, it'll need a separate piece of work either (a) adding a `TASK_SURFACE = "calibration"` + `build_output()` wrapper, or (b) adding it to the manifest with `handoff: experimental` and the actual shape documented below.

**Why APODICTIC may still want to read it:** the polarity audit produces per-signal direction-aware AUC + verdict (`matches` / `inverted` / `chance`) against an operator-labeled corpus. This is the structured evidence operators consume to know which of the 33 signals' polarity survives on a given corpus — the same workflow that produced the 2026-05-10 EditLens and 2026-05-11 MAGE findings for Tier-1 variance signals. APODICTIC's verdict layer could read this output to apply a per-signal confidence haircut to A.1 evidence, with the understanding that the shape may evolve without a `schema_version` bump.

**Actual JSON shape** (emitted by `build_report()`):

```jsonc
{
  "corpus_name": "EditLens val (essays, 2026-05-10)",
  "manifest_path": "...",
  "n_rows": {"human": 753, "ai": 753, "loaded": 1506, "skipped": 0},
  "validation_warning_count": 0,
  "aggregate_scorer": {
    "auc": 0.821, "se": 0.014, "n_pos": 753, "n_neg": 753
  },
  "signal_summary": {
    "matches": 18, "inverted": 6, "chance": 9, "unavailable": 0
  },
  "cells": [                       // 33 entries — one per signal
    {
      "feature_key": "...",
      "feature_label": "...",
      "bundle": "...",
      "option": "...",
      "paper_leaning": "ai|human",
      "paper_human_mean": 0.50,
      "paper_ai_mean": 0.72,
      "n_pos": 753, "n_neg": 753,
      "raw_auc": 0.78,
      "da_auc": 0.78,                // direction-aware: ≥0.5 = matches paper
      "se": 0.014,
      "verdict": "matches",          // matches | inverted | chance | unavailable
      "notes": [...]
    }, ...
  ]
}
```

Note the absence of the `target/baseline/results/claim_license` envelope. The fields shown are *the* output, top-level. There is no `schema_version`, no `task_surface`, no `claim_license` block.

**APODICTIC-side decision:** if APODICTIC wants this as a contract-stable input, file a request for either (a) envelope wrapping or (b) a manifest entry with explicit `handoff: experimental` framing. Until either lands, treat this section as a *description of an internal SETEC output APODICTIC could read with operator awareness that the shape may change*, not as a pin-able interface.

## Tier B — vocabulary surfaces (importable)

Three Python modules expose typed data that APODICTIC could lift as a *shared vocabulary*, independent of whether APODICTIC consumes SETEC's runtime envelopes. This makes sense if APODICTIC has its own narrative-feature computation pipeline (its own LLM judge, its own XGBoost, etc.) and wants to share feature keys, options, and paper-reported anchors with SETEC for cross-tool consistency.

### B.1 `narrative_feature_schema.CORE_FEATURES`

  - **Module:** `plugins/setec-voiceprint/scripts/narrative_feature_schema.py`
  - **Exported:** `CORE_FEATURES: tuple[CoreFeature, ...]` (30 entries), `iter_signals()` helper yielding `(feature, signal_index, signal)` tuples (33 entries).
  - **Per-entry:** `key`, `label`, `dimension` (NarraBench), `feature_type` ("scale" | "ordinal" | "categorical" | "binary" | "multi"), `question` (the LLM prompt text), `description`, `response_options` (tuple), `signals` (tuple of `FeatureSignal`s with `option` / `leaning` / `human_mean` / `ai_mean` / `bundle`), `paper_table_row`.
  - **Provenance:** every numeric value is transcribed from the paper's Table 12; an import-time self-check asserts 30 features / 33 signals / leaning↔gap-sign consistency / dimension and bundle membership.

**Use case for APODICTIC:** shared feature-key vocabulary across SETEC's audit output and APODICTIC's internal representation. APODICTIC's verdict layer could match on `feature_key="dominant_emotional_expression"` and surface paper-anchored prevalence figures without recomputing them.

### B.2 `narrative_feature_schema.BUNDLE_LABELS` (7 entries)

The paper's interpretive themes:

```python
BUNDLE_LABELS = {
  "thematic_over_determination":     "AI-elevated: Thematic over-determination",
  "sensory_embodied_performativity": "AI-elevated: Sensory & embodied performativity",
  "structural_streamlining":         "AI-elevated: Structural streamlining",
  "intertextual_richness":           "Human-elevated: Intertextual richness",
  "reader_engagement":               "Human-elevated: Reader engagement",
  "temporal_complexity":             "Human-elevated: Temporal complexity",
  "narrative_diversity":             "Human-elevated: Narrative diversity",
}
```

**Use case for APODICTIC:** bundle-level summary text in verdict reports. The bundles cluster the 33 signals into operator-readable groupings (e.g., "this story scores high on sensory_embodied_performativity and structural_streamlining" is a single-sentence summary the verdict layer can produce without re-engineering the taxonomy).

### B.3 `narrative_feature_schema.DIMENSION_LABELS` (10 entries)

The NarraBench dimensions:

```python
DIMENSION_LABELS = {
  "SIT": "Situatedness", "AGENT": "Agents", "PLT": "Plot",
  "EVT": "Events", "SET": "Setting", "REV": "Revelation",
  "TMP": "Time", "PER": "Perspective", "SOC": "Social Network",
  "STR": "Structure",
}
```

**Use case for APODICTIC:** dimension-level rollup (one level above bundles), shared with NarraBench-aware downstream tools.

### B.4 Status vocabulary (registry extension)

The capabilities manifest (PR #129) extends the framework's calibration-status vocabulary with `literature_anchored` as the canonical status for the 34 paper-anchored narrative-decision signals + aggregate. Total `literature_anchored` count: 6 → 40. APODICTIC's verdict-licensing layer (the "what does this measurement entitle a reader to claim" gate) can reuse the same vocabulary unchanged. Glossary cross-reference: `plugins/setec-voiceprint/references/signals-glossary.md`.

## Tier C — do not pin against

Three pieces of the StoryScope work are intentionally *not* in the integration surface.

### C.1 The aggregate `score` field

The aggregate is the mean per-signal `contribution` across all evaluated signals, in human-z-units relative to the paper means (1.0 = paper's human mean; 0.0 = paper's AI mean). It is exposed in the runtime envelope because operators inspecting a single document find it useful, but APODICTIC should NOT pin verdicts to it:

  - **Per-signal influence is unequal.** Contributions are `(target_value - ai_mean) / (human_mean - ai_mean)`, where the denominator varies ~6.3× across signals. A scale feature with a small paper gap (~0.20) can produce a single-signal contribution of ±11, swamping the other 32 signals.
  - **Unbounded.** A target value outside both means produces a contribution outside [0, 1], with no natural ceiling.
  - **Not what the paper reports.** Russell et al. use XGBoost+SHAP, not a mean-of-ratios. The framework-side aggregate is a single-doc convenience, not a published statistic.

**Recommended APODICTIC pattern:** if APODICTIC needs a single number, compute it from the per-signal `contributions` array using a saner pooling rule (clip to [-1, 1] then mean; or threshold-and-vote per signal; or a SHAP-style weighted sum if APODICTIC trains its own classifier on a labeled corpus).

### C.2 Judge prompts and judge identity

The narrative-decision audit ships a pluggable judge interface (default = `manifest` backend reading pre-computed values; convenience adapters for `anthropic`/`openai`/`gemini`). The system preamble and feature schema are exposed as a SHA-256 fingerprint (`prompt_fingerprint_sha256`) in the envelope so APODICTIC can verify reproducibility across runs, but:

  - **Operators choose models.** Pinning APODICTIC's verdict layer to a specific judge model (Claude Sonnet 4.6, GPT-5.4, etc.) would re-introduce the model-coupling the framework's pluggable-judge design avoids.
  - **Prompts may evolve.** v0.1 ships a single consolidated prompt; v0.2 may swap in the paper's 10 aspect-prompt pipeline for higher reproducibility. The `prompt_fingerprint_sha256` is the version-detection key; consumers verify identity, not content.

**Recommended APODICTIC pattern:** consume `judge_identity` and `prompt_fingerprint_sha256` for provenance metadata in the verdict report, but treat them as informational. Do not gate verdicts on specific judge models.

### C.3 The replication scaffold (`scripts/replication/`)

PR #128's L2/L3 replication tooling — corpus construction stages, XGBoost training, SHAP, LDA projection, rarity analysis — is research tooling, not an integration surface. The stages are stubs around the judge interface and assume operator-supplied API budget ($4k+ for L3). APODICTIC should not pin against `replication/manifest_format.py` or any of the `stages/*.py` files.

The spec doc at `plugins/setec-voiceprint/references/narrative-decision-replication-spec.md` documents this explicitly: the framework ships methodology, not corpora and not trained classifiers.

## Framing recommendation

If APODICTIC presents narrative-decision evidence to operators alongside Tier-1 variance / AIC / Binoculars signals, surface this interpretive note:

> Narrative-decision signals score how a story is *built* — its themes, plot structure, sensory register, reader stance, temporal arrangement — rather than how its sentences are *phrased*. Russell et al. 2026 reports that detection accuracy drops only 1.6 macro-F1 points after LAMP-style span-level rewriting that scrubs surface artifacts (95.5 → 93.9), because removing narrative-decision tells requires structural rewrites, not phrase-level substitution. When narrative-decision evidence and texture-level evidence disagree, narrative-decision evidence is the more rewrite-resistant of the two.

This is *not* a threshold, *not* a verdict, and *not* shipped per-signal in the envelope. It is an interpretive framing operators benefit from when reading mixed-evidence reports. The framework-side claim_license block already encodes the "complementary, not substitute" framing in its `does_not_license` text; the verdict-layer version is the operator-facing rephrasing.

## Open questions for APODICTIC

  1. **Which surfaces to pin?** Tier A.1 alone, or also Tier B? (Note: A.2 is not currently pin-able — it's a non-envelope sidecar; see §A.2 for the gating decision before it could become a pin target.)
  2. **Aggregate score posture?** Re-emit SETEC's aggregate, replace with APODICTIC-computed pool, or surface per-signal only?
  3. **Polarity-audit consumption?** Does APODICTIC's verdict layer want to read `signals_summary.inverted` as a confidence haircut on narrative-decision evidence?
  4. **Cross-tool vocabulary?** Adopt `narrative_feature_schema.CORE_FEATURES` as a shared vocabulary across both projects, or maintain APODICTIC's own taxonomy?
  5. **Polarity coverage:** APODICTIC operators running on registers outside long-form fiction (essays, op-eds, novels in translation) need the polarity-check workflow to validate the paper's signal direction before drawing inference. Should APODICTIC ship a default polarity-check report alongside its verdict UI, or gate the surface behind an operator-supplied polarity manifest?

---

## Status

  - **Filed:** 28 May 2026
  - **Author-side ownership:** SETEC framework maintainer (anotherpanacea-eng).
  - **Consumer-side decision needed:** APODICTIC team, on the open questions above.
  - **No commitment from this document.** APODICTIC may decide that narrative-decision evidence is out of scope for v1; in that case this spec serves as the reference for a future integration round. No SETEC-side change is implied by APODICTIC's decision either way — Surface 6 ships on `main` regardless.
