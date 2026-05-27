# GPT-5 elicitation kit: Magnifica Humanitas mirror discrimination run

**Version:** 0.3 (27 May 2026, neutral filenames + sealed mapping key after the v0.2 filenames were found to leak diagnostic features)
**For:** Blind cross-family replication of the mirror test in `evidence-pack-magnifica-humanitas.md`
**Methodology spec:** SETEC SPEC v0.1, Design 2, K=4 mirror discrimination

---

## Read this first: the blinding discipline this kit requires

This kit produces a meaningful result only if the operator generating GPT-5's predictions has NOT been primed on what features the test counts. If the operator knows the test is looking for, say, the phrase "Babel syndrome" or the word "genuinely," that operator's prompt construction and acceptance criteria will subtly steer GPT-5 toward those features even with the best intentions. The result then measures the operator's priming, not GPT-5's distribution.

To preserve blindness, the kit ships two run logs:

- **`run_log_during.md`**: open this file and use it during the prediction phase. It contains only word-count and regeneration tracking, no diagnostic features.
- **`run_log_after_predictions.md`**: do NOT open until all eight prediction files are saved. It contains the diagnostic features list (which specific words, phrases, and structures the analyst will look for in the predictions) and the post-prediction debrief.

If you have already read `run_log_after_predictions.md`, this kit is no longer suitable for the blind cross-family run. Use the sibling `gpt5_adversarial_kit/` folder instead, which is designed for explicitly-labeled hypothesis-aware testing.

---

## What this kit is for

A parallel Claude run of this same test was completed 26-27 May 2026 and is documented in the companion evidence pack. The Claude run found that Claude predicts Pope Leo XIV's *Magnifica Humanitas* substantially more accurately than it predicts *Dilexit Nos* (Francis 2024), at a +0.14 TFIDF-cosine gap on a K=4 aggregate. That run is consistent with substantial Claude-mediated drafting in the encyclical's English prose surfaces.

The Claude run was operator-aware (the operator constructing the sub-agent's prompt knew the project's hypothesis) but model-blind (the model itself received only the prefix and a minimal continuation instruction, with no diagnostic features list). This kit aims for the same operator-aware / model-blind standard.

What this kit does NOT yet establish: whether the encyclical's prose is *Claude-specifically* in-distribution or *frontier-LLM-in-general* in-distribution. That distinction matters for the attribution claim. The GPT-5 run is the cross-family disambiguation.

The operator will: paste eight prompts into ChatGPT (one prompt per fresh chat session), save the eight GPT-5 outputs back into this kit, then send the kit to an analyst (or run the metric script directly).

Total time required: about 90 minutes of active work for the prediction phase.

---

## What you need

**Model.** A GPT 5.x model with a training cutoff date earlier than the target document's publication date. The target is *Magnifica Humanitas*, published 25 May 2026. The control is *Dilexit Nos*, published 24 October 2024. For the test to be valid:

- The model's training cutoff MUST be before 25 May 2026 (otherwise the model may have memorized *Magnifica Humanitas* and the "prediction" is recall, not generation).
- Ideally the cutoff is also before 24 October 2024 so that *Dilexit Nos* is not in training data either. Most GPT 5.x models have cutoffs in 2024 or 2025; check the specific version you have access to. If *Dilexit Nos* is in training, the control over-estimates the model's predictive accuracy on verified-Francis prose; the Magnifica-vs-Dilexit discrimination gap then becomes a CONSERVATIVE estimate. The original Claude run had this same caveat noted.

Within those cutoff constraints, any GPT 5.x model is acceptable: 5.0, 5.1, 5.5, etc. The earlier kit drafts said "GPT-5 specifically"; that was unnecessarily narrow. What matters is the training cutoff, not the brand label.

Avoid reasoning-mode variants (GPT-5-Pro / GPT-5-Thinking / o-series). The test is calibrated for single-pass standard inference; reasoning-mode predictions answer a different question (a multi-step model is doing different things than a single-pass model). The Claude run used Claude Opus 4.7 in standard mode; match that on the GPT side.

**Interface.** Two options, both acceptable:

- **Manual chat workflow.** ChatGPT consumer subscription. Open eight separate chat sessions, paste one prompt per session, save the response. Estimated time: 90 minutes. Web search must be OFF.
- **GPT Codex serial-subagent workflow.** If you have Codex access, you can automate the eight predictions by having Codex run one subagent per prompt, serially. Each Codex subagent receives a fresh context per the prompt; serial execution (not parallel) is fine because the isolation that matters is per-prompt context isolation, not parallelism. Estimated time: 15-20 minutes. See `codex_workflow.md` (if shipped with this kit) or follow the protocol below adapted for Codex.

You do NOT need filesystem access, Python, or any setup beyond your chosen interface.

---

## How to run it

### Step 1: Open the kit

```
gpt5_elicitation_kit/
├── README.md                                  ← this file
├── run_log_during.md                          ← USE during prediction phase
├── run_log_after_predictions.md               ← SEALED until all 8 predictions saved
├── prompts/                                   ← 8 ready-to-paste prompt files (neutral IDs in v0.3)
│   ├── PROMPT_W1.txt
│   ├── PROMPT_W2.txt
│   ├── PROMPT_W3.txt
│   ├── PROMPT_W4.txt
│   ├── PROMPT_C1.txt
│   ├── PROMPT_C2.txt
│   ├── PROMPT_C3.txt
│   └── PROMPT_C4.txt
├── FILENAME_KEY_DO_NOT_OPEN_until_predictions_saved.md  ← sealed mapping
├── predictions_paste_here/                    ← save GPT-5 outputs here
├── targets_DO_NOT_READ_until_predictions_saved/   ← strict no-touch until predictions saved
└── metrics/
    └── compute_metrics.py                     ← analyst-side comparison
```

Each `PROMPT_*.txt` file contains a complete, copy-pasteable prompt for a single GPT-5 chat session. The encyclical prefix is already inlined inside the prompt; you do not need to assemble anything.

The filenames intentionally do not encode the diagnostic features. They identify only paragraph number and theme (Babel, alignment, etc.) which the operator could infer from the prefix itself anyway. The diagnostic features list (what specific words and structures the analyst will count) is sealed in `run_log_after_predictions.md`.

### Step 2: Disable web search in ChatGPT

Before the first prediction, in ChatGPT:

- Open Settings → Personalization → Tools (or equivalent path)
- Turn OFF web browsing / web search
- Verify the chat toolbar does not show a "search" toggle as active

If any GPT-5 response shows search-result citations, footnoted URLs, or "I searched for and found..." language, the prediction is contaminated; regenerate.

### Step 3: Generate the eight predictions (chat isolation, not subagent isolation)

What matters is fresh-chat isolation, not any special "subagent" infrastructure. For each of the eight `PROMPT_*.txt` files:

1. Start a NEW ChatGPT chat. Do not reuse the same chat between prompts.
2. Open one prompt file in your text editor.
3. Select all (Cmd-A / Ctrl-A) and copy.
4. Paste into ChatGPT.
5. Send.
6. Wait for GPT-5's response.
7. Verify the response is 100-200 words of continuous prose in formal papal register. If it is, copy it. If it is a refusal, meta-commentary, bulleted list, or has model-self-identification ("As an AI..."), regenerate (Step 6 in the chat). If three regenerations fail, document in `run_log_during.md` and move on.
8. Save the response as plain text to `predictions_paste_here/` with this exact filename pattern:
   - `PROMPT_W1.txt` → `W1_gpt5_prediction.txt`
   - `PROMPT_W2.txt` → `W2_gpt5_prediction.txt`
   - `PROMPT_W3.txt` → `W3_gpt5_prediction.txt`
   - `PROMPT_W4.txt` → `W4_gpt5_prediction.txt`
   - `PROMPT_C1.txt` → `C1_gpt5_prediction.txt`
   - `PROMPT_C2.txt` → `C2_gpt5_prediction.txt`
   - `PROMPT_C3.txt` → `C3_gpt5_prediction.txt`
   - `PROMPT_C4.txt` → `C4_gpt5_prediction.txt`
9. Open `run_log_during.md`, note word count and any regenerations for this window.
10. Start a new chat. Move to the next prompt. Repeat.

**Discipline checkpoint:** Do not, at any point during steps 1-10, open files in `targets_DO_NOT_READ_until_predictions_saved/` or `run_log_after_predictions.md`. The test is invalidated if you pre-view either.

Estimated time: 90 minutes for all eight (about 11 minutes per window including verification).

### Step 4: After all eight predictions are saved

You have now generated GPT-5's blind predictions. You may now:

- Open `run_log_after_predictions.md` and complete the diagnostic debrief
- Open the targets folder if you want to read what the actual encyclical says
- Send the entire kit folder (zipped if convenient) to the analyst for metric computation

The analyst will run `metrics/compute_metrics.py`, which produces a head-to-head comparison against the Claude-run baseline and an Outcome A/B/C/D diagnosis.

---

## Outcome framework (analyst-side)

Reproduced here for context; do not read closely if you have not yet generated predictions.

- **A:** GPT-5 K=4 TFIDF more than 0.05 below Claude's 0.5729; favors Claude-specific attribution.
- **B:** Both models within 0.05 of each other, both well above Dilexit control; frontier-LLM signature, single-family attribution weakened.
- **C:** GPT-5 K=4 TFIDF more than 0.05 above Claude's 0.5729; favors GPT-attribution.
- **D:** Both models below 0.40 TFIDF on Magnifica; original Claude signal may be a window-selection artifact; expand to K=8+.

---

## What this kit does NOT cover, and why a sibling kit exists

This kit specifies a **single-shot blind cross-family test**. It deliberately does not cover:

- **Hypothesis-aware adversarial testing.** A useful sister-test is: knowing what features the test counts, can a primed frontier model trivially reproduce the Claude-cued features from the prefix alone? If yes, the original Claude finding is partially undermined (the prefix forces the features regardless of authorship). If no, the original finding is strengthened. That test is run via the sibling folder `gpt5_adversarial_kit/`, which is explicitly labeled as hypothesis-aware and is the appropriate kit if you have already read this kit's run_log_after_predictions.md.
- **Multi-shot sampling.** A more rigorous version generates N=5 predictions per window and averages. Single-shot matches the Claude-run protocol; expand if cross-replication is noisy.
- **Random window selection.** The four Magnifica windows match Zhang's Pangram-flagged passages, which is biased toward likely-AI sections. A truly unbiased run would sample random windows across the 245 paragraphs. Future runs should add this cohort.
- **Gemini parallel.** A third-family run further sharpens attribution; a separate Gemini elicitation kit should be drafted before that run.

---

## Acknowledgment of methodological softness in the original Claude run

The Claude run that this kit aims to replicate was operator-aware (the sub-agent's prompt named the project, named Zhang's article, and framed the task as a stylometric test of "whether sections of *Magnifica Humanitas* are in-distribution for Claude's prose generation"). The Claude sub-agent itself received only the prefix and a minimal continuation instruction; the model was blind to the diagnostic features list. But operator-level priming is real, and could in principle have shaped the sub-agent's instructions in ways that influenced output quality. A future round should re-run the Claude side with the same blind-operator standard this GPT-5 kit aims for.

This acknowledgment is not a retraction of the Claude-run findings. It is a methodological refinement that future cross-family rounds should honor.

---

## Provenance and reporting

When the analyst completes the metrics:

1. Place `gpt5_results.json` and both run logs in the SETEC repository alongside the Claude-run artifacts.
2. Draft `evidence-pack-magnifica-humanitas-gpt5-supplement.md` with the head-to-head comparison.
3. Update the licensure tables in the original evidence pack (the single-family caveat resolves).
4. Decide whether the cross-family result warrants a follow-up Substack post.

---

*Kit version 0.2. Acknowledgments to the GPT-5.5 reviewer who flagged the run-log contamination issue in v0.1 and identified the hypothesis-aware sibling test as a separable methodological question. Joshua Miller, anotherpanacea@gmail.com.*
