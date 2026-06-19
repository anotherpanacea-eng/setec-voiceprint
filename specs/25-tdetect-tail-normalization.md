# 25-tdetect-tail-normalization

> An opt-in **Student-t tail-aware normalization** for `fast_detect_curvature` — T-Detect — that
> standardizes the Fast-DetectGPT curvature against a heavy-tailed t-distribution instead of a
> Gaussian, hardening the discrimination signal against adversarial / paraphrased text.

- **Status:** Draft
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

**Only the normalization changes** — the sampling and per-token discrepancy are identical to
Fast-DetectGPT. T-Detect standardizes the curvature against a Student-t(ν):

  𝒟ₜ(x; ν) = d(x) / √[ (ν / (ν − 2)) · V(x) ]   =   curvature / √(ν / (ν − 2))

where `ν / (ν − 2)` is the variance of a standard Student-t(ν), so 𝒟ₜ is the curvature rescaled to
unit variance **under the heavy-tailed t-null**. The paper sets **ν = 5** (fixed; reported robust over
ν ∈ {3..7}). The tail-aware significance is the Student-t survival value
`p_t = scipy.stats.t.sf(𝒟ₜ, df=ν)` (one-sided; a higher curvature → smaller `p_t`), which — because
t(ν) has heavier tails than N(0,1) — is **less extreme for the same raw deviation**, so adversarial
leptokurtic text yields fewer false positives.

For SETEC this is an opt-in `--tail student-t` mode. The default (`--tail gaussian`) is **byte-for-byte
the current output** (the Gaussian `curvature`, no new keys).

## Contract (the testable interface)

- **task_surface:** **unchanged — `discrimination_curvature`** (this modifies the existing
  `fast_detect_curvature` surface; no new surface, no new capability id, no `claim_license_surfaces/`
  label, **no surface-labels golden bump**).
- **CLI (additive):** `--tail {gaussian,student-t}` (default `gaussian`) and `--t-df N` (default 5,
  `> 2`). All existing flags unchanged.
- **JSON envelope:** the default mode's `results` are **unchanged**. Under `--tail student-t`, `results`
  additionally carries: `tail: "student-t"`, `t_df`, `curvature_t` (= 𝒟ₜ), and `p_value_t` (the t(ν)
  survival value, informational). `curvature` (the raw z-score) is **always** present, both modes.
- **Claim license:** extend the existing block — under student-t, "the curvature standardized against a
  Student-t(ν) heavy-tailed null (T-Detect), more robust to adversarial/paraphrased text." **Refuses**
  (as today): any AI/human verdict; `p_value_t` is a significance value under the t-null, **not** a
  probability the text is AI, and thresholds stay operator-side / PROVISIONAL.
- **capabilities.d:** update `fast_detect_curvature.yaml` (a `--tail student-t` example + a one-line
  note) → **capabilities-golden bump** (the single modified entry; regen `_golden_capabilities.json`
  with `json.dumps(..., indent=2)` no sort_keys, no count change — the entry is modified, not added).
- **Paper trail:** the fragment edit + a `changelog.d/` fragment (citing arXiv:2507.23577) + a
  `references/signals-glossary.md` note on the curvature entry + `gen_calibration_readiness` refresh.
  No new signal id → likely no count change; run drift / docs-freshness / `pytest test_capabilities_dropin`
  before push.
- **Dependencies:** `scipy` (already a SETEC dep) for `stats.t.sf`.

## Test contract (names + invariants)

`plugins/setec-voiceprint/scripts/tests/` (extend the existing curvature test or a new
`test_tdetect_normalization.py`) — all **stdlib/scipy over a stub backend or injected `d`/`V`**, no torch:

- **default-preserving** — `--tail gaussian` (and no `--tail`) output is identical to the pre-change
  output (the existing curvature tests still pass; the gaussian `results` carry no `curvature_t`).
- **exact formula** — for injected `d`, `V`: `curvature_t == d / sqrt((ν/(ν−2))·V)` and
  `curvature_t == curvature / sqrt(ν/(ν−2))` (e.g. ν=5 → divide by √(5/3)).
- **monotonicity** — `p_value_t` strictly decreases as `curvature_t` increases; `p_value_t ∈ (0,1)`.
- **heavier tails than Gaussian** — for the same `curvature`, `p_value_t (t-null) > Φ-survival
  (gaussian)` (the tail-aware p-value is less extreme — the core robustness property).
- **ν guard** — `--t-df 2` (or ≤ 2) is rejected (variance `ν/(ν−2)` undefined/negative); ν=3..7 all run.
- **claim-license refuses-verdict** — no `is_ai`/`verdict` key; the caveat names `p_value_t` as not a
  probability-of-AI.

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
2. **`p_value_t` or just `curvature_t`?** Report both (the p-value is the operator-banding aid that makes
   the tail-awareness legible); flag if the maintainer prefers the bare statistic only (the existing
   surface ships no p-value, so this is a small posture step — keep it clearly "not a probability of AI").
