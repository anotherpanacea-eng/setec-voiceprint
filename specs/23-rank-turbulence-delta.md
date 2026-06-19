# 23-rank-turbulence-delta

> Interpretable, per-word stylometric divergence between a target and a baseline corpus —
> rank-turbulence divergence (RTD) — that says *which words drive the difference*, the
> attribution layer Burrows Delta's single aggregate score can't give.

- **Status:** Draft
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

Given a `--target` and a `--baseline-dir`/`--manifest`, build a word-frequency distribution for each,
rank words by frequency within each system (rank 1 = most frequent; ties → shared/competition rank),
and compute, over the union of words τ:

  D^R_α(target ‖ baseline) = Z(α) · Σ_τ | 1/[r_τ,target]^α − 1/[r_τ,baseline]^α |^{1/(α+1)}

where a word absent from one system takes a **tie-extended rank** (ranked just past that system's last
word, ties shared) and Z(α) is the paper's normalization. The headline scalar is `rtd` (≥ 0; 0 iff the
two rank distributions are identical). Each summand is the **per-word contribution** δ_τ; report the
top-`--top-k` contributors in each direction — words **over-ranked in the target** vs **over-ranked in
the baseline** — with their ranks in both systems. α is tunable (`--alpha`, default **1/3** per the
allotaxonometry literature; α→0 emphasizes rare words, larger α emphasizes the top of the rank list).
Pure stdlib (counting + ranking + arithmetic), deterministic.

## Contract (the testable interface)

- **task_surface:** **reuse `voice_coherence`** (the surface `voice_distance` / `general_imposters`
  already use — a target-vs-baseline stylometric axis; precedented multi-script surface, no new
  registration). No `claim_license_surfaces/` fragment needed (the surface exists).
- **CLI:** `python3 plugins/setec-voiceprint/scripts/rank_turbulence_audit.py --target T
  [--baseline-dir D | --manifest M] [--alpha 0.333] [--top-k 20] [--min-count 1] [--json] [--out F]`.
- **JSON envelope:** via `output_schema.build_output()` with a `ClaimLicense` block. `results`:
  `rtd`, `alpha`, `n_vocab`, `top_target` / `top_baseline` (each a list of `{word, rank_target,
  rank_baseline, contribution}`), `target_tokens`, `n_baseline_docs`, `assumptions`.
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
  `verdict`/`is_ai` key; `does_not_license` carries the "not authorship / not AI" caveat).
- **graceful-degradation** — empty baseline or empty target → `available:false` `bad_input`.
- **numeric pins:** identical target/baseline word distributions → `rtd` ≈ 0 and empty/zero
  contributions; a target that heavily over-uses a word the baseline rarely uses → that word tops
  `top_target` with a positive contribution; **non-negativity** (`rtd ≥ 0`); **per-word sum** equals
  `rtd / Z(α)` (the contributions reconstruct the scalar, the interpretability invariant);
  **α-monotonicity sanity** (changing α reorders rare-vs-common emphasis as documented).
- **tie-extended rank** — a word present only in the target is assigned the tie-extended baseline rank
  (tested), so absent words contribute without a divide-by-zero or a dropped term.

## Calibration posture

Ships **PROVISIONAL / heuristic** — a measurement + attribution, no verdict, operator-side bands. A
labeled same-author/different-author (or same-topic/different-topic) corpus would calibrate an RTD band
later → `empirically_oriented` with a PROVENANCE entry. The default emits no decision.

## Out of scope / non-goals

- **Not** a modification of `voice_distance` (additive companion; wiring RTD into the Delta report is a
  later, separate change). No authorship/AI verdict. Not a topic-divergence claim (RTD over content
  words moves with topic — `do_not_use_when` topic-mismatched; an optional `--function-words-only`
  mode that restricts to a function-word list is a possible M2, not M1).

## Open questions

1. **α default** — 1/3 (allotaxonometry default) vs a stylometry-tuned value; expose `--alpha`, decide
   the default with the maintainer.
2. **Function-words-only mode** — restrict the distribution to function words (closer to Burrows Delta,
   topic-robust) as a flag/M2? Decide whether M1 ships full-vocab only.
3. **Tie-handling** — competition ("1224") vs dense ("1223") ranking for frequency ties; pick the one
   the paper uses and pin it in a test.
