# 05-esl-fairness-slice

> An ESL / L2 / translated-text **fairness slice** in the validation harness:
> per-language-status FPR/TPR so SETEC can measure (and refuse to exceed) the
> documented non-native-English false-positive failure mode.

- **Status:** Ready
- **Tier:** near-term
- **GPU required:** no
- **Upstream / prior art:** Liang et al., *Patterns* 2023 — 61% of human TOEFL essays flagged as AI ([arXiv:2304.02819](https://arxiv.org/abs/2304.02819)). SETEC already cites this in its "Why no verdict" posture and has a `language_status` manifest field + ESL ratchet in `manifest_validator.py`.
- **License decision:** N/A (code + operator-sourced fixtures). Operator supplies or sources L2/translated text under appropriate terms.

## Motivation

The brief's most *defensible-yet-underbuilt* surface. SETEC preaches the ESL
false-positive problem and validates the manifest's `language_status`, but the
validation harness does not yet **report a separate FPR slice for ESL/L2 entries**.
Aggregating native and non-native FPR into one number masks the exact failure the
field is most embarrassed by. This operationalizes an honesty claim SETEC already
makes — dependency-free, near-term.

## Method

1. Read `language_status` (native / non_native_advanced / non_native_intermediate /
   learner / unknown) already validated on manifest entries.
2. In `validation_harness.py`, compute FPR/TPR/ROC **sliced by language_status** in
   addition to the existing surface × register × length × AI-status slices.
3. Refuse to publish an aggregate FPR that pools native + non-native; require the slice
   to be shown (mirrors the existing FPR-target discipline).
4. Add labeled L2/translated **fixtures** to the validation corpus (operator-sourced;
   small public slice for unit tests).

Extends the existing harness slicing; new code is the language-status slice + the
"don't pool" guard + fixtures.

## Contract (the testable interface)

- **task_surface:** `validation` (existing). This is an extension of
  `validation_harness.py`, not a new script.
- **CLI:** existing harness invocation gains `--slice-by language_status` (and it is on
  by default when any entry carries a non-`unknown` language status).
- **JSON envelope:** the harness report gains a `language_status_slices` block:
  per-status `{n, fpr, tpr, roc_auc, ci}`; the aggregate FPR is annotated
  "native-only" or refused if statuses are mixed without slicing.
- **Claim license:** licenses per-status performance; **refuses** evaluative or
  disciplinary use when the validation set lacks comparable language backgrounds
  (explicit "this slice is empty/underpowered" message).
- **capabilities.yaml entry:** update the existing `validation_harness` entry's
  `use_when` to mention the ESL slice; no new manifest entry (so no new readiness row,
  but the CHANGELOG line is still required by the docs-freshness gate).
- **Dependencies / footprint:** none new.

## Test contract (`.../tests/test_validation_harness_esl_slice.py`)

- `test_language_status_slices_present` — report carries per-status FPR/TPR when entries
  have language status.
- `test_refuses_pooled_fpr` — mixing native + non-native without slicing triggers the
  "don't pool" guard (no single FPR emitted).
- `test_empty_slice_caveat` — a status with zero entries yields an explicit
  underpowered/refusal message, not a silent 0.
- `test_native_only_annotation` — aggregate FPR is labeled native-only when that's all
  that's present.
- `test_backward_compat` — manifests with no language status still produce the prior
  report shape.

## Calibration posture

This *is* validation infrastructure; it produces the FPR/TPR the rest of the framework
calibrates against. The deliverable is the honest slice, not a threshold.

## Out of scope / non-goals

- Not a multilingual stylometry engine (English-only spaCy stays); this is a fairness
  *measurement*, not cross-lingual support.
- Doesn't claim to "correct" ESL bias — it surfaces and refuses to hide it.

## Open questions

- Source of the L2/translated fixtures (operator corpus vs. a public set with usable
  terms) — gating item before the fixtures land.
- Whether to extend the same slice to `voice_validation_harness.py` in the same PR or a
  follow-up.
