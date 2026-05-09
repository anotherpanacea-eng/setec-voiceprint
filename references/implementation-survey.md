# Borrow-Before-Building Survey

This note records where SETEC should use established libraries, where it should treat existing projects as reference implementations, and where local code is still the right choice. The point is not dependency maximalism. The point is to stop hand-rolling mature statistical machinery when a tested implementation already exists.

## License Compatibility

SETEC ships dual-licensed: GPL-3.0-or-later for code, CC BY-SA 4.0 for documentation prose. That posture constrains which packages can be vendored or hard-required:

- **MIT / BSD libraries** — unconditionally compatible. scikit-learn (BSD-3), SciPy (BSD-3), statsmodels (BSD-3), `jsonschema` (MIT), Pydantic (MIT), spaCy (MIT), sentence-transformers (Apache-2.0). Safe to depend on directly.
- **Apache-2.0 libraries** — one-way compatible with GPL-3, per [Apache's own GPL-compatibility note](https://www.apache.org/licenses/GPL-compatibility.html). NLTK (Apache-2.0) falls here. SETEC including these in a GPL-3 distribution is fine; the Apache-licensed components keep their license, the SETEC-original code stays GPL-3.
- **GPL-3 R packages** — compatible at the source-license level. `stylo` (GPL-3), `quanteda` (GPL-3). The practical cost is pulling R into the Python toolchain, which is why we treat them as oracles (run-once-when-validating) rather than runtime dependencies.

Sentence-transformers worth a closer look: the package is Apache-2.0 but the underlying model weights it ships with (e.g. `all-MiniLM-L6-v2`) carry their own licenses — typically Apache-2.0 or CC-BY-SA, but model-specific. If we ever auto-download a model, the license check at adoption time should cover both the package and the chosen model.

## Decision Rule

Use an external dependency when all of these are true:

- The task is a standard statistical, validation, schema, or NLP operation.
- The package is maintained, documented, and compatible with the project's eventual license posture.
- SETEC can wrap the package behind a small local API and preserve its own output contract.
- A local test can verify the behavior that matters to SETEC.

Use an external project as an oracle or design reference when the method is established but importing the runtime would make the CLI heavier, less portable, or harder for writers to install.

Keep code local when the task is SETEC-specific: task-surface routing, privacy guards, claim-license text, craft-restoration framing, source triage, report language, or anything whose value depends on this framework's epistemic boundaries.

## Adopt As Dependencies

### Stylometric Parsing: spaCy

Adopted. `spacy >= 3.7` plus the `en_core_web_sm` model power Tier 2 of `variance_audit.py` (POS-bigram entropy, KL/JSD divergence against a baseline aggregate, MDD per sentence) and the POS-trigram and dependency-label n-gram families in `stylometry_core.py`. Each importer wraps the dependency in a try/except and exposes `HAS_SPACY` so a missing install degrades gracefully to Tier 1; the recommended deployment installs spaCy.

Why it fits: parsing is established machinery; the project's contribution is the variance / divergence / cluster framing on top, not the tagger or parser. spaCy's pipeline is fast enough for document-scale audits (the small English model handles ~6,000 tokens in well under a second on commodity hardware) and ships with a permissive license compatible with GPL-3.0-or-later.

Source: [spaCy 101](https://spacy.io/usage/spacy-101) documents the pipeline architecture, the small English model's POS and dependency outputs, and the `Doc.sents` sentence iterator used for both signals.

### Tier 3 Cohesion: scikit-learn (TF-IDF fallback) or sentence-transformers (preferred)

Adopted as a tier. `variance_audit.py` reports adjacent-sentence cosine cohesion (mean and SD) using whichever of `sentence-transformers` or `scikit-learn` is available, with a hard preference for sentence-transformers when both are present (the `tier3.adjacent_cosine.method` field in JSON output reports which engine ran). scikit-learn's `TfidfVectorizer` + `cosine_similarity` is the default install path because torch's footprint is large; the calibration cost is that TF-IDF cosines are systematically lower-magnitude than Sentence-BERT cosines (5,700-word Capybara article: TF-IDF mean ≈ 0.08, sentence-transformers reference range for fiction ≈ 0.30-0.55), so the heuristic thresholds in `COMPRESSION_HEURISTICS` are calibrated against sentence-transformers and TF-IDF results require interpretation against the same engine.

Why it fits: the literature's "too tidy cohesion" signal (Coh-Metrix and successors) lives in adjacent-sentence semantic similarity. Sentence-BERT and SimCSE are the canonical embedders; TF-IDF cosine is a defensible embedding-free fallback that captures lexical overlap but not semantic equivalence.

Source: [Sentence-BERT (Reimers & Gurevych, EMNLP 2019)](https://arxiv.org/abs/1908.10084) introduces siamese-trained sentence encoders; [scikit-learn `TfidfVectorizer`](https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html) and [`cosine_similarity`](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.pairwise.cosine_similarity.html) document the fallback path.

### Validation Metrics: scikit-learn

Use `sklearn.metrics` for the validation harness rather than implementing ROC, PR, and confusion-matrix math locally. The relevant primitives are `roc_auc_score`, `precision_recall_curve`, `average_precision_score`, `confusion_matrix`, and classification-report helpers.

Why it fits: the harness needs standard classifier evaluation, not new math. SETEC's contribution is manifest discipline, task-surface separation, register/length slicing, and the explicit-claim-licensing framing; the metric primitives are commodity.

Source: [scikit-learn `roc_auc_score`](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.roc_auc_score.html) documents Area Under the ROC Curve from prediction scores and supports binary, multiclass, and multilabel cases.

### Bootstrap Intervals: SciPy

Use `scipy.stats.bootstrap` for confidence intervals once length-matched bootstrap lands. SETEC still owns the window sampler and the statistic being evaluated; SciPy owns the resampling machinery and interval computation.

Why it fits: the risky part is the comparison design, not the implementation of resampling with replacement.

Source: [SciPy `stats.bootstrap`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html) computes bootstrap confidence intervals with configurable resample count, confidence level, alternative, and interval method, defaulting to BCa.

### Proportion Intervals: statsmodels

Use `statsmodels.stats.proportion.proportion_confint` for FPR/FNR/precision/recall intervals in the validation harness. Prefer Wilson intervals as the first default for small validation sets; expose method selection once calibration starts.

Why it fits: validation-set proportions will often be small-N, and normal approximations are too optimistic at the edges.

Source: [statsmodels `proportion_confint`](https://www.statsmodels.org/stable/generated/statsmodels.stats.proportion.proportion_confint.html) exposes normal, Agresti-Coull, beta/Clopper-Pearson, Wilson, Jeffreys, and binomial-test interval methods.

### Manifest Schema Growth: jsonschema

Keep the current handcrafted validator while the manifest is small. Move structural schema checks to `jsonschema` once the manifest grows nested fields, versioned schemas, or optional subdocuments. Keep SETEC's semantic cross-entry checks local.

Why it fits later: JSON Schema is good at per-entry shape validation. It is not a replacement for project-specific checks such as "validation samples cannot also be baseline split" or "voice_profile entries must be private."

Source: [`jsonschema` validation docs](https://python-jsonschema.readthedocs.io/en/v4.17.0/validate/) document versioned validators including `Draft202012Validator`, and validator objects expose `iter_errors` for collecting all failures.

## Use As Reference Implementations

### R `stylo`

Use `stylo` as the main stylometry oracle for Delta-family correctness checks. SETEC already computes Burrows-style distance and cosine distance; `stylo` has battle-tested implementations of Delta variants, cosine distance, rolling Delta, and General Imposters.

Status: **first comparison shipped.** The Burrows-Delta + cosine-distance correctness check landed in `references/stylometry-oracle.md` with a six-paper Federalist fixture under `scripts/test_data/federalist_oracle/` and the harness at `scripts/oracle/`. Phase-A result: SETEC matches stylo to floating-point precision on both metrics. Phase-B result: cosine Spearman 0.97 (close), Burrows-Delta Spearman 0.65 (the fixed-list-vs-corpus-derived-MFW choice diverges meaningfully on this fixture and is documented as a design choice).

Good uses (next passes, not yet shipped):

- Compare SETEC's character-n-gram distances against `stylo` on the same fixture (the current oracle covers function words only).
- Use `stylo::perform.delta` behavior as a reference for classifier-style candidate ranking.
- Study `stylo::rolling.delta` before building richer sliding-window voice localization.
- Study `stylo::imposters` before building impostor-baseline verification.

Do not make R a required runtime for the basic CLI. Treat it as a research oracle and optional validation fixture unless the validation harness explicitly grows an R bridge.

Sources: [`stylo::perform.delta`](https://www.rdocumentation.org/packages/stylo/versions/0.7.5/topics/perform.delta) documents Burrows Delta and alternative distance measures for supervised authorship classification; [`stylo::dist.delta`](https://www.rdocumentation.org/packages/stylo/versions/0.7.5/topics/dist.delta) documents Delta-family distance computation; [`stylo::rolling.delta`](https://www.rdocumentation.org/packages/stylo/versions/0.7.5/topics/rolling.delta) documents sequential stylometric analysis; [`stylo::imposters`](https://rdrr.io/cran/stylo/man/imposters.html) documents the General Imposters authorship-verification method.

### R `quanteda`

Use `quanteda::textstat_keyness` as the design reference for idiolect/keyness extraction. Its target-vs-reference framing matches SETEC's chapter-distinctiveness and voice-preservation needs better than generic keyword extraction.

Good uses:

- Build a Python keyness helper that reports target count, reference count, signed association score, effect size, and minimum-frequency filters.
- Compare candidate rankings against `quanteda` on small public corpora.
- Prefer likelihood-ratio or chi-square keyness for common words; use PMI only with strict frequency floors.

Source: [`quanteda::textstat_keyness`](https://quanteda.io/reference/textstat_keyness.html) computes differential feature use for a target document against a reference group and supports chi-square, Fisher exact, likelihood-ratio, and PMI measures.

### NLTK Collocations

Use NLTK's corpora and collocation finders as the design reference for idiolectic phrase extraction in `idiolect_detector.py`. SETEC's production tokenization still uses regex helpers in `stylometry_core.py` and `variance_audit.py`; NLTK enters as the Brown reference-corpus provider and as the external implementation to compare collocation rankings against, not as a replacement tokenizer.

Good uses:

- Bigram/trigram phrase candidates for the voice-profile preservation list.
- PMI or likelihood-ratio ranking with minimum-frequency filters.
- Windowed collocations for phrases that are habitual but not strictly adjacent.

Source: [NLTK's collocation module](https://www.nltk.org/api/nltk.collocations.html) provides bigram/trigram/quadgram finders, frequency filters, and association-measure scoring.

## Lower-Priority Survey Targets

Keyphrase extractors such as YAKE, TextRank-style libraries, `pke`, or `textacy` may be useful later, but they are less aligned with SETEC's current need. SETEC needs authorial habits and differential usage against a baseline, not generic document keywords. Revisit after keyness and collocation baselines exist.

## Implementation Queue

### Shipped

1. **Length-matched bootstrap.** ✅ Shipped (issue #3, commit `9326005`). `scripts/length_bootstrap.py` provides the window sampler + `scipy.stats.bootstrap` CI machinery; `variance_audit.py --bootstrap` consumes it for per-signal empirical-percentile reporting with BCa CIs.
2. **Validation harness MVP.** ✅ Shipped (issue #2, commits `3e7d263` + `b5719cc` + `ec76869`). scikit-learn supplies ROC AUC / average precision when installed, statsmodels supplies proportion intervals when installed, and the harness adds paired bootstrap CIs for ranking metrics. Reports by register, length bucket, AI status, and language status; makes the operating-point assumption explicit; refuses to publish a single aggregate accuracy number absent a stated FPR target. Per-signal AUC table with polarity check (`COMPRESSION_HEURISTICS` direction vs. empirical) shipped 2026-05-07.
3. **Stylometry oracle checks.** ✅ Shipped (issue #4, commit `f70935b`). Federalist Papers fixture at `scripts/test_data/federalist_oracle/`; harness at `scripts/oracle/`; results captured at `references/stylometry-oracle.md`. Phase A: SETEC matches stylo to floating-point precision. Phase B: cosine Spearman 0.97, Burrows-Delta Spearman 0.65 (the fixed-list-vs-corpus-derived-MFW divergence is a documented design choice).
4. **ESL field on the manifest.** ✅ Shipped (commit `4fb2177`). `language_status` field with the ratchet that warns when non-native entries land in `use: baseline` or `use: voice_profile`. The validation harness slices by `language_status`, so per-class FPR is reported separately rather than aggregated.
5. **Idiolect extraction.** ✅ Shipped. `scripts/idiolect_detector.py` implements quanteda-style keyness (likelihood-ratio, chi-square, Fisher exact, PMI), collocation filtering for multiword candidates, Brown reference-corpus mode via NLTK, and a quota-balanced preservation list. Output is framed as voice preservation, not provenance detection.
6. **Corpus-hygiene checker.** ✅ Shipped. `scripts/check_corpus.py` applies the same non-prose stripping rules used by the diagnostic scripts and reports clean/warning/fail status by file. `validation_harness.py --check-corpus` wires it in as an opt-in validation-surface preflight gate without rewriting source files.
7. **Voice-coherence validation harness.** ✅ Shipped (1.9.0). `scripts/voice_validation_harness.py` is the Surface 2 sibling to `validation_harness.py`. Per-pair scoring on labeled author pairs; per-family ROC AUC + AP; document-cluster bootstrap CI (preferred) or naive paired-record bootstrap (fallback, labeled smoke-test-only); refusal of single aggregate accuracy without `--fpr-target`. Smoke fixture: `scripts/test_data/federalist_voice_validation_manifest.jsonl` (6 Federalist docs → 15 pairs, 6 same-author + 9 different-author). On this fixture: function-word Burrows-Delta AUC ≈ 0.65, function-word cosine AUC ≈ 0.81. Smoke regression values, not calibration claims. Six regression tests cover smoke run, pair-label correctness, AUC tolerance band, claim-license refusal, operating-point appearance under FPR target, and `manifest_validator.ALLOWED_USE` round-trip.
8. **Char-n-gram oracle pass.** ✅ Shipped (1.7.0). Function-word oracle extended to all three per-n char-ngram families on the Federalist fixture; SETEC matches stylo to floating-point precision (Pearson 1.0, Mean |Δ| = 0) for Burrows-Delta and cosine on each per-n table.
9. **POS-trigram and dep-n-gram oracle pass.** ✅ Shipped (1.8.0). spaCy as parser of record on both sides; SETEC writes parse TSVs, R rebuilds n-gram frequency tables independently; bit-exact frequency-table reconstruction + floating-point distance agreement. Phase A' verifies the n-gramming + frequency-table-construction code path independently of distance math.

### Roadmap (in priority order)

10. **Adversarial test classes for the harness.** Unicode-layer first cut ✅ shipped: `scripts/adversarial_fixtures.py` plus public fixtures for zero-width spaces, homoglyphs, and soft hyphens, with `validation_harness.py` slicing by `adversarial_class`. Remaining adversarial classes: paraphrase attacks (DIPPER-class T5 paraphrasers; Krishna et al. NeurIPS 2023 baseline) and humanizer tools (commercial humanization services like StealthGPT, UndetectableAI, Quillbot — pre-baked smoothing-reversal pipelines). Each adversarial class should be a labeled `use: validation` slice with explicit `notes` provenance; the harness reports per-class TPR at the chosen FPR.
11. **Larger ESL test class for the validation harness.** The smoke fixture has zero ESL entries. A real ESL slice using `language_status: non_native_*` as the discriminator would let the harness report ESL-FPR and native-FPR side by side. Liang et al. (*Patterns* 2023) found 61.22% average FPR on TOEFL essays across seven detectors; that's the deployment failure mode this slice would catch.
12. **Manifest schema versioning.** When the manifest becomes nested or versioned, introduce `jsonschema` for per-entry structure and keep semantic cross-entry checks in `manifest_validator.py`. This is issue #6; deferred until the schema actually grows.
13. **Stylo additional oracles.** As local stylometry features mature: `stylo::imposters` as a reference for impostor-baseline verification work. (Note: `stylo::rolling.delta` is blocked at the API level — see `references/stylometry-oracle.md` "Limitations and follow-up work"; the right path if rolling-window verification becomes load-bearing is a SETEC-internal pytest contract test rather than a cross-tool oracle pass.)

## Long-Horizon: Local LLM Cross-Perplexity (Phase 7+)

The brief's strongest "layered on top" recommendation is a Binoculars-style cross-perplexity zero-shot detector (Hans et al., ICML 2024): two language models sharing a tokenizer (observer and performer), with detection statistic the ratio `log PPL_M1(x) / log X-PPL_{M1,M2}(x)`. This is the cleanest current zero-shot detector at low FPR and the only realistic path to address the homogeneous-mixing case that classical stylometry is structurally blind to.

SETEC stays in classical-stylometry territory by current scope. A Phase 7+ extension would add a sibling tool (or a new task surface, `provenance_neural`) that wraps a local LLM pair: Falcon-7B + Falcon-7B-Instruct in the original paper, or a similar shared-tokenizer pair. Two forward passes per detection. The infrastructure choice is open: `mlx-lm` is fast on Apple Silicon and Python-callable; `transformers` + `torch` is portable across platforms; `ollama-python` adds a server-boundary wrapper around llama.cpp. The dependency footprint is order-of-magnitude larger than the current install (gigabytes of weights), which is why this lives in a separate task surface rather than the core variance_audit / voice_distance pipeline.

This is documented as a horizon, not a roadmap commitment. The realistic prerequisites are a stable validation harness against classical signals first, plus an explicit user opt-in to the deployment cost.

## ESL Handling and the Manifest

Tying the brief's ESL finding into the manifest is a near-term doc + validator change rather than a research project. The manifest gains a `language_status` field with values `native`, `non_native_advanced`, `non_native_intermediate`, `learner`, or `unknown`. The validator warns when entries with `language_status: non_native_*` land in `use: baseline` for any voice-coherence-tagged downstream tool (because ESL prose sits in the same low-variance region as LLM output, and a baseline contaminated with ESL writing teaches the system that smoothing is part of the writer's voice). The validator does not block: ESL writing is a legitimate corpus, just not a legitimate baseline for AI-smoothing detection without explicit acknowledgment. The `notes` field on each entry should carry the acknowledgment when the user does choose to mix.

## Guardrails

External packages do not get to decide SETEC's claims. A library can compute AUC, bootstrap intervals, keyness, or collocation scores. It cannot decide whether a report is allowed to say "AI," whether a voice profile can be written outside a private directory, or whether a flag is earned by context. Those remain local framework responsibilities.

Every adopted dependency should land with:

- A narrow wrapper or helper, not package calls scattered across scripts.
- A smoke fixture that would fail if the dependency's behavior is misunderstood.
- A README note explaining what the dependency computes and what SETEC still owns.
- A license check at adoption time.
