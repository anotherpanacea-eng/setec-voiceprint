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

### Validation Metrics: scikit-learn

Use `sklearn.metrics` for the validation harness rather than implementing ROC, PR, and confusion-matrix math locally. The relevant primitives are `roc_auc_score`, `precision_recall_curve`, `average_precision_score`, `confusion_matrix`, and classification-report helpers.

Why it fits: the harness needs standard classifier evaluation, not new math. SETEC's contribution is manifest discipline, task-surface separation, and register/length slicing.

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

Use NLTK's collocation finders as the design reference, and possibly as a direct dependency, for idiolectic phrase extraction. SETEC already depends on NLTK for tokenization, so this is lighter than adding a new NLP stack.

Good uses:

- Bigram/trigram phrase candidates for the voice-profile preservation list.
- PMI or likelihood-ratio ranking with minimum-frequency filters.
- Windowed collocations for phrases that are habitual but not strictly adjacent.

Source: [NLTK's collocation module](https://www.nltk.org/api/nltk.collocations.html) provides bigram/trigram/quadgram finders, frequency filters, and association-measure scoring.

## Lower-Priority Survey Targets

Keyphrase extractors such as YAKE, TextRank-style libraries, `pke`, or `textacy` may be useful later, but they are less aligned with SETEC's current need. SETEC needs authorial habits and differential usage against a baseline, not generic document keywords. Revisit after keyness and collocation baselines exist.

## Implementation Queue

1. **Validation harness MVP.** Add `scikit-learn` and `statsmodels` as optional validation dependencies. Report ROC AUC, average precision, confusion matrices, FPR/FNR, and confidence intervals by task surface, register, length bucket, and AI status.
2. **Length-matched bootstrap.** Use SETEC's own window sampler plus `scipy.stats.bootstrap` for confidence intervals over baseline-window statistics.
3. **Stylometry oracle checks.** Add a small public fixture and compare SETEC's Delta/cosine outputs against `stylo` results documented in a reproducible note. Keep R optional.
4. **Idiolect extraction.** Build a keyness/collocation helper inspired by `quanteda::textstat_keyness` and NLTK collocations. Feed the output into `voice_profile.py` as a preservation list, not as a provenance verdict.
5. **Manifest schema versioning.** When the manifest becomes nested or versioned, introduce `jsonschema` for per-entry structure and keep semantic cross-entry checks in `manifest_validator.py`.

## Guardrails

External packages do not get to decide SETEC's claims. A library can compute AUC, bootstrap intervals, keyness, or collocation scores. It cannot decide whether a report is allowed to say "AI," whether a voice profile can be written outside a private directory, or whether a flag is earned by context. Those remain local framework responsibilities.

Every adopted dependency should land with:

- A narrow wrapper or helper, not package calls scattered across scripts.
- A smoke fixture that would fail if the dependency's behavior is misunderstood.
- A README note explaining what the dependency computes and what SETEC still owns.
- A license check at adoption time.
