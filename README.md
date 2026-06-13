# setec-voiceprint

**Text-only stylometry for authorial voice in prose. Not audio. Not an AI detector.**

SETEC Voiceprint is an open-source Python framework for measuring patterns in a writer's prose: how varied their sentences are, how diverse their vocabulary is, which rhetorical moves they reach for, and how all of that compares to a baseline corpus of writing (their own, or someone else's). It runs on any laptop. It ships as a Claude Code / Cowork plugin and as standalone CLI scripts.

I call it "glass-box" because every step it takes is exposed and inspectable. You can see which measurement runs, which baseline it compares against, what each number lets you say, and what the next number forbids you from saying. It's slower than a single AI-detection percentage. The slowness is the point. It lets you argue with each step.

One naming note. "Voiceprint" here is the term-of-art from authorship-attribution stylometry (Burrows Delta, function-word fingerprints, the "voice" essayists and developmental editors talk about). It has nothing to do with audio voice identification, speaker recognition, or vocal spectography. Anyone arriving from an audio-AI search will not find what they came for. The "SETEC" tips its hat to *Sneakers* (1992): SETEC ASTRONOMY, "too many secrets." That's a reminder, not a flourish. Authorial-voice profiles are voice-cloning inputs at the prose level. A profile built from a writer's corpus can be used to impersonate that writer's prose, the same way an audio voiceprint can be used for audio impersonation.

## What it does

SETEC runs four kinds of measurement.

**Distributional measurements** look at the statistical shape of a piece of prose: sentence-length variance, lexical diversity, reading-level spread, syntactic templates, semantic cohesion between adjacent sentences, per-token surprisal under a small language model. Each measurement tells you what it found (e.g., "this prose is unusually flat in sentence-length variance") and refuses to tell you why. The same flatness can come from LLM smoothing, heavy human revision, register conventions, ESL writing, translation, or a writer consciously imitating themselves.

**Craft-pattern audits** count the rhetorical tics LLMs tend to overproduce: correctio ("not X, but Y"), triplet structure, manifesto cadence, paragraph-final aphoristic kicker, image conjunction (concrete object made the subject of an abstract verb), prestige-metaphor scatter, professional-parallel stack. When a pattern fires above baseline, the audit asks you to look at each flagged passage and decide whether the pattern is doing real work in the prose or is generic AI dressing. Source-triage is judgment work, not algorithm. Sometimes the call is wrong — that's the nature of triage. The framework's job is to surface the candidates with their measured evidence and refuse to convert that evidence into a verdict the math doesn't entitle.

**Voice-attribution scores** compare a draft to a baseline corpus, looking for drift. The baseline can be the writer's own prior work or someone else's. Measurements include Burrows Delta on function words, per-feature cosine distance, idiolect detection (which phrases are distinctive to the writer and worth preserving across a revision), POV voice differentiation, and voice drift over time.

**Discrimination-evidence audits** are the methods that get closest to a binary "is this AI" call: per-token surprisal under a small reference language model, two-model perplexity comparison (the Binoculars detector from Hans et al. 2024), and a multi-LLM continuation-distance methodology (SETEC's external mirror). On the published Binoculars benchmark these reach ~95% AUC under matched conditions. The framework ships them deliberately uncalibrated: default thresholds are `None`, the verdict band reads `uncalibrated` until an operator supplies per-corpus thresholds. That's the operator-side calibration discipline that runs through everything in the validation surface — the framework provides the method, the operator provides the comparator.

The measurements are organized into five task surfaces, the workflows you'd actually run.

| Task surface | What you'd use it for | What it can't tell you |
|---|---|---|
| Prose smoothing / compression | "Has this prose narrowed into a flat, template-heavy region?" | Who caused it; whether AI was involved |
| Voice coherence | "How far is this draft from a writer, register, POV, or time-period baseline?" | Why it drifted |
| Validation and calibration | "How well do these signals behave on this labeled corpus or fairness slice, under this comparator and (judge × generator) routing?" | Whether they generalize beyond that corpus |
| Craft restoration | "Which measured drifts can become safe revision instructions, and did the revision preserve meaning?" | Whether the revision is artistically better |
| Discrimination evidence (uncalibrated by default) | "What evidence does a surprisal scan, a two-model perplexity comparison, or a multi-LLM continuation-distance comparison produce on this target?" | "Is this AI" as a binary verdict; per-corpus thresholds are operator-side |

Each measurement carries a `claim_license` block in its JSON output: what the result entitles, what comparison set produced it, what it does not entitle. The discipline lives in those blocks. Tools and scripts that mix surfaces or collapse the licensure rules are explicit anti-goals.

### Decision-audit surfaces (consumer handoff)

Alongside the five task surfaces, the framework ships a separate family of **decision-audit** surfaces. These don't measure surface style; they measure how a *narrative* or an *argument* is structurally built, scored against a paper's reported human/LLM group means — structural tells that survive the sentence-level rewriting that scrubs stylistic artifacts. They're built as JSON handoffs for a downstream consumer (`apodictic`) rather than as headline laptop workflows, and like the discrimination surface they ship **uncalibrated** (no default thresholds; register-bound anchors are a directional reference, never a verdict).

| Decision-audit surface | What it scores | Anchored to |
|---|---|---|
| `narrative_decision_audit` (StoryScope) | Narrative-decision features of long-form fiction (≥ ~2000 words) | Russell et al. 2026 |
| `argument_decision_audit` (ArgScope) | Argumentative structure of public-debate / op-ed-register essays (≥ ~300 words) | Kim et al. 2026 |

Both take a pluggable per-document LLM judge (manifest / mock / API backends) and emit the same `claim_license` discipline as everything else. Neither licenses an AI-vs-human provenance verdict or a quality judgment — the papers measure narrative/argumentative *diversity*, not authorship or merit.

## What it isn't

- **Not an AI detector.** SETEC does not output a single "this is AI" percentage. The framework is designed to refuse that conclusion because the underlying measurements do not entitle it (see "Why no verdict" below). Surface 5's discrimination tools come closest, and the framework still refuses to ship per-corpus thresholds as defaults.
- **Not audio voice ID.** No spectrograms, no speaker recognition, no microphone input. Text only.
- **Not speech-to-text, AI image detection, or plagiarism detection.** None of these.
- **Not a verdict generator of any kind.** Its job is to produce auditable measurements with disclosed scope.

## Why no verdict

Three lines of evidence about what stylometric measurement can do honestly.

**Optimization targets.** A maintained ecosystem of "humanizer" tools (OpenClaw skills archive, brandonwise/humanizer with 136 calibration tests, similar projects across HuggingFace and GitHub) exists to help users edit prose to evade AI detectors. These tools detect "AI tells" (banned vocabulary tiers, em-dash overuse, sentence-rhythm patterns, sycophantic phrasing) and rewrite text to suppress them. A framework that ships a single verdict gives the humanizer ecosystem a fixed optimization target. The framework's value reduces to the half-life of that target.

**Cross-corpus polarity volatility.** Full Tier 1 calibration surveys ran against two separate labeled corpora on consecutive days: EditLens (1,506 essays, ESL-student human comparator, May 10 2026) and MAGE (338k rows across 10 source datasets, May 11 2026). Every Tier 1 signal that produced a comparable measurement on both corpora flipped direction between them. Structural-variance signals (burstiness, sentence-length SD, FKGL standard deviation, connective density) matched the smoothing hypothesis on EditLens and inverted on MAGE. Lexical-diversity signals (MATTR, MTLD, Yule's K, Shannon entropy) inverted on EditLens and matched on MAGE. Signal polarity depends on what the human comparator looks like. No single labeled corpus produces a threshold that generalizes. Documented at `plugins/setec-voiceprint/references/calibration-findings-2026-05-11-mage.md`.

**Measurement is not adjudication.** A measurement that distinguishes prose with mode-collapse properties from prose without them is not the same as a measurement that identifies AI authorship. The same statistical properties have many causes: LLM smoothing, but also genre conventions, professional copyediting, register shift, ESL writing, translation, dictation cleanup, intentional voice imitation, time drift, POV collapse. SETEC treats measurement and adjudication as different claims with different licensure rules. Surface 5's discrimination tools tighten the gap (Hans et al. 2024 reports ~95% AUC on Binoculars under matched conditions), but the framework still refuses to ship per-corpus thresholds as defaults — operator-side calibration is required before any verdict above `uncalibrated`.

The operational consequence: SETEC ships methods and tooling. It does not ship per-signal decision thresholds derived from labeled corpora as load-bearing defaults. Users who want anchored thresholds for their own context run `calibrate_thresholds.py` (Surface 3) or `binoculars_calibrate.py` (Surface 5) against their own baseline corpus. The framework provides the methodology. The user provides the comparator.

## Quick start

All commands below run from the repo root. Script paths are written out as `plugins/setec-voiceprint/scripts/...` so they run as typed; private-baseline paths like `../ai-prose-baselines-private/...` resolve to the conventional sibling-to-repo location.

```
# Whole-document smoothing diagnosis on a draft
python3 plugins/setec-voiceprint/scripts/variance_audit.py path/to/draft.txt

# Same, with JSON output (carries the claim_license block)
python3 plugins/setec-voiceprint/scripts/variance_audit.py path/to/draft.txt --json

# Compare against a personal baseline (recommended; see "Personal baselines" below)
python3 plugins/setec-voiceprint/scripts/variance_audit.py path/to/draft.txt --baseline-dir baselines/personal/

# Voice-distance against a private baseline corpus
python3 plugins/setec-voiceprint/scripts/voice_distance.py path/to/draft.txt --baseline-dir ../ai-prose-baselines-private/fiction/

# Build a private voice profile
python3 plugins/setec-voiceprint/scripts/voice_profile.py --baseline-dir ../ai-prose-baselines-private/fiction/ \
    --out ../ai-prose-baselines-private/fiction_voice_profile.md

# Count named rhetorical patterns in a draft (correctio, triplet, manifesto cadence, etc.)
python3 plugins/setec-voiceprint/scripts/aic_pattern_audit.py path/to/draft.txt

# Validate a corpus manifest before any manifest-driven workflow
python3 plugins/setec-voiceprint/scripts/manifest_validator.py corpus_manifest.jsonl

# Sliding-window scan to localize compression inside a long document
python3 plugins/setec-voiceprint/scripts/variance_audit.py path/to/draft.txt --window-size 1000 --window-stride 500

# Surface 5: Binoculars two-model perplexity audit (uncalibrated by default)
python3 plugins/setec-voiceprint/scripts/binoculars_audit.py path/to/draft.txt --scorer tinyllama --observer gpt2
```

Smoke test (no baseline required):

```
python3 plugins/setec-voiceprint/scripts/variance_audit.py plugins/setec-voiceprint/scripts/test_data/human_sample.txt
# Expected: Insufficient signal (169 words, below all length floors)

python3 plugins/setec-voiceprint/scripts/variance_audit.py plugins/setec-voiceprint/scripts/test_data/ai_sample.txt
# Expected: Moderately smoothed, burstiness_B flagged
```

A fuller catalog of usage examples (cross-chapter dashboards, idiolect-phrase extraction, restoration-packet generation, validation harnesses, calibration pipeline, Surface 5 discrimination tools) lives in `plugins/setec-voiceprint/scripts/README.md`.

## Installation

SETEC can be installed two ways: **as a Claude Code / Cowork plugin** (skills become invocable from inside a session) or **as a standalone CLI** (run the Python scripts directly). Both paths share the same Python dependencies.

### Python dependencies (both paths)

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

This installs spaCy (Tier 2: POS-bigrams, dependency distance per sentence), SciPy (length-matched bootstrap), scikit-learn (Tier 3 cohesion via TF-IDF fallback, validation-harness ranking metrics), and statsmodels (validation-harness confidence intervals). The spaCy English model `en_core_web_sm` is not on PyPI and must be downloaded separately.

`requirements.txt` records optional dependencies in commented form: `sentence-transformers` for calibrated cohesion cosines (pulls in torch; heavier), `textstat` for tightened FKGL later, and `nltk>=3.8` for `idiolect_detector.py`'s optional Brown reference-corpus path (also requires `python -m nltk.downloader brown` to fetch the corpus data).

Tier 1 signals (sentence-length variance, MATTR, MTLD, Yule's K, Shannon entropy, FKGL, connective density, function-word ratio) run on the standard library alone. The install above is what's needed for Tier 2 and Tier 3.

Calibration and Surface 5 toolchains are opt-in:

```
pip install -r requirements-calibration.txt   # adds huggingface_hub + pyarrow
pip install -r requirements-surprisal.txt     # adds transformers + torch (Tier 4, Binoculars, external-mirror)
```

See "Calibration costs" below before starting a calibration run.

### Plugin install: Claude Code CLI / Desktop

```
claude plugin marketplace add anotherpanacea-eng/setec-voiceprint
claude plugin install setec-voiceprint@setec-voiceprint
```

Then invoke skills naturally from a Claude Code session ("compare this draft to my baseline", "audit this chapter for smoothing", "build a voice profile from this corpus") or via slash commands.

Updating: `claude plugin marketplace update setec-voiceprint` followed by `claude plugin update setec-voiceprint`, then fully quit (Cmd-Q on Mac, not just close window) and relaunch. A full quit-and-relaunch is required for skill changes to take effect.

### Plugin install: Cowork desktop

In Cowork's Plugins panel, add the marketplace by entering `anotherpanacea-eng/setec-voiceprint`. Cowork fetches the plugin from GitHub and surfaces its skills in the next session.

Updating: re-add the marketplace path through the same UI and accept the update prompt that appears. The cached plugin snapshot under `~/Library/Application Support/Claude/local-agent-mode-sessions/<session>/rpm/plugin_<id>/` refreshes to the new version.

If you've installed via `--plugin-dir` against a local clone, updates won't propagate via `git pull` and a Cowork restart. That path treats the plugin as a one-time snapshot. Remove the `--plugin-dir` install and re-add via the marketplace path. Plausibly a Cowork product gap; worth filing if you hit it.

### Standalone CLI (no plugin install)

Install the Python dependencies above and run the scripts directly. The Quick Start above covers the main entry points.

## Plugin skills

The plugin exposes skills as workflows, not just commands.

| Skill | Use when |
|---|---|
| `setup` | Installing dependencies, checking optional tiers, fixing environment gaps |
| `smoothing-diagnosis` | Auditing compression, variance loss, repetition, syntactic drift |
| `voice-coherence` | Comparing a draft to a writer, register, POV, or time-period baseline |
| `validation` | Validating manifests, checking corpus hygiene, running harnesses and calibration pipelines |
| `craft-restoration` | Reading named patterns and deciding what is earned in context |
| `metric-targeted-restoration` | Turning diagnostic JSON into bounded revision packets with post-checks |
| `corpus-acquisition` | Building private baseline and impostor corpora from blogs, takeouts, PDFs |

## Privacy notice

Voice profiles, idiolect reports, and personal baseline corpora are voice-cloning inputs. They allow prose-level impersonation of the writer they were built from. Treat them as cloning-grade inputs by default.

The `baselines/` directory ships with empty per-genre subdirectories (`literary-fiction/`, `academic-philosophy/`, `blog-essay/`, `testimony-policy/`, `personal/`) intended to be populated locally with the user's own work. The recommended layout keeps personal baselines in a separate private folder (a sibling to this repo) rather than inside it. `voice_profile.py` and `idiolect_detector.py` default to refusing output paths outside that private location unless `--allow-public-output` is passed explicitly.

`manifest_validator.py` enforces a privacy ratchet on voice-profile and idiolect manifest entries: any entry whose privacy field is not literally `'private'` raises a warning.

**Locating an existing baselines folder.** Many users sync `ai-prose-baselines-private/` via Obsidian, iCloud, Dropbox, or similar so the same corpus is visible from every machine and every worktree. SETEC honors the `SETEC_BASELINES_DIR` environment variable as the explicit baselines root. When that variable is unset, acquisition scripts fall back to a sibling of the repo, which can break inside a git worktree or after `git clone` into a fresh directory. Before the first acquisition or voice-profile run, run `python3 plugins/setec-voiceprint/scripts/baseline_discovery.py` (the `setup` skill calls this automatically). It reads state only, searches the common sync locations, and prints the `export SETEC_BASELINES_DIR="..."` line to add to your shell rc. Non-destructive; never creates folders on the user's behalf.

## Personal baselines

The personal baseline is the strongest diagnostic surface SETEC supports. Heuristic thresholds catch unsubtle cases. Any writer with a focused vocabulary or essayistic long-sentence style will land in literature's "compressed" region by absolute standards. Always run with a personal baseline if available.

The 537-post pre-AI WordPress archive cited in calibration runs is one example of a workable personal baseline: ~430k words, register-matched, confirmed pre-AI by date.

A minimum-viable personal baseline is 3-5 files at 3,000+ words each in the relevant register. More is better. The per-genre baseline READMEs at `baselines/*/README.md` document compilation strategy for each register.

For a per-capability map of what each tool needs to run, what corpus you must supply yourself, and how far to trust its output before you calibrate, see [`plugins/setec-voiceprint/references/calibration-readiness.md`](plugins/setec-voiceprint/references/calibration-readiness.md) — the "build your own runway" guide for users without the maintainer's private corpora.

## Stylometric tests

SETEC computes 56 stylometric measurements across 14 families. Full per-signal inventory (paths, polarity, calibration status, definitions) at `plugins/setec-voiceprint/references/signals-glossary.md`. Headline summary by tier:

- **Tier 1 variance (9):** sentence-length burstiness, sentence-length SD, MATTR, MTLD, Yule's K, Shannon entropy, FKGL standard deviation, connective density, function-word ratio.
- **Tier 2 syntax (3):** POS-bigram entropy, POS-bigram KL divergence, mean dependency distance SD.
- **Tier 3 trajectory (4):** adjacent-sentence cosine mean and SD, semantic trajectory cosine series and slope.
- **Tier 4 surprisal (3):** per-token surprisal mean, SD, and autocorrelation lag-1.
- **AIC-7 discourse leak (4):** correctio, triplet, manifesto cadence, professional parallel stack.
- **AIC-8 aesthetic-authority laundering (2):** image conjunction density, prestige-metaphor scatter entropy.
- **AIC-9 closure inflation (1):** kicker density.
- **Voice family (28):** Burrows Delta, per-feature cosine distance, voice drift, POV voice profiles, mimicry/cosplay audits, semantic preservation, phraseology, punctuation cadence, stance/modality, bigram-KL.
- **Repetition (2):** vocabulary repetition ratio, cluster maximum.

Surface 5 tools (`binoculars_audit.py`, `external_mirror/`) produce structured evidence packs rather than COMPRESSION_HEURISTICS signals; they don't add to the 56-signal count.

## Repository layout

```
setec-voiceprint/
├── README.md                       this file
├── SKILL.md                        plugin entry point: architecture, modes, workflows
├── ROADMAP.md                      public-facing roadmap and project narrative
├── CHANGELOG.md                    release history
├── LICENSE                         GPL-3.0-or-later (governs code)
├── LICENSE-docs                    CC BY-SA 4.0 (governs prose)
├── NOTICE                          file-by-file scope statement
├── requirements.txt                runtime dependencies (Tier 1-3 core)
├── requirements-calibration.txt    opt-in: huggingface_hub + pyarrow
├── requirements-surprisal.txt      opt-in: transformers + torch (Tier 4, Binoculars, external-mirror)
├── plugins/setec-voiceprint/       plugin tree:
│   ├── skills/                     seven workflow skills
│   ├── references/                 Layer A math, AIC flag families, signals glossary, calibration findings, craft-pattern docs
│   └── scripts/                    Python tools: variance + voice + manifest validation, calibration pipeline, Binoculars + external-mirror, restoration utilities
└── baselines/                      genre-binned scaffolding (private corpora live in a sibling private directory)
```

Full per-script catalog (Usage / Output / Notes per script) at [`plugins/setec-voiceprint/scripts/README.md`](plugins/setec-voiceprint/scripts/README.md). Reference documentation (signals glossary, per-layer math, per-corpus calibration findings, craft-pattern references) under [`plugins/setec-voiceprint/references/`](plugins/setec-voiceprint/references/).

## Calibration costs

Four of the five task surfaces have small footprints. Smoothing diagnosis and voice coherence run in seconds to minutes on a laptop. Validation against labeled fixtures runs in seconds. Craft restoration is local-text work. Surface 5's discrimination audits run two small LLMs in memory (default `tinyllama` + `gpt2`, ~1.5 GB combined) — laptop-feasible but noticeably heavier than the prose-only surfaces, and `external_mirror/` requires operator-side LLM access for the continuation step. Calibration (re-deriving thresholds from RAID, MAGE, or another large labeled corpus) is the one tier where the framework asks for non-trivial disk, time, and (optionally) GPU.

Headline figures from May 2026 RAID + MAGE runs on an M-series MacBook:

- **Disk.** RAID corpus ~16 GB (8M rows). MAGE corpus ~528 MB (436k rows). Manifests rebuilt from CSVs as needed. Budget 25-28 GB for a full RAID + MAGE setup with calibration dependencies.
- **Time.** MAGE survey (436k rows, all Tier 1 signals) runs 11-18 hours single-threaded. RAID survey (8M rows) runs 6-13 days single-threaded. Single-threaded RAID is not recommended; the sharded toolchain cuts RAID to under a week with 8 shards.
- **Memory.** Single-shard Tier 1 peaks around 250 MB resident. Tier 2 adds ~1 GB per worker (spaCy). Tier 3 with SBERT adds another ~1.5 GB. RAID-scale is still memory-light per shard.
- **GPU.** Tier 1 and Tier 2 are CPU-bound. Tier 3 with SBERT benefits from GPU but isn't blocked by its absence. A discrete GPU cuts Tier 3 wall-clock 3-5x for multi-day RAID runs.

Practical guidance:

- Don't run calibration on battery.
- Run MAGE first (overnight on a single laptop) as a feasibility check before committing to RAID.
- Smoothing diagnosis and voice coherence are unaffected. Calibration only re-runs when signals or labeled corpora change, typically once per major release.

For the full calibration pipeline (bake-off matrix runner, length-stratified subsampling, per-comparator + per-(judge × generator) direction routing, slicer + polarity audit chainer, provenance ledger), see `plugins/setec-voiceprint/scripts/README.md` and `plugins/setec-voiceprint/scripts/calibration/PROVENANCE.md`.

## Design principles

**Target discourse habits, not vocabulary.** Surface tells (specific AI words, em-dash frequency, the magic triple) decay as models change and writers learn to avoid them. The named patterns are structural habits: hedge-and-reversal moves, pseudo-aphoristic cadence, template rhythm, inflated parallelism, over-neat transitions, manifesto cadence, indefinite-pronoun gestures. They survive vocabulary changes because they are moves in prose, not magic words.

**Keep the three layers distinct.** Layer A is mathematical (distributional diagnostics). Layer B is craft-pattern recognition (AIC flag families). Layer C is voice attribution (earned/unearned source-triage). The framework's value depends on not collapsing them.

**Source triage is the hardest part to teach and the most valuable.** Most surface flags resolve on source triage as earned. The framework's authority comes from being honest about that — and from being honest that source-triage is judgment work, not algorithm, and gets cases wrong sometimes. The honest job is surfacing candidates with their measured evidence; converting that evidence into a verdict is what the framework refuses to do.

**Genre tolerance varies meaningfully.** A pattern that signals trouble in literary fiction may be partially structural to testimony or blog. The genre-tolerance table at `plugins/setec-voiceprint/references/aic-flags.md` consolidates calibration notes.

## Citation and further reading

The mathematical foundation for Layer A is documented at `plugins/setec-voiceprint/references/distributional-diagnostics.md`. Core sources: Burrows (Delta), Stamatatos (POS n-grams), Liu and Futrell-Mahowald-Gibson (dependency distance), Tanaka-Ishii and Aihara (Yule's K constancy), Reviriego et al. (LLM lexical diversity), Muñoz-Ortiz et al. (LLM burstiness), Hans et al. (Binoculars), Bao et al. (Fast-DetectGPT), Thai et al. (EditLens), Emi and Spero (Pangram), Sadasivan et al. (paraphrase impossibility result).

Implementation survey at `plugins/setec-voiceprint/references/implementation-survey.md`.

## License

Dual-licensed:

- **Code** (everything under `plugins/setec-voiceprint/scripts/`, plus root-level configuration files): GNU General Public License, version 3 or later (`GPL-3.0-or-later`). Canonical text in `LICENSE`.
- **Documentation and reference prose** (this README, `ROADMAP.md`, `CHANGELOG.md`, `SKILL.md`, `plugins/setec-voiceprint/references/*.md`, `plugins/setec-voiceprint/scripts/README.md`, `baselines/README.md` and per-genre placeholders, `NOTICE`): Creative Commons Attribution-ShareAlike 4.0 International (`CC-BY-SA-4.0`). Canonical text in `LICENSE-docs`.

See `NOTICE` for the file-by-file scope statement.

Personal baseline corpora and generated voice profiles are not part of this repository and are not licensed for redistribution from any baseline directory structure shipped here.

## Status

Active research-grade toolkit. It began as an AI-prose detection skill. Its center of gravity is now broader: voice coherence, prose transformation, validation discipline, revision-safe restoration under explicit claim limits, and structured discrimination evidence under operator-side calibration.
