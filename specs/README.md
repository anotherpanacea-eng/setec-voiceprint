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
