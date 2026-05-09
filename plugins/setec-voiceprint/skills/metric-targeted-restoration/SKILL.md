---
name: metric-targeted-restoration
description: >
  Translate SETEC diagnostic outputs (variance audit, POS-bigram diff,
  voice distance, idiolect detector, AIC pattern audit) into bounded
  revision-safe prompt packets that classify each signal as direct,
  translated, investigate-first, or avoid-direct. Use when the user
  asks to "reverse this smoothing trend," "restore the bigram drift,"
  "make a revision prompt from this diagnostic," "what can an LLM
  safely target," "metric-targeted restoration," "prompt packet,"
  "post-check this revision," "translate POS bigrams/trigrams," or
  "don't just give me AIC flags; use the Layer A findings." Sibling
  to craft-restoration (which reads prose and AIC flags); this skill
  reads diagnostic JSON and emits revision instructions with named
  guardrails and required post-check commands.
version: 1.0.0
---

# Metric-Targeted Restoration (SETEC Surface 4 — sibling to craft-restoration)

This skill is the missing bridge between SETEC's diagnostic surfaces (Surface 1 smoothing-diagnosis, Surface 2 voice-coherence) and its revision-advisor surface (Surface 4 craft-restoration). The diagnostics say *what* drifted in distributional terms; this skill translates those outputs into bounded prompt packets that classify each signal by what kind of revision can responsibly address it.

The framework's resistance to metric gaming lives in the targetability taxonomy this skill enforces. Some signals are direct craft targets; some are translatable; some should trigger a deeper diagnostic read before any revision; some should never become prompt targets at all because optimizing them directly invites prose damage and a false sense of having fixed the problem.

## What this surface licenses, and what it does not

- **Licenses:** "These specific signals moved; here is each signal's targetability class; here are the revision moves applicable to direct/translated targets; here are the diagnostic questions for investigate-first targets; here are the named guardrails and post-check commands that must run after revision."
- **Does not license:** "This passage was written by AI." "This revision restored the writer's voice." "Optimize this metric and the prose will be better." Most surface signals require local inspection to interpret; the framework's authority comes from being honest about that.

## When to use this skill

Trigger on:

- "Reverse this smoothing trend" / "restore the bigram drift" / "make a revision prompt from this diagnostic"
- "What can an LLM safely target" / "what's safe to prompt directly"
- "Metric-targeted restoration" / "prompt packet" / "post-check this revision"
- "Translate POS bigrams/trigrams" / "translate function-word cluster drift"
- "Don't just give me AIC flags; use the Layer A findings"

Do NOT trigger on:

- "Audit this passage for AI patterns" → use `craft-restoration` instead (the named-pattern + earned/unearned-by-frame skill).
- "Run a variance audit" / "is this draft compressed" → use `smoothing-diagnosis`.
- "Compare this draft to my baseline" / "voiceprint this corpus" → use `voice-coherence`.

## Targetability taxonomy

Every diagnostic signal falls into one of four classes:

1. **Direct target.** Maps cleanly to a promptable prose move. The packet may issue a revision instruction directly. *Examples: high connective density, low burstiness_B, low FKGL SD, AIC named-pattern density, idiolect preservation list.*
2. **Translated target.** Not promptable in raw form, but contributors translate into prose-level moves. POS bigrams/trigrams, dependency n-grams (selected), function-word clusters. *Example: `DET+ADJ+NOUN` elevated → "replace generic descriptor packages with concrete actors, objects, or verbs."*
3. **Investigate-first target.** Signal says "something is off" but not which revision should happen. Packet asks a diagnostic question and points to local evidence. *Examples: low MATTR/MTLD, high Yule's K, low Shannon entropy.*
4. **Avoid direct targeting.** Signal should not become a prompt target. Mentioned as evidence; never as a revision instruction. *Examples: aggregate POS-bigram KL/JSD, overall Burrows Delta, character n-gram distance, AUC, compression band.*

The full taxonomy with translation tables lives in `${CLAUDE_PLUGIN_ROOT}/references/metric-targeted-restoration.md`.

## Workflow

1. **Run the underlying diagnostics first.** This skill consumes their JSON outputs.
   - Layer A: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/variance_audit.py target.txt --json --baseline-dir baseline/ > variance.json`
   - POS-bigram drift: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/bigram_diff.py target.txt --cluster-dir comparators/ --json > bigram.json`
   - Voice distance: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/voice_distance.py target.txt --baseline-dir baseline/ --json > voice.json`
   - Idiolect preservation: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/idiolect_detector.py --target-dir writer/ --reference-dir reference/ --json > idiolect.json`
   - Layer B/C named patterns: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/aic_pattern_audit.py target.txt --baseline-dir baseline/ --json > aic.json`

2. **Generate the packet:**

   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/restoration_packet.py \
       --variance-json variance.json \
       --bigram-json bigram.json \
       --idiolect-json idiolect.json \
       --aic-json aic.json \
       --genre essay \
       --target-scope "paragraphs 4-8" \
       --max-targets 3 \
       --out packet.md \
       --json-out packet.json
   ```

3. **Read the packet.** The markdown report has three sections (when relevant inputs are supplied):
   - **Targets (actionable):** direct + translated, ordered by class then severity. The revision prompt only acts on these.
   - **Investigate first:** signals where causes need a local read before any rewrite.
   - **Evidence (do not target directly):** aggregate/avoid-direct signals; context only.

4. **Apply the revision prompt.** The packet's "Prompt for model or human reviser" section is copy/paste-ready. Hand it to the writer or to an LLM-revision pass with the named guardrails attached.

5. **Run the post-check.** Every packet carries explicit post-check commands (typically `variance_audit.py` and `bigram_diff.py` reruns with the same baseline). Compare before/after:
   - Did the targeted signal move in the intended direction?
   - Did nearby non-target signals degrade?
   - Did the idiolect preservation list survive?
   - Did the prose still satisfy source triage?

   The first implementation makes step 5 manual. A later `before_after_restoration.py` will automate the comparison.

## Guardrails (always attached to revision prompts)

- Do not add new facts.
- Do not replace writer-specific phrases from the preservation list.
- Do not optimize for POS tags or aggregate divergence directly.
- Do not flatten idiolect; preserve recurring writer-specific words and collocations.
- Do not rewrite outside the named target scope.

## Privacy

Prompt packets can contain idiolectic phrases and voice-profile evidence. Treat them as voice-cloning inputs:

- If `--idiolect-json` or `--voice-json` is used, the packet is private by default.
- Markdown/JSON output outside `ai-prose-baselines-private/` requires `--allow-public-output`.
- Never include a full voice profile in a prompt; include only the minimum local preservation list needed for the passage.
- Warn before sending idiolect-rich prompts to a remote LLM.

## CLI flags

| Flag | Purpose |
|---|---|
| `--variance-json PATH` | variance_audit JSON |
| `--bigram-json PATH` | bigram_diff or manuscript_bigram_diff JSON |
| `--voice-json PATH` | voice_distance JSON |
| `--idiolect-json PATH` | idiolect_detector JSON |
| `--aic-json PATH` | aic_pattern_audit JSON |
| `--genre TAG` | Genre tag for the prompt context |
| `--target-scope STRING` | Locality of the revision (e.g., "paragraphs 4-8") |
| `--max-targets N` | Cap actionable targets (direct + translated). Default 3 |
| `--targetability {all,direct,translated,investigate_first,actionable}` | Filter packets by class. `actionable` = direct + translated only |
| `--out PATH` | Markdown output path |
| `--json-out PATH` | JSON output path |
| `--no-prompt` | Suppress the prompt block; emit targets only |
| `--no-show-poor-targets` | Hide avoid_direct evidence from markdown |
| `--allow-public-output` | Allow output outside `ai-prose-baselines-private/` when private inputs are used |

## Limitations (v1)

- Does not auto-rewrite prose. The packet is for a human or LLM-in-the-loop pass.
- Requires diagnostic JSONs to be produced first (`variance_audit.py` / `bigram_diff.py` / etc.).
- Local examples / windows must be supplied by the diagnostic source. v1 does not extract snippets from the original text by window offsets; that is a v2 enhancement.
- Translation tables are starter sets; new entries land as audit experience identifies stable pattern→prose mappings.
- Voice-distance cluster contributors are roadmap; v1 surfaces only the aggregate as avoid-direct evidence.

## Related references

- `${CLAUDE_PLUGIN_ROOT}/references/metric-targeted-restoration.md` — the targetability taxonomy + POS-bigram/trigram + dep-n-gram translation tables. Canonical reference.
- `${CLAUDE_PLUGIN_ROOT}/references/distributional-diagnostics.md` — Layer A signals.
- `${CLAUDE_PLUGIN_ROOT}/references/aic-flags.md` — Layer B named-pattern taxonomy (drives `aic_pattern_audit.py`).
- `${CLAUDE_PLUGIN_ROOT}/references/source-triage.md` — Layer C earned/unearned/earned-by-frame methodology.
- `${CLAUDE_PLUGIN_ROOT}/references/rhetorical-countermoves.md` — figure-by-flag pairings.
