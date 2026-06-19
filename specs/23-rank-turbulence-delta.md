# 23-rank-turbulence-delta

> Interpretable, per-word stylometric divergence between a target and a baseline corpus —
> rank-turbulence divergence (RTD) — that says *which words drive the difference*, the
> attribution layer Burrows Delta's single aggregate score can't give.

- **Status:** Ready — adversarially reviewed 2026-06-19 (P1 fixes: **function-words-only M1 default**
  to kill the topic confound; Z(α) normalization + reconstruction invariant pinned; **mandatory
  self-exclusion** of the target from its own baseline; tie-rule + α-monotonicity numeric pins). M1
  cleared to build.
- **Tier:** near-term (stdlib, additive)
- **GPU required:** no
- **Upstream / prior art:**
  - **Rank-Turbulence Divergence** — Dodds et al., *Allotaxonometry and rank-turbulence
    divergence* (2020). Stylometric-Delta adaptation: *Rank-Turbulence Delta and Interpretable
    Approaches to Stylometric Delta Metrics* ([arXiv:2604.19499](https://arxiv.org/abs/2604.19499)).
  - Companion to SETEC's existing `voice_distance` (Burrows Delta on function words).
- **License decision:** **clean-room the method.** RTD is published math (a tunable-α sum of
  per-word rank-reciprocal differences); reimplemented from the papers in stdlib, no weights.

## Motivation

`voice_distance` reports an aggregate Burrows Delta — *how far* a target is from a baseline, as one
z-score-based scalar. It does not say **which words** carry that distance. Rank-Turbulence Divergence
is a rank-based, normalized divergence whose **per-word contributions are its native output**: the
sum decomposes word-by-word, so the operator sees "the divergence is driven by over-use of *X, Y* and
under-use of *Z*." That interpretability is the glass-box value — and the tunable α lets the operator
dial between common-word and rare-word sensitivity.

**Orthogonality.** `voice_distance` = aggregate Delta (function words, z-scored). `bigram_diff` =
per-POS-bigram diff. `idiolect_detector` = distinctive n-grams via Dunning's G². RTD adds a
**rank-based, vocabulary-robust, per-word-attributed divergence** over the full lexical distribution
— a different estimator and a different unit (interpretable per-word rank contributions, not a
z-score or a G² list). It is additive: it does **not** modify `voice_distance` (the maintainer may
later surface it alongside the Delta report; that wiring is out of scope here).

## Method

**M1 is function-words-only by default** (P1 — topic confound). RTD over the *full* vocabulary is
dominated by content words and measures **topic**, not style — a reviewer would (correctly) call it a
topic detector. So M1 restricts both distributions to the shared **function-word set**
(`stylometry_core.FUNCTION_WORDS`, the ~135-word list `voice_distance` / `idiolect_detector` already
use), making it a genuine *stylometric* sibling to Burrows Delta. `--all-words` is an opt-in mode,
explicitly labelled "topical, not stylometric," in the results `assumptions` and a warning.

Given a `--target` and a `--baseline-dir`/`--manifest`, build a function-word frequency distribution
for each, rank words by frequency within each system (rank 1 = most frequent; **competition "1224"
ranking** for ties, ties shared), and compute over the union of (function) words τ the canonical
rank-turbulence divergence (Dodds et al. 2020):

  D^R_α(target ‖ baseline) = Z(α) · Σ_τ | 1/[r_{τ,target}]^α − 1/[r_{τ,baseline}]^α |^{1/(α+1)}
  with **Z(α) = ((α+1)/α) · (1/N_{1,2;α})** — the paper's prefactor `(α+1)/α` times the divisive
  normalizer `1/N_{1,2;α}` (N is the normalization sum that makes the two single-system self-divergences
  equal 1; pin its exact form to the paper in the build).

A word absent from one system takes a **tie-extended rank** (ranked just past that system's last word,
ties shared) — with α > 0 the term `1/r^α` is finite, so no divide-by-zero and absent words still
contribute. The headline scalar is `rtd` (≥ 0; 0 iff the two rank distributions are identical).

The **per-word contribution** reported in the output is the **bare summand**
δ_τ = | 1/[r_{τ,target}]^α − 1/[r_{τ,baseline}]^α |^{1/(α+1)}, so the interpretability invariant is
**Σ_τ δ_τ = rtd / Z(α)** (the contributions reconstruct the scalar; pinned in a test). Report the
top-`--top-k` δ_τ in each direction — words **over-ranked in the target** vs **in the baseline** —
with their ranks in both systems; **break contribution ties by word (lexicographic)** for deterministic
output. α is tunable (`--alpha`, default **1/3** per the allotaxonometry literature; α→0 emphasizes
rare words, larger α emphasizes the top of the rank list). Pure stdlib, deterministic.

## Contract (the testable interface)

- **task_surface:** **reuse `voice_coherence`** (the surface `voice_distance` / `general_imposters`
  already use — a target-vs-baseline stylometric axis; precedented multi-script surface, no new
  registration). No `claim_license_surfaces/` fragment needed (the surface exists).
- **CLI:** `python3 plugins/setec-voiceprint/scripts/rank_turbulence_audit.py --target T
  [--baseline-dir D | --manifest M] [--alpha 0.333] [--top-k 20] [--all-words] [--json] [--out F]`.
  Default = **function-words-only**; `--all-words` opts into the full-vocab (topical) mode.
- **Self-exclusion (mandatory, P1):** drop any baseline doc whose `Path.resolve()` equals the target's
  (the house pattern — `voice_distance`'s drop loop, `general_imposters._exclude_target_path`), else the
  target self-normalizes its own rank distribution and `rtd` collapses toward 0. Emit a `dropped` count
  in `assumptions` + a stderr note; if the baseline is empty after the drop → `bad_input`.
- **JSON envelope:** via `output_schema.build_output()` with a `ClaimLicense` block. `results`:
  `rtd`, `alpha`, `mode` ("function_words" | "all_words"), `n_vocab`, `top_target` / `top_baseline`
  (each a list of `{word, rank_target, rank_baseline, contribution}`), `target_tokens`,
  `n_baseline_docs`, `assumptions` (`{mode, alpha, tie_rule: "competition", normalization, dropped_self,
  topic_caveat}`). The unavailable/empty cases route through `build_error_output(...,
  reason_category="bad_input")` (like `general_imposters`).
- **Claim license — licenses:** "a rank-based divergence between the target's and the named baseline's
  word-rank distributions, with per-word contributions identifying which words drive it." **Refuses:**
  any AI/human or authorship verdict; any claim that a high RTD means 'different author' or 'AI' (it is
  a lexical-distribution divergence — topic, genre, and length shift it); thresholds operator-side /
  PROVISIONAL.
- **capabilities.d fragment:** `rank_turbulence_audit.yaml` — `surface: voice_coherence`; `status:
  heuristic`; `compute.tier: core`; `length_floor_words` (≥ ~200 for a stable rank distribution);
  `dependencies.python: []`; `use_when` (interpretable per-word complement to a Delta run) /
  `do_not_use_when` (topic-mismatched target/baseline → topical, not stylistic, divergence).
- **Dependencies / footprint:** none (stdlib).
- **Surface-addition paper trail (AGENTS.md):** the `capabilities.d/` fragment + a `changelog.d/`
  fragment (citing arXiv:2604.19499) + `gen_calibration_readiness.py` refresh + the `references/
  signals-glossary.md` / `ROADMAP.md` updates (the latter two at release reconciliation, as for spec
  22). No new surface label (reuses `voice_coherence`). Run check_capabilities_drift /
  gen_calibration_readiness / check_docs_freshness before push.

## Test contract (names + invariants the build must satisfy)

`plugins/setec-voiceprint/scripts/tests/test_rank_turbulence_audit.py`:

- **deterministic-output**; **envelope-shape**; **claim-license-present** + **refuses-verdict** (no
  `verdict`/`is_ai` key; `does_not_license` carries the "not authorship / not AI / topic moves it"
  caveat).
- **default-is-function-words** — a run with no `--all-words` restricts to the function-word set
  (`results.mode == "function_words"`); `--all-words` flips the mode + sets the topic-caveat warning.
- **self-exclusion** — target placed INSIDE its own `--baseline-dir` is dropped (`assumptions.dropped_self
  >= 1`, stderr note); `rtd` is computed against the remaining docs, not collapsed to ~0; empty-after-drop
  → `bad_input`.
- **graceful-degradation** — empty baseline or empty target → `available:false` `bad_input`.
- **numeric pins:** identical target/baseline distributions → `rtd` ≈ 0, empty contributions;
  **non-negativity** (`rtd ≥ 0`); **reconstruction invariant** — `sum(δ_τ) == pytest.approx(rtd / Z(α))`
  (contributions are the bare summands); a target over-using a function word the baseline rarely uses →
  that word tops `top_target` with positive δ.
- **α-monotonicity (concrete)** — a fixture where a common function word A and a rare one B swap the
  top-1 contribution between `--alpha 0.1` and `--alpha 1.0`; assert the swap (pins that α dials
  rare-vs-common emphasis, not a vague "as documented").
- **tie-extended rank** — a word present only in the target is assigned the tie-extended baseline rank
  (tested), so absent words contribute without a divide-by-zero or a dropped term; **competition "1224"**
  tie rule pinned.

## Calibration posture

Ships **PROVISIONAL / heuristic** — a measurement + attribution, no verdict, operator-side bands. A
labeled same-author/different-author (or same-topic/different-topic) corpus would calibrate an RTD band
later → `empirically_oriented` with a PROVENANCE entry. The default emits no decision.

## Out of scope / non-goals

- **Not** a modification of `voice_distance` (additive companion; wiring RTD into the Delta report is a
  later, separate change). No authorship/AI verdict. The full-vocab `--all-words` mode is **topical, not
  stylometric** by design (off by default) — it is a diagnostic, never an authorship/AI claim.

## Open questions

1. **α default** — 1/3 (allotaxonometry default) vs a stylometry-tuned value; `--alpha` exposed, the
   default is a maintainer call (a calibration question, not a build blocker).
2. ~~**Function-words-only mode**~~ **Resolved: function-words is the M1 default** (topic-robust,
   genuinely stylometric); `--all-words` is the opt-in full-vocab/topical mode.
3. ~~**Tie-handling**~~ **Resolved: competition "1224"** ranking, ties shared; pinned in a test.
