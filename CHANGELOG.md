# Changelog

All notable changes to this project. Format roughly follows [Keep a Changelog](https://keepachangelog.com/), with versions to be tagged when commits land. Pre-tag entries are grouped under "Unreleased" until the first git tag is cut.

## Unreleased

This is the work shipped during the development burst that brought the framework from "MVP plus voiceprint" to "validation-spine prerequisites in place." Once committed in the slice plan documented in `internal/COMMIT_PLAN.md`, this section will be tagged and dated.

### Added

- `scripts/manifest_validator.py`: schema and integrity checks for `corpus_manifest.jsonl`. Per-entry checks (required fields, enum-valued fields, `use` is a list, `word_count` non-negative, unknown field names flagged), cross-entry checks (duplicate `id`, missing-on-disk path, two-ids-one-file, `use: validation` + `split: baseline` contradiction, `use: voice_profile` privacy ratchet, provenance contradictions). JSON output, markdown report, importable `validate_manifest(path) -> dict` for downstream gating.
- `scripts/manuscript_repetition_audit.py`: manuscript-aggregate vocabulary audit. Composes the chapter splitters from `manuscript_audit.py` with the per-document scorer from `repetition_audit.py`. Output: dispersed habit-vocabulary table (words flagged in many chapters at moderate ratio), concentrated repetition table (one or two chapters at high peak ratio), per-chapter top-N. JSON preserves per-chapter and aggregated structures separately.
- `scripts/chapter_distinctiveness_audit.py`: leave-one-out internal-baseline vocabulary audit. For each chapter, baseline is the union of all other chapters; surfaces words distinctive to one chapter rather than habit-vocabulary dispersed across the manuscript. Default `--min-ratio 1.5` because "distinctive" is a stronger claim than "barely over-represented."
- Sliding-window mode in `scripts/variance_audit.py`. New `split_into_windows()`, `audit_windows()`, and `format_windows_dashboard()` plus three CLI flags (`--window-size`, `--window-stride`, `--window-only`). Catches localized compression that whole-document scores would mask: a synthetic document combining clean and AI-flavored prose averages to "Lightly smoothed" at whole scope; the window scan correctly localizes the compression to the AI-flavored sections.
- POS-bigram KL/JSD divergence against baseline aggregate, in `scripts/variance_audit.py`. New `pos_bigram_distance()` helper with Laplace smoothing on the union of bigrams; new `compare_distributions()` entry point keeps the existing `compare_to_baseline()` z-score path unchanged.
- Feature-cluster mode for `scripts/voice_distance.py`. New `FUNCTION_WORD_CLUSTERS` registry (26 predefined syntactic groupings), new `compute_clusters()` aggregator reporting mean signed z, direction consistency, and top contributors per cluster. Catches authorial fingerprints that single-feature top-N misses when a cluster of related features moves together at moderate magnitudes.
- Genre tolerance quick-reference table in `references/aic-flags.md`. 7×6 grid (seven AIC flags by six genres) with three tolerance bands (Low, Med, High) plus N/A and six footnotes for cells where a single band misrepresents the call (AIC-1 in testimony; AIC-2 in testimony; AIC-7 in essay/testimony; AIC-3 in blog; AIC-7 in blog; AIC-3 in testimony).
- `task_surface` field on every script's JSON output and markdown header. Values: `smoothing_diagnosis` (variance/manuscript/repetition audits), `voice_coherence` (voice_distance, voice_profile), `validation` (manifest_validator and the future validation_harness). Each script exports a module-level `TASK_SURFACE` constant for downstream importers.
- Per-n character n-gram families (`char_ngrams_3`, `char_ngrams_4`, `char_ngrams_5`) in `scripts/stylometry_core.py`. Each family normalizes within its own n, has its own selection cap, and contributes its own Burrows-Delta and cosine distance. Replaces the prior unified family that mixed all three n-values in one frequency space.

### Changed

- Band classifier in `classify_compression()` now reports `compression_fraction = weighted_score / available_weight` and thresholds the fraction (< 0.15 / < 0.40 / >= 0.40), not the absolute weighted score. New `Insufficient signal` band for documents below all length floors. Previously such documents falsely classified as "Lightly smoothed."
- Baseline z-score output in `compare_to_baseline()` carries `length_floor`, `length_floor_satisfied`, and a warning string when the target is below the heuristic's floor. Markdown output marks unreliable rows with `[!]`.
- Default `--min-ratio` for `chapter_distinctiveness_audit.py` raised from 1.0 to 1.5. The leave-one-out baseline can drag down ratios for habit-vocabulary that some chapters omit; "distinctive" is a stronger claim than "barely over-represented."
- Default `--char-top` in `voice_distance.py` and `voice_profile.py` lowered from 500 to 200. Semantic also changed: now per-n cap rather than total cap across all three n-values.
- Cluster registry pruned from 27 to 26 families. Dropped `modals_volitional` (singleton "will" never fired under the cluster floor); dropped `more`/`most` from `comparison` (they overlapped `quantifiers` and read as a duplicate lens).
- Cluster `direction` label now derives from majority sign of feature deviations, not from the mean signed z. Prevents the directional flag and direction label from contradicting when one large outlier of opposite sign overwhelms several smaller features pulling the same way.
- Dispersed-habit sort key in `manuscript_repetition_audit.py` changed from `(n_chapters, mean_ratio)` to `(n_chapters, median_ratio)`. Median resists single-spike inflation.

### Fixed

- `voice_distance.py` now drops the target file from baseline entries when the same path appears in `--baseline-dir`. Previously the target self-normalized the score (cosine min collapsing to 0.0).
- `manuscript_repetition_audit.py` and `repetition_audit.py` now refuse zero-token baselines (raises `BaselineError`); surface skipped baseline files with a stderr warning rather than silently dropping them; expose `baseline_files_loaded`, `baseline_files_skipped`, and `baseline_tokens` in JSON output.
- `manifest_validator.py` rejects directory paths after path resolution (uses `is_file()` instead of `exists()`); the `voice_profile` privacy ratchet now warns on missing or non-string `privacy` values, not just non-`'private'` strings.
- `repetition_audit.py` and `manuscript_repetition_audit.py` apply a `min_ratio` floor to candidate scoring (default 1.0). The previous behavior admitted under-represented words (ratio < 1.0) into the candidate list; downstream aggregators treated them as habit-vocabulary candidates.
- `variance_audit.py` POS-bigram metric documentation and computation now match. The reference doc described KL divergence; the script previously computed only entropy of the target. Both are now produced when a baseline is supplied.
- `variance_audit.py` function-word reference doc now points readers to `voice_distance.py` for the actual Burrows-style and Cosine Delta computation. Layer A reports only `function_word_ratio` as advertised.
- README length-floor table now matches `COMPRESSION_HEURISTICS` for all 11 signals (Burstiness B 200, Shannon entropy 2000, Sentence-length SD 5000 corrected from prior stale values).
- Genre tolerance table internal contradictions resolved. Three cells (AIC-3 blog, AIC-7 blog, AIC-3 testimony) now use `Mixed` with footnotes splitting the tolerance by subtype rather than the single-band labels that contradicted the explanatory prose.
