# Calibration & readiness: building your own runway

This guide answers one question for a user who does **not** have the maintainer's
private baseline corpora: *for each capability in the package, what does it need
to run, what corpus do I have to bring myself, and how far can I trust the output
before I calibrate?*

It complements two README sections you should read alongside it:
[Calibration costs](../../../README.md#calibration-costs) (disk / time / memory /
GPU for re-deriving thresholds) and
[Personal baselines](../../../README.md#personal-baselines) (what a workable
baseline looks like). This document is the per-capability *readiness matrix* plus
the recipe for assembling the corpus the framework deliberately does not ship.

The readiness table below is **generated from `capabilities.d/`** so it cannot
drift from the code; the prose around it is hand-maintained.

## The framework ships methodology, not thresholds

SETEC refuses the single "is this AI" verdict, and it deliberately does **not**
ship anchored thresholds derived from a labeled corpus as framework defaults
(the "Stylometry to the people" posture; see
[Why no verdict](../../../README.md#why-no-verdict) and
`scripts/calibration/PROVENANCE.md`). The load-bearing artifact is the
*methodology* — the signals, the claim-license discipline, the validation
harness — not the numbers. Calibration moves into the user's hands, not a
vendor's.

The practical consequence: out of the box most capabilities give you
**measurements**, and the *trust* you can place in any banding depends on what
comparison corpus you supply. A draft scored against nothing is a curiosity; a
draft scored against the writer's own register-matched prior work is evidence.

The maintainer's private corpora (`ai-prose-baselines-private/`, personal
baselines, impostor pools) are **never** shipped from this repo. What ships is
the tooling and the recipe for building your own.

## Your runway: three inputs you supply

Most of the distance between "installed" and "useful" is corpus, not code. There
are three inputs, in rough order of how often they matter:

1. **A register-matched personal baseline** — the writer's own prior prose in the
   target register. This is the single strongest diagnostic surface SETEC
   supports. A minimum-viable baseline is **3–5 files of 3,000+ words each** in
   the relevant register; more is better, and date-tagged pre-AI work is best for
   smoothing diagnosis. Voice-coherence tools want more (`voice_distance` ≥20K
   words, `idiolect_detector` ≥50K). Assembly walkthrough:
   `scripts/calibration/PROVENANCE_TEMPLATE.md`; per-register compilation notes:
   `baselines/*/README.md`.

2. **An impostor pool** (optional) — plausible *other* writers in the same
   register, for the General Imposters attribution test (`general_imposters.py`).
   Without impostors, a voiceprint over-attributes register and topic to
   identity. Project Gutenberg, PAN authorship corpora, or your own
   register-matched collection all work.

3. **A labeled human/AI corpus** (only if you want calibrated thresholds) — feeds
   `validation_harness.py` to turn provisional bands into FPR/TPR at a stated
   operating point. You can fetch openly-licensed benchmarks
   (`scripts/calibration/fetch_raid.py`, `fetch_mage.py`) or label your own.
   `manifest_validator.py` gates the manifest before any sweep.

You do not need all three to start. The readiness table's "What you supply"
column tells you the minimum per capability.

## Readiness levels

Every capability carries a calibration `status` in the manifest. Here is what
each licenses before you bring your own labeled corpus:

- **Heuristic (uncalibrated)** — shipped, not yet calibrated. Output surfaces
  *candidates*, not scores. Useful for triage; never for a verdict.
- **Empirical (provisional)** — runs immediately, but any banding is
  local-experimentation grade and PROVISIONAL until you calibrate against your
  own corpus. Trust the *measurements*, not the band labels.
- **Literature-anchored** — close to a published condition out of the box; usable
  as evidence. The operating point for *your* corpus is still uncalibrated.
- **Calibrated** — ships with corpus-tested FPR/TPR at a stated operating point.

## Readiness matrix

<!-- BEGIN GENERATED: tools/gen_calibration_readiness.py — do not edit by hand -->

_Generated from `capabilities.d/` (schema 0.3.0) by `tools/gen_calibration_readiness.py`. Do not edit this region by hand._

### Evidence surfaces (run on a draft)

| Capability | Readiness | Runs without your corpus? | What you supply | Packages | Hardware | Length floor |
|---|---|---|---|---|---|---|
| `variance_audit` | Empirical (provisional) | Yes | register-matched personal baseline corpus (optional) | stdlib; opt: spacy, sklearn, sentence_transformers, textstat, nltk, numpy, transformers | CPU works (slow); GPU recommended; ~0.6–2 GB model weights on disk | 200 |
| `voice_distance` | Empirical (provisional) | No | register-matched personal baseline corpus (≥20K words) (required) | stdlib; opt: spacy | CPU / stdlib (+ optional spaCy model) | 2000 |
| `idiolect_detector` | Empirical (provisional) | No | register-matched personal baseline (or a reference manifest) (≥50K words) (required) | req: scipy; opt: nltk | CPU / stdlib (+ optional spaCy model) | 20000 |
| `aic_pattern_audit` | Heuristic (uncalibrated) | Yes | register-matched personal baseline corpus (optional) | stdlib; opt: spacy | CPU / stdlib (+ optional spaCy model) | 400 |
| `restoration_packet` | Heuristic (uncalibrated) | Yes | diagnostic JSON from prior Surface 1/2 runs (required) | stdlib | CPU / stdlib (+ optional spaCy model) | — |
| `binoculars_audit` | Literature-anchored | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | req: transformers, torch | CPU works (slow); GPU recommended; ~0.6–2 GB model weights on disk | 50 |
| `narrative_decision_audit` | Literature-anchored | Yes | pre-computed judge feature manifest (optional); LLM API access (key + per-call cost) (required) | stdlib | No local GPU; LLM API access (network + key + per-call cost) | 2000 |
| `agd_move_scan` | Heuristic (uncalibrated) | Yes | pre-computed judge feature manifest (optional); LLM API access (key + per-call cost) (required) | stdlib | No local GPU; LLM API access (network + key + per-call cost) | 120 |
| `argmove_profile` | Empirical (provisional) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | 300 |
| `argquality_dimension_profile` | Literature-anchored | Yes | pre-computed judge feature manifest (optional); LLM API access (key + per-call cost) (required) | stdlib | No local GPU; LLM API access (network + key + per-call cost) | 120 |
| `argument_certainty_calibration` | Heuristic (uncalibrated) | Yes | LLM API access (key + per-call cost) (required) | stdlib | No local GPU; LLM API access (network + key + per-call cost) | 50 |
| `argument_decision_audit` | Literature-anchored | Yes | register-matched personal baseline corpus (optional); pre-computed judge feature manifest (optional); LLM API access (key + per-call cost) (required) | stdlib | No local GPU; LLM API access (network + key + per-call cost) | 300 |
| `author_corpus_export` | experimental | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | — |
| `biber_features` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib; opt: torch, transformers | CPU (+ optional power-ups) | 150 |
| `compression_edit_distance_audit` | Literature-anchored | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | 50 |
| `corpus_novelty_audit` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | 24 |
| `cosine_explanation` | Literature-anchored | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib; opt: transformers, numpy | CPU (+ optional power-ups) | 0 |
| `cross_doc_argument_consistency` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | 50 |
| `cross_doc_novelty_profile` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | 100 |
| `crosslingual_voice_distance` | Heuristic (uncalibrated) | No | register-matched personal baseline corpus (required); language code shared by target and corpus (provenance) (required); muar opt-in (default OFF); adds a learned mUAR cosine block BESIDE the parser-free distance (does NOT replace delta, does NOT relax the --lang cross-language refusal). Lazily imports transformers + voice_fingerprint only on this flag. (optional) | stdlib; opt: transformers | CPU / stdlib (+ optional spaCy model) | 500 |
| `dependency_distance_audit` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | req: spacy | CPU / stdlib (+ optional spaCy model) | 150 |
| `dialogue_voice_audit` | Heuristic (uncalibrated) | Yes | register-matched personal baseline corpus (optional) | req: spacy | CPU + spaCy model | 2000 |
| `discourse_move_signature` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | — |
| `distinct_diversity_audit` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | 15 |
| `document_layout_audit` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | 300 |
| `edit_magnitude_audit` | Heuristic (uncalibrated) | Yes | operator-calibrated model (--model PATH) (optional) | req: transformers, torch | CPU works (slow); GPU recommended; ~0.6–2 GB model weights on disk | 100 |
| `embedding_attribution` | Literature-anchored | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib; opt: transformers, numpy | CPU (+ optional power-ups) | 0 |
| `enthymeme_gapflag` | Literature-anchored | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | 120 |
| `fallacy_scan` | Literature-anchored | Yes | pre-computed judge feature manifest (optional); LLM API access (key + per-call cost) (required) | stdlib | No local GPU; LLM API access (network + key + per-call cost) | 120 |
| `fast_detect_curvature` | Literature-anchored | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | req: transformers, torch | CPU works (slow); GPU recommended; ~0.6–2 GB model weights on disk | 50 |
| `formulaicity_audit` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | 300 |
| `function_word_adjacency_audit` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | 250 |
| `gecscore_audit` | Literature-anchored | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib; opt: language_tool_python, transformers, torch | CPU / stdlib (+ optional spaCy model) | 50 |
| `general_imposters` | Literature-anchored | No | labeled corpus + valid `corpus_manifest.jsonl` (required) | req: scipy | CPU / stdlib (+ optional spaCy model) | — |
| `homogeneity_audit` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib; opt: numpy | CPU / stdlib (+ optional spaCy model) | 15 |
| `house_style_decomposition` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib; opt: torch | CPU / stdlib (+ optional spaCy model) | 300 |
| `intrinsic_dimension_audit` | Literature-anchored | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | req: transformers, torch, numpy, scipy | CPU works (slow); GPU recommended; ~0.6–2 GB model weights on disk | 500 |
| `lambdag_audit` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | req: spacy | CPU / stdlib (+ optional spaCy model) | 150 |
| `manuscript_audit` | Empirical (provisional) | Yes | register-matched personal baseline corpus (optional) | stdlib; opt: spacy, sklearn, sentence_transformers, textstat, nltk, numpy | CPU / stdlib (+ optional spaCy model) | 200 |
| `mimicry_cosplay_audit` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | — |
| `model_family_attribution` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib; opt: spacy | CPU / stdlib (+ optional spaCy model) | 50 |
| `narratorial_distance_audit` | Heuristic (uncalibrated) | Yes | to override the perception/cognition verb set (optional) | req: spacy | CPU + spaCy model | 1500 |
| `near_dup_dedup` | stable | No | a `corpus_manifest.jsonl` to validate (required) | req: datasketch | CPU + network | — |
| `originality_audit` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | 24 |
| `position_pair_register` | Literature-anchored | Yes | pre-computed judge feature manifest (optional); LLM API access (key + per-call cost) (required) | stdlib | No local GPU; LLM API access (network + key + per-call cost) | 300 |
| `pov_voice_profile` | Heuristic (uncalibrated) | No | labeled corpus + valid `corpus_manifest.jsonl` (required) | stdlib; opt: spacy | CPU / stdlib (+ optional spaCy model) | 3000 |
| `productive_roughness_audit` | Heuristic (uncalibrated) | No | register-matched personal baseline corpus (required) | req: spacy | CPU + spaCy model | 1000 |
| `punctuation_cadence_audit` | Heuristic (uncalibrated) | No | register-matched personal baseline corpus (required) | stdlib | CPU / stdlib (+ optional spaCy model) | 2000 |
| `rank_space_audit` | Literature-anchored | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | req: transformers, torch | CPU works (slow); GPU recommended; ~0.6–2 GB model weights on disk | 50 |
| `rank_turbulence_audit` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | 200 |
| `reference_ecology_audit` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | 300 |
| `repetition_audit` | Heuristic (uncalibrated) | No | register-matched personal baseline corpus (required) | stdlib | CPU / stdlib (+ optional spaCy model) | — |
| `rewriting_invariance_audit` | Heuristic (uncalibrated) | Yes | MODEL (operator-supplied LLM model id used to rewrite) (required); LLM API access (key + per-call cost) (required) | stdlib | No local GPU; LLM API access (network + key + per-call cost) | 50 |
| `shingle_dedup` | Heuristic (uncalibrated) | Yes | explicit staged-descriptor JSONL or exact-pinned local index, as required by the selected mode (required); for a compatible immutable checkpoint directory (optional) | stdlib | CPU / stdlib (+ optional spaCy model) | 8 |
| `skeleton_overlap_audit` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | 24 |
| `sound_texture_audit` | Heuristic (uncalibrated) | Yes | register-matched personal baseline corpus (optional) | stdlib | CPU / stdlib (+ optional spaCy model) | 300 |
| `specdetect_audit` | Literature-anchored | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | req: transformers, torch; opt: numpy | CPU works (slow); GPU recommended; ~0.6–2 GB model weights on disk | 50 |
| `structural_shuffle_audit` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | req: transformers, torch, spacy; opt: en_core_web_sm | CPU works (slow); GPU recommended; ~0.6–2 GB model weights on disk | 50 |
| `style_vectorizer` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib; opt: spacy | CPU / stdlib (+ optional spaCy model) | 500 |
| `tocsin_audit` | Literature-anchored | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib; opt: transformers, torch, numpy | CPU / stdlib (+ optional spaCy model) | 200 |
| `voice_fingerprint` | Empirical (provisional) | Yes | register-matched personal baseline corpus (optional) | req: transformers; opt: sentence_transformers | CPU (+ optional power-ups) | 500 |
| `voice_profile` | Empirical (provisional) | No | register-matched personal baseline corpus (required) | stdlib; opt: spacy | CPU / stdlib (+ optional spaCy model) | 2000 |
| `voice_verifier` | Literature-anchored | No | labeled corpus + valid `corpus_manifest.jsonl` (required); LLM API access (key + per-call cost) (required) | stdlib | No local GPU; LLM API access (network + key + per-call cost) | — |
| `warrant_probe` | Literature-anchored | Yes | pre-computed judge feature manifest (optional); LLM API access (key + per-call cost) (required) | stdlib | No local GPU; LLM API access (network + key + per-call cost) | 120 |
| `watermark_probe` | Literature-anchored | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | — |
| `within_doc_segmentation` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | 30 |

### Runway & calibration tooling

| Capability | Readiness | Runs without your corpus? | What you supply | Packages | Hardware | Length floor |
|---|---|---|---|---|---|---|
| `validation_harness` | Empirical (provisional) | No | labeled human/AI corpus + `corpus_manifest.jsonl` (required) | stdlib; opt: sklearn, statsmodels | CPU / stdlib (+ optional spaCy model) | — |
| `manifest_validator` | Empirical (provisional) | No | a `corpus_manifest.jsonl` to validate (required) | stdlib | CPU / stdlib (+ optional spaCy model) | — |
| `dependency_check` | Heuristic (uncalibrated) | Yes | nothing (introspects your local environment) | stdlib; opt: spacy | CPU / stdlib (+ optional spaCy model) | — |
| `conformal_gate` | Heuristic (uncalibrated) | No | nonconformity scores for the reference class (JSON list or newline-delimited) (required); the target nonconformity score (required for the gate; optional with --fpr-bound, which can emit a threshold without a target) (required); nonconformity scores for a positive class (two-class mode) (optional); q reference-class false-positive ceiling in (0, 1); emits the conformal threshold bounded by q (one-tailed direction only) (optional) | stdlib | CPU / stdlib (+ optional spaCy model) | — |
| `pan_replay` | Empirical (provisional) | No | a `corpus_manifest.jsonl` to validate (required); to restrict obfuscation classes (optional); to restrict reported signals (optional) | stdlib; opt: spacy, sklearn | CPU / stdlib (+ optional spaCy model) | — |
| `paraphrase_ladder` | Empirical (provisional) | No | a `corpus_manifest.jsonl` to validate (required); to restrict reported signals (optional); IN.txt --passes N to regenerate a stdlib-proxy ladder fixture (optional) | stdlib; opt: spacy, sklearn | CPU / stdlib (+ optional spaCy model) | — |
| `paraphrase_robustness` | Empirical (provisional) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | — |
| `setec_run_set` | Heuristic (uncalibrated) | No | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | — |
| `surface_disagreement_resolver` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | — |
| `triage_agreement` | Heuristic (uncalibrated) | Yes | nothing required to run; add a baseline / labeled corpus to calibrate | stdlib | CPU / stdlib (+ optional spaCy model) | — |

**Readiness legend.**
- **Heuristic (uncalibrated)** — Shipped, not yet calibrated. Treat output as candidate-surfacing, not a score.
- **Empirical (provisional)** — Runs immediately, but bands/thresholds are local-experimentation grade — PROVISIONAL until you calibrate against your own labeled corpus.
- **Literature-anchored** — Usable as evidence out of the box (close to a published condition); the operating point for *your* corpus is still uncalibrated.
- **Calibrated** — Ships with corpus-tested FPR/TPR at a stated operating point.

<!-- END GENERATED -->

## Keeping this current

The table above is regenerated from the capabilities manifest:

```bash
# refresh the generated region after editing capabilities.d/
python3 tools/gen_calibration_readiness.py

# CI / pre-commit: fail if the doc is stale (exit 1)
python3 tools/gen_calibration_readiness.py --check
```

Run `--check` in the same CI step as `tools/check_capabilities_drift.py`: the
drift linter keeps the manifest in sync with the source, and this check keeps the
readiness matrix in sync with the manifest. Machine-readable rows are available
via `python3 tools/gen_calibration_readiness.py --json`.

## See also

- [Calibration costs](../../../README.md#calibration-costs) — disk / time / memory / GPU for threshold re-derivation.
- [Personal baselines](../../../README.md#personal-baselines) — what a workable baseline looks like.
- `references/signals-glossary.md` — per-signal calibration status and definitions.
- `scripts/calibration/PROVENANCE.md` — the calibration provenance ledger and the "Stylometry to the people" policy.
- `scripts/calibration/PROVENANCE_TEMPLATE.md` — assembling and labeling a personal baseline.
- Query the same data live: `python3 plugins/setec-voiceprint/scripts/capabilities.py list --available`.
