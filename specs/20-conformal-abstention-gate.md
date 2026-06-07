# 20-conformal-abstention-gate

> A **split-conformal** abstention layer over any existing signal: given operator-
> supplied calibration nonconformity scores and a target score, emit a
> distribution-free, finite-sample **conformal p-value** and a **prediction set** at
> coverage 1−α. Turns "the band reads `uncalibrated`" into "abstain, with a
> guaranteed error rate at this operating point." A methodology wrapper, **not** a
> new detector — and an empty or full prediction set is a *licensed* output.

- **Status:** Ready → building (this group).
- **Tier:** Trustworthiness → "Validation upgrades" (ROADMAP → "Capability-whitespace additions (2026-06-07) → W7"). The framework abstains a lot (GI gray zone, `uncalibrated` bands, evidentiary-posture labels) and reports bootstrap CIs, but the abstention itself carries no formal coverage guarantee.
- **GPU required:** no — stdlib only (`json`, `math`, `statistics`).
- **License decision:** N/A — local code; split-conformal prediction is standard published methodology (Vovk; Angelopoulos & Bates 2023 gentle-intro).

## Motivation

SETEC's epistemic posture is principled abstention. But the abstention is currently
informal — a gray zone with hand-set bounds, or a band that reads `uncalibrated`.
**Split-conformal prediction** is the rigorous version: given a calibration set of
nonconformity scores drawn from a reference class, it produces a target p-value and
a prediction set with a *distribution-free, finite-sample* guarantee — if the target
is exchangeable with the calibration set, the probability of (wrongly) excluding the
true class is ≤ α, for **any** underlying score distribution. This gives the
framework's "I won't call it" a coverage number instead of a vibe.

**Orthogonality:** this measures nothing about prose. It is a thin statistical
wrapper any existing signal (surprisal, Binoculars ratio, voice delta, KL) can feed
to convert a raw score into a calibrated abstention. It complements — does not
replace — the bootstrap CIs already shipped (those bound a *metric*; this bounds a
*decision*).

## Method

Stdlib only, deterministic. Conformal scores are operator-supplied
**nonconformity** scores (higher = less like the reference class), read from a
newline-delimited or JSON-list file.

**One-class (default).** Calibration = nonconformity scores of the reference class
(e.g., the writer's pre-AI baseline). For a target score `s` and `n` calibration
scores, the conformal p-value (higher-is-more-nonconforming) is
`p = (1 + #{cal ≥ s}) / (n + 1)`. If `p > α`, the target is **in** the reference
prediction set (cannot reject membership at coverage 1−α); if `p ≤ α`, the
prediction set is empty (target is out-of-distribution at level α). `--direction`
selects `higher_is_nonconforming` (default), `lower_is_nonconforming`, or
`two_sided` (signed-distance-from-median nonconformity).

**Two-class (optional `--calibration-positive`).** Compute a conformal p-value
against each class's calibration scores; the prediction set is
`{class : p_class > α}`. **Both an empty set and a both-classes set are legitimate,
licensed outputs** — the empty set says "unlike either reference at this α," the
full set says "consistent with both; the signal can't separate them here." That is
the rigor point and the anti-verdict guard, made concrete.

No model, no text input, no banding beyond the conformal set itself.

## Contract (the testable interface)

- **task_surface:** existing `validation` (no new surface).
- **CLI:** `python3 plugins/setec-voiceprint/scripts/conformal_gate.py --calibration FILE --score VALUE [--calibration-positive FILE] [--alpha 0.1] [--direction higher_is_nonconforming|lower_is_nonconforming|two_sided] [--reference-label reference] [--positive-label positive] [--json] [--out PATH]`.
- **JSON envelope:** `build_output(task_surface="validation", …)`, `target_path` =
  the calibration file, `target_words` = 0 (non-prose). `results` keys: `mode`
  (`one_class`/`two_class`), `alpha`, `coverage` (1−α), `target_score`,
  `direction`, `n_calibration` (per class in two-class), `p_value` (one-class) or
  `p_values` (two-class, per label), `prediction_set` (list of labels),
  `in_reference_set` (bool, one-class). Carries a `ClaimLicense`.
- **Claim license:** *licenses* "a split-conformal p-value and prediction set for a
  target nonconformity score against operator-supplied calibration scores, with a
  distribution-free finite-sample coverage guarantee at the chosen α"; *refuses* an
  AI/human verdict — validity is inherited from the operator's calibration set and
  nonconformity score, an empty or full prediction set is a licensed abstention (not
  a failure), and the guarantee is marginal and assumes exchangeability of
  calibration and target. Caveats: exchangeability assumption; calibration set must
  be representative; p-value is **not** P(AI); one-class flags out-of-distribution,
  not authorship.
- **capabilities.yaml entry:** `id: conformal_gate`, `surface: validation`,
  `status: heuristic`, `handoff: experimental`, `consumers: []`,
  `family: validation`, `compute: {tier: core}`, `dependencies.python: []`,
  `inputs.required: ["--calibration nonconformity scores for the reference class", "--score the target nonconformity score"]`.
- **Availability:** empty calibration file → `available=False` + warning.

## Test contract (`plugins/setec-voiceprint/scripts/tests/test_conformal_gate.py`)

- `test_task_surface_is_validation` — `TASK_SURFACE == "validation"`.
- `test_pvalue_formula` — known calibration + score → exact `(1+#{cal≥s})/(n+1)`.
- `test_coverage_guarantee_empirical` — over many seeded draws from the reference,
  the empirical rejection rate at α is ≤ α + tolerance (the finite-sample property).
- `test_in_reference_when_typical` — a mid-distribution score → `p > α`, `in_reference_set` True, non-empty set.
- `test_out_of_reference_when_extreme` — a far-tail score → `p ≤ α`, empty prediction set.
- `test_direction_lower` — `lower_is_nonconforming` flips the inequality correctly.
- `test_two_class_both_and_empty` — two-class mode can yield a both-labels set and an empty set; both are valid outputs.
- `test_alpha_monotonicity` — larger α never enlarges the prediction set.
- `test_claim_license_refuses_ai_verdict` — `does_not_license` names "AI"/"verdict" and "exchangeab".
- `test_empty_calibration_unavailable` — empty calibration → `available=False`.
- `test_deterministic` — same inputs → identical p-value.

## Calibration posture

This *is* the calibration primitive — it ships uncalibrated by definition (the
operator brings the calibration scores). What it guarantees is conditional on the
operator's calibration set being exchangeable with the target; the claim-license
states that assumption. No threshold is baked in; α is operator-chosen and defaults
to a conservative 0.1.

## Out of scope / non-goals

- Not a detector and not a score producer — it consumes scores another surface emits.
- Not Mondrian / class-conditional / adaptive conformal in v1 (named follow-ons).
- Does not assert P(AI); the p-value is a conformal membership statistic only.

## Open questions

- Whether to wire it directly into `binoculars_audit` / `surprisal_audit` as an
  opt-in `--conformal-calibration FILE` flag, so a surface can emit a conformal
  abstention inline (a follow-on once the standalone gate is reviewed).
- Whether to add Mondrian (per-register) conformal once register-sliced calibration
  sets exist.
