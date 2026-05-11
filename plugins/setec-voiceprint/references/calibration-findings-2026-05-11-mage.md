# Calibration findings: MAGE full Tier-1 survey (2026-05-11)

SETEC's second full single-corpus calibration survey produced an
empirical finding that is more important than any committed
threshold would have been: **stylometric signals do not have
stable polarity across labeled corpora**. The signals that
discriminated AI from human on EditLens (structural variance:
`burstiness_B`, `sentence_length_sd`, `fkgl_sd`) **invert direction**
on MAGE. The signals that inverted on EditLens (lexical diversity:
`mattr`, `mtld`, `yules_k`) **match polarity** on MAGE.

This is the empirical case for the framework's "Stylometry to the
people" policy: anchored thresholds derived from one corpus do not
generalize to another, and the framework cannot honestly ship a
single threshold as a load-bearing default.

The MAGE survey itself is an audit record under that policy — no
threshold is committed to `COMPRESSION_HEURISTICS`.

## Headline results

| Statistic | Value |
|---|---|
| Survey wall-clock | ~15h 8m single-threaded on M-series Mac |
| Rows scored | 338,226 (filtered from 436,606 manifest entries by `use: validation`) |
| Positive class (AI) | 120,476 |
| Negative class (human) | 217,750 |
| **Signals passing all gates at FPR ≤ 0.01** | **0 of 11** |
| Tier 1 signals scored | 8 (the survey ran `--no-tier2 --no-tier3`) |
| Tier 2 / Tier 3 signals | errored as expected (no scores produced) |

The 0-of-11 number is not a calibration failure. It is a finding
about MAGE's discriminability: at the framework's stated 1% FPR
operating point, no single Tier 1 lexical or structural signal
catches a non-trivial fraction of AI essays across MAGE's
heterogeneous source-dataset mix.

## The corpus

MAGE (Yichen Li et al., ACL 2024; MIT license; HF `yaful/MAGE`).
Manifest fetched via `scripts/calibration/fetch_mage.py` and
converted via `scripts/calibration/mage_to_manifest.py` to a
validator-clean 436k-row JSONL. Properties that matter for
interpretation:

- **10 source datasets** spanning 2019-2024: HC3, GPT-Sentinel,
  GLTR-TweepFake, GROVER, GPT-2 generations, Hello-SimpleAI
  hardcoded examples, Reddit, Yelp, XSum, paragraph datasets,
  plus adversarial-paraphrase rewrites.
- **Heterogeneous AI generations.** Early GPT-2 era outputs sit
  alongside modern GPT-4-era outputs. The stylometric fingerprint
  of "AI prose" varies by model generation, by year, and by
  decoding strategy.
- **Adversarial subsets.** OOD-set-gpt and OOD-set-gpt-paraphrased
  are explicitly designed to evade detectors. Including them
  pushes single-signal discrimination toward chance on a labeled
  cross-corpus basis.
- **Human comparator side** is a mix of public-domain text, news,
  social media, and academic prose — not register-controlled, not
  language-controlled (some non-native English present).

The corpus is structurally hostile to "single signal + global
threshold + low FPR" — and that hostility is what makes the
finding instructive.

## Direction-aware AUC by signal (MAGE Tier 1)

| signal | registry direction | raw AUC | da_AUC | polarity vs registry |
|---|---|---:|---:|---|
| `mattr` | lt | 0.425 | **0.575** | matches |
| `mtld` | lt | 0.436 | **0.564** | matches |
| `yules_k` | gt | 0.560 | **0.560** | matches |
| `shannon_entropy` | lt | 0.483 | **0.517** | matches (weak) |
| `connective_density` | gt | 0.493 | 0.493 | **inverted** |
| `sentence_length_sd` | lt | 0.521 | 0.479 | **inverted** |
| `fkgl_sd` | lt | 0.521 | 0.479 | **inverted** |
| `burstiness_B` | lt | 0.546 | 0.454 | **inverted** |

Direction-aware AUC reads on a consistent ">0.5 means polarity
matches the registry's hypothesis" scale. The strongest signal on
MAGE (`mattr`) is at 0.575 — moderate-strength real signal in the
correct direction, well above chance but well below "clean
discrimination."

## The cross-corpus inversion (the load-bearing finding)

Comparing MAGE results to the 2026-05-10 EditLens calibration
(`calibration-findings-2026-05-10.md`):

| signal | EditLens da_AUC | MAGE da_AUC | What changed |
|---|---:|---:|---|
| `mattr` | 0.158 (inverted) | 0.575 (matches) | **flipped: ESL human comparator → typical-English** |
| `mtld` | 0.132 (inverted) | 0.564 (matches) | **flipped: same** |
| `yules_k` | 0.337 (inverted) | 0.560 (matches) | **flipped: same** |
| `shannon_entropy` | 0.400 (inverted) | 0.517 (matches) | **flipped: same** |
| `connective_density` | 0.529 (matches, weak) | 0.493 (inverted) | **flipped: opposite direction** |
| `sentence_length_sd` | 0.695 (strong match) | 0.479 (inverted) | **flipped: opposite direction** |
| `fkgl_sd` | 0.635 (match) | 0.479 (inverted) | **flipped: opposite direction** |
| `burstiness_B` | 0.683 (match) | 0.454 (inverted) | **flipped: opposite direction** |

**Every Tier 1 signal that produced a comparable measurement
across both corpora flipped polarity between them.**

The pattern is consistent and explicable, not random. EditLens's
human comparator is predominantly ESL student writing — small
working vocabulary, formulaic phrasing, narrower topic range. AI
prose looks *more* lexically diverse than ESL student prose, so
the lexical signals (`mattr`, `mtld`, `yules_k`, `shannon_entropy`)
inverted: AI was higher, not lower. But structural signals
(`burstiness_B`, sentence-length variance, FKGL variance) still
distinguished: AI's sentence structure was flatter than ESL human
sentence structure on EditLens.

MAGE's human side is typical English prose across genres — news,
social media, academic, Reddit. Against that comparator, AI prose
shows lower lexical diversity than its human comparator (lexical
signals match polarity). But because MAGE's "AI" side includes
adversarial paraphrases and early GPT-2 generations with their own
distinctive sentence-shape patterns, the structural signals invert
in the opposite direction: AI is now the *more* structurally
varied side, not the less.

**Neither corpus is "wrong." Both are real. They produce different
polarities because polarity depends on what the human comparator
looks like, and the framework's smoothing-diagnosis hypothesis
presupposes a specific comparator shape (native-fluent + topic-
controlled) that neither corpus matches.**

## What this confirms about the Stylometry-to-the-people policy

The policy decision from 2026-05-11 (recorded in
`scripts/calibration/PROVENANCE.md`) reads:

> SETEC follows a "Stylometry to the people" posture: the framework
> ships methods, tooling, and PROVENANCE discipline. It does not
> ship per-signal decision thresholds derived from labeled corpora
> (EditLens, RAID, MAGE, or any other) as load-bearing defaults.

That decision was made before the MAGE results landed. Today's
results are the empirical case that makes the policy load-bearing:

- If we had committed an EditLens threshold for `burstiness_B`
  (which we did, then reverted to PROVISIONAL on 2026-05-11), users
  with prose closer to MAGE's distribution would have been measured
  against a signal *running in the wrong direction*.
- If we had committed a MAGE threshold for `mattr` (which the
  policy now forbids), users with prose closer to EditLens's ESL-
  student distribution would have been measured against a signal
  *running in the wrong direction*.
- There is no third corpus that would produce a "right" threshold
  for both. The signal's polarity depends on the comparator, and
  no global comparator captures every user's deployment context.

The framework's honest posture is what it already declared: ship
the methodology, document the polarity-volatility, give users the
tooling to calibrate against their own baseline. The MAGE result
is now the strongest evidence we have for why that posture is the
only one the math entitles.

## What MAGE does NOT measure (and what we still have no evidence about)

The MAGE survey scored 8 of SETEC's ~30 signals and only the
cheapest Tier 1 ones. Several framework load-bearing claims sit
outside what this survey could test:

- **Within-author drift.** Voice-distance against a writer's own
  pre-AI baseline (the entire voice-coherence task surface) is a
  *within-author* task. MAGE is *cross-author across 10 source
  datasets*. Whether SETEC's within-author tools discriminate a
  writer's pre-AI prose from their AI-collaborated drafts is not
  what MAGE tells us. It's a different task.
- **Tier 2 signals.** POS-bigram KL and MDD variance — two of the
  framework's most diagnostically central signals — were not
  scored. The `--no-tier2` flag was set so the survey could finish
  in ~15h on a laptop; running with Tier 2 would have been a
  multi-day single-machine project.
- **Tier 3 signals.** Adjacent-sentence cosine, embedding-based
  signals, the R12 Semantic Trajectory Audit's flatness measure —
  none were scored. Same reason; same caveat.
- **AIC pattern density** (Layer B). The named-pattern audits
  (correctio, pseudo-aphorism, manifesto cadence, triplet,
  professional-parallel stack, plus the four nonfiction parallel
  patterns) are not aggregate signals with thresholds — they are
  per-document density audits. The framework's Layer B value is
  not what MAGE tests.
- **Localized signal.** SETEC's sliding-window heatmap and
  source-of-smoothing localization (R8 work) produce per-window
  diagnostics for revision, not corpus-level discrimination.
  MAGE tests the latter and is silent on the former.
- **Paired-release tools R1-R12.** Every signal added by the 12
  paired releases — paragraph architecture, discourse moves,
  agency/abstraction, punctuation cadence, stance/modality,
  function-word grammar, construction signature, phraseological
  signature, mimicry/cosplay, known-editor, draft-history,
  semantic trajectory — sits outside the 8 Tier-1 signals MAGE
  scored.

The MAGE finding is therefore narrow: it falsifies "single Tier 1
signal at conservative FPR discriminates across heterogeneous
labeled corpora." It does not test, and therefore cannot falsify,
the framework's actual claims.

## Why this finding matters more than any threshold would have

Three reasons the polarity-inversion finding is the load-bearing
artifact, not the absence of a committed threshold:

1. **It demonstrates the policy.** SETEC's "we ship methodology,
   not thresholds" posture is no longer just a design preference;
   it is the response to empirical evidence that anchored
   thresholds from one corpus mis-direct on another.
2. **It refines what the framework actually claims.** The
   smoothing-diagnosis hypothesis ("AI prose compresses the
   variance that fluent native human prose shows") presupposes a
   specific human comparator. EditLens's ESL student writing
   doesn't satisfy that presupposition; MAGE's adversarial-mixed
   corpus doesn't satisfy a related one. The hypothesis is not
   "universal"; it is "conditional on comparator." Future
   framework documentation should state this explicitly.
3. **It vindicates the cross-corpus survey discipline.** Running
   both EditLens and MAGE separately and comparing was the right
   move. Running only one would have produced a confident
   threshold that the other survey would have shown to be wrong.
   The PROVENANCE discipline of "calibration entries are keyed by
   corpus, not by signal alone" is what makes the cross-corpus
   audit possible.

## Future calibration runs

Per the spec for the sharded-calibration toolchain
(v1.44.0 shipped, v1.44.1+ pending), future RAID-scale calibration
runs will be checkpointed, parallelizable, and crash-safe. Whether
RAID — a third heterogeneous corpus, larger and more diverse than
MAGE — produces a third distinct polarity profile is an open
question worth answering. The hypothesis from these two corpora
is: yes, it will, in yet another direction. The Stylometry-to-the-
people policy means that prediction does not threaten the
framework; it adds to the audit trail.

## See also

- `scripts/calibration/PROVENANCE.md` entry
  `mage_full_tier1_fpr0.01_2026-05-11`
- `references/calibration-findings-2026-05-10.md` — the prior
  EditLens single-corpus finding this document builds on
- `internal/SPEC_sharded_calibration.md` (gitignored) — the
  architecture that will run future calibration surveys
  checkpointed and parallelized
- README "Costs and resources at the calibration tier" — the
  honest disk/time/memory footprint for a calibration run, now
  with one more empirical data point (the 15h MAGE wall-clock
  was inside the predicted 11-18h window, on the long end)
