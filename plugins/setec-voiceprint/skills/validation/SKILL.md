---
name: validation
description: >
  Validate a SETEC corpus manifest, then measure how SETEC's smoothing-
  diagnosis or voice-coherence signals discriminate against labeled
  validation entries in that manifest. Use when the user asks to
  "validate the manifest," "check corpus_manifest.jsonl for errors,"
  "run the validation harness," "ROC AUC for the variance audit,"
  "FPR target," "calibrate against a labeled corpus," "what does the
  variance audit's ROC curve look like on this corpus," or any request
  to evaluate SETEC's empirical performance. Also triggers on
  "manifest validator," "validation_harness," "ROC AUC," "FPR/TPR/FNR,"
  "BCa interval," "Wilson CI," or "labeled validation corpus."
version: 1.0.0
---

# Empirical Validation (SETEC Surface 3)

This skill checks the integrity of a SETEC corpus manifest and reports how SETEC's smoothing-diagnosis signals performed on the manifest's labeled validation entries. It is the empirical-calibration surface: claims here are about how the framework behaved on a specific corpus, not about how it will behave on unseen corpora.

## What this surface licenses, and what it does not

- **Licenses:** "on this manifest, in these registers, at these lengths, the smoothing-diagnosis signal achieved this ROC AUC and these per-slice rates."
- **Does not license:** "this signal works on AI text in general." The harness reports performance on its own validation set with explicit register, length, AI-status, and language-status slicing; generalization beyond the manifest is the user's claim to make, not the harness's. The harness refuses to publish a single aggregate accuracy number absent a stated FPR target.

## Scripts and when to use which

| Script | Scope | Use when |
|---|---|---|
| `manifest_validator.py` | One JSONL manifest | Refusing contaminated or contradictory inputs before any manifest-driven flow runs |
| `validation_harness.py` | Labeled validation entries in a manifest | Measuring empirical performance by register, length, AI status, and language status |

## Quick CLI

```bash
# Manifest schema and integrity check
python3 "${CLAUDE_PLUGIN_ROOT}/../../scripts/manifest_validator.py" path/to/corpus_manifest.jsonl

# JSON output for piping
python3 "${CLAUDE_PLUGIN_ROOT}/../../scripts/manifest_validator.py" path/to/corpus_manifest.jsonl --json

# Strict mode (warnings count as errors)
python3 "${CLAUDE_PLUGIN_ROOT}/../../scripts/manifest_validator.py" path/to/corpus_manifest.jsonl --strict

# Validation harness (ranking metrics only — no thresholded rates)
python3 "${CLAUDE_PLUGIN_ROOT}/../../scripts/validation_harness.py" path/to/corpus_manifest.jsonl

# With an explicit operating-point target (publishes thresholded FPR/TPR/precision)
python3 "${CLAUDE_PLUGIN_ROOT}/../../scripts/validation_harness.py" path/to/corpus_manifest.jsonl --fpr-target 0.01

# Refuse to run on a manifest with warnings (not just errors)
python3 "${CLAUDE_PLUGIN_ROOT}/../../scripts/validation_harness.py" path/to/corpus_manifest.jsonl --strict-manifest
```

## The 0.01% FPR framing

The brief that informed this surface invokes Soheil Feizi's argument that 0.01% FPR is the only acceptable threshold for student-facing or accusation-grade detector deployment, where the cost of a single false positive (a wrongful accusation in academic-integrity proceedings) dwarfs the cost of a missed AI essay. The harness's `--fpr-target` flag makes this explicit: thresholded rates are reported only when the caller commits to an operating point. A bare `validation_harness.py path/to/manifest.jsonl` reports ROC AUC and average precision (with confidence-interval reporting on per-slice proportion rates only when a threshold has been chosen) but refuses to publish a binary accuracy number.

## ESL handling

The manifest carries a `language_status` field (`native | non_native_advanced | non_native_intermediate | learner | unknown`). The validator warns when non-native entries land in `use: baseline` or `use: voice_profile` because ESL prose sits in the same low-variance region as RLHF-aligned LLM output (Liang et al., *Patterns* 2023, 61% average FPR on TOEFL essays across seven detectors). The harness slices by `language_status` so per-class FPR is reported separately rather than aggregated; a model that hits 0.5% overall FPR by averaging 0.1% native FPR with 5% ESL FPR is producing the wrong number.

## Setup prerequisite

```bash
pip install -r "${CLAUDE_PLUGIN_ROOT}/../../requirements.txt"
python -m spacy download en_core_web_sm
```

The harness uses `scikit-learn`'s metrics (`roc_auc_score`, `average_precision_score`, `confusion_matrix`) and `statsmodels`' proportion intervals (Wilson default; Agresti-Coull, Clopper-Pearson, Jeffreys also available via `--ci-method`). Both are required by `requirements.txt`. Stdlib fallbacks exist for both — the harness will run without sklearn or statsmodels but the calibration surface is weaker.
