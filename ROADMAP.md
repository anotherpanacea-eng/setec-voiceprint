# setec-voiceprint: Roadmap

The architectural narrative and the path from MVP to validated framework. Internal working notes (session logs, design discussions, private corpus references) live separately.

## Current state

The framework ships a three-layer architecture (Layer A distributional diagnostics, Layer B AIC pattern flags, Layer C source triage), four task surfaces (smoothing diagnosis, voice coherence, validation, craft restoration), twelve Python scripts spanning the smoothing-diagnosis, voice-coherence, and validation surfaces, and four reference documents.

What is shipped:

- **Layer A scripts.** `variance_audit.py` (single-document distributional diagnostic with sliding-window mode), `manuscript_audit.py` (cross-chapter aggregate), `repetition_audit.py` (vocabulary over-representation), `manuscript_repetition_audit.py` (manuscript-aggregate habit vocabulary), `chapter_distinctiveness_audit.py` (leave-one-out internal-baseline distinctiveness).
- **Voice-coherence scripts.** `voice_distance.py` (target-vs-baseline distance with feature-cluster mode), `voice_profile.py` (private voiceprint), `stylometry_core.py` (shared feature extraction).
- **Validation scripts.** `manifest_validator.py` (schema and integrity checks for `corpus_manifest.jsonl`) and `validation_harness.py` (MVP empirical validation for smoothing-diagnosis scores over labeled manifest entries).
- **References.** Layer A math (`distributional-diagnostics.md`), Layer B flag families with genre tolerance table (`aic-flags.md`), Layer C source triage (`source-triage.md`), figure-by-flag countermoves (`rhetorical-countermoves.md`), and implementation/dependency survey notes (`implementation-survey.md`).

Every script's JSON output carries a `task_surface` tag so downstream consumers can route by surface. The framework refuses the unifying "is this AI" verdict; the math does not entitle it.

## Architecture: MVP to cathedral

The framework currently sits at MVP: it answers "how far is this draft from this baseline?" given a baseline corpus and a target document. Cathedral status would answer the grown-up version: given the right comparison set, length, register, time period, and known failure modes, what can be responsibly inferred, how confident, where in the text the signal lives, and what the practical revision decision is.

The epistemic shift is the load-bearing claim. Cathedral status does not mean "the tool can prove AI." It means every output knows what comparison it is making, what it cannot know, and what practical revision decision follows. Numbers are subordinate to the claim; the claim is subordinate to the comparison; the comparison is subordinate to the manifest.

### Eight cathedral upgrades

The substantive design moves the roadmap is organized around:

1. **Manifest as law, not convenience.** Every tool reads from `corpus_manifest.jsonl`; no serious run uses loose directories. The manifest gets validation: missing files, bad labels, AI-contaminated baseline entries, register mismatches, privacy violations. Status: `manifest_validator.py` shipped; wiring into manifest-consuming scripts is the next step.

2. **Length-matched bootstrap.** Instead of comparing a 300-word target to 8,000-word baseline files, sample hundreds of 300-word windows from the baseline and report where the target falls. Empirical percentiles replace noisy z-scores. Status: scoped, not yet built. Pairs with the sliding-window mode shipped in `variance_audit.py`.

3. **Validation harness.** Labeled test set with known-human, known-AI, AI-edited, mixed, paraphrased, and human-revised-after-AI samples. Per-register thresholds with FPR/FNR/ROC/PR and confidence intervals. Status: MVP shipped for the smoothing-diagnosis surface; voice-coherence, adversarial-class expansion, and richer corpus fixtures remain roadmap.

4. **Impostor baselines.** Compare the target writer against plausible other writers in matched registers. Without these, the voiceprint over-attributes register and topic to identity. Status: scoped; corpus collection is largely a manual and ethical-permissions task.

5. **Sliding-window localization.** Whole-chapter distance is blunt. Cathedral version says "the drift is concentrated in paragraphs 12-19, mostly function words and sentence cohesion" with a heatmap. Status: sliding-window mode shipped in `variance_audit.py` with band classification per window; heatmap visualization is roadmap.

6. **Voice profile expansion.** Add idiolectic phrase extraction, collocations, sentence-shape distributions, readability spread, MTLD/MATTR/Yule ranges, time drift, POV-specific profiles, and a "do not normalize these phrases" preservation list. Status: core profile shipped in `voice_profile.py` with function-word, character-n-gram, punctuation cadence, paragraph/dialogue, and pronoun-modal-negation features. Idiolect extraction and time-drift tracking are roadmap.

7. **Before/after restoration loop.** Run a draft, revise, rerun, and compare whether the changes restored voice or just gamed the metrics. Without this loop, the tool eventually teaches metric-chasing. Status: scoped, not yet built.

8. **Privacy and packaging guards.** The system refuses to export private baselines or voice profiles into publishable plugin folders. Status: `voice_profile.py` refuses output paths outside `ai-prose-baselines-private/` unless `--allow-public-output` is passed; `manifest_validator.py` enforces a privacy ratchet on `voice_profile`-tagged entries.

### Phase 1 to Phase 2 operational sequence

The structural backbone for the validation spine. Steps 1 and 2 are shipped; steps 3, 4, and 5 are next.

1. **`manifest_validator.py`.** Schema and integrity checks on `corpus_manifest.jsonl`. Refuses runs that depend on a contaminated or contradictory manifest. Status: **shipped** (now also includes the `language_status` field with an ESL ratchet on `use: baseline` and `use: voice_profile` entries; see "ESL handling" below).
2. **`task_surface` field in every script's JSON output.** Surface separation enforceable in code rather than vigilable by humans. Status: **shipped.**
3. **Length-matched bootstrap** for `voice_distance.py` and `variance_audit.py`. Replaces noisy z-scores at small N with empirical percentiles drawn from length-matched windows of the baseline corpus. Status: scoped; SciPy adopted as the resampling backend.
4. **`validation_harness.py`.** Reads the validated manifest, runs labeled samples through the surface-tagged scripts, reports performance by task surface × register × length × AI status × language status. The harness's report template makes the operating-point assumption explicit: it refuses to publish a single aggregate accuracy number absent a stated FPR target, with a recommended 0.01% FPR threshold for student-facing or accusation-grade deployments where the cost of a single false positive dwarfs the cost of a missed AI essay. Status: **MVP shipped for `smoothing_diagnosis`**; next pass adds voice-coherence evaluation and adversarial-class fixtures.
5. **Report template: "what this result licenses / does not license."** Every harness output carries an explicit licensing block: inputs, comparison set, length range, register match, language match, confidence interval, FPR target, and the specific claim the result does and does not entitle. Status: scoped.

### ESL handling

Non-native English prose sits in the same low-variance region of stylometric space as RLHF-aligned LLM output. Liang et al. (*Patterns* 2023) found average 61% false-positive rate on TOEFL essays across seven AI-prose detectors, and the field's most durable false-positive failure mode is ESL writing. Implications:

- The manifest carries a `language_status` field with values `native`, `non_native_advanced`, `non_native_intermediate`, `learner`, or `unknown`. `manifest_validator.py` warns when entries with non-native language status land in `use: baseline` or `use: voice_profile` for any voice-coherence-tagged downstream tool, because a baseline contaminated with ESL prose teaches the system that smoothing is part of the writer's voice.
- The validation harness reports a separate FPR slice for ESL entries. Aggregating native and ESL FPR into a single number masks the failure mode the field is most embarrassed by.
- The skill's claim-licensing language treats ESL writing as a corpus the framework cannot adjudicate: distributional compression in ESL prose is an artifact of the writer's English fluency, not provenance.

### Adversarial test classes for the validation harness

Beyond the basic known-AI / AI-edited / mixed split, the harness will evaluate against three adversarial families to be honest about the deployment surface:

- **Paraphrase attacks.** DIPPER-class T5 paraphrasers (Krishna et al., NeurIPS 2023) drop classical detector recall by 60-90 percentage points. Labeled `use: validation` slice; per-detector TPR at the chosen FPR.
- **Humanizer tools.** Commercial humanization services (StealthGPT, UndetectableAI, Quillbot) are pre-baked smoothing-reversal pipelines that target distributional signals directly. Pangram retrains continuously against this class; SETEC's calibrated thresholds will need similar attention.
- **Unicode-layer attacks.** Homoglyph swap and zero-width-space insertion exploit tokenization rather than semantics. RAID 2024 documents a 40%+ accuracy drop on five detectors against unnormalized homoglyphs. Defendable with Unicode normalization preprocessing.

Each adversarial class is a labeled `use: validation` slice with explicit `notes` provenance. The harness refuses to mix scores across classes and reports per-class TPR independently.

### Phase 7+ horizon: local LLM cross-perplexity

Classical stylometry is structurally blind to the homogeneous-mixing case where AI rewrites human ideas in AI's style: the surface form is fully AI, even though the underlying ideas are human, and any detector that operates on the surface form alone will score the entire text as AI. Hans et al. (Binoculars, ICML 2024) and Bao et al. (Fast-DetectGPT, ICLR 2024) show the cleanest current zero-shot detectors operate on cross-perplexity ratios between paired language models sharing a tokenizer.

A future Phase 7+ extension would add a sibling task surface (`provenance_neural`) that wraps a local LLM pair (Falcon-7B + Falcon-7B-Instruct in the original Binoculars paper, or a similar shared-tokenizer pair) and reports the Binoculars ratio with the same surface-tagged discipline as the existing tools. Plausible inference backends: `mlx-lm` for Apple Silicon performance, `transformers` + `torch` for cross-platform portability, `ollama-python` for a server-boundary wrapper. Two forward passes per detection. The dependency footprint is order-of-magnitude larger than the current install (gigabytes of weights), which is why this lives in a separate task surface rather than the core variance_audit / voice_distance pipeline.

This is a horizon item rather than a roadmap commitment. The realistic prerequisites are (1) a stable validation harness against classical signals, (2) explicit user opt-in to the deployment cost, and (3) calibration against the same labeled corpus the classical signals use, so the neural task surface and the classical task surfaces report comparable confidence intervals at the same operating point.

### Borrow-before-building track

The validation and idiolect roadmap should start from known implementations before local code gets written. `references/implementation-survey.md` records the current survey:

- Use `scikit-learn` and `statsmodels` for validation metrics and confidence intervals.
- Use SciPy's bootstrap machinery under SETEC's own length-matched window sampler.
- Treat R `stylo` as the Delta / cosine / rolling-Delta / General-Imposters oracle before expanding voice-distance verification.
- Treat `quanteda::textstat_keyness` and NLTK collocations as the design references for idiolect and preservation-list extraction.
- Keep privacy guards, report claim language, task-surface routing, and craft triage local.

## Open architectural questions

### Layer A

- Should `COMPRESSION_HEURISTICS` thresholds and weights be configurable? Currently constants in `variance_audit.py`. Configurable would let users tune for their register without editing source.
- Per-character variance signals in multi-POV fiction. MATTR within one POV character's passages may differ from another's. The current variance audit doesn't slice by POV. Worth considering once the per-POV diagnostic matters.
- Scene-shape-aware diagnostics. The lexical-compression signal does different work in different chapters (closed-room scenes have small vocabulary scope; revision smoothing tightens vocabulary). Distinguishing the two is hard to operationalize.

### Layer B

- AIC-7 named subtypes vs. evidence categories. Currently subtypes nest under evidence categories. Promoting subtypes to top-level may read more cleanly.
- Cross-cutting flag relationships. Lexical genericism touches both AIC-2 and AIC-7. Real audit experience will surface which boundary placements are useful.

### Layer C

- Voice attribution for narrators with multiple registers. The current voice test is binary; a multi-register version would ask "which of this character's registers should this be in?"
- The "earned by frame" verdict. Some passages are earned because the surrounding prose explicitly diagnoses them. A third verdict beyond earned and unearned is worth naming.

### Cross-layer

- Calibration of the directional-cluster threshold (0.7) against a labeled corpus. Currently a heuristic with documented step effects (3-feature clusters require 3/3, 4-feature 3/4, 5-feature 4/5).
- Calibration of the band-classification fraction thresholds (0.15, 0.40) against a labeled corpus. Currently fractions of available signal weight, not absolute percentages of evidence.
- POS-bigram KL/JSD smoothing constant. Currently add-one Laplace smoothing on the union of bigrams; literature suggests add-α with α<1 may be more principled. Calibration against a labeled corpus is the right time to tighten this.

## Voice fingerprint risk surface

The diagnostic outputs are voice-cloning inputs. The signals the variance and repetition audits compute (function-word distribution, sentence-length distribution, POS-bigram frequencies, idiolectic phrases) are exactly the inputs a stylometric voice-cloning system would consume. Three paths a hostile actor could take with a leaked voice profile: prompt-conditioning an LLM with the stylometric constraints; fine-tuning an LLM on the corpus directly; using the profile as a reward signal during generation. The framework's tools do not enable any of these directly, but a documented fingerprint makes any of them easier.

The framework's privacy posture is therefore protective by default. Personal baselines and voice profiles live in a separate private directory rather than this repo. `voice_profile.py` refuses publishable output paths unless explicitly overridden. The manifest validator enforces a privacy ratchet on voiceprint-tagged sources. The skill itself is publishable; the corpus and the voice profile that derives from it are not. Maintainers and contributors should respect this boundary.

## Design notes worth keeping

**The framework targets discourse habits, not vocabulary.** Surface tells (specific AI words, em-dash frequency) decay as models change. The named patterns are syntactic; they survive vocabulary shifts.

**Three layers stay distinct.** Layer A is mathematical, Layer B is craft-pattern recognition, Layer C is voice attribution. The framework's value depends on not collapsing them.

**Source triage is the hardest part to teach and the most valuable.** Most surface flags resolve as earned on triage. The framework's authority comes from being honest about that.

**Genre tolerance varies meaningfully.** A pattern that signals trouble in literary fiction may be partially structural to testimony or blog. The genre tolerance table consolidates the calibration notes.

**The personal baseline is the operative diagnostic.** Heuristic thresholds catch unsubtle cases. Always run with a register-matched personal baseline if available.

**The em-dash question is style preference, not AI signal.** A separate lens handles the specific surface tells. This framework catches the patterns underneath.
