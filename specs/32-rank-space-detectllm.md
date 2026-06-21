# Spec 32: `rank-space-detectllm` — DetectLLM LRR rank-space surprisal signal

**Capability id:** `rank_space_audit` (tool/script: `rank_space_audit.py`)
**Task surface (EXISTING, not new):** `binoculars_discrimination` — same surface as the discrimination-evidence family; LRR is a rank-space *member* of the surprisal family, not a new surface.
**Family:** `detectllm-rank`
**arXiv root:** Su, Zhuo, Wang, Nakov (MBZUAI, 2023), *"DetectLLM: Leveraging Log Rank Information for Zero-Shot Detection of Machine-Generated Text"*, **arXiv:2306.05540**. Cited here, in the PR body, and in the `changelog.d/` fragment per the fleet rule. **M0-verification status: the paper is a 2023 published method (mechanism verified); its specific AUC lifts (+1.75 / +3.9 on WritingPrompts, Table 3) are PROVISIONAL leads requiring an empirical reproduction on SETEC corpora before any reliance — they are NOT asserted as fact anywhere in the build.**

> **This in-repo copy adapts the Dropbox spec (written for Code-PC, `D:\...`) to the Code-Mac checkout and folds the REVIEW (`REVIEW_rank-space.md`, verdict GO-with-changes). Every change-request is folded:**
> - **[Issue 1 — CHANGE REQUIRED] argsort sort DIRECTION pinned.** `rank_series_from_distributions` sorts the vocab log-prob vector **descending** (rank 0 = highest log-prob, most probable token); `numpy.argsort` defaults to ascending, which would silently invert the signal. The module uses pure-Python ranking (no numpy) with an explicit `(lp desc, vocab-id asc)` tie-break, and `test_rank_series_fixture` pins that the most-probable token gets `log_rank = log(0+1) = 0.0`, NOT a large value. A sign/direction inversion is the rank/surprisal family's shared silent failure mode, so it is pinned in a test, not prose.
> - **[Issue 2 — CHANGE REQUIRED] T5 checkpoint identity for NPR.** NPR (the perturbation variant) is the **GPU-gated M2 / Tier-2** path and is **OUT of this M1**. The checkpoint-identity question (`t5-large` span-corruption vs `flan-t5-large` instruction-tuned for the mask-fill loop) is recorded here and deferred to the NPR build; `npr_rank_score` is a `NotImplementedError` stub that fails loud rather than returning a silent number.
> - **[Issue 3 — MODERATE] rank-0 → inf convention pinned.** `log_rank_t = log(rank_t + 1)` is finite at rank 0 (`log(1) = 0.0`), but `lrr_t = surprisal_nats / log_rank_t` divides by 0 there. Convention: `lrr_t` is emitted as `math.inf` at rank-0 positions, those positions are EXCLUDED from the `lrr` mean (count surfaced as `lrr_excluded_positions`), and **no `inf` ever reaches the output envelope's `results`** (the R4 finiteness gate rejects it; the aggregate scalars are always finite-or-`None`). When every position is rank 0 the LRR mean is `None` — a refusal, not a fabricated 0. Pinned by `test_rank_0_edge_case`.
> - **[Issue 4 — DOCUMENTATION] anti-Goodhart / `heuristic` posture.** The band ships `calibration_status: heuristic` + `calibration_anchor: user-baseline-required`; LRR is a comparison-baseline, never a held-out audit / fitness / selection signal; promotion past `heuristic` goes only through `scripts/calibration/` against a labeled corpus disjoint from any development corpus. Stated in the claim-license and the changelog.
> - **De-dup re-checked against the LIVE Code-Mac tree (the Dropbox spec/REVIEW predate two merges).** Since the spec was written, `tocsin` (spec 31, surface `token_cohesiveness`) and `specdetect-lastde` (spec 30) have MERGED to `main`. Re-grep confirms: no existing `.py` computes per-token log-rank (`log_rank` / `lrr` / `npr_score` — zero matches); `rank_turbulence_audit.py` is word-frequency RTD on `voice_coherence` (a different computation on a different surface); `tocsin_audit.py` is random-token-deletion cohesiveness (no rank). No collision. This spec DEEPENS the Tier-4 surprisal family on the existing `binoculars_discrimination` surface.
> - **Path adaptation.** All Code-PC `D:\...` smoke/empirical/MAGE paths in the Dropbox spec are local experiment scripts (M0 / M2), NOT repo files, and are out of this M1. The two repo files this M1 lands are `scripts/rank_space_signals.py` (stdlib helpers) and `scripts/rank_space_audit.py` (the registered surface), plus tests and the drop-in registry fragments.
> - **Spec slot.** Lands at the next clean integer, `specs/32-rank-space-detectllm.md` (31 is the current max).

---

## 1. Framing (one paragraph)

DetectLLM **LRR** (log-likelihood / log-rank ratio) is a **rank-space member of the existing Tier-4 surprisal family**. The per-token log-rank — the rank of the actual next token in the model's per-position vocab distribution sorted by log-prob — is a near-free DERIVED column off the log-probability distributions a causal LM ALREADY materializes in `SurprisalBackend.score_text_with_distributions` (the same call `binoculars_audit.py` v2 makes for cross-perplexity): **no second forward pass is required for LRR**, just an argsort per position. The hypothesis (arXiv:2306.05540) is that log-rank is more robust than raw log-likelihood because rank is a monotone transform less sensitive to the absolute scale of the probability mass (the "capybara problem" where per-passage difficulty averages out and `surprisal_mean` collapses to ~0.5 AUC). LRR (`mean(surprisal_nats / log(rank+1))`) and the rank-space moments (`log_rank_mean / log_rank_sd / log_rank_acf1`, the analogues of `surprisal_mean / surprisal_sd / surprisal_acf_lag1`) are the new signals. In SETEC's posture this is **descriptive only**: VALUES + a PROVISIONAL band over the LRR value's OWN axis + `calibration_status`, never an `is_ai`/`is_human` label or a thresholded verdict. It is a member of the detection-signal/evidence-pack family alongside `surprisal_audit`, `fast_detect_curvature`, `tocsin_audit`, and `binoculars_audit` — keep-the-human, anti-Goodhart, held-out-disjoint.

---

## 2. What is genuinely new

The **per-token log-rank series** and the scalars derived from it:

1. `log_rank_mean` — mean of `log(rank_t + 1)`. Rank-space analogue of `surprisal_mean`.
2. `lrr` — mean of `surprisal_nats_t / log(rank_t + 1)` over the FINITE positions. The DetectLLM-LRR statistic.
3. `log_rank_sd` — population SD of the log-rank series (`None` if < 2 positions).
4. `log_rank_acf1` — lag-1 ACF of the log-rank series (`None` if < 3 positions or constant).
5. `npr_score` (NPR) — **NOT built in M1**; GPU-gated M2 / Tier-2 perturbation variant (T5-class mask-fill loop), `npr_rank_score` is a fail-loud stub.

Log-rank at position `t` = the 0-indexed rank of `token_ids[t+1]` in the position-`t` vocab log-prob vector sorted **descending**. `log_rank_t = log(rank_t + 1)` (add-1 convention, DetectLLM). `lrr_t = surprisal_nats_t / log_rank_t` where `surprisal_nats_t = surprisal_bits_t / log2(e)`.

---

## 3. Design

### 3.1 `scripts/rank_space_signals.py` (M1 core — stdlib only, no model)

- `rank_series_from_distributions(log_probs_nats, token_ids, surprisal_bits) -> {"log_rank_series", "lrr_series"}`. Pure-Python over the `score_text_with_distributions` tuple (or an injected stub of the same shape). Descending sort; add-1 convention; `inf` at rank 0. Validates input length consistency (raises `ValueError` on a tokenization/wiring mismatch) and a token-id out-of-range (raises `IndexError`).
- `aggregate_rank_signals(log_rank_series, lrr_series, surprisal_bits) -> {log_rank_mean, log_rank_sd, log_rank_acf1, lrr, lrr_excluded_positions, n_positions}`. All scalars finite-or-`None`; the LRR mean excludes non-finite (rank-0) positions; SD is `None` < 2 points, ACF `None` < 3 points. ACF is the same biased lag-1 estimator as `surprisal_audit._acf_at_lag` (Pearson on the lag-paired series); the structural minimum is 3 (not surprisal_audit's tuned 30-token floor — documented in the helper).
- `npr_rank_score(...)` — `NotImplementedError` stub (M2 GPU-gated NPR boundary, fails loud).
- Imports NOTHING from torch / transformers / numpy / scipy, nor from fitness / calibration / binoculars / validation / loop. Exposes no `verdict` / `calibration_status` / `band` key. `test_import_is_stdlib` asserts the model stack stays out of `sys.modules` (subprocess check).

### 3.2 `scripts/rank_space_audit.py` (registered surface)

- `TASK_SURFACE = "binoculars_discrimination"` (existing surface; no new `claim_license_surfaces/` file).
- `audit_rank_space(text, *, distributions_fn=None, backend=None, model_id=None, scorer_dtype=None)` — the **injectable model seam**: M1 tests inject `distributions_fn` (a stub returning `(surprisal_bits, log_probs_nats, token_ids)`); when absent, the CLI lazily constructs a `SurprisalBackend` and uses `score_text_with_distributions` (the M2 path; torch/transformers imported only inside `main`).
- Emits the canonical `output_schema.build_output` envelope: `results.lrr / log_rank_mean / log_rank_sd / log_rank_acf1 / lrr_excluded_positions / n_positions / scorer_backend`, a DESCRIPTIVE `band` over the LRR value's own axis (`indeterminate` / `low_lrr` / `high_lrr`, `calibration_status: heuristic`), and an `assumptions` block surfacing the sign/direction convention, the rank-0 inf convention, the ESL/non-native caveat, the proxy-scorer-dependence/generator-strength-inversion caveat, and the paper-status caveat.
- Claim license: licenses the rank-space statistics under the named causal LM; does NOT license an AI/human verdict; flags the ESL/non-native failure mode explicitly; flags proxy-scorer dependence and polarity inversion on weak generators; states LRR is a comparison-baseline, never a held-out fitness/selection signal.

### 3.3 NPR (M2 / Tier-2, conditional, OUT of M1)

NPR adds a T5-class mask-fill perturbation loop (`T=25`) over the rank scorer, GPU-gated. Build only after the LRR empirical run (M2 Tier-1) shows the rank axis has signal. Checkpoint identity (`t5-large` span-corruption recommended over `flan-t5-large`) to be resolved at build per REVIEW Issue 2.

---

## 4. M0 / M1 / M2 milestones

- **M0 (local smoke, not in repo):** load `SurprisalBackend("gpt2")`, run the rank math on a real forward pass, print the four scalars. Out of this PR.
- **M1 (this PR, CI-green, model-free):** `rank_space_signals.py` + `rank_space_audit.py` + 6 unit tests over injected stubs + drop-in registry. No model loads in CI.
- **M2 (local experiment, not in repo):** LRR empirical run on SETEC corpora (lit-horror / RAID Books / MAGE); AUC + direction-stability + Spearman-vs-Binoculars; then NPR (Tier-2, conditional). The empirical corpora are DEVELOPMENT, not held-out; any calibration run uses a disjoint corpus.

---

## 5. Posture (load-bearing, non-negotiable)

- `calibration_status: heuristic` permanently, unless a calibration run against a NEW held-out corpus (disjoint from the development corpora) supplies operating-point thresholds with honest FPR@target and ESL-population validation.
- LRR/NPR are NEVER the held-out audit / fitness / selection signal in any voicewright or SETEC pipeline (anti-Goodhart).
- No threshold is shipped without calibration; the output carries no verdict band.
- The band names the MEASURED property (rank-space surprisal), never the inference target (authorship). No `is_ai` / `is_human` / `label` / `verdict` / `decision` key anywhere.
- ESL/non-native is a STRUCTURAL false-positive mode (log-rank is higher for unconventional-but-valid word choices); surfaced in the claim-license and `assumptions`.

---

## 6. Files

**New (this PR):**
- `plugins/setec-voiceprint/scripts/rank_space_signals.py`
- `plugins/setec-voiceprint/scripts/rank_space_audit.py`
- `plugins/setec-voiceprint/scripts/tests/test_rank_space_signals.py`
- `plugins/setec-voiceprint/capabilities.d/rank_space_audit.yaml`
- `plugins/setec-voiceprint/scripts/tests/_golden_capabilities/rank_space_audit.json`
- `changelog.d/feat-32-rank-space-detectllm.md`
- `specs/32-rank-space-detectllm.md` (this file)

**Not in repo (M0 / M2 local experiments):** the smoke / LRR-empirical / NPR-empirical runners.
