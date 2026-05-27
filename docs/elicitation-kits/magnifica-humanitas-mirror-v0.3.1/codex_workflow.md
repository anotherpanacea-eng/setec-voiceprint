# GPT Codex automation workflow

This document specifies how to run the eight predictions via GPT Codex's serial subagent capability, as an alternative to the manual chat workflow in `README.md`.

---

## Why Codex

The blinding discipline this kit requires is per-prompt context isolation. Each of the eight prompts must be evaluated by a model that has not seen any of the other prompts, the run log's diagnostic features, or the target text. The manual workflow achieves this by opening eight separate ChatGPT chat sessions. Codex achieves the same property by spawning eight serial subagents, each with a fresh context.

Codex cannot spawn parallel subagents at the time of this writing. Serial execution is fine. The isolation requirement is per-context, not per-time.

---

## Model selection

Same constraints as the manual workflow:

- Training cutoff must precede 25 May 2026 (the *Magnifica Humanitas* publication date).
- Preferably cutoff also precedes 24 October 2024 (*Dilexit Nos* publication). If not, the Dilexit control is contaminated in the direction of conservative discrimination.
- Use a single-pass standard model. Avoid reasoning-mode variants for this protocol.

If your Codex environment defaults to a reasoning-mode model, override to a single-pass standard model for this run. Document the model identifier and cutoff in `run_log_during.md`.

---

## Recommended Codex invocation

The following is a template. Adapt to your specific Codex CLI or interface.

```
Task: Run an eight-window prose-continuation test in eight serial subagents. Each subagent must operate in isolation: no shared context between subagents, no access to files outside the subagent's specified inputs.

For each of the eight prompts in ./prompts/:
1. Spawn a fresh subagent.
2. Pass the contents of the prompt file as the user message.
3. Capture the subagent's response.
4. Save the response to ./predictions_paste_here/ using the filename mapping below.
5. Verify the response is 100-200 words of continuous prose. If not, regenerate up to three times. If still failing, save what was produced and note the issue in ./run_log_during.md.
6. Move to the next prompt.

Filename mapping:
- prompts/PROMPT_W1_par7_Babel.txt → predictions_paste_here/W1_par7_Babel_gpt5_prediction.txt
- prompts/PROMPT_W2_par10_BabelSyndrome.txt → predictions_paste_here/W2_par10_BabelSyndrome_gpt5_prediction.txt
- prompts/PROMPT_W3_par100_GenuinelyHelpful.txt → predictions_paste_here/W3_par100_GenuinelyHelpful_gpt5_prediction.txt
- prompts/PROMPT_W4_par107_Alignment.txt → predictions_paste_here/W4_par107_Alignment_gpt5_prediction.txt
- prompts/PROMPT_C1_par7.txt → predictions_paste_here/C1_par7_gpt5_prediction.txt
- prompts/PROMPT_C2_par10.txt → predictions_paste_here/C2_par10_gpt5_prediction.txt
- prompts/PROMPT_C3_par100.txt → predictions_paste_here/C3_par100_gpt5_prediction.txt
- prompts/PROMPT_C4_par107.txt → predictions_paste_here/C4_par107_gpt5_prediction.txt

Constraints for each subagent:
- Subagents must NOT read files in ./targets_DO_NOT_READ_until_predictions_saved/
- Subagents must NOT read ./run_log_after_predictions.md
- Subagents must NOT read other subagents' outputs
- Subagents must NOT perform web search
- Subagents may read only the specific prompt file they are given

After all eight predictions are saved, exit. Do not read the targets or after-log; that is the human operator's step.
```

---

## Verification after Codex run completes

Open `run_log_during.md` and note: which model identifier was used, training cutoff, total subagents spawned, any regenerations or failures.

Confirm all eight `_gpt5_prediction.txt` files exist in `predictions_paste_here/` and contain plain text of approximately 100-200 words each.

Confirm Codex did not access the targets folder or the after-log during the run. Codex environments typically log file access; check the logs.

If everything checks out, proceed to the post-prediction debrief in `run_log_after_predictions.md`.

---

## Caveat about Codex priors

Codex is itself a frontier model and may have its own priors about how to behave when given a "stylometric test" framing. If Codex sees the README or other kit files that describe the project hypothesis, it may incidentally prime its subagents in ways that defeat the blinding. Two mitigations:

- Give Codex only the eight prompt files and the filename mapping. Do not feed Codex the README, the after-log, or the SPEC documents. Codex should see only what it needs to spawn subagents and route I/O.
- If Codex's task-orchestration layer reads any contextual files, place the kit's hypothesis-bearing documents (README, after-log) outside Codex's reachable filesystem during the run. Move them to a parent directory or a sibling folder Codex is not given access to.

If Codex unavoidably reads the README, the resulting run is still acceptable but should be labeled as "Codex-orchestrated, operator-aware" rather than "fully blind." Document the level of Codex contextual access in the run log.

---

*Codex workflow doc, kit v0.2. The serial-subagent capability solves the chat-isolation requirement without changing the methodology. Generalizes to any agent framework that can spawn isolated subagents serially (Claude Code subagents, Cline, future agentic interfaces).*
