# 03-fast-detectgpt-curvature

> A conditional-probability **curvature** detector (Fast-DetectGPT) as a Surface 5
> discrimination-evidence signal — a zero-shot statistic genuinely distinct from
> Binoculars' cross-perplexity ratio and DivEye's surprisal moments.

- **Status:** Ready
- **Tier:** near-term
- **GPU required:** optional (one small causal LM; laptop-feasible)
- **Upstream / prior art:** Bao et al., Fast-DetectGPT, ICLR 2024 ([arXiv:2310.05130](https://arxiv.org/abs/2310.05130), [repo](https://github.com/baoguangsheng/fast-detect-gpt)).
- **License decision:** **MIT** (code) — GPL-3-compatible. Clean-room or light port both fine; the conditional-curvature math is short and published.

## Motivation

SETEC's Surface 5 has Binoculars (cross-perplexity ratio between two models) and a
DivEye-style surprisal signal (per-token mean/var/autocorrelation). Fast-DetectGPT
measures a *different* quantity: the **curvature of the model's conditional log-prob**
— whether the candidate text sits at a local maximum of the model's probability
surface, estimated by sampling alternatives at each position rather than perturbing
the whole text (DetectGPT's expensive step). Orthogonal axis, cheap, MIT-licensed.

## Method

For text x under a scoring model: estimate the conditional probability curvature by
comparing log p(x) to the distribution of log p(x̃) for samples x̃ drawn from the
model's per-token conditional distribution, normalized to a z-score-like statistic.
One forward pass + sampling at each position; a single small LM suffices (reuse the
`surprisal_backend.py` causal-LM wrapper). Output: a continuous curvature score and
the per-position series.

Reuses `surprisal_backend` for model loading; new code is the curvature estimator and
the surface contract.

## Contract (the testable interface)

- **task_surface:** existing `binoculars_discrimination` is model-pair-specific;
  prefer a sibling value `discrimination_curvature` (or fold under a shared
  `discrimination_evidence` surface — decide in Open questions). Register in
  `output_schema` + `claim_license`.
- **CLI:** `python3 plugins/setec-voiceprint/scripts/fast_detect_curvature.py TARGET [--model ALIAS] [--n-samples N] [--surprisal-dtype auto|fp32|fp16|bf16] [--device DEVICE] [--json] [--out PATH]`
- **JSON envelope:** keys under `results`: `model_id`, `curvature_score`, `n_samples`,
  `per_position` (optional), `n_tokens`. `ClaimLicense` block. Uncalibrated by default
  (no threshold → no band).
- **Claim license:** licenses "the conditional-curvature statistic under model M is C";
  refuses any AI/human label absent operator-supplied thresholds; notes the
  in-distribution caveat and paraphrase sensitivity from the brief.
- **capabilities.yaml entry:** `id: fast_detect_curvature`,
  `surface: discrimination_curvature`, `status: literature_anchored`,
  `handoff: experimental`,
  `compute: {tier: surprisal, cost_note: "one small causal LM, ~0.6–2 GB weights; CPU works, GPU faster", length_floor_words: 50}`,
  `dependencies.python: [transformers, torch]`.
- **Dependencies / footprint:** reuses the `surprisal` tier; no new tier.

## Test contract (`plugins/setec-voiceprint/scripts/tests/test_fast_detect_curvature.py`)

- `test_envelope_shape` — validates; correct task_surface.
- `test_no_default_threshold_no_band` — output carries no verdict/band absent thresholds.
- `test_claim_license_refuses_verdict`.
- `test_curvature_deterministic_with_seed` — fixed seed + stub LM ⇒ stable score.
- `test_orthogonal_statistic` — score is computed independently of any Binoculars/
  surprisal field (no accidental reuse of the cross-perplexity number).
- `test_missing_torch_graceful` — clean install hint when torch absent.
- `test_capabilities_entry_present` — drift linter passes.

## Calibration posture

Literature-anchored (close to the published condition) but **uncalibrated by default**
for the operator's corpus — thresholds operator-side via the existing calibration
pipeline, exactly like `binoculars_audit`. Produces a per-corpus PROVENANCE entry when
calibrated.

## Out of scope / non-goals

- No verdict; no shipped threshold.
- Not a replacement for Binoculars/DivEye — an additional orthogonal signal.

## Open questions

- New `discrimination_curvature` surface vs. folding Binoculars + curvature + DivEye
  under one `discrimination_evidence` surface. Recommend a shared surface with a
  `method` field, to keep Surface 5 coherent — but that's a small refactor of
  `binoculars_audit`'s tag; confirm before building.
