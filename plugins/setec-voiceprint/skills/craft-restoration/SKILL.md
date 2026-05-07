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

This skill is a craft-pattern diagnostic and revision adviser. It identifies the seven AIC (AI craft) flag families that survive across model generations because they are structural rather than lexical, decides whether each instance is earned in context, and recommends specific revision moves. It is **not** a script — this surface lives entirely in the framework's reference markdown.

## What this surface licenses, and what it does not

- **Licenses:** "these specific patterns are present, here is the calibrated tolerance for each in this genre, here is the source-triage verdict (earned / unearned / earned by frame), and here are the revision moves applicable to each unearned instance."
- **Does not license:** "this passage was written by AI." Most surface flags resolve as earned on triage; the framework's authority comes from being honest about that.

## Where the content lives

The four reference documents are at `${CLAUDE_PLUGIN_ROOT}/../../references/`:

- **`aic-flags.md`** — Layer B reference. Seven flag families with distributional signatures, named subtypes (Negation hedge, Indefinite-pronoun gesture, Disguised correctio, Pseudo-aphorism, Manifesto cadence), nonfiction parallel pattern set, genre tolerance quick-reference table (7 flags × 6 genres with three tolerance bands plus footnotes), and pattern-synthesis flag compounds.
- **`source-triage.md`** — Layer C reference. The earned / unearned / earned-by-frame triage methodology, voice-attribution work, voice-slip-vs-lost-callback distinction, multi-register-narrator handling.
- **`rhetorical-countermoves.md`** — figure-by-flag pairings (fiction + nonfiction additions). Three universal principles: payoff test, soft n-gram preservation, variance reinjection.
- **`distributional-diagnostics.md`** — Layer A reference. Eleven variance signals with formulas, calibration warnings, and the writer-specific calibration note. Cross-references the smoothing-diagnosis skill.

## Workflow

1. **Identify candidate passages** with the smoothing-diagnosis skill (Surface 1) — Layer A surfaces compressed regions worth a craft pass.
2. **Run the AIC scan** by reading the passage with `aic-flags.md`'s seven-family taxonomy in mind. Note the genre tolerance band for each flag fired.
3. **Source-triage every flag** using `source-triage.md`. Three verdicts: earned (the pattern serves a craft purpose in context), unearned (the pattern is symptomatic, not thematic), or earned by frame (the surrounding prose explicitly diagnoses the pattern).
4. **Recommend revision moves** for unearned flags using `rhetorical-countermoves.md`. The three universal principles steer the move: payoff test (does the pattern pay off in the passage's craft economy?), soft n-gram preservation (don't normalize phrases that carry voice signal), variance reinjection (restore the sub-Gaussian-with-fat-tails shape of the underlying distributions, not just the surface).

## The deepest principle

Source triage is the hardest part of the framework to teach and the most valuable. Most surface flags resolve as earned on triage; the skill's authority comes from being honest about that. A passage that fires AIC-3 (sentence-level uniformity) is *unearned* if the uniformity is a smoothing artifact and *earned* if the uniformity serves a craft purpose (drumbeat, ritual, institutional voice). The framework refuses to collapse this distinction into a single verdict.
