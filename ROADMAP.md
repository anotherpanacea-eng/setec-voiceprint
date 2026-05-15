# setec-voiceprint: Roadmap

The architectural narrative and the path from MVP to validated framework. Internal working notes (session logs, design discussions, private corpus references) live separately.

## Current state

The framework ships a three-layer architecture (Layer A distributional diagnostics, Layer B AIC pattern flags, Layer C source triage), four task surfaces (smoothing diagnosis, voice coherence, validation, craft restoration), seventeen Python scripts spanning the smoothing-diagnosis, voice-coherence, validation, and craft-restoration surfaces, and four reference documents.

What is shipped:

- **Layer A scripts.** `variance_audit.py` (single-document distributional diagnostic with sliding-window mode), `sliding_window_heatmap.py` (renders sliding-window output as a localization heatmap; cathedral upgrade #5 finisher, shipped 1.29.0), `manuscript_audit.py` (cross-chapter aggregate), `repetition_audit.py` (vocabulary over-representation), `manuscript_repetition_audit.py` (manuscript-aggregate habit vocabulary), `chapter_distinctiveness_audit.py` (leave-one-out internal-baseline distinctiveness), `bigram_diff.py` (per-bigram POS-bigram diff: target vs. cluster, with both pooled-counts and per-file-mean aggregation), `manuscript_bigram_diff.py` (corpus-vs-corpus aggregate-level POS-bigram diff with the same aggregation toggle).
- **Layer B/C script.** `aic_pattern_audit.py` (named-pattern density audit covering negation hedge, disguised correctio, pseudo-aphorism, manifesto cadence, triplet, professional-parallel stack, and the four nonfiction parallel patterns: false-balance, hedge-and-affirm, recommendation template, authority laundering). Optional baseline-dir comparison flags densities exceeding the writer's voice envelope. Layer C earned/unearned verdicts remain the writer's call per instance; the script surfaces candidates and density.
- **Voice-coherence scripts.** `voice_distance.py` (target-vs-baseline distance with feature-cluster mode), `voice_profile.py` (private voiceprint), `idiolect_detector.py` (keyness/collocation extraction and preservation lists), `stylometry_core.py` (shared feature extraction).
- **Validation scripts.** `manifest_validator.py` (schema and integrity checks for `corpus_manifest.jsonl`), `check_corpus.py` (content-level non-prose contamination gate), `adversarial_fixtures.py` (deterministic public stress-fixture transforms), and `validation_harness.py` (MVP empirical validation for smoothing-diagnosis scores over labeled manifest entries).
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

4. **Impostor baselines.** Compare the target writer against plausible other writers in matched registers. Without these, the voiceprint over-attributes register and topic to identity. Status: **shipped end-to-end.** Impostor-corpus schema (1.14.3), acquisition tooling for blogs / Blogger Takeout / online magazines / PDF libraries (1.15.0–1.19.0), and the General Imposters validation harness `scripts/general_imposters.py` (1.28.0) — given a target text and a candidate writer's identity baseline + impostor pool in matched register, the GI bootstrap reports the proportion of iterations the target falls closer to the candidate than to any impostor, with a Kestemont-2016-style gray-zone refusal in [0.20, 0.80]. Personal pre-AI baseline assembly is documented in `scripts/calibration/PROVENANCE_TEMPLATE.md` (1.29.0).

5. **Sliding-window localization.** Whole-chapter distance is blunt. Cathedral version says "the drift is concentrated in paragraphs 12-19, mostly function words and sentence cohesion" with a heatmap. Status: **shipped** end-to-end. Sliding-window mode in `variance_audit.py` produces per-window band classifications; `sliding_window_heatmap.py` (1.29.0) renders them as a markdown localization map with sparkline, band tape, hot-zone summary, per-signal × per-window grid, and claim-license block.

6. **Voice profile expansion.** Add idiolectic phrase extraction, collocations, sentence-shape distributions, readability spread, MTLD/MATTR/Yule ranges, time drift, POV-specific profiles, and a "do not normalize these phrases" preservation list. Status: core profile shipped in `voice_profile.py` with function-word, character-n-gram, punctuation cadence, paragraph/dialogue, and pronoun-modal-negation features. Idiolect extraction shipped as `idiolect_detector.py`. Time-drift tracking (`voice_drift_tracker.py`) is the active next pick — bounded code work on top of the existing `stylometry_core` primitives, no exotic borrow. POV-specific profiles (`pov_voice_profile.py`) follow.

7. **Before/after restoration loop.** Run a draft, revise, rerun, and compare whether the changes restored voice or just gamed the metrics. Without this loop, the tool eventually teaches metric-chasing. Status: scoped, not yet built. Next scoped slice: metric-targeted restoration packets that translate diagnostic outputs into revision-safe prompt targets, then require a SETEC post-check.

8. **Privacy and packaging guards.** The system refuses to export private baselines, voice profiles, and idiolect preservation lists into publishable plugin folders. Status: `voice_profile.py` and `idiolect_detector.py` refuse output paths outside `ai-prose-baselines-private/` unless `--allow-public-output` is passed; `manifest_validator.py` enforces a privacy ratchet on `voice_profile`- and `idiolect`-tagged entries.

### Phase 1 to Phase 2 operational sequence

The structural backbone for the validation spine. **All six steps shipped as of 1.30.0.** What remains is calibration breadth (more signals × more corpora) and adversarial-class expansion in step 4 — both follow-up tracks rather than spine work.

1. **`manifest_validator.py`.** Schema and integrity checks on `corpus_manifest.jsonl`. Refuses runs that depend on a contaminated or contradictory manifest. Status: **shipped** (now also includes the `language_status` field with an ESL ratchet on `use: baseline` and `use: voice_profile` entries; see "ESL handling" below).
2. **`task_surface` field in every script's JSON output.** Surface separation enforceable in code rather than vigilable by humans. Status: **shipped.**
3. **Length-matched bootstrap** for `voice_distance.py` and `variance_audit.py`. Replaces noisy z-scores at small N with empirical percentiles drawn from length-matched windows of the baseline corpus. Status: **shipped end-to-end.** `length_bootstrap.py` houses the sampler + bootstrap helpers (built on SciPy's `scipy.stats.bootstrap`); `variance_audit.py --bootstrap` produces per-signal length-matched percentiles + BCa CIs against the baseline corpus; `voice_distance.py --bootstrap` (1.30.0) adds the same shape for the function-word distance, replacing the unanchored "is this Delta large?" question with a calibrated percentile against baseline-window distances at the target's word count. Per-family Burrows Delta bootstrap (full feature-extraction caching path) is the heavier follow-up.
4. **`validation_harness.py`.** Reads the validated manifest, runs labeled samples through the surface-tagged scripts, reports performance by task surface × register × length × AI status × language status. The harness's report template makes the operating-point assumption explicit: it refuses to publish a single aggregate accuracy number absent a stated FPR target, with a recommended 0.01% FPR threshold for student-facing or accusation-grade deployments where the cost of a single false positive dwarfs the cost of a missed AI essay. Status: **MVP shipped for `smoothing_diagnosis`** with paired bootstrap CIs for ROC AUC / average precision; next pass adds per-signal evaluation, voice-coherence evaluation, and adversarial-class fixtures.
5. **Report template: "what this result licenses / does not license."** Every harness output carries an explicit licensing block: inputs, comparison set, length range, register match, language match, confidence interval, FPR target, and the specific claim the result does and does not entitle. Status: **shipped end-to-end.** `scripts/claim_license.py` (1.29.0) houses the `ClaimLicense` dataclass + `render_block()` + `from_legacy()`. As of 1.30.0 every surfacing harness — `sliding_window_heatmap.py`, `validation_harness.py`, `voice_validation_harness.py`, `general_imposters.py` — renders the structured block in its markdown report. The legacy dict-shape `claim_license` field is preserved in JSON output for backward compat with downstream consumers.

6. **POS-bigram KL participates in the band classification when a baseline is supplied.** `variance_audit.py` now incorporates the baseline-relative KL signal into its compression-fraction band call, with threshold 0.15 (literature anchor), weight 2.0 (matching `burstiness_B` and `connective_density`), and length floor 500 words. Surfaced prominently in the headline output. Empirical motivation: on AI-composed prose where every variance metric reads inside human bounds against the writer's pre-AI baseline, POS-bigram KL is often the single signal carrying the syntactic-template-collapse evidence; previously the band call ignored that signal and the headline read as clean. Status: **shipped.** Weight and threshold both calibration-pending against the validation harness on a labeled corpus.

### Corpus hygiene safeguards

Layer A scripts silently accept whatever the input file contains, and spaCy will POS-tag CSS, HTML, JavaScript, fenced code blocks, and ASCII tables as if they were prose. A 2026-05-08 session surfaced this empirically: a WordPress essay with embedded styled-HTML scaffolding (interactive Reading-Mode toggle widget, ~1,150 words of CSS) produced KL = 0.41 against a register-matched baseline; the same essay with the code stripped produced KL = 0.10. The over-represented bigrams in the contaminated version were CSS rule structure (`PUNCT+PUNCT`, `PUNCT+SYM`, `SYM+NOUN`, `PUNCT+NUM`) rather than prose syntax. A user reading the headline KL alone would have flagged a clean essay as 4× more AI-shaped than its peers.

Two concrete safeguards close the gap:

- **Script-level preprocessing.** `variance_audit.py` and `stylometry_core.py` strip `<style>...</style>`, `<script>...</script>`, fenced code blocks (` ``` `), loose CSS blocks, JSON-shaped `{...}` blocks, conservative HTML tags, ASCII tables, and YAML front matter before tokenization. The script emits a "stripped N tokens of suspected non-prose" warning so users know the cleanup happened, records per-rule counts in JSON, and supports `--allow-non-prose` for intentional opt-out. Catches the common cases (WordPress exports with embedded widgets, Markdown posts with code samples, Substack drafts with raw HTML). Status: **shipped** for shared preprocessing and symmetric baseline application; KL threshold recalibration remains pending.
- **`check_corpus.py`.** A separate auditing pass that detects suspected non-prose contamination above a threshold and exits nonzero, with an explicit report of which files and which kinds of contamination were detected. Ships as a standalone command and as an importable function so the validation harness can gate manifest health on it. Pairs with `manifest_validator.py`: the validator catches schema and integrity issues; `check_corpus` catches content-level contamination the schema cannot see.

Status: both safeguards shipped as shared preprocessing plus the standalone `check_corpus.py` gate, with `validation_harness.py --check-corpus` as an opt-in preflight. The 2026-05-08 finding is the calibration evidence for both items. Now load-bearing: with POS-bigram KL participating in the headline band classification (Phase 1 step 6), contamination in either the input or the baseline shifts the band call rather than only a divergence footnote. The preprocessing guard graduates from defensive-polish to a precondition for the band claim to be defensible.

Symmetry requirement: any preprocessing rule applied to the target text must be applied to baseline files using the same rules. Otherwise the "did spaCy see prose" question is asymmetric across the comparison and KL readings drift in unpredictable directions.

### ESL handling

Non-native English prose sits in the same low-variance region of stylometric space as RLHF-aligned LLM output. Liang et al. (*Patterns* 2023) found average 61% false-positive rate on TOEFL essays across seven AI-prose detectors, and the field's most durable false-positive failure mode is ESL writing. Implications:

- The manifest carries a `language_status` field with values `native`, `non_native_advanced`, `non_native_intermediate`, `learner`, or `unknown`. `manifest_validator.py` warns when entries with non-native language status land in `use: baseline` or `use: voice_profile` for any voice-coherence-tagged downstream tool, because a baseline contaminated with ESL prose teaches the system that smoothing is part of the writer's voice.
- The validation harness reports a separate FPR slice for ESL entries. Aggregating native and ESL FPR into a single number masks the failure mode the field is most embarrassed by.
- The skill's claim-licensing language treats ESL writing as a corpus the framework cannot adjudicate: distributional compression in ESL prose is an artifact of the writer's English fluency, not provenance.

### Adversarial test classes for the validation harness

Beyond the basic known-AI / AI-edited / mixed split, the harness will evaluate against three adversarial families to be honest about the deployment surface:

- **Unicode-layer attacks.** Homoglyph swap and zero-width-space insertion exploit tokenization rather than semantics. RAID 2024 documents a 40%+ accuracy drop on five detectors against unnormalized homoglyphs. Defendable with Unicode normalization preprocessing. Status: **first public fixture slice shipped** (`scripts/test_data/adversarial/`) with `adversarial_class` metadata and harness slicing.
- **Paraphrase attacks.** DIPPER-class T5 paraphrasers (Krishna et al., NeurIPS 2023) drop classical detector recall by 60-90 percentage points. Labeled `use: validation` slice; per-detector TPR at the chosen FPR.
- **Humanizer tools.** Commercial humanization services (StealthGPT, UndetectableAI, Quillbot) are pre-baked smoothing-reversal pipelines that target distributional signals directly. Pangram retrains continuously against this class; SETEC's calibrated thresholds will need similar attention.

Each adversarial class is a labeled `use: validation` slice with explicit `notes` provenance. The harness refuses to mix scores across classes and reports per-class TPR independently.

### Metric-targeted restoration packets

The before/after restoration loop needs a translation layer between "the diagnostic signal moved" and "revise this passage." Some signals are direct craft targets; some are only promptable after translation; some are poor direct targets and should trigger a deeper causal read.

Planned artifact: a separate `metric-targeted-restoration` skill under the `craft_restoration` task surface, plus `references/metric-targeted-restoration.md` and a packet-generator script (`scripts/restoration_packet.py`). The packet generator will consume existing JSON outputs from `variance_audit.py`, `bigram_diff.py`, `voice_distance.py`, `idiolect_detector.py`, and `aic_pattern_audit.py`, then emit bounded revision packets with a claim license, targetability class, local evidence, plain-language translation, allowed moves, guardrails, and post-check commands.

Targetability classes:

- **Direct targets.** Connective density, sentence-length variance, FKGL spread, adjacent-cosine tidiness, repeated generic vocabulary, idiolect preservation lists, and named AIC pattern density.
- **Translated targets.** POS bigrams/trigrams, selected dependency n-grams, function-word clusters, and voice-distance contributors. Raw tags such as `DET+ADJ+NOUN` become prose instructions such as "replace generic descriptor packages with concrete actors, objects, or verbs."
- **Investigate-first targets.** MATTR, MTLD, Yule's K, Shannon entropy, and some function-word/dependency drift. These ask "what local cause produced the signal?" before any revision.
- **Avoid direct targeting.** Overall KL/JSD, Burrows Delta, cosine distance, char n-grams, and validation metrics. These are evidence summaries, not writing goals.

Status: scoped. The first version should not rewrite prose; it should produce prompt packets and require a before/after SETEC post-check so the writer can see whether the revision restored the intended signal without damaging neighboring signals or idiolect.

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

### Calibration corpus track

Cathedral upgrade #3 (validation harness) and the threshold-calibration prerequisite need labeled human-vs-AI corpora. The calibration toolchain shipped in 1.10.0 already includes a license-aware fetcher for Pangram Labs' EditLens (CC BY-NC-SA 4.0, gated; local-only). Two openly redistributable benchmarks remain on the roadmap as bounded follow-ups:

- **`scripts/calibration/fetch_raid.py`.** RAID benchmark (Dugan et al., NAACL 2024; Apache-2.0 dataset on HuggingFace at `liamdugan/raid`). 10M+ generations across 11 generators × 8 domains × 4 decoding strategies × 11 adversarial transforms — the most comprehensive openly-licensed AI-detection benchmark available. Fetcher mirrors `fetch_pangram_editlens.py` shape but without the CC-NC restrictions; can ship calibrated thresholds derived from RAID without the local-only constraint EditLens imposes. Highest-leverage corpus addition because it's both large and unrestrictively-licensed.

- **`scripts/calibration/fetch_mage.py`.** MAGE benchmark (Yichen Li et al., ACL 2024; MIT-licensed; HF `yaful/MAGE`). ~447K examples across 10 datasets. Companion to RAID; the four-benchmark empirical frame `references/implementation-survey.md` records is RAID + MAGE + MAGE-extension + Ghostbuster. Fetcher is a port of the EditLens pattern.

- **`scripts/calibration/PROVENANCE_TEMPLATE.md`.** Walkthrough for new users on collecting and labeling their personal pre-AI baseline corpus — the irreducible piece of the corpus pool that has to come from the user themselves, not online. Documents the manifest conventions, the date-tagging and register-tagging patterns, the privacy-ratchet rules in `manifest_validator.py`, and the borrow-before-building decision tree (when to use Project Gutenberg / PAN authorship corpora as impostor baselines vs. when to curate from personal sources).

- **`scripts/calibration/fetch_epub_corpus.py` + fiction impostor-pool scaffolding.** EPUB → plain-text extraction tooling for assembling a per-register impostor pool from locally-owned fiction. Targets the literary-horror / weird-fiction register specifically (Brian Evenson, Paul Tremblay, Helen Oyeyemi, Catriona Ward, Mona Awad, Mariana Enriquez, Camilla Grudova, Daisy Johnson, Samanta Schweblin, Kelly Link, Carmen Maria Machado, et al.) — the register the maintainer writes in, and one the framework's text fixtures + paragraph / construction / mimicry-cosplay audits explicitly need a real register-matched baseline for. Scope:
  - **EPUB / MOBI text extraction** (Calibre `ebook-convert` shell-out or Python `ebooklib`-based; matches the existing `scripts/calibration/inventory_pdf_library.py` shape).
  - **Front-matter / back-matter stripping**: cover, copyright, dedication, TOC, also-by, acknowledgments, author bio, ad pages. EPUBs commonly carry all of these as `<section>` elements that look like prose to spaCy but aren't part of the narrative; current corpus-hygiene gate (`check_corpus.py`) handles HTML/CSS contamination but not these structural-ebook elements.
  - **Per-author word budgets** so a 13-book author doesn't dominate a cross-author baseline. Default 50K words per author with the option to override per slug.
  - **Manifest scaffolding**: per-author `persona` slugs, `register: literary_fiction`, `corpus_role: impostor` (feeds `general_imposters.py`) AND `corpus_role: baseline` (feeds the register-matched cross-author baseline for `voice_distance.py`).
  - **`ai_status` tagging** by publication date: pre-2022 → `pre_ai_human` (the clean-baseline slice); 2022+ → `unknown` (post-AI-availability boundary; cannot confirm whether the author or editor used AI assistance). Post-2022 books become an interesting *test set* — does the framework's signal flag a 2024 novel by the same author differently than their 2019 novel?
  - **Privacy posture**: all copyrighted; lives in `ai-prose-baselines-private/fiction_impostors/`; `privacy: private` across the board; never published from the framework's plugin folder.
  - **Phased rollout**: first pass uses manual Calibre conversion of a few sample books to validate the cleaning approach; second pass ships the script; third pass runs the full pool through `general_imposters.py` and the cross-author bigram-diff studies. Useful both for personal-baseline drift checks ("does my prose sit inside the band these authors define?") AND for the framework's R10 mimicry-cosplay audit, which currently has no real fiction-register impostor pool to validate against.

- **Fiction calibration spine (depends on the impostor pool above).** The full research-grade project that the impostor pool unlocks. Where the impostor pool gives `general_imposters.py` a register-matched corpus to consume, the calibration spine answers the framework-shaped questions: what do Layer A thresholds look like when the human comparator is literary horror rather than general / argumentative prose, and how do per-author syntactic signatures distribute within that register? Six concrete deliverables:
  - **Fiction-specific Layer A threshold calibration.** Run `calibrate_thresholds.py` against the pre-2022 slice of the impostor pool as the human-class baseline, paired with the post-2022 slice (status `unknown` — empirically treated as positive-class for the inversion test) OR against the framework's RAID / MAGE positive class held constant. Produces `lit_horror_fiction_<signal>_fpr<target>_<date>` provenance entries. Expected outcome: the **polarity-inversion gate** (#40) fires on at least some signals when the comparator switches from student-essay / general-prose to literary-horror, which is itself the empirical artifact — fiction is precisely the register where smoothing-diagnosis polarity claims need to be re-evaluated, and the gate is the operational expression of that.
  - **POV-marker scheme + `pov_voice_profile.py` calibration.** Many of the impostor-pool authors do POV switching (Oyeyemi's *Boy, Snow, Bird* / *Mr. Fox*, Ward's *Sundial* / *The Last House on Needless Street*, Awad's *Bunny* / *Rouge*). A per-chapter POV-marker annotation on the manifest lets `pov_voice_profile.py` calibrate voice-collapse thresholds against real multi-POV authors rather than the current heuristic defaults. The deliverable is a calibrated per-POV variance band specifically for literary fiction.
  - **Cross-author bigram-diff studies.** Using `bigram_diff.py` and `manuscript_bigram_diff.py`, characterize per-author POS-bigram signatures within the literary-horror register. The question this answers: how distinct ARE these authors from each other at the syntactic level? If Evenson and Oyeyemi share the literary-horror register but their POS-bigram distributions differ markedly, the framework's `general_imposters.py` has a high-discrimination test bed. If they're indistinguishable, that tells the framework where its limits are. Output: a per-author bigram-signature reference doc (a sibling of `references/calibration-findings-*.md`) plus a tightened impostor-pool selection that drops near-duplicate-signature authors so GI iterations stay independent.
  - **Pre-AI vs post-AI within-author test set.** For the seven authors with both pre-2022 and post-2022 works (Evenson, Tremblay, Oyeyemi, Awad, Ward, Enriquez, Grudova), running `variance_audit.py` against the post-2022 books with the pre-2022 books as the personal baseline asks: do the framework's smoothing-diagnosis signals shift on the same author's later work? This is a within-author drift check — a stronger evidentiary frame than cross-author comparison because confounders other than AI exposure (genre conventions, editorial house style, register) hold constant. Output: a per-author drift table in the synthesis doc plus a note in PROVENANCE.md if any signal shifts measurably.
  - **Fiction-side validation harness slice.** The current `validation_harness.py` runs labeled AI/human samples from EditLens/MAGE/RAID. None of those corpora contain literary horror at meaningful scale. A fiction-side slice — pre-2022 books as the human class, GPT-4 / Claude / Llama-generated continuations of those books as the AI class — lets the harness compute fiction-specific FPR/TPR/ROC curves. Generation is the load-bearing operational cost (each fiction-AI sample is a per-author per-prompt run; expensive); validation-set construction is the harder problem. Defer until a sustainable generation budget is identified.
  - **Provenance commits.** Each calibration above produces an `[POLICY: AUDIT-ONLY]` entry in `PROVENANCE.md` (matching the existing EditLens/MAGE shape). Per the polarity-inversion gate posture, fiction-derived thresholds with inverted polarity vs. EditLens/MAGE/RAID are documented in the ledger but NOT encoded into `COMPRESSION_HEURISTICS` as framework defaults — they're per-register findings that operators wanting fiction-anchored thresholds can opt into by reading the ledger and editing the registry locally.

  This is multi-session research-grade work, gated on the impostor pool's existence and on the AMD calibration runs completing (so the cross-corpus polarity landscape is established before fiction calibration joins it). The framework already has every tool the calibration spine needs (`calibrate_thresholds.py`, `polarity-inversion gate`, `pov_voice_profile.py`, `bigram_diff.py`, `general_imposters.py`, `voice_distance.py`); the work is operational rather than framework-side.

- **AIC-8 / AIC-9 calibration corpus.** The AIC-8 image-conjunction and prestige-metaphor detectors and the AIC-9 kicker-density detector ship with provisional thresholds (T1 = 2.5 concreteness gap; T2 = 0.4 cosine similarity; T3 = 0.7 domain-scatter entropy) drawn from the implementation spec's starting values. Per the Stylometry-to-the-people policy these stay `provisional=True` until calibrated against a real corpus. The spec calls for four labeled fixture corpora: (a) **idiom negatives** — texts containing conventional collocations ("heavy burden", "sharp decline") that should NOT trigger image-conjunction flags; (b) **AI-image-conjunction positives** — texts containing the spec's named pattern ("constraints humming", "the machinery of grief") that SHOULD trigger at elevated density; (c) **aphoristic essayist negatives** — Borges / Bacon / La Rochefoucauld passages where high kicker density is the genre, not the failure mode; (d) **AI-rewrite positives** — essays passed through ChatGPT or Claude with a "polish this" instruction. Generation is the load-bearing operational cost (each AI-rewrite sample is a per-prompt run). The PR-1 foundation ships small synthetic fixtures (~10-20 examples each) for unit-test purposes only; the calibration-grade corpus is a separate research-grade build with its own PROVENANCE entry. Empirical evidence already in hand: on the Brysbaert data, several of the spec's own positive examples (`machinery/grief` gap 2.05, `architecture/grief` gap 0.89, `grammar/desire` gap 1.49) do not clear T1 = 2.5; the joint diagnostic with T2 is what carries the load, but the thresholds need empirical tuning before they're operational.

- **Periodic embedding-model re-evaluation for AIC-8.** The shipped image-conjunction and prestige-metaphor detectors use spaCy's GloVe-derived 300d word vectors (`en_core_web_md` preferred, `en_core_web_lg` accepted). The choice was made for dependency-light reuse of the framework's existing spaCy install rather than introducing a new embeddings stack (Word2Vec, GloVe binary, BERT). This is a defensible 2026-H1 choice but the field moves quickly; modern contextual embeddings (BERT, sentence-transformers, the various Qwen3-Embedding-style new models) may improve precision on the compound diagnostic that AIC-8 depends on. Tickler: re-evaluate every 6 months or whenever an AIC-8 precision/recall measurement against the calibration corpus (above) drops below an acceptable threshold. Specifically: (1) check whether spaCy has shipped a vectors-model upgrade; (2) check whether contextual embeddings produce sharper concreteness-gap-plus-similarity discrimination on the four-corpus fixture; (3) check whether a register-specific embeddings model (legal, fiction, academic) is now available and would improve calibration. The PR-1 foundation is deliberately structured so `scripts/embeddings.py` is the one place to swap; downstream detectors call `cosine_similarity()` and don't know which backend produced the number.

The seven are independently shippable. RAID and MAGE shipped in 1.42.x (the section above is mildly stale); the PROVENANCE template shipped in 1.29.0. The fiction impostor pool is the next concrete acquisition target. The fiction calibration spine that depends on it is the longer-horizon item the maintainer has committed to — "eventually we'll do it all." The two AIC-8/9 entries are R&D-side follow-ups to the 1.6x AIC-8/9 implementation wave; they are not blockers on shipping the detectors, only on calling their thresholds calibrated.

## Stylometric surface expansion

The shipped suite covers the core modern stylometry stack: lexical diversity, sentence/rhythm variance, POS/dependency n-grams, character n-grams, function words, Burrows Delta, General Imposters, keyness/collocation, and per-window localization. Recent reviewer-track work surfaced a longer list of candidate surfaces drawn from classical writeprint research and recent stylometry surveys (lexical / syntactic / structural / content-specific / idiosyncratic feature families; the persistent challenges around genre, topic leakage, short texts, and forensic reliability). This section catalogs each candidate with an honest priority — including the ones I'd indefinitely defer or explicitly *not* ship as voice surfaces.

The general framing: the existing suite is strong at measuring **distributional compression and distance from baseline**. The candidate surfaces below mostly measure **where the writer's choices live** — at the paragraph, discourse, agency, construction, and trajectory layers. That's where AI smoothing often does its most interesting damage and where the existing suite is structurally blind.

Candidate surfaces are tiered by build readiness, not by intellectual interest. Several that I find theoretically interesting are deferred or out-of-scope for reasons spelled out below.

### Tier 1 — Near-term builds

These three are the next concrete picks. Each is testable, doesn't require new dependencies beyond what's already imported, and lands at a layer the existing suite doesn't reach.

- **Paragraph Architecture Audit.** Paragraph-length distribution and variance, first-sentence vs. body length, terminal-sentence punchiness, one-sentence paragraph rate, paragraph opening / closing types, transition paragraph frequency, paragraph-to-paragraph semantic distance. Catches the "competent rectangle paragraphs" failure mode that AI editing produces — sliding-window mode is word-windowed, so paragraph-shape gaps are invisible to it. Cheap to build, immediately useful for restoration packets, and structurally orthogonal to every existing audit.

- **Discourse Move Signature.** Typed discourse markers (contrast / concession / consequence / elaboration / sequencing / reframing / epistemic stance / metadiscourse) plus *move sequence n-grams* (concession→reversal→claim, premise→caveat→narrower-claim, critique→alternative→standard). The natural growth of `connective_density` (currently a single ratio) and `aic_pattern_audit` (currently named-pattern density). The move-sequence layer is what makes it a voice surface rather than a marker counter — for serious nonfiction, "concede the objection, narrow the claim, sharpen the institutional implication" is identifiable voice. Highest interpretability for the kind of writing the framework's primary user does.

- **Agency and Abstraction Audit.** Nominalization density, agentless passive rate, light-verb constructions ("make a decision" / "provide support" / "conduct an analysis"), entity/action ratio, human-actor density, generic-institutional vocabulary. Lands at a meaningful semantic layer the variance signals don't reach — institutional smoothing lives here. Restoration packets gain a useful diagnostic vocabulary: "the local smoothing is agency loss, not lexical-diversity loss." Builds on top of the spaCy POS+dep extraction that's already there.

### Tier 2 — Promotions to first-class surfaces

These are partly shipped as feature columns inside `voice_profile.py` or implicit in existing audits. Promoting them to top-level surfaces with their own audits, baseline comparisons, and bootstrap percentiles is concrete deliverable work.

- **Punctuation Cadence Audit.** `voice_profile.py` already captures comma / semicolon / colon / dash / parenthesis / ellipsis rates as feature columns. What's missing is the top-level surface: punctuation feature Delta against baseline, punctuation n-gram divergence, "interruption grammar" profile (parentheses / em-dashes / appositives / asides), smoothing flags for dash-collapse / semicolon-suppression / comma-regularization. AI smoothing and copyediting often regularize punctuation before they erase vocabulary, so this surface catches a class of voice loss earlier than lexical signals do.

- **Stance / Modality / Epistemic Posture Audit.** Partly in the function-word feature family + the pronoun-modal-negation cluster, but only at frequency level. The missing pieces: deontic vs. epistemic modality distinction, hedge / booster / certainty / evidential markers as typed buckets, source-of-knowledge markers ("seems" / "suggests" / "shows" / "proves"), refusal phrases, obligation language, first-person stance density. Important for nonfiction / legal / academic / policy prose, where AI smoothing often changes not just style but epistemic ethics ("may suggest" → "shows"; "this is not enough to establish" → "this highlights").

- **Function-Word Grammar Surface.** The function-word family is currently used at frequency level via Burrows Delta. The sequence layer — function-word n-grams, function-word skip-grams, preposition profile, determiner profile, demonstrative usage (`this/that/these/those`), relative-pronoun choice (`which/that/who`), complementizer choice (`that/if/whether`), subordinator profile, auxiliary chains, pronoun transition patterns — would bridge interpretable syntax and the robust content-independent signal that classical authorship attribution leans on.

### Tier 3 — Substantive new surfaces (post-Tier-1)

Bigger builds. Each requires curated taxonomy work and meaningful methodology pinning before code lands. Best to design on paper before writing tests.

- **Construction Signature Audit.** The right answer to "POS-bigram KL is opaque." Translates raw tag-sequence machinery into interpretable construction counts: clefts ("what matters is..."), pseudo-clefts, fronted adverbials, sentence-initial participial phrases, appositives, agented vs. agentless passives, existential "there is/are," extraposition ("it is important to..."), correlative constructions ("not only / but also"), concessive openers, parenthetical insertions, stacked prepositional phrases. Pairs with the AIC density audit — same shape, different unit. Build cost dominated by the construction inventory's curation, not the spaCy pattern-matching code.

- **Mimicry / Style-Cosplay Audit.** Required once restoration tools are mature; not before. The framework already ships `before_after_restoration.py` with a metric-gaming heuristic, but it doesn't catch the failure mode where idiolect phrases survive *too conspicuously* (over-preserved) while function-word grammar fails to match the lexical mimicry, or baseline-signature features appear at unnatural density. The methodology is non-obvious: phrase-level survival and syntactic Delta need to be cross-checked, not aggregated. Useful both for adversarial testing and for restoration quality control — a bad voice-restoration pass can produce cosplay that scores well on per-feature metrics but reads as imitation.

- **Phraseological Signature Audit.** Extension of `idiolect_detector.py`'s keyness + collocation work into phrase-frame mining: skip-grams, lexical bundles, phrase frames with slots ("not because X but because Y"), preferred intensifier / stance / epistemic frames, idiom survival, hapax phrase survival, multi-word expression distinctiveness. The shape difference from keyness: keyness asks "which words/phrases are over-represented?"; phraseology asks "what reusable language frames does this writer build with?"

- **Semantic Trajectory Audit.** Sentence-to-sentence and paragraph-to-paragraph semantic-jump distributions, return-to-topic loops, semantic radius around thesis, abstraction trajectory over document position, claim/example density curves. Catches the "improved local cohesion at the cost of productive leaps" failure mode that the adjacent-cosine pair alone can't see. **Heavier dependency footprint** (SBERT or equivalent — gigabytes of weights); should ship as opt-in like the SBERT cohesion path is now. The surface most likely to drift toward "measuring meaning" rather than "measuring style"; needs careful claim-licensing language to stay on the right side of the framework's "topic ≠ style" boundary.

### Tier 4 — Specialized / fiction-specific extensions

Useful in narrower domains. Fit naturally as round-2 of existing surfaces rather than new top-level work.

- **Dialogue-Specific Voice Audit.** Dialogue tag style, contraction rate by character, interruption punctuation, vocatives, turn length, character-specific function-words and discourse-markers, profanity/intensifier profile, adjacency-pair patterns. Round 2 of `pov_voice_profile.py` — character voice collapse often appears in dialogue first, narration second. Worth building when the per-POV surface gets serious use.

- **Narratorial Distance / Free Indirect Audit.** Pronoun anchoring, perception/cognition verb density, deictic anchoring (`here/now/this/that`), evaluative-adjective density, focalization markers, free-indirect-discourse signals. Outside the standard stylometry literature but valuable for developmental editing of literary fiction. Adjacent to per-POV voice profile; shippable when the demand surfaces.

- **Productive Roughness Audit.** Fragments, sentence-initial conjunctions, colloquial contractions, repeated words, asymmetrical lists, mixed register, "thinking on page" markers. Conceptually right but methodologically fragile: "roughness" is in the eye of the beholder. The surface has to be **strictly baseline-relative** (this writer's stable roughness pattern, before any draft) — never absolute (these features are good). Otherwise it encodes editorial preferences as voice. Build only with that constraint frontloaded.

### Tier 5 — Adjacent surfaces (ship under different framing)

Real signals, but topic-bound or format-bound enough that calling them "voice" surfaces would muddy the framework's claim language. Each is worth shipping in its own right, as a non-voice surface.

- **Document Structure / Layout Audit.** Heading frequency / syntax, list rate, bullet style, section length variance, citation placement, block-quote use, link density, footnote density, opening-hook / closing-move types. Useful for blog / Substack / policy / memo / newsletter workflows where formatting is part of voice. But it's a *publishing-format* fingerprint, not stylometry in the standard sense. Ship as its own small audit, not as a voice tool.

- **Reference Ecology Audit.** Frequency and pattern of named references, parenthetical-reference style, quote integration, epigraph use, "as X says" constructions, analogy source domains, proper-noun ecology, canonical vs. idiosyncratic references. Identifiable across an essayist's career. **Heavily topic-bound.** A writer changes topic between drafts and the reference ecology changes; the tool would call it voice drift. The framework's foundational claim is that topic ≠ style; this surface has to ship with claim-licensing that explicitly refuses voice attribution. Better as a thematic / register profile than a voice tool.

- **Allusion / Quotation Habit Surface.** Same topic-leakage concern as reference ecology. Some writers have distinguishable allusion ecologies that survive across topics, but the signal is brittle and topic-correlated enough that ship-as-voice would require very strong claim-language guards.

- **Stockness / Formulaicity Audit.** Cliché density, generic transition phrases, corporate / policy boilerplate, register-specific stock phrases, phrase originality against a large reference corpus. Two structural risks: (1) the "LLM-associated phrase" list drifts as models change, so the tool needs current-empirics sourcing rather than a frozen list — that's a maintenance commitment the framework doesn't have a model for; (2) many humans use these phrases legitimately. The framing has to be *phraseological texture*, not *AI signal*; the latter would make this a Pangram-style classifier wearing stylometric clothes, which is the framework's structural anti-goal. Build with skepticism, ship with very explicit claim-licensing.

### Tier 6 — Indefinitely deferred

These I'd not build as separate surfaces. The reasons are structural, not preference.

- **Dependency-Tree Shape and Subtree Motifs.** The literature is mixed on whether dependency-tree features outperform lexical features for authorship; gain over what the existing POS-trigram + dependency-n-gram surfaces already capture is modest, and the *interpretability* problem the construction-signature audit solves applies equally here (tree-shape numbers are no more legible to a writer than POS-trigram KL). Better treated as inputs to the construction signature audit than as a standalone surface.

- **Morphological Texture Audit.** Latinate-vs-Germanic tilt, derivational morphology density, suffix preferences. The signal is real but it's *heavily correlated with register, education, and topic*, not just voice. A scientist writing for general audiences vs. peers will produce a Latinate-tilt swing that has nothing to do with voice. Could surface as columns inside other audits (the function-word grammar surface, for instance) but I wouldn't promote it to a top-level voice surface.

- **Figure-of-Speech Expansion (Beyond Current AIC Set).** Antithesis, anaphora, epistrophe, isocolon, polysyndeton, asyndeton, litotes — the broader rhetorical-figure inventory. The current `aic_pattern_audit.py` already covers the AI-prose-relevant figures (correctio, pseudo-aphorism, manifesto cadence, triplet, professional-parallel stack, plus four nonfiction parallel patterns). Expanding the inventory adds breadth but not new claim-shape; better to deepen the AIC density work (calibrated thresholds, baseline-relative density) than to broaden the figure list.

### 2.0 refactor target

- **Compression-of-Choice / Stylistic Choice Entropy.** This is the deepest theoretical extension on the list — and where the framework's central claim actually lives. The framework currently measures variance compression in **outputs** (sentence length, lexical diversity, POS-bigram distribution); the more honest object of measurement is variance in the writer's **choice architecture** — which connective among alternatives, which clause-combining strategy, which actor-reference strategy, which sentence-opener class. Built well, this surface would *generalize* every existing audit: each becomes a special case of "compression in some choice set." Sentence-length variance is compression in length-choice; MTLD is compression in lexical-choice; AIC density is compression in figure-choice. The unifier would give the framework a single load-bearing claim ("AI smoothing collapses choice-set entropy") and a clean restoration target ("expand this writer's choice set in dimension X"). It would also reframe what gets measured everywhere: every existing audit could be rewritten on top of this primitive.

  **Why 2.0**: defining defensible choice sets is a curatorial problem, not a coding problem. Sentence-opener classes, connective classes, reporting-verb classes, clause-combining strategies — each needs a curated taxonomy and a per-class baseline to be meaningful. This is research-grade work, not a one-week ship. It also implies a refactor of the existing surfaces to be expressed as choice-entropy specializations, which is a public-API breaking change consistent with a major version bump. Treat as the *target* for 2.0; not a v1 commitment, but the right shape for the next architectural generation.

### Build order (concrete commitments only)

When the toolchain returns to surface expansion, the order is:

1. Paragraph Architecture Audit (Tier 1)
2. Discourse Move Signature (Tier 1)
3. Agency and Abstraction Audit (Tier 1)
4. Punctuation Cadence Audit (Tier 2 — promotion)
5. Stance / Modality Audit (Tier 2 — promotion)
6. Function-Word Grammar Surface (Tier 2 — promotion)
7. Construction Signature Audit (Tier 3)
8. Phraseological Signature Audit (Tier 3 — extension of `idiolect_detector`)
9. Mimicry / Style-Cosplay Audit (Tier 3 — gated on restoration maturity)
10. Semantic Trajectory Audit (Tier 3 — gated on dependency posture)

The Tier 4 specialized surfaces (Dialogue, Narratorial Distance, Productive Roughness) ship when their domains pull on them, not on the cathedral schedule. The Tier 5 adjacent surfaces (Document Layout, Reference Ecology, Allusion Habit, Stockness) ship as separate non-voice surfaces with explicit claim-language guards, on the same demand-driven cadence. The Tier 6 deferred items are not commitments. The 2.0 refactor target (Compression-of-Choice) is the architectural horizon, not a v1 deliverable.

## Trustworthiness expansion

The `Stylometric surface expansion` section above catalogues *new things to measure*. This section catalogues *failure-mode control, interpretability, adversarial realism, and user workflow discipline* — the parts that stop a sophisticated stylometric tool from becoming a numerically impressive overclaimer. The shipped suite answers "what does this text look like stylometrically?" Trustworthiness work answers a different question:

> Compared to which legitimate alternatives, under what evidentiary conditions, with what confounders, and what revision moves would improve the prose without gaming the instrument?

That is the difference between a detector-shaped tool and a serious writing-forensics / voice-preservation system.

The current suite already encodes much of this discipline at the surface level: every output carries a `task_surface` tag, the `claim_license` block names what the result does and does not entitle, `manifest_validator.py` enforces ESL ratchets, the General Imposters harness has gray-zone refusals, and the metric-targetability taxonomy in `restoration_packet.py` resists naive metric-gaming. The work below is the *systematization* of that discipline — promoting it from per-surface convention into a shared interpretive layer.

### Architectural shape

These additions form a layered discipline:

- **Input layer.** Stylometric masking profiles (quotes, citations, boilerplate); register/genre gate; multilingual / dialect caution layer.
- **Core layer.** Existing surfaces (smoothing diagnosis, voice coherence, GI, idiolect, AIC, chapter distinctiveness).
- **Interpretation layer.** Confounder audit (Layer D), source-of-smoothing localization, surface-disagreement resolver, ablation reports.
- **Output discipline.** Evidentiary conditions gate, claim license (already shipped), negative/positive controls.
- **Validation layer.** Adversarial / paraphrase stress harness, calibration drift monitor, fairness guardrails.
- **Author-facing layer.** Revision-risk model, semantic preservation check, draft-history analysis, known-editor profile.
- **Research layer.** Counterfactual editing sandbox, multi-author segmentation, transformation-profile learning, house-style vs. author-voice decomposition.

The lower layers run before any claim is composed; the upper layers extend what writers can do with the claim once it lands. Build order generally proceeds bottom-up, but several items are independently shippable.

### Tier 1 — Trustworthiness upgrades

Highest leverage. Each one immediately reduces the framework's surface area for false confidence. These are the next picks once the calibration-breadth track has more committed thresholds.

- **Confounder audit (Layer D).** The most important addition on this list. The framework currently detects compression and drift but doesn't synthesize "compressed *relative to what alternative explanation*." Build a confounder signature matrix: each candidate confounder (professional copyediting, register/genre shift, legal/policy memo style, translation or ESL cleanup, dictation/transcription cleanup, house-style enforcement, developmental revision, "writing up from notes," intentional voice imitation) gets expected directions across the existing signal set (sentence-length variance, MDD variance, lexical diversity, POS-bigram KL, char n-gram Delta, punctuation cadence, idiolect survival, connective density, AIC pattern density, chapter-localization, baseline distance). Output: a *differential diagnosis*, not a verdict. "The observed signal is compatible with AI smoothing, but also compatible with professional copyediting and register shift; evidence distinguishing these is weak because no pre-edit draft, editor style baseline, or revision history was supplied." The framework's epistemic posture is that the math doesn't entitle the verdict; this audit is the formal expression of that.

- **Register / genre conditioning.** The manifest carries `register` but the comparison isn't operationalized — the claim-license block already says "matched register" but nothing checks it. Build: a register classifier for target and baseline (personal essay / literary fiction / commercial fiction / academic prose / legal memo / policy memo / blog essay / newsletter / testimony / grant or report prose / journalism / marketing / social media thread / email / dialogue-heavy fiction / exposition-heavy nonfiction); a register-match indicator (weak / moderate / strong); a register-mismatch penalty on claim strength; eventually a register-conditioned threshold set once enough labeled data exists. Critical because legal / policy / testimony writing has high legitimate rates of templates, connective scaffolding, abstraction, repeated nouns, and transitional explicitness — exactly the signals AI smoothing also produces. Without register conditioning, the framework over-flags the very institutional genres professional writers actually work in.

- **Stylometric masking profiles.** The existing `check_corpus.py` strips HTML / CSS / code / tables before tokenization. Expand into selectable masking profiles for the analytical pass: block quotes, inline quotations, citations, footnotes, bibliographies, legal citations, case names, statute names, headings, markdown artifacts, email headers, front matter, captions, boilerplate disclaimers, repeated institutional language, prompt remnants, LLM wrapper phrases. Modes: analyze-full, exclude-quotations, exclude-citations, exclude-headings, prose-body-only, dialogue-only, narration-only, argument-body-only, institutional-boilerplate-removed. The report should state explicitly when a finding *survives* masking ("the smoothing call drops from Moderate to Light after headings, citations, and quoted statutory language are removed") — this prevents embarrassing overclaiming on policy / legal / testimony inputs where statutory or quoted language is not the writer's voice.

- **Minimum evidentiary conditions gate.** Promote the per-surface gray-zone / claim-license guards into a single front-door gate. The gate evaluates target length, baseline size, register match, baseline staleness, impostor pool breadth, contamination, quotation density, multilingual mismatch, collaborative editing, rhetorical-task mismatch, presence of pre-edit versions, and asks: *what use is this output entitled for?* Output is an **Evidentiary Posture** label, not a numerical confidence score. Possible categories: revision-only, exploratory comparison, internal triage, research-grade validation, forensic-adjacent (still non-dispositive). Protects the tool from being used the way such tools always get abused.

- **Surface-disagreement resolver.** The framework runs multiple surfaces (smoothing diagnosis, voice coherence, GI, idiolect, AIC, chapter distinctiveness) and currently leaves cross-surface interpretation to the reader. Build a meta-layer that surfaces interpretable disagreement patterns: high smoothing + low voice drift → "author likely wrote it but it was heavily edited"; low smoothing + high voice drift → "genre shift, impostor, collaboration, or intentional style change"; high voice drift + high idiolect survival → "imitation, self-conscious revision, or phrase-level preservation with deeper structural change"; high POS-bigram KL + normal sentence variance → "syntactic-template shift without obvious rhythm compression"; high AIC density + normal Layer A → "rhetorical habit issue, not smoothing"; GI gray zone + high Delta → "candidate comparison inconclusive despite baseline distance." The current architecture has the components; this is the synthesis layer.

### Tier 2 — Validation upgrades

Make the tool publishable and defensible. Several of these are already partially scoped on the roadmap (the adversarial track has been open since 1.x); the contribution here is *output shape*, especially the robustness card.

- **Adversarial / paraphrase stress harness.** Already on the roadmap as the validation harness's adversarial-class track. The transformation classes worth covering: light copyedit, heavy copyedit, LLM "make this sound more natural," LLM "make this sound like author X," humanizer-tool output (StealthGPT / UndetectableAI / Quillbot), backtranslation, summary-to-prose expansion, voice-restoration pass (does the framework's own restoration tools create false reassurance?), deliberate idiolect injection, register transfer (essay → testimony, fiction → query letter). Output shape: a **robustness card** per signal — "this signal remains stable under light copyediting but collapses under paraphrase"; "this signal survives paraphrase but is highly register-sensitive"; "this signal is useful only with matched baselines over 2,000 words." The robustness card is the deliverable; without it, the harness is metrics without epistemic guidance.

- **Negative and positive controls in reports.** Every serious comparison should include known-authentic and known-smoothed reference points from the same writer where available. "The questioned text is farther from baseline than the known-authentic control, but closer than the known-smoothed control." Makes reports interpretable to non-technical users and prevents the "big number means scary" failure mode. Concrete buildable extension to all three validation harnesses.

- **Ablation reports.** Leave-one-feature-family-out for the band call and the voice-distance call. Surfaces fragile-vs-robust calls and distinguishes "rhythm-driven smoothing" from "global smoothing": "the Moderate call is robust to removing FKGL spread and lexical entropy but disappears if sentence-variance signals are removed — treat as rhythm-driven, not global." For voice coherence: "candidate distance is driven mostly by char 4-grams and punctuation cadence; function-word Delta is ordinary." Cheap to build (just re-run with one signal removed at a time); high interpretability payoff.

- **Calibration drift monitor.** The score-once cache already carries a `scorer_version` field. Add a regression-test suite using fixed benchmark texts that detects when threshold values shift after spaCy / dependency-parser / corpus updates. Output per release: "burstiness_B threshold stable; POS-bigram KL threshold shifted materially after parser update — recalibration required before publication claims." Protects against invisible infrastructure drift, especially as model versions change underneath.

- **Fairness / dialect / multilingual guardrails.** The ESL ratchet exists in `manifest_validator.py` but the broader linguistic-background caution layer is not visible at the report level. Promote into an explicit caution surface for nonnative English writers, code-switching, dialect features, translation-influenced prose, speech-to-text cleanup, neurodivergent punctuation/structure patterns, genre-specific educational prose, institutional templates. The report should explicitly state whether the validation set includes comparable language backgrounds; if not, it should refuse evaluative or disciplinary use. Critical because the AI-detection field has a documented history of producing unfair false positives against nonnative English writers, and even when the framework is not an AI detector users may try to use it that way.

### Tier 3 — Writer-facing upgrades

Extensions of existing surfaces that make the tool genuinely useful to writers (rather than just stylometrically interesting). Each pairs naturally with a surface that already ships.

- **Revision-risk model.** Extension of the metric-targetability taxonomy in `restoration_packet.py`. The current taxonomy classifies signals as direct / translated / investigate-first / avoid-direct. Add a per-suggestion **Revision Risk** label (low / medium / high) estimating the risk that the intervention will erase idiolect, create metric gaming, increase generic "humanizer" artifacts, damage clarity, damage genre expectations, make prose less publishable, overcorrect into artificial variance, preserve voice but weaken argument, or restore quirks that were intentionally edited out. Pairs each diagnostic trigger with the bad revision temptation and the better revision frame.

- **Source-of-smoothing localization.** Extension of `sliding_window_heatmap.py`. The heatmap currently says where the signal fires; this asks what *kind* of smoothing is happening there. Per hot zone, classify the dominant local phenomenon: syntactic flattening / lexical generalization / over-cohesion / connective overuse / idiom loss / paragraph uniformity / abstract-noun stacking / template rhetoric / generic authority cadence / reduced stance markers / reduced sensory or concrete detail / reduced argumentative friction. Output: "Hot zone 4 is not globally AI-like — it's specifically over-cohesive: adjacent-sentence cosine is high, connective density is high, and sentence variance is low, while idiolect survival remains normal." Gives writers something actually revisable.

- **Semantic preservation check.** Extension of `before_after_restoration.py`. The current post-check flags metric-gaming and signal direction; the next layer is semantic guardrails: claim inventory before/after, named-entity preservation, citation/authority preservation, stance preservation, modality preservation, causal-claim preservation, uncertainty-level preservation. Catches the failure mode where voice restoration accidentally makes an argument more forceful, less accurate, or less careful. Critical for policy / legal / nonfiction prose: "Voice restoration improved sentence variance and idiolect survival, but increased assertiveness — 7 hedged claims became unqualified claims." That's exactly the kind of thing a serious author-facing tool should catch.

- **Draft-history analysis.** Version-aware stylometric suite. Given multiple draft versions, answer: when did the smoothing enter? Was it gradual or sudden? Which revision introduced the voice drift? Did idiolect disappear in one pass? Did POS-bigram collapse occur after a global rewrite? Did later human editing restore or further flatten the voice? "Major distributional compression appears between v3 and v4, concentrated in sections 1, 4, and 6. Later edits restore lexical idiolect but not sentence-architecture variance." Stronger evidence than single-snapshot comparison.

- **Known-editor profile.** Underdeveloped and important. Given before/after edited-by-X pairs, learn an editorial transformation profile: what this editor typically changes, which signals shift after their edits, whether current drift resembles past human editing. Distinguishes "this was smoothed" from "this was smoothed in the ordinary way this editor smooths this writer" — for literary and institutional writing, that distinction is large. Bigger build because it requires labeled before/after pairs.

### Tier 4 — Advanced research / product layer

Higher-effort builds. Some are explicit 2.0+ horizon items; some are demand-driven.

- **House-style vs. author-voice decomposition.** Nested baselines (same-author-same-org / same-author-different-context / different-authors-same-org / same-genre-outside-org / broad reference) and decomposition. Classifies drift into author-specific signal vs. organizational/house-style signal vs. genre/task signal vs. topic vocabulary signal. Important for institutional writers — a piece can be authentically by someone and still sound unlike their essays because they're writing in an organizational voice. Bigger build because it requires curated nested-baseline structure.

- **Multi-author / multi-source segmentation.** Window-level feature vectors → unsupervised segment clustering → likely style-boundary detection → "voice discontinuity" flags → section-to-section voice-similarity matrix. Catches rewritten chapters in manuscripts, sections drafted by different staff in policy documents, AI-assisted inserts in essays. Output framing: "sections 2 and 5 are stylistically discontinuous from the rest of the document," not "these are different authors." `chapter_distinctiveness_audit.py` is adjacent prior work.

- **Counterfactual editing sandbox.** The user's "biggest missing feature" — and they're right that it would be conceptually powerful. Generate same-meaning variants under controlled perturbations (more sentence variance / lower connective density / restored idiolect / more concrete actors / less institutional abstraction / more or less baseline similarity). Use them *diagnostically*, not as final rewrites: "if only sentence variance is restored, the band call drops from Heavy to Moderate; if idiolect phrases are restored, voice Delta improves only slightly; if syntactic architecture changes, voice Delta improves substantially." Tells the user what the tool actually thinks is causing the signal. Research-grade: requires a meaning-preserving rewrite component (likely LLM-generated controlled variants), which adds a meaningful dependency footprint and a methodology question (how do we verify meaning preservation?). Architectural target, not a v1 ship.

- **Transformation-profile learning.** General version of the known-editor profile — learn typical transformation profiles from any before/after pair set, not just one editor. Useful for "what does light human copyediting look like in this register?" or "what does this institution's house-style enforcement look like?" Bigger build; pairs with known-editor as a generalization layer.

- **Interactive report UI.** Furthest from the current scope (CLI / Python / Claude-Code-plugin shape). Indefinitely deferred unless the framework adopts a UI layer.

### Explicit anti-goals

These are *not* on the roadmap, and the framework should resist building them even when users ask:

- **No single "authenticity score."** Will be abused immediately, regardless of how many caveats accompany it.
- **No "% AI-edited" dosage estimate.** The math doesn't entitle dosage grading; the 2026-05 corpus run found heavy-AI and light-AI clusters statistically indistinguishable on POS-bigram KL.
- **No model-attribution module.** "ChatGPT-ish / Claude-ish / Gemini-ish" attribution is fragile (model behavior drifts on a release cycle the framework can't track) and would tempt overclaiming. Fine in a research harness, not in author-facing reports.
- **No metric-optimizing rewrite engine.** A direct "make this pass" tool would create the very artifact the framework is designed to critique. Restoration packets are bounded prompts with required post-checks; that boundary stays.
- **No disciplinary report template.** Anything that looks like "evidence of misconduct" is out. The claim-license block is the load-bearing epistemic surface; sharpen it, don't write around it.

### Trustworthiness build order

When the toolchain returns to trustworthiness work, the order is:

1. Stylometric masking profiles (input layer; cheapest; preconditions for other Tier-1 calls)
2. Register / genre conditioning (input gate; precondition for confounder audit and evidentiary gate)
3. Confounder audit / Layer D (interpretation layer; the most leveraged single addition)
4. Surface-disagreement resolver (interpretation meta-layer)
5. Minimum evidentiary conditions gate (output discipline)
6. Ablation reports (validation; cheap, high-interpretability)
7. Adversarial stress harness with robustness cards (validation; already on roadmap)
8. Negative / positive controls in reports (output discipline)
9. Calibration drift monitor (validation infrastructure)
10. Fairness / dialect / multilingual guardrails (output discipline)
11. Source-of-smoothing localization (writer-facing; extends heatmap)
12. Revision-risk model (writer-facing; extends restoration_packet)
13. Semantic preservation check (writer-facing; extends before_after_restoration)
14. Draft-history analysis (writer-facing)
15. Known-editor profile (writer-facing; bigger build)

Tier 4 items (house-style decomposition, multi-author segmentation, counterfactual sandbox, transformation-profile learning) ship as research extensions on a longer horizon. The interactive UI is indefinitely deferred.

### The shortest formulation

The shipped suite measures *what a text looks like stylometrically*. The trustworthiness layer answers a different and more important question: *compared to which legitimate alternatives, under what evidentiary conditions, with what confounders, and what revision moves would improve the prose without gaming the instrument?* The Tier 1 picks above are how the framework gets there.

## Interleaving: paired-release schedule

The previous two sections treat *new tools* and *new guardrails* as separate tracks, each with its own internal tier ordering. That presentation is honest about each track's internal priorities, but it leaves the *interleaving* question unanswered. Building either track in isolation produces predictable failure modes: tools-only ships new metrics with stale interpretive infrastructure (the framework's surface area for false confidence grows); guardrails-only ships interpretive richness over an underpowered signal vocabulary (the confounder audit can't make differential diagnoses without typed-discourse and agency signals to work with).

The right shape is **paired releases**: each release ships one new tool with the guardrail that makes it interpretable, with two dependency rules:

1. **Input-layer guardrails ship before any new tool depends on them.** Stylometric masking profiles and register / genre conditioning are precondition work — they make every existing and future call more reliable, and they ship as their own release without a paired tool.
2. **Discourse Move Signature is a hard prerequisite for the Tier-1 confounder audit.** The confounder audit's differential diagnosis ("compatible with AI smoothing, but also with professional copyediting and register shift") needs typed-discourse evidence to distinguish institutional prose (legal / policy / testimony) from AI smoothing — those genres have characteristically different concession-and-elaboration patterns, and without typed markers the confounder matrix can't separate them. Agency and Abstraction Audit (Release 4) is a *strengthening complement*, not a hard prerequisite: it adds the agency-loss family to the confounder matrix and sharpens the differential diagnosis, but the confounder audit's first useful version (Release 3) ships with discourse evidence alone. Release 4 then folds the agency family into the confounder matrix at that point.

Beyond those two rules, pairings are coherence-driven: the tool and the guardrail make sense shipping together because the guardrail extends a surface the tool feeds, or because the two address the same problem from different angles.

### Proposed paired-release sequence

This is the schedule once two near-term tracks have shipped: the calibration-breadth track (RAID + MAGE corpus fetchers, more calibrated thresholds, polarity-inversion correction against a fluent-native corpus); and the **adversarial-class fixture track** — DIPPER-class paraphrase fixtures, humanizer-tool output fixtures, and the existing `validation_harness.py`'s ROC-AUC / AP slicing across those classes (so the harness can already evaluate per-fixture-class performance using the existing report shape). The fixture track is *fixture-acquisition + per-class slicing in the existing harness*, not the per-signal robustness-card output shape — that output shape is Release 7's contribution. Each release in the schedule below is a small, coherent feature pair, releasable independently from the rest.

| # | New tool | New guardrail | Coherence rationale |
|---|---|---|---|
| **1** ✅ | _(none — input-layer infrastructure)_ | **Stylometric masking profiles + Register / genre conditioning** (shipped 1.31.0) | Precondition work. Every existing and future call gets more trustworthy. |
| **2** ✅ | **Paragraph Architecture Audit** (Surfaces T1) (shipped 1.32.0) | **Source-of-smoothing localization** (Trust T3) (shipped 1.32.0) | Paragraph-level signal pairs with the heatmap's "what *kind* of smoothing is firing here" classifier. The heatmap needs paragraph-shape data to localize over. |
| **3** ✅ | **Discourse Move Signature** (Surfaces T1) (shipped 1.33.0) | **Confounder audit / Layer D** (Trust T1) (shipped 1.33.0) | Typed discourse markers (contrast / concession / consequence / sequencing / metadiscourse) give the confounder matrix the evidence to distinguish "legal/policy memo" from "AI smoothing." Tool is the prerequisite for guardrail. |
| **4** ✅ | **Agency and Abstraction Audit** (Surfaces T1) (shipped 1.34.0) | **Revision-risk model** (Trust T3) (shipped 1.34.0) | Agency-loss signals pair with per-suggestion risk labels in `restoration_packet.py`. The new diagnostic vocabulary ("the local smoothing is agency loss") gets paired immediately with risk classification. Agency family also folds into the confounder matrix as a strengthening complement. |
| **5** ✅ | **Punctuation Cadence + Stance/Modality + Function-Word Grammar** (Surfaces T2 promotions × 3) (shipped 1.35.0) | **Ablation reports** (Trust T2) (shipped 1.35.0) | More feature families need an interpretability mechanism for which ones drive the call. Ablation reports become more interesting as the feature space grows. |
| **6** ✅ | _(none — output-discipline release)_ | **Minimum evidentiary conditions gate + Negative/positive controls** (shipped 1.36.0) | Output-discipline release. After the major Tier-1 tool/guardrail pairings land, the framework's reports gain the front-door evidentiary-posture label and the interpretability of known-authentic / known-smoothed reference points. |
| **7** ✅ | _(none — interpretation meta-layer)_ | **Surface-disagreement resolver + Adversarial robustness-card output shape** (shipped 1.37.0) | Cross-surface meta-interpretation + per-signal **robustness card** as a new output shape over the adversarial fixtures (which were acquired pre-schedule). The fixture acquisition + per-class slicing landed in the pre-schedule adversarial-class track; what Release 7 ships is the per-signal "this signal collapses under paraphrase but survives copyediting" reporting layer that the fixtures enable. Surface-disagreement is the natural meta-layer over a now-richer surface set. |
| **8** ✅ | **Construction Signature Audit** (Surfaces T3) (shipped 1.38.0) | **Semantic preservation check** (Trust T3) (shipped 1.38.0) | Interpretable syntactic evidence (clefts, fronted adverbials, agented vs. agentless passives) pairs with claim/entity/stance preservation guardrails — both are about making structural-level revision answer to meaning. |
| **9** ✅ | _(none — validation infrastructure)_ | **Calibration drift monitor + Fairness / dialect / multilingual guardrails** (shipped 1.39.0) | Validation infrastructure release. By this point the framework has enough surface area that infrastructure drift between releases needs explicit monitoring, and the linguistic-background caution surface needs to be visible at report level. |
| **10** ✅ | **Mimicry / Style-Cosplay Audit** (Surfaces T3) (shipped 1.40.0) | **Known-editor profile** (Trust T3) (shipped 1.40.0) | Both address "smoothed-but-by-whom" from different angles: mimicry detects over-conspicuous imitation; known-editor learns what genuine human editing of this writer looks like. They make sense as a pair. |
| **11** ✅ | **Phraseological Signature Audit** (Surfaces T3) (shipped 1.41.0) | **Draft-history analysis** (Trust T3) (shipped 1.41.0) | Phrase-frame mining is more interpretable across multiple drafts (which frames survived, which collapsed, which were introduced). Pairs naturally with version-aware analysis. |
| **12** | Semantic Trajectory Audit (Surfaces T3) | _(none — research extensions land separately)_ | The trajectory surface is the heaviest dependency footprint (SBERT-class); ships when the framework adopts that posture. From here forward, releases get less paired and more research-driven. |
| **13+** | _(longer horizon)_ | Counterfactual editing sandbox + House-style decomposition + Multi-author segmentation + Transformation-profile learning | Tier-4 research items on both tracks. Each is independently shippable; none is on a near-term schedule. |

### What this schedule deliberately doesn't do

- **It doesn't try to ship every Tier-1 surface before any Tier-2 or Tier-3.** Releases 5 and 8 specifically interleave Tier-2 and Tier-3 work into the sequence because the corresponding guardrails (ablation, semantic preservation) are most useful at those points.
- **It doesn't pair every release.** Releases 1, 6, 7, 9, and 12 are guardrail-heavy or research-heavy; releases 2, 3, 4 are tool-driven with their natural guardrail pair. Forcing a 1:1 tool-guardrail ratio per release would produce artificial pairings.
- **It doesn't commit to a calendar.** The number of releases ahead is large; each is independently shippable; the framework's release cadence depends on the calibration-breadth track's progress and on user demand for specific surfaces. The order is the commitment, not the timing.
- **It doesn't replace the per-track tier orderings.** The Surfaces and Trustworthiness sections above keep their internal priorities; this section sequences releases *across* the two tracks. If the framework ever needs to deviate (e.g., a specific surface gets pulled forward by user demand), the per-track priority tells you what's safe to skip; the paired-release rationale tells you what dependency is broken if you do.

### Anti-pattern check

The single most-damaging anti-pattern this schedule resists is **shipping new tools without their interpretive guardrails**, which would systematically grow the framework's surface area for false confidence. Every tool release in the sequence above lands with either (a) an existing guardrail it strengthens, (b) a new guardrail that makes it interpretable, or (c) precondition guardrail work having already shipped in an earlier release. No release adds analytic firepower without also adding interpretive discipline.

The 2.0 refactor target (Compression-of-Choice / Stylistic Choice Entropy) sits beyond this entire schedule. When 2.0 lands, every existing surface gets rewritten as a special case of compression in some choice set, and the trustworthiness layer gets reframed as compression-aware (e.g., the confounder audit becomes "differential diagnosis across choice-set perturbations" rather than across signal directions). That's an architectural rewrite, not a release.

### Post-R12 priorities (informed by 2026-05-11 prior-art survey)

A merged-and-verified prior-art survey on 2026-05-11 (three LLM passes — Claude / GPT / Gemini — reconciled against fetched URLs rather than pattern-match-on-citation-style) confirmed SETEC's novelty as a synthesis (no project hits 4+ SETEC features; `idiolect` by Andrea Nini is the single 3-feature ancestor) and surfaced three concrete additions worth capturing in the roadmap. Each is independently shippable; none is on a paired-release rhythm because the framework has moved past the R1–R12 schedule into research-driven territory.

#### A. README "Why no verdict" docs section (smallest, ship anytime)

**Finding.** The OpenClaw humanizer ecosystem (`openclaw/skills` archive, ~4,500 stars; `brandonwise/humanizer` as the substantive example with 136 tests including `calibration.test.js`, 29 pattern detectors, 500+ vocabulary terms in 3 tiers, platform-specific thresholds for LinkedIn/Reddit/etc.) is a mature adversarial complement to forensic detection tooling. Humanizer tools help users *avoid* detection — the inverse of SETEC's surfacing-evidence posture. The two ecosystems share vocabulary (delve / tapestry / em-dash overuse) but inverted purpose.

**Roadmap implication.** Crystallize the framework's evidence-not-verdict argument in user-facing docs. The argument has three legs: (a) the humanizer ecosystem exists and is mature, (b) verdicts shipped as load-bearing become humanizer optimization targets, (c) the Stylometry-to-the-people policy (no anchored thresholds shipped from labeled corpora; see `scripts/calibration/PROVENANCE.md`) is the principled response — calibration moves into user hands, not vendor hands, and the framework's load-bearing artifact becomes the methodology rather than the numbers.

**Scope.** Single docs PR. New README section (between "Design principles" and "License", or front-mounted between "Choose the question" and "Plugin skills"). ~300–500 words. References OpenClaw as the load-bearing example; ties into claim-license discipline.

**Phasing.** v1.X.X PATCH bump. One file modified. No code, no tests.

**Risks.** Minimal. Worth running past the maintainer for tone so the framing stays technical rather than slipping into evangelism.

#### B. Costa 5-state authorship distinction (medium; spec + schema change)

**Finding.** Daniel Bruno Corvelo Costa's "Global Proof-of-Reality Infrastructure" submission (2026-03-17, non-normative public comment to the SEC Crypto Task Force; one-person proposal hosted on sec.gov, no regulatory standing, but conceptually substantive) proposes a 5-state authorship taxonomy more granular than SETEC's current binary-ish `ai_status` field. The states: human-authored unmodified / human-authored AI-modified / AI-generated from human inputs / fully AI-generated / multi-source composite — each with a different evidentiary weight under Costa's RCES (Reality Claim Evidence Sets) framing.

**Why this matters operationally.** Smoothing-diagnosis routinely encounters `human_authored_ai_modified` prose — that's the dominant real-world case, and the current `ai_status: pre_ai_human / ai_generated` binary forces it into either-wrong category. Claim-license blocks could route off `authorship_state` to produce sharper licensure ("this audit licenses inference about human-authored AI-modified prose; it does not license inference about fully-AI-generated prose"), and the validation harness's per-class slicing would gain a richer label axis.

**Roadmap placement.** Cross-references "Trustworthiness expansion → Tier 3 — Writer-facing upgrades" (the per-state claim-license routing is a Tier 3 extension of `claim_license.py`); independent of, but compatible with, every existing manifest convention.

**Scope, phased across three PRs:**

1. **Spec only.** `internal/SPEC_authorship_states.md` (gitignored). 15–25 KB. Covers the 5-state taxonomy, mapping rules from existing corpora (RAID, MAGE, EditLens — most rows default `null` because source corpora don't carry this distinction natively), operational definition of `multi_source_composite` (the vaguest of the five; needs anchoring against concrete examples like "AI-edited draft quoted in a multi-author anthology"), and the orthogonality argument against existing `ai_status` and `editing_status` fields.
2. **Validator + manifest field.** `manifest_validator.py` accepts a new `authorship_state` field with `null` default for backwards compat. `ALLOWED_AUTHORSHIP_STATE` vocabulary added. Tests pin the new vocabulary and the null-allowed rule. CHANGELOG entry tagged MINOR (additive schema change).
3. **Audit-script routing.** Per-task-surface claim-license blocks gain per-state language. Optional behavior; default unchanged. The longest sub-phase because every task surface producing a `ClaimLicense` block needs review.

**Phasing.** v1.X.0 spec PR, v1.X+1.0 validator PR, v1.X+2.0 audit-routing PR. Three weeks calendar at sustainable cadence; one week if compressed.

**Risks.** `multi_source_composite` is fuzzy; needs operational pinning in the spec. The new field overlaps semantically with `editing_status` and `ai_status`; orthogonality argument is load-bearing. Mapping existing corpora is lossy; the framework doesn't pretend to know.

#### C. DivEye surprisal-distribution signal (largest; needs its own model-choice decision)

**Finding.** DivEye (IBM, Basani & Chen, TMLR 2026; also ICML DIG-BUG 2025; CC BY-NC-SA 4.0, GPL-3 incompatible) demonstrates state-of-the-art AI-prose discrimination using surprisal-distribution features at the input layer of a classifier — specifically, mean / variance / autocorrelation of per-token surprisal under a causal language model. LLM-generated prose tends to produce more uniformly-surprising tokens than human prose; humans cluster their surprise; LLMs flatten it. The signal is structurally orthogonal to every existing Tier 1 SETEC signal (which are lexical / syntactic / aggregate-distributional).

**License posture.** Code is CC BY-NC-SA 4.0 (cannot import). Math is unrestricted. Clean-room reimplementation is the path: implement the per-token surprisal mean / variance / autocorrelation primitives against SETEC's own corpus discipline, license the implementation under GPL-3-or-later like the rest of the framework.

**Why this is bigger than items A and B.** Surprisal requires a *causal* language model (predicts next-token probability), not an embedding model. The 2026-05-11 embedding-model-choice spec we just finished revising (mxbai / Gemma / Harrier / Qwen / bge candidates, no-priority posture, §6.4 fixture suite as the load-bearing decision) does not apply — embedding models give vectors, not surprisal. Surprisal needs its own model-choice spec with its own §6.4-equivalent fixture-test gate. Candidate causal LMs at the right scale (1–3B params for laptop-runnable cheap surprisal): Phi-3 Mini (Microsoft, MIT), TinyLlama (Apache 2.0), Llama 3.2 1B (Meta custom license), Qwen 2.5 1.5B (Apache 2.0), GPT-2 small (OpenAI, MIT; old but well-understood).

**Roadmap placement.** Cross-references "Stylometric surface expansion → Tier 1 — Near-term builds" as a fourth Tier-1 surface alongside Paragraph Architecture, Discourse Move Signature, and Agency/Abstraction Audit. But unlike those three, it carries a model dependency that ripples into a sibling research project (the surprisal-model-choice spec), so it sits *above* them in cost rather than alongside.

**Scope, phased across four-plus PRs:**

1. **Specs.** `internal/SPEC_surprisal_signal.md` + `internal/SPEC_surprisal_model_choice.md`. Two gitignored documents. The signal spec defines the mathematical content (surprisal mean / variance / autocorrelation, fixed-window vs whole-document, normalization choices). The model-choice spec is the structural analog of the embedding spec — candidates listed, no priority designated, fixture test as load-bearing decision point.
2. **`surprisal_backend.py`** — pluggable causal-LM wrapper mirroring the shape of `embedding_backend.py`. Alias table, lazy load, honest failure (no silent fallback), deterministic mode. Tests with stub LM. Module exists but no audit-script integration yet.
3. **`surprisal_audit.py`** — standalone audit script. Computes the three statistics over a draft. JSON + markdown output. `ClaimLicense` block. `task_surface: smoothing_diagnosis`. PROVISIONAL banding only (Stylometry-to-the-people compliance). 30–50 tests.
4. **Integration into `variance_audit.py`** as opt-in Tier (Tier 4? or extension to Tier 3 cohesion?). Framework already has tier infrastructure; adding a tier follows the established shape.
5. **§6.4 fixture suite for surprisal-model choice** runs operationally on the calibration host. Same discipline as the embedding-model fixture work. Not a PR; an operational rollout step.

**Phasing.** v1.X.0 spec PRs (the two specs ship in one PR or two; either works). v1.X+1.0 backend. v1.X+2.0 audit. v1.X+3.0 variance_audit integration. v1.X+4.0+ calibration runs.

**Risks.** Whiplashing the surprisal-model decision the way the embedding-model decision whiplashed four times in one day. Mitigation: start with the no-priority posture from PR 1 of the spec. Memory footprint: a 1–2B-param causal LM is ~2–4 GB on disk; optional tier, users opt in; README costs section gains a row. Determinism: causal LM inference is non-deterministic across batch sizes by default; deterministic-algorithms mode at the framework level. Compute: per-token forward pass at every position is expensive; sharded toolchain (v1.44.x) becomes essential at corpus scale.

#### Cross-cutting sequencing

| Order | Item | Effort | Blocks downstream? |
|---|---|---|---|
| 1 | README "Why no verdict" (A) | 1–2 hours | No |
| 2 | Authorship-states spec (B.1) | 2–3 hours | B.2 |
| 3 | Authorship-states validator (B.2) | 3–4 hours | B.3 |
| 4 | Surprisal specs (C.1) | 4–6 hours | All C work |
| 5 | Authorship-states audit routing (B.3) | 6–8 hours, many touchpoints | No |
| 6 | Surprisal backend → audit → variance_audit integration (C.2–C.4) | 1–2 weeks calendar | Calibration runs |

Items 1–4 are spec-and-small-PR shape: appropriate for sessions where review capacity is constrained. Item 5 is a docs-pass-session shape. Item 6 is a multi-week research-grade project that probably waits for the AMD desktop calibration host to be live and for the embedding-model §6.4 fixture suite to complete (so two model-choice decisions don't run in parallel).

The 2.0 refactor target (Compression-of-Choice) sits beyond this roadmap. None of items A–C anticipate it; all three integrate cleanly into the 1.X surface.

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
- **Dosage signal is missing.** The 2026-05-08 corpus run (9 post-2022 essays each annotated by AI-involvement degree, cleaned of CSS contamination, evaluated against a 50-file pre-AI baseline) found heavy-AI-cluster mean KL = 0.167 and lighter-AI-cluster mean KL = 0.156. Statistically indistinguishable. POS-bigram KL detects the post-AI cohort against a pre-AI baseline; it does not grade AI-involvement amount within the post-AI cohort, on the corpora tested. If the framework wants a dosage signal, it needs different machinery than POS-bigram KL alone. Candidates worth investigating: model-specific bigram fingerprints (the multi-model collaborative regime may carry distinguishable per-model residue), word-level n-gram template residue (the existing `manuscript_repetition_audit.py` and `chapter_distinctiveness_audit.py` operate at word-level rather than POS-level and might pick up signal that POS-bigram KL doesn't), sentence-rhythm features (clause-balance ratios, parallelism density, antithesis frequency — the AI-shaping fingerprints the framework's named patterns don't yet catch).
- **Which diagnostic signals are safe restoration targets?** Directly prompting an LLM to optimize KL, Delta, entropy, or char n-gram distance invites metric gaming. The restoration surface needs a targetability taxonomy: direct craft targets, translated syntax targets, investigate-first diagnostics, and avoid-direct metrics. POS bigram/trigram drift is the central test case because it is diagnostic in raw form but only revision-useful after translation into prose moves.

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

**POS-bigram KL detects the post-AI cohort, not AI-involvement amount.** On corpora tested through 2026-05, KL reliably separates pre-AI prose from post-AI prose against a register-matched pre-AI baseline, but does not reliably distinguish "lightly AI-involved" from "heavily AI-composed" within the post-AI cohort. The framework's claim language should match: a post-AI cohort indicator with a calibrated TPR/FPR statement at a stated operating point, not a dosage gauge. The validation harness output is the right venue for that statement; folk thresholds in script docs are the wrong one.

**The Layer A band is necessary but not sufficient on edited collaboration outputs.** The 2026 multi-model collaborative regime (notes → AI draft → human comment → AI revision) reintroduces surface variance that the eleven variance heuristics measure. Layer A passes; Layer B and source triage do the work that catches the LLM's underlying preferences for antithesis density, paragraph-closure consistency, and structural symmetry. The framework's marketing language has at times implied Layer A alone is the detector; the architecture has always disclaimed that, and the doc language should match.

**Do not target raw metrics in revision prompts.** Metrics diagnose drift; they are not prose goals. A good restoration prompt names the local prose pattern and the allowed move, not the number. "Reduce `DET+ADJ+NOUN` packages by replacing generic descriptor labels with concrete actors or verbs" is a usable instruction. "Lower POS-bigram KL" is not.
