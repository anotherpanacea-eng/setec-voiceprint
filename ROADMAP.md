# setec-voiceprint: Roadmap

The architectural narrative and the path from MVP to validated framework. Internal working notes (session logs, design discussions, private corpus references) live separately.

## Status reconciliation (2026-06-17)

Supplements the two passes below for the **1.117.0** release. The plugin is now at **1.117.0**. Where this conflicts with the earlier reconciliations or the inline markers, this is authoritative.

### Shipped since 1.116.0

- **ArgScope grows two increments on the `argument_decision_audit` surface — B5 (collapse dynamics) and C0 (register-baseline plumbing).** B5 adds a fifth bundle, `B5_collapse_dynamics`, carrying two arc-level signals the per-paragraph {role, mode} schema can't express: `disappearing_guard_flag` (a claim hedged early then treated as unguarded later) and `discounting_straw_men_flag` (the strongest text-internal objection left un-engaged). Both are `heuristic`, directional, with **no numeric anchor**, **excluded from the aggregate and the verdict band** (the score is numerically unchanged) — they describe texture, not fairness or soundness. C0 adds `--register` / `--baseline-dir` plumbing reading `baselines/argument_register_baselines.yaml` (seed: one genre, `op-ed`, at `literature_anchored`, so the band stays `uncalibrated` and **no verdict changes**); it ships the schema so the corpus-built `empirically_oriented` / `calibrated` rows (C1/C3) drop straight in. Both envelope changes are additive (schema 1.0).
- **`narrative_decision_audit` (StoryScope) adds `setec-voicewright` as a consumer.** A consumer-membership change only — the surface, its judge, its schema (1.0), its claim-license, and its `min_setec_version` floor (`1.107.0`) are all unchanged. This makes setec-voicewright the second consumer of the StoryScope work-level narrative diagnostic (read-only over an assembled draft, never a selection/training target), picked up on this v1.117.0 release.
- **LLM-judge provider plumbing deduplicated into a shared `judge_backends.py`.** `narrative_judge` and `argument_judge` each carried near-identical `_api_judge_{anthropic,openai,gemini}` adapters; the provider plumbing now lives once in `judge_backends.make_api_judge`, parameterized per family, so the two families' data contracts stay decoupled while the plumbing can't drift again (the parity gap behind #194). No behavior change. (The 2026-06-13 section below already mentions `judge_backends.py` under repo-hygiene; it actually landed in this v1.117.0 window.)
- **Fleet release-train runbook + release/sync infra.** `references/fleet-release-runbook.md` documents the four-repo producer-before-consumer release sequence; `v*` tags now auto-publish a GitHub Release so the consumer weekly-sync workflows can resolve `latest`; `calibration_survey` and `check_corpus` gain corpus-scale resume (`--survey-cache` / `--records-cache`); `validation_harness` warns on un-checkpointed bootstrap.
- **Released as v1.117.0** (2026-06-17), the consolidated release cutting the above.

The two "What's left" lists below otherwise still stand: R12 calibration, the AIC-8/9 thresholds, the operator-blocked cascade, and the F-bis compression-polarity mirror reconciliation are unchanged.

## Status reconciliation (2026-06-19)

Supplements the 2026-06-17 pass above for the **post-1.117.0** window — a large arXiv-capability-review build batch plus a refreshed planned/horizon list. The shortlist provenance is `setec-scratch/arxiv-capability-review/` (every arXiv id below verified real against the arXiv API). Where this conflicts with older sections, this is authoritative. **No-verdict posture is unchanged:** every detector-flavored item below emits descriptive per-signal values/bands, never a label.

### Shipped since 1.117.0

These all merged to `main` this cycle (version + changelog cut at the next release per the repo's release-not-PR convention):

- **T-Detect Student-t tail normalization** — `--tail student-t` / `curvature_t` on `discrimination_curvature` (PR #228, spec 25; arXiv:2507.23577). Heavy-tail-aware curvature normalization, stdlib over the existing logits.
- **Rank-Turbulence Delta** — `rank_turbulence_audit` on the `voice_coherence` surface (PR #226, spec 23 M1; arXiv:2604.19499). Token-decomposed, interpretable per-word divergence.
- **Function-word adjacency network (FWAN)** — `function_word_adjacency_audit` on `voice_coherence` (spec 32 M1, 2026-06-20; arXiv:1406.4469). The GRAPH-structure read of the function-word transitions `function_word_grammar_audit` reads as flat top-20 bigrams: node centralities (degree + PageRank), per-node / global transition entropy, directionality (reciprocity / weight asymmetry), density, and directed motifs (2-cycles, length-3 walks, self-loops). M1 stdlib + numpy, **no networkx**, no model. Reuses the grammar audit's run-segmentation (single source of truth — `total_transitions` ties to the run total, NOT the truncated `function_bigrams` field). No-verdict: descriptive structure-concentration band from NAMED provisional signals + `calibration_status` (n_calibrated 0), **no bare `band.score`**, band suppressed below the transition floor; the paper's attribution classifier is deliberately NOT reproduced. Optional spaCy POS-disambiguated node set flagged as future M2.
- **Dependency-distance distribution** — `dependency_distance_audit` on `voice_coherence` (PR #227, spec 24; arXiv:2211.14620). Interpretable syntactic-shape geometry over the existing parse. **Curve-shape descriptors** (spec 31, 2026-06-20): an additive `results.shape` block — population variance/sd, Fisher-Pearson skew (g1) and excess kurtosis (g2), and p50/p90/p99/max tail quantiles of the pooled per-link distances; the geometry of the curve (distinct from `mdd_sd`), stdlib, no-verdict/no-band. Baseline-relative DDD distance + parametric curve fit flagged as future M2.
- **Originality / DJ-Search** — `originality_audit` on `set_level_diversity` (PR #225, spec 22 M1; arXiv:2410.04265). Reconstructibility/novelty vs the impostor reference pool, stdlib.
- **Cross-document homogenization & originality** — `corpus_novelty_audit` + `skeleton_overlap_audit` on `set_level_diversity` (spec 28 M1, 2026-06-20; arXiv:2410.04265 / 2504.09373). Set-wide DJ-Search novelty *distribution* (leave-one-out, no aggregate score) + a model-free QUDsim-style cross-document discourse-skeleton overlap matrix (topic-robust, stdlib `difflib`). No-verdict, descriptive-only, never a selection signal; M2 LLM-parsed QUD lens gated/POC-pending.
- **Distinct-cluster diversity (NoveltyBench)** — `distinct_diversity_audit` on `set_level_diversity` (**PR — OPEN, awaiting Codex review; not yet merged**; spec 33 M1; arXiv:2504.05228). Partitions a prompt-matched response pool into lexical near-dup equivalence clusters (word-shingle Jaccard + single-link union-find, stdlib, no numpy) and reports the *partition*: distinct-cluster count, cluster-size distribution, `distinct_ratio`, a utility-weighted distinctness, and one positional representative per cluster. The distinct-output axis of set diversity (orthogonal to `homogeneity_audit`'s continuous pairwise-cosine and `skeleton_overlap_audit`'s structural template overlap). **Never a single "diversity score"**; no-verdict, no band, never a selection signal; the learned/embedding deduper is the gated M2 `--lens model-dedup` seam.
- **Per-feature cross-document novelty profile (GENIE axis)** — `cross_doc_novelty_profile` on `set_level_diversity` (spec wave-4, M1, 2026-06-23; arXiv:2606.12790 / 2504.05228). For ONE target vs a reference pool, reports a per-feature mean/SD z-position + per-family |z| distribution summary over the 7 `extract_features(include_spacy=False)` stdlib families — the feature-wise COMPLEMENT to `distinct_diversity_audit`'s cluster-wise read. M1 model-free (stdlib z arithmetic, no torch/spaCy, CI-runnable); no verdict, no band, no single score; calibration_status: provisional. Robust median/MAD z + embedding M2 lens both explicitly deferred.
- **Embedding-explanation** — `cosine_explanation` on the `embedding_explanation` surface (**PR #231 — OPEN, awaiting Codex re-review of a P2 fix; not yet merged**; arXiv:2510.05362 / 2409.07072). Named side-by-side for the LUAR cosine; the opaque embedding distance becomes human-checkable.
- **ArgScope fallacy scan** — `fallacy_scan` on `argument_pattern_scan` (PR #229, spec 26 M1; arXiv:2202.13758 / 2406.12402). Candidate rhetorical-move flags as a descriptive tally.
- **ArgScope warrant probe** — `warrant_probe` on `argument_pattern_scan` (PR #230, spec 26 M2; arXiv:2412.15177). Toulmin critical-question coverage.
- **ArgScope B5 collapse-dynamics** — disappearing-guard + discounting-straw-men flags on `argument_decision_audit` (PR #217 / #215, spec 21; arXiv:2606.01736 / 2406.12402). Heuristic, directional, excluded from the aggregate and verdict band (already noted in the 2026-06-17 pass; the discounting-straw-men flag is cited to arXiv:2406.12402).
- **LambdaG grammar likelihood-ratio** — `lambdag_audit` on `voice_coherence` (spec 32 M1; arXiv:2403.08462). Model-free n-gram LM over POS sequences scoring a query's grammar log-LR against a reference-author vs background pair (the likelihood-ratio sibling of Burrows Delta). Stdlib (the spaCy POS parse is the only model-gated step; abstains without it); signed log-LR + a 3-level PROVISIONAL *leaning* band, **no same/different-author verdict**, never ranks authors, held-out-disjoint anti-Goodhart guard. M2 (richer/learned POS alphabet + KN smoothing) deferred behind a lazy import.

### Planned / horizon (spec'd or shortlisted, not built)

From the arXiv capability review. Detector-flavored items stay descriptive/advisory — values and bands, never a verdict.

**New axes (capabilities SETEC structurally lacks):**

- **Model-family attribution** ("which supplied family does this read most like?") — NEW axis; spec 34 (authored as spec 28, renumbered off the 28-collision and then off a later 32-collision to `34` — the next slot free against current main and open spec branches; PROVISIONAL until gate-pass). Adversarially reviewed → reworked: the normalized posterior was **dropped** (it manufactures a P(family) reading), OOD is now **relative** (per-family within-scatter, not a fixed floor), features are **robust-z standardized**, and the comparison subspace is resolved **once**. **M1 BUILT** (`model_family_attribution`, new `model_family_attribution` surface; PR open): a raw, un-normalized, abstention-gated per-family similarity *ranking* over the standardized `variance_audit` named features (burstiness_B / MATTR / MTLD / function-word ratio / mdd, mdd spaCy-gated), stdlib/CI-runnable, `status: heuristic` / uncalibrated. No posterior, no "produced by X" verdict, no AI-vs-human ruling (`human` may never be the reported top → abstain); self-excluding; abstains on < 2 families / thin family / relative-OOD / low margin. M2 = LUAR/style-embedding centroids, gated. arXiv:2309.13322 / 2504.11369 / 2410.16107.
- **Hivemind / cross-document homogenization** — `homogeneity_audit` (pool pairwise-cosine + effective modes) plus single-doc hivemind-proximity; **M1 stdlib local-stylometric metric SHIPPED (spec 30, no band, no verdict, status: heuristic)**, M2 LUAR / text-embedding-3-small semantic lens POC-gated (deferred). arXiv:2510.22954 / 2504.09373 (QUDsim).

**Shortlist tail (A1–A6):**

- **SpecDetect + Lastde/Lastde++** — spectral / time-series read of the surprisal vector, orthogonal to `surprisal_audit`; stdlib over existing logits. arXiv:2508.11343 / 2410.06072.
- **StyleDistance / Multilingual-UAR** — stronger style encoder than Wegmann/LUAR for `voice_fingerprint`; drop-in, GPU. arXiv:2410.12757 / 2509.16531.
- **Watermark probe** — KGW green-list z-test, "was this watermarked?"; the first watermarking axis in SETEC. **M1 BUILT** (`watermark_probe`, spec 29; model-free stdlib z-test + p-value + transform-safe `neg_log10_p`, two-tier PROVISIONAL band `under_powered`/`watermark_consistent`, no verdict field, keyed/never-sniffed). A positive is watermark-consistent with the named scheme, never "AI"; **absence ≠ human** (blind to other & semantic & scrubbed watermarks). M2 (gated multi-key sweep convenience) is optional polish. arXiv:2301.10226 / 2306.04634 / 2411.13425.
- **GAQCorpus argument-quality** — theory-based per-dimension (logic/rhetoric/dialectic) band vector complementing `argument_decision_audit`. arXiv:2006.00843.
- **LLM-as-verifier authorship** — one advisory authorship signal, not a verdict (offline InstructAV / CAVE variants). arXiv:2403.08213 / 2407.12882 / 2406.16672.
- **RAID + DIPPER robustness fixtures/harness** — expand the adversarial fixture taxonomy + a recursive-paraphrase stress harness. arXiv:2405.07940 / 2303.13408. **Detector-level AUC-degradation harness M1 BUILT** (`paraphrase_robustness`, spec 33; model-free orchestration over INJECTED detector scores — per-(detector × rung) discriminative AUC, sign-oriented + sign-pinned, TPR at FPR {0.05, 0.10}, and Δ from rung 0; no aggregate robustness scalar, no verdict, no threshold; `status: empirically_oriented`, `calibration_status: heuristic`). Measures corpus-level AUC degradation under a paraphrase attack — the population-ranking counterpart of the per-(signal × rung) single-text decay curve. The GPU detector re-scoring (Binoculars v2 / surprisal / curvature) and the neural attack (back-translation primary; DIPPER conditional, VRAM-gated) are the M2 seam. arXiv:2303.11156 (Sadasivan TV bound). Sibling `paraphrase_ladder` (spec 16, single-text decay) remains on `feat/raid-dipper-robustness`, unmerged.
- **Neurobiber 96-feature family** — fast Biber features as a new `voice_profile` family (dual-use with model attribution). arXiv:2502.18590.
- **GECScore** — black-box grammar-error-count signal; **gated behind `fairness_dialect_guardrails`** (inverts on ESL/dialect prose). arXiv:2405.04286.
- **FWAN** — function-word adjacency networks; **deferred, overlaps `function_word_grammar_audit`**. arXiv:1406.4469.
- **Gram2Vec interpretable vectorizer** — `style_vectorizer` on `voice_coherence`. **M1 BUILT** (spec 30; stdlib named-feature vector reusing `stylometry_core.extract_features(include_spacy=False)` — function words / char n-grams / punctuation / paragraph-dialogue / pronoun-modal-negation). Glass-box: every dimension is a human-named feature; emits **no aggregate scalar at all**, so there is structurally nothing to threshold or rank on (the strongest no-verdict guarantee). Single mode = full inventory (all 135 function words, no cap); `--baseline-dir` adds a per-dimension reference distribution + a PROVISIONAL band (mean ± k·sd), held-out disjoint. M2 (follow-up) adds the two spaCy-gated families (`pos_trigrams` / `dependency_ngrams`) behind `--with-spacy` — named dimensions only, no verdict. arXiv:2406.12131.
- **Glimpse / LambdaG** — interpretable stdlib companions to Delta / white-box-on-API-logprobs. arXiv:2412.11506 / 2403.08462. (**LambdaG M1 BUILT** — `lambdag_audit`, spec 32; see the Shipped section above.)

**Eval-discipline / anti-Goodhart hardening (protocol upgrades, not surfaces) — _M1 in-progress (spec 28, branch `feat/eval-discipline-bundle`)_:**

- **Topic-leakage controls** — topic-controlled splits so style signal isn't topic leakage. arXiv:2104.08530 / 2407.19164. _M1 done: `topic` manifest field + `topic_disjoint_split` + `topic_leakage_diagnostic` + `--topic-split` in `validation_harness`._
- **Multiscale conformal FPR bound** — explicit FPR upper-bound mode for `conformal_gate`. arXiv:2505.05084. _M1 done: `threshold_at_fpr_bound` + `--fpr-bound` (single-scale ceiling; multiscale/Mondrian is the named follow-on)._
- **Local-Bayesian likelihood / Simpson calibration** — detect-and-refuse when a pooled AUC ranking inverts within strata. arXiv:2605.06294. _M1 done: `simpson_inversion_check` + `--simpson-check FIELD` in `validation_harness` (consumes per-stratum scores; emits its own refusal — **not** a `surprisal_audit` change)._
- **Short-PHD** — stabilizes intrinsic-dimension reads on short texts. arXiv:2504.02873. _M1 done: `estimate_phd_short` + `audit(short_text_mode="auto")` in `intrinsic_dimension_audit` (default-preserving on long text)._
- **"Don't over-claim separability" guardrail doc** — the theoretical reliability bound; belongs in the posture docs. arXiv:2303.11156. _M1 done: `references/POSTURE_no_overclaim_separability.md` + the structural absence test._

## Status reconciliation (2026-06-13)

Supplements the 2026-06-06 pass below for the **1.110 → 1.116** window. At the time of this pass the plugin was at **1.116.0** (the 2026-06-17 section above carries it to 1.117.0; the 2026-06-06 section below still says 1.109.x). Where this conflicts with the older reconciliation or the inline markers, this is authoritative.

### Shipped since 2026-06-06

- **The normalized-entrypoint consumer contract (R1–R5).** `capabilities.py emit --json` (the whole manifest as one machine-readable document with a top-level version floor), the `setec_run.py` dispatcher (`setec run <surface> --json` — resolves a surface, enforces its `min_setec_version` floor + Python deps, and guarantees a `schema_version: 1.0` envelope on stdout), the R3 structured error model (`available:false` + branchable `reason_category`), the R4 output-validity bounds gate at the `build_output()` boundary, and R5 golden contract fixtures (`references/contract_fixtures/` + a vendorable `fake_setec.py` + a fixture-drift gate wired into the drift linter). **Two downstream consumers now ride it:** apodictic (nine subprocess surfaces) and setec-voicewright (four more surfaces promoted to the bundle).
- **ArgScope Increment A1 — a new surface family.** `argument_decision_audit.py` + `argument_judge.py` + `argument_feature_schema.py` score how an *argument* is built (B1 paragraph-role transition arc + B2 discourse-mode mix) against Kim et al. 2026, plus the judge-free `argmove_profile.py` separation baseline (the new `assertoric` surface). Ships unconditionally `uncalibrated`, register-bound to op-ed, apodictic-consumed. The argument-domain sibling of Surface 6.
- **Fiction impostor-pool acquisition tooling landed.** `acquire_epub.py` (#141) and `acquire_manuscript.py` for `.docx`/`.md`/`.txt` (#142) are now **merged to `main`** with tests — this updates "What's left" item 2 below, which still describes them as pending PRs. Assembling the pool + the fiction calibration spine remain.
- **Repo-hygiene / dedup.** Shared `judge_backends.py` (provider plumbing for both judge families), shared `tools/r1_bundle.py` field-bundle validator, and CI now runs the consistency gates (capabilities-drift / docs-freshness / calibration-readiness), not just pytest.
- **Releases cut 1.110 → 1.116.0**, the latest being the v1.116.0 consolidated release that cuts the ArgScope A1 `min_setec_version` floor.

The 2026-06-06 "What's left" list below otherwise still stands: R12 calibration (item 1), the AIC-8/9 thresholds (item 3), and the operator-blocked cascade (item 4) are unchanged.

## Status reconciliation (2026-06-06)

A full pass over the 1.42 → 1.109 CHANGELOG and the shipped script catalog, reconciling this document against the code. **Where the inline status markers further down conflict with this section, this section is authoritative** — many of them predate the work below. The plugin is at **1.109.x**; the framework has shipped well past the point most of this document describes.

### Shipped since this roadmap was last current

- **The entire paired-release spine (Releases 1–11) shipped** — every Tier-1/2/3 surface *and* every Tier-1/2/3 trustworthiness guardrail. On disk: `paragraph_audit`, `discourse_move_signature`, `agency_abstraction_audit`, `punctuation_cadence_audit`, `stance_modality_audit`, `function_word_grammar_audit`, `construction_signature_audit`, `phraseological_signature_audit`, `mimicry_cosplay_audit`; and `register_classifier`, `confounder_audit`, `surface_disagreement_resolver`, `evidentiary_conditions_gate`, `adversarial_robustness_card`, `controls_audit`, `calibration_drift_monitor`, `fairness_dialect_guardrails`, `semantic_preservation_check`, `known_editor_profile`, `draft_history_analysis`, plus source-of-smoothing localization and the revision-risk model inside `restoration_packet`.
- **Release 12 (Semantic Trajectory Audit) is shipped as code** — `semantic_trajectory_audit.py` (~1,110 lines: SBERT windowing, trajectory/drift/autocorrelation/flatness stats, baseline-comparison mode, `--dtype/--device`, full test file). Its banding ships PROVISIONAL; **calibration is the only remaining R12 work** (see "What's left").
- **Post-R12 items A, B, C all shipped.** (A) README "Why no verdict" section is live. (B) The Costa-derived authorship-state taxonomy shipped end-to-end — spec (`internal/SPEC_authorship_states.md`), `manifest_validator` `authorship_state` field, and B.3 per-state claim-license routing across all 10 `claim_license`-using scripts (waves 1–4). (C) The DivEye surprisal signal shipped — `surprisal_audit.py`, `surprisal_backend.py`, Tier-4 wiring in `variance_audit`, a dedicated `surprisal` dependency tier, and calibration-pipeline integration.
- **The Phase 7+ "horizon" cross-perplexity surface shipped as Surface 5** — `binoculars_audit.py` + `binoculars_calibrate.py`, uncalibrated by default.
- **A sixth surface that postdates this doc: narrative decision audit** — `narrative_decision_audit.py` + `narrative_judge.py` + `narrative_feature_schema.py` (Surface 6), plus `aesthetic_authority_audit.py` (AIC-8 prestige-metaphor + AIC-9 kicker composite).
- **Calibration corpus fetchers shipped** — RAID, MAGE, Pangram EditLens, and Brysbaert concreteness norms, each with a `*_to_manifest.py` adapter; sharded calibration toolchain; per-comparator-class + per-(judge × generator) routing.
- **A discoverability layer that postdates this doc** — `capabilities.py` manifest + query CLI + drift linter + the APODICTIC handoff contract (v0.3, in `Unreleased`).

### What's left

1. **R12 calibration — actionable now (not on Mac).** The trajectory *tool* is done; its PROVISIONAL bands need the §6.4 fixture suite run against a real baseline to anchor thresholds. This needs the SBERT/GPU calibration host — the "not on Mac" constraint.
2. **Fiction impostor pool** (next concrete acquisition target — the EPUB-extraction tooling is implemented in **pending PRs not yet merged to `main`**: `acquire_epub.py` (#141) + `acquire_manuscript.py` for `.docx`/`.md`/`.txt` identity baselines (#142). What remains is landing those, then *assembling* the pool) → **fiction calibration spine** (six research-grade deliverables, gated on the pool).
3. **AIC-8/9 calibration corpus** — detectors shipped; thresholds still `provisional`. Plus the standing 6-month AIC-8 embedding-model re-evaluation tickler.
4. **Post-1.101 cascade, operator-blocked only** — E.1 per-GPU parallelism (justify-blocked), F.1 populate the 13 RAID `comparator_dependent` cells (data-blocked), F.3 + G.1–G.3 operator data work. All framework-side infrastructure (D, E.2, E.3, F.2) shipped.
5. **Tier-4 / Release-13+ research items (both tracks converge here)** — Counterfactual editing sandbox, House-style vs. author-voice decomposition, Multi-author/multi-source segmentation, Transformation-profile learning. Interactive report UI remains indefinitely deferred.
6. **Open architectural / calibration-against-labeled-corpus questions** — see the final section (configurable `COMPRESSION_HEURISTICS`, threshold calibration for the 0.7 directional-cluster and the 0.15/0.40 band fractions, the POS-bigram KL smoothing constant, the missing dosage signal).
7. **2.0 horizon** — the Compression-of-Choice / Stylistic Choice Entropy refactor. Architectural, not a 1.x deliverable.

## Current state

The framework ships a three-layer architecture (Layer A distributional diagnostics, Layer B AIC pattern flags, Layer C source triage), six task surfaces (smoothing diagnosis, voice coherence, validation + calibration, craft restoration, Surface 5 discrimination evidence shipped uncalibrated by default — Binoculars two-model perplexity + the SETEC external-mirror methodology + Tier-4 surprisal — and Surface 6 narrative decision audit), a full interpretation/trustworthiness layer (confounder audit, register conditioning, evidentiary-conditions gate, surface-disagreement resolver, negative/positive controls, ablation, calibration-drift monitor, fairness guardrails) plus a capabilities discoverability layer, a script catalog spanning all surfaces (full per-script catalog at `plugins/setec-voiceprint/scripts/README.md`), and a reference-documentation tree under `plugins/setec-voiceprint/references/` (signals glossary + per-layer math + per-corpus calibration findings + craft-pattern references).

What is shipped:

- **Layer A scripts.** `variance_audit.py` (single-document distributional diagnostic with sliding-window mode), `sliding_window_heatmap.py` (renders sliding-window output as a localization heatmap; cathedral upgrade #5 finisher, shipped 1.29.0), `manuscript_audit.py` (cross-chapter aggregate), `repetition_audit.py` (vocabulary over-representation), `manuscript_repetition_audit.py` (manuscript-aggregate habit vocabulary), `chapter_distinctiveness_audit.py` (leave-one-out internal-baseline distinctiveness), `bigram_diff.py` (per-bigram POS-bigram diff: target vs. cluster, with both pooled-counts and per-file-mean aggregation), `manuscript_bigram_diff.py` (corpus-vs-corpus aggregate-level POS-bigram diff with the same aggregation toggle).
- **Layer B/C script.** `aic_pattern_audit.py` (named-pattern density audit covering negation hedge, disguised correctio, pseudo-aphorism, manifesto cadence, triplet, professional-parallel stack, and the four nonfiction parallel patterns: false-balance, hedge-and-affirm, recommendation template, authority laundering). Optional baseline-dir comparison flags densities exceeding the writer's voice envelope. Layer C earned/unearned verdicts remain the writer's call per instance; the script surfaces candidates and density.
- **Voice-coherence scripts.** `voice_distance.py` (target-vs-baseline distance with feature-cluster mode), `voice_profile.py` (private voiceprint), `idiolect_detector.py` (keyness/collocation extraction and preservation lists), `stylometry_core.py` (shared feature extraction).
- **Validation scripts.** `manifest_validator.py` (schema and integrity checks for `corpus_manifest.jsonl`), `check_corpus.py` (content-level non-prose contamination gate), `adversarial_fixtures.py` (deterministic public stress-fixture transforms), and `validation_harness.py` (MVP empirical validation for smoothing-diagnosis scores over labeled manifest entries).
- **References.** Layer A math (`distributional-diagnostics.md`), Layer B flag families with genre tolerance table (`aic-flags.md`), Layer C source triage (`source-triage.md`), figure-by-flag countermoves (`rhetorical-countermoves.md`), and implementation/dependency survey notes (`implementation-survey.md`).

Every script's JSON output carries a `task_surface` tag so downstream consumers can route by surface. The framework refuses the unifying "is this AI" verdict; the math does not entitle it.

## Architecture: MVP to cathedral

The framework currently sits at MVP: it answers "how far is this draft from this baseline?" given a baseline corpus and a target document. Cathedral status would answer the grown-up version: given the right comparison set, length, register, time period, and known failure modes, what can be responsibly inferred, how confident, where in the text the signal lives, and what the practical revision decision is.

The epistemic shift is the load-bearing claim. Cathedral status does not mean "the tool can prove AI." It means every output knows what comparison it is making, what it cannot know, and what practical revision decision follows. Numbers are subordinate to the claim; the claim is subordinate to the comparison; the comparison is subordinate to the manifest.

### Eight cathedral upgrades

The substantive design moves the roadmap is organized around:

1. **Manifest as law, not convenience.** Every tool reads from `corpus_manifest.jsonl`; no serious run uses loose directories. The manifest gets validation: missing files, bad labels, AI-contaminated baseline entries, register mismatches, privacy violations. Status: `manifest_validator.py` shipped; wiring into manifest-consuming scripts is the next step.

2. **Length-matched bootstrap.** Instead of comparing a 300-word target to 8,000-word baseline files, sample hundreds of 300-word windows from the baseline and report where the target falls. Empirical percentiles replace noisy z-scores. Status: **shipped** — `length_bootstrap.py` plus `--bootstrap` on `variance_audit.py` and `voice_distance.py` (see Phase 1 step 3 for detail). Per-family Burrows Delta bootstrap is the heavier follow-up.

3. **Validation harness.** Labeled test set with known-human, known-AI, AI-edited, mixed, paraphrased, and human-revised-after-AI samples. Per-register thresholds with FPR/FNR/ROC/PR and confidence intervals. Status: **shipped** for smoothing-diagnosis (`validation_harness.py`) and voice-coherence (`voice_validation_harness.py`), with adversarial-class fixtures and per-signal robustness cards (`adversarial_robustness_card.py`). What remains is calibration breadth — more signals × more corpora.

4. **Impostor baselines.** Compare the target writer against plausible other writers in matched registers. Without these, the voiceprint over-attributes register and topic to identity. Status: **shipped end-to-end.** Impostor-corpus schema (1.14.3), acquisition tooling for blogs / Blogger Takeout / online magazines / PDF libraries (1.15.0–1.19.0), and the General Imposters validation harness `scripts/general_imposters.py` (1.28.0) — given a target text and a candidate writer's identity baseline + impostor pool in matched register, the GI bootstrap reports the proportion of iterations the target falls closer to the candidate than to any impostor, with a Kestemont-2016-style gray-zone refusal in [0.20, 0.80]. Personal pre-AI baseline assembly is documented in `scripts/calibration/PROVENANCE_TEMPLATE.md` (1.29.0).

5. **Sliding-window localization.** Whole-chapter distance is blunt. Cathedral version says "the drift is concentrated in paragraphs 12-19, mostly function words and sentence cohesion" with a heatmap. Status: **shipped** end-to-end. Sliding-window mode in `variance_audit.py` produces per-window band classifications; `sliding_window_heatmap.py` (1.29.0) renders them as a markdown localization map with sparkline, band tape, hot-zone summary, per-signal × per-window grid, and claim-license block.

6. **Voice profile expansion.** Add idiolectic phrase extraction, collocations, sentence-shape distributions, readability spread, MTLD/MATTR/Yule ranges, time drift, POV-specific profiles, and a "do not normalize these phrases" preservation list. Status: core profile shipped in `voice_profile.py` with function-word, character-n-gram, punctuation cadence, paragraph/dialogue, and pronoun-modal-negation features. Idiolect extraction shipped as `idiolect_detector.py`. Time-drift tracking (`voice_drift_tracker.py`) and POV-specific profiles (`pov_voice_profile.py`) **both shipped.** Status: **shipped.**

7. **Before/after restoration loop.** Run a draft, revise, rerun, and compare whether the changes restored voice or just gamed the metrics. Without this loop, the tool eventually teaches metric-chasing. Status: **shipped.** `before_after_restoration.py` runs the loop with a metric-gaming heuristic; `restoration_packet.py` translates diagnostic outputs into revision-safe prompt targets with a targetability taxonomy and a required SETEC post-check.

8. **Privacy and packaging guards.** The system refuses to export private baselines, voice profiles, and idiolect preservation lists into publishable plugin folders. Status: `voice_profile.py` and `idiolect_detector.py` refuse output paths outside `ai-prose-baselines-private/` unless `--allow-public-output` is passed; `manifest_validator.py` enforces a privacy ratchet on `voice_profile`- and `idiolect`-tagged entries.

### Phase 1 to Phase 2 operational sequence

The structural backbone for the validation spine. **All six steps shipped as of 1.30.0.** What remains is calibration breadth (more signals × more corpora) and adversarial-class expansion in step 4 — both follow-up tracks rather than spine work.

1. **`manifest_validator.py`.** Schema and integrity checks on `corpus_manifest.jsonl`. Refuses runs that depend on a contaminated or contradictory manifest. Status: **shipped** (now also includes the `language_status` field with an ESL ratchet on `use: baseline` and `use: voice_profile` entries; see "ESL handling" below).
2. **`task_surface` field in every script's JSON output.** Surface separation enforceable in code rather than vigilable by humans. Status: **shipped.**
3. **Length-matched bootstrap** for `voice_distance.py` and `variance_audit.py`. Replaces noisy z-scores at small N with empirical percentiles drawn from length-matched windows of the baseline corpus. Status: **shipped end-to-end.** `length_bootstrap.py` houses the sampler + bootstrap helpers (built on SciPy's `scipy.stats.bootstrap`); `variance_audit.py --bootstrap` produces per-signal length-matched percentiles + BCa CIs against the baseline corpus; `voice_distance.py --bootstrap` (1.30.0) adds the same shape for the function-word distance, replacing the unanchored "is this Delta large?" question with a calibrated percentile against baseline-window distances at the target's word count. Per-family Burrows Delta bootstrap (full feature-extraction caching path) is the heavier follow-up.
4. **`validation_harness.py`.** Reads the validated manifest, runs labeled samples through the surface-tagged scripts, reports performance by task surface × register × length × AI status × language status. The harness's report template makes the operating-point assumption explicit: it refuses to publish a single aggregate accuracy number absent a stated FPR target, with a recommended 0.01% FPR threshold for student-facing or accusation-grade deployments where the cost of a single false positive dwarfs the cost of a missed AI essay. Status: **MVP shipped for `smoothing_diagnosis`** with paired bootstrap CIs for ROC AUC / average precision; next pass adds per-signal evaluation, voice-coherence evaluation, and adversarial-class fixtures.
5. **Report template: "what this result licenses / does not license."** Every harness output carries an explicit licensing block: inputs, comparison set, length range, register match, language match, confidence interval, FPR target, and the specific claim the result does and does not entitle. Status: **shipped end-to-end.** `scripts/claim_license.py` (1.29.0) houses the `ClaimLicense` dataclass + `render_block()` + `from_legacy()`. As of 1.30.0 every surfacing harness — `sliding_window_heatmap.py`, `validation_harness.py`, `voice_validation_harness.py`, `general_imposters.py` — renders the structured block in its markdown report. The legacy dict-shape `claim_license` field is preserved in JSON output for backward compat with downstream consumers.

6. **POS-bigram KL participates in the band classification when a baseline is supplied.** `variance_audit.py` now incorporates the baseline-relative KL signal into its compression-fraction band call, with threshold 0.15 (literature anchor), weight 2.0 (matching `burstiness_B` and `connective_density`), and length floor 500 words. Surfaced prominently in the headline output. Empirical motivation: on AI-composed prose where every variance metric reads inside human bounds against the writer's pre-AI baseline, POS-bigram KL is often the single signal carrying the syntactic-template-collapse evidence; previously the band call ignored that signal and the headline read as clean. Status: **shipped.** Weight and threshold both calibration-pending against the validation harness on a labeled corpus.

### Corpus hygiene safeguards

Layer A scripts silently accept whatever the input file contains, and spaCy will POS-tag CSS, HTML, JavaScript, fenced code blocks, and ASCII tables as if they were prose. A 2026-05-08 session surfaced this empirically: a WordPress essay with embedded styled-HTML scaffolding (interactive Reading-Mode toggle widget, ~1,150 words of CSS) produced KL = 0.41 against a register-matched baseline; the same essay with the code stripped produced KL = 0.10. The over-represented bigrams in the contaminated version were CSS rule structure (`PUNCT+PUNCT`, `PUNCT+SYM`, `SYM+NOUN`, `PUNCT+NUM`) rather than prose syntax. A user reading the headline KL alone would have flagged a clean essay as 4× more AI-shaped than its peers.

Two concrete safeguards close the gap:

- **Script-level preprocessing.** `variance_audit.py` and `stylometry_core.py` strip `<style>...</style>`, `<script>...</script>`, fenced code blocks (` ``` `), loose CSS blocks, JSON-shaped `{...}` blocks, conservative HTML tags, ASCII tables, and YAML front matter before tokenization. The script emits a "stripped N tokens of suspected non-prose" warning so users know the cleanup happened, records per-rule counts in JSON, and supports `--allow-non-prose` for intentional opt-out. Catches the common cases (WordPress exports with embedded widgets, Markdown posts with code samples, Substack drafts with raw HTML). Status: **shipped** for shared preprocessing and symmetric baseline application; KL threshold recalibration remains pending.
- **`check_corpus.py`.** A separate auditing pass that detects suspected non-prose contamination above a threshold and exits nonzero, with an explicit report of which files and which kinds of contamination were detected. Ships as a standalone command and as an importable function so the validation harness can gate manifest health on it. Pairs with `manifest_validator.py`: the validator catches schema and integrity issues; `check_corpus` catches content-level contamination the schema cannot see.

Status: both safeguards shipped as shared preprocessing plus the standalone `check_corpus.py` gate, with `validation_harness.py --check-corpus` as an opt-in preflight. The 2026-05-08 finding is the calibration evidence for both items. Now load-bearing: with POS-bigram KL participating in the headline band classification (Phase 1 step 6), contamination in either the input or the baseline shifts the band call rather than only a divergence footnote. The preprocessing guard graduates from defensive-polish to a precondition for the band claim to be defensible.

Symmetry requirement: any preprocessing rule applied to the target text must be applied to baseline files using the same rules. Otherwise the "did spaCy see prose" question is asymmetric across the comparison and KL readings drift in unpredictable directions.

### ESL handling

Non-native English prose sits in the same low-variance region of stylometric space as RLHF-aligned LLM output. Liang et al. (*Patterns* 2023) found average 61% false-positive rate on TOEFL essays across seven AI-prose detectors, and the field's most durable false-positive failure mode is ESL writing. Implications:

- The manifest carries a `language_status` field with values `native`, `non_native_advanced`, `non_native_intermediate`, `learner`, or `unknown`. `manifest_validator.py` warns when entries with non-native language status land in `use: baseline` or `use: voice_profile` for any voice-coherence-tagged downstream tool, because a baseline contaminated with ESL prose teaches the system that smoothing is part of the writer's voice.
- The validation harness reports a separate FPR slice for ESL entries. Aggregating native and ESL FPR into a single number masks the failure mode the field is most embarrassed by.
- The skill's claim-licensing language treats ESL writing as a corpus the framework cannot adjudicate: distributional compression in ESL prose is an artifact of the writer's English fluency, not provenance.

### Adversarial test classes for the validation harness

Beyond the basic known-AI / AI-edited / mixed split, the harness will evaluate against three adversarial families to be honest about the deployment surface:

- **Unicode-layer attacks.** Homoglyph swap and zero-width-space insertion exploit tokenization rather than semantics. RAID 2024 documents a 40%+ accuracy drop on five detectors against unnormalized homoglyphs. Defendable with Unicode normalization preprocessing. Status: **first public fixture slice shipped** (`scripts/test_data/adversarial/`) with `adversarial_class` metadata and harness slicing.
- **Paraphrase attacks.** DIPPER-class T5 paraphrasers (Krishna et al., NeurIPS 2023) drop classical detector recall by 60-90 percentage points. Labeled `use: validation` slice; per-detector TPR at the chosen FPR.
- **Humanizer tools.** Commercial humanization services (StealthGPT, UndetectableAI, Quillbot) are pre-baked smoothing-reversal pipelines that target distributional signals directly. Pangram retrains continuously against this class; SETEC's calibrated thresholds will need similar attention.

Each adversarial class is a labeled `use: validation` slice with explicit `notes` provenance. The harness refuses to mix scores across classes and reports per-class TPR independently.

### Metric-targeted restoration packets

The before/after restoration loop needs a translation layer between "the diagnostic signal moved" and "revise this passage." Some signals are direct craft targets; some are only promptable after translation; some are poor direct targets and should trigger a deeper causal read.

Planned artifact: a separate `metric-targeted-restoration` skill under the `craft_restoration` task surface, plus `references/metric-targeted-restoration.md` and a packet-generator script (`scripts/restoration_packet.py`). The packet generator will consume existing JSON outputs from `variance_audit.py`, `bigram_diff.py`, `voice_distance.py`, `idiolect_detector.py`, and `aic_pattern_audit.py`, then emit bounded revision packets with a claim license, targetability class, local evidence, plain-language translation, allowed moves, guardrails, and post-check commands.

Targetability classes:

- **Direct targets.** Connective density, sentence-length variance, FKGL spread, adjacent-cosine tidiness, repeated generic vocabulary, idiolect preservation lists, and named AIC pattern density.
- **Translated targets.** POS bigrams/trigrams, selected dependency n-grams, function-word clusters, and voice-distance contributors. Raw tags such as `DET+ADJ+NOUN` become prose instructions such as "replace generic descriptor packages with concrete actors, objects, or verbs."
- **Investigate-first targets.** MATTR, MTLD, Yule's K, Shannon entropy, and some function-word/dependency drift. These ask "what local cause produced the signal?" before any revision.
- **Avoid direct targeting.** Overall KL/JSD, Burrows Delta, cosine distance, char n-grams, and validation metrics. These are evidence summaries, not writing goals.

Status: scoped. The first version should not rewrite prose; it should produce prompt packets and require a before/after SETEC post-check so the writer can see whether the revision restored the intended signal without damaging neighboring signals or idiolect.

### Phase 7+ horizon: local LLM cross-perplexity

**Status (2026-06-06): shipped as Surface 5.** `binoculars_audit.py` + `binoculars_calibrate.py` ship the cross-perplexity surface (uncalibrated by default, operator-supplied thresholds), alongside the Tier-4 surprisal stack (`surprisal_audit.py` / `surprisal_backend.py`). What follows is the original horizon framing, kept for the design rationale.

Classical stylometry is structurally blind to the homogeneous-mixing case where AI rewrites human ideas in AI's style: the surface form is fully AI, even though the underlying ideas are human, and any detector that operates on the surface form alone will score the entire text as AI. Hans et al. (Binoculars, ICML 2024) and Bao et al. (Fast-DetectGPT, ICLR 2024) show the cleanest current zero-shot detectors operate on cross-perplexity ratios between paired language models sharing a tokenizer.

A future Phase 7+ extension would add a sibling task surface (`provenance_neural`) that wraps a local LLM pair (Falcon-7B + Falcon-7B-Instruct in the original Binoculars paper, or a similar shared-tokenizer pair) and reports the Binoculars ratio with the same surface-tagged discipline as the existing tools. Plausible inference backends: `mlx-lm` for Apple Silicon performance, `transformers` + `torch` for cross-platform portability, `ollama-python` for a server-boundary wrapper. Two forward passes per detection. The dependency footprint is order-of-magnitude larger than the current install (gigabytes of weights), which is why this lives in a separate task surface rather than the core variance_audit / voice_distance pipeline.

This is a horizon item rather than a roadmap commitment. The realistic prerequisites are (1) a stable validation harness against classical signals, (2) explicit user opt-in to the deployment cost, and (3) calibration against the same labeled corpus the classical signals use, so the neural task surface and the classical task surfaces report comparable confidence intervals at the same operating point.

### Borrow-before-building track

The validation and idiolect roadmap should start from known implementations before local code gets written. `references/implementation-survey.md` records the current survey:

- Use `scikit-learn` and `statsmodels` for validation metrics and confidence intervals.
- Use SciPy's bootstrap machinery under SETEC's own length-matched window sampler.
- Treat R `stylo` as the Delta / cosine / rolling-Delta / General-Imposters oracle before expanding voice-distance verification.
- Treat `quanteda::textstat_keyness` and NLTK collocations as the design references for idiolect and preservation-list extraction.
- Keep privacy guards, report claim language, task-surface routing, and craft triage local.

### Calibration corpus track

Cathedral upgrade #3 (validation harness) and the threshold-calibration prerequisite need labeled human-vs-AI corpora. The calibration toolchain shipped in 1.10.0 already includes a license-aware fetcher for Pangram Labs' EditLens (CC BY-NC-SA 4.0, gated; local-only). Two openly redistributable benchmarks remain on the roadmap as bounded follow-ups:

- **`scripts/calibration/fetch_raid.py`.** RAID benchmark (Dugan et al., NAACL 2024; Apache-2.0 dataset on HuggingFace at `liamdugan/raid`). 10M+ generations across 11 generators × 8 domains × 4 decoding strategies × 11 adversarial transforms — the most comprehensive openly-licensed AI-detection benchmark available. Fetcher mirrors `fetch_pangram_editlens.py` shape but without the CC-NC restrictions; can ship calibrated thresholds derived from RAID without the local-only constraint EditLens imposes. Highest-leverage corpus addition because it's both large and unrestrictively-licensed.

- **`scripts/calibration/fetch_mage.py`.** MAGE benchmark (Yichen Li et al., ACL 2024; MIT-licensed; HF `yaful/MAGE`). ~447K examples across 10 datasets. Companion to RAID; the four-benchmark empirical frame `references/implementation-survey.md` records is RAID + MAGE + MAGE-extension + Ghostbuster. Fetcher is a port of the EditLens pattern.

- **`scripts/calibration/PROVENANCE_TEMPLATE.md`.** Walkthrough for new users on collecting and labeling their personal pre-AI baseline corpus — the irreducible piece of the corpus pool that has to come from the user themselves, not online. Documents the manifest conventions, the date-tagging and register-tagging patterns, the privacy-ratchet rules in `manifest_validator.py`, and the borrow-before-building decision tree (when to use Project Gutenberg / PAN authorship corpora as impostor baselines vs. when to curate from personal sources).

- **Fiction impostor-pool assembly (extraction tooling implemented, pending merge).** The EPUB → plain-text extraction tooling is implemented in `scripts/acquire_epub.py` (**PR #141, pending review — not yet on `main`**) — built on the `acquisition_core` pipeline rather than the originally-scoped Calibre/`ebooklib` route — alongside `scripts/acquire_manuscript.py` (**PR #142, pending**) for ingesting local `.docx`/`.md`/`.txt` as an *identity* baseline. What remains: actually **assembling the pool** (running the tooling over the maintainer's owned fiction) plus two open scope gaps below (structural front/back-matter stripping, per-author word budgets). Originally scoped as `fetch_epub_corpus.py`; targets the literary-horror / weird-fiction register specifically (Brian Evenson, Paul Tremblay, Helen Oyeyemi, Catriona Ward, Mona Awad, Mariana Enriquez, Camilla Grudova, Daisy Johnson, Samanta Schweblin, Kelly Link, Carmen Maria Machado, et al.) — the register the maintainer writes in, and one the framework's text fixtures + paragraph / construction / mimicry-cosplay audits explicitly need a real register-matched baseline for. Scope:
  - **EPUB text extraction** — implemented in `acquire_epub.py` (**PR #141, not yet merged**): stdlib `zipfile` + `ElementTree` OPF parsing, one manifest entry per reading-order spine chapter. Built on `acquisition_core`, not the originally-scoped Calibre/`ebooklib` route. MOBI/AZW are reported and skipped (convert with Calibre first).
  - **Front-matter / back-matter stripping** — ◻ partial. `acquire_epub.py` drops most front/back matter via a `--min-words` floor (default 500); explicit structural `<section>`-aware stripping (cover, copyright, dedication, TOC, also-by, acknowledgments, author bio, ad pages) remains the gap. EPUBs carry these as `<section>` elements that look like prose to spaCy but aren't narrative; the corpus-hygiene gate (`check_corpus.py`) handles HTML/CSS but not structural-ebook elements.
  - **Per-author word budgets** so a 13-book author doesn't dominate a cross-author baseline. Default 50K words per author with the option to override per slug.
  - **Manifest scaffolding**: per-author `persona` slugs, `register: literary_fiction`, `corpus_role: impostor` (feeds `general_imposters.py`) AND `corpus_role: baseline` (feeds the register-matched cross-author baseline for `voice_distance.py`).
  - **`ai_status` tagging** by publication date: pre-2022 → `pre_ai_human` (the clean-baseline slice); 2022+ → `unknown` (post-AI-availability boundary; cannot confirm whether the author or editor used AI assistance). Post-2022 books become an interesting *test set* — does the framework's signal flag a 2024 novel by the same author differently than their 2019 novel?
  - **Privacy posture**: all copyrighted; lives in `ai-prose-baselines-private/fiction_impostors/`; `privacy: private` across the board; never published from the framework's plugin folder.
  - **Phased rollout**: the cleaning-approach validation and the script itself (first + second pass) are implemented in `acquire_epub.py` (**PR #141, pending review — not yet merged**); the remaining **third pass** runs the full pool through `general_imposters.py` and the cross-author bigram-diff studies. Useful both for personal-baseline drift checks ("does my prose sit inside the band these authors define?") AND for the framework's R10 mimicry-cosplay audit, which currently has no real fiction-register impostor pool to validate against.

- **Fiction calibration spine (depends on the impostor pool above).** The full research-grade project that the impostor pool unlocks. Where the impostor pool gives `general_imposters.py` a register-matched corpus to consume, the calibration spine answers the framework-shaped questions: what do Layer A thresholds look like when the human comparator is literary horror rather than general / argumentative prose, and how do per-author syntactic signatures distribute within that register? Six concrete deliverables:
  - **Fiction-specific Layer A threshold calibration.** Run `calibrate_thresholds.py` against the pre-2022 slice of the impostor pool as the human-class baseline, paired with the post-2022 slice (status `unknown` — empirically treated as positive-class for the inversion test) OR against the framework's RAID / MAGE positive class held constant. Produces `lit_horror_fiction_<signal>_fpr<target>_<date>` provenance entries. Expected outcome: the **polarity-inversion gate** (#40) fires on at least some signals when the comparator switches from student-essay / general-prose to literary-horror, which is itself the empirical artifact — fiction is precisely the register where smoothing-diagnosis polarity claims need to be re-evaluated, and the gate is the operational expression of that.
  - **POV-marker scheme + `pov_voice_profile.py` calibration.** Many of the impostor-pool authors do POV switching (Oyeyemi's *Boy, Snow, Bird* / *Mr. Fox*, Ward's *Sundial* / *The Last House on Needless Street*, Awad's *Bunny* / *Rouge*). A per-chapter POV-marker annotation on the manifest lets `pov_voice_profile.py` calibrate voice-collapse thresholds against real multi-POV authors rather than the current heuristic defaults. The deliverable is a calibrated per-POV variance band specifically for literary fiction.
  - **Cross-author bigram-diff studies.** Using `bigram_diff.py` and `manuscript_bigram_diff.py`, characterize per-author POS-bigram signatures within the literary-horror register. The question this answers: how distinct ARE these authors from each other at the syntactic level? If Evenson and Oyeyemi share the literary-horror register but their POS-bigram distributions differ markedly, the framework's `general_imposters.py` has a high-discrimination test bed. If they're indistinguishable, that tells the framework where its limits are. Output: a per-author bigram-signature reference doc (a sibling of `references/calibration-findings-*.md`) plus a tightened impostor-pool selection that drops near-duplicate-signature authors so GI iterations stay independent.
  - **Pre-AI vs post-AI within-author test set.** For the seven authors with both pre-2022 and post-2022 works (Evenson, Tremblay, Oyeyemi, Awad, Ward, Enriquez, Grudova), running `variance_audit.py` against the post-2022 books with the pre-2022 books as the personal baseline asks: do the framework's smoothing-diagnosis signals shift on the same author's later work? This is a within-author drift check — a stronger evidentiary frame than cross-author comparison because confounders other than AI exposure (genre conventions, editorial house style, register) hold constant. Output: a per-author drift table in the synthesis doc plus a note in PROVENANCE.md if any signal shifts measurably.
  - **Fiction-side validation harness slice.** The current `validation_harness.py` runs labeled AI/human samples from EditLens/MAGE/RAID. None of those corpora contain literary horror at meaningful scale. A fiction-side slice — pre-2022 books as the human class, GPT-4 / Claude / Llama-generated continuations of those books as the AI class — lets the harness compute fiction-specific FPR/TPR/ROC curves. Generation is the load-bearing operational cost (each fiction-AI sample is a per-author per-prompt run; expensive); validation-set construction is the harder problem. Defer until a sustainable generation budget is identified.
  - **Provenance commits.** Each calibration above produces an `[POLICY: AUDIT-ONLY]` entry in `PROVENANCE.md` (matching the existing EditLens/MAGE shape). Per the polarity-inversion gate posture, fiction-derived thresholds with inverted polarity vs. EditLens/MAGE/RAID are documented in the ledger but NOT encoded into `COMPRESSION_HEURISTICS` as framework defaults — they're per-register findings that operators wanting fiction-anchored thresholds can opt into by reading the ledger and editing the registry locally.

  This is multi-session research-grade work, gated on the impostor pool's existence and on the AMD calibration runs completing (so the cross-corpus polarity landscape is established before fiction calibration joins it). The framework already has every tool the calibration spine needs (`calibrate_thresholds.py`, `polarity-inversion gate`, `pov_voice_profile.py`, `bigram_diff.py`, `general_imposters.py`, `voice_distance.py`); the work is operational rather than framework-side.

- **AIC-8 / AIC-9 calibration corpus.** The AIC-8 image-conjunction and prestige-metaphor detectors and the AIC-9 kicker-density detector ship with provisional thresholds (T1 = 2.5 concreteness gap; T2 = 0.4 cosine similarity; T3 = 0.7 domain-scatter entropy) drawn from the implementation spec's starting values. Per the Stylometry-to-the-people policy these stay `provisional=True` until calibrated against a real corpus. The spec calls for four labeled fixture corpora: (a) **idiom negatives** — texts containing conventional collocations ("heavy burden", "sharp decline") that should NOT trigger image-conjunction flags; (b) **AI-image-conjunction positives** — texts containing the spec's named pattern ("constraints humming", "the machinery of grief") that SHOULD trigger at elevated density; (c) **aphoristic essayist negatives** — Borges / Bacon / La Rochefoucauld passages where high kicker density is the genre, not the failure mode; (d) **AI-rewrite positives** — essays passed through ChatGPT or Claude with a "polish this" instruction. Generation is the load-bearing operational cost (each AI-rewrite sample is a per-prompt run). The PR-1 foundation ships small synthetic fixtures (~10-20 examples each) for unit-test purposes only; the calibration-grade corpus is a separate research-grade build with its own PROVENANCE entry. Empirical evidence already in hand: on the Brysbaert data, several of the spec's own positive examples (`machinery/grief` gap 2.05, `architecture/grief` gap 0.89, `grammar/desire` gap 1.49) do not clear T1 = 2.5; the joint diagnostic with T2 is what carries the load, but the thresholds need empirical tuning before they're operational.

- **Periodic embedding-model re-evaluation for AIC-8.** The shipped image-conjunction and prestige-metaphor detectors use spaCy's GloVe-derived 300d word vectors (`en_core_web_md` preferred, `en_core_web_lg` accepted). The choice was made for dependency-light reuse of the framework's existing spaCy install rather than introducing a new embeddings stack (Word2Vec, GloVe binary, BERT). This is a defensible 2026-H1 choice but the field moves quickly; modern contextual embeddings (BERT, sentence-transformers, the various Qwen3-Embedding-style new models) may improve precision on the compound diagnostic that AIC-8 depends on. Tickler: re-evaluate every 6 months or whenever an AIC-8 precision/recall measurement against the calibration corpus (above) drops below an acceptable threshold. Specifically: (1) check whether spaCy has shipped a vectors-model upgrade; (2) check whether contextual embeddings produce sharper concreteness-gap-plus-similarity discrimination on the four-corpus fixture; (3) check whether a register-specific embeddings model (legal, fiction, academic) is now available and would improve calibration. The PR-1 foundation is deliberately structured so `scripts/embeddings.py` is the one place to swap; downstream detectors call `cosine_similarity()` and don't know which backend produced the number.

The seven are independently shippable. RAID and MAGE shipped in 1.42.x (the section above is mildly stale); the PROVENANCE template shipped in 1.29.0. The fiction impostor pool is the next concrete acquisition target. The fiction calibration spine that depends on it is the longer-horizon item the maintainer has committed to — "eventually we'll do it all." The two AIC-8/9 entries are R&D-side follow-ups to the 1.6x AIC-8/9 implementation wave; they are not blockers on shipping the detectors, only on calling their thresholds calibrated.

## Stylometric surface expansion

The shipped suite covers the core modern stylometry stack: lexical diversity, sentence/rhythm variance, POS/dependency n-grams, character n-grams, function words, Burrows Delta, General Imposters, keyness/collocation, and per-window localization. Recent reviewer-track work surfaced a longer list of candidate surfaces drawn from classical writeprint research and recent stylometry surveys (lexical / syntactic / structural / content-specific / idiosyncratic feature families; the persistent challenges around genre, topic leakage, short texts, and forensic reliability). This section catalogs each candidate with an honest priority — including the ones I'd indefinitely defer or explicitly *not* ship as voice surfaces.

The general framing: the existing suite is strong at measuring **distributional compression and distance from baseline**. The candidate surfaces below mostly measure **where the writer's choices live** — at the paragraph, discourse, agency, construction, and trajectory layers. That's where AI smoothing often does its most interesting damage and where the existing suite is structurally blind.

Candidate surfaces are tiered by build readiness, not by intellectual interest. Several that I find theoretically interesting are deferred or out-of-scope for reasons spelled out below.

### Tier 1 — Near-term builds

These three are the next concrete picks. Each is testable, doesn't require new dependencies beyond what's already imported, and lands at a layer the existing suite doesn't reach.

- **Paragraph Architecture Audit.** Paragraph-length distribution and variance, first-sentence vs. body length, terminal-sentence punchiness, one-sentence paragraph rate, paragraph opening / closing types, transition paragraph frequency, paragraph-to-paragraph semantic distance. Catches the "competent rectangle paragraphs" failure mode that AI editing produces — sliding-window mode is word-windowed, so paragraph-shape gaps are invisible to it. Cheap to build, immediately useful for restoration packets, and structurally orthogonal to every existing audit.

- **Discourse Move Signature.** Typed discourse markers (contrast / concession / consequence / elaboration / sequencing / reframing / epistemic stance / metadiscourse) plus *move sequence n-grams* (concession→reversal→claim, premise→caveat→narrower-claim, critique→alternative→standard). The natural growth of `connective_density` (currently a single ratio) and `aic_pattern_audit` (currently named-pattern density). The move-sequence layer is what makes it a voice surface rather than a marker counter — for serious nonfiction, "concede the objection, narrow the claim, sharpen the institutional implication" is identifiable voice. Highest interpretability for the kind of writing the framework's primary user does.

- **Agency and Abstraction Audit.** Nominalization density, agentless passive rate, light-verb constructions ("make a decision" / "provide support" / "conduct an analysis"), entity/action ratio, human-actor density, generic-institutional vocabulary. Lands at a meaningful semantic layer the variance signals don't reach — institutional smoothing lives here. Restoration packets gain a useful diagnostic vocabulary: "the local smoothing is agency loss, not lexical-diversity loss." Builds on top of the spaCy POS+dep extraction that's already there.

### Tier 2 — Promotions to first-class surfaces

These are partly shipped as feature columns inside `voice_profile.py` or implicit in existing audits. Promoting them to top-level surfaces with their own audits, baseline comparisons, and bootstrap percentiles is concrete deliverable work.

- **Punctuation Cadence Audit.** `voice_profile.py` already captures comma / semicolon / colon / dash / parenthesis / ellipsis rates as feature columns. What's missing is the top-level surface: punctuation feature Delta against baseline, punctuation n-gram divergence, "interruption grammar" profile (parentheses / em-dashes / appositives / asides), smoothing flags for dash-collapse / semicolon-suppression / comma-regularization. AI smoothing and copyediting often regularize punctuation before they erase vocabulary, so this surface catches a class of voice loss earlier than lexical signals do.

- **Stance / Modality / Epistemic Posture Audit.** Partly in the function-word feature family + the pronoun-modal-negation cluster, but only at frequency level. The missing pieces: deontic vs. epistemic modality distinction, hedge / booster / certainty / evidential markers as typed buckets, source-of-knowledge markers ("seems" / "suggests" / "shows" / "proves"), refusal phrases, obligation language, first-person stance density. Important for nonfiction / legal / academic / policy prose, where AI smoothing often changes not just style but epistemic ethics ("may suggest" → "shows"; "this is not enough to establish" → "this highlights").

- **Function-Word Grammar Surface.** The function-word family is currently used at frequency level via Burrows Delta. The sequence layer — function-word n-grams, function-word skip-grams, preposition profile, determiner profile, demonstrative usage (`this/that/these/those`), relative-pronoun choice (`which/that/who`), complementizer choice (`that/if/whether`), subordinator profile, auxiliary chains, pronoun transition patterns — would bridge interpretable syntax and the robust content-independent signal that classical authorship attribution leans on.

### Tier 3 — Substantive new surfaces (post-Tier-1)

Bigger builds. Each requires curated taxonomy work and meaningful methodology pinning before code lands. Best to design on paper before writing tests.

- **Construction Signature Audit.** The right answer to "POS-bigram KL is opaque." Translates raw tag-sequence machinery into interpretable construction counts: clefts ("what matters is..."), pseudo-clefts, fronted adverbials, sentence-initial participial phrases, appositives, agented vs. agentless passives, existential "there is/are," extraposition ("it is important to..."), correlative constructions ("not only / but also"), concessive openers, parenthetical insertions, stacked prepositional phrases. Pairs with the AIC density audit — same shape, different unit. Build cost dominated by the construction inventory's curation, not the spaCy pattern-matching code.

- **Mimicry / Style-Cosplay Audit.** Required once restoration tools are mature; not before. The framework already ships `before_after_restoration.py` with a metric-gaming heuristic, but it doesn't catch the failure mode where idiolect phrases survive *too conspicuously* (over-preserved) while function-word grammar fails to match the lexical mimicry, or baseline-signature features appear at unnatural density. The methodology is non-obvious: phrase-level survival and syntactic Delta need to be cross-checked, not aggregated. Useful both for adversarial testing and for restoration quality control — a bad voice-restoration pass can produce cosplay that scores well on per-feature metrics but reads as imitation.

- **Phraseological Signature Audit.** Extension of `idiolect_detector.py`'s keyness + collocation work into phrase-frame mining: skip-grams, lexical bundles, phrase frames with slots ("not because X but because Y"), preferred intensifier / stance / epistemic frames, idiom survival, hapax phrase survival, multi-word expression distinctiveness. The shape difference from keyness: keyness asks "which words/phrases are over-represented?"; phraseology asks "what reusable language frames does this writer build with?"

- **Semantic Trajectory Audit.** Sentence-to-sentence and paragraph-to-paragraph semantic-jump distributions, return-to-topic loops, semantic radius around thesis, abstraction trajectory over document position, claim/example density curves. Catches the "improved local cohesion at the cost of productive leaps" failure mode that the adjacent-cosine pair alone can't see. **Heavier dependency footprint** (SBERT or equivalent — gigabytes of weights); should ship as opt-in like the SBERT cohesion path is now. The surface most likely to drift toward "measuring meaning" rather than "measuring style"; needs careful claim-licensing language to stay on the right side of the framework's "topic ≠ style" boundary.

### Tier 4 — Specialized / fiction-specific extensions

Useful in narrower domains. Fit naturally as round-2 of existing surfaces rather than new top-level work.

- **Dialogue-Specific Voice Audit.** Dialogue tag style, contraction rate by character, interruption punctuation, vocatives, turn length, character-specific function-words and discourse-markers, profanity/intensifier profile, adjacency-pair patterns. Round 2 of `pov_voice_profile.py` — character voice collapse often appears in dialogue first, narration second. Worth building when the per-POV surface gets serious use.

- **Narratorial Distance / Free Indirect Audit.** Pronoun anchoring, perception/cognition verb density, deictic anchoring (`here/now/this/that`), evaluative-adjective density, focalization markers, free-indirect-discourse signals. Outside the standard stylometry literature but valuable for developmental editing of literary fiction. Adjacent to per-POV voice profile; shippable when the demand surfaces.

- **Productive Roughness Audit.** Fragments, sentence-initial conjunctions, colloquial contractions, repeated words, asymmetrical lists, mixed register, "thinking on page" markers. Conceptually right but methodologically fragile: "roughness" is in the eye of the beholder. The surface has to be **strictly baseline-relative** (this writer's stable roughness pattern, before any draft) — never absolute (these features are good). Otherwise it encodes editorial preferences as voice. Build only with that constraint frontloaded.

### Tier 5 — Adjacent surfaces (ship under different framing)

Real signals, but topic-bound or format-bound enough that calling them "voice" surfaces would muddy the framework's claim language. Each is worth shipping in its own right, as a non-voice surface.

- **Document Structure / Layout Audit.** Heading frequency / syntax, list rate, bullet style, section length variance, citation placement, block-quote use, link density, footnote density, opening-hook / closing-move types. Useful for blog / Substack / policy / memo / newsletter workflows where formatting is part of voice. But it's a *publishing-format* fingerprint, not stylometry in the standard sense. Ship as its own small audit, not as a voice tool.

- **Reference Ecology Audit.** Frequency and pattern of named references, parenthetical-reference style, quote integration, epigraph use, "as X says" constructions, analogy source domains, proper-noun ecology, canonical vs. idiosyncratic references. Identifiable across an essayist's career. **Heavily topic-bound.** A writer changes topic between drafts and the reference ecology changes; the tool would call it voice drift. The framework's foundational claim is that topic ≠ style; this surface has to ship with claim-licensing that explicitly refuses voice attribution. Better as a thematic / register profile than a voice tool.

- **Allusion / Quotation Habit Surface.** Same topic-leakage concern as reference ecology. Some writers have distinguishable allusion ecologies that survive across topics, but the signal is brittle and topic-correlated enough that ship-as-voice would require very strong claim-language guards.

- **Stockness / Formulaicity Audit.** Cliché density, generic transition phrases, corporate / policy boilerplate, register-specific stock phrases, phrase originality against a large reference corpus. Two structural risks: (1) the "LLM-associated phrase" list drifts as models change, so the tool needs current-empirics sourcing rather than a frozen list — that's a maintenance commitment the framework doesn't have a model for; (2) many humans use these phrases legitimately. The framing has to be *phraseological texture*, not *AI signal*; the latter would make this a Pangram-style classifier wearing stylometric clothes, which is the framework's structural anti-goal. Build with skepticism, ship with very explicit claim-licensing.

### Tier 6 — Indefinitely deferred

These I'd not build as separate surfaces. The reasons are structural, not preference.

- **Dependency-Tree Shape and Subtree Motifs.** The literature is mixed on whether dependency-tree features outperform lexical features for authorship; gain over what the existing POS-trigram + dependency-n-gram surfaces already capture is modest, and the *interpretability* problem the construction-signature audit solves applies equally here (tree-shape numbers are no more legible to a writer than POS-trigram KL). Better treated as inputs to the construction signature audit than as a standalone surface.

- **Morphological Texture Audit.** Latinate-vs-Germanic tilt, derivational morphology density, suffix preferences. The signal is real but it's *heavily correlated with register, education, and topic*, not just voice. A scientist writing for general audiences vs. peers will produce a Latinate-tilt swing that has nothing to do with voice. Could surface as columns inside other audits (the function-word grammar surface, for instance) but I wouldn't promote it to a top-level voice surface.

- **Figure-of-Speech Expansion (Beyond Current AIC Set).** Antithesis, anaphora, epistrophe, isocolon, polysyndeton, asyndeton, litotes — the broader rhetorical-figure inventory. The current `aic_pattern_audit.py` already covers the AI-prose-relevant figures (correctio, pseudo-aphorism, manifesto cadence, triplet, professional-parallel stack, plus four nonfiction parallel patterns). Expanding the inventory adds breadth but not new claim-shape; better to deepen the AIC density work (calibrated thresholds, baseline-relative density) than to broaden the figure list.

### 2.0 refactor target

- **Compression-of-Choice / Stylistic Choice Entropy.** This is the deepest theoretical extension on the list — and where the framework's central claim actually lives. The framework currently measures variance compression in **outputs** (sentence length, lexical diversity, POS-bigram distribution); the more honest object of measurement is variance in the writer's **choice architecture** — which connective among alternatives, which clause-combining strategy, which actor-reference strategy, which sentence-opener class. Built well, this surface would *generalize* every existing audit: each becomes a special case of "compression in some choice set." Sentence-length variance is compression in length-choice; MTLD is compression in lexical-choice; AIC density is compression in figure-choice. The unifier would give the framework a single load-bearing claim ("AI smoothing collapses choice-set entropy") and a clean restoration target ("expand this writer's choice set in dimension X"). It would also reframe what gets measured everywhere: every existing audit could be rewritten on top of this primitive.

  **Why 2.0**: defining defensible choice sets is a curatorial problem, not a coding problem. Sentence-opener classes, connective classes, reporting-verb classes, clause-combining strategies — each needs a curated taxonomy and a per-class baseline to be meaningful. This is research-grade work, not a one-week ship. It also implies a refactor of the existing surfaces to be expressed as choice-entropy specializations, which is a public-API breaking change consistent with a major version bump. Treat as the *target* for 2.0; not a v1 commitment, but the right shape for the next architectural generation.

### Build order (concrete commitments only)

**Status (2026-06-06): items 1–9 shipped; item 10 (Semantic Trajectory Audit) shipped as code, threshold calibration pending.** The order, as originally committed:

1. Paragraph Architecture Audit (Tier 1)
2. Discourse Move Signature (Tier 1)
3. Agency and Abstraction Audit (Tier 1)
4. Punctuation Cadence Audit (Tier 2 — promotion)
5. Stance / Modality Audit (Tier 2 — promotion)
6. Function-Word Grammar Surface (Tier 2 — promotion)
7. Construction Signature Audit (Tier 3)
8. Phraseological Signature Audit (Tier 3 — extension of `idiolect_detector`)
9. Mimicry / Style-Cosplay Audit (Tier 3 — gated on restoration maturity)
10. Semantic Trajectory Audit (Tier 3 — gated on dependency posture)

The Tier 4 specialized surfaces (Dialogue, Narratorial Distance, Productive Roughness) ship when their domains pull on them, not on the cathedral schedule. The Tier 5 adjacent surfaces (Document Layout, Reference Ecology, Allusion Habit, Stockness) ship as separate non-voice surfaces with explicit claim-language guards, on the same demand-driven cadence. The Tier 6 deferred items are not commitments. The 2.0 refactor target (Compression-of-Choice) is the architectural horizon, not a v1 deliverable.

## Trustworthiness expansion

The `Stylometric surface expansion` section above catalogues *new things to measure*. This section catalogues *failure-mode control, interpretability, adversarial realism, and user workflow discipline* — the parts that stop a sophisticated stylometric tool from becoming a numerically impressive overclaimer. The shipped suite answers "what does this text look like stylometrically?" Trustworthiness work answers a different question:

> Compared to which legitimate alternatives, under what evidentiary conditions, with what confounders, and what revision moves would improve the prose without gaming the instrument?

That is the difference between a detector-shaped tool and a serious writing-forensics / voice-preservation system.

The current suite already encodes much of this discipline at the surface level: every output carries a `task_surface` tag, the `claim_license` block names what the result does and does not entitle, `manifest_validator.py` enforces ESL ratchets, the General Imposters harness has gray-zone refusals, and the metric-targetability taxonomy in `restoration_packet.py` resists naive metric-gaming. The work below is the *systematization* of that discipline — promoting it from per-surface convention into a shared interpretive layer.

### Architectural shape

These additions form a layered discipline:

- **Input layer.** Stylometric masking profiles (quotes, citations, boilerplate); register/genre gate; multilingual / dialect caution layer.
- **Core layer.** Existing surfaces (smoothing diagnosis, voice coherence, GI, idiolect, AIC, chapter distinctiveness).
- **Interpretation layer.** Confounder audit (Layer D), source-of-smoothing localization, surface-disagreement resolver, ablation reports.
- **Output discipline.** Evidentiary conditions gate, claim license (already shipped), negative/positive controls.
- **Validation layer.** Adversarial / paraphrase stress harness, calibration drift monitor, fairness guardrails.
- **Author-facing layer.** Revision-risk model, semantic preservation check, draft-history analysis, known-editor profile.
- **Research layer.** Counterfactual editing sandbox, multi-author segmentation, transformation-profile learning, house-style vs. author-voice decomposition.

The lower layers run before any claim is composed; the upper layers extend what writers can do with the claim once it lands. Build order generally proceeds bottom-up, but several items are independently shippable.

### Tier 1 — Trustworthiness upgrades

Highest leverage. Each one immediately reduces the framework's surface area for false confidence. These are the next picks once the calibration-breadth track has more committed thresholds.

- **Confounder audit (Layer D).** The most important addition on this list. The framework currently detects compression and drift but doesn't synthesize "compressed *relative to what alternative explanation*." Build a confounder signature matrix: each candidate confounder (professional copyediting, register/genre shift, legal/policy memo style, translation or ESL cleanup, dictation/transcription cleanup, house-style enforcement, developmental revision, "writing up from notes," intentional voice imitation) gets expected directions across the existing signal set (sentence-length variance, MDD variance, lexical diversity, POS-bigram KL, char n-gram Delta, punctuation cadence, idiolect survival, connective density, AIC pattern density, chapter-localization, baseline distance). Output: a *differential diagnosis*, not a verdict. "The observed signal is compatible with AI smoothing, but also compatible with professional copyediting and register shift; evidence distinguishing these is weak because no pre-edit draft, editor style baseline, or revision history was supplied." The framework's epistemic posture is that the math doesn't entitle the verdict; this audit is the formal expression of that.

- **Register / genre conditioning.** The manifest carries `register` but the comparison isn't operationalized — the claim-license block already says "matched register" but nothing checks it. Build: a register classifier for target and baseline (personal essay / literary fiction / commercial fiction / academic prose / legal memo / policy memo / blog essay / newsletter / testimony / grant or report prose / journalism / marketing / social media thread / email / dialogue-heavy fiction / exposition-heavy nonfiction); a register-match indicator (weak / moderate / strong); a register-mismatch penalty on claim strength; eventually a register-conditioned threshold set once enough labeled data exists. Critical because legal / policy / testimony writing has high legitimate rates of templates, connective scaffolding, abstraction, repeated nouns, and transitional explicitness — exactly the signals AI smoothing also produces. Without register conditioning, the framework over-flags the very institutional genres professional writers actually work in.

- **Stylometric masking profiles.** The existing `check_corpus.py` strips HTML / CSS / code / tables before tokenization. Expand into selectable masking profiles for the analytical pass: block quotes, inline quotations, citations, footnotes, bibliographies, legal citations, case names, statute names, headings, markdown artifacts, email headers, front matter, captions, boilerplate disclaimers, repeated institutional language, prompt remnants, LLM wrapper phrases. Modes: analyze-full, exclude-quotations, exclude-citations, exclude-headings, prose-body-only, dialogue-only, narration-only, argument-body-only, institutional-boilerplate-removed. The report should state explicitly when a finding *survives* masking ("the smoothing call drops from Moderate to Light after headings, citations, and quoted statutory language are removed") — this prevents embarrassing overclaiming on policy / legal / testimony inputs where statutory or quoted language is not the writer's voice.

- **Minimum evidentiary conditions gate.** Promote the per-surface gray-zone / claim-license guards into a single front-door gate. The gate evaluates target length, baseline size, register match, baseline staleness, impostor pool breadth, contamination, quotation density, multilingual mismatch, collaborative editing, rhetorical-task mismatch, presence of pre-edit versions, and asks: *what use is this output entitled for?* Output is an **Evidentiary Posture** label, not a numerical confidence score. Possible categories: revision-only, exploratory comparison, internal triage, research-grade validation, forensic-adjacent (still non-dispositive). Protects the tool from being used the way such tools always get abused.

- **Surface-disagreement resolver.** The framework runs multiple surfaces (smoothing diagnosis, voice coherence, GI, idiolect, AIC, chapter distinctiveness) and currently leaves cross-surface interpretation to the reader. Build a meta-layer that surfaces interpretable disagreement patterns: high smoothing + low voice drift → "author likely wrote it but it was heavily edited"; low smoothing + high voice drift → "genre shift, impostor, collaboration, or intentional style change"; high voice drift + high idiolect survival → "imitation, self-conscious revision, or phrase-level preservation with deeper structural change"; high POS-bigram KL + normal sentence variance → "syntactic-template shift without obvious rhythm compression"; high AIC density + normal Layer A → "rhetorical habit issue, not smoothing"; GI gray zone + high Delta → "candidate comparison inconclusive despite baseline distance." The current architecture has the components; this is the synthesis layer.

### Tier 2 — Validation upgrades

Make the tool publishable and defensible. Several of these are already partially scoped on the roadmap (the adversarial track has been open since 1.x); the contribution here is *output shape*, especially the robustness card.

- **Adversarial / paraphrase stress harness.** Already on the roadmap as the validation harness's adversarial-class track. The transformation classes worth covering: light copyedit, heavy copyedit, LLM "make this sound more natural," LLM "make this sound like author X," humanizer-tool output (StealthGPT / UndetectableAI / Quillbot), backtranslation, summary-to-prose expansion, voice-restoration pass (does the framework's own restoration tools create false reassurance?), deliberate idiolect injection, register transfer (essay → testimony, fiction → query letter). Output shape: a **robustness card** per signal — "this signal remains stable under light copyediting but collapses under paraphrase"; "this signal survives paraphrase but is highly register-sensitive"; "this signal is useful only with matched baselines over 2,000 words." The robustness card is the deliverable; without it, the harness is metrics without epistemic guidance.

- **Negative and positive controls in reports.** Every serious comparison should include known-authentic and known-smoothed reference points from the same writer where available. "The questioned text is farther from baseline than the known-authentic control, but closer than the known-smoothed control." Makes reports interpretable to non-technical users and prevents the "big number means scary" failure mode. Concrete buildable extension to all three validation harnesses.

- **Ablation reports.** Leave-one-feature-family-out for the band call and the voice-distance call. Surfaces fragile-vs-robust calls and distinguishes "rhythm-driven smoothing" from "global smoothing": "the Moderate call is robust to removing FKGL spread and lexical entropy but disappears if sentence-variance signals are removed — treat as rhythm-driven, not global." For voice coherence: "candidate distance is driven mostly by char 4-grams and punctuation cadence; function-word Delta is ordinary." Cheap to build (just re-run with one signal removed at a time); high interpretability payoff.

- **Calibration drift monitor.** The score-once cache already carries a `scorer_version` field. Add a regression-test suite using fixed benchmark texts that detects when threshold values shift after spaCy / dependency-parser / corpus updates. Output per release: "burstiness_B threshold stable; POS-bigram KL threshold shifted materially after parser update — recalibration required before publication claims." Protects against invisible infrastructure drift, especially as model versions change underneath.

- **Fairness / dialect / multilingual guardrails.** The ESL ratchet exists in `manifest_validator.py` but the broader linguistic-background caution layer is not visible at the report level. Promote into an explicit caution surface for nonnative English writers, code-switching, dialect features, translation-influenced prose, speech-to-text cleanup, neurodivergent punctuation/structure patterns, genre-specific educational prose, institutional templates. The report should explicitly state whether the validation set includes comparable language backgrounds; if not, it should refuse evaluative or disciplinary use. Critical because the AI-detection field has a documented history of producing unfair false positives against nonnative English writers, and even when the framework is not an AI detector users may try to use it that way.

### Tier 3 — Writer-facing upgrades

Extensions of existing surfaces that make the tool genuinely useful to writers (rather than just stylometrically interesting). Each pairs naturally with a surface that already ships.

- **Revision-risk model.** Extension of the metric-targetability taxonomy in `restoration_packet.py`. The current taxonomy classifies signals as direct / translated / investigate-first / avoid-direct. Add a per-suggestion **Revision Risk** label (low / medium / high) estimating the risk that the intervention will erase idiolect, create metric gaming, increase generic "humanizer" artifacts, damage clarity, damage genre expectations, make prose less publishable, overcorrect into artificial variance, preserve voice but weaken argument, or restore quirks that were intentionally edited out. Pairs each diagnostic trigger with the bad revision temptation and the better revision frame.

- **Source-of-smoothing localization.** Extension of `sliding_window_heatmap.py`. The heatmap currently says where the signal fires; this asks what *kind* of smoothing is happening there. Per hot zone, classify the dominant local phenomenon: syntactic flattening / lexical generalization / over-cohesion / connective overuse / idiom loss / paragraph uniformity / abstract-noun stacking / template rhetoric / generic authority cadence / reduced stance markers / reduced sensory or concrete detail / reduced argumentative friction. Output: "Hot zone 4 is not globally AI-like — it's specifically over-cohesive: adjacent-sentence cosine is high, connective density is high, and sentence variance is low, while idiolect survival remains normal." Gives writers something actually revisable.

- **Semantic preservation check.** Extension of `before_after_restoration.py`. The current post-check flags metric-gaming and signal direction; the next layer is semantic guardrails: claim inventory before/after, named-entity preservation, citation/authority preservation, stance preservation, modality preservation, causal-claim preservation, uncertainty-level preservation. Catches the failure mode where voice restoration accidentally makes an argument more forceful, less accurate, or less careful. Critical for policy / legal / nonfiction prose: "Voice restoration improved sentence variance and idiolect survival, but increased assertiveness — 7 hedged claims became unqualified claims." That's exactly the kind of thing a serious author-facing tool should catch.

- **Draft-history analysis.** Version-aware stylometric suite. Given multiple draft versions, answer: when did the smoothing enter? Was it gradual or sudden? Which revision introduced the voice drift? Did idiolect disappear in one pass? Did POS-bigram collapse occur after a global rewrite? Did later human editing restore or further flatten the voice? "Major distributional compression appears between v3 and v4, concentrated in sections 1, 4, and 6. Later edits restore lexical idiolect but not sentence-architecture variance." Stronger evidence than single-snapshot comparison.

- **Known-editor profile.** Underdeveloped and important. Given before/after edited-by-X pairs, learn an editorial transformation profile: what this editor typically changes, which signals shift after their edits, whether current drift resembles past human editing. Distinguishes "this was smoothed" from "this was smoothed in the ordinary way this editor smooths this writer" — for literary and institutional writing, that distinction is large. Bigger build because it requires labeled before/after pairs.

### Tier 4 — Advanced research / product layer

Higher-effort builds. Some are explicit 2.0+ horizon items; some are demand-driven.

- **House-style vs. author-voice decomposition.** Nested baselines (same-author-same-org / same-author-different-context / different-authors-same-org / same-genre-outside-org / broad reference) and decomposition. Classifies drift into author-specific signal vs. organizational/house-style signal vs. genre/task signal vs. topic vocabulary signal. Important for institutional writers — a piece can be authentically by someone and still sound unlike their essays because they're writing in an organizational voice. Bigger build because it requires curated nested-baseline structure.

- **Multi-author / multi-source segmentation.** Window-level feature vectors → unsupervised segment clustering → likely style-boundary detection → "voice discontinuity" flags → section-to-section voice-similarity matrix. Catches rewritten chapters in manuscripts, sections drafted by different staff in policy documents, AI-assisted inserts in essays. Output framing: "sections 2 and 5 are stylistically discontinuous from the rest of the document," not "these are different authors." `chapter_distinctiveness_audit.py` is adjacent prior work.

- **Counterfactual editing sandbox.** The user's "biggest missing feature" — and they're right that it would be conceptually powerful. Generate same-meaning variants under controlled perturbations (more sentence variance / lower connective density / restored idiolect / more concrete actors / less institutional abstraction / more or less baseline similarity). Use them *diagnostically*, not as final rewrites: "if only sentence variance is restored, the band call drops from Heavy to Moderate; if idiolect phrases are restored, voice Delta improves only slightly; if syntactic architecture changes, voice Delta improves substantially." Tells the user what the tool actually thinks is causing the signal. Research-grade: requires a meaning-preserving rewrite component (likely LLM-generated controlled variants), which adds a meaningful dependency footprint and a methodology question (how do we verify meaning preservation?). Architectural target, not a v1 ship.

- **Transformation-profile learning.** General version of the known-editor profile — learn typical transformation profiles from any before/after pair set, not just one editor. Useful for "what does light human copyediting look like in this register?" or "what does this institution's house-style enforcement look like?" Bigger build; pairs with known-editor as a generalization layer.

- **Interactive report UI.** Furthest from the current scope (CLI / Python / Claude-Code-plugin shape). Indefinitely deferred unless the framework adopts a UI layer.

### Explicit anti-goals

These are *not* on the roadmap, and the framework should resist building them even when users ask:

- **No single "authenticity score."** Will be abused immediately, regardless of how many caveats accompany it.
- **No "% AI-edited" dosage estimate.** The math doesn't entitle dosage grading; the 2026-05 corpus run found heavy-AI and light-AI clusters statistically indistinguishable on POS-bigram KL.
- **No model-attribution module.** "ChatGPT-ish / Claude-ish / Gemini-ish" attribution is fragile (model behavior drifts on a release cycle the framework can't track) and would tempt overclaiming. Fine in a research harness, not in author-facing reports.
- **No metric-optimizing rewrite engine.** A direct "make this pass" tool would create the very artifact the framework is designed to critique. Restoration packets are bounded prompts with required post-checks; that boundary stays.
- **No disciplinary report template.** Anything that looks like "evidence of misconduct" is out. The claim-license block is the load-bearing epistemic surface; sharpen it, don't write around it.

The five above refuse a verdict about the **text**. A second cluster refuses a verdict about the **person** — the category the list was silent on until the 2026-06-07 capability-whitespace discussion named it. The machinery for each is a short step from shipped surfaces (verification, General Imposters, idiolect, the impostor pools), which is exactly why the refusal has to be explicit:

- **No open-set author de-anonymization / 1-to-many attribution.** Unmasking a pseudonymous author by scanning a large, unknown, unconsented population is not a "harder verification" — it's a phase change in the error model (base rates, the open-set assumption, the absence of a matched candidate set) that requires a load-bearing operating point the framework refuses to ship. The harm of a false unmask (a dissident, an abuse survivor, a whistleblower) is categorically worse than a disputed essay grade, and by the "will be abused immediately" test this is a clearer anti-goal than the ones above. Closed-set, register-matched, *known*-candidate attribution with a gray-zone refusal already ships as General Imposters — that stays; the open-set population scan does not.
- **No demographic / author profiling.** Emitting "this prose looks like a [age / gender / L1 / personality]" promotes the framework's most-documented failure mode (ESL prose sits in the same stylometric region as RLHF output; 61% FPR on TOEFL essays, Liang et al. 2023) from a confound-to-control into a label-to-ship. The register classifier and fairness guardrails exist to treat linguistic background as a confound; profiling inverts them.
- **No sock-puppet / cross-document identity linkage as a shipped verdict.** "Were these N anonymous documents written by one person?" is the front end of the unmasking pipeline; the framework can't govern the downstream "therefore one person, therefore unmask" step. Within-*document* multi-author segmentation ("sections 2 and 5 are stylistically discontinuous," explicitly not "different authors") is the licensed, in-scope sibling.

The carve-out — and it matters — is the **lab**. These three may be *studied* as a refusal-curve laboratory: an instrument whose deliverable is the demonstration of where the signal exists and where it is bullshit, with the untooled LLM's confident attribution plotted against that curve. That is on-brand (it is the "Why no verdict" argument extended from *is-this-AI* to *who-is-the-author*), and it is the justification *for* naming the anti-goal, not an exception to it. The lab ships strength-of-evidence (likelihood ratios / posterior odds / proximity-to-distribution), never a name or a label; runs only against consented or held-out references with ground truth (never a live unconsented population); and invests in a deliberately *strong* foil, because a weak tool's null result proves nothing. The protocol, the three experiments, the E3 non-leak/redaction rule, and the dispatchable contract (a `validation`-surface harness with consent / redaction / foil-strength go/no-go gates) are specified in [`specs/21-attribution-refusal-lab.md`](specs/21-attribution-refusal-lab.md) — a build-gated spec whose code build is deferred behind the strong-foil resourcing decision. Until that lab is resourced, no attribution / profiling / linkage surface ships, even uncalibrated.

### Trustworthiness build order

**Status (2026-06-06): items 1–15 all shipped.** The Tier-4 research items below this list remain unbuilt. The order, as originally committed:

1. Stylometric masking profiles (input layer; cheapest; preconditions for other Tier-1 calls)
2. Register / genre conditioning (input gate; precondition for confounder audit and evidentiary gate)
3. Confounder audit / Layer D (interpretation layer; the most leveraged single addition)
4. Surface-disagreement resolver (interpretation meta-layer)
5. Minimum evidentiary conditions gate (output discipline)
6. Ablation reports (validation; cheap, high-interpretability)
7. Adversarial stress harness with robustness cards (validation; already on roadmap)
8. Negative / positive controls in reports (output discipline)
9. Calibration drift monitor (validation infrastructure)
10. Fairness / dialect / multilingual guardrails (output discipline)
11. Source-of-smoothing localization (writer-facing; extends heatmap)
12. Revision-risk model (writer-facing; extends restoration_packet)
13. Semantic preservation check (writer-facing; extends before_after_restoration)
14. Draft-history analysis (writer-facing)
15. Known-editor profile (writer-facing; bigger build)

Tier 4 items (house-style decomposition, multi-author segmentation, counterfactual sandbox, transformation-profile learning) ship as research extensions on a longer horizon. The interactive UI is indefinitely deferred.

### The shortest formulation

The shipped suite measures *what a text looks like stylometrically*. The trustworthiness layer answers a different and more important question: *compared to which legitimate alternatives, under what evidentiary conditions, with what confounders, and what revision moves would improve the prose without gaming the instrument?* The Tier 1 picks above are how the framework gets there.

## Interleaving: paired-release schedule

The previous two sections treat *new tools* and *new guardrails* as separate tracks, each with its own internal tier ordering. That presentation is honest about each track's internal priorities, but it leaves the *interleaving* question unanswered. Building either track in isolation produces predictable failure modes: tools-only ships new metrics with stale interpretive infrastructure (the framework's surface area for false confidence grows); guardrails-only ships interpretive richness over an underpowered signal vocabulary (the confounder audit can't make differential diagnoses without typed-discourse and agency signals to work with).

The right shape is **paired releases**: each release ships one new tool with the guardrail that makes it interpretable, with two dependency rules:

1. **Input-layer guardrails ship before any new tool depends on them.** Stylometric masking profiles and register / genre conditioning are precondition work — they make every existing and future call more reliable, and they ship as their own release without a paired tool.
2. **Discourse Move Signature is a hard prerequisite for the Tier-1 confounder audit.** The confounder audit's differential diagnosis ("compatible with AI smoothing, but also with professional copyediting and register shift") needs typed-discourse evidence to distinguish institutional prose (legal / policy / testimony) from AI smoothing — those genres have characteristically different concession-and-elaboration patterns, and without typed markers the confounder matrix can't separate them. Agency and Abstraction Audit (Release 4) is a *strengthening complement*, not a hard prerequisite: it adds the agency-loss family to the confounder matrix and sharpens the differential diagnosis, but the confounder audit's first useful version (Release 3) ships with discourse evidence alone. Release 4 then folds the agency family into the confounder matrix at that point.

Beyond those two rules, pairings are coherence-driven: the tool and the guardrail make sense shipping together because the guardrail extends a surface the tool feeds, or because the two address the same problem from different angles.

### Proposed paired-release sequence

This is the schedule once two near-term tracks have shipped: the calibration-breadth track (RAID + MAGE corpus fetchers, more calibrated thresholds, polarity-inversion correction against a fluent-native corpus); and the **adversarial-class fixture track** — DIPPER-class paraphrase fixtures, humanizer-tool output fixtures, and the existing `validation_harness.py`'s ROC-AUC / AP slicing across those classes (so the harness can already evaluate per-fixture-class performance using the existing report shape). The fixture track is *fixture-acquisition + per-class slicing in the existing harness*, not the per-signal robustness-card output shape — that output shape is Release 7's contribution. Each release in the schedule below is a small, coherent feature pair, releasable independently from the rest.

| # | New tool | New guardrail | Coherence rationale |
|---|---|---|---|
| **1** ✅ | _(none — input-layer infrastructure)_ | **Stylometric masking profiles + Register / genre conditioning** (shipped 1.31.0) | Precondition work. Every existing and future call gets more trustworthy. |
| **2** ✅ | **Paragraph Architecture Audit** (Surfaces T1) (shipped 1.32.0) | **Source-of-smoothing localization** (Trust T3) (shipped 1.32.0) | Paragraph-level signal pairs with the heatmap's "what *kind* of smoothing is firing here" classifier. The heatmap needs paragraph-shape data to localize over. |
| **3** ✅ | **Discourse Move Signature** (Surfaces T1) (shipped 1.33.0) | **Confounder audit / Layer D** (Trust T1) (shipped 1.33.0) | Typed discourse markers (contrast / concession / consequence / sequencing / metadiscourse) give the confounder matrix the evidence to distinguish "legal/policy memo" from "AI smoothing." Tool is the prerequisite for guardrail. |
| **4** ✅ | **Agency and Abstraction Audit** (Surfaces T1) (shipped 1.34.0) | **Revision-risk model** (Trust T3) (shipped 1.34.0) | Agency-loss signals pair with per-suggestion risk labels in `restoration_packet.py`. The new diagnostic vocabulary ("the local smoothing is agency loss") gets paired immediately with risk classification. Agency family also folds into the confounder matrix as a strengthening complement. |
| **5** ✅ | **Punctuation Cadence + Stance/Modality + Function-Word Grammar** (Surfaces T2 promotions × 3) (shipped 1.35.0) | **Ablation reports** (Trust T2) (shipped 1.35.0) | More feature families need an interpretability mechanism for which ones drive the call. Ablation reports become more interesting as the feature space grows. |
| **6** ✅ | _(none — output-discipline release)_ | **Minimum evidentiary conditions gate + Negative/positive controls** (shipped 1.36.0) | Output-discipline release. After the major Tier-1 tool/guardrail pairings land, the framework's reports gain the front-door evidentiary-posture label and the interpretability of known-authentic / known-smoothed reference points. |
| **7** ✅ | _(none — interpretation meta-layer)_ | **Surface-disagreement resolver + Adversarial robustness-card output shape** (shipped 1.37.0) | Cross-surface meta-interpretation + per-signal **robustness card** as a new output shape over the adversarial fixtures (which were acquired pre-schedule). The fixture acquisition + per-class slicing landed in the pre-schedule adversarial-class track; what Release 7 ships is the per-signal "this signal collapses under paraphrase but survives copyediting" reporting layer that the fixtures enable. Surface-disagreement is the natural meta-layer over a now-richer surface set. |
| **8** ✅ | **Construction Signature Audit** (Surfaces T3) (shipped 1.38.0) | **Semantic preservation check** (Trust T3) (shipped 1.38.0) | Interpretable syntactic evidence (clefts, fronted adverbials, agented vs. agentless passives) pairs with claim/entity/stance preservation guardrails — both are about making structural-level revision answer to meaning. |
| **9** ✅ | _(none — validation infrastructure)_ | **Calibration drift monitor + Fairness / dialect / multilingual guardrails** (shipped 1.39.0) | Validation infrastructure release. By this point the framework has enough surface area that infrastructure drift between releases needs explicit monitoring, and the linguistic-background caution surface needs to be visible at report level. |
| **10** ✅ | **Mimicry / Style-Cosplay Audit** (Surfaces T3) (shipped 1.40.0) | **Known-editor profile** (Trust T3) (shipped 1.40.0) | Both address "smoothed-but-by-whom" from different angles: mimicry detects over-conspicuous imitation; known-editor learns what genuine human editing of this writer looks like. They make sense as a pair. |
| **11** ✅ | **Phraseological Signature Audit** (Surfaces T3) (shipped 1.41.0) | **Draft-history analysis** (Trust T3) (shipped 1.41.0) | Phrase-frame mining is more interpretable across multiple drafts (which frames survived, which collapsed, which were introduced). Pairs naturally with version-aware analysis. |
| **12** ✅ (code) | **Semantic Trajectory Audit** (Surfaces T3) — `semantic_trajectory_audit.py` shipped; thresholds PROVISIONAL, §6.4 calibration pending | _(none — research extensions land separately)_ | The trajectory surface is the heaviest dependency footprint (SBERT-class). The tool is built; what remains is calibration on a GPU/SBERT host. From here forward, releases get less paired and more research-driven. |
| **13+** | _(longer horizon)_ | Counterfactual editing sandbox + House-style decomposition + Multi-author segmentation + Transformation-profile learning | Tier-4 research items on both tracks. Each is independently shippable; none is on a near-term schedule. |

### What this schedule deliberately doesn't do

- **It doesn't try to ship every Tier-1 surface before any Tier-2 or Tier-3.** Releases 5 and 8 specifically interleave Tier-2 and Tier-3 work into the sequence because the corresponding guardrails (ablation, semantic preservation) are most useful at those points.
- **It doesn't pair every release.** Releases 1, 6, 7, 9, and 12 are guardrail-heavy or research-heavy; releases 2, 3, 4 are tool-driven with their natural guardrail pair. Forcing a 1:1 tool-guardrail ratio per release would produce artificial pairings.
- **It doesn't commit to a calendar.** The number of releases ahead is large; each is independently shippable; the framework's release cadence depends on the calibration-breadth track's progress and on user demand for specific surfaces. The order is the commitment, not the timing.
- **It doesn't replace the per-track tier orderings.** The Surfaces and Trustworthiness sections above keep their internal priorities; this section sequences releases *across* the two tracks. If the framework ever needs to deviate (e.g., a specific surface gets pulled forward by user demand), the per-track priority tells you what's safe to skip; the paired-release rationale tells you what dependency is broken if you do.

### Anti-pattern check

The single most-damaging anti-pattern this schedule resists is **shipping new tools without their interpretive guardrails**, which would systematically grow the framework's surface area for false confidence. Every tool release in the sequence above lands with either (a) an existing guardrail it strengthens, (b) a new guardrail that makes it interpretable, or (c) precondition guardrail work having already shipped in an earlier release. No release adds analytic firepower without also adding interpretive discipline.

The 2.0 refactor target (Compression-of-Choice / Stylistic Choice Entropy) sits beyond this entire schedule. When 2.0 lands, every existing surface gets rewritten as a special case of compression in some choice set, and the trustworthiness layer gets reframed as compression-aware (e.g., the confounder audit becomes "differential diagnosis across choice-set perturbations" rather than across signal directions). That's an architectural rewrite, not a release.

### Post-R12 priorities (informed by 2026-05-11 prior-art survey)

A merged-and-verified prior-art survey on 2026-05-11 (three LLM passes — Claude / GPT / Gemini — reconciled against fetched URLs rather than pattern-match-on-citation-style) confirmed SETEC's novelty as a synthesis (no project hits 4+ SETEC features; `idiolect` by Andrea Nini is the single 3-feature ancestor) and surfaced three concrete additions worth capturing in the roadmap. Each is independently shippable; none is on a paired-release rhythm because the framework has moved past the R1–R12 schedule into research-driven territory.

#### A. README "Why no verdict" docs section (smallest, ship anytime) — ✅ SHIPPED

**Status (2026-06-06): shipped.** The README carries a "Why no verdict" section. Original scoping kept below.

**Finding.** The OpenClaw humanizer ecosystem (`openclaw/skills` archive, ~4,500 stars; `brandonwise/humanizer` as the substantive example with 136 tests including `calibration.test.js`, 29 pattern detectors, 500+ vocabulary terms in 3 tiers, platform-specific thresholds for LinkedIn/Reddit/etc.) is a mature adversarial complement to forensic detection tooling. Humanizer tools help users *avoid* detection — the inverse of SETEC's surfacing-evidence posture. The two ecosystems share vocabulary (delve / tapestry / em-dash overuse) but inverted purpose.

**Roadmap implication.** Crystallize the framework's evidence-not-verdict argument in user-facing docs. The argument has three legs: (a) the humanizer ecosystem exists and is mature, (b) verdicts shipped as load-bearing become humanizer optimization targets, (c) the Stylometry-to-the-people policy (no anchored thresholds shipped from labeled corpora; see `scripts/calibration/PROVENANCE.md`) is the principled response — calibration moves into user hands, not vendor hands, and the framework's load-bearing artifact becomes the methodology rather than the numbers.

**Scope.** Single docs PR. New README section (between "Design principles" and "License", or front-mounted between "Choose the question" and "Plugin skills"). ~300–500 words. References OpenClaw as the load-bearing example; ties into claim-license discipline.

**Phasing.** v1.X.X PATCH bump. One file modified. No code, no tests.

**Risks.** Minimal. Worth running past the maintainer for tone so the framing stays technical rather than slipping into evangelism.

#### B. Costa 5-state authorship distinction (medium; spec + schema change) — ✅ SHIPPED

**Status (2026-06-06): shipped end-to-end.** B.1 spec (`internal/SPEC_authorship_states.md`), B.2 `manifest_validator` `authorship_state` field, and B.3 per-state claim-license routing across all 10 `claim_license`-using scripts (waves 1–4, v1.49.0–v1.58.x). Original scoping kept below.

**Finding.** Daniel Bruno Corvelo Costa's "Global Proof-of-Reality Infrastructure" submission (2026-03-17, non-normative public comment to the SEC Crypto Task Force; one-person proposal hosted on sec.gov, no regulatory standing, but conceptually substantive) proposes a 5-state authorship taxonomy more granular than SETEC's current binary-ish `ai_status` field. The states: human-authored unmodified / human-authored AI-modified / AI-generated from human inputs / fully AI-generated / multi-source composite — each with a different evidentiary weight under Costa's RCES (Reality Claim Evidence Sets) framing.

**Why this matters operationally.** Smoothing-diagnosis routinely encounters `human_authored_ai_modified` prose — that's the dominant real-world case, and the current `ai_status: pre_ai_human / ai_generated` binary forces it into either-wrong category. Claim-license blocks could route off `authorship_state` to produce sharper licensure ("this audit licenses inference about human-authored AI-modified prose; it does not license inference about fully-AI-generated prose"), and the validation harness's per-class slicing would gain a richer label axis.

**Roadmap placement.** Cross-references "Trustworthiness expansion → Tier 3 — Writer-facing upgrades" (the per-state claim-license routing is a Tier 3 extension of `claim_license.py`); independent of, but compatible with, every existing manifest convention.

**Scope, phased across three PRs:**

1. **Spec only.** `internal/SPEC_authorship_states.md` (gitignored). 15–25 KB. Covers the 5-state taxonomy, mapping rules from existing corpora (RAID, MAGE, EditLens — most rows default `null` because source corpora don't carry this distinction natively), operational definition of `multi_source_composite` (the vaguest of the five; needs anchoring against concrete examples like "AI-edited draft quoted in a multi-author anthology"), and the orthogonality argument against existing `ai_status` and `editing_status` fields.
2. **Validator + manifest field.** `manifest_validator.py` accepts a new `authorship_state` field with `null` default for backwards compat. `ALLOWED_AUTHORSHIP_STATE` vocabulary added. Tests pin the new vocabulary and the null-allowed rule. CHANGELOG entry tagged MINOR (additive schema change).
3. **Audit-script routing.** Per-task-surface claim-license blocks gain per-state language. Optional behavior; default unchanged. The longest sub-phase because every task surface producing a `ClaimLicense` block needs review.

**Phasing.** v1.X.0 spec PR, v1.X+1.0 validator PR, v1.X+2.0 audit-routing PR. Three weeks calendar at sustainable cadence; one week if compressed.

**Risks.** `multi_source_composite` is fuzzy; needs operational pinning in the spec. The new field overlaps semantically with `editing_status` and `ai_status`; orthogonality argument is load-bearing. Mapping existing corpora is lossy; the framework doesn't pretend to know.

#### C. DivEye surprisal-distribution signal (largest; needs its own model-choice decision) — ✅ SHIPPED

**Status (2026-06-06): shipped.** `surprisal_backend.py` (pluggable causal-LM wrapper) + `surprisal_audit.py` (standalone audit, PROVISIONAL banding) + Tier-4 integration in `variance_audit.py` + a dedicated `surprisal` dependency tier + calibration-pipeline plumbing (`--surprisal-dtype`, per-comparator routing). Original scoping kept below.

**Finding.** DivEye (IBM, Basani & Chen, TMLR 2026; also ICML DIG-BUG 2025; CC BY-NC-SA 4.0, GPL-3 incompatible) demonstrates state-of-the-art AI-prose discrimination using surprisal-distribution features at the input layer of a classifier — specifically, mean / variance / autocorrelation of per-token surprisal under a causal language model. LLM-generated prose tends to produce more uniformly-surprising tokens than human prose; humans cluster their surprise; LLMs flatten it. The signal is structurally orthogonal to every existing Tier 1 SETEC signal (which are lexical / syntactic / aggregate-distributional).

**License posture.** Code is CC BY-NC-SA 4.0 (cannot import). Math is unrestricted. Clean-room reimplementation is the path: implement the per-token surprisal mean / variance / autocorrelation primitives against SETEC's own corpus discipline, license the implementation under GPL-3-or-later like the rest of the framework.

**Why this is bigger than items A and B.** Surprisal requires a *causal* language model (predicts next-token probability), not an embedding model. The 2026-05-11 embedding-model-choice spec we just finished revising (mxbai / Gemma / Harrier / Qwen / bge candidates, no-priority posture, §6.4 fixture suite as the load-bearing decision) does not apply — embedding models give vectors, not surprisal. Surprisal needs its own model-choice spec with its own §6.4-equivalent fixture-test gate. Candidate causal LMs at the right scale (1–3B params for laptop-runnable cheap surprisal): Phi-3 Mini (Microsoft, MIT), TinyLlama (Apache 2.0), Llama 3.2 1B (Meta custom license), Qwen 2.5 1.5B (Apache 2.0), GPT-2 small (OpenAI, MIT; old but well-understood).

**Roadmap placement.** Cross-references "Stylometric surface expansion → Tier 1 — Near-term builds" as a fourth Tier-1 surface alongside Paragraph Architecture, Discourse Move Signature, and Agency/Abstraction Audit. But unlike those three, it carries a model dependency that ripples into a sibling research project (the surprisal-model-choice spec), so it sits *above* them in cost rather than alongside.

**Scope, phased across four-plus PRs:**

1. **Specs.** `internal/SPEC_surprisal_signal.md` + `internal/SPEC_surprisal_model_choice.md`. Two gitignored documents. The signal spec defines the mathematical content (surprisal mean / variance / autocorrelation, fixed-window vs whole-document, normalization choices). The model-choice spec is the structural analog of the embedding spec — candidates listed, no priority designated, fixture test as load-bearing decision point.
2. **`surprisal_backend.py`** — pluggable causal-LM wrapper mirroring the shape of `embedding_backend.py`. Alias table, lazy load, honest failure (no silent fallback), deterministic mode. Tests with stub LM. Module exists but no audit-script integration yet.
3. **`surprisal_audit.py`** — standalone audit script. Computes the three statistics over a draft. JSON + markdown output. `ClaimLicense` block. `task_surface: smoothing_diagnosis`. PROVISIONAL banding only (Stylometry-to-the-people compliance). 30–50 tests.
4. **Integration into `variance_audit.py`** as opt-in Tier (Tier 4? or extension to Tier 3 cohesion?). Framework already has tier infrastructure; adding a tier follows the established shape.
5. **§6.4 fixture suite for surprisal-model choice** runs operationally on the calibration host. Same discipline as the embedding-model fixture work. Not a PR; an operational rollout step.

**Phasing.** v1.X.0 spec PRs (the two specs ship in one PR or two; either works). v1.X+1.0 backend. v1.X+2.0 audit. v1.X+3.0 variance_audit integration. v1.X+4.0+ calibration runs.

**Risks.** Whiplashing the surprisal-model decision the way the embedding-model decision whiplashed four times in one day. Mitigation: start with the no-priority posture from PR 1 of the spec. Memory footprint: a 1–2B-param causal LM is ~2–4 GB on disk; optional tier, users opt in; README costs section gains a row. Determinism: causal LM inference is non-deterministic across batch sizes by default; deterministic-algorithms mode at the framework level. Compute: per-token forward pass at every position is expensive; sharded toolchain (v1.44.x) becomes essential at corpus scale.

#### Cross-cutting sequencing

| Order | Item | Effort | Blocks downstream? |
|---|---|---|---|
| 1 | README "Why no verdict" (A) | 1–2 hours | No |
| 2 | Authorship-states spec (B.1) | 2–3 hours | B.2 |
| 3 | Authorship-states validator (B.2) | 3–4 hours | B.3 |
| 4 | Surprisal specs (C.1) | 4–6 hours | All C work |
| 5 | Authorship-states audit routing (B.3) | 6–8 hours, many touchpoints | No |
| 6 | Surprisal backend → audit → variance_audit integration (C.2–C.4) | 1–2 weeks calendar | Calibration runs |

Items 1–4 are spec-and-small-PR shape: appropriate for sessions where review capacity is constrained. Item 5 is a docs-pass-session shape. Item 6 is a multi-week research-grade project that probably waits for the AMD desktop calibration host to be live and for the embedding-model §6.4 fixture suite to complete (so two model-choice decisions don't run in parallel).

The 2.0 refactor target (Compression-of-Choice) sits beyond this roadmap. None of items A–C anticipate it; all three integrate cleanly into the 1.X surface.

### Post-1.101 follow-ups (informed by the 2026-05-18 PR cascade)

A nine-PR cascade between 2026-05-18 and 2026-05-18 (#99 MAGE polarity flips → #101 embedding dtype/device → #100 cloud bake-off matrix → #102 length-sort benchmark → #103 per-comparator routing → #104 dtype-in-Markdown → #105 calibration-pipeline `comparator_class` threading → #106 per-(judge × generator) routing infrastructure → version cascade closure) shipped the per-comparator routing infrastructure end-to-end (`ThresholdSpec.direction_by_comparator` + `direction_by_comparator_and_slice`, `resolve_direction_with_slice`, CLI flags on every entry point, calibration-pipeline plumbing, cache-identity contracts, provenance replay). Several follow-ups surfaced during that cascade that are explicitly out-of-scope for the merged PRs but worth pinning so they don't drift.

**Status as of 2026-05-19.** Items **D, E.2, E.3, F.2 shipped** (PRs #112, #117, #118, #119). Items **F.1, F.3, G** remain operator-data-blocked; item **E.1** remains operator-justify-blocked (don't optimize sequential matrix until it's the bottleneck on a real cloud run). See per-item status lines below and the closeout row in the cross-cutting sequencing table.

#### D. Per-(judge × generator) calibration-pipeline plumbing (mirror of #105 for #106's infrastructure) — **shipped (PR #112)**

**Status.** Shipped by PR #112. `judge` / `generator` thread through `validation_harness.score_smoothing_entry`, `calibrate_thresholds.score_corpus`, `calibration_survey.py`, `calibrate_thresholds.py`, and `bakeoff_matrix.sh` (`SETEC_JUDGE` / `SETEC_GENERATOR` env vars); cache identity includes both fields; provenance replay surfaces `--judge X --generator Y`. The original finding + scope is preserved below as historical record.

**Original finding.** PR #106 (1.100.0) shipped `direction_by_comparator_and_slice` on `ThresholdSpec` + the `resolve_direction_with_slice` helper + `--judge` / `--generator` CLI flags on `variance_audit.py`. PR #105 (1.101.0) shipped `comparator_class` threading through the entire calibration pipeline (`validation_harness.score_smoothing_entry` → `calibrate_thresholds.score_corpus` → `calibration_survey.py --comparator-class` → `bakeoff_matrix.sh SETEC_COMPARATOR_CLASS`). The symmetric gap remains: `judge` and `generator` are not yet threaded through the calibration pipeline. An operator running `bakeoff_matrix.sh --corpus raid` against the eventual 13-cell RAID override population (item F below) will still get the per-class routing only; the per-(judge × generator) cell verdicts the inner-most table is designed to deliver won't fire on the calibration path.

**Why this matters operationally.** Same shape as PR #105's load-bearing closure for `comparator_class`. Without this, the standalone `variance_audit.py --comparator-class raid --judge chatgpt --generator gpt-4o` produces the correct per-cell verdict but the calibration pipeline (the workflow operators actually use for the matrix bake-off) silently falls back to per-class direction. Same end-to-end parity discipline the cascade has been chasing.

**Scope, single PR:**

1. `validation_harness.score_smoothing_entry(..., judge: str | None = None, generator: str | None = None)` — forward into `classify_compression`.
2. `calibrate_thresholds.score_corpus` — `getattr(args, "judge", None)` / `getattr(args, "generator", None)` plumbing.
3. `calibration_survey.py --judge` / `--generator` CLI flags, forwarded into the inner Namespace.
4. `calibrate_thresholds.py --judge` / `--generator` CLI flags.
5. `bakeoff_matrix.sh` — new `SETEC_JUDGE` / `SETEC_GENERATOR` env vars; appended to `BASE_ARGS` when set. No corpus-based default (unlike `SETEC_COMPARATOR_CLASS` which auto-defaults from `SETEC_CORPUS_LABEL`) — judge and generator are slice axes within a corpus, not properties of the corpus itself.
6. Cache identity: add `judge` + `generator` to `scoring_meta` / `interim_meta` / `cache_is_compatible`. Same contract shape as `comparator_class`.
7. Provenance replay: thread into `_build_harness_command` so ledger entries surface `--judge X --generator Y`.

**Phasing.** Single `feat:` PR, 1.X+1.0 MINOR. ~300 lines of plumbing + ~150 lines of tests mirroring `test_comparator_class_calibration_pipeline.py`. Independent of item F (operator data); ships infrastructure parity with PR #106 regardless of when the override table gets populated.

**Risks.** Minimal. The infrastructure exists; this is mechanical plumbing of two more kwargs through the same chain PR #105 already plumbed.

#### E. `bakeoff_matrix.sh` operational follow-ups (deferred from PR #100) — **E.2 and E.3 shipped; E.1 still pending operator-justify data**

PR #100 (1.97.0) shipped the cloud-portable matrix runner but explicitly deferred three operational extensions:

1. **Per-GPU parallelism within one matrix process.** _Status: still pending; operator-justify-blocked._ Spec called for `xargs -P N` parallelization of Phase A/B loops; PR #100 shipped sequential cells, with the workaround being multiple matrix processes pinned to different GPUs via `CUDA_VISIBLE_DEVICES` + per-process `SETEC_BAKEOFF_DIR` values. The follow-up: optional `SETEC_MATRIX_PARALLELISM=N` env var that uses `xargs -P` (or GNU parallel as a fallback) to run N cells concurrently within one process. The operator data point that justifies it: a real cloud run where the sequential-cells time is the long pole vs. an alternative resource (GPU memory, model-load overhead).
2. **Auto-trigger slicer + polarity audit chaining.** _Status: shipped (PR #117, 1.103.0)._ `scripts/calibration/queue_slice_after_matrix.sh` watches `$SETEC_BAKEOFF_DIR/` for completed `survey_*.json` files and triggers `slice_bakeoff_v2.py` + `polarity_audit.py` automatically. Marker-gated idempotency (`<survey>.sliced` + `<survey>.polarity`); `--once` mode for cron-style invocation; routing axes flow from the same env vars as the matrix script.
3. **Length-stratified subsampling.** _Status: shipped (PR #118, 1.104.0)._ `calibration_survey.py --length-stratify N --length-buckets B [--length-stratify-floor M]` subsamples the manifest by length bucket before scoring; composes with the existing `--max-entries` (length stratifies first via temp manifest, label stratifies second on filtered set); the survey JSON's `length_stratify` block records bucket bounds + populations + sample counts for replay equivalence.

**Phasing.** Three independent `feat:` PRs, each ~150-300 lines. Item 1 (parallelism) needs operator validation data first — should land only when an operator reports the sequential matrix is actually the bottleneck on a real cloud GPU. Items 2 and 3 are ship-anytime infrastructure.

**Risks.** Item 1 has the most subtlety (`xargs -P` interacts with `CUDA_VISIBLE_DEVICES` in ways that could surprise — concurrent processes contending for the same GPU is a common cloud footgun). Items 2 and 3 are straightforward.

#### F. Operator-side data: populate the 13 RAID `comparator_dependent` cells — **F.2 infrastructure shipped (PR #119); F.1 + F.3 pending operator data**

**Finding.** PR #103 (1.98.0) shipped per-comparator-class routing with one populated override (`surprisal_sd: {"raid": "lt"}` for the 4 RAID `globally_inverted` cells). PR #106 (1.100.0) shipped the deeper per-(judge × generator) infrastructure with the override table **empty** — the 13 RAID `comparator_dependent` cells from the 2026-05-18 audit need the per-(judge × generator) slice analysis to settle before the table can be populated. Once it settles, populating the table is a **single PR with no plumbing change** — just the override entries on the affected specs (`adjacent_cosine_mean`, `surprisal_mean`, `surprisal_acf_lag1`, and 13 specific cells across them).

**Blocker.** Operator-side data work, not framework code. Needs the 2026-05-18 RAID 5K bake-off bundle re-run with `slice_bakeoff_v2.py --crosstab judge,generator --audit polarity` so the per-(LM-judge × generator-family) cell verdicts are surfaced. The current operator notes call this out as "deferred to a future chunk informed by per-generator RAID data."

**Once data lands, follow-up has three parts:**

1. **Single populated-table PR.** _Status: pending operator data._ Edits to `variance_audit.COMPRESSION_HEURISTICS[<signal>].direction_by_comparator_and_slice` on the affected specs. Five-line table per signal. CHANGELOG entry tagged `fix:` / PATCH (encoding empirically-derived directions, not adding a new feature).
2. **Synced copies in `polarity_audit` / `slice_bakeoff_v2`.** _Status: shipped (PR #119, 1.105.0)._ Both modules now carry `DEFAULT_REGISTRY_DIRECTIONS_BY_COMPARATOR_AND_SLICE` (resp. `SIGNAL_SPECS_BY_COMPARATOR_AND_SLICE`), `resolve_registry_direction_with_slice` / `resolve_signal_direction_with_slice`, `--judge` / `--generator` CLI flags, and the slicer's per-cell emission + integrated polarity-audit handoff both use the same resolver. Override tables ship EMPTY — item F.1 fills them when data lands.
3. **End-to-end validation.** _Status: pending operator data._ Re-run RAID bake-off matrix with `--judge` / `--generator` populated (item D's plumbing is now in place per PR #112) and confirm the per-cell verdicts match the polarity-audit findings cell-by-cell.

**Phasing.** Item 1 ships immediately when data lands. Item 2 followed item D ahead of data. Item 3 is operational validation, not a PR.

#### F-bis. Compression-polarity resolution: reconcile the `polarity_audit` / `slice_bakeoff_v2` default mirrors — **integrator + tests shipped (compression-polarity PR, 2026-06-02); mirror reconciliation pending**

**Resolution shipped.** The `variance_audit` compression integrator's UNBASELINED default directions for the Tier-4 surprisal signals were the MAGE *AI-detection* directions (`surprisal_mean`/`surprisal_sd` `gt`, `surprisal_acf_lag1` `lt`), which made the band flag high-variance / anti-smoothed prose as "smoothed" — the exact polarity inversion this gate exists to catch (a real instance: an unbaselined literary-fiction run banded "Moderately smoothed" while the standalone surprisal band correctly banded "typical"). The fix re-bases the DEFAULT to the **smoothing** directions (`mean`/`sd` `lt`, `acf` `gt`), shared with `surprisal_audit`'s standalone band via the new canonical `surprisal_backend.SMOOTHED_DIRECTION`. The MAGE empirical directions are preserved as `direction_by_comparator["mage"]` overrides (applied only when `comparator_class="mage"`); RAID keeps its `surprisal_sd: lt` override (now == the default). `adjacent_cosine_mean` (polarity corpus-unstable) is gated from the unbaselined band via `_POLARITY_UNBASELINED_GATE` rather than asserting a contested direction. A regression test (`test_compression_polarity.py`) pins integrator↔standalone-band agreement.

**Pending — mirror reconciliation.** `polarity_audit.DEFAULT_REGISTRY_DIRECTIONS` and `slice_bakeoff_v2.SIGNAL_SPECS` still carry the pre-fix MAGE-as-default direction values, and their `*_BY_COMPARATOR` tables lack the new `surprisal_mean` / `surprisal_acf_lag1` `mage` overrides. Per their "keep in sync with the registry" contract these must move in lockstep: flip the three surprisal defaults to the smoothing directions and add the `mage` overrides (mean `gt`, acf `lt`; `surprisal_sd` already has `{"raid": "lt"}` and gains `{"mage": "gt"}`). Mechanical, but it touches the MAGE/RAID calibration toolchain, so it should land with the full calibration/polarity test-suite as the gate. Until then the two calibration mirrors are documented-stale relative to the integrator (no CI break — their tests pin their own values).

#### G. Original Tier-4 audit writeup extensions (operator-side, data-collection) — **pending operator data; framework-side support already in place**

The 2026-05-18 Tier-4 surprisal audit writeup against the *Conceptual Repair and Its Counterfeits* essay surfaced three follow-up extensions the operator explicitly called out as "what I'd want to do next." None require framework code; all are operator-side data work that the framework already supports via existing CLI surfaces.

1. **Labeled AI-only control corpus.** Five to ten LLM-generated essays of comparable length / register, scored against the same four models (GPT-2 / Pythia / TinyLlama / OLMo-2-0425-1B). The contrast against the essay's placement in the writeup's per-model surprisal table is the load-bearing finding; an AI-only control anchors the absolute numbers. Pure data work; no framework change.
2. **Second-cycle scoring on the published version.** Score the final published version of the essay (after any hand-edits between v2 and publication) against the same four models. Compare to the v2 numbers. The pre-publication hand-edit pass is typically larger than the SETEC-driven v1→v2 transition, so the cross-cycle delta is informative.
3. **Section-by-section sliding-window scan on v2.** Already supported by `surprisal_audit.py --sliding-window --window-size 200 --stride 100`. The writeup's chunked helper aggregates per-document and loses this resolution; the framework's standalone CLI does the per-section scan natively. Pure operator action.

#### Cross-cutting sequencing

| Order | Item | Status | Effort (estimated) |
|---|---|---|---|
| 1 | D — Judge/generator calibration-pipeline plumbing | ✓ shipped (PR #112) | 4–6 hours |
| 2 | E.2 — Auto-trigger slicer chaining | ✓ shipped (PR #117, 1.103.0) | 3–4 hours |
| 3 | E.3 — Length-stratified subsampling | ✓ shipped (PR #118, 1.104.0) | 4–6 hours |
| 4 | G.1/G.2/G.3 — Operator data extensions | Pending operator data (no PR) | Operator-side |
| 5 | F.1 — Populate the 13 RAID cells (once data lands) | Pending operator data | 1–2 hours |
| 6 | F.2 — Deeper-shape synced copies | ✓ shipped (PR #119, 1.105.0) | 3–4 hours |
| 7 | F.3 — End-to-end RAID validation | Pending operator data (no PR) | Operator-side |
| 8 | E.1 — Per-GPU parallelism | Pending operator-justify data | 6–8 hours |

**As of 2026-05-19.** The framework infrastructure for the post-1.101 cascade is closed: items D, E.2, E.3, F.2 all shipped (PRs #112, #117, #118, #119). The remaining items are operator-side data work (F.1, F.3, G.1/G.2/G.3) or operator-justify-blocked (E.1, where the sequential matrix needs to be empirically the bottleneck before parallelism is worth the `xargs -P` × `CUDA_VISIBLE_DEVICES` footgun). When operator data settles the 13 RAID `comparator_dependent` cells, F.1 is a single-PR five-line-table fix on the affected `direction_by_comparator_and_slice` specs — no further plumbing needed.

## Capability-whitespace additions (2026-06-07)

A capability-whitespace survey asked the inverse of every section above: not "what's the next signal / guardrail on the planned tracks," but *what could a tool like this do that is absent from both this roadmap and the `specs/` frontier brief?* The `specs/00` brief already saturates the detection / stylometry / neural-embedding frontier (LUAR/Wegmann, Fast-DetectGPT, intrinsic dimension, Raidar, watermark-key, the generative voice-matching companion). The genuine whitespace sits **off** those axes — in measurement dimensions outside lexis/syntax/distribution, in validating against humans rather than labeled corpora, in language-agnostic operation, and in the statistical-rigor layer under the abstention calls. Four such themes are adopted here. Each ships its flagship capability first (stdlib-only, laptop-trivial, **no verdict, PROVISIONAL/descriptive** by default, per "Stylometry to the people"); each names sibling work deferred behind it. Build contracts live in `specs/17`–`specs/20`.

These four are independently shippable and do not sit on the R1–R12 paired-release rhythm — they are research-driven additions in the same idiom as the Post-R12 and Post-1.101 sections above.

### W2. Non-lexical measurement axes — sound and affect

Every one of the 56 shipped signals lives in lexical, syntactic, distributional, or surprisal space. Two orthogonal *measurement* axes are structurally absent:

- **Sound-texture / phonological stylometry (flagship — `specs/17`, `sound_texture_audit.py`).** Alliteration, assonance, consonance density, and consonant-class (plosive / fricative / sibilant / nasal / liquid) profile. The suite measures sentence *rhythm* only by length variance; it never measures rhythm at the level of sound. Ships as an **orthographic-onset proxy** (stdlib, no pronunciation dictionary) with the claim-license stating plainly that it approximates sound from spelling and is not a phonetic transcription; a `cmudict`/`g2p` true-phoneme path is a noted optional enhancement. New descriptive `sound_texture` surface; baseline-relative comparison optional; no verdict. Cross-references "Stylometric surface expansion → Tier 4" (fiction-relevant craft surfaces).
- **Affect / emotional-arc trajectory (sibling, deferred).** Reagan-style valence arcs over document position — a distinct axis from the shipped semantic-trajectory surface, which deliberately measures *topic* cohesion and stays on the "topic ≠ style" side of its boundary. Deferred behind the sound-texture flagship: a defensible valence lexicon and the "this measures affect, not style/quality" claim-language need design work before code, and the arc is most meaningful baseline-relative.

### W3. Validation against humans, not just labeled corpora

The framework's load-bearing thesis is that *source triage is judgment work, not algorithm* — yet nothing instruments that judgment.

- **Human-judgment / inter-rater agreement (flagship — `specs/18`, `triage_agreement.py`).** Ingests operator triage labels (earned / unearned, or flag / clear) alongside the framework's surfaced candidates and reports the agreement between framework flags and human calls: confusion matrix, percent agreement, Cohen's κ, prevalence- and bias-adjusted κ (PABAK), and a seeded bootstrap CI on κ. Closes the loop the framework keeps open on principle, and gives the "most flags resolve as earned on triage" claim an *actual measured number* per corpus. New surface reuse: `validation`. Descriptive — reports agreement, not which side is right.
- **Reader-perception / behavioral validation (sibling, deferred).** All current validation is text-intrinsic or corpus-label-based; nothing connects a "smoothed" measurement to whether prose actually *reads* flat to humans. A perception-study harness needs human subjects and an ethics/consent posture, so it is named and deferred, not built in-sandbox.

### W5. First-class language-agnostic operation

The pipeline is English-only (spaCy `en`). Multilingual appears **only** as a defensive caution (fairness guardrails) and a planned ESL/L2 *fairness fixture* slice (`specs/05`) — both about English written by L2 speakers, not about analyzing a non-English author's prose at all.

- **Cross-lingual (parser-free) voice distance (flagship — `specs/19`, `crosslingual_voice_distance.py`).** The classical multilingual-attribution backbone: character n-gram profiles, punctuation profile, token-length and sentence-length distributions, whitespace/diacritic statistics — **no spaCy, no English assumption**, works on any Unicode script. Computes a target-vs-baseline distance with a required `--lang` provenance tag. Honest about its ceiling: it is language-*agnostic*, not language-*aware* (no morphology, no function-word list), so it refuses morphology-dependent voice claims. New surface reuse: `voice_coherence`. PROVISIONAL; descriptive. This is the door-opener; per-language function-word lists / non-English spaCy pipelines are the heavier follow-on.

### W7. The statistical-rigor layer under the abstention calls

The framework abstains a lot (GI gray zone, `uncalibrated` bands, evidentiary-posture labels) and reports bootstrap CIs — but the abstention itself has no formal coverage guarantee, and corpus construction is brute-force survey.

- **Split-conformal abstention gate (flagship — `specs/20`, `conformal_gate.py`).** Given a calibration array of a signal's nonconformity scores on a labeled reference class and a target score, emit a distribution-free, finite-sample conformal p-value and a prediction *set* at coverage 1−α — turning "the band reads uncalibrated" into "abstain, with a guaranteed error rate at this operating point." A methodology wrapper over existing signals, not a new detector; reuses `validation`; refuses to become a verdict (an empty or full prediction set is a legitimate, licensed output). Stdlib only.
- **Active-learning corpus construction; real-time in-editor "voice-drift meter"; style-space (UMAP/t-SNE) projection (siblings, deferred).** Each is a real "could": uncertainty-sampling to tell an operator which samples to label next; an incremental write-time drift readout distinct from the indefinitely-deferred interactive *report* UI; a static style-space projection of a draft among baseline + impostors. Named here so they don't drift; none is a near-term commitment.

### What this group deliberately does not do

It does not relax any anti-goal. The sound-texture and cross-lingual surfaces are descriptive and refuse voice/AI/quality verdicts; the agreement and conformal capabilities are about *characterizing and bounding* the framework's own calls, never about manufacturing a verdict the math doesn't entitle. Two capabilities the same survey surfaced — **defensive anti-cloning / voice-hardening** and **privacy-preserving / federated voice computation** — are the most on-theme gaps relative to the project's own SETEC premise, but they are larger, ethically load-bearing builds; they are recorded in the "Voice fingerprint risk surface" section's orbit and are **not** part of this group.

## Open architectural questions

### Layer A

- Should `COMPRESSION_HEURISTICS` thresholds and weights be configurable? Currently constants in `variance_audit.py`. Configurable would let users tune for their register without editing source.
- Per-character variance signals in multi-POV fiction. MATTR within one POV character's passages may differ from another's. The current variance audit doesn't slice by POV. Worth considering once the per-POV diagnostic matters.
- Scene-shape-aware diagnostics. The lexical-compression signal does different work in different chapters (closed-room scenes have small vocabulary scope; revision smoothing tightens vocabulary). Distinguishing the two is hard to operationalize.

### Layer B

- AIC-7 named subtypes vs. evidence categories. Currently subtypes nest under evidence categories. Promoting subtypes to top-level may read more cleanly.
- Cross-cutting flag relationships. Lexical genericism touches both AIC-2 and AIC-7. Real audit experience will surface which boundary placements are useful.

### Layer C

- Voice attribution for narrators with multiple registers. The current voice test is binary; a multi-register version would ask "which of this character's registers should this be in?"
- The "earned by frame" verdict. Some passages are earned because the surrounding prose explicitly diagnoses them. A third verdict beyond earned and unearned is worth naming.

### Cross-layer

- Calibration of the directional-cluster threshold (0.7) against a labeled corpus. Currently a heuristic with documented step effects (3-feature clusters require 3/3, 4-feature 3/4, 5-feature 4/5).
- Calibration of the band-classification fraction thresholds (0.15, 0.40) against a labeled corpus. Currently fractions of available signal weight, not absolute percentages of evidence.
- POS-bigram KL/JSD smoothing constant. Currently add-one Laplace smoothing on the union of bigrams; literature suggests add-α with α<1 may be more principled. Calibration against a labeled corpus is the right time to tighten this.
- **Dosage signal is missing.** The 2026-05-08 corpus run (9 post-2022 essays each annotated by AI-involvement degree, cleaned of CSS contamination, evaluated against a 50-file pre-AI baseline) found heavy-AI-cluster mean KL = 0.167 and lighter-AI-cluster mean KL = 0.156. Statistically indistinguishable. POS-bigram KL detects the post-AI cohort against a pre-AI baseline; it does not grade AI-involvement amount within the post-AI cohort, on the corpora tested. If the framework wants a dosage signal, it needs different machinery than POS-bigram KL alone. Candidates worth investigating: model-specific bigram fingerprints (the multi-model collaborative regime may carry distinguishable per-model residue), word-level n-gram template residue (the existing `manuscript_repetition_audit.py` and `chapter_distinctiveness_audit.py` operate at word-level rather than POS-level and might pick up signal that POS-bigram KL doesn't), sentence-rhythm features (clause-balance ratios, parallelism density, antithesis frequency — the AI-shaping fingerprints the framework's named patterns don't yet catch).
- **Which diagnostic signals are safe restoration targets?** Directly prompting an LLM to optimize KL, Delta, entropy, or char n-gram distance invites metric gaming. The restoration surface needs a targetability taxonomy: direct craft targets, translated syntax targets, investigate-first diagnostics, and avoid-direct metrics. POS bigram/trigram drift is the central test case because it is diagnostic in raw form but only revision-useful after translation into prose moves.

## Voice fingerprint risk surface

The diagnostic outputs are voice-cloning inputs. The signals the variance and repetition audits compute (function-word distribution, sentence-length distribution, POS-bigram frequencies, idiolectic phrases) are exactly the inputs a stylometric voice-cloning system would consume. Three paths a hostile actor could take with a leaked voice profile: prompt-conditioning an LLM with the stylometric constraints; fine-tuning an LLM on the corpus directly; using the profile as a reward signal during generation. The framework's tools do not enable any of these directly, but a documented fingerprint makes any of them easier.

The framework's privacy posture is therefore protective by default. Personal baselines and voice profiles live in a separate private directory rather than this repo. `voice_profile.py` refuses publishable output paths unless explicitly overridden. The manifest validator enforces a privacy ratchet on voiceprint-tagged sources. The skill itself is publishable; the corpus and the voice profile that derives from it are not. Maintainers and contributors should respect this boundary.

## Design notes worth keeping

**The framework targets discourse habits, not vocabulary.** Surface tells (specific AI words, em-dash frequency) decay as models change. The named patterns are syntactic; they survive vocabulary shifts.

**Three layers stay distinct.** Layer A is mathematical, Layer B is craft-pattern recognition, Layer C is voice attribution. The framework's value depends on not collapsing them.

**Source triage is the hardest part to teach and the most valuable.** Most surface flags resolve as earned on triage. The framework's authority comes from being honest about that.

**Genre tolerance varies meaningfully.** A pattern that signals trouble in literary fiction may be partially structural to testimony or blog. The genre tolerance table consolidates the calibration notes.

**The personal baseline is the operative diagnostic.** Heuristic thresholds catch unsubtle cases. Always run with a register-matched personal baseline if available.

**The em-dash question is style preference, not AI signal.** A separate lens handles the specific surface tells. This framework catches the patterns underneath.

**POS-bigram KL detects the post-AI cohort, not AI-involvement amount.** On corpora tested through 2026-05, KL reliably separates pre-AI prose from post-AI prose against a register-matched pre-AI baseline, but does not reliably distinguish "lightly AI-involved" from "heavily AI-composed" within the post-AI cohort. The framework's claim language should match: a post-AI cohort indicator with a calibrated TPR/FPR statement at a stated operating point, not a dosage gauge. The validation harness output is the right venue for that statement; folk thresholds in script docs are the wrong one.

**The Layer A band is necessary but not sufficient on edited collaboration outputs.** The 2026 multi-model collaborative regime (notes → AI draft → human comment → AI revision) reintroduces surface variance that the eleven variance heuristics measure. Layer A passes; Layer B and source triage do the work that catches the LLM's underlying preferences for antithesis density, paragraph-closure consistency, and structural symmetry. The framework's marketing language has at times implied Layer A alone is the detector; the architecture has always disclaimed that, and the doc language should match.

**Do not target raw metrics in revision prompts.** Metrics diagnose drift; they are not prose goals. A good restoration prompt names the local prose pattern and the allowed move, not the number. "Reduce `DET+ADJ+NOUN` packages by replacing generic descriptor labels with concrete actors or verbs" is a usable instruction. "Lower POS-bigram KL" is not.

## Tooling & portability follow-ups

**~~`tools/` gates crash printing a status glyph on a non-UTF-8 (Windows) console.~~ Resolved.** The doc/capability gates `print`ed glyphs (`✔`, `≥`, `→`, `⇒`) that raise `UnicodeEncodeError` under a cp1252 default console *after* the check ran (a pass → nonzero exit). CI is Linux/UTF-8 so it never fired there; it only bit a maintainer running the gate locally on Windows. *Surfaced 2026-06-19 propagating the AGENTS.md workflow refinement; fixed the same day.* The audit found **four** affected tools (the literal-`✔` predicate had missed two — `gen_calibration_readiness.py`'s `≥`/`--help` docstring and `seed_capabilities.py`'s `→`-to-stderr): a shared `tools/_console.enable_utf8_stdio()` now reconfigures stdout/stderr to UTF-8 at the top of each tool's `main()` (preserves the glyphs → local↔CI parity; guarded), pinned by `test_tools_console_utf8.py`. Same class as the apodictic gate-parity fix (apodictic #115/#118).
