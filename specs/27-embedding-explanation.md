# 27-embedding-explanation

> Turn the opaque LUAR/`voice_fingerprint` cosine into **human-checkable, named style
> descriptions**: which interpretable stylometric features account for the embedding similarity, and
> how much is residual neural-style not captured by any named feature. An explanation layer — it adds
> **no new verdict**, it makes the existing one inspectable.

- **Status:** Draft
- **Tier:** research-grade (explanation over an existing embedding; the named-feature math is CPU/stdlib,
  the LUAR embedding itself is the gated `authorship_embedding` tier — **not CI-runnable here**, POC-gated
  exactly like the spec-22 M2 homogeneity LUAR lens)
- **GPU required:** no for the explanation math; the LUAR embedding path needs the style-embedding tier
  (transformers + torch), which CI lacks → the real-embedding path is **skipif-gated**, the math is
  tested over an injected stub embedding/cosine.
- **Upstream / prior art:**
  - **Residualized Similarity** ([arXiv:2510.05362](https://arxiv.org/abs/2510.05362)): decompose a
    black-box similarity into an interpretable-feature-explained part + a residual. The M1 backbone.
  - **Latent-Space Interpretation for Stylometry** ([arXiv:2409.07072](https://arxiv.org/abs/2409.07072)):
    attach human-readable style-attribute names to directions in the embedding space. The M2 backbone.
- **License decision:** clean-room the methods (a decomposition + a named-direction projection). No
  weights beyond the LUAR encoder already vendored by `voice_fingerprint`.

## Motivation

`voice_fingerprint` (surface `authorship_embedding`) reports a LUAR cosine distribution — the framework's
single most predictive same-author signal, and its **least interpretable**: an operator sees `0.71` and
has no way to check *why*. The whole house posture is no-verdict / keep-the-human-in-the-loop, and an
un-inspectable number is in tension with that. This adds the missing interpretability layer: it explains
the cosine in terms of SETEC's **already-named** stylometric signals (burstiness, MATTR, MTLD,
function-word ratio, dependency distance, …) and surfaces the **residual** — the part of the embedding
similarity that no named feature accounts for, i.e. exactly where the operator must look at the neural
signal on trust.

**Orthogonality.** `voice_fingerprint` (`authorship_embedding`) computes the cosine; `voice_profile` /
`variance_audit` (`voice_coherence` / `smoothing_diagnosis`) compute the named features. Neither
*explains* the cosine in terms of the others. New axis: **interpretation of an existing similarity**, not
a new distance. It computes no new authorship number.

## Posture — load-bearing

- **An explanation, not a verdict.** It inherits and re-states `authorship_embedding`'s refusals (no
  "same person", "different author", "AI/human") and adds none of its own. The decomposition does not
  make the cosine *more* authoritative — it makes it *inspectable*.
- **The named basis is a chosen lens, not ground truth.** The "explained" fraction is explained *relative
  to the named feature set we happen to compute*; a large residual means "not captured by these names,"
  NOT "more authentic" or "more AI". The output says so.
- **Privacy-gated.** It consumes a LUAR embedding, so it rides `authorship_embedding`'s privacy gate —
  same consent/registration posture as `voice_fingerprint`; never a looser one.
- Ships `uncalibrated`. No threshold on the residual; the operator reads it.

## Method

### M1 — `residual_explanation` (the CI-testable backbone; Residualized Similarity)

Given a target + comparison (or target-vs-baseline), take **(a)** the LUAR cosine from `voice_fingerprint`
and **(b)** the pair's vector of named stylometric features (reused from the existing audits — never
recomputed here). Standardize the named features against a reference scale, form a per-feature *agreement*
between the named-feature similarity and the LUAR cosine, and report:
- `named_feature_alignment`: per named feature, how much the two texts' agreement on THAT feature tracks
  the LUAR cosine (a signed, descriptive contribution — "shared low burstiness pulls toward the high
  cosine; divergent MATTR pulls against it").
- `explained_fraction` + `residual_fraction`: how much of the cosine the named set accounts for vs. the
  residual neural-style signal. **The residual is the headline** — it points the operator at what the
  named features cannot see.
- `luar_cosine` (carried through, attributed to `voice_fingerprint`).

**No fit by default** (open Q1): the v1 decomposition is the transparent side-by-side + agreement, NOT a
regression that needs a training corpus. A fitted residualization (regress cosine on features over an
operator corpus, report the true OLS residual) is the calibrated upgrade, gated behind `--fit-baseline`.

### M2 — `named_direction_profile` (Latent-Space Interpretation; POC-gated)

Project the LUAR embedding onto a set of **named attribute directions** (precomputed anchor directions,
each tagged with a human-readable style attribute) → a ranked list of named descriptions ("high
formality, low sentence complexity, …"). The anchor directions are themselves LUAR-embedded, so M2 is
**POC-gated** (can't build/verify the real directions in CI here); it lands after the LUAR-box POC, like
spec-22 M2.

## Contract (the testable interface)

- **task_surface:** **new — `embedding_explanation`** (both `residual_explanation.py` and, later,
  `named_direction_profile.py` declare it). New surface → **both goldens** (`_golden_capabilities.json`
  +1/id; `_golden_task_surface_labels.json` +1 + the `test_claim_license_surfaces` count) — the
  [[voiceprint-capability-golden-bump]] two-golden rule.
- **CLI (M1):** `python3 .../residual_explanation.py TARGET --comparison FILE [--features-json F]
  [--cosine FLOAT] [--json] [--out F]`. `--cosine`/`--features-json` inject precomputed inputs (the
  CI/stub path); without them it calls `voice_fingerprint` + the named audits (the gated real path).
- **JSON envelope:** `build_output()` + `ClaimLicense`; `results` = `named_feature_alignment`,
  `explained_fraction`, `residual_fraction`, `luar_cosine`, `n_features`, `calibration_status:
  "uncalibrated"`, provenance (which audits sourced the features + the embedding model). **No** authorship
  score / verdict key.
- **Claim license — licenses:** "a descriptive decomposition of the `authorship_embedding` LUAR cosine
  into named interpretable stylometric features + a residual, as an interpretation aid." **Refuses:** any
  same-author / different-author / AI-human determination (inherited from `authorship_embedding`); any
  claim that the residual measures authenticity or AI-ness; any threshold. The named basis is a lens, not
  ground truth. `uncalibrated`; privacy-gated.
- **Gates:** privacy gate inherited from `authorship_embedding`; the real-embedding path needs the
  style-embedding tier (skipif/POC in CI); a degenerate input (one window, zero-norm embedding) →
  `bad_input` fail-loud.
- **Paper trail:** fragment(s) + the `claim_license_surfaces` label + `changelog.d` (cites both arXiv ids)
  + glossary pointer + **both golden bumps** + `gen_calibration_readiness`. Drift / docs-freshness /
  `pytest test_capabilities_dropin test_claim_license_surfaces` before push.

## Test contract (stub embedding; torch-free)

`tests/test_residual_explanation.py` (M1): inject `--cosine` + `--features-json` (a fixed feature vector)
→
- deterministic `named_feature_alignment` shape (signed per-feature contribution) + `explained_fraction`
  + `residual_fraction` summing sensibly; **explained + residual partition the cosine** (pinned).
- **no-verdict guard** — `not in` over results keys for `same_author`, `verdict`, `is_ai`, `score`,
  `authorship_*`; `calibration_status == "uncalibrated"`.
- **claim-license refuses-verdict** — `does_not_license` inherits the authorship refusals + "the residual
  does not measure authenticity / AI-ness" + "named basis is a lens, not ground truth".
- **residual headline** — a cosine fully explained by features ⇒ `residual_fraction ≈ 0`; an orthogonal
  feature set ⇒ `residual_fraction` large (pinned with two stub vectors).
- **degenerate input** — zero-norm / empty feature vector ⇒ `bad_input`.
- the real LUAR path is `skipif` (no torch) — a stub encoder smoke test only.

## Calibration posture

Ships `uncalibrated`; the residual is never thresholded into a verdict. A fitted residualization
(`--fit-baseline`) would calibrate the *explained/residual split* against an operator corpus, reported as
provenance, never a shipped authorship operating point.

## Out of scope / non-goals

- No same-author / AI verdict, ever (inherits the `authorship_embedding` refusals). No new distance metric
  (it explains the existing cosine). M2 (`named_direction_profile`) is POC-gated and lands separately. The
  named feature set is SETEC's existing signals — this surface never invents a new stylometric feature.

## Open questions

1. **No-fit side-by-side vs fitted OLS residualization** — default v1 = no-fit (transparent, corpus-free);
   `--fit-baseline` upgrade later. Confirm v1 scope.
2. **New surface `embedding_explanation` vs. extending `authorship_embedding`** — default new (distinct
   task: explanation, not the cosine itself). Confirm.
3. **Which named features feed M1** — the full `variance_audit`/`voice_coherence` set, or a curated
   human-legible subset (burstiness, MATTR, MTLD, function-word ratio, dependency distance)? Default: the
   curated legible subset (an explanation people can read), full set behind a flag.
