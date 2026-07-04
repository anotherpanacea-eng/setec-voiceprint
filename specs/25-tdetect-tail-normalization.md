# 25-tdetect-tail-normalization

> An opt-in **Student-t tail-aware normalization** for `fast_detect_curvature` — T-Detect — that
> standardizes the Fast-DetectGPT curvature against a heavy-tailed t-distribution instead of a
> Gaussian, hardening the discrimination signal against adversarial / paraphrased text.

- **Status:** Shipped — adversarially reviewed 2026-06-19 (verdict NEEDS-REWORK → reworked). Fixes: real
  key names (`curvature_score`, `actual/reference_*_sum_nats`; identity now holds against the code);
  **design leads with `p_value_t`** (curvature_t is a constant rescale, discrimination-inert — the
  t-null comparison is the whole value); the heavier-tails invariant pinned to an exact numeric pair;
  the `p_value_t` "NOT P(AI)" caveat + test; byte-identity default-preserving test; golden = modified
  entry (count stays 90), no glossary entry exists to edit. Shipped via **PR #228**
  (`fast_detect_curvature.py --tail student-t` mode); during that review the `p_value_t` survival
  value was removed as statistically unsupported, so the T-Detect score `curvature_t` ships as the
  deliverable — superseding the "p_value_t is the deliverable" language recorded above.
- **Tier:** near-term (an additive, **default-preserving** flag on a shipped surface; the new math is
  pure stdlib/scipy over the backend's existing output — no change to sampling)
- **GPU required:** no (the t-normalization runs over the backend's `d(x)` / `V(x)`; same torch-gated
  sampling as today, unchanged)
- **Upstream / prior art:**
  - **T-Detect** — *Tail-Aware Statistical Normalization for Robust Detection of Adversarial
    Machine-Generated Text* ([arXiv:2507.23577](https://arxiv.org/abs/2507.23577)).
  - Builds on `fast_detect_curvature` (Fast-DetectGPT, Bao et al. 2024; `specs/03`).
- **License decision:** **clean-room the method** (a one-line normalization change + a Student-t
  survival value). No weights.

## Motivation

`fast_detect_curvature` ships the Fast-DetectGPT conditional curvature as a **Gaussian z-score**:
`curvature = d(x) / √V(x)`, with `d(x) = Σ_t (lp_t − μ_t)` and `V(x) = Σ_t var_t`. T-Detect's finding:
adversarial / paraphrased machine text is **leptokurtic** (heavy-tailed), so the Gaussian assumption
under-states tail probability → the z-score mis-bands exactly the adversarial cases the detector most
needs to catch. T-Detect's fix is a **tail-aware normalization** against a Student-t distribution.

This is the SHORT-LIST's top "cheap, high-ROI" pick: it hardens a shipped signal against its known
failure mode, riding compute SETEC already runs, with no change to the default behavior.

## Method (exact, per arXiv:2507.23577)

**Only the normalization changes** — sampling and per-token discrepancy are identical to
Fast-DetectGPT. In the existing code the curvature is `curvature_score = (actual_log_prob_sum_nats −
reference_mean_sum_nats) / √(reference_variance_sum_nats2)` — i.e. `d(x) / √V(x)` with
`d(x) = actual_sum − μ_sum` and `V(x) = var_sum`. T-Detect standardizes against a Student-t(ν):

  𝒟ₜ = d(x) / √[ (ν / (ν − 2)) · V(x) ]   =   curvature_score / √(ν / (ν − 2))

**Shipped deliverable (PR #228 revision): the T-Detect score `curvature_t` (= 𝒟ₜ); `p_value_t` is NOT
emitted.** The original design below led with a tail-aware p-value `p_value_t = scipy.stats.t.sf(𝒟ₜ, df=ν)`.
PR #228 review **removed it as statistically unsupported**: 𝒟ₜ = d/√[(ν/(ν−2))·V] is, like the
Fast-DetectGPT z-score, an asymptotically **Gaussian** sum-statistic (≈ N(0, (ν−2)/ν)), **not** a
Student-t(ν) variate — so `t.sf(𝒟ₜ, ν)` is not uniform under the null, and naming it `p_value_t` would
over-claim a calibrated probability the transform does not support. **Do not re-add `p_value_t`.** What
ships is `curvature_t` = 𝒟ₜ = `curvature_score / √(ν/(ν−2))`; with ν = 5 that is `curvature_score / 1.291`.
The paper sets **ν = 5** (robust over ν ∈ {3..7}); `--t-df` exposes it (`ν > 2` required — `ν/(ν−2)` is
undefined at 2, negative below).

**Known limitation (open follow-up, see Open questions #2).** Because ν is fixed, `curvature_t` is a
**constant monotone rescale** of `curvature_score` — so, alone, it adds **zero** discrimination (identical
ranking/AUC to the z-score). The tail-aware *comparison* that is T-Detect's actual contribution is
therefore **not** realized by the shipped `curvature_t` scalar. Exposing that comparison as a
**non-probability heuristic tail coordinate** (uncalibrated, explicitly NOT P(AI)) — rather than either the
unsupported `p_value_t` or the inert `curvature_t` — is an open design question deferred to the maintainer.

For SETEC this is an opt-in `--tail student-t` mode. The default (`--tail gaussian`) is **byte-for-byte
the current output** — the new keys are added strictly inside the `tail == "student-t"` branch (and
nothing is added to `comparison_set` etc. in the gaussian path).

## Contract (the testable interface)

- **task_surface:** **unchanged — `discrimination_curvature`** (this modifies the existing
  `fast_detect_curvature` surface; no new surface, no new capability id, no `claim_license_surfaces/`
  label, **no surface-labels golden bump**).
- **CLI (additive):** `--tail {gaussian,student-t}` (default `gaussian`) and `--t-df N` (default 5;
  rejected at validation time if `N <= 2`, with a clear error — `ν/(ν−2)` is undefined at 2, negative
  below). All existing flags unchanged.
- **JSON envelope:** the default mode's `results` are **byte-for-byte unchanged**. Under
  `--tail student-t`, `results` additionally carries (inside the student-t branch only): `tail:
  "student-t"`, `t_df`, and `curvature_t` (= 𝒟ₜ, the shipped deliverable). **`p_value_t` is NOT emitted**
  (removed in PR #228 review as unsupported — see Method). As shipped, `tail`/`t_df` are emitted whenever
  student-t mode runs and `curvature_t` when the reference variance is non-degenerate. `curvature_score`
  (the raw z-score) is **always** present, both modes (it is the existing key; do not rename it).
- **Claim license:** extend the existing block — under student-t, "the Fast-DetectGPT curvature
  re-standardized against a Student-t(ν) heavy-tailed scale (T-Detect)." As shipped, `does_not_license`
  names **`curvature_t`** (not `p_value_t`): it is a constant rescale of `curvature_score` (same ranking),
  **NOT** a calibrated probability, **NOT** P(AI), and **NOT** a threshold — the operator supplies any
  band. (As today: no AI/human verdict; bands operator-side / PROVISIONAL.)
- **capabilities.d:** update `fast_detect_curvature.yaml` (a `--tail student-t` example + a one-line
  note). `test_capabilities_dropin` compares the manifest **by parsed id-dict** (not bytes), so what
  matters is the golden's `fast_detect_curvature` entry reflecting the edited fragment — **regen
  `_golden_capabilities.json`'s modified entry to match** (match the file's existing serialization for a
  clean diff; formatting is cosmetic to the test). **Count is unchanged (90) — the entry is MODIFIED,
  not added; no surface-labels golden** (`discrimination_curvature` is already registered).
- **Paper trail:** the fragment edit + a `changelog.d/` fragment (citing arXiv:2507.23577) +
  `gen_calibration_readiness` refresh. (No glossary edit — there is no `fast_detect`/curvature entry in
  `signals-glossary.md` to annotate.) Run drift / docs-freshness / `pytest test_capabilities_dropin`
  before push.
- **Dependencies:** `scipy` (already a SETEC dep) for `stats.t.sf`.

## Test contract (names + invariants)

`plugins/setec-voiceprint/scripts/tests/` (extend the existing curvature test or a new
`test_tdetect_normalization.py`) — all **stdlib/scipy over a stub backend or injected `d`/`V`**, no torch:

- **default-preserving (byte-identity)** — diff the FULL gaussian-mode envelope before vs. after the
  change (same stub backend/seed): identical, and the gaussian `results` carry **no** `curvature_t`/
  `p_value_t`/`tail`/`t_df`. The existing 13 curvature tests still pass unchanged.
- **exact formula** — for injected `d`/`V` (`actual_sum`, `μ_sum`, `var_sum`): `curvature_t ==
  (actual_sum − μ_sum) / sqrt((ν/(ν−2))·var_sum)` AND `curvature_t == curvature_score / sqrt(ν/(ν−2))`
  (ν=5 → divide by √(5/3) ≈ 1.291).
- **the shipped deliverable = `curvature_t`** — `curvature_t == (actual_sum − μ_sum) /
  sqrt((ν/(ν−2))·var_sum)` AND `curvature_t == curvature_score / sqrt(ν/(ν−2))` (ν=5 → divide by
  √(5/3) ≈ 1.291). **No `p_value_t` is emitted or asserted** (the earlier "deliverable = tail-aware
  p-value" / heavier-tails-`p_value_t`-pair tests are superseded by PR #228 as unsupported).
- **ν guard** — `--t-df 2` (or ≤ 2) rejected at validation with a clear error; and (as shipped) rejected
  in `audit()` on **every** student-t input incl. degenerate variance; ν ∈ {3,4,5,6,7} all run.
- **mode marker on degenerate variance** — a degenerate-variance student-t run still emits `tail`/`t_df`
  (with `curvature_t` absent), so the mode is distinguishable from gaussian.
- **claim-license refuses-verdict + `curvature_t` caveat** — no `is_ai`/`verdict` key; AND
  `does_not_license` names `curvature_t` as "NOT a probability the text is AI" and a constant rescale.

## Calibration posture

Ships **PROVISIONAL / heuristic**, opt-in. The default Gaussian path is unchanged (its existing status
stands). A labeled adversarial corpus would calibrate the student-t bands later → `empirically_oriented`
with a PROVENANCE entry. No verdict in either mode.

## Out of scope / non-goals

- No change to the sampling or per-token discrepancy (only the normalization). The student-t mode is
  **off by default** — the published Gaussian curvature stays the default output (no contract change).
  No AI/human verdict. Not a new surface.

## Open questions

1. **ν default** — 5 (the paper's value, robust over 3..7); expose `--t-df`. Maintainer may pick another.
2. **`p_value_t`, inert `curvature_t`, or a renamed heuristic tail coordinate? (OPEN — reversed in PR #228.)**
   The earlier "resolved: `p_value_t` is the deliverable" was **reversed at build/review**: `p_value_t` is
   statistically unsupported (𝒟ₜ is asymptotically Gaussian, not t(ν)) and was removed; the shipped
   `curvature_t` is a constant rescale of `curvature_score` and so is discrimination-inert. Neither
   realizes T-Detect's tail-aware comparison. The open follow-up is whether to expose that comparison as a
   **non-probability heuristic tail coordinate** (e.g. `tail_significance_t` / `robust_tail_score`),
   documented uncalibrated and explicitly NOT P(AI) — a maintainer design decision, not yet made. Until
   then, `curvature_t` ships as-is and **`p_value_t` must not be re-added**.
