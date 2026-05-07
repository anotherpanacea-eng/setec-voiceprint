# Borrow-Before-Building Survey

This note records where SETEC should use established libraries, where it should treat existing projects as reference implementations, and where local code is still the right choice. The point is not dependency maximalism. The point is to stop hand-rolling mature statistical machinery when a tested implementation already exists.

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

Good uses:

- Compare SETEC's feature-matrix distances against `stylo::dist.delta` and `stylo::dist.cosine` on synthetic and public corpora.
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

Use NLTK's collocation finders as the design reference for idiolectic phrase extraction in the upcoming `idiolect_detector.py`. NLTK is not currently a SETEC dependency; tokenization throughout the codebase uses regex helpers in `stylometry_core.py` and `variance_audit.py`. The idiolect work is the first place NLTK becomes a candidate for direct adoption (the alternative is hand-rolling collocation finders against `Counter`, which loses well-tested association-measure scoring).

Good uses:

- Bigram/trigram phrase candidates for the voice-profile preservation list.
- PMI or likelihood-ratio ranking with minimum-frequency filters.
- Windowed collocations for phrases that are habitual but not strictly adjacent.

Source: [NLTK's collocation module](https://www.nltk.org/api/nltk.collocations.html) provides bigram/trigram/quadgram finders, frequency filters, and association-measure scoring.

## Lower-Priority Survey Targets

Keyphrase extractors such as YAKE, TextRank-style libraries, `pke`, or `textacy` may be useful later, but they are less aligned with SETEC's current need. SETEC needs authorial habits and differential usage against a baseline, not generic document keywords. Revisit after keyness and collocation baselines exist.

## Implementation Queue

1. **Length-matched bootstrap.** Use SETEC's own window sampler plus `scipy.stats.bootstrap` for confidence intervals over baseline-window statistics. SciPy is already required for the install path; this is now an integration task on existing infrastructure rather than a new dependency. Pairs with the sliding-window mode shipped in `variance_audit.py`.
2. **Validation harness MVP.** scikit-learn is already adopted (Tier 3 cohesion); add `statsmodels` for proportion intervals. Report ROC AUC, average precision, confusion matrices, FPR/FNR, and confidence intervals by task surface, register, length bucket, AI status, and **language status** (see ESL handling below). The harness's report template should make the operating-point assumption explicit (cf. Soheil Feizi's argument that 0.01% FPR is the only acceptable threshold for student-facing detector deployment, where the cost of a single false accusation in academic-integrity proceedings dwarfs the cost of a missed AI essay) and refuse to publish a single aggregate accuracy number absent a stated FPR target.
3. **ESL test class for the validation harness.** The brief identifies non-native-English writing as the field's most durable false-positive failure mode (Liang et al. *Patterns* 2023: TOEFL essays produced average FPR 61.22% across seven detectors). Add an `ESL` slice to the validation manifest using `language_status` as the manifest discriminator, and produce a separate FPR report for it. The harness should report ESL-FPR and native-FPR side by side rather than aggregating; a model that achieves 0.5% overall FPR by averaging 0.1% native FPR with 5% ESL FPR is producing the wrong number.
4. **Adversarial test classes.** Beyond the basic known-AI / AI-edited / mixed split, the harness should evaluate against three adversarial families to be honest about the deployment surface: paraphrase attacks (DIPPER-class T5 paraphrasers; Krishna et al. NeurIPS 2023 baseline), humanizer tools (commercial humanization services like StealthGPT, UndetectableAI, Quillbot — pre-baked smoothing-reversal pipelines), and Unicode-layer attacks (homoglyph swap, zero-width-space insertion; RAID 2024 documents 40%+ accuracy drop on five detectors against unnormalized homoglyphs). Each adversarial class should be a labeled `use: validation` slice with explicit `notes` provenance; the harness reports per-class TPR at the chosen FPR.
5. **Stylometry oracle checks.** Add a small public fixture and compare SETEC's Delta/cosine outputs against `stylo` results documented in a reproducible note. Keep R optional.
6. **Idiolect extraction.** Build a keyness/collocation helper inspired by `quanteda::textstat_keyness` and NLTK collocations. Feed the output into `voice_profile.py` as a preservation list, not as a provenance verdict.
7. **Manifest schema versioning.** When the manifest becomes nested or versioned, introduce `jsonschema` for per-entry structure and keep semantic cross-entry checks in `manifest_validator.py`.

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
