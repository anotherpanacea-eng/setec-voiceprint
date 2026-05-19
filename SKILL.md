---
name: setec-voiceprint
description: "Text-only stylometry for authorial voice in prose — not audio voice identification or speaker recognition. Measure prose transformation and authorial-voice coherence under explicit claim limits. Use when a writer asks whether a draft has been smoothed, whether it still sounds like a writer/persona/POV/register, what changed after editing, how to preserve idiolect during revision, how to validate stylometric claims against a corpus, or how to turn diagnostics into revision-safe restoration packets. Also trigger on 'voiceprint,' 'authorial voice,' 'prose voice,' 'voice coherence,' 'variance audit,' 'prose smoothing,' 'AI contamination,' 'does this sound like me,' 'style cosplay,' 'voice drift,' 'idiolect preservation,' 'source triage,' 'restoration packet,' or any request to identify and repair measurable prose drift. Works on fiction and argument-shaped nonfiction. Do NOT use for: academic plagiarism detection; authorship verdicts; AI provenance verification; audio voice identification, speaker recognition, or vocal spectography. This framework measures stylometric signals in text and comparison sets, not ultimate causes and not vocal signatures."
---

# SETEC Voiceprint

**Text-only stylometry. Not audio.** This skill measures the *authorial voice* a writer's prose carries — the writer-level patterns essayists and developmental editors talk about, captured via authorship-attribution stylometry (Burrows Delta, function-word fingerprints). It has nothing to do with audio voice identification, speaker recognition, or vocal spectography.

Measure how prose changes, compare it against human baselines, and recommend bounded restoration moves when the evidence supports them. AI-assisted writing is one important use case because LLM collaboration often leaves smoothing, compression, syntactic drift, and phrase-preservation artifacts in prose. But the same signals can also come from genre, education, dialect, translation, institutional templates, human editing, time drift, POV collapse, or a writer consciously imitating themselves.

This skill is not an AI detector. It also is not an audio voice ID tool. Its core discipline is to measure the stylometric signal in text, name the comparison set, and refuse claims the evidence does not license.

## Five Task Surfaces

This skill ships tools that share statistical signals but answer five different questions. Most failure modes come from confusing them. Each surface has its own claim, its own inputs, and its own limits. The first four are the core diagnostic + restoration surfaces; the fifth is discrimination evidence shipped uncalibrated by default.

### 1. Prose smoothing / compression diagnosis  *(`variance_audit.py`, Layer A)*

**Question.** Has this prose been smoothed into a narrower-than-typical region of stylometric space, regardless of who wrote it or why?

**Inputs.** A single document; optionally a genre or personal baseline.

**Outputs.** A band classification (Lightly / Moderately / Heavily smoothed), per-signal magnitudes, optional baseline z-scores.

**Cannot answer.** Who wrote it. Whether the smoothing is an artifact of register, scene type, the writer's natural style, or AI involvement. What to do about it.

### 2. Voice-coherence comparison  *(`voice_distance.py`, `voice_profile.py`, `idiolect_detector.py`)*

**Question.** How far is this draft from a writer's or register's own stylometric baseline, and which phrases should revision preserve?

**Inputs.** A target text plus a writer/register baseline (corpus directory or manifest query). Voice-profile builds the baseline-side artifact. Idiolect detection compares a target corpus against a reference corpus.

**Outputs.** Burrows-style Delta, cosine distances, per-family deviations, top features driving the divergence, and a private "do not normalize" preservation list of idiolectic phrases.

**Cannot answer.** Whether the divergence is caused by AI involvement, register shift, time drift, genuine voice change, or the writer working in an unusual mode. The verdict is "drifted from this baseline," not "AI-written."

### 3. Empirical performance validation  *(`validation_harness.py`, `voice_validation_harness.py`, calibration tools)*

**Question.** How well do these signals behave on this labeled corpus, in this register, at this text length, under this dependency stack and fairness slice?

**Inputs.** A labeled corpus (known-human / known-AI / AI-edited / mixed / paraphrased / human-revised-after-AI samples) plus the scripts under test.

**Outputs.** Per-register performance tables with FPR, FNR, ROC, PR, confidence intervals; per-length-band breakdowns; threshold behavior reports.

**Cannot answer.** Whether the framework will work on unseen corpora outside the registers and lengths the harness covered. The harness produces claims about *this* corpus, not the world.

### 4. Craft restoration advice  *(Layer B AIC flags, Layer C source triage, rhetorical countermoves)*

**Question.** Which specific prose patterns are present, are they earned in context, and what revision moves apply?

**Inputs.** Prose passages and (for Layer C) voice-bearing material the writer can identify.

**Outputs.** AIC flag inventory with severity, source-triage verdicts (earned / unearned / earned-by-frame / voice-slip / lost-callback), salvage triage (Keep / Recast / Replace), figure-by-flag rhetorical countermoves.

**Cannot answer.** Anything about provenance. Anything quantitative about distributional smoothing. Whether the writer should have used AI in the first place.

### 5. Discrimination evidence  *(`binoculars_audit.py`, `binoculars_calibrate.py`, `external_mirror/`)*

**Question.** Under a two-model perplexity comparison (Hans et al. 2024 Binoculars) or a multi-LLM continuation-distance comparison (SETEC's external mirror), what evidence does a target text produce about its proximity to LLM-generated continuations or LLM-coupled per-token surprisal patterns?

**Inputs.** A target text. For Binoculars: a scorer LLM + observer LLM pair (default `tinyllama` + `gpt2`). For external mirror: a windowed prefix plus operator-side LLM continuation outputs pasted back into the workflow harness.

**Outputs.** Structured evidence packs with schema-versioned envelopes and `claim_license` blocks. Verdict bands read `uncalibrated` by default; operator-supplied per-corpus thresholds activate calibrated bands.

**Cannot answer.** "Is this AI" as a binary verdict. The framework deliberately ships these tools with `DEFAULT_THRESHOLD_LOW = DEFAULT_THRESHOLD_HIGH = None`; per-corpus calibration is operator-side via `binoculars_calibrate.py`. Hans et al. 2024 reports ~95% AUC on the Binoculars detector under matched conditions, but those conditions do not generalize without local calibration. The framework provides the methodology; the operator provides the comparator.

## Why Five, Not One

The five surfaces share signals because RLHF-induced mode collapse, register conventions, and time-stable authorial idiolect all leave traces in the same statistical features (function-word distributions, lexical diversity, sentence-length variance, syntactic patterns). But they answer different questions, license different claims, and fail in different ways. A single "is this AI" verdict would have to collapse them all into one number; the math does not entitle that.

The skill therefore ships narrow surfaces and refuses the unifying verdict. Each output knows what comparison it is making, what it cannot know, and what practical revision decision follows. Surface 5's discrimination tools come closest to a binary call — at ~95% AUC under matched conditions on the published Binoculars benchmark — and the framework still refuses to ship per-corpus thresholds as defaults. Calibration moves the responsibility for thresholded claims onto the operator who supplied the comparator corpus, where it belongs.

## The Mode-Collapse Lens

A useful conceptual lens — though not a literal claim about what every AI-prose detector computes — is that RLHF-aligned LLM output tends to occupy a narrower, lower-variance sub-region of human stylometric space. Sentence lengths cluster in a band. Readability scores cluster in a band. Function-word ratios converge toward training-data averages. Connectives appear at metronome density. This is *one reason* the multiple statistical signals in Layer A correlate when run on AI-drafted prose: they are picking up correlated compressions, not measuring the same thing.

Different detectors compute different things on this same surface. Burrows' Delta measures function-word distance. GLTR measures token-rank density. DetectGPT and Fast-DetectGPT measure local curvature (with or without closed-form mean). Binoculars measures cross-perplexity ratio. EditLens measures embedding-shift magnitude. Pangram trains on labeled examples. They produce correlated outputs because the underlying distributional compressions are correlated, not because they are different formulations of one master metric.

The seven AIC flag families catalog prose-level manifestations of these compressions. The named patterns are structural habits: hedge-and-reversal moves, pseudo-aphoristic cadence, template rhythm, inflated parallelism, over-neat transitions, manifesto cadence, and indefinite-pronoun gestures. (See `references/aic-flags.md` for the original literary-taxonomy names: Negation hedge, Disguised correctio, Pseudo-aphorism, Manifesto cadence, Indefinite-Pronoun Gesture.) Source triage at Layer C answers the question that no distributional analysis can: whose voice is this, and is it doing real work?

## Three Layers

The skill operates at three resolutions. Each layer's blind spot is the next layer's expertise.

| Layer | What it measures | What it can't see |
|---|---|---|
| **A. Distributional** | Variance signals: sentence-length SD, MATTR, FKGL std, adjacent-sentence cosine std, function-word distribution, POS-bigram KL, MDD variance | Whose voice; what the prose is doing |
| **B. Pattern (AIC flags)** | Recurring prose habits across passages, including named structural patterns and the parallel set for nonfiction | Whether this instance is earned in context |
| **C. Source triage** | Voice attribution; the payoff test; earned vs. unearned per passage | Doesn't scale; requires character/narrator/persona knowledge |

The skill never collapses these into a single AI-or-not score. The math doesn't entitle that conclusion. Layer A produces a magnitude (Lightly / Moderately / Heavily smoothed). Layer B produces a flag inventory with severity. Layer C produces an earned/unearned verdict per passage.

## Two Modes

The same vocabulary runs in two directions.

**Diagnostic mode** identifies where smoothing, voice drift, compression, or pattern repetition has happened and names what kind. Default mode when a writer shares a draft.

**Restoration mode** recommends concrete revision moves to reintroduce variance and voice. Mirrors the diagnostic: each layer's findings drive the corresponding restoration moves.

Restoration is symmetric to diagnosis in the feature space and asymmetric in the objective. Diagnosis finds a measurable drift; restoration adds back human variation, voice, and friction without chasing aggregate metrics directly. The same statistics serve opposite purposes.

## What You Need

A prose passage, scene, chapter, or manuscript draft. Optionally:
- Which portions are AI-generated, AI-assisted, or mixed
- Specific concerns ("the dialogue feels flat," "this chapter sounds different from the rest")
- Genre and narrative mode (literary fiction, thriller, essay, testimony, blog post; tolerances differ)
- For Layer C: voice-bearing material the writer can identify (per character for fiction, per persona/audience for nonfiction)
- For Layer A relative scoring: a baseline corpus, ideally the writer's own prior unedited work

If the writer doesn't specify, ask before running the audit.

## Diagnostic Workflow

### 1. Scope Selection

For a single passage or scene under 3,000 words, run the full audit directly.

For a chapter or manuscript, select three representative passages:

| Passage | What to pick | Why |
|---|---|---|
| A: Dialogue-heavy | A conversation with two or more characters | Tests AIC-5 (Puppet Dialogue), AIC-1 (Voice Singularity) |
| B: Interiority-heavy | Internal reflection, aftermath, bridge scene | Tests AIC-2 (Velvet Fog), AIC-7 (Discourse Leak) |
| C: Action or transition | Physical movement, setting, time passage | Tests AIC-2, AIC-3 (Echo Stack), AIC-6 (Continuity Smear) |

For multi-POV fiction, ensure samples span at least two POV characters. The most diagnostic test is whether patterns appear at authorial frequency (the generating consciousness repeats them across all POVs) or character frequency (each character uses them differently).

For argument-shaped nonfiction, substitute:

| Passage | What to pick |
|---|---|
| A: Claim-heavy | A paragraph that states a thesis or recommendation |
| B: Evidence-heavy | A paragraph deploying citations, statistics, or testimony |
| C: Concession or framing | A paragraph that acknowledges an opposing view or sets context |

### 2. Layer A: Distributional Diagnostic

Read `references/distributional-diagnostics.md` for the full Layer A reference.

Run the variance audit. The core signals:

- Sentence-length distribution (mean, SD, min, max). Burstiness B = (σ − μ)/(σ + μ).
- MATTR (moving-average type-token ratio, window 50)
- MTLD (measure of textual lexical diversity)
- Yule's K (length-robust diversity)
- Shannon entropy of token distribution
- Per-sentence Flesch-Kincaid Grade Level (mean and SD)
- Adjacent-sentence cosine similarity (mean and SD), if embeddings available
- Function-word frequency (top 100, comparison against baseline)
- POS-bigram distribution KL divergence against baseline (if spaCy available)
- Mean Dependency Distance variance (if spaCy available)

Three scripts support Layer A at progressively wider scope:

- `scripts/variance_audit.py` — single document. Outputs band classification, per-signal statistics, optional baseline z-scores. Use for one chapter or passage.
- `scripts/manuscript_audit.py` — whole manuscript across multiple chapters. Outputs a chapters × signals dashboard plus manuscript-wide pattern detection and outlier ranking. Use when single-chapter results suggest the pattern is repeating.
- `scripts/repetition_audit.py` — vocabulary-level diagnostic on a single document. Surfaces over-represented words and within-text clustering. Use when Layer A flags lexical compression and you want specific candidates for restoration.

All three accept a `--baseline-dir` argument and produce comparable z-score interpretations. See `scripts/README.md` for usage.

Output a magnitude band: Lightly smoothed, Moderately smoothed, or Heavily smoothed. The bands are calibrated against genre baselines and reflect how much variance has been compressed relative to human reference distributions.

### 3. Layer B: Pattern Flag Scan

Read `references/aic-flags.md` for the full diagnostic framework. Run each passage against the seven flag families:

| Flag | Name | What it catches |
|---|---|---|
| AIC-1 | Generic Hand | One voice for every character, scene, register |
| AIC-2 | Velvet Fog | Scenes without physical grounding; lexical genericism (includes indefinite-pronoun gestures as a named subtype) |
| AIC-3 | Echo Stack | Mechanical repetition at sentence, paragraph, or scene level |
| AIC-4 | Register Seams | Detectable shifts where drafting method changed |
| AIC-5 | Puppet Dialogue | Characters who all speak identically |
| AIC-6 | Continuity Smear | World-model failures (objects, space, time, information) |
| AIC-7 | Discourse Leak | Assistant-register habits in narrative prose; includes hedge-and-reversal moves, pseudo-aphoristic cadence, template rhythm, inflated parallelism, over-neat transitions, and manifesto cadence as named subtypes |

For each flag that fires, assign severity:
- **Spot** — isolated to one passage; surrounding text is clean
- **Pattern** — recurring across scenes or correlating with specific content types
- **Systemic** — manuscript-wide; the text reads as one entity generating all of it

Note flag compounds (AIC-1 + AIC-5, AIC-2 + AIC-6, AIC-7 + AIC-1 at Pattern or higher). Compounds change the revision scope.

For nonfiction, run the parallel pattern set documented in `references/aic-flags.md` (Abstraction Shielding, False-Balance Construction, Hedge-and-Affirm, Recommendation Template, Authority Laundering).

### 4. Layer C: Source Triage

Run only if voice-bearing material has been supplied. Otherwise note that source-triage findings are unavailable and stop after Layer B.

Read `references/source-triage.md` for the full framework.

For each Layer B flag instance, ask: is this earned in context, or is this AI-fluent prose wearing a character's name?

The payoff test: AI-fluent prose often takes the shape AI-fluent setup → character-voice payoff. Cut the setup, promote the payoff.

The voice test: is this sentence something this specific character (or this story's narrator, or this argument's persona) would actually say? Generic monologue wearing a character's name is the failure mode.

The triage rules:
- **Dialogue:** patterns usually earned. Character voice tolerates more pattern.
- **Character interior, character actively sorting:** usually earned. The negation IS the cognitive act.
- **Character interior, generic introspection:** usually unearned. The "Not X. Not Y." setup is AI-fluent monologue.
- **Narrator-pose commentary:** usually unearned. The "There was X in his Y" structures are pose.
- **Genre-required language (hypnotic induction, prayer, ritual):** earned by genre.

For nonfiction:
- **Argument under construction (working through):** patterns may be earned.
- **Recommendation paragraphs:** usually unearned. The "DC must commit to..." templates are pose.
- **Concession passages:** false-balance constructions are usually unearned.
- **Evidence deployment:** authority laundering is usually unearned.

### 5. Output

Structure the audit:

```
1. Layer A summary: magnitude band + per-signal quantiles + which signals are most compressed
2. Layer B summary table: flags fired, severity, compounds
3. Top 3 patterns (stated as reader-impact claims, not codes)
4. Layer C verdict (if run): per-passage earned/unearned with reasoning
5. Passage-by-passage findings: flags + quoted evidence + source-triage verdict
6. Revision priorities
```

**Revision priorities** classify each flagged passage:
- **Rewrite** — structural intent is sound but prose needs the writer's voice. Use the flagged passage as outline; replace the sentences, don't edit them.
- **Counteract** — passage is close but needs specific moves injected. Deploy recommended rhetorical figures.
- **Cut** — passage doesn't serve the story or argument structurally and the prose is generic. Remove or reconceive.
- **Keep** — prose works despite its origin.

## Restoration Workflow

Restoration mirrors the diagnostic. For each layer's findings, deploy the corresponding restoration moves.

### Layer A Restoration: Variance Reinjection

Where Layer A shows compressed sentence-length variance, low MATTR, low FKGL std, or high adjacent-sentence cosine similarity, the restoration moves work at the distribution level:

- **Sentence-length variance.** Add fragments. Add longer sentences. Don't normalize toward the middle band.
- **Lexical diversity.** Allow local repetition. Keep idiosyncratic word choices. Resist thesaurus passes.
- **Readability variance.** Don't flatten everything to plain English. Let technical paragraphs spike grade level; let anecdotes drop it.
- **Cohesion variance.** Cut some discourse markers. Allow vague antecedents where the prose can carry them. Preserve missing transitions.
- **Soft n-gram preservation.** Keep original collocations, idioms, and domain phrases verbatim. Full synonymization is a giveaway. Especially preserve multi-word chunks the writer used in their draft.

### Layer B Restoration: Pattern-Specific Countermoves

Read `references/rhetorical-countermoves.md` for the full figure-by-flag pairing system.

Each AIC flag has specific rhetorical figures that counteract it. Each named subtype within AIC-7 has subtype-specific countermoves. Each nonfiction pattern has its own countermove set.

Do not just name the figures. For each fired flag, recommend two or three specific countermoves with the figure name, a one-line definition, why it counteracts this particular AI pattern, and a fresh example showing the move in action.

Do not deploy more than two or three countermoves per passage. A passage with anacoluthon, tmesis, bdelygmia, and scesis onomaton would read like a rhetorical exhibition.

### Layer C Restoration: Voice-Driven Revision

Where Layer C identified unearned patterns:

- **Cut the setup, promote the payoff.** When AI-fluent setup precedes character-voice payoff, delete the setup and let the payoff stand alone.
- **Replace indefinites with concrete imagery.** "Something soft and uncertain" becomes a specific body, object, or action.
- **Cut narrator-pose negation hedges.** Almost always unearned. Let surrounding prose carry.
- **Differentiate voice across characters.** If the same construction appears across multiple POVs at similar density, give each character their own version. Don't use the same self-correction syntax for everyone.
- **Don't fake voice you can't sustain.** When cutting AI-fluent prose leaves a vacuum, sometimes a character-specific phrase fills it. But only when you have real voice to draw from. If you can't reproduce the phrase in another scene with the same character, leave the cut empty.

## Calibration

**Genre matters.** Thriller pacing carries some Velvet Fog if momentum is strong. Literary fiction cannot. Romance requires Puppet Dialogue to be zero. Argument-shaped nonfiction tolerates more Hedge Drift than fiction (qualifications are part of the work). Adjust tolerances.

**False positives exist.** Essayistic narrators (Sebald, Bernhard, Knausgaard) hedge as a formal strategy. Philosophical characters qualify deliberately. Trauma-loop cognition circles. Before flagging AIC-7, check whether the pattern is characterization or contamination. Source triage is the resolution.

**Surface tells decay; categories persist.** "Delve" and excessive em-dashes are yesterday's tells. The underlying habits (lexical convergence, commitment evasion, template loops) outlast any model generation. The named structural patterns are sharper than vocabulary lists because they catch syntactic moves that survive word-level swaps.

**ESL handling.** This skill is a craft diagnostic for the writer revising their own work, not a third-party authorship classifier. ESL writing has lower lexical diversity and lower text perplexity by default, which puts it in the same low-burstiness region as LLM output. Do not run this skill on writing in a writer's second language as if its variance signals carried the same meaning. The bands are calibrated against fluent native or near-native distributions.

**Some writers' natural registers fall in the AI-suspect region.** Empirically verified: a writer with a focused vocabulary, fragment-heavy fiction style, or essayistic long-sentence register can produce pre-AI prose that triggers Layer A heuristic flags. This is the most important false-positive condition in the framework. If a writer was ever accused of using AI before they actually started using AI, their natural distribution sits where the heuristics were calibrated to fire. For these writers, the absolute thresholds are useless and the personal-baseline z-score approach is essential. The diagnostic question becomes "does this draft deviate from the writer's own prior corpus?" rather than "does this draft fall outside the literature's human-baseline range?"

**Short text degrades the diagnostic.** Below 200 words, Layer A statistics become noisy. Below 50 words, they're meaningless. Use Layer B and C only for short passages.

**The em-dash-reduction skill is complementary.** It handles a specific surface tell; this skill handles the categories underneath. A writer whose AI prose has both Generic Hand and excessive em-dashes should run both.

**The prescription sharpens the detection.** If the audit cannot recommend a specific countermove, the diagnosis is too vague. Push for specificity: not "the interiority is flat" but "the interiority self-corrects through identical syntax across three characters." The countermove follows from the specific diagnosis.

**Diminishing returns are real.** First pass cuts substantial AI-fluency. After roughly twenty cuts in a chapter, you're polishing what's already polished. Stop when the next cut starts replacing voice rather than removing pose.

## Limits

This skill measures stylistic provenance, not authorship. When the question is "did a human compose this idea," no surface-form statistic can answer it. When the question is "was this surface form produced by an AI process," every measure here is in some sense valid. The framework's authority comes from being explicit about that distinction. Calibrated band-graded outputs (Lightly / Moderately / Heavily smoothed) report a magnitude of stylistic AI involvement; they do not entitle a reader to conclude anything about idea provenance.

Mathematical impossibility result (Sadasivan et al. 2023): as paraphraser quality approaches the human distribution, AUROC of any detector approaches 0.5. This skill operates well below that asymptote, but the ceiling is real. Heavy paraphrase by sophisticated tools will defeat any stylometric diagnostic, including this one. Layer C source triage is the most paraphrase-resistant layer because it operates on voice attribution rather than surface-form statistics.
