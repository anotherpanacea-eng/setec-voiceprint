# `specs/` — capability specs & contracts

Committed specifications for capabilities being built into SETEC. Each spec is the
**contract a builder (local or the GPU box) implements against**: task surface, JSON
envelope, CLI, capabilities-manifest entry, test cases, calibration posture, and the
upstream license decision. Reviewers pin to the spec; the docs-freshness gate keeps
the shipped capability and its docs in sync afterward.

Why committed (not gitignored like `internal/SPEC_*.md`): the "kit" is meant to be
buildable by anyone, and the remote build loop needs a single, reviewable source of
truth for each contract.

## Index

| File | What |
|---|---|
| [`00-stylometry-kit-research-brief.md`](00-stylometry-kit-research-brief.md) | Frontier survey (RoBERTa / embeddings / EditLens / zero-shot / PAN / ESL / watermark); ranked shortlist; license-verification TODO. |
| [`01-stylometry-kit-build-plan.md`](01-stylometry-kit-build-plan.md) | The spec→build→review→merge loop, sequencing, cross-cutting constraints. |
| [`02-voice-fingerprint-embedding.md`](02-voice-fingerprint-embedding.md) | **Ready.** Same-author style-embedding verification surface (LUAR + Wegmann). |
| [`03-fast-detectgpt-curvature.md`](03-fast-detectgpt-curvature.md) | **Ready.** Conditional-probability curvature detector (Surface 5 add). |
| [`04-pan-obfuscation-replay-harness.md`](04-pan-obfuscation-replay-harness.md) | **Ready.** Replay SETEC signals against PAN obfuscation fixtures. |
| [`05-esl-fairness-slice.md`](05-esl-fairness-slice.md) | **Ready.** ESL/L2 + translated-text fairness slice in the validation harness. |
| [`06-voice-matching-companion.md`](06-voice-matching-companion.md) | **Draft.** The generative inverse — a companion project that writes in an authorized author's voice using SETEC as a held-out fitness function. Defines SETEC's side of the contract; built in a separate repo (TBD). |
| [`07-document-layout-audit.md`](07-document-layout-audit.md) | **Built** (this round). Non-voice document structure / layout profile on a new `document_layout` surface — descriptive only, refuses voice/AI inference. |
| [`08-reference-ecology-audit.md`](08-reference-ecology-audit.md) | **Built** (this round). Non-voice reference-ecology profile (citation/quote/attribution/link-domain breadth) on a new `reference_ecology` surface — descriptive, refuses voice/AI, flags topic-leakage. (07 = document-layout, in PR #148.) |
| [`09-formulaicity-audit.md`](09-formulaicity-audit.md) | **Built** (this round). Non-voice phraseological-texture profile (stock-phrase density) on a new `formulaicity` surface — descriptive, explicitly NOT an AI signal or quality judgment. (07 = doc-layout #148, 08 = reference-ecology #149.) |
| [`10-productive-roughness-audit.md`](10-productive-roughness-audit.md) | **Spec.** Strictly baseline-relative roughness profile (fragments, sentence-initial conjunctions, contractions). spaCy; box build. |
| [`11-dialogue-voice-audit.md`](11-dialogue-voice-audit.md) | **Spec.** Per-character dialogue-voice profiling + cross-character divergence. spaCy; `voice_coherence`. |
| [`12-narratorial-distance-audit.md`](12-narratorial-distance-audit.md) | **Spec.** Narratorial-distance / free-indirect-discourse profile + trajectory. spaCy. |
| [`13-editlens-edit-magnitude.md`](13-editlens-edit-magnitude.md) | **Spec (research).** Clean-room edit-magnitude regressor; same-corpus calibrated estimate, never absolute "% AI." torch + corpus. |
| [`14-intrinsic-dimension-phd.md`](14-intrinsic-dimension-phd.md) | **Spec (research).** PHD intrinsic-dimension discrimination signal — orthogonal topological axis. embeddings + TDA. |
| [`15-raidar-rewriting-invariance.md`](15-raidar-rewriting-invariance.md) | **Spec (research).** Rewriting-invariance discrimination signal; reuses the LLM-judge plumbing. |
| [`16-explain-mode.md`](16-explain-mode.md) | **Spec (stdlib, buildable in-sandbox).** Plain-language renderer over a single envelope; invents nothing. |
| [`17-sound-texture-audit.md`](17-sound-texture-audit.md) | **Built** (capability-whitespace group W2). Descriptive sound-texture profile (alliteration/assonance/consonance + consonant-class) via an orthographic-onset proxy; new `sound_texture` surface; non-verdict. stdlib. |
| [`18-triage-agreement.md`](18-triage-agreement.md) | **Built** (group W3). Framework-vs-human triage agreement (confusion, percent agreement, Cohen's κ, PABAK, bootstrap CI) on `validation`; measures concordance, not ground truth. stdlib. |
| [`19-crosslingual-voice-distance.md`](19-crosslingual-voice-distance.md) | **Built** (group W5). Language-agnostic, parser-free voice distance (char n-grams, punctuation, length distributions, script stats) on `voice_coherence`; language-agnostic not language-aware. stdlib. |
| [`20-conformal-abstention-gate.md`](20-conformal-abstention-gate.md) | **Built** (group W7). Split-conformal p-value + prediction set over operator calibration scores on `validation`; distribution-free finite-sample abstention. stdlib. |
| [`21-attribution-refusal-lab.md`](21-attribution-refusal-lab.md) | **Spec (research; build-gated).** Refusal-curve lab on the `validation` surface for the no-verdict-about-the-person cluster (open-set attribution / demographic profiling / identity linkage). Dispatchable contract: harness entrypoint, lab-manifest schema, aggregate-only output, claim license, + three go/no-go gates (consent / redaction / foil-strength) and a testable E3 non-leak rule. Build deferred behind the strong-foil decision; reports strength-of-evidence only, never an identity. |
| `_TEMPLATE.md` | Copy this to start a new spec. |

Research-grade specs (EditLens-style edit-magnitude regressor, intrinsic-dimension/
PHD, Raidar) are listed in the build plan and written once priority + license/corpus
gating is confirmed.

## Lifecycle of a spec

1. **Draft** — fill `_TEMPLATE.md`; resolve the license gate from the research brief.
2. **Ready** — contract section complete enough to dispatch; listed "ready" in the plan.
3. **In build** — a feature branch implements against the contract.
4. **Shipped** — merged; `capabilities.yaml` entry added; readiness matrix regenerates;
   CHANGELOG line written; spec's "status" updated to point at the shipped script.
5. **Calibrated** (optional) — labeled-corpus calibration + PROVENANCE entry promotes
   the capability's manifest `status` from `heuristic`/`empirically_oriented`.
