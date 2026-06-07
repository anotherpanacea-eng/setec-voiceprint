# 19-crosslingual-voice-distance

> A **language-agnostic, parser-free** stylometric distance between a target and a
> baseline corpus: character n-grams, punctuation profile, token-/sentence-length
> distributions, and script statistics — **no spaCy, no English assumption**, works
> on any Unicode script. The door-opener for non-English operation, which the
> framework currently treats only as a fairness *caution*. Honest about its
> ceiling: language-*agnostic*, not language-*aware*.

- **Status:** Ready → building (this group).
- **Tier:** "Capability-whitespace additions (2026-06-07) → W5". The shipped pipeline is English-only (spaCy `en`); multilingual exists only as the fairness-guardrail caution + the planned ESL/L2 fairness fixture slice (`specs/05`), both about *English written by L2 speakers*.
- **GPU required:** no — stdlib only (`re`, `unicodedata`, `collections`, `math`, `statistics`).
- **License decision:** N/A — local code; character-n-gram authorship distance is classical (Kešelj et al. 2003; Stamatatos surveys) and content-independent by construction.

## Motivation

SETEC *preaches* the ESL false-positive literature but cannot analyze a non-English
author's prose at all — every Layer A/B/C tool depends on the English spaCy
pipeline and English function-word lists. Yet the most robust, content-independent
signal in classical authorship attribution — the **character n-gram profile** — is
entirely language-agnostic and needs no parser. A parser-free voice-distance surface
opens the framework to any Unicode language with zero new dependencies, as a first
honest step.

**Orthogonality:** `voice_distance` is the English, spaCy-/function-word-backed
Burrows-Delta surface; this is its language-agnostic sibling. It deliberately uses
only signals that survive a language switch (character n-grams, punctuation,
length distributions, script statistics) and explicitly refuses the
morphology-/function-word-dependent claims `voice_distance` makes.

## Method

Stdlib only, deterministic. For the target and each baseline file:

- **Character n-gram profile** (default n = 3, `--char-ngram`): relative
  frequencies over the top-`K` (default 200) n-grams by pooled count across
  target + baseline. NFC-normalized; whitespace runs collapsed to a single space.
- **Punctuation profile:** counts of a fixed Unicode punctuation set per 1k chars.
- **Token-length distribution:** mean + sd of whitespace-token character lengths.
- **Sentence-length distribution:** split on a multilingual terminator set
  `[.!?。！？…।]` → mean + sd of token counts per sentence.
- **Script statistics:** non-ASCII-letter (diacritic/script) ratio, uppercase
  ratio, whitespace ratio.

**Distance.** Burrows-Delta-style: z-normalize each top-`K` char-n-gram relative
frequency against the **baseline corpus** per feature (mean/sd over baseline files),
then `delta` = mean absolute z of the target. Also report `cosine_distance` on the
same top-`K` frequency vector (target vs baseline centroid). Headline = `delta`.
Per-baseline-file deltas summarized as mean/sd. Top contributing n-grams (largest
|z|) surfaced for interpretability.

`--baseline-dir` is **required** (it is a distance tool). `--lang` is **required**
provenance: it is recorded in the envelope and the claim-license, and the
claim-license states that comparing across languages is meaningless — target and
baseline must share the declared language.

## Contract (the testable interface)

- **task_surface:** existing `voice_coherence` (no new surface).
- **CLI:** `python3 plugins/setec-voiceprint/scripts/crosslingual_voice_distance.py TARGET[.md|.txt] --baseline-dir DIR --lang CODE [--char-ngram 3] [--top-k 200] [--json] [--out PATH]`.
- **JSON envelope:** `build_output(task_surface="voice_coherence", …)`, with
  `baseline=build_baseline_metadata(...)`. `results` keys: `lang`, `char_ngram_n`,
  `top_k`, `delta`, `cosine_distance`, `per_baseline_file` (`{mean, sd, n}`),
  `top_contributing_ngrams` (`[[ngram, z], …]`), `target_profile` + `baseline_profile`
  (punctuation per-1k, token/sentence length mean+sd, script ratios). Carries a
  `ClaimLicense`.
- **Claim license:** *licenses* "a language-agnostic, parser-free stylometric distance
  (character n-grams, punctuation, token/sentence-length, script statistics) between a
  target and a baseline corpus in the declared language"; *refuses* AI-provenance and
  identity verdicts, any morphology- or function-word-dependent voice claim (it is
  language-agnostic, not language-aware), and cross-language comparison (the `--lang`
  tag is provenance; target and baseline must share it). Caveats: character n-grams
  carry topic leakage; needs matched language + register; PROVISIONAL — no operating
  point ships.
- **capabilities.yaml entry:** `id: crosslingual_voice_distance`,
  `surface: voice_coherence`, `status: heuristic`, `handoff: experimental`,
  `consumers: []`, `family: voice-coherence`,
  `compute: {tier: core, length_floor_words: 500}`, `dependencies.python: []`,
  `inputs.required: ["--baseline-dir register-matched same-language baseline corpus", "--lang language code"]`.
- **Availability:** target under 500 words, or empty baseline → `available=False` + warning.

## Test contract (`plugins/setec-voiceprint/scripts/tests/test_crosslingual_voice_distance.py`)

- `test_task_surface_is_voice_coherence` — `TASK_SURFACE == "voice_coherence"`.
- `test_envelope_shape_validates` — payload validates; baseline metadata present; result keys present.
- `test_no_spacy_import` — the module's source imports neither `spacy` nor `en_core_web`.
- `test_self_distance_small` — target identical to a baseline file → small `delta`.
- `test_distinct_text_larger_distance` — a stylistically different target → larger `delta` than the self case.
- `test_non_ascii_text` — a Spanish/accented fixture runs without error; `script ratio` > 0.
- `test_lang_recorded` — `--lang es` is echoed into `results["lang"]` and the claim-license.
- `test_baseline_required` — missing `--baseline-dir` exits non-zero / unavailable.
- `test_claim_license_refuses_morphology_and_crosslang` — `does_not_license` names morphology / cross-language.
- `test_too_short_unavailable` — under-floor target → `available=False`.
- `test_deterministic` — same inputs → identical `delta`.

## Calibration posture

PROVISIONAL — ships no operating point. `delta` is a relative distance; what counts
as "far" is operator-/corpus-defined, exactly as the English `voice_distance`
bootstrap path is. A future non-English labeled corpus (e.g., a per-language PAN
authorship set) would calibrate a percentile band; out of scope for v1.

## Out of scope / non-goals

- Not language-aware: no morphology, no per-language function-word list, no
  non-English POS/dependency parse (those are the heavier follow-on the spec names).
- Not a verdict surface: it produces a distance, never an authorship call.
- Not a translation / translationese detector (a separate, topic-bound concern).

## Open questions

- Whether to ship per-language function-word lists (promoting selected languages
  from agnostic to aware) as an opt-in data directory.
- Whether the bootstrap percentile machinery in `length_bootstrap.py` can wrap this
  delta the way it wraps the English `voice_distance` delta.
