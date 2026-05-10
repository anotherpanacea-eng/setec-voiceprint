# Calibration findings: EditLens val split (2026-05-10)

The maintainer's first end-to-end calibration run produced one
committed threshold (`burstiness_B`) and surfaced an empirical
finding worth recording on its own: **5 of 11 SETEC signals have
*inverted polarity* on this corpus relative to the registry's
hypothesis.** This document is the calibration *finding* that the
PROVENANCE.md selection criteria call out as separate-from-commit:

> Per Selection Criterion 1: "If the registry says a signal is `lt`
> (compressed when low) but the calibration sweep finds the
> opposite direction discriminates better, the corpus's polarity
> inverts the registry's. That's a *finding* about the corpus or
> about the registry's polarity convention, not a threshold to
> commit."

Five signals matched that description on this run.

## The corpus

EditLens val split (Pangram Labs, ICLR 2026; CC BY-NC-SA 4.0).
Fetched via `scripts/calibration/fetch_pangram_editlens_github.py`
at commit `05a588f15d792330ccaf91be8ee4fdb54ce26835`. Shape:

- 1506 essays after dropping the `label=-1` (mixed/edited) class
- 753 `ai_status=ai_generated` / 753 `ai_status=pre_ai_human`
- Predominantly student-essay register
- Human comparator side is largely **ESL student writing** —
  language-status `non_native_advanced` per the upstream metadata
  on the related `nonnative_english.csv` slice; `editlens_test`
  preset on `val.csv` defaults to `native` but the pool is mixed

This corpus characteristic — ESL student writing as the human
comparator — is the load-bearing detail for the polarity inversion
below.

## Direction-aware AUC by signal

Direction-aware AUC reads a consistent "≥ 0.5 means polarity
matches the registry's hypothesis" scale across all signals:

| signal | registry direction | raw AUC | da_AUC | polarity |
|---|---|---:|---:|---|
| `sentence_length_sd` | lt | 0.305 | **0.695** | matches |
| `burstiness_B` | lt | 0.317 | **0.683** | matches (committed threshold) |
| `adjacent_cosine_sd` | lt | 0.319 | **0.681** | matches |
| `fkgl_sd` | lt | 0.365 | **0.635** | matches |
| `mdd_sd` | lt | 0.415 | **0.585** | matches |
| `connective_density` | gt | 0.529 | **0.529** | matches (weak) |
| `shannon_entropy` | lt | 0.600 | 0.400 | **inverted** |
| `yules_k` | gt | 0.337 | 0.337 | **inverted** |
| `adjacent_cosine_mean` | gt | 0.261 | 0.261 | **inverted** |
| `mattr` | lt | 0.842 | 0.158 | **inverted** |
| `mtld` | lt | 0.868 | 0.132 | **inverted** |

## What the inversion means

The five inverted signals all measure **lexical / entropic
diversity** or **mean cohesion** at the document level:

- `mtld` (Measure of Textual Lexical Diversity) — AI essays score
  153 (mean) vs. ESL human essays at 97. AI's MTLD is *higher*.
- `mattr` (Moving Average TTR) — AI 0.86 vs. ESL human 0.81. AI's
  MATTR is *higher*.
- `shannon_entropy_bits` — AI 6.95 vs. ESL human 6.72. AI's entropy
  is *higher*.
- `yules_k` — AI score lower than ESL human. AI's vocabulary is
  more diverse, K is lower (lower K = more diverse vocab).
- `adjacent_cosine_mean` — AI cohesion mean lower than ESL human.

The registry's hypothesis was built from the smoothing-diagnosis
literature: AI prose **compresses** the variance / diversity that
fluent native human prose shows. The hypothesis presupposes a
fluent-native human comparator. Against ESL student writing — which
itself has compressed diversity (smaller working vocabulary, more
formulaic phrasing, narrower topic range) — the AI essays look *more
diverse*, not less. The polarity flips because the human comparator
is on the same side of the diversity axis as the AI mode-collapse
hypothesis would put AI prose.

This isn't a bug in the framework. It's an empirical clarification
of what the hypothesis actually requires: **the smoothing-diagnosis
hypothesis presupposes the human comparator has native fluency.**
When the human comparator is ESL or student-level, the diversity
gap closes (or inverts) and the same signals that discriminate AI
from native fluent prose stop discriminating, or invert.

## What still discriminates

The six **structural** signals that match polarity (sentence-length
variance, burstiness, FKGL spread, MDD spread, cohesion variance,
connective density) measure rhythm and structural texture — not
lexical diversity. AI essays show smaller variance in these on the
val corpus; that pattern survives the human-comparator-shift to ESL
student writing.

The strongest of the six is `sentence_length_sd` (da_AUC 0.695),
followed by `burstiness_B` (0.683), `adjacent_cosine_sd` (0.681),
`fkgl_sd` (0.635), `mdd_sd` (0.585), and `connective_density`
(0.529). Only `burstiness_B` cleared all four evaluable selection-
criteria gates at FPR target 0.01 (TPR 7.0% at FPR 0.93%); the
others failed gate 4 (TPR-floor) at the chosen FPR — they
discriminate in the right direction but not strongly enough at the
strict-FPR operating point.

## What this means for the framework

1. **The committed `burstiness_B` threshold (-0.622724) is calibrated
   for the student-essay-vs-AI-student-essay context.** It encodes
   "AI essays in this register show smaller sentence-length variance
   than human ESL essays." Generalization to the canonical SETEC
   registers (literary fiction, blog essay, academic philosophy)
   requires separate calibration against those corpora — those are
   the registers where the original smoothing-diagnosis hypothesis
   was built and where the lexical-diversity signals likely behave
   per the registry direction.

2. **The inverted lexical-diversity signals are not invalid;** they
   need a different polarity convention for ESL-comparator corpora.
   Future calibration runs should record the inversion as a
   corpus-specific finding rather than overwriting the registry's
   declared direction. The registry's direction reflects the native-
   fluent hypothesis; the val corpus shows the ESL inversion.

3. **The `language_status` ratchet in `manifest_validator.py`
   becomes load-bearing here.** The validator already warns when
   ESL-labeled entries land in `use: baseline` for voice-coherence
   work; this finding extends the ratchet's rationale to calibration
   corpora. The framework should *expect* polarity inversion when
   the human comparator's language status is non-native — and the
   PROVENANCE notes for any future ESL-comparator threshold should
   record which polarity convention applies.

4. **Future calibration needs native-fluent corpora.** RAID and MAGE
   (roadmap items) include native-fluent samples; those would let
   the framework calibrate `mtld`, `mattr`, `shannon_entropy`,
   `yules_k`, and `adjacent_cosine_mean` against the registry's
   declared polarity. The val-split inversion is informative but
   shouldn't be the only calibration anchor for these signals.

## Why this finding matters more than the threshold commit

`burstiness_B` at -0.622 is a single floating-point number that
will eventually be re-derived against larger / more diverse corpora.
The polarity inversion is a *characterization* of the framework's
behavior under a corpus-mismatch condition, and it generalizes:

- ESL false-positive rates on AI-prose detectors are the field's
  most embarrassing failure mode (Liang et al., *Patterns* 2023:
  61% average FPR on TOEFL essays across seven detectors).
- The framework's defense against that failure mode has been the
  language-status ratchet. This finding adds a second defense:
  documented, empirically-grounded knowledge that *which signals
  invert under ESL comparison*, so users running the framework with
  ESL baselines know which signals to treat as suspect.
- A future "what this licenses / does not license" report block
  should cite this finding when the corpus contains ESL entries.

## See also

- `plugins/setec-voiceprint/scripts/calibration/PROVENANCE.md` —
  the committed `burstiness_B` provenance entry references this
  document.
- `plugins/setec-voiceprint/scripts/calibration/thresholds_calibrated.json`
  — `editlens_val_burstiness_B_fpr0.01_2026-05-10` carries the
  committed threshold + bootstrap CIs.
- `ai-prose-baselines-private/editlens/_records_cache_val.json`
  (private) — the scored records cache the survey was built from.
- `ai-prose-baselines-private/editlens/_survey_val_2026-05-10.json`
  (private) — the full direction-aware survey output.
- `references/implementation-survey.md` § ESL handling — the
  framework's prior commitments about ESL writing.
- Liang et al., *Patterns* 2023 — the empirical anchor for ESL
  false-positive risk.
