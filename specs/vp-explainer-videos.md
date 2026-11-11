# VP-VID-1: offline explainer videos for the Voiceprint app (planning spec)

> Short silent films, captioned, that show what each Voiceprint tool measures, why it matters, and what it can't tell you. They ship with the offline app, linked to the handbook.

- **Status:** Draft, planning only. Fleet card VP-VID-1.
- **Authority:**
  - **Owner, Nov 6:** the offline app should include videos explaining what the tools do and why they matter.
  - **The card's gate:** planning only. No production, paid generation, purchases, publication or unapproved source sharing.
- **Linked specs:** VP-APP-1 (offline app) and VP-HBK-1 (handbook), neither written yet, plus CB-13 (hermetic mode). This spec decides only what is the videos'. Bundling and the alpha minimum are settled jointly with the app and handbook specs (below).

## What exists today (inventory)

No Voiceprint explainer video exists. Nothing below is a planned video already made.

| Asset | Where | What it is | Reuse |
|---|---|---|---|
| **Glass-Box Stylometry explainer, BM-10** | Fleet Dropbox `blog-media/bm10-glass-box-2026-10-31/`; Motion films on the OCA seat | Two 90 s silent takes, built 2026-10-31 from the published blog sequence: distribution, craft patterns, voice drift, mirror test. SRTs and a claims ledger. The owner's pick and MP4 export are still owed. | Candidate for the orientation video (V0). Re-cut for the app: blog-specific cases come out, and handbook links go in. |
| **Evidence-pack pages** | `docs/evidence-packs/magnifica-humanitas.html`, `docs/evidence-packs/nazir-serpent-in-the-grove.html` | Public case write-ups with rerunnable steps | Possible worked-example sources. Their use in videos is an owner call. |
| **Capability cards** | `plugins/setec-voiceprint/capabilities.d/*.yaml` (147 entries) | `purpose`, `use_when`, `do_not_use_when`, the claim license | The source of each video's "what it can't tell you" |
| **Signals glossary, scripts catalog** | `plugins/setec-voiceprint/references/signals-glossary.md`, `plugins/setec-voiceprint/scripts/README.md` | Prose definitions | The handbook's starting points. Videos link to handbook entries, not to these files. |

## Learning goals

After a video, a viewer can say:
1. what question the tool answers, in one sentence;
2. what it needs as input, including a baseline where the tool needs one;
3. how to read its output;
4. what it does **not** license. No tool returns an AI or authorship verdict. Bands are provisional. Known failure modes include topic leakage, memorization, short windows and register mismatch.

Goal 4 isn't optional. Every video ends on the tool's limits, taken from its capability card's `do_not_use_when` and claim license.

## Proposed alpha set

Four videos, each 60–90 s, silent, with burned-in captions. The minimum candidates on the cards are LuAR, LAPD and surprisal.

| Video | Tools (capability ids) | Question shown | Limits it must show |
|---|---|---|---|
| **V0 Orientation: what a voiceprint measures** | none in particular | Measurements a reader can check, rather than a black-box score. The kinds of evidence. | A flag starts an investigation, never finishes it. There is no verdict. |
| **V1 Style embeddings (LuAR)** | `voice_fingerprint` (surface `authorship_embedding`, default encoder LUAR), `cosine_explanation`, `embedding_attribution` | Does this read like the same writer as the baseline? Then, where do named features agree or diverge (`cosine_explanation`)? | Cosines from different encoders aren't comparable. Provisional, with no threshold. No identity verdict. Topic and register shift the cosine. On real text, `embedding_attribution` abstains today: it has no production attribution fit, so contributions are null and the band is indeterminate. The video shows that abstention and gives no per-feature contribution numbers, unless one is labelled a non-production illustration. |
| **V2 Surprisal** | `surprisal_audit` (family `surprisal`; default TinyLlama), with `variance_audit` as context | How predictable is the text to a language model, token by token, and how even is that? | Needs a user baseline. Bands are illustrative. Text in the model's training data reads as low-surprise. No AI-provenance verdict. |
| **V3 LAPD** | not yet a registered capability | (waits) | V3 waits until VP-APP-1 reconciles LAPD with a registered consumer surface. No video describes a tool the app doesn't ship. |

## Later lessons (the full arsenal, by theme)

The 147 capability entries group into nine teaching themes. A later lesson is one video per theme, not one per tool. The ids in brackets are examples, not exhaustive lists.
1. **Distribution and variance:** `variance_audit`, `sliding_window_heatmap`, `punctuation_cadence_audit`, `paragraph_audit`, `dependency_distance_audit`.
2. **Voice distance and function words:** `voice_distance`, `voice_profile`, `function_word_grammar_audit`, `function_word_adjacency_audit`, `general_imposters`, `lambdag_audit`, `rank_turbulence_audit`, `controls_audit`.
3. **Model-probability signals beyond surprisal:** `binoculars_audit`, `fast_detect_curvature`, `rank_space_audit`, `structural_shuffle_audit`, `tocsin_audit`, `intrinsic_dimension_audit`.
4. **Craft patterns and phraseology:** `formulaicity_audit`, `phrase_overuse_profile`, `aic_pattern_audit`, `kicker_density`, `prestige_metaphor`, `construction_signature_audit`, `phraseological_signature_audit`.
5. **Mirror and rewrite tests:** `rewriting_invariance_audit`, `external_mirror_workflow`, `paraphrase_ladder`, `paraphrase_robustness`, `edit_magnitude_audit`, `mimicry_cosplay_audit`.
6. **Set-level comparison:** `homogeneity_audit`, `corpus_novelty_audit`, `verbatim_mosaic_audit`, `distinct_diversity_audit`.
7. **Narrative and argument structure:** `narrative_decision_audit`, `dialogue_voice_audit`, `narratorial_distance_audit`, `pov_voice_profile`, `argument_decision_audit`, `argmove_profile`.
8. **Why there is no verdict:** `validation_harness`, `conformal_gate`, `triage_agreement`, `fairness_dialect_guardrails` and the claim license itself.
9. **Setup, acquisition and corpus hygiene:** the handbook covers these (`dependency_check`, `baseline_discovery`, `acquire_*`, `check_corpus`). A video only if the app's first-run workflow (VP-APP-1) needs one.

## Examples and sources

- **Public domain or synthetic only.** Use Project Gutenberg texts, or text written for the video and labelled as written for it. Never the owner's private writing, and never a third party's private text.
- **Drawn, not data.** Every number on screen comes from a real run on the shown example, or is labelled an illustration. Each video keeps a claims ledger: time, claim, source. This is the BM-10 practice.
- The published evidence packs, and the blog sequence BM-10 draws on, are public. Using them is owner call 2.

## Format, accessibility and offline playback

- **Format:** MP4 (H.264), 1920×1080, silent by default, with captions burned in, plus an SRT and a plain-text transcript per video. One still per scene, so a viewer who can't play video can still follow it.
- **Accessibility:**
  - Captions on every spoken-equivalent line, with text contrast of at least 4.5:1.
  - No flashing above 3 per second.
  - Each video's transcript is linked from its handbook entry.
  - The app's player respects reduced motion by offering the stills and transcript.
- **Offline:** the app plays local files and fetches nothing at runtime.

## Asset, version and storage choices (proposed)

- **Per-video record:**
  - id
  - capability ids
  - the setec-voiceprint version it was checked against
  - the claims ledger
  - MP4, SRT and transcript sha256
- **Storage:** proposed is that the alpha set (V0–V2) is bundled with the app and later lessons are optional downloads.
  - At 60–90 s of mostly flat graphics, an alpha MP4 should be a few MB.
  - The real sizes get measured when the first export exists, and that figure goes to VP-APP-1's packaging comparison.
- **When a linked capability card changes in a release,** re-read that video's claims ledger against the new card before the release ships. A stale limit on screen is a wrong claim with nothing to catch it, and this re-read is the only check.

## Production, licensing and distribution: decisions for the owner

| # | Decision | Proposed |
|---|---|---|
| 1 | Production tool | Claude Motion on the OCA seat: zero credits, MP4 by the film page's Export, the way BM-10 was made. No paid generation. |
| 2 | Example sources | Gutenberg and synthetic text, plus the public evidence packs and blog sequence if approved |
| 3 | Bundled vs downloaded | Alpha bundled, later lessons downloaded, settled jointly with VP-APP-1 |
| 4 | Licensing of the videos | The repo's docs license (`LICENSE-docs`). Fonts are rendered into the picture, so no font files ship. No music. |
| 5 | Distribution beyond the app | None in this spec. A public post needs its own approval. |
| 6 | V0 source | Re-cut BM-10's picked take for the app, or make a new film |

## Not in scope

- Producing any video.
- The app's player, UI or packaging (VP-APP-1).
- Handbook text (VP-HBK-1).
- LAPD's video before LAPD is a registered capability.
- Narration or voiceover.
