# Filename-to-window key

> **STOP.** Do NOT open this file until all eight `_gpt5_prediction.txt` files are saved in `predictions_paste_here/`.
>
> This file maps the neutral window IDs (W1, W2, etc.) to the specific paragraphs in *Magnifica Humanitas* and *Dilexit Nos* and to the thematic features the test counts. Reading this file before generating predictions contaminates the blind run by revealing which diagnostic features the analyst will look for. v0.2 of the kit shipped with feature-revealing filenames; v0.3 fixed that by moving the mapping to this sealed file.

---

## Window mapping

| Neutral ID | Source document | Paragraph | Theme (analyst-side, sealed) |
|---|---|---|---|
| W1 | Magnifica Humanitas | par 7 | Babel passage; subheading "Two biblical images" |
| W2 | Magnifica Humanitas | par 10 | "Babel syndrome" coinage |
| W3 | Magnifica Humanitas | par 100 | "genuinely helpful" passage |
| W4 | Magnifica Humanitas | par 107 | "alignment" critique |
| C1 | Dilexit Nos | par 7 | Pope Francis's grandmother-pastry anecdote (autobiographical-particular control) |
| C2 | Dilexit Nos | par 10 | Loss of heart / liquid society |
| C3 | Dilexit Nos | par 100 | Hosea / God's heart |
| C4 | Dilexit Nos | par 107 | Bonaventure procession |

---

## Why this matters

The original v0.1 kit named files `PROMPT_W2_par10_BabelSyndrome.txt`, `PROMPT_W3_par100_GenuinelyHelpful.txt`, `PROMPT_W4_par107_Alignment.txt`. Two of those filenames named the EXACT lexical features the test counts (Babel syndrome, genuinely); the third named the technical pivot. A Codex subagent that sees its own filename effectively has the answer key for what features to produce, even though the prompt body does not name them.

The May 27 Codex run was conducted under v0.1 filenames, so the qualitative observations from that run ("GPT-5 did not spontaneously produce 'genuinely'," etc.) should be treated as contaminated. The cleanest aggregate TFIDF comparison may still be informative, but the spontaneity claim about specific features cannot be made from that run.

v0.3 of the kit (this version) uses neutral filenames so that future runs preserve the blinding standard at both the prompt-body and filename layers.

---

## What to do if you opened this file before predictions were saved

The blind run is no longer valid for this kit. Two options:

1. Use the `gpt5_adversarial_kit/` instead, which is hypothesis-aware by design.
2. Run a fresh blind run on a different set of windows you select randomly, generating new prefix files and a new sealed key.

Document the deviation in the run log.
