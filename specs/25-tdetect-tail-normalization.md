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

**The deliverable is the tail-aware p-value, not 𝒟ₜ.** Because ν is fixed at 5, `𝒟ₜ =
curvature_score / √(5/3) = curvature_score / 1.291` is a **constant monotonic rescale** — `curvature_t`
ALONE adds **zero** discrimination (identical ranking/AUC to the z-score). ALL the robustness is in the
**comparison against the heavy-tailed t-null**:

  `p_value_t = scipy.stats.t.sf(𝒟ₜ, df=ν)`   (one-sided; higher curvature → smaller p_value_t)

Since t(ν) has heavier tails than N(0,1), the same raw deviation yields a **less extreme** p-value than
the Gaussian survival `norm.sf(curvature_score)` — so adversarial leptokurtic text produces fewer false
positives. `curvature_t` is reported only as the by-product the p-value is computed from. The paper
sets **ν = 5** (robust over ν ∈ {3..7}); `--t-df` exposes it (`ν > 2` required — `ν/(ν−2)` is undefined
at 2, negative below).

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
  "student-t"`, `t_df`, `curvature_t` (= 𝒟ₜ, the by-product), and **`p_value_t`** (the t(ν) survival
  value — the actual deliverable). `curvature_score` (the raw z-score) is **always** present, both modes
  (it is the existing key; do not rename it).
- **Claim license:** extend the existing block — under student-t, "the Fast-DetectGPT curvature compared
  against a Student-t(ν) heavy-tailed null (T-Detect), more robust to adversarial/paraphrased text."
  **`does_not_license` must name `p_value_t` explicitly:** "a significance under a heavy-tailed t-null —
  NOT a probability the text is AI, and not a threshold." (As today: no AI/human verdict; bands
  operator-side / PROVISIONAL.)
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
- **the deliverable = tail-aware p-value** — `p_value_t == scipy.stats.t.sf(curvature_t, ν)`, strictly
  decreasing in `curvature_t`, in (0,1).
- **heavier tails (CORE property), exact pair** — `p_value_t = t.sf(curvature_t, ν)` is **greater than**
  the Gaussian survival of the **shipped statistic** `norm.sf(curvature_score)`. Numeric fixture:
  `curvature_score = 5`, ν=5 → `p_value_t ≈ 5.9e-3` > `norm.sf(5) ≈ 2.9e-7`.
- **ν guard** — `--t-df 2` (or ≤ 2) rejected at validation with a clear error; ν ∈ {3,4,5,6,7} all run.
- **claim-license refuses-verdict + p_value_t caveat** — no `is_ai`/`verdict` key; AND
  `does_not_license` contains the explicit "NOT a probability the text is AI" string for `p_value_t`.

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
2. ~~**`p_value_t` or just `curvature_t`?**~~ **Resolved: `p_value_t` is the deliverable** — `curvature_t`
   alone is a constant rescale (zero discrimination), so it ships only as the by-product `p_value_t` is
   computed from. The p-value is a deliberate (small) posture step on a surface that ships none today,
   gated by the explicit "NOT a probability of AI" caveat + its test.
