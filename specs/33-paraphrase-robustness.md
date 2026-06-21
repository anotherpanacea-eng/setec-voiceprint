# SPEC 33 — Adversarial-paraphrase detector-level AUC-degradation harness

> **Provisional.** Adapted for the Code-Mac checkout from the roadmap draft
> `detection-roadmap-2026/SPEC_paraphrase.md` (written for Code-PC, `D:\` paths).
> The Code-PC paths below are rewritten to this checkout and the Code-PC-only
> milestones (the litprose frontier corpus, the SN850X checkpoint drive, the
> gfx1100 / ROCm rig discipline) are quoted as the **M2 GPU operator runbook**,
> not as anything this PR ships. **This PR ships M1 only.** The REVIEW
> (`detection-roadmap-2026/REVIEW_paraphrase.md`, GO-with-changes) is folded;
> every change-request is marked `[REVIEW-n folded]` at the point it lands.

**Roadmap delta:** detection-roadmap-2026 §D-E (DOSSIER_ADDENDUM §D).

**Central references (M0 status carried as given; preprint numbers are LEADS, never asserted as fact):**
- Sadasivan et al., arXiv:2303.11156 "Can AI-Generated Text be Reliably Detected?" — TV-bound
  impossibility for single-document detection under paraphrase. `[VERIFIED in DOSSIER_ADDENDUM §B.2]`
  Bound form: `AUROC(D) <= 0.5 + TV(M,H) - TV(M,H)^2/2`. The "Corollary 3" reference is
  `[CORRECTED — does not exist]`; only the bound as stated is used. **[REVIEW-2 folded: M0.2 closed.]**
- Krishna et al., arXiv:2303.13408 "Paraphrasing Evades Detectors" (DIPPER) — 11B T5 paraphraser;
  the canonical neural paraphrase attack. `[VERIFIED]` DIPPER predates Binoculars (2024), so the
  DIPPER paper reports no Binoculars-specific result — its paraphrase results bind DetectGPT /
  watermark detectors. **The M2 DIPPER run is GPU-only and out of scope for this M1 PR.**
- Chakraborty et al., arXiv:2304.04736 "On the Possibilities..." — multi-sample possibility result.
  `[VERIFIED, arXiv-only]`
- Peng/Hans et al., arXiv:2401.12070 "Binoculars" — the DOSSIER cites a "~36.1% **accuracy** drop
  under **synonym substitution** on RAID" (a word-level lexical attack, NOT a DIPPER-class neural
  paraphrase). **[REVIEW-1 folded: M0.1 narrowed — the figure is accuracy, the attack is
  `synonym_swap`, and the M2 neural attack is a strictly STRONGER adversary than this headline.]**
  This number is a LEAD; it is never asserted as a measured result in any artifact this harness
  emits, and no test pins it.

**Status:** M1 build-ready (model-free orchestration over INJECTED scorer outputs). The detector
re-scoring and any GPU model load is the M2 seam, off by default and never reached in CI.

**Drafted:** 2026-06-20. **Adapted + built (M1):** 2026-06-21.

---

## 1. What this covers and what it reuses

### 1.1 De-duplication (load-bearing — re-verified against THIS checkout's `main`)

The roadmap de-dup audit was re-run against `main` of the Code-Mac checkout. The result
**differs from the spec's assumption** and is recorded here because it changes the build:

- **`paraphrase_ladder.py` / `build_proxy_ladder` / `synonym_swap` / `alternative_spelling` /
  `whitespace` / `_SADASIVAN_CEILING` live ONLY on the unmerged branch
  `feat/raid-dipper-robustness` (spec 16, PR #240). They are NOT on `main`.** The `.pyc` left in
  `scripts/calibration/__pycache__/` on `main` is a stale artifact, not a tracked module. The
  RAID transforms (`synonym_swap` et al.) are likewise raid-dipper-branch-only;
  `adversarial_fixtures.py` on `main` ships only `insert_zero_width_spaces` / `insert_soft_hyphens`
  / `apply_homoglyphs`.
- Therefore the spec's "import directly; do not reimplement `build_proxy_ladder`" instruction
  **cannot be honored on a clean-`main` build** without taking a hard dependency on un-merged
  code. Per the BUILD NOTE, this harness builds **only the genuinely-new part** and defines its
  own self-contained injectable paraphraser (see §3.3), and **says so** (this section + the PR).

- **`paraphrase_ladder` (raid-dipper branch)** measures a per-(signal × rung) **decay curve on
  individual texts** — how one signal's reading moves as one text is paraphrased again. **This
  harness** measures a per-(detector × rung) **corpus-level AUC degradation** — how a detector's
  P(machine > human) separation over a *labeled corpus* moves under a corpus-scale attack. These
  are measurements at different levels (single-text signal movement vs population-level ranking
  shift) and **do not overlap**. When raid-dipper lands, this harness's injectable paraphraser
  Protocol (§3.3) can accept `build_proxy_ladder` as one driver; nothing here re-implements its
  decay-curve output.

- **`adversarial_robustness_card`** (per-signal fixture card), **`validation_harness`** (per-signal
  ROC AUC on unmodified manifests; has an ESL slice but no paraphrase loop), **`binoculars_audit`**,
  **`fast_detect_curvature`**, **`surprisal_backend`**, **`variance_audit`** — all confirmed: none
  computes detector-level AUC *degradation after a corpus-scale paraphrase attack*. The gap is real.

**Finding (carried into the PR):** No existing script computes detector-level AUC degradation
after a corpus-scale paraphrase attack. This harness fills exactly that gap. Because the
raid-dipper proxy is unmerged, the M1 deliverable is the **orchestration + AUC/FPR/TPR/Δ logic
over injected scores**, with a self-contained injectable paraphraser interface (REVIEW-3).

### 1.2 What is genuinely new (this M1 PR)

A model-free, CI-runnable **AUC-degradation orchestrator** with:

1. An **injectable `Paraphraser` Protocol** (REVIEW-3) + a small self-contained stdlib lexical
   proxy (`StdlibProxyParaphraser`, labeled `proxy_stdlib` everywhere). The proxy is honestly
   weaker than a neural paraphraser — a flat curve under it is never read as robustness.
2. An **injectable `Scorer` Protocol** — in M1 the scorer is a stub that returns pre-injected
   scores; in M2 the GPU operator binds the real `binoculars_audit` / `surprisal_backend` /
   `fast_detect_curvature` / `variance_audit` calls. **No model is imported at module load.**
3. **Per-(detector × rung) AUC** computed by the existing WMW-U helper
   (`validation_harness.fallback_roc_auc`, reused not re-implemented), **oriented by each
   detector's known sign** so the reported AUC is the discriminative separation, with the
   sign/direction pinned in a test (§4, the scoring-divergence / silent-inversion guard, REVIEW).
4. **FPR/TPR at operating points** {0.05, 0.10} and **Δ-from-rung-0** for each cell.
5. The **scoring-divergence gate** (REVIEW): a rung-0 self-consistency assertion that the
   harness's rung-0 AUC reproduces the baseline AUC computed directly from the same scores — a
   pipeline-divergence catch before any degradation number is read.

---

## 2. Posture (detection-surface family rules — non-negotiable)

This is an **evaluation harness on the existing `validation` task surface**, a sibling of
`pan_replay` / `paraphrase_ladder`. It is registered (so `build_output` validates and CI can run
it) but it is **NOT a verdict surface**:

- **Descriptive only.** The output is a per-(detector × rung) table of MEASURED properties
  (AUC = P(machine > human); TPR at a stated FPR budget; Δ from baseline). A band/label names the
  **measured ranking separation**, never an `is_ai` / `is_human` / verdict / threshold-decision.
- **No-verdict recursive walk.** The serialized payload is walked at every depth and MUST contain
  **no aggregate robustness scalar** — `robustness_score`, `auc_retained`, `area_under_decay`,
  `is_robust`, `overall_robustness`, `n_robust_signals`, `headline` are banned at any depth
  (the `_walk` banned-key check from `pan_replay`, extended). The per-cell `auc`/`tpr`/`fpr` keys
  are the legitimate descriptive deliverable and are NOT banned — banning them would delete the
  output; what is banned is the *summary* scalar that invites Goodharting.
- **The sign/direction is pinned in a test.** Silent inversion (reporting AUC < 0.5 as if it were
  separation, or flipping a detector's polarity) is this family's shared failure mode; §4's
  `test_sign_direction_pinned` fixes the orientation of every detector.
- **calibration_status: heuristic.** No threshold is set, modified, or shipped from this
  experiment. The attack AUC never feeds calibration, conformal prediction, or a selector.
- **Held-out / disjoint.** Human windows are the FIXED reference class and are NEVER paraphrased
  (the adversary paraphrases only its own machine output). The attack corpus and rung-scored
  outputs MUST NOT be used as voicewright generation training data or humanization targets — they
  are adversarially constructed to fool detectors, not authentic human prose. **[REVIEW-5 folded.]**
- **Attack strength is always stated.** Every claim carries the paraphraser label + rung depth.

---

## 3. Design

### 3.1 Three-stage pipeline (M1 = stages 1+3 over injected stage-2 scores)

```
STAGE 1  attack generation   machine windows -> Paraphraser -> N rungs  [M1: StdlibProxyParaphraser]
STAGE 2  detector re-scoring  (paraphrased machine + original human) -> Scorer -> scores  [M2 GPU seam]
STAGE 3  degradation report   per (detector,rung): AUC, TPR@FPR{05,10}, Δ from rung 0
```

In **M1**, stage 2 is an injected `Scorer` returning pre-supplied per-(detector, rung) score
lists. The orchestration (stage 1 attack application + stage 3 AUC/Δ/TPR math + report assembly)
is exercised end-to-end with no model. In **M2**, the GPU operator binds a real `Scorer` that
calls the existing detectors one model at a time (operator runbook, §6).

### 3.2 AUC orientation (the silent-inversion guard)

AUC is `P(machine > human)`. But each detector's machine-vs-human SIGN is fixed and known:

| Detector | source on `main` | machine side | orient |
|---|---|---|---|
| `binoculars_v2` | `binoculars_audit` (`ratio < low -> ai_likely`) | machine LOWER | negate |
| `fast_detect_curvature` | `fast_detect_curvature` | machine HIGHER | as-is |
| `surprisal_mean` | `surprisal_backend.SMOOTHED_DIRECTION = "lt"` | machine LOWER | negate |
| `surprisal_sd` | `surprisal_backend.SMOOTHED_DIRECTION = "lt"` | machine LOWER | negate |
| `surprisal_acf_lag1` | `surprisal_backend.SMOOTHED_DIRECTION = "gt"` | machine HIGHER | as-is |
| `yules_k` | `variance_audit` | machine HIGHER | as-is |
| `burstiness_B` | `variance_audit` | machine LOWER | negate |
| `mtld` | `variance_audit` | machine LOWER | negate |

The harness carries a `DETECTOR_DIRECTION` map (the single source of sign truth, copied with
provenance from `SMOOTHED_DIRECTION` and the binoculars band) and orients each detector's raw
scores so the WMW-U AUC is the discriminative separation. `test_sign_direction_pinned` asserts
the map and the orientation so a future edit that flips a sign fails loudly.

### 3.3 Injectable `Paraphraser` Protocol (REVIEW-3 folded)

```python
class Paraphraser(Protocol):
    label: str
    def paraphrase(self, text: str, *, rung: int) -> str: ...
```

The runner defines its OWN Protocol — the `LadderParaphraser` seam the spec referenced does NOT
exist in `paraphrase_ladder.py` on `feat/raid-dipper-robustness` (REVIEW §1 verified this; folded
here). `StdlibProxyParaphraser` (label `proxy_stdlib`) is the bundled M1 driver: a deterministic,
model-free lexical proxy (a small closed synonym table + whitespace jitter), composing `rung`
passes. It is honestly weaker than DIPPER/back-translation; a corruption guard skips any rung
whose output is < 50% of the original length (never pass corrupted text to a scorer).

### 3.4 Output shape (no aggregate scalar)

A `build_output(task_surface="validation", ...)` envelope whose `results` carry:
`paraphraser_label`, `n_rungs`, `detectors`, `rung_0` (per-detector baseline AUC), and `per_rung`
(a list of `{rung, paraphraser_label, per_detector: {<det>: {auc, delta_auc, tpr_at_fpr05,
tpr_at_fpr10, delta_tpr_at_fpr05}}}`). **No** `robustness_score` / `auc_retained` /
`area_under_decay` / `is_robust` / aggregate of any kind. The `ClaimLicense` refuses any
"robust to paraphrase" claim and any detector-accuracy headline.

---

## 4. M1 tests (CI-runnable, model-free — every spec test + the REVIEW guards)

1. `test_auc_computation` — inject 10 machine / 15 human stub scores; AUC matches WMW-U; the
   all-machine-higher case = 1.0 and all-lower = 0.0 (after orientation).
2. `test_delta_auc` — Δ_AUC = AUC(rung_1) − AUC(rung_0), correctly carried.
3. `test_fpr_tpr_at_operating_points` — TPR at FPR ≤ 0.05 correctly read off the ROC curve.
4. `test_human_windows_not_paraphrased` — a recording stub Paraphraser is never called on a
   human-label window.
5. `test_no_aggregate_score` — the banned-key `_walk` finds no `robustness_score` /
   `auc_retained` / `area_under_decay` / `is_robust` / `overall_robustness` / `headline` at depth.
6. `test_rung0_auc_consistency` — the scoring-divergence gate: rung-0 AUC from the runner equals
   the baseline AUC computed directly from the same injected scores (REVIEW).
7. `test_proxy_attack_changes_text` — `StdlibProxyParaphraser` changes a fixture string.
8. `test_sign_direction_pinned` — every detector's machine-vs-human sign and its orientation are
   pinned (the silent-inversion guard, the family's shared failure mode).
9. `test_empty_and_tie_inputs` — empty / single-class / all-tied score lists return `None` AUC
   (no crash, no spurious 0.5), per the MATH/BOUNDS preflight.
10. `test_corruption_guard_skips_short_paraphrase` — a rung whose paraphrase collapses the text is
    skipped with a warning, never scored.

---

## 5. Anti-Goodhart posture (carried verbatim from the roadmap spec §9)

- Evaluation harness, not a detector. Registered on `validation`, never a verdict.
- The robustness report is NEVER a held-out fitness signal / reward / selector.
- No aggregate robustness scalar; the per-(detector × rung) table is the output.
- No threshold set or modified from the attack results.
- Honest FPR: TPR at FPR=0.05 and 0.10 reported before/after attack; AUC alone is not licensed.
- Attack strength (paraphraser label + rung depth) is stated in every claim.
- Attack-corpus texts must not be voicewright training data / humanization targets (REVIEW-5).
- `calibration_status: heuristic` for any downstream surface; nothing here ships calibrated.

---

## 6. M2 GPU operator runbook (NOT shipped in this PR — Code-PC rig discipline, quoted)

M2 binds a real `Scorer`: load Binoculars v2 (Llama-3.2-3B observer / 1B scorer, bf16) and gpt2
surprisal / curvature **one model at a time** (VRAM check `torch.cuda.mem_get_info()`, abort
< 4 GB free), score the 87 machine + 125 human lit-horror windows per rung, checkpoint per
(detector, rung). **[REVIEW-6 folded: back-translation (Helsinki-NLP opus-mt, ~300 MB/dir,
CPU-first verify) is the PRIMARY M2 neural attack; DIPPER 11B (bf16 ≈ 22 GB > 20 GB ceiling) is a
conditional extension gated on confirming a smaller variant.]** **[REVIEW-7: confirm the MarianMT
models are cached/repaired before the M2 session.]** Rung-0 self-consistency gate must pass
(within 0.005 of the known Binoculars 0.963) before any degradation number is interpreted. The
attack corpus is the upstream "R7 attack set" consumed by SPEC_shuffle §5 and SPEC_gec §5
**[REVIEW-4 folded: this harness is the producer]**. LRR is an optional detector column gated on
rank-space M2; the experiment does not block on it.

---

## 7. Files (this M1 PR)

- `plugins/setec-voiceprint/scripts/calibration/paraphrase_robustness.py` — the harness
  (capability id `paraphrase_robustness`, `validation` surface).
- `plugins/setec-voiceprint/scripts/tests/test_paraphrase_robustness.py` — the M1 tests above.
- `plugins/setec-voiceprint/capabilities.d/paraphrase_robustness.yaml` + the drop-in golden
  fragment `scripts/tests/_golden_capabilities/paraphrase_robustness.json`.
- `changelog.d/feat-33-paraphrase-robustness.md`; glossary + ROADMAP paper-trail lines.
