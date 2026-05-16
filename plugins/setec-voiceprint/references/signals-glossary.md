# SETEC Voiceprint: Stylometric Signals Glossary

Terse reference for every analytical measurement SETEC computes on prose. 56 signals across 14 families.

This document is the framework's authoritative index of names, signal paths, polarity, and calibration status. Long-form pedagogy — worked before/after examples, interpretive guidance, register-specific case studies, the rhetorical-bankruptcy framing for the AIC family — lives in the framework's external primer (in development; see the Glass-Box Stylometry Sequence for the maintained track). Operators wanting more than the metadata block should consult that.

## Reading the entries

Each entry block carries the signal's metadata on a single line:

    `signal.path` · family · polarity · **status** · provenance note

**Polarity arrows.** `↓` low = more AI-like; `↑` high = more AI-like; `↔` both extremes diagnostic; `—` no polarity (diagnostic / baseline-relative only).

**Status (4-tier + 1, retiered v1.66.0 per `internal/SPEC_calibration_status_retier.md`).**

- **calibrated** — Corpus-tested with reported performance metrics (FPR, TPR, AUC, ROC, or distribution moments). Provenance cites the corpus + version + reported metrics. Per the Stylometry-to-the-people policy, the framework ships no calibrated thresholds as load-bearing defaults; the registry's calibrated count is expected to be 0 until operators run their own calibration locally.
- **literature_anchored** — Based on published metric behavior in peer-reviewed or recognized open-access work. Provenance cites the publication.
- **empirically_oriented** — Based on local experimentation (`voice_profile.py` aggregation, internal fixture testing, the `references/calibration-findings-*.md` track). Provenance cites the local source.
- **heuristic** — Plausible working value awaiting validation. Provenance is `null`. Default for new signals. The §5.4 calibration corpus track in `ROADMAP.md` is the named promotion path: `heuristic` → `calibrated` as corpus data lands.
- **structural_only** — Feeds downstream signals; not thresholded directly (e.g., `function_word_ratio` feeds Burrows Delta).

**Length floors and dependencies** are noted per entry where they matter.

## Contents

- [Tier 1: Variance signals (9)](#tier-1-variance-signals)
- [Tier 2: Syntactic signals (3)](#tier-2-syntactic-signals)
- [Tier 3: Trajectory signals (4)](#tier-3-trajectory-signals)
- [Tier 4: Surprisal signals (3)](#tier-4-surprisal-signals)
- [AIC-7: Discourse Leak / Assistant-Register Intrusion (4)](#aic-7-discourse-leak)
- [AIC-8: Aesthetic Authority Laundering (2)](#aic-8-aesthetic-authority-laundering)
- [AIC-9: Closure Inflation (1)](#aic-9-closure-inflation)
- [Voice-distance signals (2)](#voice-distance-signals)
- [Voice-drift signals (2)](#voice-drift-signals)
- [POV-voice signals (2)](#pov-voice-signals)
- [Mimicry / cosplay signals (2)](#mimicry-cosplay-signals)
- [Semantic preservation signals (3)](#semantic-preservation-signals)
- [Phraseology signals (5)](#phraseology-signals)
- [Punctuation cadence signals (4)](#punctuation-cadence-signals)
- [Stance / modality signals (7)](#stance-modality-signals)
- [Bigram-KL signals (1)](#bigram-kl-signals)
- [Repetition signals (2)](#repetition-signals)
- [Totals](#totals)

---

## Tier 1: Variance signals

Layer A; cheapest signals; no baseline required. Six of nine carry local empirical anchors from the `editlens_v1_findings_2026-05-10` calibration run.

### Sentence-length burstiness (B)

`tier1.sentence_length.burstiness_B` · tier1-variance · ↓ · **empirically_oriented** · editlens_v1_findings_2026-05-10 (da_AUC 0.683, FPR 0.93%, TPR 7.0%)

Normalized sentence-length variance: `B = (SD − mean) / (SD + mean)` over per-sentence word counts. Range `[-1, 1]`. Lower = more uniform sentence lengths. Length floor 200 tokens.

### Sentence-length standard deviation

`tier1.sentence_length.sd` · tier1-variance · ↓ · **empirically_oriented** · editlens_v1_findings_2026-05-10 (da_AUC 0.695)

Population SD of per-sentence word counts. Range `[0, ∞)` words. Length floor 200 tokens. Heavily register-dependent — the strongest standalone variance signal in the 2026-05-10 EditLens findings, but use the personal-baseline z-score over the raw absolute threshold.

### Moving-average type-token ratio (MATTR)

`tier1.mattr.value` · tier1-variance · ↓ · **literature_anchored** · mattr_literary_fiction_baseline_window_50

Slide a 50-token window, compute type/token in each, average. Range `[0, 1]`. Length floor 300 tokens. Literary-fluent fiction sits 0.70-0.82 at window 50.

### Measure of textual lexical diversity (MTLD)

`tier1.mtld` · tier1-variance · ↓ · **heuristic**

Tokens needed before TTR drops below 0.72; forward/backward-pass average. Range `[0, ∞)` tokens. Length floor 500. Noisy below the floor.

### Yule's K

`tier1.yules_k` · tier1-variance · ↑ · **heuristic**

`K = 10⁴ × (Σcount² − N) / N²` over the word-frequency distribution. Range `[0, ∞)`. Length floor 500 tokens. Sensitive to high-frequency-word outliers.

### Shannon entropy

`tier1.shannon_entropy_bits` · tier1-variance · ↓ · **literature_anchored** · shannon_entropy_native_fiction_literature

Information entropy of word-frequency distribution: `H = −Σ p_i log₂ p_i`. Range `[0, log₂(vocab_size)]` bits. Length floor 2000 tokens. Native English fiction sits 9.5-10.5 in the literature; threshold deliberately set low to spare focused-vocabulary registers.

### Flesch-Kincaid grade-level standard deviation (FKGL SD)

`tier1.fkgl.sd` · tier1-variance · ↓ · **empirically_oriented** · editlens_v1_findings_2026-05-10 (da_AUC 0.635)

Per-sentence FKGL = `0.39 × W + 11.8 × (Sy/W) − 15.59` then SD across sentences. Range `[0, ∞)` grade levels. Length floor 200 tokens. Human prose 3-5; LLM 0.8-1.5.

### Connective density

`tier1.connective_density.per_1000_tokens` · tier1-variance · ↑ · **empirically_oriented** · editlens_v1_findings_2026-05-10 (da_AUC 0.529)

Discourse-marker count (≈50 curated connectives: furthermore, moreover, however, …) per 1000 tokens. Range `[0, ∞)`. Length floor 200 tokens. Academic prose elevates naturally; calibrate against register baseline.

### Function-word ratio

`tier1.function_words.function_word_ratio` · tier1-variance · — · **structural_only**

Proportion of tokens in the curated FUNCTION_WORDS set. Range `[0, 1]`. Not thresholded — feeds Burrows Delta voice-distance computations. Stable across registers within an author (typically 0.45-0.55).

---

## Tier 2: Syntactic signals

Layer A continuation; requires spaCy (`en_core_web_sm` or larger).

### POS-bigram entropy

`tier2.pos_bigrams.entropy_bits` · tier2-syntax · — · **empirically_oriented** · voice_profile_aggregation_v1

Shannon entropy over POS-bigram (e.g., DET-NOUN, ADJ-NOUN) frequency distribution. Range `[0, log₂(unique_bigrams)]` bits. Typically 7-9 for English. Feeds POS-bigram KL.

### POS-bigram KL divergence

`baseline_divergences.pos_bigrams.kl` · tier2-syntax · ↑ · **literature_anchored** · pos_bigram_kl_distributional_diagnostics

`KL(target ‖ baseline) = Σ p(b) log₂(p(b)/q(b))` with Laplace smoothing. Range `[0, ∞)` bits. Length floor 500 tokens. Cross-human KL typically < 0.05; human-vs-LLM 0.10-0.30. Requires baseline.

### Mean dependency distance SD (MDD SD)

`tier2.mdd.sd` · tier2-syntax · ↓ · **empirically_oriented** · editlens_v1_findings_2026-05-10 (da_AUC 0.585)

Per-sentence mean dependency distance via spaCy parse; then SD across sentences. Range `[0, ∞)` tokens. Length floor 200 tokens. Minimum 2 sentences.

---

## Tier 3: Trajectory signals

Layer A continuation; requires sentence-transformers (preferred) or TF-IDF fallback.

### Adjacent-sentence cosine, mean

`tier3.adjacent_cosine.mean` · tier3-trajectory · ↑ · **heuristic**

Mean cosine similarity between sentence embeddings of adjacent sentences. Range `[0, 1]`. Length floor 2 sentences. Higher = tighter cohesion (an LLM tell).

### Adjacent-sentence cosine, standard deviation

`tier3.adjacent_cosine.sd` · tier3-trajectory · ↓ · **empirically_oriented** · editlens_v1_findings_2026-05-10 (da_AUC 0.681)

SD of adjacent-sentence cosines. Range `[0, ∞)`. Lower = uniform transitions. The strongest tier-3 AI signal; pair with mean for the joint diagnostic.

### Semantic trajectory cosine series

`semantic_trajectory_audit:window_trajectories[i].cosine_to_next` · tier3-trajectory · ↔ · **heuristic**

Per-window cosine similarities across paragraph-level windows. Range `[0, 1]` per pair. Diagnostic shape; not thresholded.

### Semantic trajectory slope

`semantic_trajectory_audit:trajectory_analysis.slope` · tier3-trajectory · ↔ · **heuristic**

Linear regression of adjacent-window cosine against window position. Slope ∈ ℝ; `R² ∈ [0, 1]`. Diagnostic, not pass/fail.

---

## Tier 4: Surprisal signals

Per-token surprisal under a small causal LM; opt-in via `--tier4`. All three signals share the DivEye literature anchor (Basani & Chen, TMLR 2026). Requires transformers + torch + a base causal LM (default TinyLlama).

### Per-token surprisal mean

`tier4.surprisal.mean` · tier4-surprisal · ↓ · **literature_anchored** · diveye_basani_chen_tmlr_2026

Arithmetic mean of `−log₂ P(token_i | prefix)` over the document. Bits per token. Length floor 300. AI-generated text tends near the LM's mode → lower mean.

### Per-token surprisal SD

`tier4.surprisal.sd` · tier4-surprisal · ↓ · **literature_anchored** · diveye_basani_chen_tmlr_2026

Sample SD of the per-token surprisal series. Bits. Length floor 300. The most sensitive of the three Tier 4 signals per DivEye.

### Per-token surprisal autocorrelation, lag 1

`tier4.surprisal.autocorrelation.lag_1` · tier4-surprisal · ↑ · **literature_anchored** · diveye_basani_chen_tmlr_2026

`ACF(1) = Cov(X_t, X_{t+1}) / Var(X)` over the surprisal series. Range `[-1, 1]`. Length floor 500 tokens (≥ 30-token series). AI prose tends positive → predictability streaks.

---

## AIC-7: Discourse Leak

Named-pattern density from `aic_pattern_audit.py`. Regex-based; cheap. Enable via `variance_audit.py --aic7`.

### Correctio density

`patterns.correctio.density_per_1k` · aic-7-discourse-leak · ↑ · **heuristic**

Density of "not X, but Y" inline + "It is not X. It is Y" frames per 1000 tokens. Length floor 400. Schnell case-study anchor (15.8/1k for one essayist) is a single-author empirical anchor, not a calibrated band; current threshold 12.0 is conservative below it.

### Triplet density

`patterns.triplet.density_per_1k` · aic-7-discourse-leak · ↑ · **heuristic**

Density of 3- or 4-item comma-and lists ("X, Y, and Z") per 1000 tokens. Length floor 400.

### Manifesto cadence density

`patterns.manifesto_cadence.density_per_1k` · aic-7-discourse-leak · ↑ · **heuristic**

Density of 3+ consecutive sentences with anaphoric heads per 1000 tokens. Length floor 400.

### Professional parallel stack density

`patterns.professional_parallel_stack.density_per_1k` · aic-7-discourse-leak · ↑ · **heuristic**

Density of 2+ adjacent paragraphs sharing an "A X may Y" opening clause structure per 1000 tokens. Length floor 400.

---

## AIC-8: Aesthetic Authority Laundering

Image-conjunction and prestige-metaphor scatter detectors. Enable via `variance_audit.py --aic8`. Requires spaCy + `en_core_web_md` or `_lg` (word vectors) + Brysbaert concreteness norms (ship in-repo).

### Image conjunction density

`aic_8_9.image_conjunction_density.value` · aic-8-laundering · ↑ · **heuristic**

Abstract-concrete word pairs from dependency parse, filtered by concreteness gap ≥ T1 (default 2.5) AND embedding cosine ≤ T2 (default 0.4). Density per 1000 tokens. Length floor 400. The compound filter isolates AI image conjunctions from conventional idioms; spec's T1/T2 don't crisply separate the two on Brysbaert data — calibration is pending.

### Prestige-metaphor scatter

`aic_8_9.prestige_metaphor_density.domain_scatter_entropy` · aic-8-laundering · ↑ · **heuristic**

Normalized Shannon entropy of prestige-domain distribution across detected image conjunctions. Range `[0, 1]`. Length floor 400. Domain classification: hardcoded list of 18 spec-named domains (architecture, grammar, machinery, …) + WordNet hypernym fallback. High entropy + elevated density = metaphor confetti.

---

## AIC-9: Closure Inflation

Kicker-shape paragraph-final detector. Enable via `variance_audit.py --aic9`. Regex with optional spaCy POS check.

### Kicker density

`aic_8_9.kicker_density.value` · aic-9-closure-inflation · ↑ · **heuristic**

Proportion of paragraphs whose final sentence is kicker-shaped: ≤ 15 words, declarative period-final, no digits, no proper nouns. Range `[0, 1]`. Length floor 400 tokens. Spec threshold 0.25 sits above register-typical contemporary essay (~0.08).

---

## Voice-distance signals

Compare a draft against a personal or register-matched baseline. From `voice_distance.py`.

### Burrows Delta (function-word)

`voice_distance:deltas.function_words` · voice-distance · ↑ · **empirically_oriented** · voice_profile_aggregation_v1

Euclidean / Mahalanobis-style norm of per-function-word z-scores against baseline. Standardized distance units. Requires baseline ≥ 20K words; topic-and-register-matched for tight bounds.

### Per-feature cosine distance

`voice_distance:cosines.function_words` (and other feature families) · voice-distance · ↑ · **empirically_oriented** · voice_profile_aggregation_v1

`1 − cosine_similarity(draft, baseline)` per feature-family vector. Range `[0, 1]`. Complement to Burrows Delta; cosine catches relative-shape changes, Delta catches magnitude changes.

---

## Voice-drift signals

Cross-period stylometric variance from `voice_drift_tracker.py`. Requires date-tagged baseline.

### Voice drift (cross-period CV)

`voice_drift_tracker:drifting_features` · voice-drift · ↑ · **heuristic**

Per-feature coefficient of variation across time periods: `SD(period_means) / mean(period_means)`. Range `[0, ∞)`. High CV = drifting feature.

### Voice stability

`voice_drift_tracker:stable_features` · voice-drift · ↓ · **heuristic**

Inverse of voice drift: features with low cross-period CV. The durable idiolect surface.

---

## POV-voice signals

Multi-POV cross-character comparison from `pov_voice_profile.py`. Requires manifest with `pov` field.

### POV voice-distance matrix

`pov_voice_profile:pairwise_distances` · pov-voice · ↑ · **heuristic**

Pairwise Burrows Delta + cosine distance between POV characters. Mahalanobis units + `[0, 1]`. Requires ≥ 5K words per POV for stable estimates.

### POV voice-collapse verdict

`pov_voice_profile:voice_collapse_verdict` · pov-voice · ↑ · **heuristic**

Boolean per pair: Delta below heuristic threshold flags collapsed POVs. Genre-dependent threshold.

---

## Mimicry / cosplay signals

From `mimicry_cosplay_audit.py`. Joint condition with voice-distance signals.

### Lexical mimicry survival rate

`mimicry_cosplay_audit:lexical_survival.survival_rate` · mimicry · ↑ · **heuristic**

Proportion of baseline signature n-grams reappearing in target. Range `[0, 1]`. Diagnostic only when paired with syntactic Delta.

### Syntactic mimicry (POS-trigram Delta)

`mimicry_cosplay_audit:syntactic_delta.overall` · mimicry · ↑ · **heuristic**

Burrows Delta on POS-trigram relative frequencies. Standardized distance units. High lexical survival + high syntactic Delta = cosplay signature.

---

## Semantic preservation signals

Before/after restoration checks from `semantic_preservation_check.py`. Diagnostic; no polarity.

### Claim inventory preservation

`semantic_preservation_check:preservation.claim_inventory.before_count` (+ `after_count`, `change`) · semantic-preservation · — · **heuristic**

Approximate declarative-sentence count before vs. after. Regex-based proxy for propositional content.

### Named-entity preservation

`semantic_preservation_check:preservation.named_entities.*` · semantic-preservation · — · **heuristic**

Count of proper-noun named entities (PERSON/ORG/GPE) before vs. after. spaCy NER preferred; regex fallback.

### Citation / authority preservation

`semantic_preservation_check:preservation.citations_and_authorities.*` · semantic-preservation · — · **heuristic**

Count of evidential frames ("according to X", "X argues", "Y shows") before vs. after. Regex-based.

---

## Phraseology signals

From `phraseological_signature_audit.py`. Multi-word construction inventory.

### Lexical bundle survival

`phraseological_signature_audit:categories.lexical_bundles` · phraseology · — · **heuristic**

Proportion of baseline 3-/4-gram bundles (`min_count ≥ 2`) reappearing in target. Range `[0, 1]`.

### Slot-frame survival

`phraseological_signature_audit:categories.slot_frames` · phraseology · — · **heuristic**

Hits per writer-characteristic variable-slot frame ("not X but Y", "the X of the Y"). ~20 curated frames.

### Idiom survival

`phraseological_signature_audit:categories.idioms` · phraseology · — · **heuristic**

Hits per curated English idiom (~45 entries: "by and large", "on the other hand"). Voice-bearing register markers.

### Stance-frame survival

`phraseological_signature_audit:categories.stance_frames` · phraseology · — · **heuristic**

Hits per evaluative stance frame ("it seems to me", "to be honest"). ~8 curated frames.

### Hapax-phrase survival

`phraseological_signature_audit:categories.hapax_phrase_survival` · phraseology · — · **heuristic**

Proportion of one-of-a-kind baseline 3-grams reappearing in target. Range `[0, 1]`. Pair with syntactic-distance for cosplay adjudication.

---

## Punctuation cadence signals

From `punctuation_cadence_audit.py`. Voice-bearing punctuation profile.

### Sentence-final punctuation distribution

`punctuation_cadence_audit:sentence_final_distribution` · punctuation · — · **heuristic**

Relative frequency of period / question / exclamation / ellipsis / em-dash / quote at sentence boundary. Range `[0, 1]` per mark.

### Punctuation bigrams

`punctuation_cadence_audit:punctuation_bigrams` · punctuation · — · **heuristic**

Top-20 most common adjacent punctuation pairs. Diagnostic only.

### Interruption grammar

`punctuation_cadence_audit:interruption_grammar` · punctuation · — · **heuristic**

Per-1000-token density of parenthetical / em-dash / appositive interruptions. Range `[0, ∞)` per pattern.

### Comma-period share

`punctuation_cadence_audit:comma_period_share` · punctuation · — · **heuristic**

`(periods + semicolons) / (periods + semicolons + commas)`. Range `[0, 1]`. Hemingway near 1, James near 0.

---

## Stance / modality signals

From `stance_modality_audit.py`. Per-marker densities.

### Deontic modality density

`stance_modality_audit:markers.deontic_modality.density_per_1k` · stance-modality · — · **heuristic**

Frequency of obligation language (must, shall, ought, required) per 1000 tokens.

### Epistemic modality density

`stance_modality_audit:markers.epistemic_modality.density_per_1k` · stance-modality · — · **heuristic**

Frequency of possibility / uncertainty language (may, might, could) per 1000 tokens.

### Hedge density

`stance_modality_audit:markers.hedge.density_per_1k` · stance-modality · — · **heuristic**

Frequency of hedge markers (somewhat, sort of, arguably) per 1000 tokens.

### Booster density

`stance_modality_audit:markers.booster.density_per_1k` · stance-modality · — · **heuristic**

Frequency of assertive intensifiers (clearly, obviously, definitely) per 1000 tokens.

### Evidential density

`stance_modality_audit:markers.evidential.density_per_1k` · stance-modality · — · **heuristic**

Frequency of source-of-knowledge markers (seems, suggests, shows, indicates) per 1000 tokens.

### First-person stance density

`stance_modality_audit:markers.first_person_stance.density_per_1k` · stance-modality · — · **heuristic**

Frequency of first-person evaluative frames ("I think", "we argue") per 1000 tokens.

### Refusal / negation density

`stance_modality_audit:markers.refusal.density_per_1k` · stance-modality · — · **heuristic**

Frequency of careful refusal/limitation phrases ("cannot conclude", "this does not show") per 1000 tokens.

---

## Bigram-KL signals

From `bigram_diff.py`. Per-bigram decomposition of POS-bigram KL.

### Per-bigram KL contribution

`bigram_diff:top_contributors` · bigram-kl · ↑ · **heuristic**

Per-bigram `p(b) × log₂(p(b)/q(b))` over target vs. baseline. Bits × probability (signed). Top-N reported (default 20).

---

## Repetition signals

From `repetition_audit.py`. Vocabulary over-representation vs. baseline.

### Vocabulary repetition ratio

`repetition_audit:candidates[i].ratio` · repetition · ↑ · **heuristic**

`target_freq / baseline_freq` per candidate word, normalized per 1000 tokens. Range `[0, ∞)`. Filtered to `min_ratio ≥ 1.0`.

### Cluster maximum

`repetition_audit:candidates[i].cluster_max` · repetition · ↑ · **heuristic**

Maximum occurrences of a word in any 300-token sliding window. Diagnostic for concentrated vs. distributed repetition.

---

## Totals

| Family | Count |
|---|---|
| tier1-variance | 9 |
| tier2-syntax | 3 |
| tier3-trajectory | 4 |
| tier4-surprisal | 3 |
| aic-7-discourse-leak | 4 |
| aic-8-laundering | 2 |
| aic-9-closure-inflation | 1 |
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
| **TOTAL** | **56** |

## Calibration-status distribution (v1.66.0)

| Status | Count | Notes |
|---|---|---|
| calibrated | 0 | Per Stylometry-to-the-people policy; no corpus-derived thresholds shipped as load-bearing defaults |
| literature_anchored | 6 | mattr, shannon_entropy, surprisal_mean / sd / acf_lag1, pos_bigram_kl |
| empirically_oriented | 8 | The six 2026-05-10 EditLens-measured variance signals + pos_bigram_entropy + Burrows Delta + per_feature_cosine |
| heuristic | 41 | Everything else; the long tail of AIC + phraseology + punctuation + stance + diagnostic checkpoints |
| structural_only | 1 | function_word_ratio |
| **TOTAL** | **56** |

## Related references

- `references/aic-flags.md` — pattern-resolution layer for the 9 AIC flag families.
- `references/source-triage.md` — voice-attribution layer; per-instance refinement once frequency-elevation flags fire.
- `references/laundering-vocabulary.md` — the four laundering moves (calibration / procedural / audit / aesthetic-authority).
- `references/calibration-findings-2026-05-10.md` — the EditLens v1 empirical anchor for six variance signals.
- `scripts/calibration/PROVENANCE.md` — Stylometry-to-the-people policy statement.
- `internal/SPEC_calibration_status_retier.md` — the v1.66.0 retier spec this glossary reflects.
- External primer (in development; Glass-Box Stylometry Sequence) — long-form pedagogy.
