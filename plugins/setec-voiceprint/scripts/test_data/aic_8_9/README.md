# AIC-8 / AIC-9 test fixtures

Small synthetic fixtures for unit-testing the AIC-8 (Aesthetic Authority Laundering) and AIC-9 (Closure Inflation) detectors. These are not calibration-grade corpora; they exist to verify that the detectors return the expected verdicts on hand-curated positive and negative examples.

The calibration-grade corpus (four labeled fixture corpora: idiom negatives, AI-image-conjunction positives, aphoristic-essayist negatives, AI-rewrite positives) is a roadmap follow-on; see `ROADMAP.md` "Calibration corpus track" for scope.

## Files

- `kicker_aphoristic_positive.md` — 6 paragraphs, each ending with a kicker-shaped sentence. Tests the high-density-kicker case. Expected `kicker_density ≈ 1.0`.
- `kicker_normal_negative.md` — 6 paragraphs, none ending with a kicker-shaped sentence. Tests the low-density baseline. Expected `kicker_density ≈ 0.0`.
- `kicker_mixed_clustered.md` — 8 paragraphs where kickers cluster in one passage rather than distributing across the document. Tests `spacing_variance` behavior on clustered kickers.

The synthetic prose is illustrative; no operator should treat these texts as ground-truth examples of either pattern.
