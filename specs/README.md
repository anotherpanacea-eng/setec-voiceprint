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

> **Reconciled 2026-07-02** against `capabilities.d/` + `fleet_inventory.py` (the docs had drifted — the shortlist build loop shipped faster than this index updated). Statuses below are oracle-verified. Specs **22–35** were not indexed here; see [§ Specs 22–35](#specs-2235--arxiv-shortlist-wave). Tier annotations mark cloud-buildability: **Tier 1/2 (stdlib/spaCy) → cloud** (CI installs spaCy + runs the suite); **torch/embeddings/surprisal/judge → local box** (`skipif` in CI).

| File | What |
|---|---|
| [`00-stylometry-kit-research-brief.md`](00-stylometry-kit-research-brief.md) | Frontier survey (RoBERTa / embeddings / EditLens / zero-shot / PAN / ESL / watermark); ranked shortlist; license-verification TODO. |
| [`01-stylometry-kit-build-plan.md`](01-stylometry-kit-build-plan.md) | The spec→build→review→merge loop, sequencing, cross-cutting constraints. |
| [`02-voice-fingerprint-embedding.md`](02-voice-fingerprint-embedding.md) | **Shipped** (`voice_fingerprint`, surface `authorship_embedding`). Same-author style-embedding verification (LUAR + Wegmann). torch/embeddings → local tier. |
| [`03-fast-detectgpt-curvature.md`](03-fast-detectgpt-curvature.md) | **Ready.** Conditional-probability curvature detector (Surface 5 add). |
| [`04-pan-obfuscation-replay-harness.md`](04-pan-obfuscation-replay-harness.md) | **Ready.** Replay SETEC signals against PAN obfuscation fixtures. |
| [`05-esl-fairness-slice.md`](05-esl-fairness-slice.md) | **Ready** *(unverified — no standalone capability; likely folded into `validation_harness`)*. ESL/L2 + translated-text fairness slice in the validation harness. |
| [`06-voice-matching-companion.md`](06-voice-matching-companion.md) | **Draft.** The generative inverse — a companion project that writes in an authorized author's voice using SETEC as a held-out fitness function. Defines SETEC's side of the contract; built in a separate repo (TBD). |
| [`07-document-layout-audit.md`](07-document-layout-audit.md) | **Built** (this round). Non-voice document structure / layout profile on a new `document_layout` surface — descriptive only, refuses voice/AI inference. |
| [`08-reference-ecology-audit.md`](08-reference-ecology-audit.md) | **Built** (this round). Non-voice reference-ecology profile (citation/quote/attribution/link-domain breadth) on a new `reference_ecology` surface — descriptive, refuses voice/AI, flags topic-leakage. (07 = document-layout, in PR #148.) |
| [`09-formulaicity-audit.md`](09-formulaicity-audit.md) | **Built** (this round). Non-voice phraseological-texture profile (stock-phrase density) on a new `formulaicity` surface — descriptive, explicitly NOT an AI signal or quality judgment. (07 = doc-layout #148, 08 = reference-ecology #149.) |
| [`10-productive-roughness-audit.md`](10-productive-roughness-audit.md) | **Shipped** (`productive_roughness_audit`). Strictly baseline-relative roughness profile (fragments, sentence-initial conjunctions, contractions). spaCy (Tier 2 → cloud). |
| [`11-dialogue-voice-audit.md`](11-dialogue-voice-audit.md) | **Shipped** (`dialogue_voice_audit`). Per-character dialogue-voice profiling + cross-character divergence. spaCy (Tier 2 → cloud). |
| [`12-narratorial-distance-audit.md`](12-narratorial-distance-audit.md) | **Shipped** (`narratorial_distance_audit`). Narratorial-distance / free-indirect-discourse profile + trajectory. spaCy (Tier 2 → cloud). |
| [`13-editlens-edit-magnitude.md`](13-editlens-edit-magnitude.md) | **Shipped** (`edit_magnitude_audit`). Clean-room edit-magnitude regressor; same-corpus calibrated estimate, never absolute "% AI." torch + corpus → local tier. |
| [`14-intrinsic-dimension-phd.md`](14-intrinsic-dimension-phd.md) | **Shipped** (`intrinsic_dimension_audit`). PHD intrinsic-dimension discrimination signal — orthogonal topological axis. embeddings + TDA → local tier. |
| [`15-raidar-rewriting-invariance.md`](15-raidar-rewriting-invariance.md) | **Shipped** (`rewriting_invariance_audit`). Rewriting-invariance discrimination signal; reuses the LLM-judge plumbing (judge/API → local). |
| [`16-explain-mode.md`](16-explain-mode.md) | **Spec — OPEN** (stdlib, ✅ cloud-buildable; no branch yet). Plain-language renderer over a single envelope; invents nothing. |
| [`17-sound-texture-audit.md`](17-sound-texture-audit.md) | **Built** (capability-whitespace group W2). Descriptive sound-texture profile (alliteration/assonance/consonance + consonant-class) via an orthographic-onset proxy; new `sound_texture` surface; non-verdict. stdlib. |
| [`18-triage-agreement.md`](18-triage-agreement.md) | **Built** (group W3). Framework-vs-human triage agreement (confusion, percent agreement, Cohen's κ, PABAK, bootstrap CI) on `validation`; measures concordance, not ground truth. stdlib. |
| [`19-crosslingual-voice-distance.md`](19-crosslingual-voice-distance.md) | **Built** (group W5). Language-agnostic, parser-free voice distance (char n-grams, punctuation, length distributions, script stats) on `voice_coherence`; language-agnostic not language-aware. stdlib. |
| [`20-conformal-abstention-gate.md`](20-conformal-abstention-gate.md) | **Built** (group W7). Split-conformal p-value + prediction set over operator calibration scores on `validation`; distribution-free finite-sample abstention. stdlib. |
| [`21-attribution-refusal-lab.md`](21-attribution-refusal-lab.md) | **Spec (research; build-gated).** Refusal-curve lab on the `validation` surface for the no-verdict-about-the-person cluster (open-set attribution / demographic profiling / identity linkage). Dispatchable contract: harness entrypoint, lab-manifest schema, aggregate-only output, claim license, + three go/no-go gates (consent / redaction / foil-strength) and a testable E3 non-leak rule. Build deferred behind the strong-foil decision; reports strength-of-evidence only, never an identity. |
| `_TEMPLATE.md` | Copy this to start a new spec. |

The research-grade specs once listed here as future work — EditLens edit-magnitude (13),
intrinsic-dimension/PHD (14), Raidar rewriting-invariance (15) — have all **shipped** (see
the flipped statuses above); their model/corpus tiers run on the local box.

## Specs 22–35 — arXiv-shortlist wave

Built by the autonomous shortlist loop and **not** in the index above. Status verified
2026-07-02 against `capabilities.d/` + `fleet_inventory.py`. Spec numbers repeat (a number
is a wave group, not a unique id).

**Shipped:** 22 set-level-diversity (`set_level_diversity`) · 23 rank-turbulence-delta
(`rank_turbulence_audit`) · 24 dependency-distance (`dependency_distance_audit`) ·
26 fallacy-warrant-scan (`fallacy_scan`) · 27 embedding-explanation (`cosine_explanation`) ·
28 cross-doc-originality (`cross_doc_novelty_profile`) · 29 watermark-probe (`watermark_probe`;
cut recommended) · 30 gaqcorpus-argquality (`argquality_dimension_profile`) · 30 gram2vec ·
30 homogeneity-audit (`homogeneity_audit`) · 30 specdetect-lastde (`specdetect_audit`) ·
31 dependency-distance-distribution (M1, folded into `dependency_distance_audit`) ·
31 tocsin-token-cohesiveness (`tocsin_audit`) · 32 deepa2-enthymeme (`enthymeme_gapflag`) ·
32 function-word-adjacency (`function_word_adjacency_audit`) · 32 gec-linguistic-error
(`gecscore_audit`) · 32 lambdag (`lambdag_audit`) · 32 rank-space-detectllm (`rank_space_audit`) ·
32 structural-shuffle-perplexity (`structural_shuffle_audit`) · 33 distinct-diversity
(`distinct_diversity_audit`) · 33 paraphrase-robustness (`paraphrase_robustness`) ·
34 model-family-attribution (`model_family_attribution`).

**Open** (feature branch, unbuilt) — cloud verdict per the tier boundary above:

| Spec | Branch | Cloud verdict |
|---|---|---|
| 16 raid-dipper-robustness | `feat/raid-dipper-robustness` | ⚠️ M1 cloud (stdlib obfuscation replay); RAID corpus + DIPPER model leg → local |
| 25 tdetect-tail-normalization | `spec/25-tdetect-tail-normalization` | ✅ M1 cloud (stdlib/scipy over the backend's existing output) |
| 28 eval-discipline-bundle | `feat/eval-discipline-bundle` | ✅ cloud (pure stdlib harness math) |
| 28 styledistance-encoder-upgrade | `feat/styledistance-encoder-upgrade` | ❌ local (swaps in a trained embedding encoder) |
| 31 llm-verifier-authorship | `feat/llm-verifier-authorship` | ⚠️ M1 mock cloud / real tier `api_llm` |
| 32 diveye-surprisal-diversity | `feat/detect-diveye` | ✅ M1 cloud (stdlib over injected surprisal series); M2 → local |
| 35 host-delegated-judge | *(M1 in-flight)* | ✅ M1 cloud (judge-provider seam + stub); real transports runtime-side |

Also open from the 00–21 range: **16 explain-mode** (✅ cloud, stdlib; no branch) and
**21 attribution-refusal-lab** (✅ M1 cloud, Tier 2, but build-gated on the strong-foil decision).

## Lifecycle of a spec

1. **Draft** — fill `_TEMPLATE.md`; resolve the license gate from the research brief.
2. **Ready** — contract section complete enough to dispatch; listed "ready" in the plan.
3. **In build** — a feature branch implements against the contract.
4. **Shipped** — merged; `capabilities.yaml` entry added; readiness matrix regenerates;
   CHANGELOG line written; spec's "status" updated to point at the shipped script.
5. **Calibrated** (optional) — labeled-corpus calibration + PROVENANCE entry promotes
   the capability's manifest `status` from `heuristic`/`empirically_oriented`.
