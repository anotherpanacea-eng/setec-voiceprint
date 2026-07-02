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
  "Yule's K," "FKGL std," "MDD variance," "adjacent-sentence cosine
  cohesion," or "sliding-window scan."
version: 1.0.0
---

# Smoothing Diagnosis (SETEC Surface 1)

This skill measures whether a target document occupies a narrower-than-typical region of human stylometric space. It does **not** answer who wrote the document or whether the compression is the writer's natural register; it answers *only* whether the surface has been smoothed.

## What this surface licenses, and what it does not

- **Licenses:** "this prose shows characteristics of AI smoothing," with a band classification (Lightly / Moderately / Heavily smoothed / Insufficient signal) and per-signal compression evidence.
- **Does not license:** "this prose was written by AI." Provenance is a different surface; classical stylometry cannot adjudicate it from the surface form alone. ESL writing and certain natural registers (technical prose, institutional voice) sit in the same low-variance region as RLHF-aligned LLM output and will land in the smoothed band without AI involvement.

## Scripts and when to use which

| Script | Scope | Use when |
|---|---|---|
| `variance_audit.py` | Single document | Diagnostic on one chapter, scene, or essay |
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

The scripts degrade gracefully when Tier 2 (spaCy) or Tier 3 (sentence-transformers / scikit-learn) are missing, but the recommended install gives the full diagnostic.

## Interpreting the output

Every script's JSON output and markdown header carry `task_surface: smoothing_diagnosis` so downstream consumers route correctly. The variance audit reports an aggregate band classification plus the eleven per-signal evidence items. Reference math lives at `${CLAUDE_PLUGIN_ROOT}/references/distributional-diagnostics.md`. Length floors, calibration warnings, and the writer-specific calibration note are documented there.

A `setec_run_set.py` run additionally emits the surface-disagreement report: the per-surface readings table plus every disagreement pattern compatible with them. Multiple matches are expected; the framework refuses to rank them — read the matched interpretations as a differential, and use the mechanical `next_action` block for the exact follow-up commands.

When a baseline is supplied, prefer `--bootstrap` over the default z-score path at small target N (under 1,000 words) or small baseline file counts (under 10 files): the empirical length-matched percentile with a BCa CI is more reliable than a z-score against full-file baseline aggregates.
