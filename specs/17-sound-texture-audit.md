# 17-sound-texture-audit

> A **descriptive** sound-texture profile of prose: alliteration / assonance /
> consonance adjacency density and a consonant-class (plosive / fricative /
> sibilant / nasal / liquid / glide) profile — the writer's *sonic* rhythm, which
> the shipped suite (sentence-length variance only) is structurally blind to.
> Ships as an **orthographic-onset proxy** (stdlib, no pronunciation dictionary)
> and says so plainly. Non-verdict.

- **Status:** Ready → building (this group).
- **Tier:** Tier 4 "specialized / fiction-specific" (ROADMAP → "Capability-whitespace additions (2026-06-07) → W2"). Sound is a real voice dimension for literary registers and is absent from every shipped signal.
- **GPU required:** no — stdlib only (`re`, `collections`, `math`, `statistics`).
- **License decision:** N/A — local code; the consonant-class letter mapping is
  general English phonics, not sourced from any vendor artifact.

## Motivation

All 56 shipped signals live in lexical / syntactic / distributional / surprisal
space. The framework measures *rhythm* only as sentence-length variance — never at
the level of **sound**. Alliteration, assonance, consonance, and consonant-class
texture are a mature, orthogonal stylometric axis, and for the maintainer's
register (literary horror / weird fiction) they are load-bearing voice features an
editor would actually talk about.

**Orthogonality:** distinct from `variance_audit` (length/rhythm at the
sentence level), `punctuation_cadence` (interruption grammar), and every lexical
signal. This is the phonetic-texture layer; nothing else in the suite touches it.

**The honest ceiling (frontloaded in the claim-license):** true phonology needs a
pronunciation dictionary (CMUdict) or a grapheme-to-phoneme model. v1 deliberately
ships a dependency-free **orthographic proxy** — it reads sound off spelling
(word-initial consonant letters, vowel-letter nuclei, final consonant letters,
consonant-class letter membership). English spelling is an imperfect sound map
("knight", "psalm", "though"), so the claim-license states the proxy explicitly and
refuses any phonetic-transcription claim. A `cmudict` / `g2p` true-phoneme backend
is a noted optional enhancement, swappable behind the same surface.

## Method

Stdlib only, deterministic. Lowercase; tokenize alphabetic words via
`\b[^\W\d_]+\b` (Unicode letters). Vowel letters = `aeiou` (+`y` as a vowel only
when not word-initial). For each alphabetic word derive: **onset** = leading
maximal consonant-letter run (empty for vowel-initial words); **nucleus** = first
maximal vowel-letter run; **coda** = trailing maximal consonant-letter run.

Adjacency metrics (over the alphabetic-word stream, per 1,000 words):

- `alliteration_pairs_per_1k` — adjacent word pairs `(i, i+1)` where both have a
  non-empty onset and share the same onset **first letter**.
- `assonance_pairs_per_1k` — adjacent pairs sharing the same nucleus vowel-letter
  group.
- `consonance_pairs_per_1k` — adjacent pairs with non-empty codas sharing the same
  coda **last letter**.

Texture profile (over consonant letters in the whole text):

- `consonant_class_fractions` — disjoint partition of consonant letters into
  `plosive` (p b t d k g c q), `fricative` (f v s z h), `nasal` (m n),
  `liquid` (l r), `glide` (w y j), `other` (remaining). Fractions sum to 1.
- `sibilant_ratio` — `(s+z+x)` count / consonant-letter count (a separate,
  intentionally non-disjoint descriptive ratio; overlaps `fricative`).
- `vowel_consonant_ratio` — vowel letters / consonant letters.

**Baseline mode (optional `--baseline-dir`).** Recompute every metric over each
baseline file; report per metric `{draft, baseline_mean, baseline_sd, z}` (z = 0
when sd = 0). Strictly descriptive deviation, never a verdict. No baseline → the
metrics stand alone (`baseline: null`).

No thresholds, no banding, no flags.

## Contract (the testable interface)

- **task_surface:** new value `sound_texture` — added to
  `output_schema.VALID_TASK_SURFACES` + `claim_license.TASK_SURFACE_LABELS`
  (additive; the surface-parity test asserts only a subset, so additive is safe).
- **CLI:** `python3 plugins/setec-voiceprint/scripts/sound_texture_audit.py INPUT[.md|.txt] [--baseline-dir DIR] [--json] [--out PATH]`.
- **JSON envelope:** `build_output(task_surface="sound_texture", …)`. `results`
  keys: `alliteration_pairs_per_1k`, `assonance_pairs_per_1k`,
  `consonance_pairs_per_1k`, `consonant_class_fractions`, `sibilant_ratio`,
  `vowel_consonant_ratio`, and (baseline mode) `baseline_deviation`
  `{metric: {draft, baseline_mean, baseline_sd, z}}`. Carries a `ClaimLicense`.
  **No** band/verdict.
- **Claim license:** *licenses* "descriptive sound-texture measurements (alliteration
  / assonance / consonance adjacency density + consonant-class profile) via an
  orthographic-onset proxy"; *refuses* AI-provenance, voice/authorship identity, and
  writing-quality inference. Caveats: orthographic proxy, **not** a phonetic
  transcription; tuned to English spelling; density is register-dependent;
  descriptive only.
- **capabilities.yaml entry:** `id: sound_texture_audit`, `surface: sound_texture`,
  `status: heuristic`, `handoff: experimental`, `consumers: []`,
  `family: voice-coherence`, `compute: {tier: core, length_floor_words: 300}`,
  `dependencies.python: []`, `python_optional: []`.
- **Availability:** under the 300-word floor → `available=False` + warning.

## Test contract (`plugins/setec-voiceprint/scripts/tests/test_sound_texture_audit.py`)

- `test_task_surface_registered` — `sound_texture` ∈ `VALID_TASK_SURFACES`.
- `test_envelope_shape_validates` — payload validates; correct surface; result keys present.
- `test_no_verdict_keys` — no `band`/`verdict`/`compression`/`smoothed`.
- `test_claim_license_refuses_ai_voice_quality` — `does_not_license` names AI / voice / quality.
- `test_claim_license_states_orthographic_proxy` — caveats name the orthographic / non-phonetic proxy.
- `test_alliteration_detected` — "Peter Piper picked a peck of pickled peppers" → ≥3 alliteration pairs (the `p`-onset adjacencies: peter–piper, piper–picked, pickled–peppers).
- `test_assonance_detected` — a fixed phrase with a repeated vowel nucleus scores > 0.
- `test_consonant_class_fractions_sum_to_one` — the disjoint partition sums to ~1.0.
- `test_no_alliteration_on_vowel_initial` — vowel-initial adjacents don't count as alliteration.
- `test_baseline_deviation_block` — `--baseline-dir` produces `{draft,baseline_mean,baseline_sd,z}` per metric.
- `test_too_short_unavailable` — under-floor input → `available=False`.
- `test_deterministic` — same input → identical result.

## Calibration posture

Nothing to calibrate — descriptive. The claim-license names it a texture
measurement, not a signal with an operating point. A future labeled study could ask
whether sound-texture features add discrimination value, but v1 ships no threshold.

## Out of scope / non-goals

- Not a phonetic transcription and not a poetry scansion tool (no meter / stress / rhyme-scheme).
- Not an AI detector and not a quality judgment (alliteration is a craft choice, not a tell).
- No banding, no per-window trajectory in v1 (a sliding-window mode is a noted follow-on).

## Open questions

- Whether to add an optional CMUdict / g2p true-phoneme backend later (new optional
  dependency tier), swappable behind the same surface and claim-license.
- Whether a sliding-window trajectory (like `semantic_trajectory_audit`) is worth
  the added surface area for v2.
