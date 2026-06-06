# 11-dialogue-voice-audit

> Per-character **dialogue voice** profiling: contraction rate, turn length, dialogue-
> tag style, vocatives, interruption punctuation, and character-specific function-word
> / discourse-marker signatures — to catch character-voice collapse, which shows up in
> dialogue before narration.

- **Status:** Spec (full-deps box — needs spaCy).
- **Tier:** Tier 4 (ROADMAP → "Dialogue-Specific Voice Audit … Round 2 of `pov_voice_profile.py` — character voice collapse often appears in dialogue first, narration second.").
- **GPU required:** no, but **needs spaCy** for dialogue tokenization + POS/dep.
- **License:** N/A (local).

## Motivation & orthogonality

`pov_voice_profile.py` profiles per-POV narration. Dialogue is a separate, earlier
signal: when an author (or an AI revision) flattens character voices, the characters'
*spoken* registers converge first. This surface extracts quoted dialogue, attributes
it to speakers (tag-based), and profiles each character's dialogue voice — then flags
convergence across characters. Voice-coherence surface; requires a baseline (the
manuscript's other chapters, or the character's prior dialogue).

## Method (spaCy-backed)

Extract quoted spans + dialogue tags ("said X", "X asked"); attribute to speakers;
per character compute: contraction rate, mean turn length + variance, dialogue-tag
verb diversity, vocative rate, interruption/trailing punctuation (—, …), top function
words + discourse markers. Cross-character **divergence matrix** (pairwise distance);
low divergence = converged/flat character voices.

## Contract

- **task_surface:** `voice_coherence` (existing — this is a voice-coherence audit).
- **CLI:** `python3 scripts/dialogue_voice_audit.py MANUSCRIPT [--baseline-dir DIR] [--json] [--out PATH]`.
- **JSON envelope:** `results` = per-character profiles + a cross-character divergence matrix + a `converged_pairs` list (descriptive, not a verdict). `ClaimLicense`.
- **Claim license:** *licenses* "per-character dialogue-voice profiles and their cross-character divergence within this manuscript"; *refuses* author-identity, AI-provenance, and quality inference; notes attribution is tag-heuristic (unattributed dialogue is bucketed separately).
- **capabilities.yaml:** `id: dialogue_voice_audit`, `surface: voice_coherence`, `status: heuristic`, `compute: {tier: spacy, length_floor_words: 2000}`, `dependencies.python: [spacy]`.

## Test contract (`tests/test_dialogue_voice_audit.py`)

- `test_surface_registered`; `test_dialogue_extraction` (quotes + tags → speaker buckets); `test_unattributed_bucketed`; `test_divergence_matrix_shape`; `test_claim_license_refuses_identity`; `test_envelope_shape`; `test_deterministic`.

## Non-goals

- No "who wrote this" / AI call; no quality judgment.
- Not a coreference engine — tag-based attribution only; unattributed dialogue is
  reported separately, never force-attributed.

## Open questions

- Whether to consume a `pov_voice_profile` output for speaker priors; keep v1 standalone.
