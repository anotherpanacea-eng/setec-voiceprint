# 06-voice-matching-companion

> The **generative inverse** of SETEC: a companion project that makes an LLM
> write in a *specific, authorized* author's voice (your own; or a rights-holder/
> estate continuing a series from a plot plan), using SETEC's diagnostic stack as
> a **held-out fitness function**. SETEC measures voice; this *targets* that
> measurement — carefully, because the measurement and the optimizer must never
> be the same thing.

- **Status:** Draft (design + SETEC-side contract). Not buildable from this repo — see "Placement & prerequisite."
- **Tier:** research-grade, multi-session, GPU + corpus dependent.
- **Decisions locked (2026-06-06):** placement = **separate companion repo**; v1 scope = **full pipeline incl. plot-plan outliner**; consent/provenance gating = **deferred to v2** (with v1 guardrails-by-default, below).
- **Upstream / prior art** (the voice-matching research brief is not committed as a separate file; cite primary sources directly): "Catch Me If You Can? Not Yet" (EMNLP 2025, [2509.14543](https://arxiv.org/abs/2509.14543)); StyleAdaptedLM ([2507.18294](https://arxiv.org/abs/2507.18294)); DOC long-form generation ([2212.10077](https://arxiv.org/abs/2212.10077)); LUAR ([EMNLP 2021](https://aclanthology.org/2021.emnlp-main.70/), Apache-2.0); Wegmann content-controlled style embeddings ([RepL4NLP 2022](https://aclanthology.org/2022.repl4nlp-1.26/)).

## The honest ceiling (design premise)

LLMs can *approximate* an author's voice but cannot yet fully reproduce an
individual's stylometric signature (EMNLP 2025, 40k+ generations / 400+ authors).
So the target is **assisted approximation with a human in the loop**, not an
autonomous forgery indistinguishable to SETEC's own verifiers. That gap is the
guardrail (good imitation is hard) and the research question (how close can the
closed loop get without gaming the metric?).

## Placement & prerequisite

A **separate companion repo** that depends on SETEC as a library — keeps the
forensic tool's no-verdict brand and privacy ratchet uncompromised, and makes the
matcher the first real external consumer of SETEC's normalized-entrypoint contract
(`references/setec-normalized-entrypoint-spec.md` — defined in #139, **not yet
merged**; see the Contract's pre-build note) + the APODICTIC handoff
(`capabilities` v0.3 `handoff: stable`). **Prerequisite to *building*:** the repo
must be created and added to the agent's session scope (currently restricted to
`anotherpanacea-eng/setec-voiceprint`). This spec lives here in the interim because
it defines SETEC's side of the contract; it moves to the companion repo once that
exists.

## Architecture (the full v1 pipeline)

```
plot plan ─▶ outliner (DOC/Re3-style) ─▶ [voice generator: QLoRA model + corpus RAG] ─▶ N candidates
   │  (plot-fidelity job, kept SEPARATE                                                   │
   │   from the voice job — the estate case)                                              ▼
   │                       SETEC FITNESS — held-out, used to SELECT not to train:    ranker ─▶ best
   │                       • style-embedding cosine (LUAR + content-independent Wegmann)    │
   │                       • Burrows Delta / function-word / POS-ngram distance            ▼
   │                       • idiolect-preservation checklist                     self-critique revise
   │                       • surprisal / Binoculars band (human-like, not flat)            │
   └───────────────────────────────────────────────────────────────────────────▶ human review ─▶ output
```

Components (v1 sub-milestones, buildable in order):

1. **Voice generator.** QLoRA fine-tune on the author's corpus (Apache/MIT base —
   Mistral/Qwen/OLMo — for GPL-compatible licensing; LUAR weights are Apache-2.0).
   ~500–2k samples, 24 GB GPU, <1 hr. Corpus RAG over the author's own passages
   stabilizes idiolect and grounds content. **v1a** can ship with RAG-only
   generation (no fine-tune) to exercise the loop before the LoRA lands.
2. **Plot-plan outliner.** DOC/Re3-style hierarchical controller turning the plot
   plan → chapter outline → drafts. **Kept architecturally separate from the voice
   model** — plot fidelity and voice fidelity are different jobs; the estate
   supplies the plan, the voice model supplies the prose.
3. **SETEC fitness ranker.** Best-of-N selection over candidates (see Contract).
4. **Self-critique revise.** Targeted revision on idiolect/voice misses (prose
   instructions, not metric numbers — per SETEC's `metric-targeted-restoration`).
5. **Human-in-the-loop gate.** Chapter-by-chapter acceptance; novel-length voice
   consistency is unsolved, so the human is load-bearing, not optional.

## Contract: how it consumes SETEC

- **Entry point.** *Pre-build prerequisite — not yet on `main`.* The normalized
  `setec run <surface> --json` dispatcher and its spec
  (`references/setec-normalized-entrypoint-spec.md`) are defined in **#139, still
  open**; neither the dispatcher nor that reference file exists on `main` yet.
  Until #139 lands, the companion consumes each surface's **existing per-script
  CLI directly** — e.g. `python3 plugins/setec-voiceprint/scripts/voice_distance.py
  … --json` — which already emits the `build_output()` envelope; the dispatcher is
  a convenience layer over those same envelopes, not a new contract. Pin only
  `handoff: stable` surfaces (`variance_audit`, `voice_distance`,
  `idiolect_detector`, `aic_pattern_audit`, `restoration_packet`) + the planned
  **voice-fingerprint embedding surface (`specs/02`)**, the matcher's lead
  evaluator, which should ship first.
- **Selection signals** (the ranker reads these): LUAR + Wegmann cosine
  (`specs/02`), `voice_distance` Burrows-Delta / function-word / POS-ngram,
  `idiolect_detector` preservation survival.
- **HELD-OUT validators** (never exposed during selection — the circularity
  check): a *second* SETEC config including `mimicry_cosplay_audit` (catches
  over-conspicuous imitation), `general_imposters`, and the surprisal/Binoculars
  band. Final acceptance runs these; if a candidate scores well on selection but
  trips the held-out validators, it's cosplay, not voice.

## Goodhart guardrails (non-negotiable, ported from SETEC's posture)

1. **Select, don't train, on the metric.** SETEC is a best-of-N **ranker /
   acceptance test**, never a dense RL reward. RL directly on stylometric scores
   produces reward-hacked, voice-empty prose.
2. **Ensemble + disentangle.** Pair topic-leaky LUAR with content-independent
   Wegmann/StyleDistance so the loop can't "win" by matching the author's
   *subjects* instead of their voice.
3. **Hold out the evaluator.** The acceptance config (incl. `mimicry_cosplay_audit`)
   is disjoint from the selection config.
4. **Respect the targetability taxonomy.** Treat aggregate KL / Burrows Delta /
   char-n-gram / AUC as *avoid-direct* evidence — never optimization targets
   (mirrors `references/metric-targeted-restoration.md`).
5. **Human gate.** No autonomous book generation; chapter-level human acceptance.

## v1 guardrails-by-default (formal gating deferred to v2)

Gating machinery (signed authorization records, C2PA/SynthID provenance) is v2.
But "deferred" ≠ "absent" — v1 ships three cheap defaults so the riskiest failure
(untagged voice-clone output leaking) can't happen by accident:

- **Private by default.** v1 outputs inherit SETEC's privacy posture — written
  under a private dir, not for distribution; the README states v1 is for
  authorized self/estate use only.
- **Provenance stamp.** Every artifact carries a metadata tag
  (`ai_generated: true`, `voice_matched_to: <persona>`, `authorized_by:
  <operator>`, `tool/version`) even before the full C2PA record in v2.
- **Operator attestation, logged.** A one-line run-start attestation that the
  operator is the rights-holder (or authorized by the estate), recorded with the
  run. v2 upgrades this to a verifiable signed record + refuse-by-default.

## Footprint / license

QLoRA on a 7–8B Apache/MIT base fits ~24 GB VRAM, <1 hr for a few-thousand-sample
adapter; tooling (peft / TRL / Unsloth / Axolotl) is permissive. **Verify before
vendoring:** the Wegmann `Style-Embedding` weight-card license tag (carried over
from `specs/00` TODO); base-model license (prefer Apache/MIT over Llama/Gemma).

## Open questions

- **Companion repo name + creation** (prerequisite to building). Add to agent scope
  once created.
- **v1a RAG-only vs. straight-to-LoRA** as the first runnable milestone (RAG-only
  exercises the loop without GPU; recommended first step despite "full pipeline" v1).
- **Where the spec lives long-term** — moves to the companion repo on creation.
- Whether the held-out validator set should include a *fresh* GI impostor pool per
  run (independence) — ties into the fiction impostor-pool track.
