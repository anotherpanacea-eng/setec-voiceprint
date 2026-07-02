---
name: setec
description: >
  Discoverability entry point for the SETEC Voiceprint framework. Use
  when the user asks "what can SETEC do," "which audit should I run
  for X," "what's available given my dependencies," "how do I
  detect / measure / diagnose / audit Y in this prose," or otherwise
  needs help routing among the framework's many task surfaces and
  scripts. Also triggers on "show me everything SETEC can do,"
  "recommend a pipeline for this situation," "what runs on stdlib,"
  "what handles essays / fiction / ESL," and any question framed as
  "I have X, what should I run?" Do NOT use for actual execution of
  audits — this skill recommends; the user runs the recommended
  commands.
version: 1.0.0
---

# /setec — capability routing for SETEC Voiceprint

This skill is the answer to "what can this plugin do?" and "what
should I run for *this* situation?" It reads the capabilities
manifest at `plugins/setec-voiceprint/capabilities.d/` (via
`plugins/setec-voiceprint/scripts/capabilities.py`) and routes the
user toward the right audit, with the right caveats, at the right
compute tier.

## What this skill does

  1. Asks the user what they're trying to figure out (free text).
  2. Asks for the context that constrains the choice: target length,
     register, whether they have a baseline corpus, what compute
     tier they have (stdlib / spaCy / surprisal models / API LLM
     access).
  3. Calls `capabilities.py recommend` to get a ranked list of
     matching audits.
  4. Presents the top 1–3 with their purpose, `use_when`,
     `do_not_use_when`, example invocation, and whether the user's
     environment has the required deps installed.
  5. If multiple audits are recommended, frames the pipeline (run
     this first, then that, then adjudicate using this reference
     doc).

## What this skill does NOT do

  * It does not execute audits. The user runs the recommended
    commands themselves.
  * It does not adjudicate AI / human verdicts. The framework's
    `do_not_use_when` warnings are surfaced; the user decides.
  * It does not invent capabilities. If `capabilities.py recommend`
    returns nothing, the skill says so and points the user at
    `capabilities.py list` to browse manually rather than guessing.

## Step 1: Ask what they want

If the user's message already includes a clear situation phrase
("I have a 5000-word short story and want to know if AI edited it"),
use it directly. Otherwise, ask once, briefly:

  > What are you trying to figure out? (e.g., "is this essay
  > AI-generated?", "did this revision preserve the writer's voice?",
  > "what tools do I have for ESL writing?")

## Step 2: Gather context

Three pieces of context narrow the recommendation:

  * **Length** — `--length-floor-max <words>` filters out audits
    whose minimum length exceeds the target. If the user says
    "short story," assume ≥ 2000 words; if "essay," ≥ 500; if
    "fragment," ≥ 200.
  * **Register** — `--register long_form_fiction`, `blog_essay`,
    `long_form_journalism`, `academic_philosophy`, `all_fiction`,
    `all_nonfiction`. Infer from the situation; only ask if
    ambiguous.
  * **Compute / availability** — Pass `--available` to filter to
    audits with installed deps. If the user says "I haven't
    installed anything yet," recommend `dependency_check` first.

## Step 3: Run the recommendation

Use the bash tool:

```bash
python3 plugins/setec-voiceprint/scripts/capabilities.py recommend \
    --situation "<user's situation, verbatim>" \
    --available \
    --format md
```

If `--available` returns an empty list but the un-filtered list has
results, surface that explicitly: "These audits would help but
require deps you don't have installed yet. Want me to walk you
through `dependency_check`?"

## Step 4: Present the result

The `capabilities.py recommend` markdown output is already shaped
for direct presentation. Optionally augment with:

  * **Pipeline framing** when multiple audits are returned: "Run X
    first to get distributional evidence, then Y for the
    pattern-level audit, then adjudicate using the
    `do_not_use_when` warnings as your decision rules."
  * **Cost callout** when the recommendation includes an `api_llm`
    tier audit: surface the cost note prominently so the user
    knows what they're spending.
  * **Reference docs** are already in each entry's `references:`
    list; promote the top one ("see `references/aic-flags.md` for
    the source-triage discipline") rather than dumping the full
    list.

## Step 5: Hand off

Do not run the audit yourself. The skill's job is routing; the
user executes. If the user explicitly asks the model to run the
audit on a target file, switch context out of the skill and use
the bash tool directly — but flag the choice ("I'll run it; in
the future this is the kind of thing the user runs themselves so
they can see the diagnostic output").

**When two or more audits are recommended for one target**, present
the multi-surface run-set runner as the default "full picture"
option instead of a hand-chained pipeline (same posture: this skill
recommends, the user runs):

```bash
python3 plugins/setec-voiceprint/scripts/setec_run_set.py \
    --set full_picture --target draft.md \
    [--baseline-dir baselines/<register>/] \
    [--attach general_imposters=out/gi.json] \
    [--attach idiolect_detector=out/idiolect.json]
```

Two presets exist: `smoothing_core` (the five target-only core-tier
audits) and `full_picture` (adds `voice_distance`, which runs only
with `--baseline-dir`, plus `general_imposters` and
`idiolect_detector`, which are **attach-only** — they need comparator
corpora the runner has no args for, so the user produces their
envelopes separately and joins them via `--attach <id>=<path>`).
The runner feeds the collected envelopes to
`surface_disagreement_resolver` and emits disagreement patterns plus
mechanical next-action commands — no composite score, no verdict.
Note: the runner's own `--situation` output is informational only —
this skill's recommendation remains authoritative when the two
differ.

## Common situations + canonical routes

These are baked into `capabilities.py`'s `CURATED_ROUTES`; this
section duplicates them in narrative form so the skill can answer
without always shelling out.

  * **"Is this essay AI-generated?"** → `variance_audit` (Tier 1 +
    Tier 4 surprisal if available), `aic_pattern_audit` (named
    patterns), optionally `binoculars_audit` (perplexity), then
    `validation_harness` if the user has a labeled corpus.

  * **"Is this long-form fiction AI-generated?"** → All of the
    above PLUS `narrative_decision_audit` (the StoryScope-anchored
    Surface 6 audit, which survives stylistic rewriting).

  * **"Did this revision preserve the writer's voice?"** →
    `voice_distance` (Burrows Delta + per-feature cosine against
    baseline), `idiolect_detector` (preservation list), optionally
    `mimicry_cosplay_audit` if you suspect the LLM cloned the
    writer's surface.

  * **"Run the full picture over this draft" / any situation where
    ≥ 2 of the above audits apply to one target** → the
    `setec_run_set` runner (`--set smoothing_core` or
    `--set full_picture`) instead of hand-chaining scripts: it
    collects the member envelopes, runs the
    `surface_disagreement_resolver` cross-surface read, and emits
    the next-action commands. `general_imposters` /
    `idiolect_detector` join via `--attach` (see Step 5).

  * **"What's safe to ask an LLM editor to change?"** →
    `restoration_packet` (turns diagnostic JSON into bounded
    revision instructions), then `before_after_restoration` on the
    revised draft.

  * **"I'm calibrating thresholds against a labeled corpus."** →
    `manifest_validator` (validate the manifest), then
    `validation_harness` (compute FPR/TPR/AUC), then the
    per-surface calibrator (e.g., `binoculars_calibrate` for
    Surface 5).

  * **"What can I run with no extra installs?"** →
    `capabilities.py list --tier core` lists everything that runs
    on the framework's core requirements (spaCy + scikit-learn +
    statsmodels). Tier `api_llm` and Tier `surprisal` audits are
    omitted from that view.

  * **"What about ESL writing?"** → Surface the
    `calibration-findings-2026-05-10.md` finding (Tier 1 variance
    signals invert against ESL student writing); recommend
    `variance_audit` with the explicit caveat that the registry
    polarity may not hold, and point at
    `references/calibration-findings-2026-05-10.md`.

## Failure modes

  * **No recommendation matches.** Don't invent one. Say "I can't
    find a clear match; here's the full list at
    `capabilities.py list --tier <user's tier>`" and let them
    browse.

  * **The user names a tool that isn't in the manifest.** Check
    `capabilities.py list --include-todo` — it may be an
    auto-seeded entry with status `todo`. If so, surface the
    auto-extracted purpose with the caveat that it hasn't been
    hand-curated yet.

  * **The manifest is missing.** `capabilities.py` raises
    `FileNotFoundError` with a pointer at the seed command. Run
    the seed command at the user's request rather than working
    around the missing file.
