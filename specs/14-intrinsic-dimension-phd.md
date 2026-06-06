# 14-intrinsic-dimension-phd

> An **intrinsic-dimension** discrimination signal (PHD — persistent-homology
> dimension of the contextual-embedding point cloud). Human prose tends to a higher
> intrinsic dimension than AI prose. A topological axis fully orthogonal to SETEC's
> lexical / perplexity signals.

- **Status:** Spec (research-grade; full-deps box).
- **Tier:** Research (research brief §D — "most orthogonal axis"; multilingual, robust).
- **GPU required:** optional (embedding forward passes; CPU-feasible but slow).
- **Upstream:** Tulchinskii et al., "Intrinsic Dimension Estimation for Robust Detection of AI-Generated Texts" ([arXiv:2306.04723](https://arxiv.org/abs/2306.04723); GPTID repo).
- **License:** **verify GPTID repo license before vendoring;** the PHD math is published — clean-room if non-permissive. Embedding model: prefer Apache/MIT.

## Motivation & orthogonality

Every shipped discrimination signal is probabilistic (perplexity/surprisal) or lexical.
PHD measures the **geometry** of the embedding cloud: the persistent-homology fractal
dimension of per-token contextual embeddings. It is reported as the single most
orthogonal axis in the literature and is notably multilingual-robust — which pairs
well with SETEC's ESL-fairness concern. Evidence, not verdict.

## Method (clean-room-able)

Embed tokens/sentences with a contextual model → point cloud → estimate persistent-
homology dimension (PHD) via the published algorithm. Report the scalar PHD + the
sample size. Uncalibrated by default.

## Contract

- **task_surface:** new `intrinsic_dimension` (discrimination family; add to enum + labels). Uncalibrated by default.
- **CLI:** `python3 scripts/intrinsic_dimension_audit.py TARGET [--model ALIAS] [--json] [--out PATH]`.
- **JSON envelope:** `results` = PHD scalar + n_points + embedding-model id; no band absent operator thresholds. `ClaimLicense`.
- **Claim license:** *licenses* "the intrinsic (PHD) dimension of the text's embedding cloud under model M"; *refuses* an AI/human verdict absent operator-supplied thresholds; notes short-text instability and the embedding-model dependence.
- **capabilities.yaml:** `id: intrinsic_dimension_audit`, `surface: intrinsic_dimension`, `status: literature_anchored`, `compute: {tier: surprisal, length_floor_words: 500}`, `dependencies.python: [transformers, torch]` (+ a TDA dep — `gph`/`ripser` — to verify/vendor-check).

## Test contract

- `test_surface_registered`; `test_phd_deterministic_with_seed` (stub embedder); `test_no_default_threshold_no_band`; `test_claim_license_refuses_verdict`; `test_short_text_instability_warned`; `test_envelope_shape`.

## Non-goals

- No verdict / shipped threshold; orthogonal *evidence* only.
- Not a replacement for Binoculars/DivEye/Fast-DetectGPT — an additional axis.

## Gating prerequisites

1. Confirm GPTID repo + any TDA library license (clean-room the math if blocked).
2. Choose the embedding backend (reuse `embedding_backend.py`; Apache/MIT model).
