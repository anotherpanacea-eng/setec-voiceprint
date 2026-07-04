# 32-diveye-surprisal-diversity

> Surprisal-DIVERSITY feature aggregation (DivEye, arXiv:2509.18880): the four
> missing temporal/distribution-shape signals (delta / acceleration / histogram
> entropy / ACF-of-acceleration) over a per-token surprisal series, fused with
> the five already-shipped surprisal moments into a 9-signal DivEye vector.

- **Status:** Shipped (M1) — merged via **PR #258** (`diveye_signals.py`; model-free signal
  aggregation + CI tests). M2 (the registered `diveye_audit.py` discrimination
  surface + XGBoost calibration) is experiment-gated and NOT in this build; the M2
  real-surprisal path remains local/gated.
- **Tier:** near-term (M1, stdlib, CPU); research-grade (M2, gated).
- **GPU required:** no (M1 runs over injected/already-computed surprisal series;
  the real surprisal forward pass is the M2 seam).
- **Upstream / prior art:** arXiv:2509.18880 (Basani, BITS Goa; Chen, IBM
  Research), "DivEye". **arXiv status: PROVISIONAL on this checkout** — the spec
  header sourced the MAGE-OOD numbers (DivEye 0.86 vs Binoculars 0.71 on unseen
  models; 0.97 vs 0.80 on unseen domains) to a Code-PC dossier addendum
  (`_detection_dossier_2026-06-20/DOSSIER_ADDENDUM_2026-06-20.md §B.1`) that is
  NOT present in this Code-Mac repo. **No paper AUROC number is asserted as fact
  here — they are LEADS for the M2 experiment, not targets.** The 33%
  "zero-shot-over-supervised" framing is `[CORRECTED — WRONG]` in the source
  spec: the 33.2% gap is DivEye vs other zero-shot baselines, not zero-shot over
  supervised.
- **License decision:** clean-room the method (the nine DivEye signals are
  reimplemented from the algorithm description; no upstream weights or code are
  vendored). gpt2 is the paper's scorer for the eventual M2 forward pass.

> **Path adaptation note.** This spec was authored for the Code-PC checkout
> (`D:\Code-PC\...`). Every path below has been adapted to the Code-Mac live
> checkout at `/Users/anotherpanacea/Documents/Code-Mac/setec-voiceprint`. The
> Code-PC-only experiment scaffolds (`_diveye_smoke.py`, `_diveye_empirical.py`,
> `_litprose_frontier_2026-06-20`, `_raid_tier4`) are M0/M2 local artifacts and
> are NOT created in this repo by the M1 build.

## Motivation

After the 2026-06-20 MAGE label fix, the source spec reports the existing SETEC
arsenal on cross-generator OOD as: Binoculars ~0.71 (MAGE Testbed-5 unseen
models), DivEye ~0.86, gpt2 surprisal mean/sd/acf1 ~0.77/0.73. **All such
numbers are PROVISIONAL leads** (the dossier they cite is not on this checkout).
The motivating *hypothesis*: on cross-generator OOD, the surprisal-diversity
feature vector may outperform the cross-perplexity ratio, because the diversity
features answer a different question ("how uniform is this text's surprisal
profile under a single model?") than Binoculars' cross-perplexity ratio.

**Orthogonality.** The de-duplication audit (below) confirms `surprisal_audit.py`
already ships DivEye features F1–F4 (mean/var/skew/kurtosis) and F9 (ACF lag-1).
The genuinely new signals are F5–F8: the 1st-order difference (delta) series, the
2nd-order difference (acceleration) series, the Shannon entropy of the surprisal
histogram, and the lag-1 ACF of the acceleration series. This spec adds the four
missing signals + an aggregation that assembles the full 9-signal DivEye vector,
**reusing** `surprisal_audit.py`'s already-tested moment helpers rather than
duplicating the math.

### De-duplication statement (load-bearing)

Existing code audited in the Code-Mac checkout:

- `scripts/surprisal_audit.py` — per-token surprisal series + mean / variance /
  skew / excess-kurtosis / ACF (lags 1,2,3,5,10) / sliding-window. **Covers F1–F4
  and F9.** `TASK_SURFACE = "smoothing_diagnosis"`. Exposes the reusable helpers
  `_mean`, `_sample_variance`, `_sample_sd`, `_acf_at_lag`, `_skew`,
  `_excess_kurtosis`, and `MIN_SERIES_FOR_ACF = 30` (verified on this checkout).
- `scripts/variance_audit.py` — Tier-4 glass-box (stylometry + surprisal
  embedding). Emits mean/sd/acf1. No temporal-difference or distribution-entropy
  signals.
- `scripts/fast_detect_curvature.py` — Fast-DetectGPT conditional curvature
  (probability-space z-score). Its docstring already names "DivEye-style per-token
  surprisal moments" as a distinct, orthogonal method. No DivEye diversity
  signals. Confirmed orthogonal.
- `scripts/binoculars_audit.py` — two-model cross-perplexity ratio
  (`TASK_SURFACE = "binoculars_discrimination"`). No per-token diversity features.
- `scripts/rank_turbulence_audit.py` (REVIEW N1) — Rank-Turbulence Divergence
  (Dodds et al. 2020) over **function-word frequency distributions** between a
  target and a baseline corpus. `TASK_SURFACE = "voice_coherence"`. No per-token
  surprisal, no temporal-difference signals, no entropy of a surprisal histogram.
  **Confirmed orthogonal.**
- `specs/30-specdetect-lastde.md` / `specs/31-tocsin-token-cohesiveness.md` —
  roadmap placeholders; no shipped `.py`/`.yaml`. Lastde (sliding-window spectral
  entropy) is a third, complementary family; `delta_series` here is reusable as
  its first-order-difference input.

| DivEye feature | Status in codebase |
|---|---|
| F1 — mean surprisal | shipped (`surprisal_audit.py`) |
| F2 — variance surprisal | shipped |
| F3 — skew surprisal | shipped |
| F4 — excess-kurtosis surprisal | shipped |
| F5 — 1st-order temporal differences (delta) | **NEW** |
| F6 — 2nd-order temporal differences (acceleration) | **NEW** |
| F7 — Shannon entropy of surprisal histogram | **NEW** |
| F8 — ACF of the acceleration series (lag-1) | **NEW** |
| F9 — ACF of surprisal series (lag-1) | shipped |

## Method

A pure-Python, stdlib-only module `scripts/diveye_signals.py`. No torch /
transformers / scipy / sklearn / xgboost import at module level. `TASK_SURFACE =
None` — it is a math helper, not a registered surface, so it requires no
capabilities.d entry, no golden fragment, and no claim-license file (the drift
linter only requires registration for scripts that declare a string
`TASK_SURFACE`; verified against `tools/check_capabilities_drift.py`).

Functions operate on a `list[float]` per-token surprisal series (the output of
`SurprisalBackend.score_text` in M2; an injected stub series in M1):

- `delta_series(surprisal) -> list[float]` — `d[t] = s[t] - s[t-1]`, t=1..N-1.
  Length N-1, `[]` for N < 2.
- `accel_series(delta) -> list[float]` — `a[t] = d[t] - d[t-1]`, on the delta
  output. Length N-2 for the original series, `[]` for delta length < 2.
- `surprisal_histogram_entropy(surprisal, n_bins=50) -> float | None` — Shannon
  entropy (base 2, bits) of the equal-width histogram over
  `[min(surprisal), max(surprisal)]`. `None` for a constant series (zero range)
  or N < 2. Zero-count bins contribute 0 (`0 * log2(0) = 0`).
- `aggregate_diveye_signals(surprisal, *, n_entropy_bins=50, min_acf_length=30)
  -> dict[str, float | None]` — assembles the 9-signal DivEye vector, reusing
  `surprisal_audit`'s moment helpers via a **lazy** import (inside the function
  body, with a self-contained stdlib fallback if the import is unavailable).
  `min_acf_length=30` matches `surprisal_audit.MIN_SERIES_FOR_ACF` (REVIEW §1.2).

### Output naming and the output_schema range-check trap (REVIEW C1 — CRITICAL)

`output_schema._SURPRISAL_RE` matches the whole-token set
`{surprisal, perplexity, entropy, cross_entropy, nll}` and `validate_results_bounds`
(invoked by `build_output(validate_bounds=True)`) enforces `>= 0` on matched keys
unless a transform guard (`log|ln|logit|ratio|sum|delta|diff`) or a
standardization guard (`z|score|...`) also matches. **Three DivEye signals are
legitimately signed:** skew (left-skewed → negative), excess kurtosis
(platykurtic → negative), and any lag-1 ACF (anti-correlated → negative). A flat
key like `surprisal_skew` / `surprisal_kurtosis` / `surprisal_acf1` would trip
`_SURPRISAL_RE` and raise `OutputValidityError` on ordinary human-prose windows —
a ship-blocker for the M2 registered surface.

**Adopted fix (REVIEW Option A — mirror `surprisal_audit.py`).** The signed
moments are emitted under un-prefixed keys that do NOT carry a surprisal-family
token: `skew`, `excess_kurtosis`, `surprisal_acf1` is renamed to `acf1`, and the
acceleration ACF to `accel_acf1`. The `surprisal_entropy` key — which IS a real
DivEye signal name and DOES carry the `entropy` token — is therefore the one that
*would* be range-checked, but Shannon entropy of a histogram is always `>= 0`, so
it is safe under the gate. The aggregate's returned keys are chosen so that **the
entire returned dict passes `validate_results_bounds` for any finite input,
including negative skew/kurtosis/ACF.** This is pinned by a test
(`test_aggregate_output_passes_output_schema_bounds`) that feeds a negative-ACF /
negative-skew aggregate through `validate_results_bounds` and asserts no raise.

Returned keys (the 11-key vector — REVIEW M1 folds `accel_sd` in so the named
discriminative features are all present):

| key | DivEye feature | signed? | range-safe under gate? |
|---|---|---|---|
| `surprisal_mean` | F1 | no (≥0 bits) | yes (matched, ≥0 holds) |
| `surprisal_var` | F2 | no (≥0) | yes |
| `skew` | F3 | **yes** | yes (un-prefixed, unmatched) |
| `excess_kurtosis` | F4 | **yes** | yes (unmatched) |
| `delta_mean` | F5 | yes (~0) | yes (`delta` transform-guarded) |
| `delta_sd` | F5b | no (≥0) | yes (`delta` transform-guarded) |
| `accel_mean` | F6 | yes (~0) | yes (un-prefixed `accel*` unmatched) |
| `accel_sd` | F6b | no (≥0) | yes (unmatched) |
| `surprisal_entropy` | F7 | no (≥0 bits) | yes (matched, ≥0 holds) |
| `accel_acf1` | F8 | **yes** | yes (unmatched) |
| `acf1` | F9 | **yes** | yes (un-prefixed, unmatched) |

**Named discriminative feature (REVIEW M1 / build note).** `delta_mean` and
`accel_mean` are ~0 by the telescoping-sum argument and are NOT standalone
discriminators. The discriminative members of those pairs are `delta_sd`,
`accel_sd`, and `accel_acf1`; the key DivEye signal is `surprisal_entropy`
(machine-lower hypothesis). All are present in the returned vector.

## Sign / direction (pinned in a test — the family's shared failure mode)

Silent inversion is the surprisal-detector family's shared failure mode, so the
hypothesized directions are pinned by `test_aggregate_diveye_signals_direction`:
for an "AI-like" narrow-band series vs a "human-like" wide-band series,
`surprisal_var`, `surprisal_entropy`, and `delta_sd` are all LOWER for the AI-like
series. **These directions are hypotheses, not verdicts** — they pin the SIGN of
the measured property, not an authorship inference. (An ESL / restricted-register
human passage can look identical to the AI-like fixture under `surprisal_entropy`;
the unit test deliberately includes no ESL fixture — see REVIEW §3 — because the
signal alone cannot separate them. That failure mode is documented in the test
docstring and belongs in the M2 claim-license `does_not_license` block.)

## Posture (no-verdict / keep-the-human / anti-Goodhart)

- The M1 module emits no `verdict` / `is_ai` / `is_human` / `band` /
  `calibration_status` key and imports nothing from `{fitness, calibration,
  binoculars_audit, validation_harness, setec_signals, loop}`. It is descriptive
  surprisal-diversity evidence, never a selection/inference target.
- M2 (registered surface) ships `calibration_status: heuristic` permanently until
  a NEW held-out corpus (not lit-horror / RAID / MAGE) supplies operating points
  with honest FPR@target + ESL-population validation; no verdict band without an
  operator-supplied calibration; LOGO is mandatory; the lit-horror / RAID / MAGE
  corpora are DEVELOPMENT, not held-out; DivEye features are never the held-out
  SETEC fitness signal.
- ESL / non-native / restricted-register false-positive risk and the
  generator-strength inversion risk are documented for the M2 claim-license block.

## Test contract (M1)

File: `plugins/setec-voiceprint/scripts/tests/test_diveye_signals.py`. Stdlib,
deterministic, no model, no torch.

1. `test_delta_series_basic` — `[1,2,1.5,3,2.5]` → `[1,-0.5,1.5,-0.5]` (len N-1).
2. `test_delta_series_edge_cases` — `[]`→`[]`; len-1→`[]`; len-2→len-1.
3. `test_accel_series_basic` — from the delta above → `[-1.5,2,-2]` (len N-2).
4. `test_accel_series_edge_cases` — delta len-1→`[]`; delta len-2→len-1.
5. `test_surprisal_entropy_uniform` — constant→`None`; spread series → entropy
   near `log2(n_bins)`; monotone-increasing as the distribution spreads.
6. `test_surprisal_entropy_short` — len-1→`None`; empty→`None`.
7. `test_aggregate_diveye_signals_basic` — 11 keys present; `delta_mean` ==
   mean of `delta_series`; `accel_mean` == mean of `accel_series`; entropy finite.
8. `test_aggregate_diveye_signals_direction` — pins the sign (above).
9. `test_import_is_stdlib` — subprocess import pulls no
   torch/transformers/scipy/sklearn/xgboost.
10. `test_aggregate_output_passes_output_schema_bounds` (REVIEW C1) — an aggregate
    with negative skew / negative ACF passes `validate_results_bounds` with no raise.
11. `test_separation_guard` — module imports none of the forbidden modules and
    exposes no verdict/band/calibration_status symbol.
12. ACF None-below-`min_acf_length` and short-series degeneracy.

## Calibration posture

M1 ships no bands, no thresholds, no `calibration_status`. M2's registered surface
ships `calibration_status: heuristic` until a new held-out corpus is calibrated
(per the anti-Goodhart section above). The default is never a verdict.

## Out of scope / non-goals (this M1 build)

- No `diveye_audit.py` registered surface, no `build_output` wiring, no XGBoost /
  logistic classifier, no `capabilities.d` / golden / claim-license registration
  (all M2; gated on the M0 lit-horror direction check + M2-Tier-1 empirics).
- No real surprisal forward pass; no GPU; no model load.
- The Code-PC M0/M2 experiment scaffolds are not recreated in this repo.

## Open questions (carried to M2)

- Does the MAGE-OOD advantage transfer to the lit-horror frontier, or is it
  BLOOM-7B-specific (the central M0 gate)?
- Does the XGBoost path survive LOGO, or does it collapse like the 13-signal
  glass-box logistic (AUC 0.539 on Claude)? If it collapses, ship only the
  standalone direction-stable feature columns.
