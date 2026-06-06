# 15-raidar-rewriting-invariance

> A **rewriting-invariance** discrimination signal (Raidar): ask an LLM to rewrite the
> text and measure how much it changes — LLMs edit their own (AI-like) prose less than
> human prose. A paraphrase-robust axis orthogonal to perplexity signals.

- **Status:** Spec (research-grade; full-deps box; needs LLM access).
- **Tier:** Research (research brief §D).
- **GPU required:** no (uses an LLM API or a local instruct model); network/key if API.
- **Upstream:** Mao et al., "Raidar: geneRative AI Detection viA Rewriting" ([ICLR 2024, arXiv:2401.12970](https://arxiv.org/abs/2401.12970)).
- **License:** **verify Raidar repo license;** the *idea* (rewrite + edit-distance) is simple and published — clean-room if blocked. Reuse SETEC's existing LLM-judge plumbing (Surface 6) for the rewrite call.

## Motivation & orthogonality

A genuinely different axis: not how *likely* the text is under a model, but how much a
model *changes* it on a "rewrite to improve" prompt. AI-like prose is edited less
(the model already sees it as "good"), human prose more. Robust to paraphrase attacks
that defeat perplexity detectors. Evidence, not verdict.

## Method (clean-room-able)

Prompt an LLM to rewrite the target ("rewrite to improve clarity"); compute
edit-distance / token-overlap between original and rewrite; low change ⇒ AI-like.
Average over N rewrites for stability. Reuse the `narrative_judge`-style pluggable
LLM client; record the judge model + prompt in provenance.

## Contract

- **task_surface:** new `rewriting_invariance` (discrimination family; add to enum + labels). Uncalibrated by default.
- **CLI:** `python3 scripts/rewriting_invariance_audit.py TARGET --judge MODEL [--n 3] [--json] [--out PATH]`.
- **JSON envelope:** `results` = mean rewrite-distance + per-trial distances + judge model/prompt id + n. No band absent operator thresholds. `ClaimLicense`.
- **Claim license:** *licenses* "the mean rewriting distance under judge model M and prompt P"; *refuses* an AI/human verdict absent thresholds; notes strong dependence on the judge model + prompt (must be pinned in provenance) and per-call cost.
- **capabilities.yaml:** `id: rewriting_invariance_audit`, `surface: rewriting_invariance`, `status: heuristic`, `compute: {tier: api_llm}`, `dependencies.python: []` (+ the operator's LLM SDK).

## Test contract

- `test_surface_registered`; `test_distance_with_stub_judge` (deterministic stub rewriter); `test_no_default_threshold_no_band`; `test_claim_license_names_judge_dependence`; `test_envelope_shape`; `test_n_trials_aggregated`.

## Non-goals

- No verdict / shipped threshold; judge-and-prompt-dependent evidence only.
- Not robust to a poorly-pinned judge — provenance of model+prompt is mandatory.

## Gating prerequisites

1. Confirm Raidar license (clean-room the prompt+distance if blocked).
2. Decide the default judge model (operator-supplied; document cost).
