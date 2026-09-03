---
name: smoothing-diagnosis
description: >
  Diagnose AI-prose smoothing in fiction or argument-shaped nonfiction.
  Use when the user asks to "audit prose for AI smoothing," "run a
  variance audit," "check for AI patterns in this draft," "is this
  draft compressed," "Layer A diagnostic," "manuscript-wide variance
  audit," "habit vocabulary," "chapter distinctiveness," "find the
  AI patches in this chapter," or any request to measure whether a
  text has been smoothed into a narrower-than-typical region of
  stylometric space. Also triggers on "burstiness," "MATTR," "MTLD,"
  "Yule's K," "FKGL std," "MDD variance," "dependency-distance
  distribution," "long-range dependency tail," "adjacent-sentence
  cosine cohesion," or "sliding-window scan."
version: 1.0.1
---

# Smoothing Diagnosis (SETEC Surface 1)

This skill primarily measures whether a target document occupies a narrower-than-typical region of human stylometric space. It also routes to a cross-surface dependency-distance follow-up when the operator needs the full syntactic-link distribution behind the variance audit's MDD summary. It does **not** answer who wrote the document or whether the compression is the writer's natural register.

## What this surface licenses, and what it does not

- **Licenses:** "this prose shows characteristics of AI smoothing," with a band classification (Lightly / Moderately / Heavily smoothed / Insufficient signal) and per-signal compression evidence.
- **Does not license:** "this prose was written by AI." Provenance is a different surface; classical stylometry cannot adjudicate it from the surface form alone. ESL writing and certain natural registers (technical prose, institutional voice) sit in the same low-variance region as RLHF-aligned LLM output and will land in the smoothed band without AI involvement.

## Scripts and when to use which

| Script | Scope | Use when |
|---|---|---|
| `variance_audit.py` | Single document | Diagnostic on one chapter, scene, or essay |
| `dependency_distance_audit.py` | Single document; cross-surface `voice_coherence` follow-up | Tier-2 MDD needs its dependency-distance histogram, adjacent/long-range shares, or pooled-distribution shape inspected |
| `manuscript_audit.py` | Multi-chapter manuscript | Surfacing manuscript-wide compression patterns and outlier chapters |
| `repetition_audit.py` | Single document, vocabulary level | Layer A flagged lexical compression and you want specific habit-vocabulary candidates |
| `manuscript_repetition_audit.py` | Manuscript, vocabulary level | Surfacing dispersed habit-vocabulary that recurs across chapters at moderate frequency |
| `chapter_distinctiveness_audit.py` | Manuscript, vocabulary level | Surfacing words distinctive to one chapter against the rest of the manuscript (leave-one-out) |
| `bigram_diff.py` | Single document vs. cluster, syntactic level | The variance audit's POS-bigram KL elevated against a baseline and you want to know which specific POS-bigrams are driving the divergence |
| `manuscript_bigram_diff.py` | Corpus A vs. corpus B, syntactic level | Comparing the syntactic-template footprint of two corpora (e.g. AI-collaborated cohort vs. pre-AI archive) at the aggregate level |

## Quick CLI

The plugin's scripts ship inside the plugin directory at `${CLAUDE_PLUGIN_ROOT}/scripts/`. Use `${CLAUDE_PLUGIN_ROOT}` to reach them portably:

```bash
# Whole-document Layer A audit
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/variance_audit.py" path/to/draft.txt

# JSON output for downstream piping
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/variance_audit.py" path/to/draft.txt --json

# Cross-surface follow-up: full dependency-distance distribution and shape
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dependency_distance_audit.py" path/to/draft.txt --json

# Optional operator-chosen boundary for the reported long-range share
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/dependency_distance_audit.py" path/to/draft.txt \
    --long-threshold 5 --json

# Cross-surface read as the follow-up to a single variance audit: the
# full_picture run-set collects variance + paragraph + AIC + discourse +
# agency (+ voice_distance when --baseline-dir is given; general_imposters /
# idiolect_detector join via --attach) and feeds them to the
# surface_disagreement_resolver
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/setec_run_set.py" --set full_picture \
    --target path/to/draft.txt --baseline-dir path/to/baseline/

# Compare against a personal baseline (z-scores)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/variance_audit.py" path/to/draft.txt --baseline-dir path/to/baseline/

# Length-matched bootstrap percentiles (recommended at small N or small baselines)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/variance_audit.py" path/to/draft.txt --baseline-dir path/to/baseline/ --bootstrap

# Sliding-window scan to localize compression within a long document
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/variance_audit.py" path/to/draft.txt --window-size 1000 --window-stride 500

# Cross-chapter manuscript dashboard
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/manuscript_audit.py" path/to/manuscript.md --baseline-dir path/to/baseline/

# Manuscript-aggregate habit-vocabulary audit
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/manuscript_repetition_audit.py" path/to/manuscript.md --baseline-dir path/to/baseline/

# Chapter-distinctiveness audit (leave-one-out internal baseline; no external baseline needed)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/chapter_distinctiveness_audit.py" path/to/manuscript.md

# Per-bigram diff: target document vs. cluster of comparators (both pooled and per-file mean)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/bigram_diff.py" path/to/target.md \
    --cluster-dir path/to/comparators/ --top 20 --min-count 5

# Per-bigram diff: corpus A vs. corpus B at the aggregate level
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/manuscript_bigram_diff.py" \
    --corpus-a-dir path/to/post_ai/ --label-a "post-ai" \
    --corpus-b-dir path/to/pre_ai/  --label-b "pre-ai" \
    --top 20 --min-count 10
```

## Setup prerequisite

Before invoking any script, confirm dependencies are installed in the user's Python environment. The plugin's `requirements.txt` declares the runtime stack:

```bash
pip install -r "${CLAUDE_PLUGIN_ROOT}/requirements.txt"
python -m spacy download en_core_web_sm
```

The smoothing scripts degrade gracefully when optional Tier 2 (spaCy) or Tier 3 (sentence-transformers / scikit-learn) components are missing. The dependency-distance follow-up has no faithful parse-free mode: without spaCy plus `en_core_web_sm`, it returns `available: false` with `reason_category: missing_dependency` and exits 3.

## Interpreting the output

The smoothing scripts' JSON output and markdown headers carry `task_surface: smoothing_diagnosis`. The cross-surface `dependency_distance_audit.py` follow-up instead carries `task_surface: voice_coherence`; it is a descriptive distribution/shape read with no verdict, band, baseline, authorship/AI inference, writing-quality/readability judgment, or "complexity score." It is English- and length-sensitive; treat results below roughly 150 parsed tokens as unstable.

The variance audit's `mdd_sd` is the across-sentence standard deviation of sentence-level mean dependency distance. The dependency follow-up's `results.shape.sd` is different: it is the standard deviation within the pooled distribution of individual dependency-link distances. Reference math for the smoothing surface lives at `${CLAUDE_PLUGIN_ROOT}/references/distributional-diagnostics.md`; its length floors, calibration warnings, and writer-specific calibration note remain authoritative for smoothing-band interpretation.

A `setec_run_set.py` run additionally emits the surface-disagreement report: the per-surface readings table plus every disagreement pattern compatible with them. Multiple matches are expected; the framework refuses to rank them — read the matched interpretations as a differential, and use the mechanical `next_action` block for the exact follow-up commands.

When a baseline is supplied, prefer `--bootstrap` over the default z-score path at small target N (under 1,000 words) or small baseline file counts (under 10 files): the empirical length-matched percentile with a BCa CI is more reliable than a z-score against full-file baseline aggregates.
