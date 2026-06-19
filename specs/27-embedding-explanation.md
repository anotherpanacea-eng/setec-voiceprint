# 27-embedding-explanation

> Turn the opaque LUAR/`voice_fingerprint` cosine into a **human-checkable, named side-by-side**: show
> the cosine next to each named interpretable stylometric feature's per-pair similarity, and mark where
> the named lens **tracks** vs **diverges** from the embedding. An explanation layer — it adds **no new
> verdict** and **no fabricated number**, it makes the existing cosine inspectable.

- **Status:** Ready — adversarially reviewed 2026-06-19 (NEEDS-REWORK → reworked). The make-or-break P1:
  you **cannot** partition a single neural cosine scalar into explained+residual without a fit (the cited
  Residualized Similarity *is* a fitted method), so the original "no-fit `explained_fraction`/
  `residual_fraction`" was fabricated and the "residual headline" would read as an AI verdict. Fixed:
  **M1 v1 is a transparent SIDE-BY-SIDE with a per-feature `agreement ∈ {tracks, diverges}` and NO numeric
  partition**; a numeric explained/residual split exists **only** under `--fit-baseline` (OLS R²,
  residual = 1−R², corpus-relative provenance). Inject is a **test seam** (monkeypatch `compute_inputs`),
  not a CLI footgun; privacy stated mechanically (real-text path gated, injected path consumes no text →
  no privacy surface, marked non-production). See the rework log at the foot.
- **Tier:** research-grade (explanation over an existing embedding; the named-feature side-by-side is
  CPU/stdlib, the LUAR embedding is the gated `authorship_embedding` tier — **not CI-runnable here**, so
  the real-embedding path is `skipif`-gated and tested via a monkeypatched input seam, the same
  LUAR-can't-run-in-CI gating the privacy-gated LUAR lens elsewhere uses).
- **GPU required:** no for the side-by-side; the LUAR path needs transformers + torch (CI lacks it).
- **Upstream / prior art:**
  - **Residualized Similarity** ([arXiv:2510.05362](https://arxiv.org/abs/2510.05362)): a *fitted*
    decomposition of a black-box similarity into interpretable-feature-explained + residual. Backs the
    **`--fit-baseline`** upgrade ONLY (it needs a corpus); not the no-fit v1.
  - **Latent-Space Interpretation for Stylometry** ([arXiv:2409.07072](https://arxiv.org/abs/2409.07072)):
    human-readable style-attribute names for embedding directions. Backs M2.
- **License decision:** clean-room the methods. No weights beyond the LUAR encoder `voice_fingerprint`
  already vendors.

## Motivation

`voice_fingerprint` (surface `authorship_embedding`) reports a LUAR cosine — the framework's most
predictive same-author signal and its least interpretable: an operator sees `0.71` and cannot check
*why*. The house posture is no-verdict / keep-the-human; an un-inspectable number is in tension with that.
This adds the missing interpretability layer by putting the cosine **side by side** with SETEC's
already-named stylometric signals (burstiness, MATTR, MTLD, function-word ratio, dependency distance) and
marking where they **agree** with the embedding vs **diverge** — the divergences are where the operator
must look at the neural signal on its own terms.

**Orthogonality.** `voice_fingerprint` (`authorship_embedding`) computes the cosine; `variance_audit` /
`voice_profile` (`smoothing_diagnosis` / `voice_coherence`) compute the named features. Neither *places
them side by side and marks agreement*. New axis: interpretation of an existing similarity, not a new
distance. It computes no new authorship number.

## Posture — load-bearing

- **A side-by-side, not a verdict and not a fabricated partition.** v1 emits the LUAR cosine, each named
  feature's per-pair similarity, and a per-feature `agreement ∈ {tracks, diverges}` — and **no**
  `explained_fraction`/`residual_fraction` (those would be invented numbers with no defined relationship
  to a neural cosine). It inherits and re-states `authorship_embedding`'s refusals (no "same person",
  "different author", "AI/human") and adds none of its own.
- **Divergence is an inspection pointer, not a suspicion score.** A feature that diverges means "the named
  lens and the embedding disagree here — look closer," NOT "more AI" / "less authentic." The claim license
  says this explicitly; there is no divergence threshold and no divergence count presented as a score.
- **The named basis is a chosen lens, not ground truth.** Agreement is relative to the named feature set
  we happen to compute; the output says so.
- **Privacy — stated mechanically, not asserted.** The real-text path loads LUAR and consumes the texts,
  so it rides `authorship_embedding`'s privacy gate exactly. The test/injected path (`inputs_source:
  "injected"`) consumes **no text** and loads no embedding, so there is no privacy surface to gate — and
  it is marked non-production (the claim license refuses to read an injected run as a real interpretation).
- Ships `uncalibrated`.

## Method

### M1 — `cosine_explanation` (the CI-testable side-by-side)

`compute_inputs(target, comparison)` (the single seam tests monkeypatch) returns **(a)** the LUAR cosine
from `voice_fingerprint` and **(b)** the pair's named feature values (reused from the existing audits —
never recomputed here). Then, with no fit:
- `named_feature_comparison`: per curated named feature, `{feature, target_value, comparison_value,
  feature_similarity, agreement}` where `feature_similarity` is a defined per-feature closeness
  (1 − normalized |Δ| against a reference scale) and `agreement ∈ {tracks, diverges}` is `tracks` iff the
  feature_similarity falls on the same side of its reference midpoint as the cosine (a transparent,
  defined rule — NOT a partition of the cosine).
- `divergent_features`: the list of features marked `diverges` — the **headline** (a qualitative pointer,
  never a count-as-score).
- `luar_cosine`: carried through, attributed to `voice_fingerprint`.
- **No `explained_fraction`/`residual_fraction`** unless `--fit-baseline CORPUS` is supplied: that path
  fits OLS of cosine on the named features over the operator corpus and emits `fit_r2` +
  `fit_residual = 1 − fit_r2` with **corpus-relative provenance** (model + corpus id), explicitly NOT a
  per-pair scalar. v1 default ships without it.

### M2 — `named_direction_profile` (Latent-Space Interpretation; POC-gated)

Project the LUAR embedding onto **named attribute directions** (precomputed anchors, each a human-readable
attribute) → a ranked named-description list. The anchors are themselves LUAR-embedded, so M2 is
**POC-gated** (cannot build/verify the real directions in CI here); lands separately after the LUAR-box POC.

## Contract (the testable interface)

- **task_surface:** **new — `embedding_explanation`** (both scripts declare it). New surface → **both
  goldens**: `_golden_capabilities.json` **90 → +1 per registered script id** + the
  `test_capabilities_dropin` count; `_golden_task_surface_labels.json` **23 → 24** + the
  `test_claim_license_surfaces` count (M1 only; M2 reuses the surface). The
  [[voiceprint-capability-golden-bump]] two-golden rule.
- **CLI (M1):** `python3 .../cosine_explanation.py TARGET --comparison FILE [--fit-baseline CORPUS]
  [--inputs-json F] [--json] [--out F]`. **No `--cosine` flag.** Default path computes via
  `compute_inputs` (gated, loads LUAR). `--inputs-json` is an explicit cached/injected path → the envelope
  carries `inputs_source: "injected"` and the claim license refuses reading it as production. The CI test
  seam is a monkeypatch of `compute_inputs`, mirroring `voice_fingerprint`'s `_load_encoder` test seam.
- **JSON envelope:** `build_output()` + `ClaimLicense`; `results` = `named_feature_comparison`,
  `divergent_features`, `luar_cosine`, `n_features`, `inputs_source` ∈ {computed, injected},
  `calibration_status: "uncalibrated"`, provenance (the audits + embedding model). **No**
  `explained_fraction`/`residual_fraction`/authorship-score/verdict key (the fitted `fit_r2`/`fit_residual`
  appear only when `--fit-baseline` is given).
- **Claim license — licenses:** "a named side-by-side of the `authorship_embedding` LUAR cosine against
  interpretable stylometric features, marking where the named lens tracks vs diverges from the embedding —
  an interpretation aid." **Refuses:** any same-author / different-author / AI-human determination
  (inherited from `authorship_embedding`); any reading of divergence as authenticity / AI-ness / suspicion;
  any threshold; reading an `inputs_source: "injected"` run as a production interpretation. The named basis
  is a lens, not ground truth. `uncalibrated`; the real-text path is privacy-gated.
- **Gates:** privacy gate inherited on the real-text path (LUAR + text); a degenerate input (zero-norm
  embedding, empty feature vector, missing comparison) → `bad_input` fail-loud; the real-embedding path is
  `skipif` (no torch) in CI.
- **Paper trail:** fragment(s) + the `claim_license_surfaces` label + `changelog.d` (cites both arXiv ids)
  + glossary pointer + both golden bumps + `gen_calibration_readiness`. Drift / docs-freshness / `pytest
  test_capabilities_dropin test_claim_license_surfaces` before push.

## Test contract (monkeypatched input seam; torch-free)

`tests/test_cosine_explanation.py` (M1): monkeypatch `compute_inputs` to return a fixed `(cosine,
features)` →
- deterministic `named_feature_comparison` shape (each row has feature/target_value/comparison_value/
  feature_similarity/`agreement ∈ {tracks, diverges}`) + `divergent_features` = the diverging rows.
- **no-verdict / no-fabricated-number guard** — `not in results` for `same_author`, `verdict`, `is_ai`,
  `score`, `authorship_*`, **`explained_fraction`, `residual_fraction`** (absent without `--fit-baseline`);
  `calibration_status == "uncalibrated"`.
- **claim-license refuses-verdict** — `does_not_license.lower()` contains the authorship refusals +
  "does not measure authenticity" + "lens, not ground truth" + the injected-run refusal (pinned via the
  repo's `does_not_license` substring idiom).
- **agreement is defined, not invented** — a feature whose similarity sits on the cosine's side of its
  reference midpoint ⇒ `tracks`; the opposite ⇒ `diverges` (two stub features pin both).
- **injected provenance** — an `--inputs-json` run sets `inputs_source: "injected"`; a computed run sets
  `"computed"`.
- **degenerate input** — empty feature vector / missing comparison ⇒ `bad_input`.
- the real LUAR path is `skipif` (no torch) — a monkeypatched-seam smoke test only.

## Calibration posture

Ships `uncalibrated`; v1 emits no fraction to threshold. `--fit-baseline` calibrates an explained/residual
split against an operator corpus (OLS R²), reported as corpus-relative provenance, never a shipped
per-pair authorship operating point.

## Out of scope / non-goals

- No same-author / AI verdict, ever (inherits `authorship_embedding` refusals). No fabricated
  explained/residual number without a fit. No new distance metric (it explains the existing cosine). M2
  (`named_direction_profile`) is POC-gated and lands separately. The named feature set is SETEC's existing
  signals — this surface never invents a new stylometric feature.

## Open questions

1. ~~No-fit fractions~~ **Resolved (review P1): v1 = side-by-side + `agreement`, NO numeric partition.**
   The fitted explained/residual split is an `--fit-baseline` upgrade (needs a corpus), not v1.
2. ~~New surface vs extend `authorship_embedding`~~ **Resolved: new surface `embedding_explanation`**
   (explanation is a distinct task from computing the cosine).
3. **Curated feature subset** — default to ~5 legible features (burstiness, MATTR, MTLD, function-word
   ratio, dependency distance); full `variance_audit` set behind a flag. (Review: keep the curated default.)

## Rework log (2026-06-19, after adversarial review → NEEDS-REWORK)

- **P1 decomposition math:** dropped the fabricated no-fit `explained_fraction`/`residual_fraction`; v1 is
  a defined side-by-side with `agreement ∈ {tracks, diverges}`. Numeric split only under `--fit-baseline`
  (OLS R², residual = 1−R², corpus-relative).
- **P1 residual-as-verdict:** no residual number in v1; `divergent_features` is a qualitative pointer, and
  the claim license refuses reading divergence as authenticity/AI-ness.
- **P1 inject footgun:** removed `--cosine`; the CI seam is a monkeypatch of `compute_inputs`. `--inputs-json`
  (if used) sets `inputs_source: "injected"` and the license refuses production interpretation of it.
- **P1 privacy:** stated mechanically — real-text path gated; injected path consumes no text (no privacy
  surface, non-production), no claimed inheritance the wiring doesn't deliver.
- **P2:** golden counts pinned (caps 90→+1/id, labels 23→24); dropped the unverifiable "spec-22" numbered
  appeal; no-verdict tests pinned to the repo's `does_not_license` substring idiom.
