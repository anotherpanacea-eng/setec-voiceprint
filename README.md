# setec-voiceprint

**Text-only stylometry for authorial voice in prose. Not audio.**

SETEC Voiceprint is a stylometric framework for measuring **authorial voice in prose** — the writer-level patterns visible in a manuscript's text. It is text-only. It has nothing to do with audio voice identification, speaker recognition, or vocal spectography. "Voiceprint" here is the term-of-art borrowed from authorship-attribution stylometry (Burrows Delta, function-word fingerprints, the "voice" essayists and developmental editors talk about), not the audio-processing sense. Anyone arriving from an audio-AI search will not find what they came for.

What it does: diagnose smoothing in prose, compare a draft against a writer's own prior corpus, build private authorial-voice profiles, surface idiolect features worth preserving, validate stylometric signals against labeled corpora, and generate revision-safe restoration packets. It is built for writers, editors, researchers, and toolmakers who need evidence about prose transformation without collapsing that evidence into an overconfident verdict.

SETEC is not an AI detector. It also is not an audio voice ID tool. The "voice" it measures is the *authorial voice* a writer's prose carries, not a speaker's vocal signature.

AI-assisted writing is one important use case because LLM collaboration often leaves smoothing, compression, syntactic drift, and phrase-preservation artifacts in prose. But the same signals can also come from genre, education, dialect, translation, institutional templates, human editing, time drift, POV collapse, or a writer consciously imitating themselves. SETEC's core discipline is to measure the signal, name the comparison set, and refuse claims the evidence does not license.

The "SETEC" in the name tips its hat to *Sneakers* (1992): SETEC ASTRONOMY, "too many secrets." Authorial-voice profiles are voice-cloning inputs at the prose level — a profile built from a writer's corpus can be used to impersonate that writer's prose voice, the same way a real voiceprint can be used for audio impersonation. The framework's outputs are useful to the writer who runs them; they are also leverage to anyone else who gets hold of them. The "setec" in the name is a reminder, not a flourish.

The framework distinguishes four task surfaces, three diagnostic layers, and a vocabulary of named prose patterns. The Python tooling supports distributional diagnostics on prose, authorial-voice-coherence comparison, vocabulary-repetition audits, corpus acquisition, manifest validation, empirical calibration, and craft restoration of prose.

## Choose the question

| Surface | Tools | Question it answers | Question it does NOT answer |
|---|---|---|---|
| **1. Prose smoothing / compression** | `variance_audit.py`, `surprisal_audit.py`, `manuscript_audit.py`, `repetition_audit.py`, `manuscript_repetition_audit.py`, `chapter_distinctiveness_audit.py`, `bigram_diff.py`, `manuscript_bigram_diff.py`, `sliding_window_heatmap.py` | Has this prose narrowed into a low-variance or template-heavy region? | Who caused it; whether AI was involved |
| **2. Voice coherence** | `voice_distance.py`, `voice_profile.py`, `idiolect_detector.py`, `voice_drift_tracker.py`, `pov_voice_profile.py`, `mimicry_cosplay_audit.py` | How far is this draft from a writer, register, POV, or time-period baseline? | Why it drifted |
| **3. Validation and calibration** | `manifest_validator.py`, `check_corpus.py`, `validation_harness.py`, `voice_validation_harness.py`, `calibration_drift_monitor.py`, `fairness_dialect_guardrails.py`; calibration pipeline (`calibration_survey.py`, `calibrate_thresholds.py`, `polarity_audit.py`, `slice_bakeoff_v2.py`, `bakeoff_matrix.sh`, `queue_slice_after_matrix.sh`, `cross_polarity_audit.py`, `bakeoff_mage_tier34.sh`) | How well do these signals behave on this labeled corpus, dependency stack, fairness slice, comparator class, and (judge × generator) slice? | Whether they generalize outside that evidence |
| **4. Craft restoration** | `aic_pattern_audit.py`, `restoration_packet.py`, `before_after_restoration.py`, `semantic_preservation_check.py`, `known_editor_profile.py`; reference docs | Which measured drifts can become safe revision instructions, and did revision preserve meaning? | Whether the revision is artistically better |
| **5. Discrimination evidence (uncalibrated by default)** | `binoculars_audit.py`, `binoculars_calibrate.py`, `external_mirror/` (`build_prompts.py`, `ingest_outputs.py`, `compute_distances.py`, `compose_evidence_pack.py`, `workflow.py`) | What evidence does a per-token two-model perplexity comparison or a multi-LLM continuation-distance comparison produce on this target? | "Is this AI" — the framework ships uncalibrated bands; per-corpus thresholds are operator-side |

The first four surfaces share statistical signals because RLHF-induced mode collapse, register conventions, and time-stable authorial idiolect all leave traces in the same features. They answer different questions and license different claims. The framework refuses the unifying "is this AI" verdict because the underlying math does not entitle it. **Surface 5** is the deliberate accommodation: external-mirror discrimination (Hans et al. 2024 Binoculars and the SETEC external-mirror methodology) produces *structured evidence* about AI coupling without shipping a verdict — `DEFAULT_THRESHOLD_LOW = DEFAULT_THRESHOLD_HIGH = None`, verdict bands read `uncalibrated` by default, and the framework's "no thresholded claims without calibration" discipline applies. The calibration scripts in Surface 3 are the operator-side path to thresholded claims; the framework provides methodology, the operator provides the comparator.

Every script's JSON output and markdown report carry an explicit `task_surface` field so downstream consumers can route by surface and refuse to mix scores across them.

## Why no verdict

SETEC's "is not an AI detector" posture is not modesty or hedging. It is the conclusion of three distinct lines of evidence about what stylometric measurement can and cannot do honestly.

**Optimization targets.** A maintained ecosystem of "humanizer" tools (OpenClaw skills archive, brandonwise/humanizer with 136 calibration tests, similar projects across HuggingFace and GitHub) exists specifically to help users edit prose to evade AI detectors. These tools detect "AI tells" — banned vocabulary tiers, em-dash overuse, sentence-rhythm patterns, sycophantic phrasing — and rewrite text to suppress them. They share substantial vocabulary and pattern catalogs with SETEC's signals. The inverse purpose is the point: a framework that ships a single verdict or threshold gives the humanizer ecosystem a fixed optimization target. The framework's value reduces to the half-life of that target.

**Cross-corpus polarity volatility.** The framework ran full Tier 1 calibration surveys against two separate labeled corpora on consecutive days. EditLens val split (1,506 essays, ESL-student human comparator, 2026-05-10) and MAGE (338k rows across 10 source datasets, 2026-05-11). Every Tier 1 signal that produced a comparable measurement on both corpora **flipped direction between them**. Structural-variance signals (`burstiness_B`, `sentence_length_sd`, `fkgl_sd`, `connective_density`) matched the framework's smoothing-diagnosis hypothesis on EditLens and inverted it on MAGE. Lexical-diversity signals (`mattr`, `mtld`, `yules_k`, `shannon_entropy`) inverted on EditLens and matched on MAGE. The pattern is consistent, not random: signal polarity depends on what the human comparator looks like, and no single labeled corpus produces a threshold that generalizes. Cross-corpus polarity inversion is documented in `plugins/setec-voiceprint/references/calibration-findings-2026-05-11-mage.md` and the predecessor 2026-05-10 doc.

**Measurement is not adjudication.** The four task surfaces measure properties of prose — variance compression, distance from baseline, named-pattern density, semantic trajectory. The same properties have many causes (LLM smoothing, but also genre conventions, professional copyediting, register shift, ESL writing, translation, dictation cleanup, intentional voice imitation, time drift, POV collapse). A measurement that distinguishes prose with mode-collapse properties from prose without them is not the same thing as a measurement that identifies AI authorship. The framework treats those as different claims with different licensure rules; the evidence does not entitle conflation.

The operational consequence is the "Stylometry to the people" calibration posture (see `plugins/setec-voiceprint/scripts/calibration/PROVENANCE.md`): SETEC ships methods, tooling, and provenance discipline. It does not ship per-signal decision thresholds derived from labeled corpora as load-bearing defaults. Users who want anchored thresholds for their own context run `calibrate_thresholds.py` against their own baseline. The framework provides the methodology; the user provides the comparator.

Each output's `claim_license` block names what the result entitles, what comparison set produced it, and what it does not entitle. The discipline is enforceable at the block, not vigilable by readers. Tools and scripts that mix surfaces or collapse the licensure rules are explicit anti-goals (see `ROADMAP.md`, "Explicit anti-goals").

## Plugin skills

The plugin exposes skills as workflows, not just surfaces.

| Skill | Use when |
|---|---|
| `setup` | Installing dependencies, checking optional tiers, fixing environment gaps |
| `smoothing-diagnosis` | Auditing compression, variance loss, repetition, and syntactic drift |
| `voice-coherence` | Comparing a draft to a writer, register, POV, or time-period baseline |
| `validation` | Validating manifests, checking corpus hygiene, running harnesses and calibration checks |
| `craft-restoration` | Reading named patterns and deciding what is earned in context |
| `metric-targeted-restoration` | Turning diagnostic JSON into bounded revision packets with post-checks |
| `corpus-acquisition` | Building private baseline and impostor corpora from blogs, takeouts, PDFs, and magazines |

## Stylometric tests

SETEC computes 56 stylometric measurements across 14 families. Reference inventory below; signal paths, polarity, status (calibrated / literature_anchored / empirically_oriented / heuristic / structural_only), and per-signal definitions live in [`plugins/setec-voiceprint/references/signals-glossary.md`](plugins/setec-voiceprint/references/signals-glossary.md).

- **Tier 1 variance (9)** — sentence-length burstiness (B), sentence-length SD, MATTR, MTLD, Yule's K, Shannon entropy, Flesch-Kincaid grade SD, connective density, function-word ratio.
- **Tier 2 syntax (3)** — POS-bigram entropy, POS-bigram KL divergence, mean dependency distance SD.
- **Tier 3 trajectory (4)** — adjacent-sentence cosine mean, adjacent-sentence cosine SD, semantic trajectory cosine series, semantic trajectory slope.
- **Tier 4 surprisal (3)** — per-token surprisal mean, per-token surprisal SD, per-token surprisal autocorrelation lag-1.
- **AIC-7 discourse leak (4)** — correctio density, triplet density, manifesto cadence density, professional parallel stack density.
- **AIC-8 aesthetic authority laundering (2)** — image conjunction density, prestige-metaphor scatter entropy.
- **AIC-9 closure inflation (1)** — kicker density.
- **Voice-distance (2)** — Burrows Delta (function-word), per-feature cosine distance.
- **Voice-drift (2)** — voice drift (cross-period coefficient of variation), voice stability.
- **POV-voice (2)** — POV voice-distance matrix, POV voice-collapse verdict.
- **Mimicry / cosplay (2)** — lexical mimicry survival, syntactic mimicry (POS-trigram Delta).
- **Semantic preservation (3)** — claim inventory, named-entity preservation, citation / authority preservation.
- **Phraseology (5)** — lexical bundle survival, slot-frame survival, idiom survival, stance-frame survival, hapax-phrase survival.
- **Punctuation cadence (4)** — sentence-final punctuation distribution, punctuation bigrams, interruption grammar, comma-period share.
- **Stance / modality (7)** — deontic modality, epistemic modality, hedge density, booster density, evidential density, first-person stance density, refusal / negation density.
- **Bigram-KL (1)** — per-bigram KL contribution.
- **Repetition (2)** — vocabulary repetition ratio, cluster maximum.

## Files

```
setec-voiceprint/
├── README.md                       (this file)
├── SKILL.md                        skill entry point: 3-layer arch, 2 modes, workflows
├── ROADMAP.md                      public-facing roadmap and project narrative
├── CHANGELOG.md                    release history and notable changes
├── LICENSE                         GPL-3.0-or-later (canonical text, governs code)
├── LICENSE-docs                    CC BY-SA 4.0 (canonical text, governs prose)
├── NOTICE                          dual-license scope: which files each license governs
├── requirements.txt                runtime deps (spaCy + SciPy + scikit-learn + NLTK) and optional extras
├── .claude-plugin/
│   └── marketplace.json            Claude Code / Cowork plugin marketplace catalog
├── plugins/
│   └── setec-voiceprint/           plugin tree: manifest + seven workflow skills
│       ├── .claude-plugin/plugin.json
│       └── skills/
│           ├── setup/                         install/dependency guidance
│           ├── smoothing-diagnosis/           Surface 1: prose smoothing/compression
│           ├── voice-coherence/               Surface 2: writer/register/POV voice comparison
│           ├── validation/                    Surface 3: manifest checks, harnesses, calibration
│           ├── craft-restoration/             Surface 4: pattern triage and craft repair
│           ├── metric-targeted-restoration/   Surface 4: diagnostic JSON → revision-safe packets
│           └── corpus-acquisition/            private baseline/impostor corpus collection
├── plugins/setec-voiceprint/references/
│   ├── signals-glossary.md             authoritative index of all 56 signals across 14 families
│   ├── distributional-diagnostics.md   Layer A: variance signals with math
│   ├── aic-flags.md                    Layer B: 7 flag families + nonfiction parallel + genre tolerance table
│   ├── source-triage.md                Layer C: voice attribution, named patterns, earned/unearned triage
│   ├── rhetorical-countermoves.md      figure-by-flag pairings (fiction + nonfiction additions)
│   ├── implementation-survey.md        dependency/reference survey for borrow-before-building work
│   ├── manifest-schema.md              corpus_manifest.jsonl field semantics
│   ├── calibration-findings-*.md       per-corpus calibration findings (EditLens, MAGE)
│   ├── laundering-vocabulary.md        banned-term taxonomy and provenance
│   ├── metric-targeted-restoration.md  restoration-packet methodology
│   └── stylometry-oracle.md            R stylo bridge and authorship-attribution reference
├── plugins/setec-voiceprint/scripts/
│   ├── README.md                       script catalog (long-form per-script usage)
│   ├── variance_audit.py               Layer A computation; sliding-window mode; Tier 3 + 4 hooks
│   ├── surprisal_audit.py              Tier 4 standalone: per-token surprisal against a configurable local LLM
│   ├── sliding_window_heatmap.py       sliding-window output → markdown localization heatmap
│   ├── manuscript_audit.py             cross-chapter Layer A dashboard
│   ├── repetition_audit.py             vocabulary over-representation against external baseline
│   ├── manuscript_repetition_audit.py  manuscript-aggregate vocabulary audit
│   ├── chapter_distinctiveness_audit.py  leave-one-out internal-baseline vocabulary audit
│   ├── stylometry_core.py              shared stylometric feature extraction + compute_clusters
│   ├── surprisal_backend.py            local LLM loader + batched scoring (CUDA / MPS / CPU, fp32 / bf16 / fp16)
│   ├── embedding_backend.py            sbert-family loader for Tier 3 + external-mirror
│   ├── voice_distance.py               target-vs-baseline voice distance with cluster mode
│   ├── voice_profile.py                private baseline voiceprint report
│   ├── idiolect_detector.py            keyness/collocation extraction for preservation lists
│   ├── adversarial_fixtures.py         deterministic Unicode stress-fixture transforms
│   ├── manifest_validator.py           schema + integrity checks; Issue #6 schema-migration tripwire
│   ├── check_corpus.py                 content-level non-prose contamination gate
│   ├── validation_harness.py           empirical validation over labeled manifest entries
│   ├── voice_validation_harness.py     voice-coherence validation harness
│   ├── mimicry_cosplay_audit.py        lexical mimicry without syntactic conformity
│   ├── known_editor_profile.py         learned before/after editorial transformation profile
│   ├── length_bootstrap.py             length-matched window sampler + scipy.stats.bootstrap helpers
│   ├── binoculars_audit.py             Surface 5: two-model perplexity-ratio (v1) + cross-perplexity (v2) audit
│   ├── binoculars_calibrate.py         Surface 5: threshold calibration against a labeled manifest
│   ├── external_mirror/                Surface 5: prompt builder + ingest + distance + evidence pack
│   │   ├── build_prompts.py            Phase A: window the target, emit ready-to-paste prompts
│   │   ├── ingest_outputs.py           Phase B: paste-back parser (T3 + T4 formats)
│   │   ├── compute_distances.py        Phase B: per-window pairwise cosine matrix (sbert + v2 metrics)
│   │   ├── compose_evidence_pack.py    Phase B: schema 1.0 envelope + claim-license
│   │   └── workflow.py                 harness: prepare / status / score subcommands
│   ├── calibration/                    Surface 3 calibration pipeline
│   │   ├── PROVENANCE.md               selection-criteria gates + commit-ledger procedure
│   │   ├── RUNBOOK_*.md                operator runbooks (sharded hygiene, multi-machine, Tier 4 install)
│   │   ├── calibration_survey.py       run derive_threshold across every signal; per-corpus survey ledger
│   │   ├── calibrate_thresholds.py     per-signal ROC sweep + threshold + Hanley-McNeil CIs + bootstrap
│   │   ├── polarity_audit.py           comparator-aware sign verdict from slicer CSV
│   │   ├── slice_bakeoff_v2.py         per-stratum AUC + CIs + integrated polarity audit
│   │   ├── cross_polarity_audit.py     cross-corpus polarity comparison (e.g., MAGE × RAID)
│   │   ├── bakeoff_matrix.sh           cloud-portable matrix runner over (embedding × surprisal × signal) cells
│   │   ├── queue_slice_after_matrix.sh polling chainer: matrix output → slicer → polarity audit
│   │   ├── bakeoff_mage_tier34.sh      MAGE Tier 3+4 model-selection bake-off driver
│   │   ├── bakeoff_mage_tier34_compare.py  companion reader: comparison tables + recommended winner
│   │   ├── editlens_to_manifest.py     fetched-corpus → manifest writer (EditLens shape)
│   │   ├── mage_to_manifest.py         ditto for MAGE
│   │   ├── raid_to_manifest.py         ditto for RAID
│   │   ├── fetch_pangram_editlens.py   EditLens corpus fetch (license-gated)
│   │   ├── fetch_mage.py               MAGE corpus fetch
│   │   ├── fetch_raid.py               RAID corpus fetch
│   │   ├── shard_runner.py             distributed-scoring shard worker
│   │   ├── shard_state.py              shard state-machine + checkpoint
│   │   └── sharding.py                 shard partitioning helpers
│   └── test_data/                      smoke-test corpus
└── baselines/
    ├── README.md                   structure and compilation strategy
    ├── literary-fiction/           per-genre directory with placeholder README
    ├── academic-philosophy/        ditto
    ├── blog-essay/                 ditto
    ├── testimony-policy/           ditto
    └── personal/                   intended for the user's own register-matched prior work
```

## Privacy notice

Voice profiles, idiolect reports, and personal baseline corpora are voice-cloning inputs. The `baselines/` directory ships with empty per-genre subdirectories meant to be populated locally with the user's own pre-AI-era work. The recommended layout keeps personal baselines in a separate private folder (a sibling to this repo) rather than inside it, with `voice_profile.py` and `idiolect_detector.py` defaulting to refusing output paths outside that private location unless `--allow-public-output` is passed explicitly.

The `manifest_validator.py` script enforces a privacy ratchet on `voice_profile`- and `idiolect`-tagged manifest entries: any entry whose privacy is not literally `'private'` (including missing or non-string values) raises a warning. Treat voiceprints as cloning-grade inputs by default.

**Locating an existing baselines folder.** Many users sync `ai-prose-baselines-private/` via Obsidian, iCloud, Dropbox, or similar so the same corpus is visible from every machine and every worktree. SETEC honors the `SETEC_BASELINES_DIR` environment variable as the explicit baselines root. When that variable is unset, acquisition scripts fall back to a sibling of the repo, which can break inside a git worktree or after `git clone` into a fresh directory — a fresh SETEC instance will then create a duplicate empty folder rather than reuse the synced one. Before the first acquisition or voice-profile run, run `python3 plugins/setec-voiceprint/scripts/baseline_discovery.py` (the `setup` skill calls this automatically). It reads state only, searches the common sync locations, and prints the `export SETEC_BASELINES_DIR="..."` line to add to your shell rc. The script is non-destructive and never creates folders on the user's behalf.

## Installation

setec-voiceprint can be installed two ways: **as a Claude Code / Cowork plugin** (skills become invocable from inside a session) or **as a standalone CLI** (run the Python scripts directly). Both paths share the same Python dependencies.

### Python dependencies (both paths)

The recommended path is a project-local virtual environment plus `requirements.txt`:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

This installs spaCy (Tier 2: POS-bigrams, MDD per sentence), SciPy (length-matched bootstrap), scikit-learn (Tier 3 cohesion via TF-IDF fallback, plus validation-harness ranking metrics), statsmodels (validation-harness confidence intervals), and NLTK (Brown reference corpus support for `idiolect_detector.py`). The spaCy English model `en_core_web_sm` is not on PyPI and must be downloaded separately.

`requirements.txt` records the optional deps in commented form: `sentence-transformers` for calibrated cohesion cosines comparable to the literature's reference values (heavier — pulls in torch), and `textstat` if you want tightened FKGL later.

Tier 1 (sentence-length variance, MATTR, MTLD, Yule's K, Shannon entropy, FKGL, connective density, function-word ratio) runs on the standard library alone; the install above is what's needed for Tier 2 and Tier 3.

**Calibration toolchain (opt-in).** `scripts/calibration/` provides a per-signal threshold calibration toolchain that consumes labeled corpora (e.g., Pangram Labs' EditLens, CC BY-NC-SA 4.0, gated). It's not part of the core install; opt in with `pip install -r requirements-calibration.txt` (adds `huggingface_hub` + `pyarrow`). The toolchain is local-only by design — corpora download into `ai-prose-baselines-private/` (gitignored) and aggregate derived thresholds get encoded in `COMPRESSION_HEURISTICS` with a `provenance` slug pointing at `scripts/calibration/PROVENANCE.md`. See `scripts/calibration/PROVENANCE.md` for the calibration ledger and `internal/SPEC_calibration_toolchain.md` (gitignored) for the design.

### Plugin install — Claude Code CLI / Desktop

```
claude plugin marketplace add anotherpanacea-eng/setec-voiceprint
claude plugin install setec-voiceprint@setec-voiceprint
```

Then invoke skills naturally from inside a Claude Code session ("compare this draft to my baseline", "audit this chapter for smoothing", "build a voice profile from this corpus", etc.) or via explicit slash commands if you have them configured.

**Updating from CLI / Desktop:**

```
claude plugin marketplace update setec-voiceprint
claude plugin update setec-voiceprint
# Then fully quit (Cmd-Q on Mac, not just close window) and relaunch.
```

A full quit-and-relaunch is required for skill changes to take effect; `/reload-plugins` reloads hooks/MCP/LSP only and does **not** reload skills.

### Plugin install — Cowork desktop

Cowork has two install paths and they behave differently. The marketplace path is what works for ongoing updates.

**Recommended: install via the Cowork plugins UI from the GitHub repo.** In Cowork, open the Plugins panel and add the marketplace by entering `anotherpanacea-eng/setec-voiceprint`. Cowork fetches the plugin from GitHub and surfaces its skills in the next session. This is the path that supports updates: when new commits land on the remote, re-add the marketplace through the same UI and Cowork will detect the version bump and prompt to update.

**Updating in Cowork:**

After pushing a new release (with the version bumped in `plugins/setec-voiceprint/.claude-plugin/plugin.json`), in Cowork: Plugins → re-add the marketplace path `anotherpanacea-eng/setec-voiceprint` → accept the update prompt that appears. The cached plugin snapshot under `~/Library/Application Support/Claude/local-agent-mode-sessions/<session>/rpm/plugin_<id>/` will refresh to the new version.

**Alternative path: `--plugin-dir` against a local clone.** Cowork can also be pointed at a local checkout, but `--plugin-dir`-installed plugins are treated as one-time snapshots: `git pull` on the local checkout does NOT propagate updates to the running Cowork install, even with a version bump and a Cowork restart. Empirical finding (2026-05-08): the cache is invalidated only by the marketplace re-add path. If you've installed via `--plugin-dir`, the working update flow is to remove the `--plugin-dir` install and re-add via the marketplace path above. This is plausibly a Cowork product gap and worth filing if you hit it.

If you've previously installed via `--plugin-dir` and updates seem stuck, the diagnostic is:

```
ls "$HOME/Library/Application Support/Claude/local-agent-mode-sessions/"
# Each session directory has its own rpm/plugin_<id>/ snapshot. Compare
# the served plugin.json version against the source version in your local
# clone or on GitHub. Stale = the cache hasn't refreshed; remediation is
# the marketplace re-add described above.
```

### Standalone CLI (no plugin install)

If you don't want the plugin, install the Python deps as above and run the scripts directly. See the Quick start section below.

## Costs and resources at the calibration tier

Three of the four task surfaces have small footprints. Smoothing diagnosis and voice coherence run in seconds-to-minutes on a laptop. Validation runs on labeled fixtures in seconds. **Calibration — re-deriving thresholds from RAID, MAGE, or another large labeled corpus — is the one tier where the framework asks for nontrivial disk, time, and (optionally) GPU.** Nothing in this section applies to ordinary diagnostic use; it exists so a user planning a calibration run can decide whether to start one.

The figures below are honest measurements from the 2026-05 RAID + MAGE runs on an M-series MacBook, not theoretical estimates.

### Disk

| Resource | Size on disk | Notes |
|---|---|---|
| RAID corpus (full, including adversarial) | ~16 GB | `train.csv` 11 GB + `extra.csv` 3.5 GB + `test.csv` 1.1 GB. 8.0M rows after validator-clean conversion. Downloaded once via `scripts/calibration/fetch_raid.py`. |
| MAGE corpus (full, all 10 source datasets + adversarial) | ~528 MB | `train.csv` 385 MB + `test.csv` 68 MB + `valid.csv` 69 MB + OOD subsets ~5 MB. 436k rows after conversion. |
| RAID `manifest.jsonl` | ~5.0 GB | One JSON-line per row, validator-conformant. |
| MAGE `manifest.jsonl` | ~187 MB | Same shape, smaller corpus. |
| Per-shard survey output | <50 MB | `_survey_full_*.json` carries per-signal histograms. |
| Optional SBERT cohesion model | ~2 GB | `sentence-transformers` install pulls in torch; the MiniLM-L6-v2 model itself is ~90 MB. |
| Optional research-grade embeddings (R12) | ~2.4 GB | `mxbai-embed-large-v1` (Apache 2.0; selected for the Semantic Trajectory Audit work, not yet wired into the public release). |

**Total disk for a full RAID + MAGE setup with calibration deps: budget 25–28 GB.** Corpora live under `$SETEC_BASELINES_DIR/raid/` and `$SETEC_BASELINES_DIR/mage/`; both are gitignored. Manifests can be deleted and regenerated from the CSVs as needed.

### Time (single-threaded, all Tier 1 signals)

Wall-clock figures from a 2026-05 run on an M-series Mac. CPU pegged at 99% on one core for the duration; memory peak ~250 MB resident.

| Run | Wall-clock | Throughput |
|---|---|---|
| MAGE survey (436k rows, all Tier 1 signals) | 11–18 hours | ~7–11 rows/sec |
| RAID survey (8.0M rows, all Tier 1 signals) | **6–13 days single-threaded** | Same throughput; row count is 18× MAGE. |

The variance comes from row-length distribution; the runtime predictor inside the survey is noisy on long-tailed batches. **Single-threaded RAID is not recommended.** The sharded-calibration design (v1.43.0 implementation pending) parallelizes the worker side across a worker pool and cuts RAID wall-clock to under a week with 8 shards.

**How signal count affects cost.** `calibration_survey.py` uses a score-once-survey-many cache: every Tier 1 signal is scored over the corpus in a single pass (this is what the wall-clock above measures), then the per-signal threshold sweeps reuse the cached records via `derive_threshold_from_records`. Adding a ninth Tier 1 signal to an eight-signal sweep does NOT multiply runtime by 9/8 — the threshold sweep itself is seconds per signal. The expensive step is record collection, which is paid once. Plan resource budgets accordingly: a one-signal Tier 1 run and an all-Tier-1 run cost roughly the same wall-clock against the same records cache. (The per-signal `1/n_neg` FPR-resolution gate decides which signals' thresholds are *reportable*, not whether they get scored.)

Adding higher tiers multiplies wall-clock roughly as follows. Each multiplier applies to the per-row scoring step, since the cache architecture amortises the cost across all signals in that tier:

| Add | Multiplier vs. Tier 1 |
|---|---|
| Tier 2 (POS-bigrams + MDD via spaCy) | ×2–3 |
| Tier 3 cohesion via TF-IDF (CPU only) | ×1.2–1.5 |
| Tier 3 cohesion via SBERT (CPU only) | ×3–5 |
| Tier 3 cohesion via SBERT (CUDA / ROCm GPU) | ×1.1–1.3 |

### Memory

A single-shard Tier 1 survey peaks around 250 MB resident for MAGE-scale corpora. Adding Tier 2 brings spaCy's pipeline online (~1 GB resident per worker). Tier 3 with SBERT and a loaded model adds another ~1.5 GB. RAID-scale runs are still memory-light per shard — the corpus is paged in row-by-row, not loaded all at once. The sharded design assumes 8 concurrent workers each consuming under 1 GB resident on Tier 1, comfortable on a 16 GB machine.

### Optional GPU

Tier 1 and Tier 2 are CPU-bound. Tier 3 with SBERT and the planned R12 embedding work benefit from GPU but are not blocked by its absence; TF-IDF fallback and CPU torch handle the no-GPU case. For multi-day runs on RAID-scale corpora, a discrete GPU (NVIDIA CUDA on Linux/Windows or AMD ROCm on Linux/WSL2) cuts Tier 3 wall-clock by 3–5×.

The sharded-calibration design assumes a mixed-hardware setup: macOS laptops for one shard cohort, a Windows + AMD + WSL2 (ROCm 7.2.1) Linux desktop for another, coordinated via cloud-synced state files. Shards are claimed atomically by file-rename, workers checkpoint on SIGTERM, and a small state file records which shards are done so any worker can resume work no machine has yet touched. (Architecture and migration details land in the calibration toolchain's `PROVENANCE.md` once v1.43.0 ships.)

### What this means for the user

- **Don't run calibration on battery.** Plug in. Sleep is supported when the survey honors SIGTERM checkpoint (the sharded design does; pre-sharded runs do not — they restart from zero).
- **One full RAID survey is a multi-day project on a single machine.** Plan accordingly, or wait for the sharded toolchain, or use a multi-machine setup.
- **MAGE first.** Smaller (528 MB, 436k rows) and tractable on a single laptop in an overnight run. Useful as a feasibility check before committing to RAID.
- **Smoothing-diagnosis and voice-coherence are unaffected.** Those tiers do not need any of this. Calibration only re-runs when the underlying signals or labeled corpora change — typically once per major release that touches a signal definition or a corpus version.
- **Manifests are reproducible from CSVs.** Delete the multi-GB manifest after a successful run if disk pressure matters; rebuild from the CSVs with the `*_to_manifest.py` converters when needed.

## Quick start

These examples are grouped by workflow. Many scripts compose: a smoothing audit can feed a restoration packet, a voice-distance report can feed a surface-disagreement resolver, and validation outputs should be read through their claim-license blocks.

### Prose smoothing / compression

```
# Whole-document Layer A audit
python3 scripts/variance_audit.py path/to/draft.txt

# JSON output
python3 scripts/variance_audit.py path/to/draft.txt --json

# Compare against a personal baseline
python3 scripts/variance_audit.py path/to/draft.txt --baseline-dir baselines/personal/

# Opt out of default HTML/CSS/code stripping when intentionally auditing non-prose
python3 scripts/variance_audit.py path/to/draft.txt --allow-non-prose

# Sliding-window scan to localize compression within a long document
python3 scripts/variance_audit.py path/to/draft.txt --window-size 1000 --window-stride 500

# Cross-chapter manuscript dashboard
python3 scripts/manuscript_audit.py path/to/manuscript.md --baseline-dir baselines/literary-fiction/

# Vocabulary repetition against an external baseline
python3 scripts/repetition_audit.py path/to/draft.txt --baseline-dir baselines/personal/

# Manuscript-aggregate habit-vocabulary audit
python3 scripts/manuscript_repetition_audit.py path/to/manuscript.md --baseline-dir baselines/personal/

# Chapter-distinctiveness audit (leave-one-out internal baseline)
python3 scripts/chapter_distinctiveness_audit.py path/to/manuscript.md
```

### Voice coherence

```
# Voice-distance against a private baseline
python3 scripts/voice_distance.py path/to/draft.txt --baseline-dir ../ai-prose-baselines-private/fiction/

# Build a private voice profile
python3 scripts/voice_profile.py --baseline-dir ../ai-prose-baselines-private/fiction/ \
    --out ../ai-prose-baselines-private/fiction_voice_profile.md

# Extract idiolect phrases to preserve during revision
python3 scripts/idiolect_detector.py \
    --target-dir ../ai-prose-baselines-private/fiction/target/ \
    --reference-dir baselines/literary-fiction/ \
    --out ../ai-prose-baselines-private/fiction_idiolect.md
```

### Validation and calibration

```
# Validate a corpus manifest before any of the manifest-driven flows
python3 scripts/manifest_validator.py corpus_manifest.jsonl

# Check corpus files for HTML/CSS/code/table contamination before calibration runs
python3 scripts/check_corpus.py --manifest corpus_manifest.jsonl --filter use=baseline

# Evaluate smoothing-diagnosis scores against labeled validation entries
python3 scripts/validation_harness.py corpus_manifest.jsonl --fpr-target 0.01

# Include the corpus-hygiene gate in a validation run
python3 scripts/validation_harness.py corpus_manifest.jsonl --check-corpus

# Validation-harness smoke fixture
python3 scripts/validation_harness.py scripts/test_data/validation_smoke_manifest.jsonl \
    --no-tier2 --no-tier3 --fpr-target 0.01 --seed 7
```

### Craft restoration

```
# Count named rhetorical patterns for source triage
python3 scripts/aic_pattern_audit.py path/to/draft.txt

# Turn diagnostic JSON into bounded revision instructions
python3 scripts/restoration_packet.py \
    --variance-json variance.json \
    --bigram-json bigram.json \
    --out packet.md --json-out packet.json

# Check whether a revision improved target signals without gaming aggregates
python3 scripts/before_after_restoration.py \
    --packet-json packet.json \
    --before-variance-json before/variance.json \
    --after-variance-json after/variance.json \
    --before-bigram-json before/bigram.json \
    --after-bigram-json after/bigram.json \
    --out report.md --json-out report.json
```

## Smoke test

```
python3 scripts/variance_audit.py scripts/test_data/human_sample.txt
# Expected: Insufficient signal (169 words, below all length floors)

python3 scripts/variance_audit.py scripts/test_data/ai_sample.txt
# Expected: Moderately smoothed, burstiness_B flagged
```

## Design principles

**The framework targets discourse habits, not vocabulary.** Surface tells (specific AI words, em-dash frequency, the magic triple) decay as models change and writers learn to avoid them. The named patterns are structural habits: hedge-and-reversal moves, pseudo-aphoristic cadence, template rhythm, inflated parallelism, over-neat transitions, manifesto cadence, and indefinite-pronoun gestures. They survive vocabulary changes because they are moves in prose, not magic words. `references/aic-flags.md` documents the same patterns under their original literary names (Negation hedge, Disguised correctio, Pseudo-aphorism, Manifesto cadence, Indefinite-Pronoun Gesture) for cross-reference with the audit script.

**Three layers are kept distinct.** Layer A is mathematical (distributional diagnostics). Layer B is craft-pattern recognition (the AIC flag families). Layer C is voice attribution (the earned/unearned triage). The framework's value depends on not collapsing them.

**Source triage is the hardest part to teach and the most valuable.** Most surface flags resolve on source triage as earned. The framework's authority comes from being honest about that.

**The personal baseline is the operative diagnostic.** Heuristic thresholds catch unsubtle cases. Any writer with a focused vocabulary or essayistic long-sentence style will land in literature's "compressed" region by absolute standards. Always run with a personal baseline if available; the heuristic thresholds are general-prose calibrations, not genre-specific or writer-specific ones.

**Genre tolerance varies meaningfully.** A pattern that signals trouble in literary fiction may be partially structural to testimony or blog. The genre tolerance table in `references/aic-flags.md` consolidates the calibration notes.

## Citation and further reading

The mathematical foundation for Layer A is documented in `references/distributional-diagnostics.md`. Core sources: Burrows (Delta), Stamatatos (POS n-grams), Liu and Futrell-Mahowald-Gibson (dependency distance), Tanaka-Ishii and Aihara (Yule's K constancy), Reviriego et al. (LLM lexical diversity), Muñoz-Ortiz et al. (LLM burstiness), Hans et al. (Binoculars), Bao et al. (Fast-DetectGPT), Thai et al. (EditLens), Emi and Spero (Pangram), Sadasivan et al. (paraphrase impossibility result).

Implementation survey notes live in `references/implementation-survey.md`. The standing rule is borrow mature statistical machinery where it exists, use established stylometry packages as oracles where importing them would burden the CLI, and keep SETEC-specific claim framing, privacy guards, and craft triage local.

## License

Dual-licensed:

- **Code** (everything under `scripts/`, plus root-level configuration files) is licensed under the **GNU General Public License, version 3 or later** (`GPL-3.0-or-later`). Canonical text in `LICENSE`.
- **Documentation and reference prose** (`README.md`, `ROADMAP.md`, `CHANGELOG.md`, `SKILL.md`, `references/*.md`, `scripts/README.md`, `baselines/README.md` and the per-genre placeholders, this `NOTICE`) is licensed under **Creative Commons Attribution-ShareAlike 4.0 International** (`CC-BY-SA-4.0`). Canonical text in `LICENSE-docs`.

See `NOTICE` for the file-by-file scope statement.

Personal baseline corpora and generated voice profiles are not part of this repository and are not licensed for redistribution from any baseline directory structure shipped here.

## Status

This is an active research-grade toolkit. It began as an AI-prose detection skill, but its center of gravity is now broader: voice coherence, prose transformation, validation discipline, and revision-safe restoration under explicit claim limits.
