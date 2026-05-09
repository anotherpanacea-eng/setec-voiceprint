---
name: craft-restoration
description: >
  Identify AI-prose craft patterns (the seven AIC flag families) in a
  passage, decide whether each pattern is earned in context, and
  recommend specific revision moves. Use when the user asks to "audit
  this passage for AI patterns," "is this earned in context,"
  "Negation hedge," "Indefinite-pronoun gesture," "Disguised correctio,"
  "Pseudo-aphorism," "Manifesto cadence," "rhetorical countermoves,"
  "source triage," "earned by frame," "is the pattern thematic or
  symptomatic," "what's the variance reinjection move," or any
  request involving craft-level diagnosis of AI patterns and revision
  guidance. Also triggers on "AIC flags," "AIC-1 through AIC-7,"
  "Layer B," "Layer C," "voice slip vs. lost callback," or "genre
  tolerance."
version: 1.0.0
---

# Craft Restoration (SETEC Surface 4)

This skill is a craft-pattern diagnostic and revision adviser. It identifies the seven AIC (AI craft) flag families that survive across model generations because they are structural rather than lexical, decides whether each instance is earned in context, and recommends specific revision moves. The surface is primarily reference markdown — the named-pattern taxonomy, source-triage methodology, and rhetorical countermoves all live in prose because the earned/unearned verdict is irreducibly a writer's call per instance. One quantitative pre-pass script (`aic_pattern_audit.py`) counts named-pattern density and surfaces candidate instances for that adjudication; everything else on this surface stays in the reference docs.

## What this surface licenses, and what it does not

- **Licenses:** "these specific patterns are present, here is the calibrated tolerance for each in this genre, here is the source-triage verdict (earned / unearned / earned by frame), and here are the revision moves applicable to each unearned instance."
- **Does not license:** "this passage was written by AI." Most surface flags resolve as earned on triage; the framework's authority comes from being honest about that.

## Where the content lives

The four reference documents are at `${CLAUDE_PLUGIN_ROOT}/references/`:

- **`aic-flags.md`** — Layer B reference. Seven flag families with distributional signatures, named subtypes (Negation hedge, Indefinite-pronoun gesture, Disguised correctio, Pseudo-aphorism, Manifesto cadence), nonfiction parallel pattern set, genre tolerance quick-reference table (7 flags × 6 genres with three tolerance bands plus footnotes), and pattern-synthesis flag compounds.
- **`source-triage.md`** — Layer C reference. The earned / unearned / earned-by-frame triage methodology, voice-attribution work, voice-slip-vs-lost-callback distinction, multi-register-narrator handling.
- **`rhetorical-countermoves.md`** — figure-by-flag pairings (fiction + nonfiction additions). Three universal principles: payoff test, soft n-gram preservation, variance reinjection.
- **`distributional-diagnostics.md`** — Layer A reference. Eleven variance signals with formulas, calibration warnings, and the writer-specific calibration note. Cross-references the smoothing-diagnosis skill.

## Workflow

1. **Identify candidate passages** with the smoothing-diagnosis skill (Surface 1) — Layer A surfaces compressed regions worth a craft pass.
2. **Run the AIC scan** by reading the passage with `aic-flags.md`'s seven-family taxonomy in mind. Note the genre tolerance band for each flag fired. For a quantitative pre-pass on the named patterns from `source-triage.md`, run `aic_pattern_audit.py` (see CLI block below) which counts negation hedge, disguised correctio, pseudo-aphorism, manifesto cadence, triplet, professional-parallel stack, and the four nonfiction parallel patterns (false-balance, hedge-and-affirm, recommendation template, authority laundering) at per-thousand-word density and (with `--baseline-dir`) flags densities that exceed the writer's pre-AI baseline.
3. **Source-triage every flag** using `source-triage.md`. Three verdicts: earned (the pattern serves a craft purpose in context), unearned (the pattern is symptomatic, not thematic), or earned by frame (the surrounding prose explicitly diagnoses the pattern). The script reports candidates and density; the earned/unearned verdict is irreducibly the writer's call per instance.
4. **Recommend revision moves** for unearned flags using `rhetorical-countermoves.md`. The three universal principles steer the move: payoff test (does the pattern pay off in the passage's craft economy?), soft n-gram preservation (don't normalize phrases that carry voice signal), variance reinjection (restore the sub-Gaussian-with-fat-tails shape of the underlying distributions, not just the surface).

## Quick CLI

```bash
# Per-pattern density audit, no baseline (general thresholds only)
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/aic_pattern_audit.py" path/to/draft.md

# With personal pre-AI baseline for register-matched comparison
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/aic_pattern_audit.py" path/to/draft.md \
    --baseline-dir path/to/personal_pre_ai/

# Filter to specific named patterns
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/aic_pattern_audit.py" path/to/draft.md \
    --pattern correctio --pattern pseudo_aphorism --top 30

# JSON output for piping into a revision pass or downstream tooling
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/aic_pattern_audit.py" path/to/draft.md \
    --baseline-dir path/to/personal_pre_ai/ --json
```

The audit strips markdown blockquote lines (`>`) by default so quoted passages from other writers do not inflate the writer's pattern density. Pass `--keep-quotes` to disable. Known v1 limitation: the disguised-correctio detector matches only the explicit "not X, but Y" inline form and the "It is not X. It is Y" frame; subtler multi-sentence correctios are not yet captured. v2 will add a sentence-pair detector.

## The deepest principle

Source triage is the hardest part of the framework to teach and the most valuable. Most surface flags resolve as earned on triage; the skill's authority comes from being honest about that. A passage that fires AIC-3 (sentence-level uniformity) is *unearned* if the uniformity is a smoothing artifact and *earned* if the uniformity serves a craft purpose (drumbeat, ritual, institutional voice). The framework refuses to collapse this distinction into a single verdict.
