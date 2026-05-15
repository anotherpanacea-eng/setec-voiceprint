# AIC-8 / AIC-9 test fixtures

Small synthetic fixtures for unit-testing the AIC-8 (Aesthetic Authority Laundering) and AIC-9 (Closure Inflation) detectors. These are not calibration-grade corpora; they exist to verify that the detectors return the expected verdicts on hand-curated positive and negative examples.

The calibration-grade corpus (four labeled fixture corpora: idiom negatives, AI-image-conjunction positives, aphoristic-essayist negatives, AI-rewrite positives) is a roadmap follow-on; see `ROADMAP.md` "Calibration corpus track" for scope.

## Files

### AIC-9 (kicker density)

- `kicker_aphoristic_positive.md` — 6 paragraphs, 5 ending with kicker-shaped sentences. Tests the high-density-kicker case. Expected `kicker_density ≈ 0.833`.
- `kicker_normal_negative.md` — 6 paragraphs, none ending with a kicker-shaped sentence. Tests the low-density baseline. Expected `kicker_density ≈ 0.0`.
- `kicker_mixed_clustered.md` — 7 paragraphs where kickers cluster in one passage rather than distributing across the document. Tests `spacing_variance` behavior on clustered kickers.

### AIC-8 (image conjunction + prestige metaphor)

- `idiom_negative.md` — 4 paragraphs of conventional collocations (heavy burden, sharp decline, hard problem, deep meaning, warm reception, etc.). Tests that the compound filter (concreteness gap + low embedding similarity) does NOT trigger on idioms whose word-pairs have high embedding similarity.
- `ai_image_conjunction_positive.md` — 4 paragraphs of canonical AI-style image conjunctions ("the machinery of grief", "the architecture of attention", "the grammar of desire", "the topology of memory", etc.). Tests that the compound filter SHOULD trigger and that domain-scatter entropy is high (different prestige domain per conjunction).
- `concentrated_metaphor_negative.md` — 3 paragraphs that use image conjunctions concentrated around a SINGLE prestige domain (machinery / mechanism / engine, all from the machinery domain). Tests the scatter-entropy diagnostic: high conjunction density but LOW domain-scatter entropy (single domain) should NOT fire the prestige-metaphor flag.

The synthetic prose is illustrative; no operator should treat these texts as ground-truth examples of either pattern.
