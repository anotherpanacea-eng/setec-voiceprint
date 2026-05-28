# Surface 6: Narrative-decision audit

**Status:** new surface, v0.1.0 (2026-05-28). Heuristic / literature_anchored.

## What this surface measures

The 30 *core narrative-decision features* from Russell et al. 2026,
*StoryScope: Narrative-Level Detection of AI-Generated Fiction*
(arXiv:2604.03136v4, COLM 2026 submission), applied to a single
target prose document.

These are **not** stylistic features. The paper's whole point is
the contrast with AIC-7/8/9, em-dashes, "delve", Tier-1 burstiness,
surprisal-style tells, and similar texture-level signals. The 30
features describe narrative *decisions* — how a story articulates
its themes, who drives the resolution, where subplots sit, whether
the narrator addresses the reader, whether time is linear, what
register of sensory rendering is used. The paper reports that
detection accuracy drops only 1.6 macro-F1 points after LAMP-style
span-level rewriting that scrubs surface artifacts (95.5 → 93.9),
because the features survive the kind of edits that defeat AIC-
and surprisal-style detectors.

For SETEC purposes that means this surface is **complementary** to
the existing surfaces, not a replacement. Run it *alongside* a
Tier-1 variance audit, AIC pattern audit, and (optionally)
Binoculars: the four signals answer different questions, and
mixed evidence is the kind a glass-box framework should produce.

## Architecture

The surface ships three scripts:

  - ``narrative_feature_schema.py`` — the 30 core features as data.
    Encoded verbatim from the paper's Table 12. Carries paper-reported
    human and AI group means, response-option vocabularies, and
    bundle assignments (the 7 interpretive themes). An import-time
    self-check guards transcription errors.

  - ``narrative_judge.py`` — pluggable LLM-judge interface. The
    framework does not bake in a model choice. Five backends:
    ``manifest`` (default, reads pre-computed values from a JSON
    file), ``mock`` (deterministic, for tests), ``anthropic``,
    ``openai``, and ``gemini``. The API backends are
    convenience-only — the recommended pipeline for cross-corpus
    work runs the judge outside this script and feeds pre-computed
    values in via the ``manifest`` backend, mirroring the
    operator-side discipline of the rest of the framework.

  - ``narrative_decision_audit.py`` — the audit. Reads the target
    text, runs the judge, validates the emitted values, computes
    per-signal contributions in human-z-units relative to the
    paper's reported means, aggregates by bundle, and emits the
    standard SETEC JSON envelope with a ``claim_license`` block.

## The 33 signals and how they're scored

The 30 features produce 33 signals because 3 categorical features
("Subplot Integration", "Reference Explicitness", "Dominant
Emotional Expression") carry two signals each: one AI-leaning
option and one human-leaning option (see Table 12 of the paper).
Each signal is scored independently.

Per-signal target value:

  - **scale** (Likert 1–5): the integer rated by the judge.
  - **ordinal** (e.g., `none_or_vague` / `minimal` / …): the
    0-based index into the response options.
  - **binary**: 1 if "yes", 0 if "no".
  - **categorical / multi (option-named)**: 1.0 if the target
    selected that option, 0.0 otherwise.

Per-signal contribution to the literature-anchored scorer:

    contribution = (target_value − paper_ai_mean) /
                   (paper_human_mean − paper_ai_mean)

The contribution is in *human-z-units*: 1.0 = the paper's reported
human mean; 0.0 = the paper's reported AI mean. Positive
contributions push the aggregate toward human-leaning; negative
contributions push toward AI-leaning. Values can exceed ±1.0 when
a target sits beyond either of the paper's reported means.

Aggregate score:

    aggregate = mean(contributions across all evaluated signals)

The aggregate is signed. Positive = the target is more human-like
than the paper's mean-of-means; negative = more AI-like.

## What it ships uncalibrated

The audit ships **without per-corpus thresholds**, per the
framework rule. The verdict band defaults to ``uncalibrated`` and
the audit reports the raw aggregate score and per-signal
contributions only. Operators who want a verdict band run the
polarity-check workflow (below) against their own corpus, derive
their own ``--threshold-low`` / ``--threshold-high``, and assume
responsibility for those thresholds being appropriate for their
register, judge model, and corpus shape.

## Register gating

The paper's home register is long-form fiction (mean 4,753 words,
Books3-derived). The audit emits a register warning when the
target is under 2,000 words or appears to contain no dialogue.
Several features (subplot integration, anachrony intensity, frame
narratives, sensory density) are not meaningfully extractable on
essays, op-eds, or short-form prose. The warning is surfaced into
``claim_license.additional_caveats`` so downstream readers see it
inline.

## Cross-corpus polarity check

Mirrors the workflow that produced
``calibration-findings-2026-05-10.md`` (EditLens essays) and
``calibration-findings-2026-05-11-mage.md`` (MAGE) for Tier-1
variance signals: take a labeled corpus, run the audit's judge on
every text, drop the per-text feature values into a JSONL
manifest, then run:

    python3 scripts/calibration/narrative_polarity_audit.py \
        --manifest path/to/judged.jsonl \
        --out-json polarity.json \
        --out-md polarity.md \
        --corpus-name "EditLens val (essays, 2026-MM-DD)" \
        --human-label pre_ai_human \
        --ai-label ai_generated

The polarity audit emits per-signal direction-aware AUC and a
verdict for each of the 33 signals (``matches`` / ``inverted`` /
``chance``), plus an aggregate AUC for the literature-anchored
scorer. It is pure Python and has no model dependencies; the LLM
cost lives in the manifest-construction step, outside this script.

The recommended target corpora for the v0.1 polarity run are:

  - **EditLens val** (1,506 essays). Tests the polarity on a
    register the paper does not cover (essays vs. fiction) and
    against an ESL human comparator (~50% of EditLens is ESL
    student writing per the 2026-05-10 finding). A high inversion
    rate here would tell us narrative-decision features behave
    differently on short-form non-fiction than on long-form
    fiction, which is the expected and acceptable outcome.

  - **A Books3-style fiction slice** (≥ 200 stories per class,
    ≥ 2,000 words each). Tests whether the paper's polarity
    replicates on the operator's own fiction baseline using the
    operator's chosen judge model. This is the matched-register
    sanity check.

## Provenance

  - Russell et al. 2026, *StoryScope: Narrative-Level Detection
    of AI-Generated Fiction* (arXiv:2604.03136v4). Tables 10, 11,
    12 in the appendix carry the 30 core features and their
    human/AI group means.
  - Hamilton et al. 2025, *NarraBench*. Cited by the paper as the
    feature-taxonomy source; the 10 NarraBench dimensions (Agent,
    Social Network, Event, Plot, Structure, Setting, Time,
    Revelation, Perspective, Style) are encoded as the
    ``DIMENSION_LABELS`` map in the schema module.
  - Chakrabarty et al. 2024 (LAMP) — the span-level rewriting
    framework the paper uses to argue narrative features survive
    stylistic editing. Cited in the audit's claim_license block
    when discussing robustness.

## Anti-goals

This surface explicitly does NOT:

  - emit a binary AI / human verdict from the literature-anchored
    score; the verdict band stays ``uncalibrated`` unless an
    operator supplies thresholds;
  - generalize to non-fiction without an operator-side polarity
    check;
  - replace Tier-1 variance, AIC, or Binoculars audits — its job
    is to add a different *level* of evidence (narrative
    decisions) to the operator's existing stack of texture-level
    signals.
