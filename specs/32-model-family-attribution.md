# 32-model-family-attribution

> **Spec number PROVISIONAL.** Authored as `28-model-family-attribution` on branch
> `spec/28-model-family-attribution`; `28` collides with three other specs already on `main`
> (`28-cross-doc-originality`, `28-eval-discipline-bundle`, `28-styledistance-encoder-upgrade`), so this
> build renumbers it to the next free slot, `32`. Renumber to the canonical number at gate-pass.

> "Which model family does this read most like?" — an **abstention-first ranked similarity** of a target
> against operator-supplied per-family reference corpora, over **standardized** interpretable stylometric
> features, with a **relative** out-of-distribution gate. **No normalized posterior, no attribution
> verdict, no AI-vs-human verdict.** Raw ranked evidence for a human, under heavy abstention.

- **Status:** Ready — adversarially reviewed 2026-06-19 (NEEDS-REWORK → reworked). 4 P1s fixed: (1)
  **dropped the normalized `advisory_posterior`** — a sum-to-1 quantity over families reads as P(family)
  and renormalizes to look confident on a bad fit (the #231 `residual_fraction` lesson); M1 emits only
  raw per-family similarities. (2) **OOD is now RELATIVE** — a per-family within-scatter baseline, not a
  fixed absolute floor (an absolute floor cannot tell "true source absent" from "register mismatch", and
  any text is closest to *some* centroid). (3) **features are STANDARDIZED** (robust-z against the pooled
  reference) before aggregation — they are non-commensurate (MTLD∈[10,200+] would dominate a raw mean).
  (4) **the feature set is resolved ONCE** at run start (intersection across target + all refs); spaCy-
  gated `mdd` is uniformly in or out, never per-doc (else centroids compare different subspaces). Plus:
  `human` may not occupy the top slot (forces abstention); a hard min docs/family; a new family-dir
  loader. M1 cleared to build.
- **Tier:** research-grade. **M1 is stdlib / model-CPU** (interpretable named features over text corpora —
  no embedding model, no GPU), CI-runnable end to end. Optional embedding backend is M2 (gated).
- **GPU required:** no (M1).
- **Upstream / prior art:**
  - **Biber-feature separation of LLM families** ([arXiv:2410.16107](https://arxiv.org/abs/2410.16107)):
    interpretable features separate families — but with ~96 features over large corpora. M1 uses SETEC's
    ~5 named signals over operator corpora, so it is **deliberately weak, abstention-first evidence**, not
    the paper's classifier (the output says so).
  - **From Text to Source** ([arXiv:2309.13322](https://arxiv.org/abs/2309.13322)) + **OpenTuringBench**
    ([arXiv:2504.11369](https://arxiv.org/abs/2504.11369)): the attribution task + the open-set / OOD
    framing the relative gate addresses.
- **License decision:** clean-room (standardized per-family centroids + raw ranked similarity + relative
  OOD + abstention). No weights in M1.

## Motivation

SETEC's discrimination surfaces ask "how machine-like under one model?"; none ask **which family this
reads most like** (confirmed: no `capabilities.d` surface attributes a family). It is also the most
over-claim-prone axis, so the design refuses the verdict and surfaces raw ranked evidence under heavy,
*real* abstention.

**Orthogonality.** Distinct from the discrimination surfaces (machine-ness vs one model) and from
`authorship_embedding` / `voice_coherence`. New axis: family-relative similarity, advisory.

## Posture — load-bearing, non-negotiable

- **No normalized posterior.** M1 emits raw per-family `similarity` (un-normalized, 0–1) + a ranking; it
  emits **nothing that sums to 1 over families** — a normalized posterior manufactures a P(family) reading
  and looks confident even when every family fits badly.
- **Not an attribution verdict.** No "produced by <family>" key or claim; the claim license refuses it.
- **Not an AI-vs-human verdict.** A `human`-class reference (if supplied) is ranked like any label, but it
  may **never occupy the reported top slot** — if it would, the surface abstains. A high `human` similarity
  is not a human certificate; the AI/human axis belongs to the discrimination surfaces, which also refuse
  it. **The human-class gate is relabel-proof:** the match is case/space/hyphen-normalized AND covers a
  small reserved synonym set (`human`, `humans`, `human_writers`, `people`, `organic`, `non_ai`, …) routed
  through the single `_is_human_label` chokepoint, so a one-character relabel (`Human`, `humans`) cannot
  route a 'reads most like HUMAN' ruling around the gate. It does not over-match (`humane_llm`,
  `superhuman` are ordinary labels).
- **Abstention-first, and REAL.** Abstains (`attribution_available: false`, ranking demoted to
  evidence-only) when: (a) < 2 reference families; (b) any family has fewer than `MIN_DOCS_PER_FAMILY` (≥5,
  a HARD floor an operator may only RAISE) docs, or a reference doc is below the length floor; (c) **the
  TARGET is below the `--min-words` length floor** — the same floor that drops short reference docs guards
  the input being judged (sub-floor stylometry is unstable), so a too-short target can never be attributed;
  (d) **relative OOD** — the target's distance to the top family's centroid is not within that family's own
  within-scatter (the target is an outlier even relative to the family's members, so the true source is
  plausibly absent); (e) the top-2 margin is below an ambiguity threshold; (f) a `human`-class label would
  be top. Abstention is the default, not the exception.
- **Weak, low-dimensional evidence.** ~5 features over operator corpora ≠ the Biber paper's ~96 features
  over large corpora; the `assumptions` block and the license say so explicitly.
- **Corpus/set-dependent, uncalibrated, self-excluding.** Only as good as the references; a missing family
  is never named; thresholds PROVISIONAL; the target is dropped from its own family corpus (the
  `general_imposters` resolve-path-equality pattern).

## Method

### M1 — `model_family_attribution` (stdlib, CI-runnable)

1. **Resolve the feature set ONCE.** The named set is burstiness_B / MATTR / MTLD / function-word ratio /
   mean-dependency-distance (reused from `variance_audit`: `sentence_length_stats` / `mattr` / `mtld` /
   `function_word_fingerprint` / `mdd_stats`). `mdd` needs spaCy; if spaCy is absent it is dropped for
   **everyone** (target + all references) — the comparison subspace is fixed at run start, never per-doc.
2. **Standardize.** Compute a per-feature robust centre+scale (median + MAD) over the **pooled** reference
   docs; map every doc's features to robust-z. (Non-commensurate raw features would let MTLD's scale
   dominate.)
3. **Per-family centroid** = the median standardized vector of that family's docs; **within-scatter** =
   the median distance of the family's own docs to their centroid (the relative-OOD baseline).
4. **Similarity** = a distance-derived score in standardized space (`1 / (1 + dist)`), raw and
   un-normalized. `family_ranking` = `[{family, distance, similarity, within_scatter, n_docs}]` desc.
5. **Gates** → `attribution_available` + `reason` per the posture (relative OOD: `dist_to_top >
   k * within_scatter_top`; margin; min docs; <2 families; human-would-be-top). The ranking is still
   emitted as raw evidence when not attributable, flagged.

Reference input: `--reference-manifest` (JSONL `{family, text|text_path}`) or `--reference-dir` of
`family/<files>` subdirs via a **new `_load_family_dir`** (the flat `idiolect_detector.directory_entries`
loader does not group by subdir). Robust loading from the start (missing / non-UTF-8 / non-object row /
unreadable target → `bad_input`, never a traceback — the #225/#226 lesson). Self-exclusion via resolved-
path equality (`general_imposters`).

### M2 — embedding backend (optional, gated)

Swap the named-feature centroids for LUAR/style-embedding centroids (From-Text-to-Source style); needs the
style-embedding tier → gated/skipif like `voice_fingerprint`. Lands separately; M1 stands alone.

## Contract (the testable interface)

- **task_surface:** **new — `model_family_attribution`.** New surface → **both goldens** (caps 90→91 +
  count; labels 23→24 + count) — [[voiceprint-capability-golden-bump]].
- **CLI:** `python3 .../model_family_attribution.py TARGET (--reference-manifest F | --reference-dir D)
  [--ood-k X] [--margin X] [--min-docs N] [--min-words N] [--json] [--out F]`.
- **JSON envelope:** `build_output()` + `ClaimLicense`; `results` = `family_ranking` (raw similarities +
  per-family within_scatter + n_docs), `top_margin`, `out_of_distribution`, `attribution_available`,
  `reason`, `n_families`, `feature_set` (the resolved features), `target_words`, `calibration_status:
  "uncalibrated"`, `assumptions` (weak/low-dim + corpus-dependence + provisional thresholds). **No**
  `advisory_posterior`, `attributed_family`, `is_ai`, `source`, `verdict`, or `label` key.
- **Claim license — licenses:** "a raw, abstention-gated per-family similarity ranking of the target
  against operator-supplied reference corpora over standardized named features — weak, low-dimensional
  advisory evidence." **Refuses:** any "produced by <family>" attribution; any AI-vs-human ruling; any
  calibrated probability / normalized posterior; naming a family absent from the references. `uncalibrated`.
- **Gates:** the abstention gates above; self-exclusion; robust input (`bad_input`).
- **Paper trail:** fragment + `claim_license_surfaces` label + `changelog.d` (cites all three arXiv ids) +
  glossary pointer + both golden bumps + `gen_calibration_readiness`. Drift / docs-freshness / `pytest
  test_capabilities_dropin test_claim_license_surfaces` before push.

## Test contract (stdlib; torch-free; CI-runnable end to end)

`tests/test_model_family_attribution.py` — small synthetic per-family corpora (≥ MIN_DOCS_PER_FAMILY) with
distinct stylometric profiles →
- deterministic `family_ranking` (target drawn from family X ranks X top) over RAW similarities; **no key
  sums to 1** (assert no `advisory_posterior`).
- **no-verdict guard** — `not in results` for `attributed_family`, `verdict`, `is_ai`, `source`, `label`,
  `advisory_posterior`; `calibration_status == "uncalibrated"`.
- **abstention is real** — <2 families → unavailable; an OOD target (drawn from a 6th, unreferenced
  profile) trips the RELATIVE gate (not a fixed floor); a near-tie trips margin; a thin family (<min docs)
  trips the count gate; a `human`-would-be-top case abstains — each with its `reason`.
- **standardization** — a feature with a huge raw scale (MTLD) does not dominate the ranking (pin via two
  stub corpora differing mainly on a small-scale feature).
- **fixed subspace** — with mdd unavailable, target + refs are all compared without it (same feature_set);
  pin `feature_set` excludes `mean_dependency_distance` when spaCy is absent.
- **claim-license refuses-verdict** — `does_not_license` substring-refuses "produced by"/AI-human/
  posterior + the "names only supplied families" + "weak, low-dimensional" caveats.
- **robust input** — missing / non-UTF-8 / non-object reference row + target → `bad_input`.
- **self-exclusion** — the target dropped from its own family corpus.

## Calibration posture

Ships `uncalibrated`; `--ood-k` + `--margin` + `--min-docs` are PROVISIONAL. A labelled attribution corpus
would calibrate the *abstention thresholds* only (reported as provenance), never a shipped "attributed to
X" operating point. The default is, and stays, abstention-first advisory evidence.

## Out of scope / non-goals

- No "produced by family X" verdict, ever. No AI-vs-human ruling. No normalized posterior. No closed-world
  assumption — the relative OOD gate exists because the true source may be absent. M2 (embedding backend)
  lands separately. Never invents a family.

## Open questions

1. ~~advisory_posterior~~ **Resolved (P1): dropped.** Raw per-family similarities only.
2. ~~absolute OOD floor~~ **Resolved (P1): relative within-scatter gate** (`dist_to_top > k·within_scatter`).
3. ~~undefined similarity~~ **Resolved (P1): robust-z standardize (median/MAD over the pooled reference),
   then `1/(1+dist)`.**
4. **`human` reference** — default: allowed as a label but never the reported top (forces abstention).
   Confirm vs. disallowing it outright.
5. **Mahalanobis upgrade** — per-family covariance instead of a scalar within-scatter, once corpora are
   large enough; an M1.x upgrade, not v1 (small-n).

## Rework log (2026-06-19, after adversarial review → NEEDS-REWORK)

- **P1 posterior leak:** dropped `advisory_posterior`; raw un-normalized similarities only.
- **P1 fake OOD:** absolute floor → relative within-family-scatter gate; the surface gates on it.
- **P1 non-commensurate features:** robust-z standardization (median/MAD over pooled reference) before
  aggregation; rule specified.
- **P1 uneven mdd:** feature set resolved ONCE (intersection); spaCy-gated mdd uniformly in/out.
- **P2 human leak:** `human` may never be the reported top → abstain. **P2 small-n:** hard MIN_DOCS_PER_FAMILY
  + weak/low-dim caveat in output + license.
- **P3:** new `_load_family_dir` (subdir grouping); golden counts (caps→91, labels→24); license refusals
  are prose substrings (does_not_license is free text).
- **Path note:** references the `main` analogues (`general_imposters` self-exclusion, `idiolect_detector`
  loader, `variance_audit` features) — the originally-cited `originality_audit`/`cosine_explanation` live
  on unmerged branches and are not present when this surface builds from `main`.

## Fold log (2026-06-21, second-pass review → 5 P2 folds)

- **P2 min-docs floor was soft.** `--min-docs` was validated only `>= 1` and passed straight through, so
  `--min-docs 2` returned `attribution_available=True` for 3-doc families — defeating the small-n
  protection. Made HARD at the root: `rank_families` clamps `min_docs = max(MIN_DOCS_PER_FAMILY, min_docs)`
  (operator may only RAISE), the CLI clamps + warns on stderr, and the `--min-docs` help says so. Tests
  pin that the floor cannot be lowered below 5 (function + CLI) and that raising IS honored.
- **P2 standardization test was a tautology.** `test_standardization_prevents_mtld_domination` gave both
  families an IDENTICAL MTLD distribution, so MTLD cancelled between centroids and the test passed with OR
  without standardization. Rebuilt MTLD as discriminative-but-misleading (A~50, B~60; target raw-MTLD 58
  nearer B, fwr=A's) + a monkeypatched identity-scaler sub-assertion that the ranking FLIPS to familyB
  without standardization — the test now FAILS if standardization is removed.
- **P2 margin test co-tripped OOD.** `test_near_tie_trips_margin` put the target midway between two
  WELL-SEPARATED families, so it sat far from both centroids and tripped relative-OOD first;
  `attribution_available=False` was satisfied by OOD, not margin. Rebuilt as an IN-distribution near-tie
  (two families overlapping on a single axis with within-scatter ≫ centroid gap), asserting
  `out_of_distribution is False` and `'out-of-distribution' not in reason` so the margin gate is isolated
  and shown independently reachable.
- **P2 human gate defeatable by relabel.** The never-an-AI/human-verdict gate was an exact case-sensitive
  `== "human"` compare, so `Human`/`humans`/`human_writers`/`people` returned `attribution_available=True`
  with that label as the reported top. Replaced with `_is_human_label` (casefold + space/hyphen-normalize +
  a reserved synonym set); spec posture + tests pin that a relabel cannot route around it and that ordinary
  labels (`humane_llm`, `superhuman`) are not over-matched.
- **P2 length floor skipped the target.** The advertised `length_floor_words: 50` floor was applied only to
  reference docs; a 3-word target produced a full ranking. The same `--min-words` floor now guards the
  target: a sub-floor target is forced to abstain with an explicit too-short reason + warning +
  `target_below_min_words` flag (ranking stays raw evidence, per the abstention-first posture).
