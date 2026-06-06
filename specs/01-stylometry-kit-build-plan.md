# Stylometry Kit — build plan & the spec→build→merge loop

Turns the [research brief](00-stylometry-kit-research-brief.md) into an ordered,
dispatchable program. Each capability becomes a **spec + contract** (this dir) that
the GPU box implements against, gets reviewed, and merges — in a loop.

## The loop

```
spec+contract (here, cheap, no GPU)
      │
      ▼
GPU box implements against the contract on a feature branch
      │
      ▼
PR → CI (pytest + check_capabilities_drift + gen_calibration_readiness --check
         + check_docs_freshness) → /code-review + /security-review
      │
      ▼
address review → merge → docs regenerate (capabilities → readiness matrix;
                                           CHANGELOG; ROADMAP reconciliation)
      │
      ▼
calibrate on a labeled corpus → PROVENANCE entry → status: heuristic → calibrated
```

This is the same loop that shipped the calibration-readiness kit (PR #144); it is
proven, not aspirational.

### What "contract" means

Each spec's **Contract** section is the testable interface the implementation must
satisfy. It reuses existing repo conventions so a remote builder has zero ambiguity:

- **task_surface** — one value from `output_schema.py::VALID_TASK_SURFACES` (add a
  new one there if the capability opens a new surface).
- **JSON envelope** — built via `output_schema.build_output()`, carrying a
  `ClaimLicense` block (`claim_license.py`) that states what the result licenses and
  refuses. New surfaces register a label in `claim_license.TASK_SURFACE_LABELS`.
- **CLI surface** — flags, `--json`, `--out`, matching sibling audits.
- **capabilities.yaml entry** — so it auto-appears in `/setec` and the readiness
  matrix. New entry ⇒ the drift linter and readiness `--check` both gate it.
- **Test contract** — the spec names the test cases + invariants; the builder makes
  them pass. Lives in `plugins/setec-voiceprint/scripts/tests/`.
- **Calibration posture** — ships PROVISIONAL/uncalibrated by default
  ("Stylometry to the people"); a PROVENANCE entry promotes `status` later.
- **License gate** — the spec records the upstream license decision (wrap weights vs
  clean-room) and any pre-build verification TODO from the brief.

## Sequencing

The order respects the brief's tiers and the GPU/no-GPU split. Near-term items are
laptop-buildable and license-clean; research items need the GPU box and/or a corpus.

| Order | Spec | Tier | GPU? | Spec status | Gating |
|---|---|---|---|---|---|
| 1 | [`02` voice-fingerprint embedding](02-voice-fingerprint-embedding.md) | near-term | inference optional | **ready** | confirm Wegmann weight license |
| 2 | [`03` Fast-DetectGPT curvature](03-fast-detectgpt-curvature.md) | near-term | optional | **ready** | none (MIT) |
| 3 | [`04` PAN obfuscation replay harness](04-pan-obfuscation-replay-harness.md) | near-term | no | **ready** | confirm PAN fixture redistribution terms |
| 4 | [`05` ESL/L2 fairness slice](05-esl-fairness-slice.md) | near-term | no | **ready** | source L2/translated fixtures |
| 5 | EditLens-style edit-magnitude regressor | research | **yes** (fine-tune) | spec TODO | non-NC paired pre/post corpus |
| 6 | Intrinsic-dimension / PHD signal | research | yes | spec TODO | verify GPTID repo license |
| 7 | Raidar rewriting-invariance | research | LLM access | spec TODO | verify Raidar license / clean-room |
| 8 | Watermark-key opt-in test module | defer | no | not scheduled | low value; track PAN 2026 |

"Ready" specs can be dispatched to the GPU box now. The four research specs are
written once the maintainer confirms priority + the corpus/license gating in their
rows (the brief's verification TODO). #5–#7 each carry a license-verification step
that **must** clear before any upstream code/weights are touched.

## Cross-cutting constraints (every capability)

- **No verdict.** No capability ships a single AI/human label or a "% AI" number as
  a default. New detectors emit evidence + claim-license, thresholds operator-side.
- **Orthogonality test.** A new signal must measure an axis we don't already have
  (the brief's orthogonality column). Marginal-overlap candidates (Lastde++, GLTR as
  signal) are explicitly out.
- **Footprint honesty.** Anything pulling new weights/deps gets a dependency tier in
  `dependency_check.py` and a disk/VRAM line in the readiness matrix + README costs.
- **Docs stay current** automatically: a new `capabilities.yaml` entry flows into the
  readiness matrix; the docs-freshness gate (`tools/check_docs_freshness.py`) fails
  CI if a curated capability lacks a CHANGELOG line or the matrix is stale.

## What this plan deliberately doesn't do

- Doesn't ship any restricted weights/data (EditLens, STAR, unconfirmed repos) — the
  method is reimplemented clean-room or the capability waits.
- Doesn't promise calibrated thresholds at merge time — calibration is a separate,
  corpus-gated follow-up per capability.
- Doesn't add a supervised single-verdict classifier as an authority (bias-control
  framing only, if at all).
