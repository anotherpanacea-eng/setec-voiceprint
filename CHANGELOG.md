# Changelog

All notable changes to this project. Format follows [Keep a Changelog](https://keepachangelog.com/) with [Semantic Versioning](https://semver.org/). The version field in `plugins/setec-voiceprint/.claude-plugin/plugin.json` bumps on every shipped commit: `feat:` → MINOR, `fix:` / `chore:` / `docs:` → PATCH. Major version is reserved for breaking changes to the public CLI / JSON contract.

## Unreleased

_(Empty. Future work lands here, gets versioned on commit.)_

## [1.7.0] - 2026-05-08

Char-n-gram correctness pass against R `stylo`, extending the function-word oracle to all three per-n character n-gram families.

### Added

- Char-n-gram oracle pass extending `scripts/oracle/`. The function-word oracle that closed issue #4 verified SETEC's Burrows-Delta + cosine math against stylo on the function-word feature space; this extension does the same for all three of SETEC's per-n character n-gram families (char-3-grams, char-4-grams, char-5-grams) using the top-200 corpus-derived char-ngrams per n. Phase A result on the Federalist fixture: SETEC matches stylo to floating-point precision (Pearson 1.0, Mean |Δ| = 0) for Burrows-Delta and cosine distance on each per-n table independently. Confirms SETEC's per-n separation design (introduced in commit `88c6073` to fix the prior unified-family char-ngram bug) is internally consistent — each per-n family behaves like a standalone Burrows-Delta input the way stylo expects. New helper `setec_to_stylo.char_ngram_table(docs, n)` exports per-n top-K corpus-derived char-ngram frequency tables; `run_stylo.R` reads each and runs `stylo::dist.delta` / `dist.cosine` per n; `compare.py` surfaces the four feature-space results (function words + char-3 + char-4 + char-5) in the comparison report. Char-n-gram Phase B (stylo's own char-ngram tokenization vs. SETEC's) is roadmap. POS-trigram and dependency-n-gram oracle passes are also roadmap (would need a different reference since stylo doesn't natively do POS or dependency parsing).

## [1.6.0] - 2026-05-08

Idiolect detector, content-level corpus-hygiene gate wired into the validation harness, adversarial Unicode-layer fixtures.

### Added

- `scripts/idiolect_detector.py`: keyness/collocation extractor for voice preservation. Scores 1/2/3-grams against a reference corpus, reports idiolectic and anti-idiolectic candidates, emits a quota-balanced "do not normalize" preservation list, shares corpus-hygiene preprocessing with the rest of the pipeline, and applies voice-cloning-grade output privacy guards.
- `scripts/adversarial_fixtures.py` and `scripts/test_data/adversarial/`: deterministic Unicode-layer validation stress fixtures derived from the bundled AI smoke sample (zero-width spaces, Cyrillic homoglyphs, soft hyphens). Manifest entries carry `adversarial_class`, `source_id`, and `transform`; `manifest_validator.py` summarizes adversarial classes and `validation_harness.py` reports a `by_adversarial_class` slice.
- `language_status` field on `corpus_manifest.jsonl` entries (`native | non_native_advanced | non_native_intermediate | learner | unknown`). `manifest_validator.py` warns when entries with non-native language status land in `use: baseline`, `use: voice_profile`, or `use: idiolect`, because ESL prose sits in the same low-variance region of stylometric space as RLHF-aligned LLM output (Liang et al., *Patterns* 2023, 61% average FPR on TOEFL essays across seven detectors). Validator summary block now reports `by_language_status` counts.

### Changed

- `validation_harness.py` can now run the content-level corpus hygiene gate with `--check-corpus`. The harness validates the manifest, selects the entries under `--use`, runs `check_corpus_paths()` with the same strip-rule configuration, fails fast if contamination exceeds `--corpus-fail-threshold` (default 0.05), and includes a `corpus_hygiene` block in JSON/Markdown.

## [1.5.2] - 2026-05-08

Documentation pass: license-compatibility section added to the implementation survey, implementation queue refreshed.

### Changed

- `references/implementation-survey.md`: new license-compatibility section enumerating the licenses of each external package SETEC adopts or treats as a reference implementation, with notes on GPL-compatible vs. permissive trade-offs for downstream redistribution. Implementation queue refreshed against current code state.

## [1.5.1] - 2026-05-08

Surface-tag chore fix on `aic_pattern_audit.py`.

### Fixed

- `scripts/aic_pattern_audit.py` surface tag aligned with the rest of the smoothing-diagnosis surface.

## [1.5.0] - 2026-05-08

Layer B/C named-pattern density audit. The framework's first scriptable Layer B/C tool.

### Added

- `scripts/aic_pattern_audit.py`: counts the named rhetorical patterns from `references/aic-flags.md` and `references/source-triage.md` in a target document, reports per-thousand-word density, and (with `--baseline-dir`) compares against a baseline corpus to flag patterns whose density exceeds the writer's voice envelope. Patterns covered: negation hedge, disguised correctio, pseudo-aphorism, manifesto cadence, triplet, professional-parallel stack, plus the four regex-tractable nonfiction parallel patterns (false-balance, hedge-and-affirm, recommendation template, authority laundering). Markdown blockquote stripping is on by default (`--keep-quotes` to disable). Layer C earned/unearned verdicts remain the writer's call per instance; the script surfaces candidates and density. Two patterns deferred to v2 because they need NER + abstractness scoring or context analysis: abstraction shielding and indefinite-pronoun gesture. Known v1 limitation: the disguised-correctio detector matches only the explicit `not X, but Y` inline form and the `It is not X. It is Y` frame; multi-sentence correctios are deferred to a sentence-pair scanner using spaCy's dependency parse.

## [1.4.0] - 2026-05-08

Stylometry oracle test harness with R `stylo` and the public-domain Federalist Papers fixture corpus. Closes #4.

### Added

- Stylometry oracle test harness: `scripts/oracle/setec_to_stylo.py` + `scripts/oracle/run_stylo.R` + `scripts/oracle/compare.py`, plus the public-domain Federalist Papers fixture corpus at `scripts/test_data/federalist_oracle/` (six papers from Project Gutenberg eBook #18: 3 Hamilton, 3 Madison, ~13,700 words). Two-phase comparison: Phase A tests distance correctness on identical input (SETEC's Burrows-Delta and cosine distance vs. R `stylo`'s `dist.delta` and `dist.cosine` on the same frequency table); Phase B tests end-to-end agreement on raw text where each side does its own tokenization and feature selection. Phase A: SETEC matches stylo to floating-point precision on both metrics (Pearson 1.0, Mean |Δ| = 0). Phase B: cosine Spearman 0.97 (feature-set choice barely shifts cosine ranking), Burrows-Delta Spearman 0.65 (the fixed-list-vs-corpus-derived-MFW design choice meaningfully shifts the L1-z-score Delta ranking). One bug surfaced and fixed in the oracle harness during the test: the initial draft averaged Burrows-Delta over all features in the fixed wordlist (including constant-zero columns), producing a systematic factor-of-(n_informative / n_total) underestimate vs. stylo's informative-features-only mean; the production `stylometry_core.family_distance` was already correct (only accumulates abs(z) when sd > 0), so the discovery confirmed the production path. The full methodology and divergence catalog lives in `references/stylometry-oracle.md`. R remains optional: the comparison is run-once-when-validating, output CSVs are committed alongside the report, no R install required to read it.

## [1.3.0] - 2026-05-08

Per-signal AUC table for the smoothing-diagnosis validation harness.

### Added

- Per-signal AUC table in `scripts/validation_harness.py`. The harness now reports ROC AUC + average precision + paired bootstrap CIs for each of the 13 Layer A signals independently, in addition to the aggregate `compression_fraction` ranking. Signal scores are extracted at scoring time into `record["per_signal_scores"]`; `per_signal_ranking_metrics()` loops over `_SIGNAL_PATHS`, builds a `(label, signal_value)` paired sample per signal, and runs the existing rank-based metrics + paired bootstrap. Output appears under `slices.overall.per_signal_ranking` in JSON and as a "Per-Signal Discrimination" section in the markdown report. Per-signal CIs are computed only on the overall slice (per-slice per-signal would explode report size and slice samples are typically too small for stable per-signal CIs). Each signal's polarity is checked against `COMPRESSION_HEURISTICS`'s expected direction (`gt` for signals like `yules_k`/`connective_density`/`function_word_ratio` that rise under compression; `lt` for variance signals like `mattr`/`mtld`/`fkgl_sd`/`burstiness_B` that fall); the harness labels each signal as "matches expected direction" or "does NOT match expected direction" so calibration drift or polarity inversion is visible at a glance. Addresses the "which signals are actually carrying the discrimination on this corpus" diagnostic gap that aggregate ranking metrics hide.

## [1.2.0] - 2026-05-08

Per-bigram POS-bigram KL decomposition. Pair of scripts surfacing which specific syntactic templates drive an elevated KL.

### Added

- `scripts/bigram_diff.py` and `scripts/manuscript_bigram_diff.py`: per-bigram POS-bigram KL decomposition. `bigram_diff.py` compares one target document against a cluster of comparator files; `manuscript_bigram_diff.py` compares two corpora at the aggregate level. Both decompose `variance_audit.py`'s aggregate POS-bigram KL into ranked per-bigram contributions, surfacing which specific syntactic templates drive an elevated KL number. Cluster aggregation toggles between pooled counts (long files dominate) and per-file mean (each file weighted equally); default `both` reports side-by-side. Markdown output includes two ranked tables per mode (over-represented and under-represented in target/corpus A) with KL contribution, log₂ ratio, raw probabilities, and example token pairs. JSON output preserves the same fields with `task_surface: smoothing_diagnosis`. Cache machinery in `parse_cluster_files` parses each file once even when running both aggregation modes.
- New helpers in `scripts/variance_audit.py`: `normalize_pos_bigram_counts(counts, keys=None, *, alpha=0.0)` returns Lidstone add-α normalized probabilities; `pos_bigram_kl_contributions(target_probs, baseline_probs, *, target_counts=None, baseline_counts=None, eps=1e-9, min_count=1)` decomposes KL into per-bigram contributions sorted by `abs(kl_contrib)` descending. Both reused by the new bigram-diff scripts.

## [1.1.0] - 2026-05-08

Corpus-hygiene preprocessor wired into Layer A and the validation harness. Catches CSS / HTML / JS / code contamination that previously inflated POS-bigram KL by ~4× against register-matched baselines.

### Added

- `scripts/preprocessing.py`: shared corpus-hygiene preprocessor for `variance_audit.py` and `stylometry_core.py`. Strips suspected non-prose (HTML/CSS/JS scaffolding, Markdown code, loose CSS blocks, conservative HTML tags, JSON-shaped blocks, ASCII tables, YAML front matter) before tokenization and POS-tagging; exposes per-rule token accounting and baseline per-file metadata.
- `scripts/check_corpus.py`: standalone content-level corpus hygiene gate. Runs the shared preprocessing detector over files, directories, or manifest-selected slices; reports stripped-token ratios, dominant stripping rules, and per-file clean/warning/fail status; exits nonzero when contamination exceeds the configured fail threshold or any file cannot be read. Importable as `check_corpus_paths()` for future validation-harness gating.
- `scripts/test_data/preprocessing/css_contaminated_fixture.md`, `scripts/test_data/preprocessing/css_contaminated_fixture_clean.md`, and `scripts/test_data/clean_baseline/`: public synthetic CSS-contamination fixture plus regression coverage for the failure mode where embedded CSS scaffolding inflates POS-bigram KL by ~4× against a register-matched baseline.
- POS-bigram KL band integration: the band classifier in `variance_audit.py` now reads the POS-bigram KL/JSD divergence as a contributing signal alongside the eleven Layer A variance signals, with its own length-floor and weight in `COMPRESSION_HEURISTICS`.

### Changed

- POS-bigram KL and all Layer A text statistics in `variance_audit.py` are computed on preprocessed text by default. Baseline files receive the same preprocessing as the target; `--allow-non-prose` records an explicit opt-out in JSON for users intentionally auditing markup-heavy material. Voice-coherence feature extraction inherits the same preprocessing through `stylometry_core.py`.

## [1.0.0] - 2026-05-07

Initial Cowork plugin release. Packages the SETEC stylometric framework as a Claude Code / Cowork plugin with four task-surface skills. Bundles the development burst that brought the framework from "MVP plus voiceprint" to "validation-spine prerequisites in place."

### Added

- Plugin packaging for Claude Code CLI / Desktop and the Cowork SDK harness. New `.claude-plugin/marketplace.json` declares the marketplace catalog with one plugin entry; new `plugins/setec-voiceprint/.claude-plugin/plugin.json` declares the plugin manifest at version 1.0.0. Four `SKILL.md` files at `plugins/setec-voiceprint/skills/{smoothing-diagnosis,voice-coherence,validation,craft-restoration}/` map one-to-one onto the framework's four task surfaces; each skill's `description` field carries trigger phrases for model-driven invocation, and each script-wrapping skill documents CLI usage with `${CLAUDE_PLUGIN_ROOT}/../../scripts/...` paths so invocations work regardless of where the repo is cloned. README installation section now distinguishes the Claude Code CLI/Desktop install path (`claude plugin marketplace add` + marketplace-driven update flow) from the Cowork harness install path (`--plugin-dir` against a local checkout, `git pull` + new session for updates). The version field lives only in `plugin.json` (not duplicated in the marketplace plugin entry) so resolution priority is unambiguous, and only the canonical `.claude-plugin/marketplace.json` ships (no root-level marketplace.json duplicate) to avoid split-brain when only one of two catalog files gets bumped.
- `scripts/manifest_validator.py`: schema and integrity checks for `corpus_manifest.jsonl`. Per-entry checks (required fields, enum-valued fields, `use` is a list, `word_count` non-negative, unknown field names flagged), cross-entry checks (duplicate `id`, missing-on-disk path, two-ids-one-file, `use: validation` + `split: baseline` contradiction, `use: voice_profile` privacy ratchet, provenance contradictions). JSON output, markdown report, importable `validate_manifest(path) -> dict` for downstream gating.
- `scripts/manuscript_repetition_audit.py`: manuscript-aggregate vocabulary audit. Composes the chapter splitters from `manuscript_audit.py` with the per-document scorer from `repetition_audit.py`. Output: dispersed habit-vocabulary table (words flagged in many chapters at moderate ratio), concentrated repetition table (one or two chapters at high peak ratio), per-chapter top-N. JSON preserves per-chapter and aggregated structures separately.
- `scripts/chapter_distinctiveness_audit.py`: leave-one-out internal-baseline vocabulary audit. For each chapter, baseline is the union of all other chapters; surfaces words distinctive to one chapter rather than habit-vocabulary dispersed across the manuscript. Default `--min-ratio 1.5` because "distinctive" is a stronger claim than "barely over-represented."
- Sliding-window mode in `scripts/variance_audit.py`. New `split_into_windows()`, `audit_windows()`, and `format_windows_dashboard()` plus three CLI flags (`--window-size`, `--window-stride`, `--window-only`). Catches localized compression that whole-document scores would mask: a synthetic document combining clean and AI-flavored prose averages to "Lightly smoothed" at whole scope; the window scan correctly localizes the compression to the AI-flavored sections.
- POS-bigram KL/JSD divergence against baseline aggregate, in `scripts/variance_audit.py`. New `pos_bigram_distance()` helper with Laplace smoothing on the union of bigrams; new `compare_distributions()` entry point keeps the existing `compare_to_baseline()` z-score path unchanged.
- Feature-cluster mode for `scripts/voice_distance.py`. New `FUNCTION_WORD_CLUSTERS` registry (26 predefined syntactic groupings), new `compute_clusters()` aggregator reporting mean signed z, direction consistency, and top contributors per cluster. Catches authorial fingerprints that single-feature top-N misses when a cluster of related features moves together at moderate magnitudes.
- Genre tolerance quick-reference table in `references/aic-flags.md`. 7×6 grid (seven AIC flags by six genres) with three tolerance bands (Low, Med, High) plus N/A and six footnotes for cells where a single band misrepresents the call (AIC-1 in testimony; AIC-2 in testimony; AIC-7 in essay/testimony; AIC-3 in blog; AIC-7 in blog; AIC-3 in testimony).
- `task_surface` field on every script's JSON output and markdown header. Values: `smoothing_diagnosis` (variance/manuscript/repetition audits), `voice_coherence` (voice_distance, voice_profile, idiolect_detector), `validation` (manifest_validator, validation_harness). Each script exports a module-level `TASK_SURFACE` constant for downstream importers.
- Per-n character n-gram families (`char_ngrams_3`, `char_ngrams_4`, `char_ngrams_5`) in `scripts/stylometry_core.py`. Each family normalizes within its own n, has its own selection cap, and contributes its own Burrows-Delta and cosine distance. Replaces the prior unified family that mixed all three n-values in one frequency space.
- `references/implementation-survey.md`: borrow-before-building survey for validation, bootstrap, stylometry-oracle, idiolect, and manifest-schema work. Records which external packages should become dependencies, which should remain reference implementations, and which SETEC-specific responsibilities stay local.
- Final license texts. `LICENSE` carries the canonical GNU GPL v3 text governing code (`GPL-3.0-or-later`); `LICENSE-docs` carries the canonical Creative Commons Attribution-ShareAlike 4.0 International text governing documentation and reference prose (`CC-BY-SA-4.0`); `NOTICE` enumerates which files each license governs and confirms that personal baseline corpora and generated voice profiles fall outside the repository's licensed scope.
- `requirements.txt`: declares `spacy>=3.7,<4`, `scipy>=1.11`, `scikit-learn>=1.3`, `statsmodels>=0.14`, and `nltk>=3.8` as runtime dependencies, with `sentence-transformers` and `textstat` listed as commented optional extras for calibrated cohesion and tightened FKGL. Replaces the scattered `pip install` snippets in the README's Installation section.
- `scripts/length_bootstrap.py` and `--bootstrap` flag in `variance_audit.py`. Phase 1 step 3 of the validation spine. For each Layer A signal, the bootstrap samples random length-matched word-slice windows from each baseline file, pools the per-window statistic values into an empirical distribution at the target's word length, reports the target's mid-rank percentile in that distribution, and uses `scipy.stats.bootstrap` to put a BCa confidence interval on the percentile. Replaces noisy z-scores at small N. Flags: `--bootstrap-windows-per-file` (default 50), `--bootstrap-max-windows` (default 500), `--bootstrap-resamples` (default 9999), `--bootstrap-confidence` (default 0.95), `--bootstrap-seed`. Output appears under `baseline_bootstrap` in JSON and as a "Length-matched bootstrap" section in markdown.
- `scripts/validation_harness.py`: MVP validation harness for the `smoothing_diagnosis` surface. Reads a validated manifest, runs `variance_audit` scoring on entries tagged `use: validation`, reports ROC AUC / average precision with paired bootstrap CIs when both classes are present, and reports thresholded FPR/TPR/FNR/precision only when an explicit `--fpr-target` operating point is supplied. Slices output by register, length bucket, language status, and AI status; includes claim-license language refusing individual-document provenance verdicts and single aggregate accuracy. Defaults leave `mixed` outside the binary label frame unless explicitly mapped.
- `scripts/test_data/validation_smoke_manifest.jsonl`: public smoke fixture for `validation_harness.py`, pointing at the bundled capybara human sample and AI smoke sample.

### Changed

- `references/implementation-survey.md` rewritten against actual code state. spaCy and scikit-learn now appear under Adopt As Dependencies (both already imported by `variance_audit.py` and `stylometry_core.py` as optional, now formally adopted via `requirements.txt`); SciPy moved from "future bootstrap dep" to current runtime requirement; NLTK is now the optional-reference-corpus path for `idiolect_detector.py`'s Brown corpus mode. New sections: ESL handling, adversarial test classes for the validation harness, 0.01% FPR target framing, and a Phase 7+ horizon item for local-LLM cross-perplexity.
- `ROADMAP.md` updated to enumerate ESL handling, adversarial test classes (paraphrase / humanizer / Unicode-layer), the 0.01% FPR target as the recommended deployment threshold for accusation-grade settings, and a Phase 7+ horizon for a local-LLM cross-perplexity sibling tool. The harness step now explicitly slices by `language_status` and refuses to publish a single aggregate accuracy number absent a stated FPR target.
- `README.md` Installation section now points to `requirements.txt` and documents the `python -m spacy download en_core_web_sm` step explicitly, replacing the prior scattered Tier 1 / Tier 2 / Tier 3 pip snippets.
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
- `manifest_validator.py` rejects directory paths after path resolution (uses `is_file()` instead of `exists()`); the voiceprint privacy ratchet now warns on `voice_profile` and `idiolect` entries with missing or non-string `privacy` values, not just non-`'private'` strings.
- `repetition_audit.py` and `manuscript_repetition_audit.py` apply a `min_ratio` floor to candidate scoring (default 1.0). The previous behavior admitted under-represented words (ratio < 1.0) into the candidate list; downstream aggregators treated them as habit-vocabulary candidates.
- `variance_audit.py` POS-bigram metric documentation and computation now match. The reference doc described KL divergence; the script previously computed only entropy of the target. Both are now produced when a baseline is supplied.
- `variance_audit.py` function-word reference doc now points readers to `voice_distance.py` for the actual Burrows-style and Cosine Delta computation. Layer A reports only `function_word_ratio` as advertised.
- README length-floor table now matches `COMPRESSION_HEURISTICS` for all 11 signals (Burstiness B 200, Shannon entropy 2000, Sentence-length SD 5000 corrected from prior stale values).
- Genre tolerance table internal contradictions resolved. Three cells (AIC-3 blog, AIC-7 blog, AIC-3 testimony) now use `Mixed` with footnotes splitting the tolerance by subtype rather than the single-band labels that contradicted the explanatory prose.

[Unreleased]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.7.0...HEAD
[1.7.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.5.2...v1.6.0
[1.5.2]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.5.1...v1.5.2
[1.5.1]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.5.0...v1.5.1
[1.5.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/releases/tag/v1.0.0
