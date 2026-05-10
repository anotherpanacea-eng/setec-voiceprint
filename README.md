# setec-voiceprint

SETEC Voiceprint is a stylometric framework for measuring how prose changes.

It can diagnose smoothing, compare a draft against a writer's own baseline, build private voice profiles, surface idiolect features worth preserving, validate signals against labeled corpora, and generate revision-safe restoration packets. It is built for writers, editors, researchers, and toolmakers who need evidence about prose transformation without collapsing that evidence into an overconfident verdict.

SETEC is not an AI detector.

AI-assisted writing is one important use case because LLM collaboration often leaves smoothing, compression, syntactic drift, and phrase-preservation artifacts. But the same signals can also come from genre, education, dialect, translation, institutional templates, human editing, time drift, POV collapse, or a writer consciously imitating themselves. SETEC's core discipline is to measure the signal, name the comparison set, and refuse claims the evidence does not license.

The name tips its hat to *Sneakers* (1992): SETEC ASTRONOMY, "too many secrets." Voice profiles are voice-cloning inputs. The framework's outputs are useful to the writer who runs them; they are also leverage to anyone else who gets hold of them. The "setec" in the name is a reminder, not a flourish.

The framework distinguishes four task surfaces, three diagnostic layers, and a vocabulary of named patterns. The Python tooling supports distributional diagnostics, voice-coherence comparison, vocabulary-repetition audits, corpus acquisition, manifest validation, empirical calibration, and craft restoration.

## Choose the question

| Surface | Tools | Question it answers | Question it does NOT answer |
|---|---|---|---|
| **1. Prose smoothing / compression** | `variance_audit.py`, `manuscript_audit.py`, `repetition_audit.py`, `manuscript_repetition_audit.py`, `chapter_distinctiveness_audit.py`, `bigram_diff.py`, `manuscript_bigram_diff.py` | Has this prose narrowed into a low-variance or template-heavy region? | Who caused it; whether AI was involved |
| **2. Voice coherence** | `voice_distance.py`, `voice_profile.py`, `idiolect_detector.py`, `voice_drift_tracker.py`, `pov_voice_profile.py`, `mimicry_cosplay_audit.py` | How far is this draft from a writer, register, POV, or time-period baseline? | Why it drifted |
| **3. Validation and calibration** | `manifest_validator.py`, `check_corpus.py`, `validation_harness.py`, `voice_validation_harness.py`, `calibration_drift_monitor.py`, `fairness_dialect_guardrails.py`, calibration scripts | How well do these signals behave on this labeled corpus, dependency stack, and fairness slice? | Whether they generalize outside that evidence |
| **4. Craft restoration** | `aic_pattern_audit.py`, `restoration_packet.py`, `before_after_restoration.py`, `semantic_preservation_check.py`, `known_editor_profile.py`; reference docs | Which measured drifts can become safe revision instructions, and did revision preserve meaning? | Whether the revision is artistically better |

The four surfaces share statistical signals because RLHF-induced mode collapse, register conventions, and time-stable authorial idiolect all leave traces in the same features. They answer different questions and license different claims. The framework refuses the unifying "is this AI" verdict because the underlying math does not entitle it.

Every script's JSON output and markdown report carry an explicit `task_surface` field so downstream consumers can route by surface and refuse to mix scores across them.

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
├── references/
│   ├── distributional-diagnostics.md   Layer A: 11 variance signals with math
│   ├── aic-flags.md                Layer B: 7 flag families + nonfiction parallel set + genre tolerance table
│   ├── source-triage.md            Layer C: voice attribution, named patterns, earned/unearned triage
│   ├── rhetorical-countermoves.md  figure-by-flag pairings (fiction + nonfiction additions)
│   └── implementation-survey.md    dependency/reference survey for borrow-before-building work
├── scripts/
│   ├── README.md                       script catalog and usage
│   ├── variance_audit.py               Layer A computation; sliding-window mode
│   ├── sliding_window_heatmap.py       sliding-window output → markdown localization heatmap
│   ├── manuscript_audit.py             cross-chapter Layer A dashboard
│   ├── repetition_audit.py             vocabulary over-representation against external baseline
│   ├── manuscript_repetition_audit.py  manuscript-aggregate vocabulary audit
│   ├── chapter_distinctiveness_audit.py  leave-one-out internal-baseline vocabulary audit
│   ├── stylometry_core.py              shared stylometric feature extraction + compute_clusters
│   ├── voice_distance.py               target-vs-baseline voice distance with cluster mode
│   ├── voice_profile.py                private baseline voiceprint report
│   ├── idiolect_detector.py            keyness/collocation extraction for preservation lists
│   ├── adversarial_fixtures.py         deterministic Unicode stress-fixture transforms
│   ├── manifest_validator.py           schema and integrity checks for corpus_manifest.jsonl
│   ├── check_corpus.py                 content-level non-prose contamination gate
│   ├── validation_harness.py           empirical validation over labeled manifest entries
│   ├── voice_validation_harness.py     voice-coherence validation harness
│   ├── mimicry_cosplay_audit.py        lexical mimicry without syntactic conformity
│   ├── known_editor_profile.py         learned before/after editorial transformation profile
│   ├── length_bootstrap.py             length-matched window sampler + scipy.stats.bootstrap helpers
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
python3 scripts/restoration_packet.py --variance-json variance.json --out packet.md

# Check whether a revision improved target signals without gaming aggregates
python3 scripts/before_after_restoration.py --packet packet.json --before-json before.json --after-json after.json
```

## Smoke test

```
python3 scripts/variance_audit.py scripts/test_data/human_sample.txt
# Expected: Insufficient signal (169 words, below all length floors)

python3 scripts/variance_audit.py scripts/test_data/ai_sample.txt
# Expected: Moderately smoothed, burstiness_B flagged
```

## Design principles

**The framework targets discourse habits, not vocabulary.** Surface tells (specific AI words, em-dash frequency, the magic triple) decay as models change and writers learn to avoid them. The named patterns are structural habits: hedge-and-reversal moves, pseudo-aphoristic cadence, template rhythm, inflated parallelism, over-neat transitions, and indefinite-pronoun gestures. They survive vocabulary changes because they are moves in prose, not magic words.

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
