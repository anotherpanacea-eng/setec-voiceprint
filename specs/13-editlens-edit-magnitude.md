# 13-editlens-edit-magnitude

> A **clean-room** document-level *edit-magnitude* regressor (EditLens-style): how
> much a text was AI-edited, framed as a **same-corpus calibrated estimate with
> explicit OOD caveats** — never an absolute "% AI."

- **Status:** Spec (research-grade; full-deps box; needs a corpus + GPU fine-tune).
- **Tier:** Research (research brief §C; ROADMAP calibration track).
- **GPU required:** **yes** (fine-tune RoBERTa-Large; ~355M params).
- **Upstream:** EditLens (Thai et al., ICLR 2026, [arXiv:2510.03154](https://arxiv.org/abs/2510.03154)).
- **License:** **clean-room only.** EditLens weights *and* dataset are CC BY-NC-SA → **not** vendorable in GPL-3. The *method* (RoBERTa + MSE against a BERTScore-style similarity proxy between pre/post-edit pairs) is not copyrightable; reimplement against a **non-NC paired corpus** (gating prerequisite). Do not import EditLens weights/data.

## Motivation & honesty

Smoothing-diagnosis routinely meets the `human_authored_ai_modified` case — the
dominant real-world one. EditLens reframes ill-posed span detection as document-level
*magnitude* regression, matching SETEC's posture. **But** the dosage science is
shaky: APT-Eval / Guo et al. show degree-estimation only works *in-distribution* and
collapses toward binary OOD (research brief §C). So this ships as a same-corpus
calibrated estimate, PROVISIONAL, with the OOD caveat load-bearing — explicitly **not**
an absolute "% AI" gauge (which the roadmap lists as an anti-goal).

## Method (clean-room)

Fine-tune a RoBERTa-Large (or smaller) regressor with MSE against a similarity-proxy
target (BERTScore-family) computed between original/edited text pairs from a
**non-NC** paired corpus the operator supplies. Output: a continuous magnitude score
+ a calibrated band *for that corpus only*.

> **Proxy-target candidate (2026-07-05, Fable verdict).** The shipped
> `compression_edit_distance` surface (PR #298; directional raw-LZMA2 compression
> edit-distance, [arXiv:2412.17321](https://arxiv.org/abs/2412.17321)) is a candidate
> **replacement** for the BERTScore-family similarity proxy as the regression target:
> it is license-clean, deterministic, and model-free at data-generation time, and it
> measures edit **magnitude** (what the regressor wants) rather than semantic quality.
> Decision deferred to this spec's build (still corpus/GPU-gated); noted here so the
> target choice is revisited before the fine-tune corpus is generated.

## Contract

- **task_surface:** new `edit_magnitude` (discrimination family; add to enum + labels). Uncalibrated by default.
- **CLI:** `python3 scripts/edit_magnitude_audit.py TARGET [--model PATH] [--json] [--out PATH]`; training in a sibling `scripts/calibration/train_edit_magnitude.py`.
- **JSON envelope:** `results` = magnitude score + (if a calibrated model supplied) the same-corpus band + the corpus provenance. `ClaimLicense`.
- **Claim license:** *licenses* "an edit-magnitude estimate relative to the specific corpus the model was calibrated on"; *refuses* an absolute "% AI" / dosage claim, cross-corpus generalization, and any per-sentence localization. States the OOD-collapse caveat explicitly.
- **capabilities.yaml:** `id: edit_magnitude_audit`, `surface: edit_magnitude`, `status: heuristic` until a PROVENANCE-logged calibration exists, `compute: {tier: surprisal}`, `dependencies.python: [transformers, torch]`.

## Test contract

- `test_surface_registered`; `test_uncalibrated_no_band` (no model → score only, no band); `test_claim_license_refuses_absolute_percent`; `test_envelope_shape`; stubbed-model determinism test. Training script: a smoke test on a tiny synthetic pair set.

## Non-goals

- **No** absolute "% AI-edited" number as a default; no cross-corpus claim.
- No vendoring of EditLens weights/data (NC). Clean-room method only.

## Gating prerequisites (before build)

1. A **non-NC** paired pre/post-edit corpus (operator-sourced).
2. Confirm the chosen base-model license (prefer Apache/MIT).
