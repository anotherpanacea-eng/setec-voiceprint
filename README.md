# setec-voiceprint

A framework and toolkit for diagnosing AI-prose patterns in fiction and argument-shaped nonfiction. Targets the discourse habits underneath specific AI words: the patterns that survive across model generations because they are structural, not lexical.

The name tips its hat to *Sneakers* (1992): SETEC ASTRONOMY, "too many secrets." Voice profiles are voice-cloning inputs. The framework's outputs are useful to the writer who runs them; they are also leverage to anyone else who gets hold of them. The "setec" in the name is a reminder, not a flourish.

The framework distinguishes four task surfaces, three diagnostic layers, and a vocabulary of named patterns. The Python tooling supports distributional diagnostics, voice-coherence comparison, vocabulary-repetition audits, manifest validation, and an MVP validation harness for empirical calibration.

## Four task surfaces

| Surface | Tools | Question it answers | Question it does NOT answer |
|---|---|---|---|
| **1. AI-prose smoothing diagnosis** | `variance_audit.py`, `manuscript_audit.py`, `repetition_audit.py`, `manuscript_repetition_audit.py`, `chapter_distinctiveness_audit.py`; Layer A in audit | Has this prose been smoothed into a narrower-than-typical stylometric region? | Who wrote it; whether smoothing is artifact of register / scene / writer's natural style |
| **2. Voice-coherence comparison** | `voice_distance.py`, `voice_profile.py` | How far is this draft from a writer's or register's own baseline? | Whether divergence is caused by AI involvement, register shift, time drift, or genuine voice change |
| **3. Empirical performance validation** | `manifest_validator.py`, `validation_harness.py` | How well do these signals discriminate against this labeled corpus, at these registers, at these lengths? | Whether the framework will work on unseen corpora outside the harness's coverage |
| **4. Craft restoration advice** | `references/aic-flags.md`, `references/source-triage.md`, `references/rhetorical-countermoves.md`; Layers B and C in audit | Which patterns are present, are they earned in context, and what revision moves apply? | Anything quantitative about provenance or distributional smoothing |

The four surfaces share statistical signals because RLHF-induced mode collapse, register conventions, and time-stable authorial idiolect all leave traces in the same features. They answer different questions and license different claims. The framework refuses the unifying "is this AI" verdict because the underlying math does not entitle it.

Every script's JSON output and markdown report carry an explicit `task_surface` field so downstream consumers can route by surface and refuse to mix scores across them.

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
├── requirements.txt                runtime deps (spaCy + SciPy + scikit-learn) and optional extras
├── .claude-plugin/
│   └── marketplace.json            Claude Code / Cowork plugin marketplace catalog
├── plugins/
│   └── setec-voiceprint/           plugin tree: manifest + 4 SKILL.md (one per task surface)
│       ├── .claude-plugin/plugin.json
│       └── skills/{smoothing-diagnosis,voice-coherence,validation,craft-restoration}/SKILL.md
├── references/
│   ├── distributional-diagnostics.md   Layer A: 11 variance signals with math
│   ├── aic-flags.md                Layer B: 7 flag families + nonfiction parallel set + genre tolerance table
│   ├── source-triage.md            Layer C: voice attribution, named patterns, earned/unearned triage
│   ├── rhetorical-countermoves.md  figure-by-flag pairings (fiction + nonfiction additions)
│   └── implementation-survey.md    dependency/reference survey for borrow-before-building work
├── scripts/
│   ├── README.md                       script catalog and usage
│   ├── variance_audit.py               Layer A computation; sliding-window mode
│   ├── manuscript_audit.py             cross-chapter Layer A dashboard
│   ├── repetition_audit.py             vocabulary over-representation against external baseline
│   ├── manuscript_repetition_audit.py  manuscript-aggregate vocabulary audit
│   ├── chapter_distinctiveness_audit.py  leave-one-out internal-baseline vocabulary audit
│   ├── stylometry_core.py              shared stylometric feature extraction + compute_clusters
│   ├── voice_distance.py               target-vs-baseline voice distance with cluster mode
│   ├── voice_profile.py                private baseline voiceprint report
│   ├── manifest_validator.py           schema and integrity checks for corpus_manifest.jsonl
│   ├── validation_harness.py           empirical validation over labeled manifest entries
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

Voice profiles and personal baseline corpora are voice-cloning inputs. The `baselines/` directory ships with empty per-genre subdirectories meant to be populated locally with the user's own pre-AI-era work. The recommended layout keeps personal baselines in a separate private folder (a sibling to this repo) rather than inside it, with `voice_profile.py` defaulting to refusing output paths outside that private location unless `--allow-public-output` is passed explicitly.

The `manifest_validator.py` script enforces a privacy ratchet on `voice_profile`-tagged manifest entries: any entry whose privacy is not literally `'private'` (including missing or non-string values) raises a warning. Treat voiceprints as cloning-grade inputs by default.

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

This installs spaCy (Tier 2: POS-bigrams, MDD per sentence), SciPy (length-matched bootstrap), scikit-learn (Tier 3 cohesion via TF-IDF fallback, plus validation-harness ranking metrics), and statsmodels (validation-harness confidence intervals). The spaCy English model `en_core_web_sm` is not on PyPI and must be downloaded separately.

`requirements.txt` records the optional deps in commented form: `sentence-transformers` for calibrated cohesion cosines comparable to the literature's reference values (heavier — pulls in torch), and `textstat` / `nltk` if you want tightened FKGL or NLTK-driven idiolect tooling later.

Tier 1 (sentence-length variance, MATTR, MTLD, Yule's K, Shannon entropy, FKGL, connective density, function-word ratio) runs on the standard library alone; the install above is what's needed for Tier 2 and Tier 3.

### Plugin install — Claude Code CLI / Desktop

```
claude plugin marketplace add anotherpanacea-eng/setec-voiceprint
claude plugin install setec-voiceprint@setec-voiceprint
```

Then invoke skills naturally from inside a Claude Code session ("audit this draft for AI smoothing", "build a voice profile from this corpus", etc.) or via explicit slash commands if you have them configured.

**Updating from CLI / Desktop:**

```
claude plugin marketplace update setec-voiceprint
claude plugin update setec-voiceprint
# Then fully quit (Cmd-Q on Mac, not just close window) and relaunch.
```

A full quit-and-relaunch is required for skill changes to take effect; `/reload-plugins` reloads hooks/MCP/LSP only and does **not** reload skills.

### Plugin install — Cowork SDK harness

The Cowork harness loads plugins via `--plugin-dir` directly from a local checkout; the marketplace catalog system is bypassed entirely. Clone the repo and point Cowork at it:

```
git clone https://github.com/anotherpanacea-eng/setec-voiceprint.git
# Configure Cowork to load --plugin-dir <path>/setec-voiceprint/plugins/setec-voiceprint
```

**Updating in Cowork:**

```
cd path/to/setec-voiceprint
git pull
# Then start a new Cowork session. Cowork has no /reload-plugins command;
# the running session keeps the previously loaded plugin until restart.
```

If updates seem stuck, check that the `--plugin-dir` path resolves to the same checkout you just `git pull`ed. Multiple checkouts on disk are the most common failure mode for "I pulled but I still see the old plugin."

### Standalone CLI (no plugin install)

If you don't want the plugin, install the Python deps as above and run the scripts directly. See the Quick start section below.

## Quick start

```
# Whole-document Layer A audit
python3 scripts/variance_audit.py path/to/draft.txt

# JSON output
python3 scripts/variance_audit.py path/to/draft.txt --json

# Compare against a personal baseline
python3 scripts/variance_audit.py path/to/draft.txt --baseline-dir baselines/personal/

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

# Voice-distance against a private baseline
python3 scripts/voice_distance.py path/to/draft.txt --baseline-dir ../ai-prose-baselines-private/fiction/

# Build a private voice profile
python3 scripts/voice_profile.py --baseline-dir ../ai-prose-baselines-private/fiction/ \
    --out ../ai-prose-baselines-private/fiction_voice_profile.md

# Validate a corpus manifest before any of the manifest-driven flows
python3 scripts/manifest_validator.py corpus_manifest.jsonl

# Evaluate smoothing-diagnosis scores against labeled validation entries
python3 scripts/validation_harness.py corpus_manifest.jsonl --fpr-target 0.01

# Validation-harness smoke fixture
python3 scripts/validation_harness.py scripts/test_data/validation_smoke_manifest.jsonl \
    --no-tier2 --no-tier3 --fpr-target 0.01 --seed 7
```

## Smoke test

```
python3 scripts/variance_audit.py scripts/test_data/human_sample.txt
# Expected: Insufficient signal (169 words, below all length floors)

python3 scripts/variance_audit.py scripts/test_data/ai_sample.txt
# Expected: Moderately smoothed, burstiness_B flagged
```

## Design principles

**The framework targets discourse habits, not vocabulary.** Surface tells (specific AI words, em-dash frequency, the magic triple) decay as models change and writers learn to avoid them. The named patterns within AIC-7 (Negation hedge, Disguised correctio, Pseudo-aphorism, Manifesto cadence) and within AIC-2 (Indefinite-pronoun gesture) are syntactic, not lexical. They survive vocabulary changes across model generations because they are structural moves.

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

This is a v2 rebuild of an earlier skill, with substantive expansion: a manifest validator, validation-spine infrastructure, sliding-window scope, separated character n-gram families, feature-cluster mode for voice-distance, manuscript-aggregate vocabulary audits, and a genre tolerance reference. The validation harness and length-matched bootstrap are the next architectural milestones.
