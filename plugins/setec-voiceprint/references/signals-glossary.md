# SETEC Voiceprint: Stylometric Signals Glossary

A reference for every analytical measurement SETEC computes on prose. For each entry: what it measures, how it's computed, how to interpret the score, a concrete example, and known caveats.

**Scope**: stylometric tests only — the analytical measurements computed on the text itself, the kind of thing a stylometry paper would cite. Operational machinery (manifest validation, calibration toolchain, ROC harnesses, restoration packets, data acquisition) is documented elsewhere.

**Reading guide**:

  * Entries marked **calibrated** have empirical bands tied to a labeled corpus (RAID v1, EditLens v1, etc.). The shipped values are PROVISIONAL per the Stylometry-to-the-people policy regardless of calibration anchor; operators wanting load-bearing thresholds run their own calibration locally.
  * Entries marked **provisional** have heuristic or literature-anchor bands. Treat as orientation, not adjudication.
  * Polarity arrows: ↓ means "lower values are more AI-like"; ↑ means "higher values are more AI-like"; ↔ means "no single direction, both extremes can be diagnostic"; — means "no polarity registered, used as a baseline-relative signal."
  * Signal paths in `code formatting` correspond to the JSON shape `variance_audit.py` emits and the keys the band-classifier walks.

**Iteration note (for the maintainer)**: this doc is in active iteration. Entries with sparse interpretation / example sections are marked `(NEEDS REFINEMENT)` for the LLM-pass that comes after the initial commit. The technical scaffold (definition, computation, range, status, calibration anchor) is filled in from primary sources.

---

## Contents

  * [Tier 1: Variance signals](#tier-1-variance-signals)
  * [Tier 2: Syntactic signals](#tier-2-syntactic-signals)
  * [Tier 3: Semantic cohesion / trajectory signals](#tier-3-semantic-cohesion--trajectory-signals)
  * [Tier 4: Surprisal signals](#tier-4-surprisal-signals)
  * [Voice-distance signals](#voice-distance-signals)
  * [Voice-drift signals](#voice-drift-signals)
  * [POV-voice signals](#pov-voice-signals)
  * [Mimicry / cosplay signals](#mimicry--cosplay-signals)
  * [Semantic preservation signals](#semantic-preservation-signals)
  * [Phraseology signals](#phraseology-signals)
  * [Punctuation cadence signals](#punctuation-cadence-signals)
  * [Stance / modality signals](#stance--modality-signals)
  * [Bigram-KL signals](#bigram-kl-signals)
  * [Repetition signals](#repetition-signals)
  * [Totals](#totals)

---

## Tier 1: Variance signals

Layer-A variance audits surface when prose has been smoothed: surface-form variability collapses. Tier 1 signals operate on word counts, type-token ratios, and sentence-length distributions — the cheapest computations in the framework, all available without a baseline.

### Sentence-length burstiness (B)

**Signal path**: `tier1.sentence_length.burstiness_B` · **Family**: tier1-variance · **Polarity**: ↓ · **Status**: calibrated · **Calibration anchor**: EditLens v1 (FPR 0.93%, TPR 7.0%, da_AUC 0.683)

**What it measures**: Whether sentences cluster (a few short, a few long, mixed unpredictably) or stay near a uniform length. Burstiness normalizes the variance against the mean so the score is comparable across registers.

**How it's computed**: For each sentence, count words. Then `B = (SD − mean) / (SD + mean)` where SD and mean are over the per-sentence word counts.

**Range / units**: `[-1, 1]`. Closer to `-1` means sentences are uniform in length; closer to `+1` means sentence-length distribution is heavy-tailed.

**Interpretation**: Natural prose mixes sentence lengths and produces moderately negative B (often in the −0.05 to −0.25 range for essayistic prose, more negative for fiction with rapid pacing). AI-smoothed prose collapses toward sentence-length uniformity and produces more strongly negative B. The framework's threshold (B < −0.4) was tightened to spare essayistic registers that naturally reach B ≈ −0.40.

**Example**: A 50K-word essayistic blog post with measured B = −0.18 sits comfortably in the natural-prose distribution. The same blog post passed through ChatGPT's "smooth this" prompt often drops to B = −0.45 or lower while preserving most other Tier 1 signals — burstiness is the canary.

**Caveats**: Strongly register-dependent. Literary horror short fiction can register B near −0.5 naturally because of intentional sentence-length compression for pacing. Personal-baseline z-scores beat raw B for any single-author audit.

---

### Sentence-length standard deviation

**Signal path**: `tier1.sentence_length.sd` · **Family**: tier1-variance · **Polarity**: ↓ · **Status**: calibrated · **Calibration anchor**: RAID v1

**What it measures**: Raw variability of sentence length in words. The un-normalized cousin of burstiness B.

**How it's computed**: Population standard deviation of the per-sentence word-count series.

**Range / units**: `[0, ∞)` words.

**Interpretation**: A typical essay has per-sentence word counts ranging from ~5 to ~40, producing SD in the 8-15 range. Highly uniform AI-smoothed prose can compress to SD in the 3-6 range. Conversational or fragmented styles push SD higher.

**Example**: An academic article will often show SD ≈ 12 with mean ≈ 22. The same content rewritten by a model to "improve readability" typically lands at SD ≈ 7 with mean ≈ 18 — the words are tighter, but the cadence has been ironed flat.

**Caveats**: Register-dependent in the same way as B. The framework's threshold is intentionally loose because essayistic prose has high natural SD that resembles "uneven" AI prose in isolation. Use the personal-baseline z-score.

---

### Moving-average type-token ratio (MATTR)

**Signal path**: `tier1.mattr.value` · **Family**: tier1-variance · **Polarity**: ↓ · **Status**: calibrated · **Calibration anchor**: Literary-fluent-fiction baseline (0.70-0.82 at window 50)

**What it measures**: Lexical diversity averaged across sliding windows. The fraction of distinct words in each window, averaged.

**How it's computed**: Slide a 50-token window through the document. In each window, compute `|unique tokens| / |total tokens|`. Average across all windows.

**Range / units**: `[0, 1]`.

**Interpretation**: Higher MATTR means more vocabulary turnover (more diverse word choice). Natural literary fiction usually scores in the 0.70-0.82 range at the default 50-token window. Heavily repetitive prose (instructional text, beginner narrative) scores lower. AI-smoothed prose sometimes scores low because of vocabulary collapse onto safe register-appropriate words, but this signal is less sharp than burstiness B for the AI/human contrast.

**Example**: An 80,000-word literary novel might show MATTR = 0.76. A blog post heavy on "in conclusion" and "moreover" rewrites can drop to MATTR = 0.58. The window size matters: MATTR at window 100 reads ~0.05 higher than MATTR at window 50 on the same text.

**Caveats**: Sensitive to window size; the framework default is 50. MATTR is the recommended TTR variant over raw Type-Token Ratio because it doesn't depend on document length the way raw TTR does.

---

### Measure of textual lexical diversity (MTLD)

**Signal path**: `tier1.mtld` · **Family**: tier1-variance · **Polarity**: ↓ · **Status**: calibrated · **Calibration anchor**: User-calibration recommended below ~500 words (noisy)

**What it measures**: Length-of-prose-needed-before-vocabulary-diversity-falls-below-a-threshold. A more robust lexical diversity measure than MATTR for long documents.

**How it's computed**: Walk the document token by token, tracking running type-token ratio. Each time the running TTR drops to or below 0.72, register a "factor" and reset. Average the number of tokens per factor across forward and backward passes.

**Range / units**: `[0, ∞)` tokens. Higher values mean more lexical diversity (more tokens fit per factor).

**Interpretation**: MTLD captures how long the writer can go before they reuse vocabulary heavily. Literary fiction routinely scores MTLD in the 60-110 range. Heavily templated AI prose can compress into the 30-50 range.

**Example**: A 60K-word literary horror novel scores MTLD ≈ 85 (diverse vocabulary throughout). The same novel passed through "tighten this prose" instruction-tuning rewrites compresses to MTLD ≈ 52 because the rewrite collapses vocabulary onto safer high-frequency words. **(NEEDS REFINEMENT: tune the example to match a documented before/after pair if available.)**

**Caveats**: Noisy below ~500 words. Below that threshold the score is sensitive to where in the document the TTR-0.72 thresholds happen to fall. Use a personal baseline rather than the literature anchor for short manuscripts.

---

### Yule's K

**Signal path**: `tier1.yules_k` · **Family**: tier1-variance · **Polarity**: ↑ · **Status**: calibrated · **Calibration anchor**: Provisional

**What it measures**: Concentration of vocabulary on a small set of frequent types. A second-moment measure of vocabulary distribution.

**How it's computed**: `K = 10⁴ × (M₂ − N) / N²` where `M₂ = Σ(count²)` over each type and `N` is the total token count.

**Range / units**: `[0, ∞)` dimensionless units.

**Interpretation**: Yule's K is the inverse of lexical diversity — higher K means the same words recur more often. AI-smoothed prose tends to recycle a small set of safe high-frequency words and shows elevated K. Natural literary prose has lower K.

**Example**: **(NEEDS REFINEMENT: typical natural-prose K values and typical AI-prose K values. The framework has bands but the literature anchor for Yule's K specifically isn't in the spec.)**

**Caveats**: Yule's K is sensitive to outliers (a few very-high-count function words dominate the numerator). Function-word stripping changes the score substantially.

---

### Shannon entropy (vocabulary entropy)

**Signal path**: `tier1.shannon_entropy_bits` · **Family**: tier1-variance · **Polarity**: ↓ · **Status**: calibrated · **Calibration anchor**: Literature anchor 9.5-10.5 bits/token for native fiction; empirical pre-AI human prose 8.0-9.6

**What it measures**: Average information content (in bits) per token, computed over the document's word-frequency distribution. The information-theoretic version of "vocabulary diversity."

**How it's computed**: For each unique word i with frequency p_i = (count_i / total), compute `H = −Σ p_i log₂(p_i)`.

**Range / units**: `[0, log₂(vocab_size)]` bits.

**Interpretation**: Higher entropy means a more uniform distribution across more distinct vocabulary items. Natural English prose with a rich vocabulary clusters around 9.5-10.5 bits/token; restricted-vocabulary writing scores lower. AI-smoothed prose can score lower because vocabulary concentrates on safer high-frequency words.

**Example**: A literary novel with 25K distinct words across 80K tokens registers ≈ 9.8 bits/token. An instructional manual with 4K distinct words across 30K tokens registers ≈ 7.2 bits/token. AI rewrites of literary prose typically drop the original 9.5+ to roughly 8.7-9.1.

**Caveats**: Depends heavily on vocabulary scope and register. The framework's threshold is set very loose because focused-vocabulary writing (technical, instructional) naturally scores low and would otherwise produce false positives. Personal-baseline z-scores are the load-bearing read.

---

### Flesch-Kincaid grade level standard deviation (FKGL SD)

**Signal path**: `tier1.fkgl.sd` · **Family**: tier1-variance · **Polarity**: ↓ · **Status**: calibrated · **Calibration anchor**: Human prose 3-5; LLM 0.8-1.5

**What it measures**: Variability in sentence-by-sentence reading-difficulty estimates. Captures whether the document mixes hard and easy sentences or pulls them toward a uniform grade level.

**How it's computed**: Per sentence, compute FKGL = `0.39 × W + 11.8 × (Sy / W) − 15.59` where W is words per sentence and Sy is syllables per sentence. Then take the standard deviation across the per-sentence FKGL series.

**Range / units**: `[0, ∞)` grade levels.

**Interpretation**: Human prose mixes difficulty (short and easy → long and complex → fragmentary → expository → ...) and produces FKGL SD in the 3-5 range. LLM prose tends to converge on a target grade level and produces FKGL SD in the 0.8-1.5 range. This is one of the cleanest signal-to-noise tier-1 signals.

**Example**: A blog essay measured at FKGL SD = 4.1 sits in the natural distribution. The same essay rewritten by an LLM to "make this clearer" typically drops to FKGL SD ≈ 1.2 — every sentence ends up at the same grade level.

**Caveats**: FKGL is itself a noisy per-sentence statistic for very short sentences (single-clause fragments produce undefined grade levels). The SD is robust to this because it averages across the document.

---

### Connective density

**Signal path**: `tier1.connective_density.per_1000_tokens` · **Family**: tier1-variance · **Polarity**: ↑ · **Status**: calibrated · **Calibration anchor**: AI prose 25-50 per 1000 tokens; humans 5-15

**What it measures**: How often the writer uses discourse-connecting words (furthermore, moreover, however, therefore, additionally, consequently, …). The "AI cohesion smell."

**How it's computed**: Count occurrences of a curated set of ~50 connectives. Normalize to a per-1000-token rate.

**Range / units**: `[0, ∞)` connectives per 1000 tokens.

**Interpretation**: AI-trained text uses discourse markers far more often than human prose because RLHF and instruction-tuning reward explicit-cohesion writing. Human writers tend to imply transitions rather than mark them with "moreover." A rate above ~25 per 1000 is a strong AI tell; rates above 50 are characteristic of heavily-smoothed AI output.

**Example**: A 5000-word essay by a human writer typically contains 30-75 connectives total (rate of 6-15 per 1000). The same essay rewritten by a model often grows to 125-250 connectives (rate of 25-50 per 1000) without changing the underlying argument.

**Caveats**: Academic prose naturally elevates connective density (target rate often 18-25 per 1000) and can produce false positives. Genre-aware calibration helps. The connective list is hand-curated and may miss writer-specific transitional vocabulary.

---

### Function-word ratio

**Signal path**: `tier1.function_words.function_word_ratio` · **Family**: tier1-variance · **Polarity**: — (not directly thresholded; feeds Burrows Delta) · **Status**: structural-only · **Calibration anchor**: None

**What it measures**: Proportion of tokens that are function words (pronouns, articles, prepositions, auxiliaries, conjunctions). Used as the lexical-vector input to Burrows Delta voice-distance computations.

**How it's computed**: Count tokens that appear in the curated `FUNCTION_WORDS` set; divide by total tokens.

**Range / units**: `[0, 1]`.

**Interpretation**: Function-word ratio is steady across registers within an author (typically 0.45-0.55 across English prose). The voice signal lives in the per-word distribution, not in the aggregate ratio — the same author writing about different topics has the same function-word ratio but the same characteristic relative frequencies of `the`, `of`, `that`, `which`, etc. The ratio itself is informational, not diagnostic.

**Example**: **(NEEDS REFINEMENT: the per-feature Burrows Delta entries below are the load-bearing voice signals; this entry exists to document the underlying ratio so readers understand what feeds the Delta.)**

**Caveats**: Not registered in `COMPRESSION_HEURISTICS`. Operators should not threshold this; the diagnostic value is in the per-word distribution that feeds Burrows Delta.

---

## Tier 2: Syntactic signals

Tier 2 operates on POS tags and dependency-parse output. Requires spaCy (`en_core_web_sm` or a multilingual model).

### POS-bigram entropy

**Signal path**: `tier2.pos_bigrams.entropy_bits` · **Family**: tier2-syntax · **Polarity**: — (feeds POS-bigram KL) · **Status**: calibrated · **Calibration anchor**: Empirical baselines via voice_profile.py

**What it measures**: Distributional entropy over POS-tag pairs (e.g., DET-NOUN, ADJ-NOUN, VERB-ADV). A measure of syntactic-template diversity.

**How it's computed**: POS-tag every token via spaCy. Form bigrams of adjacent POS tags. Compute Shannon entropy over the bigram-frequency distribution.

**Range / units**: `[0, log₂(unique_bigram_count)]` bits. Typically 6-9 bits for English prose.

**Interpretation**: A document with high POS-bigram entropy uses many different syntactic templates. Lower entropy means the writing returns to a smaller set of grammatical patterns. AI-smoothed prose tends to compress POS-bigram distribution onto a smaller template set; the consequence shows up most clearly in the KL signal below, not in the raw entropy.

**Example**: **(NEEDS REFINEMENT: typical bits-per-bigram ranges for natural vs. AI prose. The signal is real but the raw entropy isn't as sharp as the KL divergence built on top of it.)**

**Caveats**: Not directly thresholded. Used as a baseline-relative signal via POS-bigram KL. Requires spaCy.

---

### POS-bigram KL divergence

**Signal path**: `baseline_divergences.pos_bigrams.kl` · **Family**: tier2-syntax · **Polarity**: ↑ · **Status**: provisional · **Calibration anchor**: Literature 0.15; cross-human KL typically < 0.05; human-vs-LLM 0.10-0.30

**What it measures**: How much the target document's POS-bigram distribution diverges from a baseline (the writer's own corpus, or a reference corpus). Detects syntactic-template collapse.

**How it's computed**: Tag both target and baseline; build POS-bigram frequency distributions; apply Laplace (add-one) smoothing to handle unseen bigrams; compute `KL(target ‖ baseline) = Σ p(b) × log₂(p(b) / q(b))`.

**Range / units**: `[0, ∞)` bits.

**Interpretation**: KL is asymmetric: it measures how surprising the target's syntactic distribution looks under the baseline's expectations. Cross-human KL (your prose vs. another human's prose in the same register) typically stays below 0.05. Human-vs-LLM KL on the same topic typically lands in the 0.10-0.30 range. Values above ~0.30 usually mean substantial register shift or genuine template collapse.

**Example**: A literary blogger's recent post compared against their last five years of archived posts measures KL = 0.04 (within natural variation). The same post rewritten by Claude to "tighten this" measures KL = 0.18 — the syntactic distribution has shifted, even when individual word choices look mostly the same.

**Caveats**: Requires spaCy and a baseline. Laplace smoothing is essential when bigram coverage is sparse; without it the KL is dominated by unseen-bigram log(p/0) blowups. The weight in `COMPRESSION_HEURISTICS` is 2.0 (matched with burstiness and connective density).

---

### Mean dependency distance standard deviation (MDD SD)

**Signal path**: `tier2.mdd.sd` · **Family**: tier2-syntax · **Polarity**: ↓ · **Status**: calibrated · **Calibration anchor**: Provisional

**What it measures**: Variability in syntactic compactness. For each token in a parsed sentence, the dependency distance is the number of tokens between that token and its syntactic head. The per-sentence mean of those distances is MDD; this signal is the SD of MDD across sentences in the document.

**How it's computed**: spaCy-parse each sentence. For each token (excluding ROOT), compute `|token_position − head_position|`. Average within the sentence to get per-sentence MDD. Take the SD of the per-sentence MDD series across the document.

**Range / units**: `[0, ∞)` tokens.

**Interpretation**: Human prose mixes long-distance constructions (relative clauses, fronted modifiers, parentheticals) with compact ones; the per-sentence MDD varies substantially. AI-smoothed prose tends to use a narrower band of syntactic constructions and produces low MDD SD.

**Example**: **(NEEDS REFINEMENT: typical natural-prose MDD SD vs. AI-prose MDD SD. The signal is documented in the spec but the bands are provisional.)**

**Caveats**: Requires spaCy parsing. Minimum 2 sentences needed to compute SD. The signal is sometimes called "compressed syntactic variation" in the framework's internal notes.

---

## Tier 3: Semantic cohesion / trajectory signals

Tier 3 operates on sentence embeddings. Default backend is `sentence-transformers/all-MiniLM-L6-v2` with a TF-IDF fallback. Per-document cohesion + trajectory analysis.

### Adjacent-sentence cosine, mean

**Signal path**: `tier3.adjacent_cosine.mean` · **Family**: tier3-trajectory · **Polarity**: ↑ · **Status**: calibrated · **Calibration anchor**: Tight cohesion is an LLM tell

**What it measures**: How similar consecutive sentences are to each other on average, in semantic-vector space. The "every sentence follows logically from the last" cohesion smell that distinguishes AI prose from human prose.

**How it's computed**: Embed every sentence with the sentence-encoder backend. For each adjacent pair (i, i+1), compute cosine similarity. Average across all adjacent pairs in the document.

**Range / units**: `[0, 1]` (cosine; in practice `[0.3, 0.9]` for natural prose).

**Interpretation**: Human writing makes sharp topical jumps, includes asides, returns to earlier threads — and adjacent sentences often have moderate similarity (0.5-0.7 typical). AI prose tends to produce smoother trajectories with adjacent-sentence cosine in the 0.7-0.9 range. The cohesion is real, but it's *too* tight.

**Example**: An academic-philosophy essay measured at adjacent-cosine mean = 0.62 sits in the natural distribution. The same essay rewritten by Claude for clarity often climbs to 0.81 — every transition has been smoothed.

**Caveats**: Falls back to TF-IDF if `sentence-transformers` is unavailable; the signal still works but the calibration anchor changes. Requires minimum 2 sentences. Genre matters: technical writing naturally has higher cohesion than narrative.

---

### Adjacent-sentence cosine, standard deviation

**Signal path**: `tier3.adjacent_cosine.sd` · **Family**: tier3-trajectory · **Polarity**: ↓ · **Status**: calibrated · **Calibration anchor**: None documented

**What it measures**: Variability in sentence-to-sentence semantic transitions. The complement to adjacent-cosine mean.

**How it's computed**: SD of the cosine-similarity series computed in `adjacent-sentence cosine, mean`.

**Range / units**: `[0, ∞)`.

**Interpretation**: Natural prose makes some tight transitions (continuing a thought) and some loose ones (changing topic, adding an aside) — producing moderate SD. AI prose evens out the transitions and compresses SD. A document with high mean cohesion *and* low cohesion SD is the strongest tier-3 AI signal.

**Example**: **(NEEDS REFINEMENT: typical natural-prose SD vs. AI-prose SD. The signal is documented but the bands aren't pinned in the spec.)**

**Caveats**: Same backend dependencies as adjacent-cosine mean. The pair (mean, SD) is the load-bearing read; either signal alone is interpretable but ambiguous.

---

### Semantic trajectory cosine series

**Signal path**: `semantic_trajectory_audit` output (`window_trajectories[i].cosine_to_next`) · **Family**: tier3-trajectory · **Polarity**: ↔ (diagnostic, not thresholded) · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Per-window cosine similarities between consecutive semantic windows. The paragraph-level extension of adjacent-sentence cosine: how does the document's semantic content shift across paragraphs?

**How it's computed**: Slide a window through the document (default: paragraph-level, alternative: token-level). Embed each window. Compute cosine similarity between consecutive windows. Report the per-window series.

**Range / units**: `[0, 1]` cosine per pair.

**Interpretation**: A document with stable semantic trajectory (all paragraphs similar to their neighbors) is on a single topic; one with variable trajectory traverses multiple topics or perspectives. The trajectory itself isn't a pass/fail signal; it's a diagnostic shape readers and tools interpret in context.

**Example**: **(NEEDS REFINEMENT: a worked example of a typical literary essay vs. an AI summary, showing the trajectory shapes side by side.)**

**Caveats**: Paragraph-level windowing is the default; token-level windowing produces a finer-grained but noisier signal. Requires sentence-transformers (TF-IDF fallback available).

---

### Semantic trajectory slope

**Signal path**: `semantic_trajectory_audit` output (`trajectory_analysis.slope`, `intercept`, `r_squared`) · **Family**: tier3-trajectory · **Polarity**: ↔ · **Status**: provisional · **Calibration anchor**: None

**What it measures**: The linear trend in adjacent-window cosine across the document. Whether the document tightens or loosens semantically as it progresses.

**How it's computed**: Linear regression of `cosine_to_next` against window position. Extract slope, intercept, and R² of the fit.

**Range / units**: Slope ∈ ℝ; R² ∈ `[0, 1]`.

**Interpretation**: A positive slope means the document closes more tightly than it opens (transitions are tighter at the end); a negative slope means it disperses. Neither direction is inherently "AI-like"; the signal is diagnostic about the document's structure. R² above ~0.3 suggests the trend is meaningful; below that the slope is noise.

**Example**: **(NEEDS REFINEMENT: cite a documented case where slope identified a structural issue, e.g., a summary tacked onto the end that didn't match the document's opening.)**

**Caveats**: Requires enough windows for meaningful regression (target >= 10). On short documents the regression is dominated by single-window noise.

---

## Tier 4: Surprisal signals

Tier 4 computes per-token surprisal under a small causal LM (TinyLlama by default; see `SPEC_surprisal_model_choice.md`). Opt-in via `--tier4` because the cost is 1-2 orders of magnitude over Tiers 1-3.

### Per-token surprisal mean

**Signal path**: `tier4.surprisal.mean` · **Family**: tier4-surprisal · **Polarity**: ↓ · **Status**: provisional · **Calibration anchor**: user-baseline-required

**What it measures**: Average predictability of the document's tokens under a causal LM, expressed in bits per token. AI-generated text tends to be near the mode of the LM's distribution; human prose ranges more widely.

**How it's computed**: For every token position i ≥ 1, the LM computes `−log₂(P(token_i | tokens_{<i}))`. The arithmetic mean of that series is the surprisal mean.

**Range / units**: `[0, ∞)` bits per token. Typical natural English under TinyLlama: 4-7 bits per token.

**Interpretation**: Lower mean = more predictable = closer to the LM's expected distribution = more likely AI-generated. Higher mean = more surprising = more likely human and idiosyncratic. The signal is most useful as a personal-baseline z-score because base-rate surprisal varies by register, topic, and language complexity.

**Example**: A 5K-word literary-horror short story scored against TinyLlama measures surprisal mean ≈ 6.2 bits/token. The same story rewritten by GPT-4 to "polish this" measures ≈ 4.8 bits/token — the LM finds the rewrite easier to predict, because the rewrite landed closer to a generic LM's mode.

**Caveats**: Provisional bands. The surprisal value depends sharply on which causal LM is the backend (see `SPEC_surprisal_model_choice.md` §3.8 on training-data contamination bucketing). Per the framework's stylometry-to-the-people policy, the shipped thresholds are PROVISIONAL regardless of backend.

---

### Per-token surprisal standard deviation

**Signal path**: `tier4.surprisal.sd` · **Family**: tier4-surprisal · **Polarity**: ↓ · **Status**: provisional · **Calibration anchor**: user-baseline-required

**What it measures**: How variably surprised the LM is across the document. AI prose stays uniformly close to the LM's expectations; human prose has both predictable stretches and isolated surprising tokens.

**How it's computed**: Sample (Bessel-corrected) standard deviation of the per-token surprisal series.

**Range / units**: `[0, ∞)` bits.

**Interpretation**: Low SD means uniform predictability — the LM is rarely surprised. High SD means scattered moments of surprise that punctuate longer predictable stretches. DivEye (Basani & Chen, TMLR 2026) identifies this as the most sensitive of the three Tier 4 signals; it isolates the "no isolated surprises" signature of AI text.

**Example**: A literary essay scored under TinyLlama measures surprisal SD ≈ 4.1 bits — frequent moments of unusual vocabulary or surprising sentence-construction punctuate the prose. The same essay rewritten by Claude measures surprisal SD ≈ 2.7 bits — the surprises have been smoothed out.

**Caveats**: Same backend-dependence and provisional-status caveats as surprisal mean.

---

### Per-token surprisal autocorrelation, lag 1

**Signal path**: `tier4.surprisal.autocorrelation.lag_1` · **Family**: tier4-surprisal · **Polarity**: ↑ · **Status**: provisional · **Calibration anchor**: user-baseline-required

**What it measures**: How correlated each token's surprisal is with the previous token's surprisal. Captures "predictability streaks" — runs of consecutive predictable tokens (AI) versus more isolated surprises (human).

**How it's computed**: `ACF(1) = Cov(X_t, X_{t+1}) / Var(X)` over the per-token surprisal series.

**Range / units**: `[−1, 1]`.

**Interpretation**: Positive autocorrelation means surprisal levels persist across consecutive tokens — easy-to-predict tokens cluster, hard-to-predict tokens cluster. AI prose tends toward positive autocorrelation because the model generates in steady-predictability stretches. Human prose has lower autocorrelation because surprises arrive at less regular intervals.

**Example**: **(NEEDS REFINEMENT: typical ACF(1) values for natural vs. AI prose. The framework's bands are provisional and the literature anchor is fresh — DivEye paper 2026.)**

**Caveats**: Requires minimum 30 tokens for meaningful estimation. The JSON output includes a `degenerate=true` flag when the series is too short for stable autocorrelation.

---

## Voice-distance signals

Voice-distance signals measure how a draft differs from the writer's own baseline corpus. The traditional stylometric "is this the same author?" question.

### Burrows Delta (function-word)

**Signal path**: `voice_distance` output (`deltas.function_words`) · **Family**: voice-distance · **Polarity**: ↑ (higher = more divergent) · **Status**: calibrated · **Calibration anchor**: voice_profile.py baseline aggregation

**What it measures**: How much the draft's function-word usage profile differs from the writer's baseline, computed as the Euclidean norm of standardized z-scores across the most-common function words.

**How it's computed**: Compute per-function-word relative frequency in baseline and draft. Standardize each draft frequency to a z-score against baseline mean and SD (per word). Compute the Euclidean norm (or weighted Mahalanobis-style norm) of the z-score vector. This is the Burrows Delta.

**Range / units**: Standardized distance units (typical scale 0-3; values >2 increasingly suspicious).

**Interpretation**: Delta near 0 means the draft uses function words the way the baseline does. Delta climbing past 1.5-2 means the function-word profile has shifted: either a register change, a topic change that altered function-word use, or a different author/agent generating the text. The classic stylometric authorship test.

**Example**: A blogger's recent post compared against their last five years of writing measures Delta = 0.6 (within their own distribution). The same blogger's post that was actually drafted by an LLM and lightly edited often measures Delta = 1.9 — the function-word distribution shifted even when surface vocabulary looks consistent.

**Caveats**: Burrows Delta requires a baseline of sufficient size (typically >= 20K words; ideally a topic-and-register-matched baseline). On heterogeneous baselines (the writer mixes blog posts, fiction, and academic prose) Delta inflates against natural register-shift.

---

### Per-feature cosine distance

**Signal path**: `voice_distance` output (`cosines.function_words`, `cosines.pos_trigrams`, etc.) · **Family**: voice-distance · **Polarity**: ↑ · **Status**: calibrated · **Calibration anchor**: voice_profile.py

**What it measures**: Cosine distance between draft and baseline in each of the framework's feature spaces (function-word frequencies, POS-trigram frequencies, punctuation distribution, etc.). The "angular" complement to Burrows Delta.

**How it's computed**: Normalize feature-frequency vectors to unit length. Compute `cosine_similarity(draft, baseline)`. Distance = 1 − similarity.

**Range / units**: `[0, 1]`.

**Interpretation**: Cosine distance near 0 means the relative shape of the feature distribution matches the baseline. Cosine distance climbing past 0.05-0.10 means the feature profile has shifted in shape (some features dropped or amplified). Cosine and Delta are complementary — cosine catches relative-shape changes; Delta catches magnitude changes.

**Example**: **(NEEDS REFINEMENT: typical author-internal cosine distances vs. author-vs-LLM cosine distances. The framework documents the metric but bands are loose.)**

**Caveats**: Cosine is less sensitive to magnitude shifts than Burrows Delta — a document that uses function words in the same relative proportions but at a different absolute rate produces low cosine distance and high Delta. Read both together.

---

## Voice-drift signals

Voice-drift tracking compares a writer's prose across time periods to surface which features have drifted and which have stayed stable.

### Voice drift (cross-period coefficient of variation)

**Signal path**: `voice_drift_tracker` output (`drifting_features`) · **Family**: voice-drift · **Polarity**: ↑ (high CV = drifting) · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Which stylistic features have shifted over time and which have stayed durable. Per-feature, the coefficient of variation across time-period means.

**How it's computed**: Group the writer's baseline corpus by time period (year, quarter, month, or custom). For each feature, compute its mean per period. The coefficient of variation across periods is `CV = SD(period_means) / mean(period_means)`. Features with high CV are drifting; features with low CV are stable.

**Range / units**: `[0, ∞)` dimensionless (CV).

**Interpretation**: High-CV features are part of the writer's evolving voice (vocabulary that comes and goes; phrases learned and dropped). Low-CV features are the durable idiolect — the part of style that defines the writer across decades. For voice-preservation work, the durable features are what restoration packets protect.

**Example**: A blogger's 10-year archive shows function-word relative frequencies with CV ≈ 0.05 (stable) but emoji/punctuation patterns with CV ≈ 0.4 (drifting as platform conventions shift). The function-word profile is the load-bearing signal for "is this still you?"

**Caveats**: Requires date-tagged baseline (manifest with `date_written` or date-prefixed filenames). Short periods with few documents produce unreliable CV estimates.

---

### Voice stability (low cross-period CV)

**Signal path**: `voice_drift_tracker` output (`stable_features`) · **Family**: voice-drift · **Polarity**: ↓ (low CV = stable) · **Status**: provisional · **Calibration anchor**: None

**What it measures**: The complement of voice drift. The features with the lowest CV across time periods — the writer's durable signature.

**How it's computed**: Inverse logic of `voice drift`. Features ranked by ascending CV; the lowest-CV features are reported as stable.

**Range / units**: `[0, ∞)` CV.

**Interpretation**: Stable features are what a voice-preservation pass needs to retain. If a restoration draft scores well on tier-1 variance signals but shows shifted values on the stable-feature subset, the restoration has succeeded at surface compression but damaged the underlying voice.

**Example**: **(NEEDS REFINEMENT: a worked example of voice-stability features for a specific writer, showing what the durable signature looks like.)**

**Caveats**: Same date-tagging requirement as voice drift.

---

## POV-voice signals

For multi-POV fiction: how distinct are the writer's POV characters' voices?

### POV voice-distance matrix

**Signal path**: `pov_voice_profile` output (`pairwise_distances`) · **Family**: pov-voice · **Polarity**: ↑ (high = distinct POVs) · **Status**: provisional · **Calibration anchor**: None

**What it measures**: For each pair of POV characters in a multi-POV manuscript, the Burrows Delta and cosine distance between their voiceprints. Higher distances mean the writer is distinguishing the voices in measurable stylometric features.

**How it's computed**: Aggregate the writer's baseline by POV (using manifest `pov` field). Build per-POV voiceprints (function-word profiles, POS-trigram profiles, etc.). Compute pairwise Burrows Delta and cosine distance for each POV pair.

**Range / units**: Mahalanobis distance units (Delta); `[0, 1]` (cosine).

**Interpretation**: A well-distinguished POV pair has Delta typically > 1.5 and cosine distance > 0.1. POV pairs with Delta < 1.0 are the "voice collapse" candidates — characters whose stylometric profiles are indistinguishable from each other.

**Example**: **(NEEDS REFINEMENT: a fictional example with three POV characters, showing distinct vs. collapsed pairs.)**

**Caveats**: Requires manifest with `pov` field and enough text per POV (typically >= 5K words per POV for stable estimates). Cross-POV-pair statistical comparisons are noisy below that threshold.

---

### POV voice-collapse verdict

**Signal path**: `pov_voice_profile` output (`voice_collapse_verdict`) · **Family**: pov-voice · **Polarity**: ↑ (high = collapse) · **Status**: provisional · **Calibration anchor**: None

**What it measures**: A pass/fail-style verdict flagging POV pairs whose pairwise Burrows Delta sits below a heuristic threshold — the characters are statistically indistinguishable in stylometric terms.

**How it's computed**: Apply a heuristic threshold to the `pairwise_distances` matrix. Pairs with Delta below the threshold are flagged as collapsed.

**Range / units**: Boolean per pair + the underlying Delta value.

**Interpretation**: Collapsed POV pairs are a craft-restoration signal: the writer intended distinct voices but failed to differentiate them in measurable lexical/syntactic features. Useful as a revision prompt rather than a verdict.

**Example**: **(NEEDS REFINEMENT: realistic collapsed-pair example with what differentiation would look like.)**

**Caveats**: Threshold is heuristic and not anchored to a labeled dataset. The "right" threshold depends on genre — literary fiction can sustain less-distinguished POVs than commercial fiction can.

---

## Mimicry / cosplay signals

Mimicry-cosplay detection asks: has someone (or an LLM) generated text that mimics the writer's surface vocabulary but doesn't share the writer's syntactic profile? The smoking gun of style impersonation.

### Lexical mimicry survival rate

**Signal path**: `mimicry_cosplay_audit` output (`lexical_survival.survival_rate`) · **Family**: mimicry · **Polarity**: ↑ (high survival + high syntactic Delta = cosplay) · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Proportion of the writer's signature lexical n-grams (from baseline) that survive in the target document. Captures surface-vocabulary mimicry.

**How it's computed**: Identify the writer's high-frequency or distinctive lexical n-grams from the baseline corpus. Count occurrences in the target document. `survival_rate = target_count / baseline_expected_count`.

**Range / units**: `[0, 1]`.

**Interpretation**: High survival means the target document is reproducing the writer's surface vocabulary. On its own, this isn't suspicious — it's expected for genuine work by the same writer. Combined with high syntactic Delta (next entry), it's the cosplay signature: surface vocabulary borrowed without underlying syntactic profile.

**Example**: **(NEEDS REFINEMENT: a concrete cosplay example — surface vocabulary survives but POS-trigram profile diverges.)**

**Caveats**: A genuine work by the same writer will also score high on lexical survival. The signal is only diagnostic when paired with the syntactic Delta.

---

### Syntactic mimicry (Burrows Delta on POS-trigrams)

**Signal path**: `mimicry_cosplay_audit` output (`syntactic_delta.overall`) · **Family**: mimicry · **Polarity**: ↑ (high = dissociation from lexical surface) · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Burrows Delta computed on POS-trigram frequencies (rather than function-word frequencies). When syntactic Delta is high and lexical mimicry survival is also high, the writer's vocabulary is being borrowed without the writer's underlying grammar.

**How it's computed**: Same Burrows Delta math as the voice-distance entry above, but the feature vector is POS-trigram relative frequencies instead of function-word relative frequencies.

**Range / units**: Standardized distance units.

**Interpretation**: The cosplay test is the joint condition `lexical_survival > X AND syntactic_delta > Y`. Surface vocabulary matches but the grammatical fingerprint doesn't — strong evidence of impersonation, whether by another writer or by an LLM prompted to "write like X."

**Example**: **(NEEDS REFINEMENT: a worked cosplay example with both signals' values.)**

**Caveats**: Same baseline-size and POS-tagger dependencies as the underlying signals.

---

## Semantic preservation signals

For craft restoration: did a restoration pass preserve the document's load-bearing semantic content? These are *diagnostic* signals (no polarity, no AI/human verdict) used to check that restoration didn't strip propositions, citations, or named entities.

### Claim inventory preservation

**Signal path**: `semantic_preservation_check` output (`preservation.claim_inventory.before_count`, `after_count`, `change`) · **Family**: semantic-preservation · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Approximate count of declarative sentences in the before/after of a restoration. A proxy for "how many propositions did the document assert?"

**How it's computed**: Regex-based detection of sentence-final periods + sentence-initial capitalization, filtered to exclude questions and exclamations. Each detected sentence is counted as one claim.

**Range / units**: `[0, ∞)` sentence count + signed `change` (after − before).

**Interpretation**: A restoration that drops 20+ claims has likely stripped propositions the original author was asserting. A restoration that adds 20+ claims has added new propositions and may be drifting from the original voice. Neither direction is inherently bad; the signal is a checkpoint.

**Example**: A 4000-word essay revised for variance restoration goes from 178 claims to 165 claims (−13). Within the documented tolerance band; restoration didn't drop substantive content. The same essay rewritten by an over-eager LLM might go from 178 to 142 claims — that's a propositional loss worth investigating.

**Caveats**: Regex-based; over-counts run-on sentences and under-counts fragmented ones. Use as a checkpoint rather than a verdict.

---

### Named-entity preservation

**Signal path**: `semantic_preservation_check` output (`preservation.named_entities.before_count`, `after_count`, `change`) · **Family**: semantic-preservation · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Count of proper-noun named entities (persons, places, organizations) in before and after. Captures whether the restoration preserved factual references.

**How it's computed**: spaCy NER when available; regex fallback (capitalized multi-word patterns) otherwise. Sums all entities regardless of category.

**Range / units**: `[0, ∞)` entity count + signed `change`.

**Interpretation**: A restoration that drops named entities has likely stripped references that grounded the document in specifics. This is the classic restoration failure mode: AI-generated revisions tend to abstract away specific names and citations because they're "smoothing."

**Example**: A 3000-word op-ed mentioning 14 specific people, places, and organizations before restoration; the restored version retains 12. Within band. A restoration that drops to 4 has stripped most of the factual grounding.

**Caveats**: NER quality depends on the spaCy model; regex fallback is much noisier. Cross-checking counts before/after with the same NER pipeline is the load-bearing comparison, not the absolute counts.

---

### Citation / authority preservation

**Signal path**: `semantic_preservation_check` output (`preservation.citations_and_authorities.before_count`, `after_count`, `change`) · **Family**: semantic-preservation · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Count of evidential frames ("according to X", "X said", "X argues", "as Y shows", "Z notes") in before and after. Source-attribution preservation.

**How it's computed**: Regex pattern matching over a curated set of evidential-frame templates.

**Range / units**: `[0, ∞)` pattern count + signed `change`.

**Interpretation**: Citations and authority attributions are the academic / journalistic load-bearing features that AI revision most commonly strips. A restoration that loses 30%+ of citations has flattened the argumentative structure; the document still reads but the evidence chain is gone.

**Example**: **(NEEDS REFINEMENT: example with documented before/after citation counts on a real restoration.)**

**Caveats**: Pattern-based regex; coverage limited to the curated template set. Custom evidential phrasings won't match.

---

## Phraseology signals

Phraseological signature audits look at multi-word constructions: which phrases the writer uses repeatedly, which slot-frames are characteristic, which idioms appear.

### Lexical bundle survival

**Signal path**: `phraseological_signature_audit` output (`categories.lexical_bundles`) · **Family**: phraseology · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Proportion of high-frequency multi-word bundles (3- and 4-grams with min_count ≥ 2) from the baseline that survive in the target. A measure of preserved phraseological signature.

**How it's computed**: Extract all 3- and 4-grams from the baseline that appear at least twice. Count occurrences in the target. Survival rate is the proportion of baseline bundles found in target.

**Range / units**: `[0, 1]`.

**Interpretation**: High survival means the writer's characteristic phrase-level constructions are preserved. For voice-coherence work this is a positive signal; for AI-detection work, it's the surface vocabulary that an LLM-mimicry attempt would preserve.

**Example**: **(NEEDS REFINEMENT: documented bundles for a specific writer + survival rate on an LLM-mimicry attempt.)**

**Caveats**: Bundle-threshold and bundle-order are configurable (defaults: n=3 and n=4, min_count=2). Lower min_count produces noisier signal.

---

### Slot-frame survival

**Signal path**: `phraseological_signature_audit` output (`categories.slot_frames`) · **Family**: phraseology · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Occurrence of curated variable-slot phrase frames ("not X but Y", "the X of the Y", "what is X is Y") in the target. Detects writer-characteristic structural templates.

**How it's computed**: Regex pattern matching over ~20 hand-curated frame templates with variable slots. Count hits per frame in target.

**Range / units**: `[0, ∞)` count per frame.

**Interpretation**: Each slot-frame is a structural-template tell. The "not X but Y" frame is a Joshua-Greene-style correctio — recognizable across his work. Frame preservation across restoration indicates the writer's structural templates have survived.

**Example**: **(NEEDS REFINEMENT: example mapping a specific writer to their characteristic slot-frames and how they look post-restoration.)**

**Caveats**: ~20 hand-curated frames; coverage depends on curation. A writer's idiosyncratic frames won't match.

---

### Idiom survival

**Signal path**: `phraseological_signature_audit` output (`categories.idioms`) · **Family**: phraseology · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Occurrence of curated English idioms ("all things considered", "by and large", "on the other hand", ~45 entries) in the target. Idiomatic-vocabulary preservation.

**How it's computed**: Regex pattern matching over a curated 45-idiom set.

**Range / units**: `[0, ∞)` count.

**Interpretation**: Idioms are voice-bearing register markers. Their absence in revised prose is a flag for voice-flattening — AI revision often replaces idiom with paraphrase. Their preservation through restoration is a positive signal.

**Example**: **(NEEDS REFINEMENT: documented before/after idiom counts on a real restoration.)**

**Caveats**: Hand-curated 45-idiom set; broader idiomatic coverage requires extension.

---

### Stance-frame survival

**Signal path**: `phraseological_signature_audit` output (`categories.stance_frames`) · **Family**: phraseology · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Occurrence of evaluative stance frames ("it seems to me", "what is X is Y", "to be honest") in the target. Detects writer-characteristic stance signaling.

**How it's computed**: Regex pattern matching over ~8 hand-curated stance-frame templates.

**Range / units**: `[0, ∞)` count per frame.

**Interpretation**: Stance frames are the explicit voice signals — soft-claim markers, predicative emphasis, qualifier frames. Strong personal voice typically uses stance frames at characteristic rates; their disappearance flags depersonalization.

**Example**: **(NEEDS REFINEMENT: writer-specific stance frame catalogue + restoration before/after.)**

**Caveats**: ~8 hand-curated frames; limited coverage.

---

### Hapax-phrase survival

**Signal path**: `phraseological_signature_audit` output (`categories.hapax_phrase_survival`) · **Family**: phraseology · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Proportion of one-of-a-kind n-grams (hapax legomena, appearing exactly once in baseline) that reappear in the target. Captures memory of idiosyncratic phrasing.

**How it's computed**: Extract n-grams that occur exactly once in baseline. Count occurrences in target. Survival rate = survivors / baseline_hapax_count.

**Range / units**: `[0, 1]`.

**Interpretation**: Hapaxes are the writer's most idiosyncratic phrasings. Their reappearance suggests genuine continuation of the same voice; their absence is consistent with either drift or impersonation. As with lexical-bundle survival, the signal is only sharp when paired with syntactic-distance signals.

**Example**: **(NEEDS REFINEMENT: a worked hapax-survival example.)**

**Caveats**: Hapax-order is configurable (default n=3). Lower n produces more candidates but noisier signal.

---

## Punctuation cadence signals

Punctuation patterns are part of the writer's voice — em-dash habits, comma-period ratios, exclamation usage, parenthetical-aside frequency. These signals capture the rhythmic and structural punctuation profile.

### Sentence-final punctuation distribution

**Signal path**: `punctuation_cadence_audit` output (`sentence_final_distribution`) · **Family**: punctuation · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Relative frequency of period, question mark, exclamation point, ellipsis, em-dash, and other sentence-final punctuation. Captures writing-style cadence.

**How it's computed**: Regex-based sentence-boundary detection. For each sentence, identify the final punctuation mark. Normalize to a per-document relative-frequency distribution.

**Range / units**: `[0, 1]` per mark.

**Interpretation**: A writer with high question-density (frequent rhetorical questions) reads as inquisitive; one with high exclamation-density reads as emphatic. The distribution itself is a voice signature; sharp shifts in the distribution flag register change or voice drift.

**Example**: **(NEEDS REFINEMENT: example writers with characteristic punctuation profiles, and what an AI rewrite typically does to those profiles.)**

**Caveats**: Sentence-boundary detection is regex-based and can miss embedded periods (abbreviations, decimal numbers) without exhaustive preprocessing.

---

### Punctuation bigrams

**Signal path**: `punctuation_cadence_audit` output (`punctuation_bigrams`) · **Family**: punctuation · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Most common 2-grams of punctuation marks: comma-period, period-comma, comma-em-dash, etc. The "rhythm signature" of the writer's punctuation.

**How it's computed**: Extract the punctuation-only sequence from the document. Form bigrams over that sequence. Count and rank.

**Range / units**: Count + rank. Top 20 reported by default.

**Interpretation**: A writer who routinely interrupts clauses with em-dashes will show high em-dash-adjacency bigrams. A writer who uses semicolons for elaboration will show semicolon-comma adjacencies. AI-prose punctuation bigrams typically collapse onto a smaller set of safe patterns (period-period, comma-period).

**Example**: **(NEEDS REFINEMENT: documented top-20 punctuation bigrams for a specific writer.)**

**Caveats**: Diagnostic only; no thresholding.

---

### Interruption grammar (parentheticals, em-dashes, appositives)

**Signal path**: `punctuation_cadence_audit` output (`interruption_grammar`) · **Family**: punctuation · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Density of interruption-grammar patterns — parenthetical insertions, em-dash interjections, appositive constructions — per 1000 tokens.

**How it's computed**: Regex for paired parentheses, em-dash sequences, appositive marker patterns. Count per construction type. Normalize per 1000 tokens.

**Range / units**: `[0, ∞)` per 1000 tokens per pattern type.

**Interpretation**: Interruption grammar is a strong voice signal — some writers structure thought through asides (high parenthetical density), others through em-dash-bounded shifts (high em-dash density). AI-smoothed prose tends to flatten interruption grammar; its disappearance is a voice-flattening tell.

**Example**: **(NEEDS REFINEMENT: writer-specific interruption-grammar density profiles.)**

**Caveats**: Em-dash detection requires careful handling of hyphens and dashes; some preprocessing pipelines collapse them.

---

### Comma-period share

**Signal path**: `punctuation_cadence_audit` output (`comma_period_share`) · **Family**: punctuation · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Ratio of period-and-semicolon-class punctuation to comma-class punctuation. Indicates whether the writer favors short sentences (high period share) or long subordinated sentences (high comma share).

**How it's computed**: `(periods + semicolons) / (periods + semicolons + commas)` over the document.

**Range / units**: `[0, 1]`.

**Interpretation**: High comma-period share (close to 1) means short sentences dominate; low share (close to 0) means commas dominate, subordinated long sentences. Authorial style tells: Hemingway prose lives near 1, Henry James prose lives near 0.

**Example**: **(NEEDS REFINEMENT: spectrum of comma-period shares across writer types.)**

**Caveats**: Doesn't distinguish coordinated commas from subordinating ones.

---

## Stance / modality signals

Stance and modality markers signal how the writer relates to their claims: obligation (deontic), possibility (epistemic), hedging, boosting, evidential sourcing, first-person framing, refusal/limitation. Density patterns reveal voice, register, and AI smoothing tells.

### Deontic modality density

**Signal path**: `stance_modality_audit` output (`markers.deontic_modality.density_per_1k`) · **Family**: stance-modality · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Frequency of obligation language (must, shall, should, ought, required, necessary, …) per 1000 tokens.

**How it's computed**: Regex over a curated word/phrase list. Normalize per 1000 tokens.

**Range / units**: `[0, ∞)` per 1000 tokens.

**Interpretation**: High deontic modality is characteristic of policy writing, prescriptive essays, and AI-generated advice. Its presence in fiction is unusual unless the narrator is explicitly didactic.

**Example**: **(NEEDS REFINEMENT: documented deontic-modality density across registers.)**

**Caveats**: Register-dependent. A legal brief naturally has high deontic modality; a literary essay does not.

---

### Epistemic modality density

**Signal path**: `stance_modality_audit` output (`markers.epistemic_modality.density_per_1k`) · **Family**: stance-modality · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Frequency of possibility/uncertainty language (may, might, could, probably, likely, perhaps, …) per 1000 tokens.

**How it's computed**: Regex over a curated word/phrase list. Normalize per 1000 tokens.

**Range / units**: `[0, ∞)` per 1000 tokens.

**Interpretation**: High epistemic modality signals careful, qualified assertion — academic and scientific prose typical. Low epistemic modality combined with high booster density (next entries) is the "over-confident AI assistant" register.

**Example**: **(NEEDS REFINEMENT: documented density across registers.)**

**Caveats**: Register-dependent.

---

### Hedge density

**Signal path**: `stance_modality_audit` output (`markers.hedge.density_per_1k`) · **Family**: stance-modality · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Frequency of hedge markers (somewhat, sort of, kind of, arguably, fairly, …) per 1000 tokens.

**How it's computed**: Regex over a curated word list. Normalize per 1000 tokens.

**Range / units**: `[0, ∞)` per 1000 tokens.

**Interpretation**: Hedges qualify claims — "this is sort of true" rather than "this is true." Combined with booster density (next entry), reveals the writer's confidence calibration. Hedge-then-booster oscillation ("clearly, in some sense, this is obviously true") is documented as an LLM characteristic in the framework's notes.

**Example**: **(NEEDS REFINEMENT: documented hedge-booster oscillation examples in AI prose.)**

**Caveats**: Hedge vocabulary is heavily register-dependent. Academic prose has high hedge density naturally.

---

### Booster density

**Signal path**: `stance_modality_audit` output (`markers.booster.density_per_1k`) · **Family**: stance-modality · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Frequency of assertive intensifiers (clearly, obviously, definitely, certainly, indeed, ...) per 1000 tokens.

**How it's computed**: Regex over a curated word list. Normalize per 1000 tokens.

**Range / units**: `[0, ∞)` per 1000 tokens.

**Interpretation**: Boosters mark the writer's confidence. Heavy booster usage with low hedge density and absent refusal markers is the institutional-AI-assistant signature. Booster-dominant prose with minimal qualification is a compression tell.

**Example**: **(NEEDS REFINEMENT: writer-specific booster patterns vs. AI-prose booster patterns.)**

**Caveats**: Register-dependent.

---

### Evidential density

**Signal path**: `stance_modality_audit` output (`markers.evidential.density_per_1k`) · **Family**: stance-modality · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Frequency of source-of-knowledge markers (seems, suggests, shows, indicates, reveals, demonstrates, ...) per 1000 tokens. Captures the writer's evidentiality framing.

**How it's computed**: Regex over a curated word list. Normalize per 1000 tokens.

**Range / units**: `[0, ∞)` per 1000 tokens.

**Interpretation**: Evidential density is highest in academic and scientific writing. Its presence in fiction or memoir signals a specific narrative register (the analytical narrator).

**Example**: **(NEEDS REFINEMENT: documented density across registers.)**

**Caveats**: Register-dependent.

---

### First-person stance density

**Signal path**: `stance_modality_audit` output (`markers.first_person_stance.density_per_1k`) · **Family**: stance-modality · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Frequency of first-person evaluative frames ("I think", "I believe", "we argue", "it seems to me") per 1000 tokens. Captures how often the writer takes explicit ownership of claims.

**How it's computed**: Regex over a curated word/phrase list. Normalize per 1000 tokens.

**Range / units**: `[0, ∞)` per 1000 tokens.

**Interpretation**: Personal essays, memoir, and opinion writing have high first-person stance density. Academic prose suppresses it (passive constructions preferred). AI-prose rewrites tend to flatten first-person stance — the "depersonalization" signature.

**Example**: A personal essay measured at 8 first-person stance markers per 1000 tokens drops to 1 per 1000 in an AI-clean-up rewrite. The voice has been institutionally flattened.

**Caveats**: Register-dependent.

---

### Refusal / negation density

**Signal path**: `stance_modality_audit` output (`markers.refusal.density_per_1k`) · **Family**: stance-modality · **Polarity**: — · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Frequency of careful refusal/limitation phrases ("this does not show", "cannot conclude", "is not enough to establish", "I am skeptical") per 1000 tokens.

**How it's computed**: Regex over a curated phrase list. Normalize per 1000 tokens.

**Range / units**: `[0, ∞)` per 1000 tokens.

**Interpretation**: Refusal markers are the writer's checks on their own claims. A scrupulous essayist will use them frequently. AI-generated prose typically *under*-uses refusal — it over-claims rather than under-claims, which the framework's notes flag as a compression signature.

**Example**: **(NEEDS REFINEMENT: documented refusal-marker absence in AI prose vs. presence in human academic / essay prose.)**

**Caveats**: Heavily register-dependent. Hand-curated phrase list with limited coverage.

---

## Bigram-KL signals

### Per-bigram KL contribution

**Signal path**: `bigram_diff` output (`top_contributors`) · **Family**: bigram-kl · **Polarity**: ↑ (high |contribution| = high divergence impact) · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Per-bigram decomposition of the POS-bigram KL divergence (Tier 2). Shows which specific syntactic patterns drive the overall divergence between target and baseline.

**How it's computed**: For each POS-bigram b: `contribution(b) = p(b) × log₂(p(b) / q(b))` where p is target frequency and q is baseline frequency. Sort by absolute contribution descending. Report top N.

**Range / units**: KL contribution in bits × probability (signed). Magnitude indicates impact; sign indicates direction (positive = over-used in target relative to baseline; negative = under-used).

**Interpretation**: Top-contributor bigrams are the syntactic patterns most responsible for the overall KL divergence. A target with high KL but no concentrated contributors means dispersed shift; a target with high KL driven by 5-10 bigrams means specific template changes.

**Example**: A blog post rewritten by Claude shows KL = 0.18 driven by 60% contribution from three bigrams: DET-NOUN, ADP-DET, and PRON-VERB. The model has over-used those patterns relative to the writer's baseline — concrete, fixable evidence of syntactic templating.

**Caveats**: Requires spaCy POS-tagging and a baseline. Top-N is configurable (default 20).

---

## Repetition signals

### Vocabulary repetition ratio

**Signal path**: `repetition_audit` output (`candidates[i].ratio`) · **Family**: repetition · **Polarity**: ↑ (ratio > 1 = over-represented) · **Status**: provisional · **Calibration anchor**: None

**What it measures**: For each candidate word, the per-1000-token frequency in target divided by the per-1000-token frequency in baseline. Flags over-representation.

**How it's computed**: `target_freq = (count_in_target / target_tokens) × 1000`; `baseline_freq = (count_in_baseline / baseline_tokens) × 1000`; `ratio = target_freq / (baseline_freq + smoothing)`.

**Range / units**: `[0, ∞)`. Ratios above 1.0 mean over-representation.

**Interpretation**: A ratio of 3 means the target uses the word 3× more frequently than the baseline. For revision work, identifies words the writer (or LLM editor) has over-leaned on. For AI-detection work, identifies the LLM's signature vocabulary.

**Example**: A literary essay shows ratio = 4.2 for "moreover" relative to the writer's baseline — strong AI-rewrite signature.

**Caveats**: Filtered by `min_ratio ≥ 1.0` by default (over-representation only; under-representation also matters but is surfaced separately). Excludes function words and short words (< 4 characters) by default.

---

### Cluster maximum (repetition peak)

**Signal path**: `repetition_audit` output (`candidates[i].cluster_max`, `cluster_window`) · **Family**: repetition · **Polarity**: ↑ (higher = more concentrated repetition) · **Status**: provisional · **Calibration anchor**: None

**What it measures**: Maximum number of occurrences of a candidate word in any sliding window. Captures repetition *clustering* (the word recurs in a small region) vs. distributed repetition (the word recurs evenly across the document).

**How it's computed**: Slide a window of size 300 tokens (default) through the document. Count occurrences of the candidate word in each window. Report the maximum count.

**Range / units**: `[0, ∞)` count.

**Interpretation**: A `cluster_max` of 7 in a 300-token window means the word appeared 7 times within 300 consecutive tokens — repetition concentrated in one passage rather than spread evenly. Useful for revision work: cluster_max identifies which words need de-repetition in specific passages, not as a global vocabulary issue.

**Example**: **(NEEDS REFINEMENT: typical cluster_max values for natural repetition vs. excessive concentrated repetition.)**

**Caveats**: Window size is configurable; default 300 tokens.

---

## Totals

| Family | Count |
|---|---|
| tier1-variance | 9 |
| tier2-syntax | 3 |
| tier3-trajectory | 4 |
| tier4-surprisal | 3 |
| voice-distance | 2 |
| voice-drift | 2 |
| pov-voice | 2 |
| mimicry | 2 |
| semantic-preservation | 3 |
| phraseology | 5 |
| punctuation | 4 |
| stance-modality | 7 |
| bigram-kl | 1 |
| repetition | 2 |
| **TOTAL** | **49** |

Note on omitted items: a simple type-token ratio (TTR) entry is documented in the inventory but isn't a standalone signal — it's an internal computation that feeds MATTR. It is not surfaced in framework output. Composite aggregators (`manuscript_audit`, `paragraph_audit`, `sliding_window_heatmap`) pass through the above signals at different aggregation levels; they are not separate signals.

---

## Related references

  * `internal/SPEC_surprisal_signal.md` — math behind Tier 4.
  * `internal/SPEC_surprisal_model_choice.md` — causal-LM selection for Tier 4.
  * `internal/SPEC_embedding_model_choice.md` — embedding backend for Tier 3.
  * `references/aic-flags.md` — restoration-time flag taxonomy that consumes these signals.
  * `references/manifest-schema.md` — baseline data format that drives voice-distance / voice-drift / POV signals.
  * `references/calibration-findings-2026-05-10.md` and `-2026-05-11-mage.md` — empirical calibration evidence for tier-1 bands.
  * `scripts/calibration/PROVENANCE.md` — Stylometry-to-the-people policy. The shipped signal bands are PROVISIONAL regardless of calibration anchor.
