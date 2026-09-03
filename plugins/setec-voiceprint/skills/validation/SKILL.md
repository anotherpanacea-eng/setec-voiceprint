---
name: validation
description: >
  Validate a SETEC corpus manifest, then measure how SETEC's smoothing-
  diagnosis or voice-coherence signals discriminate against labeled
  validation entries in that manifest. Use when the user asks to
  "validate the manifest," "check corpus_manifest.jsonl for errors,"
  "check the corpus for contamination," "run check_corpus,"
  "run the validation harness," "ROC AUC for the variance audit,"
  "FPR target," "calibrate against a labeled corpus," "what does the
  variance audit's ROC curve look like on this corpus," or any request
  to evaluate SETEC's empirical performance. Also triggers on
  "manifest validator," "check_corpus," "validation_harness," "ROC AUC,"
  "FPR/TPR/FNR," "BCa interval," "Wilson CI," "conformal,"
  "prediction set," "abstention," "exchangeability," "FPR-bound
  threshold," or "labeled validation corpus."
version: 1.0.1
---

# Empirical Validation (SETEC Surface 3)

This skill checks the integrity and content hygiene of a SETEC corpus, then reports how SETEC's smoothing-diagnosis signals performed on the manifest's labeled validation entries. It also routes operator-precomputed nonconformity scores to the `conformal_gate` abstention layer. It is the empirical-calibration surface: claims here are about behavior on a specific corpus or exchangeable calibration set, not about unseen corpora in general.

## What this surface licenses, and what it does not

- **Licenses:** "on this manifest, in these registers, at these lengths, the smoothing-diagnosis signal achieved this ROC AUC and these per-slice rates."
- **Does not license:** "this signal works on AI text in general." The harness reports performance on its own validation set with explicit register, length, AI-status, and language-status slicing; generalization beyond the manifest is the user's claim to make, not the harness's. The harness refuses to publish a single aggregate accuracy number absent a stated FPR target.

## Scripts and when to use which

| Script | Scope | Use when |
|---|---|---|
| `manifest_validator.py` | One JSONL manifest | Refusing contaminated or contradictory inputs before any manifest-driven flow runs |
| `check_corpus.py` | Files, directories, or manifest slice ≤ ~1M files | Refusing HTML/CSS/code/table contamination before KL-sensitive or validation runs |
| `shard_runner.py --task corpus_hygiene` | Manifest > ~1M files | Same contamination check, sharded with workers + state.json checkpointing for corpora at RAID scale |
| `validation_harness.py` | Labeled validation entries in a manifest | Measuring empirical performance by register, length, AI status, and language status |
| `conformal_gate.py` | Operator-precomputed nonconformity scores; no prose or manifest input | Producing one- or two-class prediction sets, or a one-tailed reference-class FPR-bound threshold |

### Picking between `check_corpus.py` and the sharded path

`check_corpus.py` is the right tool for manifests up to ~1M files. Beyond that the single-process iteration sinks into NTFS small-file open latency and becomes impractical. As an order-of-magnitude guide:

| Manifest size | Tool | Approximate wall-clock |
|---|---|---|
| 10K files | `check_corpus.py` | seconds |
| 100K files | `check_corpus.py` | ~5 min |
| 436K files (MAGE) | `check_corpus.py` | ~30 min |
| 1M files | borderline — either tool | ~1-2 hr direct, ~15 min sharded |
| 8.3M files (RAID) | `shard_runner.py --task corpus_hygiene` (with `--workers 8`) | ~13 hr direct, ~1.5 hr sharded |

`check_corpus.py` prints a runtime warning suggesting the sharded path when the input manifest exceeds ~1M files. See `scripts/calibration/RUNBOOK_corpus_hygiene_sharded.md` for the sharded workflow.

## Quick CLI

```bash
# Manifest schema and integrity check
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/manifest_validator.py" path/to/corpus_manifest.jsonl

# JSON output for piping
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/manifest_validator.py" path/to/corpus_manifest.jsonl --json

# Strict mode (warnings count as errors)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/manifest_validator.py" path/to/corpus_manifest.jsonl --strict

# Content-level corpus hygiene check
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check_corpus.py" \
    --manifest path/to/corpus_manifest.jsonl \
    --filter use=baseline

# Validation harness (ranking metrics only — no thresholded rates)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validation_harness.py" path/to/corpus_manifest.jsonl

# Validation harness with corpus hygiene preflight
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validation_harness.py" \
    path/to/corpus_manifest.jsonl \
    --check-corpus

# With an explicit operating-point target (publishes thresholded FPR/TPR/precision)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validation_harness.py" path/to/corpus_manifest.jsonl --fpr-target 0.01

# Refuse to run on a manifest with warnings (not just errors)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validation_harness.py" path/to/corpus_manifest.jsonl --strict-manifest

# One-class prediction set at the default alpha (0.1)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/conformal_gate.py" \
    --calibration path/to/reference_scores.txt --score 4.2

# Two-class prediction set from JSON-list or newline-delimited score files
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/conformal_gate.py" \
    --calibration path/to/reference_scores.json \
    --calibration-positive path/to/positive_scores.json \
    --score 4.2 --direction two_sided --json

# Threshold-only reference-class FPR ceiling (one-tailed directions only)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/conformal_gate.py" \
    --calibration path/to/reference_scores.txt --fpr-bound 0.05 \
    --direction higher_is_nonconforming --json
```

## Conformal abstention follow-up

`conformal_gate.py` is a stdlib methodology wrapper over scores produced elsewhere, not a detector or a prose scorer. `--calibration` accepts a JSON list or newline-delimited floats. `--score` is required for the one- and two-class gates, but optional with `--fpr-bound`, which can emit a threshold without evaluating a target. Default output is Markdown; add `--json` for the normalized envelope or `--out PATH` to write either format.

The one-class result tests out-of-distribution membership against the supplied reference, not authorship. In two-class mode, both an empty prediction set and a set containing both labels are licensed abstentions. A conformal p-value is not a probability that text is AI. Coverage and FPR claims are marginal: in one-class and FPR-bound modes, the reference calibration scores must represent the reference class and be exchangeable with reference targets; in two-class mode, each class's supplied calibration scores must be representative and exchangeable with targets from that class.

`--fpr-bound` accepts only `higher_is_nonconforming` or `lower_is_nonconforming`; `two_sided` has no single tail for this ceiling. The legacy `threshold` field remains the nonconformity-space cutoff. For operator use, apply the separate raw-domain pair `raw_score_threshold` and `raw_score_comparator` (`>=` for higher, `<=` for lower). An empty primary calibration file in any mode, an empty positive calibration file in two-class mode, or an FPR-bound calibration set that is too small or too tied produces an `available: false` report with a warning (exit 0) rather than an unsafe result. The command returns evidence for an operator to interpret: it does not wire itself into the validation harness, apply a detector verdict, or complete the roadmap's larger C2/bakeoff integration.

## The 0.01% FPR framing

The brief that informed this surface invokes Soheil Feizi's argument that 0.01% FPR is the only acceptable threshold for student-facing or accusation-grade detector deployment, where the cost of a single false positive (a wrongful accusation in academic-integrity proceedings) dwarfs the cost of a missed AI essay. The harness's `--fpr-target` flag makes this explicit: thresholded rates are reported only when the caller commits to an operating point. A bare `validation_harness.py path/to/manifest.jsonl` reports ROC AUC and average precision (with confidence-interval reporting on per-slice proportion rates only when a threshold has been chosen) but refuses to publish a binary accuracy number.

## ESL handling

The manifest carries a `language_status` field (`native | non_native_advanced | non_native_intermediate | learner | unknown`). The validator warns when non-native entries land in `use: baseline`, `use: voice_profile`, or `use: idiolect` because ESL prose sits in the same low-variance region as RLHF-aligned LLM output (Liang et al., *Patterns* 2023, 61% average FPR on TOEFL essays across seven detectors). The harness slices by `language_status` so per-class FPR is reported separately rather than aggregated; a model that hits 0.5% overall FPR by averaging 0.1% native FPR with 5% ESL FPR is producing the wrong number.

## Setup prerequisite

```bash
pip install -r "${CLAUDE_PLUGIN_ROOT}/requirements.txt"
python -m spacy download en_core_web_sm
```

The harness uses `scikit-learn`'s metrics (`roc_auc_score`, `average_precision_score`, `confusion_matrix`) and `statsmodels`' proportion intervals (Wilson default; Agresti-Coull, Clopper-Pearson, Jeffreys also available via `--ci-method`). Both are required by `requirements.txt`. Stdlib fallbacks exist for both — the harness will run without sklearn or statsmodels but the calibration surface is weaker.
