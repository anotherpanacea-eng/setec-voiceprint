# Spec 31: `tocsin-token-cohesiveness` — TOCSIN token-cohesiveness detection signal

**Capability id:** `tocsin_audit` (tool/script: `tocsin_audit.py`)
**Task surface (NEW):** `token_cohesiveness`
**Family:** `discrimination-perturbation`
**arXiv root:** Wang, Cheng, et al., *"Zero-Shot Detection of LLM-Generated Text using Token Cohesiveness"* (TOCSIN), **arXiv:2409.16914**. Cited here, in the PR body, and in the `changelog.d/` fragment per the fleet rule.

> **Review folded (`tocsin-token-cohesiveness-findings.md`, verdict GO-WITH-CHANGES).** The findings are folded into this in-repo copy:
> - **[P1]** `compute.tier: stylometry` is NOT a valid tier — the live vocabulary is `{acquisition, api_llm, core, ocr, optional, spacy, surprisal}`. The M1 stdlib proxy path is **`tier: core`** (matching `rank_turbulence_audit`, the stdlib-discrimination sibling); the M2 embedding path raises the tier to `surprisal` (noted in the fragment `cost_note`, the path is not built here).
> - **[P2]** Manifest `status: literature_anchored` (signal direction anchored in arXiv:2409.16914) and `band.calibration_status: heuristic` (threshold un-anchored) are different objects, asserted separately (AC-8). The capabilities.d fragment carries `dependencies.python: []` for M1 (zero-Python-dep / CI-runnable); `transformers`/`torch`/`numpy` go under `python_optional` (the M2 tier-raise).
> - **[P3]** The optional `--from-surprisal-envelope` path is **CUT from M1 entirely** (deferred to M2+). `surprisal_audit`'s JSON envelope exposes only `summary` aggregates + `top_k_tokens` + a `sliding_window` trajectory — it does NOT emit a raw per-token surprisal series, so the spec's "autocorrelation/local-smoothness over the per-token series" cannot be recomputed from the envelope. The default M1 path is the self-contained deletion + lexical-overlap proxy, so this is a pure scope cut, not a blocker.
> - **[P3]** `length_floor_words` is **pinned to 200** (matching `rank_turbulence_audit`), and §9's "below the length floor the surface warns" uses the same number.
> - **Open question (metric):** M1 commits to **token-set Jaccard** as the one stdlib semantic-difference metric (`semantic_diff = 1 - |A∩B| / |A∪B|` over the word-token sets), recorded in `semantic_diff_backend.metric`.
> - **Spec slot:** the spec's "28 is open" note is stale — `specs/` already carries multiple `28-*.md` and `30-*.md` files. This lands at the next clean integer, **`specs/31-tocsin-token-cohesiveness.md`**.

---

## 1. Framing (one paragraph)

TOCSIN is a **black-box, detector-flavored discrimination signal**: it measures the *token cohesiveness* of a text — how stable the text's meaning is under repeated random token deletion. The arXiv:2409.16914 finding is that **LLM-generated text exhibits higher token cohesiveness than human text** (its semantics degrade less when tokens are randomly dropped), making cohesiveness an axis the paper reports as **orthogonal to surprisal/curvature** (it is a plug-and-play *second channel* over a base zero-shot detector, not a re-derivation of perplexity). The true paper mechanism is: (1) draw several random-token-deletion perturbations of the target, (2) measure the **semantic difference** between each perturbation and the original, (3) summarize the distribution of those differences into a cohesiveness statistic. The semantic-difference step is the load-bearing model dependency, so this capability splits cleanly: **M1 ships the perturbation engine, the cohesiveness statistic over an *injectable* semantic-difference callable, the full values+band+`calibration_status` envelope, and a stdlib semantic-difference proxy** (token-set Jaccard distance — no model) so the surface is CI-runnable and produces real numbers today; **M2 swaps in the real embedding/encoder semantic-difference backend** behind the same injected seam. In SETEC's posture this is **descriptive only**: it emits VALUES + a PROVISIONAL band + `calibration_status`, **never** an `is_ai`/`is_human` label or a thresholded verdict. It maps alongside `surprisal_audit` (`smoothing_diagnosis`), `fast_detect_curvature` (`discrimination_curvature`), and `intrinsic_dimension_audit` (`intrinsic_dimension`) as a member of the detection-signal/evidence-pack family — orthogonal axis, keep-the-human, anti-Goodhart.

---

## 2. Unit of analysis

- **Input:** a single `--target` prose text file (UTF-8). One score per text (per-document surface, like surprisal/curvature/intrinsic-dimension — NOT a per-pool/set-level surface).
- **Perturbation unit:** a **token**, where "token" at M1 is the stdlib word token (`stylometry_core.word_tokens`, regex-based, lowercased, model-free). M2 may optionally re-anchor the deletion unit to the LM's sub-word tokenizer when the real backend is wired (recorded as a `deletion_unit` field so the two regimes are never silently compared). The paper's "random token deletion" is over the model's tokens; M1's word-token approximation is honestly labeled as an approximation in the band/claim-license.
- **Perturbation:** delete a fixed fraction `p` (default 0.10) of token positions, drawn at random under a fixed seed, repeated `n_perturbations` times (default 30). Determinism is mandatory (golden tests + reproducibility) → all randomness flows from a single seeded `random.Random(seed)`.
- **Semantic difference:** a callable `semantic_diff(original_tokens: list[str], perturbed_tokens: list[str]) -> float in [0, 1]`, where 0 = identical meaning, 1 = maximally different. **Injectable** (mirrors `intrinsic_dimension_audit`'s injectable `embed` and `surprisal_audit`'s injectable `score_fn`): M1 default = token-set Jaccard distance (`1 - jaccard(set(original), set(perturbed))`, deterministic, no model); M2 = `1 - cosine(embed(original), embed(perturbed))` from `embedding_backend.py`.
- **Cohesiveness statistic:** `token_cohesiveness = 1 - mean(semantic_diff over the n_perturbations)`. Higher cohesiveness ⇒ meaning survives deletion ⇒ the paper's "more LLM-like" direction. Reported with its dispersion (SD across perturbations) so the operator sees stability of the estimate, not just a point value.

---

## 3. EXACT result data shape (and the proof it carries NO verdict)

`audit_tocsin(...)` returns the script-specific `results` payload passed to `build_output(...)`. Shape:

```jsonc
{
  "token_cohesiveness": 0.842,          // VALUE: 1 - mean(semantic_diff). The axis. [0,1].
  "cohesiveness_sd": 0.031,             // VALUE: SD of (1 - semantic_diff) across perturbations. >= 0.
  "mean_semantic_diff": 0.158,          // VALUE: raw mean semantic difference under deletion. [0,1].
  "n_perturbations": 30,                // run metadata
  "deletion_fraction": 0.10,            // run metadata
  "deletion_unit": "word_token",        // "word_token" (M1) | "lm_subword" (M2) — never silently mixed
  "seed": 1729,                         // determinism anchor
  "target_tokens": 740,                 // run metadata
  "semantic_diff_backend": {            // PROVENANCE of the load-bearing model dep
    "kind": "lexical_overlap_stdlib",   // M1 | "embedding:<model-id>@<revision>" at M2
    "id": null,                         // model id at M2; null for the stdlib proxy
    "metric": "1 - jaccard(token_sets)" // exact distance used
  },
  "band": {                             // CALIBRATED BAND — descriptive, not a verdict
    "band": "indeterminate",            // one of: "indeterminate" | "low_cohesiveness" | "high_cohesiveness"
    "flags": ["cohesiveness_high"],     // descriptive flags, never "ai"/"human"
    "calibration_status": "heuristic",  // SETEC ladder value (see §3.2). NOT a bool.
    "calibration_anchor": "user-baseline-required",
    "thresholds_used": { "token_cohesiveness": { "high_above": 0.85, "low_below": 0.65 } },
    "orientation": "high cohesiveness = meaning survives deletion (paper's 'more LLM-like' DIRECTION); NOT 'is AI'"
  },
  "assumptions": {
    "method": "TOCSIN random-token-deletion cohesiveness (arXiv:2409.16914)",
    "orientation": "higher token_cohesiveness = more stable under deletion; orthogonal axis to surprisal/curvature, NOT a verdict",
    "m1_proxy": "M1 semantic difference is a stdlib token-set Jaccard distance, a PROXY for the paper's embedding semantic difference; the value is not comparable to an embedding-backed run (deletion_unit + semantic_diff_backend record which regime produced it)",
    "corpus_dependence": "cohesiveness is register- and length-dependent; thresholds are PROVISIONAL / operator-side"
  }
}
```

### 3.1 No-verdict proof (the #1 thing the reviewer hunts)

1. **No label key exists.** There is no `is_ai`, `is_human`, `label`, `prediction`, `classification`, `verdict`, or `decision` key anywhere in `results` or `band`. The only categorical leaf is `band.band ∈ {indeterminate, low_cohesiveness, high_cohesiveness}` — a **descriptive band over the value's own axis**, never over authorship. This mirrors `surprisal_audit`'s `band ∈ {indeterminate, smoothed, typical}` and `intrinsic_dimension`'s no-band path.
2. **No selection scalar.** The surface emits no probability-of-AI, no score-against-a-threshold-that-fires-a-decision, no ranking that picks one text as "the AI one." `token_cohesiveness` is a measurement on `[0,1]`; the band is illustrative and carries `calibration_anchor: user-baseline-required`.
3. **Recursive-walk guard (R4 / `validate_results_bounds`).** `build_output(...)` recursively walks `results`. The payload is all finite numerics (cohesiveness/SD/diffs in `[0,1]`, integer counts) plus strings — it passes the unconditional NaN/inf finiteness check, and `token_cohesiveness`/`mean_semantic_diff`/`cohesiveness_sd` carry no `surprisal`/`perplexity`/`entropy`/`probability` token in their key names (verified against `output_schema._SURPRISAL_RE` / `_PROBABILITY_RE`), so they trip **no** range gate falsely (they are bounded `[0,1]` by construction at the computing surface, not by a name-based assertion). An acceptance test additionally asserts **no verdict/label key string** appears anywhere in the recursively-walked envelope (see §6, AC-3).
4. **Claim-license refusal.** The `does_not_license` block explicitly refuses any AI/human determination, any thresholded authorship verdict, and names the orthogonality + corpus-dependence caveats (mirrors `intrinsic_dimension_audit` / `fast_detect_curvature` / `originality_audit`).

### 3.2 `calibration_status` (the SETEC ladder, not a bool)

`band.calibration_status` is one of the canonical ladder values (`THRESHOLD_STATUS_VALUES` in `variance_audit.py`, retiered v1.66.0 per `internal/SPEC_calibration_status_retier.md`): `calibrated | literature_anchored | empirically_oriented | heuristic | structural_only`.

- **M1 ships `heuristic`** — the band thresholds are fixture-derived first-reading numbers, provenance `None`, `calibration_anchor: user-baseline-required`. This is the honest tier for an un-anchored new signal (same as the spec's default-for-new-signals rule).
- The capability **manifest `status` (`capabilities.d` fragment) is `literature_anchored`** — the *signal direction* (LLM text ⇒ higher cohesiveness) is anchored in arXiv:2409.16914, exactly as `intrinsic_dimension_audit` carries manifest `status: literature_anchored` while its band is uncalibrated. The two are different objects: manifest `status` = the signal's literature footing; `band.calibration_status` = the *threshold's* anchoring tier. Do not conflate them; the reviewer will check both (AC-8).
- Promotion to `calibrated` happens only via `scripts/calibration/` against a labeled corpus (out of M1 scope; recorded as the M2+ path).

---

## 4. M1 scope (model-free stdlib, CI-runnable) vs M2 (model/GPU seam)

### M1 — stdlib, CI-runnable (this build)

In scope:
- `tocsin_audit.py` with `audit_tocsin(text, *, semantic_diff=None, n_perturbations=30, deletion_fraction=0.10, seed=1729)`.
- Seeded random-token-deletion perturbation engine over `stylometry_core.word_tokens` (stdlib).
- Cohesiveness statistic + dispersion + the full §3 `results` payload.
- **Default stdlib `semantic_diff`**: token-set Jaccard distance (`1 - |A∩B| / |A∪B|` over the lowercased word-token sets), recorded in `semantic_diff_backend.metric` as `"1 - jaccard(token_sets)"`. Deterministic, pure-Python, no `numpy` required for the proxy path.
- PROVISIONAL band (`heuristic`), claim-license, markdown renderer, JSON envelope via `build_output`.
- CLI: `python3 scripts/tocsin_audit.py --target TARGET [--n-perturbations N] [--deletion-fraction F] [--seed S] [--json] [--out PATH]`.
- Error paths via `build_error_output`: empty/too-short target (`text_too_short`), unreadable/non-UTF-8 target (`bad_input`), `deletion_fraction` out of `(0,1)` (usage error, exit 2 / `bad_input`).

**[P3 — folded] The `--from-surprisal-envelope` path is CUT from M1.** The spec originally floated an optional "stdlib cohesiveness over an already-emitted surprisal series" reading. That path is not buildable as described: `surprisal_audit`'s `--json` envelope emits only `summary` aggregates (mean/sd/variance/autocorrelation{lag_k}/…) + `top_k_tokens` + a `sliding_window` trajectory — it does NOT expose a raw per-token surprisal series (`series_list` stays internal). There is nothing to recompute local-smoothness over from the envelope, so the path is deferred to M2+ (once `surprisal_audit` optionally emits a raw series). It was always flag-gated and never the default, so cutting it changes nothing in the default M1 build.

Out of M1 scope: any model load, any GPU, any embedding/encoder call. No model is imported at module top-level or touched in tests.

### M2 — lazy-import + skipif model/GPU seam (follow-on)

- Real `semantic_diff` = `1 - cosine(embed(original), embed(perturbed))` using `embedding_backend.py` (`EmbeddingBackend.encode` / `resolve_model_arg` / `identifier_block`, all verified present; Apache/MIT model, alias convention). Constructed in `main()` only, lazy-imported, passed as the `semantic_diff=` callable — **never** loaded at import or in unit tests (the injectable-callable pattern from `intrinsic_dimension_audit`).
- Optional M2b: re-anchor `deletion_unit` to the LM sub-word tokenizer via `surprisal_backend.score_text_with_distributions` (verified present on `SurprisalBackend`, reused — never re-implemented) for paper-faithful token deletion. Records `deletion_unit: "lm_subword"`.
- M2 tests are `@pytest.mark.skipif`-gated on model/dep availability (the framework's standard pattern), so CI without the heavy deps still exercises M1 via the injected stub.
- The envelope shape is **unchanged** between M1 and M2 — only `semantic_diff_backend.kind/id`, `deletion_unit`, and the numeric values differ. Promotion of `band.calibration_status` past `heuristic` is an M2+/calibration concern, not a schema change.

---

## 5. Maps-to / orthogonality

- **Surface:** NEW task surface `token_cohesiveness`. It does NOT extend `smoothing_diagnosis` (surprisal) or `discrimination_curvature` — the paper's whole claim is that cohesiveness is an **orthogonal second channel**; folding it into an existing surface would erase that and muddy the claim-license. (Contrast: spec-24 dependency-distance correctly attached to the existing `voice_coherence` surface because MDD *is* a voice-coherence feature; cohesiveness is a *discrimination* axis, not a voice-coherence one, so it earns its own surface — see §7-D1.)
- **Family:** `discrimination-perturbation`, sibling to `discrimination-topology` (`intrinsic_dimension`) and the surprisal/curvature signals. Belongs in the multi-signal evidence pack.

---

## 6. Acceptance criteria (numbered)

**Functional**
- **AC-1.** `audit_tocsin(text, semantic_diff=<stub>)` returns the exact §3 `results` shape with `token_cohesiveness ∈ [0,1]`, `cohesiveness_sd ≥ 0`, integer counts, and `deletion_unit == "word_token"` on the M1 path. Deterministic under a fixed seed (same input ⇒ byte-identical `results` across runs).
- **AC-2.** CLI emits a `schema_version: 1.0` `build_output` envelope on the happy path and a `build_error_output` envelope (`text_too_short` / `bad_input`) on the error paths, exit code per the `originality_audit` convention (0 available, 3 unavailable, 2 usage).

**Posture guards (load-bearing)**
- **AC-3 (no-verdict recursive walk).** A test recursively walks the FULL envelope (`results`, `band`, `claim_license`, every nested dict/list) and asserts that **no key and no string value** matches `{is_ai, is_human, ai_generated, human_written, label, prediction, classification, verdict, decision, p_ai, prob_ai}` (case-insensitive). The only categorical leaf permitted is `band.band ∈ {indeterminate, low_cohesiveness, high_cohesiveness}`.
- **AC-4 (never-selects).** Given two targets, the surface produces two independent measurements; it exposes NO API that returns "which one is AI" / a selection index / an argmax-over-texts. (Asserted by the absence of any multi-target/selection entry point — the CLI takes exactly one `--target`.)
- **AC-5 (anti-Goodhart held-out disjoint).** The band thresholds and any fixture used to set them are **disjoint** from any corpus SETEC uses for held-out audit/validation (no threshold is tuned on the validation manifest). The band ships `heuristic` + `user-baseline-required` precisely so it is never read as a calibrated decision boundary. A test asserts `band.calibration_status == "heuristic"` and `band.calibration_anchor == "user-baseline-required"` at M1 (the signal cannot silently graduate to a load-bearing threshold without going through `scripts/calibration/`).
- **AC-6 (stdlib-import).** A test imports `tocsin_audit` and runs the default M1 path with **no** `transformers`/`torch`/embedding model available (and asserts none is imported at module load) — proves M1 is model-free and CI-runnable. The M2 embedding path is exercised only under a `skipif` gate.

**Bounds / envelope**
- **AC-7.** `build_output(..., validate_bounds=True)` accepts the payload (all finite, in-range) and the R4 walk raises nothing; a deliberately-injected NaN in a stubbed `semantic_diff` causes `OutputValidityError` (proves the recursive-walk guard is live on this surface).
- **AC-8 (calibration honesty).** Manifest `status == "literature_anchored"` (signal direction anchored in arXiv:2409.16914) AND `band.calibration_status == "heuristic"` (threshold un-anchored) — the two-object distinction from §3.2 is asserted so a reviewer-flagged conflation can't regress.

---

## 7. Load-bearing design calls (RESOLVED)

- **D1 — TOCSIN detector-posture is the spine (resolved): values + band + `calibration_status`, never a label.** The surface emits `token_cohesiveness` (+ SD + raw diff) as VALUES, a descriptive `band` over the value's own axis carrying `calibration_status: heuristic` + `calibration_anchor: user-baseline-required`, and a claim-license that refuses any AI/human or thresholded verdict. No `is_ai`/`is_human`/threshold-decision key exists; the only categorical leaf is the descriptive band. This is the entire reason the capability is shaped as a *signal*, not a detector. Precedent: `surprisal_audit` (provisional band), `intrinsic_dimension_audit` (manifest `literature_anchored` + uncalibrated band + injectable model), `fast_detect_curvature` ("value, NOT a verdict and NOT a shipped threshold; the operator supplies any band").
- **D2 — New surface vs. extend (resolved): NEW surface `token_cohesiveness`.** Cohesiveness is the paper's explicitly-orthogonal second channel; it is a *discrimination* axis, not a smoothing or voice-coherence feature, so it must not fold into `smoothing_diagnosis`/`discrimination_curvature`/`voice_coherence`. The new surface registers via a single `claim_license_surfaces/token_cohesiveness.txt` fragment; `VALID_TASK_SURFACES` derives from it automatically (no shared-dict edit).
- **D3 — M1/M2 line (resolved, cleanly drawn): M1 = perturbation engine + cohesiveness stat + envelope + stdlib token-set-Jaccard `semantic_diff` proxy; M2 = real embedding/encoder `semantic_diff` behind the SAME injected callable.** The paper's semantic-difference step is the only model dependency, so it is the seam. The schema, CLI, band, and claim-license are identical across M1/M2; only `semantic_diff_backend`, `deletion_unit`, and the numeric values change. The injectable-callable pattern (from `intrinsic_dimension_audit.embed` / `surprisal_audit.score_fn`) keeps every model out of import and tests. The optional `--from-surprisal-envelope` path is CUT from M1 ([P3] above) and deferred to M2+.
- **D4 — Determinism (resolved): single seeded `random.Random(seed)`, default 1729 (matches `intrinsic_dimension_audit`'s seed convention).** All deletion randomness flows from it; `seed` is recorded in `results` so a run is reproducible and golden-testable.

---

## 8. Capability registration plan (drop-in, post-#170 / post-#239)

No shared-dict edits; every registration is a fragment.

1. **`plugins/setec-voiceprint/capabilities.d/tocsin_audit.yaml`** — `entries:` with `id: tocsin_audit`, `script_path:`, `surface: token_cohesiveness`, `status: literature_anchored`, `handoff: experimental`, `family: discrimination-perturbation`, `consumers: []`, `purpose` (cohesiveness signal + arXiv:2409.16914 + uncalibrated/no-verdict), `use_when` / `do_not_use_when` (do-not-use: "you want an AI/human verdict — this surface refuses one"; "values comparable across semantic-diff backends — M1 proxy ≠ M2 embedding"), **`compute.tier: core`** for the M1 proxy path (no model — `[P1]` fix, matching `rank_turbulence_audit`) with a `cost_note` recording that the M2 embedding path raises the tier to `surprisal`, **`length_floor_words: 200`** (`[P3]` fix), **`dependencies.python: []`** for M1 with `transformers`/`torch`/`numpy` under `python_optional` (`[P2]` fix — the M2 tier-raise), `references:` citing arXiv:2409.16914.
2. **`plugins/setec-voiceprint/scripts/claim_license_surfaces/token_cohesiveness.txt`** — the NEW surface label (the golden source of truth; `TASK_SURFACE_LABELS` and thus `VALID_TASK_SURFACES` derive from it). One line, e.g.: `Token-cohesiveness stability under random token deletion (TOCSIN, Wang et al. 2024 — discrimination evidence, uncalibrated, non-verdict)`.
3. **`plugins/setec-voiceprint/scripts/tests/_golden_capabilities/tocsin_audit.json`** — the per-id golden fragment (drop-in, NO `==N` count literal anywhere), structurally mirroring `intrinsic_dimension_audit.json` (status `literature_anchored`, family present, `dependencies.python: []` for M1). The capability build fans out in parallel and merges without a count bump (per the #239 drop-in golden retirement). Generated from `capabilities.load_manifest()` so the golden never disagrees with the fragment.
4. **`changelog.d/feat-31-tocsin-token-cohesiveness.md`** — behavior-shipping fragment (never edit `CHANGELOG.md`); cites arXiv:2409.16914 (title + id/URL) per the fleet rule and references the capability `id` `tocsin_audit` (docs-freshness coverage). Version/date cut at release via `tools/assemble_changelog.py`, not in the PR.
5. No `claim_license.py` / `output_schema.py` / `capabilities.py` shared-file edit is needed — the surface, label, and validity all derive from the fragments.

Docs-freshness (`tools/check_docs_freshness.py`) is satisfied by the `changelog.d/` fragment + the capability fragment; CI runs it.

---

## 9. Assumptions / limits

- **M1 proxy ≠ paper.** M1's token-set Jaccard semantic difference is a deliberate stdlib stand-in for the paper's embedding semantic difference; the M1 `token_cohesiveness` is internally consistent and deterministic but is **not numerically comparable** to an M2 embedding-backed run. `semantic_diff_backend.kind` + `deletion_unit` make the regime explicit so the two are never silently compared. Operators wanting paper-faithful values run M2.
- **Word-token vs sub-word deletion.** M1 deletes word tokens; the paper deletes model sub-word tokens. The direction of the signal is preserved (deletion stresses meaning either way) but magnitudes differ — recorded in `deletion_unit`.
- **Register / length dependence.** Cohesiveness is register- and length-sensitive (short or highly formulaic text inflates apparent stability); the band is PROVISIONAL/`heuristic` and `user-baseline-required` for this reason. **Below the `length_floor_words: 200` floor the surface warns rather than over-claims** (same number as the fragment floor).
- **Single signal.** Per the claim-license and the SETEC posture, cohesiveness is one axis among many; AI-edited prose, institutional voice, and formulaic genres can all read high. It licenses no authorship verdict on its own and is intended for the multi-signal evidence pack, with the human in the loop.
- **No held-out leakage (anti-Goodhart).** Band thresholds are disjoint from any validation/audit corpus; promotion past `heuristic` goes only through `scripts/calibration/` against a labeled corpus, never by tuning on the held-out set.

---

## 10. Build gating

This capability is a **detection/discrimination signal that ships uncalibrated, descriptive-only, with a hard no-verdict posture**. The posture guards are baked into the acceptance criteria (AC-3..AC-6, AC-8). It does **not** require a separate posture/fairness/operator approval gate *before it can land*: it follows the exact precedent of `intrinsic_dimension_audit` and `fast_detect_curvature` (both landed as uncalibrated, no-verdict signals through the normal Codex code-PR review gate). The standing gates apply: **Codex 5.5 review on the code PR** (this is a code PR, not docs-only) and the **merge-commit-not-squash** mechanic. Calibration to a labeled corpus (promotion past `heuristic`) is a separate, later, operator-run step and is explicitly out of this build's scope.
