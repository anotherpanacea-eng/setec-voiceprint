# Stylometry oracle: SETEC vs R `stylo`

`references/implementation-survey.md` lists R `stylo` (Eder, Rybicki, Kestemont 2016) as a *reference implementation* for SETEC's voice-distance machinery — not a runtime dependency, not something we want to load at CLI time, but a battle-tested oracle the framework's Burrows-style Delta and cosine distance can be checked against. This document records the comparison.

## The oracle test

Six Federalist Papers (3 Hamilton, 3 Madison; ~13,700 words total) at `scripts/test_data/federalist_oracle/` are the public-domain fixture. The Hamilton-vs-Madison binary is the canonical Mosteller-Wallace stylometric benchmark, originally settled by Mosteller and Wallace's 1964 *Inference and Disputed Authorship*. Both SETEC and stylo should produce distance matrices where within-author distances cluster together and cross-author distances open up.

Three complementary phases:

**Phase A — distance correctness on identical input.** SETEC exports its frequency table per feature family (function words, char-3-grams, char-4-grams, char-5-grams, POS-trigrams, dependency n-grams) to CSV. stylo runs `dist.delta` and `dist.cosine` on each table; SETEC runs its own Burrows-Delta and cosine distance on the same table. If the two implementations agree on the math, the numbers should match to floating-point noise (~1e-10). Disagreement at any larger scale is a math bug. This phase isolates the *distance computation* from feature-selection and tokenization differences. The exported tables are production-shaped: each value is the feature's full-family relative frequency from `stylometry_core.py` (i.e., normalized over the whole feature family before selection), and the selected top-K subset is *not* row-renormalized. Row sums are typically < 1.0 — the mass not captured by the top-K is the share of features outside the selection. (An earlier draft of the oracle did renormalize the selected subset so rows summed to 1.0; that produced an internally-consistent but non-production table, and the fix landed in 1.8.1 — see CHANGELOG.)

**Phase A' — frequency-table correctness on identical parse (POS / dep only).** stylo doesn't natively do POS or dependency parsing, so for the POS-trigram and dep-n-gram families the parser of record is spaCy on both sides: SETEC writes per-document parse TSVs, and the R side reads them and reconstructs the production-shaped selected-feature frequency tables from scratch — same per-sentence reset, same n-gram window construction, same key-format strings, same top-K corpus-aggregate selection, same full-family denominator preservation (no subset renormalization). Cell-by-cell agreement here verifies the n-gramming + frequency-table-construction code path independently of distance math.

**Phase B — end-to-end on raw text (function-words only).** stylo's full pipeline (its own tokenization, its own corpus-derived MFW ranking) vs. SETEC's full pipeline (its own tokenization, its fixed Mosteller-Wallace + extensions wordlist) on the same raw fixture. Disagreement here is *expected* and *informative*: it shows how much SETEC's design choice (fixed wordlist) diverges from stylo's (corpus-derived ranking) on a real text. Spearman rank correlation is the appropriate measure — we want the Hamilton-vs-Madison cluster structure to surface in both regardless of absolute distance values.

The harness lives at `scripts/oracle/`:

- `setec_to_stylo.py` — SETEC side. Reads the fixture, computes per-family frequency tables (function words, char-3/4/5-grams, POS-trigrams, dep-n-grams) and pairwise Burrows-Delta + cosine distances; for the POS / dep families also writes per-document spaCy parse TSVs to `results/parses/<doc_id>.tsv`.
- `run_stylo.R` — stylo side. For each frequency table runs `stylo::dist.delta` / `dist.cosine` (Phase A); for the function-word fixture runs stylo's full pipeline on the raw `.txt` files (Phase B); for the POS / dep families reads the parse TSVs and independently rebuilds frequency tables (Phase A'). Writes pairwise distance matrices and reconstructed frequency tables.
- `compare.py` — generates the markdown comparison report from both sides' outputs.

To run:

```
python3 scripts/oracle/setec_to_stylo.py
Rscript scripts/oracle/run_stylo.R
python3 scripts/oracle/compare.py
```

The generated report lands at `scripts/oracle/results/oracle_comparison_report.md`. Both Python and R outputs (CSVs) are committed alongside it so the comparison is reproducible without re-running R.

## Intentional differences

Where SETEC's stylometry diverges from stylo's defaults, the divergence is a *design choice*, not a defect. The four documented differences:

### 1. Function-word selection: fixed list vs. corpus-derived MFW

stylo's Burrows pipeline ranks words by total frequency across the corpus and keeps the top-N. The "MFW" (Most Frequent Words) selection is therefore corpus-dependent: the Federalist fixture's MFW differs from a Henry James fixture's MFW.

SETEC uses a fixed list (Mosteller-Wallace + extensions, defined in `stylometry_core.FUNCTION_WORDS`, currently 135 words). The vocabulary is the same regardless of which baseline the writer is comparing against.

The trade-off:

- **Fixed list (SETEC):** stable across runs and corpora. A writer's voice profile built today and re-checked next year uses the same vocabulary. Cross-document comparability of distance numbers is preserved.
- **Corpus-derived MFW (stylo):** adapts to the genre/register/time period of the corpus. Useful when the corpus has a distinctive vocabulary that the canonical Mosteller-Wallace list misses (e.g., 18th-century political prose has different high-frequency function words than 21st-century blog essays).

For the framework's "voice coherence" task surface (target draft vs. writer's own baseline), the fixed list is the right call: stability beats corpus adaptation when the goal is to track the *same* writer over time. For provenance-classification tasks across heterogeneous corpora, corpus-derived MFW would be more responsive.

The Phase B Spearman correlation is the empirical measure of how much this matters on the Federalist fixture.

### 2. Z-score population

stylo's `dist.delta` z-scores each feature column across the *entire corpus* including the document being scored. The mean and SD are corpus-wide.

SETEC's `voice_distance.py` z-scores each feature against the *baseline* only — the target document is not part of the z-score population. This matches the framework's directional framing: "how far is this draft from the writer's prior work," not "where does this draft sit in a symmetric pairwise space."

For the oracle test in Phase A, SETEC's `setec_to_stylo.py` uses corpus-wide z-scoring (matching stylo) so the distance math can be checked apples-to-apples. For real voice-distance runs via `voice_distance.py`, the directional baseline-only z-scoring stays.

### 3. Selected-feature caps

stylo's default in `stylo()` is to scan multiple MFW counts (100, 200, 300, 400, 500) and report distances at each. The single-N call is configurable.

SETEC caps at 100 function words by default (`--word-top 100` in `voice_distance.py`) and uses per-n caps for character n-grams (`--char-top 200` per n in `char_ngram_features`, applied separately to char_ngrams_3 / 4 / 5). The per-n separation prevents 3-grams from dominating the cap by sheer count of types.

For the oracle test, both sides use the same N (matched to SETEC's fixed list size).

### 4. Per-n character n-gram handling

stylo treats character n-grams as a single feature family: top-N most frequent *across all n-values* in one frequency space. With the unified family, 3-grams dominate selection (more types fit any threshold) and 4- and 5-gram signal gets drowned out.

SETEC separates char_ngrams_3, char_ngrams_4, and char_ngrams_5 into distinct families with per-n caps (default 200 each) and per-n normalization (each family's frequencies sum to 1.0 within its own n). This was a deliberate fix in the framework's history — earlier versions used the unified approach and the 4- and 5-gram signal was effectively lost. The trade-off is more total features (600 by default vs. stylo's 200-ish if scanning N=200 across all n combined).

The oracle test's Phase A operates on function words only (where SETEC and stylo agree on feature-set shape); the per-n char-ngram divergence is documented but not tested at this slice. A future pass could add a parallel oracle test for character n-grams.

## Results (initial run on the Federalist fixture)

**Phase A (distance correctness on identical input): perfect match across all six feature spaces.**

| Feature space | Burrows-Delta Pearson r | Burrows-Delta Mean \|Δ\| | Cosine Pearson r | Cosine Mean \|Δ\| |
|---|---:|---:|---:|---:|
| Function words (135 fixed list) | 1.0000 | 0.000000 | 1.0000 | 0.000000 |
| Char-3-grams (top-200 corpus-derived) | 1.0000 | 0.000000 | 1.0000 | 0.000000 |
| Char-4-grams (top-200 corpus-derived) | 1.0000 | 0.000000 | 1.0000 | 0.000000 |
| Char-5-grams (top-200 corpus-derived) | 1.0000 | 0.000000 | 1.0000 | 0.000000 |
| POS-trigrams (top-300 corpus-derived) | 1.0000 | 0.000000 | 1.0000 | 0.000000 |
| Dependency n-grams n=2,3 (top-300, single pool) | 1.0000 | 0.000000 | 1.0000 | 0.000000 |

SETEC's pairwise Burrows-Delta and cosine distance computations match stylo's `dist.delta` and `dist.cosine` to floating-point precision when both operate on the same frequency table, across all six feature spaces SETEC supports. The math is verified for the function-word path, for each per-n char-ngram path, for the POS-trigram path, and for the combined dep-2-gram + dep-3-gram path. SETEC's design choice to separate char-ngrams into per-n families (3, 4, 5) with per-n caps (default 200) and per-n normalization is internally consistent — each per-n table behaves like a standalone Burrows-Delta input, exactly as stylo treats single-MFW tables. SETEC's design choice to share a single normalization pool for dep-2 and dep-3 grams (rather than per-n separation) is also internally consistent — the combined table behaves like a single-MFW table at top-300.

**Phase A' (frequency-table reconstruction on identical parse): perfect match for POS-trigrams and dep-n-grams.**

| Feature space | n cells compared | setec-only feats | stylo-only feats | Pearson r | Mean \|Δ\| |
|---|---:|---:|---:|---:|---:|
| POS-trigrams | 1800 | 0 | 0 | 1.0000 | 0.000000 |
| Dependency n-grams (n=2,3) | 1800 | 0 | 0 | 1.0000 | 0.000000 |

Phase A' is the load-bearing addition of the POS / dep oracle pass. SETEC's per-document spaCy parses are exported to TSV (`scripts/oracle/results/parses/<doc_id>.tsv`); the R side reads those TSVs and rebuilds the POS-trigram and dep-n-gram frequency tables from scratch using its own per-sentence reset, its own n-gram window construction, its own key-format string assembly (`pos:A-B-C`, `dep{n}:X-Y[-Z]`), its own top-K corpus-aggregate selection, and its own production-denominator preservation (no subset renormalization, matching `stylometry_core.py`). Cell-by-cell agreement at zero difference (and zero feature-set asymmetry) confirms that SETEC's `pos_trigram_features` / `dependency_ngram_features` + selection + normalization code paths match a from-scratch reimplementation. The only remaining unverified component is the spaCy parse itself, which is the parser of record on both sides — verifying spaCy is out of scope for an oracle test of SETEC's stylometric math.

One bug surfaced and fixed during this oracle test: an earlier draft of `scripts/oracle/setec_to_stylo.py` averaged Burrows-Delta over all features in the fixed wordlist, including constant-zero columns (function words from the Mosteller-Wallace + extensions list that don't appear in the Federalist fixture). That produced a systematic factor-of-(n_informative / n_total) ≈ 8/9 underestimate of stylo's Delta — same Pearson 1.0 (perfect linear correlation, identical ranking) but a constant offset on absolute values. Fixed by averaging only over informative features (those with non-zero SD across the corpus), matching both stylo's convention and the production `stylometry_core.family_distance` behavior (which already accumulated abs(z) only when `sd > 0`). The production code was already correct; the oracle harness was wrong. Worth noting because the discovery validates the production path: had the oracle harness's earlier behavior matched production, the test would have falsely reported a math discrepancy. The fix in the oracle harness is recorded in `setec_to_stylo.py`'s `burrows_delta` docstring.

**Phase B (end-to-end on raw text): substantial agreement, divergence informative.**

| Metric | Pearson r | Spearman ρ | Mean \|Δ\| | Max \|Δ\| | Relative MAE |
|---|---:|---:|---:|---:|---:|
| `burrows_delta` | 0.7870 | 0.6464 | 0.051 | 0.221 | 0.044 |
| `cosine_distance` | 0.9688 | 0.9679 | 0.007 | 0.014 | 0.16 |

Cosine distance survives the feature-set divergence well: Pearson 0.97 and Spearman 0.97 mean SETEC and stylo see the same Hamilton-vs-Madison cluster structure with very similar absolute values, even though one uses SETEC's fixed wordlist and the other uses stylo's corpus-derived MFW.

Burrows-Delta is more sensitive to the feature-set choice: Spearman 0.65 is *real disagreement* on which document pairs are closer than which. The L1-z-score-mean formula amplifies the effect of including or excluding any individual function word, because z-scores are sensitive to which features appear in the corpus-wide variance pool. This confirms what the implementation survey suspected: SETEC's fixed-list choice is a *design choice* with empirical consequences, not a bug. Whether SETEC or stylo produces the "right" Delta depends on what the user is asking — stable cross-corpus comparability (SETEC) or corpus-adaptive feature ranking (stylo).

For the framework's voice-coherence task surface (target draft vs. writer's own baseline), SETEC's fixed-list call is the right one: the writer's voice profile from a year ago and today should use the same vocabulary so distance numbers are comparable across time. The Phase B disagreement is a feature, not a bug — but it does mean SETEC's Burrows-Delta numbers are *not* directly comparable to a stylo user's Delta numbers on the same corpus.

## Acceptance criteria for this oracle test

Per issue #4:

1. **Reproducible note or script records the comparison.** ✓ — this document plus the harness at `scripts/oracle/`. The output CSVs are committed alongside so the report can be re-read without re-running R.
2. **Differences are either within tolerance or explicitly explained.** ✓ — Phase A: zero difference (perfect match on the math). Phase B: explained by the fixed-list-vs-corpus-derived-MFW design choice; the Spearman correlations document how much the choice matters per metric (cosine: barely; Burrows-Delta: meaningfully).
3. **R remains optional.** ✓ — neither `requirements.txt` nor any runtime script imports R. The oracle test is run-once-when-validating; the rest of the framework is Python-only. The output CSVs are committed so the comparison is reproducible from the report alone, no R install required.

## Limitations and follow-up work

Six documents and 135 function words (plus 200 char-ngrams per n) is a small fixture. The Pearson and Spearman estimates have wide CIs at this N. A larger oracle fixture (e.g., the full Federalist set with disputed authorship, ~85 papers) would tighten the comparison and add a discrimination test (does Spearman ρ between SETEC and stylo hold up when we add Madison's contested papers vs. Hamilton's known papers?).

**Char-n-gram Phase B is roadmap.** The current Phase A confirms SETEC's per-n char-ngram math matches stylo on identical input. A Phase B that lets stylo do its own char-ngram tokenization (`stylo::txt.to.features(parsed, features="c", ngram.size=n)`) and SETEC do its own — then compares — would surface any divergence in the *tokenization* layer (whitespace handling, character normalization, n-gram boundary rules). Lower priority than Phase A correctness; useful for users who want to interpret cross-tool char-ngram results.

**POS-trigram and dependency-n-gram oracle passes are now oracle-verified.** SETEC's `voice_distance.py` reports six feature families total (function words, char-3, char-4, char-5, POS-trigrams, dependency n-grams). All six are now oracle-verified at floating-point precision. The POS / dep pass uses spaCy as the parser of record on both sides (the R side reads SETEC's per-document parse TSVs at `scripts/oracle/results/parses/`); the load-bearing comparison is Phase A' frequency-table reconstruction, where R independently rebuilds the n-gram frequency tables from the parses and the result matches SETEC's exports cell-by-cell. The pass requires spaCy installed in the runtime; without spaCy, the SETEC side skips the POS / dep export with a notice and the rest of the oracle still runs.

**Rolling-window Delta oracle is blocked on the stylo API.** `stylo::rolling.delta` exposes only four parameters in its function signature (gui, path, primary.corpus.dir, secondary.corpus.dir); the window controls a SETEC user would want to verify against (`text.slice.length`, `text.slice.overlap`, `mfw`, `distance.measure`) are baked into the function body as local defaults. The documented `config.txt` override path hangs the R process under the conditions tested. If rolling-window verification becomes load-bearing later, the right move is a SETEC-internal pytest contract test on the windowing logic rather than a cross-tool oracle pass — `stylo::rolling.delta` was never going to provide a clean cross-tool reference at this API surface. The cross-tool oracle stays focused on what `stylo` is well-suited to be a reference for: feature-set frequency tables and pairwise distance math on those tables.

The fixture is bounded by the public-domain commit constraint. The author's personal baseline corpus is not committed (it's voice-cloning input), so the oracle test cannot speak directly to the framework's distance-correctness on production data. The Federalist fixture is a valid proxy: same Burrows-Delta math, different register.
