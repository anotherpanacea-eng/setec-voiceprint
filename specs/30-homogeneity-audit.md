# homogeneity-audit (pool-level set homogeneity / "AI hivemind")

> A **pool-level** detection axis: every existing voiceprint surface scores ONE document; this
> scores a **set of N responses**. Reports the **distribution of pairwise cosine similarities**
> across the pool + an **effective number of modes**, plus an optional **single-doc
> hivemind-proximity** signal (cosine of one target to an operator-supplied AI-typical centroid).
> Descriptive distribution, **no absolute band** for M1. NO `is_ai` / `is_human` / verdict; the unit
> of analysis is the **distribution**, not any one text.

> **This file folds the four review findings** (`spec-wave-2/homogeneity-audit-findings.md`) into the
> build contract. The deltas from the original draft, summarized:
>
> - **[P2] Privacy gate is a bare `sys.exit(2)` + stderr, not a `policy_refused` envelope, and lives in
>   `general_imposters` — not `voice_fingerprint` (which has no gate).** `acquisition_core.check_output_privacy`
>   (`acquisition_core.py:202`) writes to stderr and calls `sys.exit(2)` directly (`:228`);
>   `general_imposters.py:928` is the canonical caller (build `paths_to_check`, then
>   `ac.check_output_privacy(paths, allow_public=…, tool=TOOL_NAME)`). M1 ships **no LUAR lens and no
>   privacy gate** (M1's stdlib vectors are not voiceprint-shaped); Acceptance 8 is an **M2-only** item
>   and is written to the **real contract** (exit code 2 + stderr) — the M2 LUAR path may instead wrap
>   the `SystemExit` into `build_error_output(reason_category="policy_refused")`, but that is an M2
>   decision, not asserted here for M1. The privacy-gate inheritance citation is `general_imposters`,
>   never `voice_fingerprint`.
> - **[P2] No absolute band for M1.** Exactly like the sibling `originality_audit` (which emits NO
>   band and pushes all thresholding to the operator), M1 ships only the **raw** `mean_pairwise_cosine`
>   + `effective_modes` + the confounds in `assumptions` / claim license. The paper's ~0.8 line is the
>   *upstream semantic-lens* figure and is **NOT inherited** by the stylometric lens — it is named in
>   `assumptions.reference_threshold_source` as not-calibrated-for-this-lens and is **not** a code cut.
>   No three-bucket `provisional_band` for M1; a band (if any) is an M2/POC item once the lens is
>   calibrated.
> - **[P3] No phantom golden file.** The canonical claim-license artifact is the drop-in
>   `claim_license_surfaces/set_level_diversity.txt` fragment (already present, already contains
>   "within-set homogeneity"); `test_claim_license_surfaces.py` derives the surface set from fragments
>   with **no `==N` count literal** (the aggregate `_golden_task_surface_labels.json` was deleted in the
>   #170 drop-in refactor). Registering a new script id on the existing surface needs **zero**
>   claim_license edits.
> - **[P3] numpy is honestly declared.** `effective_modes` (a headline key + a hard numeric pin) needs
>   numpy for the Gram eigenvalues. The capabilities fragment lists numpy under `python_optional` with a
>   `cost_note` ("`effective_modes` requires numpy; the pairwise-cosine distribution ships stdlib-only");
>   the build guards the numpy import and degrades to `effective_modes: null` (distribution still
>   emitted) on a bare-stdlib box, never crashes.

- **Status:** Build — **M1 (stdlib / model-free) SHIPPED**; M2 (LUAR + `text-embedding-3-small`
  semantic lenses) POC-gated behind `--lens` flags, deferred.
- **Tier:** near-term (M1 stdlib local-stylometric lens) · research-grade (M2 gated semantic/LUAR lens).
- **GPU required:** **no** for M1 (stdlib char/function-word vectors + cosine — CI-runnable, no model,
  no API).
- **Upstream / prior art:**
  - **Artificial Hivemind** — Jiang, Choi, Sap et al., *NeurIPS 2025 D&B (Oral)*
    ([arXiv:2510.22954](https://arxiv.org/abs/2510.22954)): set-level LM homogeneity, the
    average-pairwise-cosine metric over a response pool, mode collapse, the Infinity-Chat dataset. The
    reported AI regime: ~79% of prompts have average pairwise cosine > 0.8 under
    `text-embedding-3-small` — the **semantic** lens, **not** transferred to M1's stylometric lens.
  - **QUDsim** — discourse-structure reuse across LLM text ([arXiv:2504.09373](https://arxiv.org/abs/2504.09373)).
    A later discourse-unit refinement (M3), not M1/M2.
- **License decision:** **clean-room the metric.** Average pairwise cosine and the effective-modes
  estimator are plain arithmetic reimplemented from the paper (no weights). M1's local lens is
  **stdlib stylometric vectors computed in-tree** (function-word + char-n-gram frequency vectors, the
  `voice_distance` / `idiolect_detector` feature family, via `stylometry_core.function_word_features` /
  `char_ngram_features` / `word_tokens` — all regex/`Counter`, spaCy-free). M2's local LUAR lens reuses
  `voice_fingerprint`'s encoder (`voice_fingerprint._load_encoder:263`); M2's semantic lens is a remote
  API (`text-embedding-3-small`), opt-in. **Privacy gate (M2 `--lens luar` only):** per-text LUAR
  embeddings are voiceprint-shaped, so M2's LUAR lens wires the **`general_imposters`** output-privacy
  gate (`acquisition_core.check_output_privacy`, `acquisition_core.py:202`; canonical caller
  `general_imposters.py:928`). **M1's stdlib vectors and M2's semantic lens are not LUAR-shaped and do
  not trip the gate.** (`voice_fingerprint` has no privacy gate at all — it is cited only for the LUAR
  *encoder* reuse, not the gate.)

## Motivation

Every current SETEC surface scores **one document** against a baseline. One question they structurally
cannot answer: **how tightly does a *set* of responses cluster?** LLM outputs collapse toward a shared
centroid (the Hivemind finding) — a property of the **distribution** over N texts, not of any one text.
A pool of 30 model answers to the same prompt can each look unremarkable per-document and still be
collectively near-identical, and no per-doc surface can see that, because the signal lives **between**
the texts.

**Orthogonality (the missing axis).** The unit of analysis is a *pool*, not a doc-vs-baseline:

- `voice_fingerprint` (`authorship_embedding`) computes *one target's* cosine to a *baseline centroid*
  — a target-vs-pool reading, never the **all-pairs** within-pool distribution + effective modes.
- `originality_audit` (this same surface, spec 22) measures *verbatim-span reconstructibility* of one
  target against a reference pool — a coverage question, not a clustering one.
- `variance_audit` measures *within-text* spread of one document; homogeneity measures *between-text*
  spread of a set.

This is the producer-side measurement surface for the `ROADMAP.md` "Hivemind / cross-document
homogenization" line; it never becomes a generation target (§Anti-Goodhart).

## The load-bearing design question (and its answer)

> **What embedding lens does the headline pool-homogeneity number come from?**

A *semantic* pairwise cosine measures whether the pool says the same **content** (which a tight topical
prompt forces regardless of who/what wrote it); a *stylometric* pairwise cosine measures whether the
pool is written in the same **style** (the mode-collapse signal SETEC actually cares about).

**Resolution — ship a model-free local-stylometric lens as the DEFAULT (M1), gate the
paper-comparable semantic and LUAR lenses behind a flag and a POC (M2).** The default must be the lens
that is *runnable, glass-box, and posture-aligned* — the stdlib stylometric one. The semantic lens is
offered for **paper comparison**, not as the on-by-default detector, precisely because high *semantic*
homogeneity is the most confoundable reading.

> **Second design call: is hivemind-proximity its own surface, or a mode?**

**Resolution — a *mode* of the same `homogeneity_audit` script.** Pool homogeneity (N texts) and
single-doc hivemind-proximity (1 text vs a centroid) are the same axis — closeness to the AI mode —
at two scales. `--target T --centroid C` switches to single-doc proximity mode and emits
`hivemind_proximity` in `results`. There is **no bundled AI-typical centroid** — a shipped default
centroid would smuggle an implied verdict — so `--centroid` is operator-supplied and, absent it, the
proximity mode is `bad_input`, not defaulted.

## Method

One new script, `homogeneity_audit.py`, on the **existing** `set_level_diversity` surface.

### M1 — model-free local-stylometric core (stdlib, CI-runnable)

Inputs: a **pool of N texts** via `--manifest M` (JSONL, `id` + `text`|`text_path`, the same shape
`originality_audit._load_reference_manifest` reads) **or** `--dir D` (a `.txt`/`.md` directory).

1. **Per-text vector — stdlib, no model.** Each pooled text → a fixed-length numeric vector built from
   `stylometry_core.function_word_features` (the sorted FUNCTION_WORDS frequency dict) concatenated with
   the top-K char-n-gram frequencies from `stylometry_core.char_ngram_features` over a **pool-fixed**
   vocabulary (the union of n-grams appearing in the pool, sorted, so every vector shares the same
   coordinate order). Deterministic, ordering-stable, no numpy required for the vector build (a plain
   list of floats), no spaCy, no transformers. (Open Question 3 — exact feature set — resolved: the
   function-word family + char-3/4/5-gram families, the `voice_distance` family, with a pool-fixed
   shared vocabulary so coordinates align across the pool.)
2. **Pairwise cosine distribution.** All unique `i<j` cosines across the N vectors. The distribution
   summary clean-rooms `voice_fingerprint.cosine_distribution`'s 7-key shape (`n`, `mean`, `sd`, `min`,
   `p10`, `p50`, `p90`) so the homogeneity block reads identically to the embedding surface — computed
   in **pure stdlib** (`statistics` + a linear-interpolation quantile), no numpy. The **headline
   scalar** is `mean_pairwise_cosine` (= the paper's "average pairwise cosine"), oriented
   `gt = more homogeneous`.
3. **Effective number of modes.** A clean-room participation-ratio estimator over the Gram matrix of the
   (mean-centered, unit-normed) pool vectors: `effective_modes = (Σλ_i)² / Σ(λ_i²)` over the Gram
   eigenvalues — `≈ 1` when the pool collapses to one direction, `≈ N` when maximally spread. This is
   the one place M1 needs numpy (N×N eigenvalues); the import is **guarded** and degrades to
   `effective_modes: null` (distribution still emitted) if numpy is somehow absent — never crashes.
4. **No absolute band (per finding P2 band).** Like `originality_audit`, M1 emits the raw
   `mean_pairwise_cosine` + `effective_modes` and pushes thresholding to the operator. The paper's ~0.8
   AI-regime line is recorded in `assumptions.reference_threshold_source` as
   `"arXiv:2510.22954, semantic lens — NOT calibrated for this stylometric lens"` and is **not** a code
   cut. No `provisional_band` key, no `high_homogeneity` label, no inherited 0.8.

`results` (M1 pool mode): `n_texts`, `lens: "local-stylometric"`, `pairwise_cosine_distribution` (the
7-key block), `mean_pairwise_cosine`, `effective_modes`, `assumptions` (prompt-tightness / shared-genre
/ single-source confound, set-floor note, the reference-threshold-source line). **No
`is_ai`/`is_human`/`verdict`/`label`/`score` key** (§Anti-Goodhart, structurally enforced by the
recursive results walk).

### M1 — single-doc hivemind-proximity mode (still model-free)

With `--target T --centroid C` (C = an operator-supplied centroid file, OR `--centroid-dir D2` whose
mean vector is the centroid), compute, under the **same local-stylometric lens**, the cosine of the
target's vector to the centroid → `hivemind_proximity` (oriented `gt = closer to the supplied centroid =
more hivemind-like`). No bundled centroid; absent `--centroid`/`--centroid-dir`, this mode is
`bad_input`, not defaulted. `results` (single-doc mode): `lens`, `hivemind_proximity`,
`centroid_provenance` (n texts / source the centroid was built from), `assumptions`. The vector builder
and the mean (clean-room arithmetic; `_centroid` at `voice_fingerprint.py:347` is the *shape* reference)
are M1's own; the target and the centroid texts are embedded over the **same pool-fixed vocabulary**
(the union over target + centroid texts).

### M2 — gated embedding-lens seam (`--lens luar` / `--lens semantic`), DEFERRED

The lens is a single injectable seam, `_embed_pool(texts, lens, vocab) -> list[vector]`. M2 adds
`luar` (lazy-imports `voice_fingerprint._load_encoder`; **privacy-gated** via the `general_imposters`
wiring — `acquisition_core.check_output_privacy`; not CI-runnable) and `semantic` (lazy-imports the
OpenAI client; remote API, opt-in; absent a key/`openai` → `available:false` `missing_dependency`,
**never a silent fallback to the local lens**). Both are **POC-gated** (do not promote past `heuristic`
until the Code-PC POC shows the lens reproduces the AI regime and separates a human pool from an AI
pool). M1 ships first and independently.

## Contract (the testable interface)

- **task_surface:** `set_level_diversity` — **already registered** (spec 22 dropped
  `scripts/claim_license_surfaces/set_level_diversity.txt`, which already reads "…within-set
  homogeneity"). This is **NOT a new surface**: no new `claim_license_surfaces/` fragment, no label
  edit. `test_claim_license_surfaces.py` derives the surface set from the fragments with **no `==N`
  count literal** (the aggregate `_golden_task_surface_labels.json` was deleted in the #170 drop-in
  refactor — it does not exist on disk). One new **script id** (`homogeneity_audit`) on that surface.
- **CLI:** `python3 plugins/setec-voiceprint/scripts/homogeneity_audit.py [--manifest M | --dir D]
  [--lens local-stylometric] [--target T --centroid C | --centroid-dir D2] [--min-set 10]
  [--json] [--out F]`. Default lens `local-stylometric`; default mode = pool; `--target`+a centroid
  switches to single-doc proximity. **No `--verdict` / `--is-ai` / `--label` flag exists.** (`--lens
  luar|semantic` and `--allow-public-output` land with M2.)
- **JSON envelope:** built via `output_schema.build_output()` on success and
  `output_schema.build_error_output()` on refusal/abstention; one `ClaimLicense` block via
  `claim_license.from_legacy`.
- **Claim license — licenses:** "the pairwise-cosine distribution + effective number of modes of the
  supplied **pool** under the named lens" (pool mode) / "the cosine of the target to the
  operator-supplied centroid under the named lens" (single-doc). **does_not_license:** any AI/human
  verdict; any reading of high homogeneity as "AI" (a tight topical prompt, a shared genre, or a single
  source forces homogeneity with no AI involvement); any reading of the single-doc proximity as an
  identity/authenticity claim; any band that is not operator-supplied; that the local-stylometric number
  is comparable to the paper's semantic-lens number.
- **capabilities.d fragment:** one new `capabilities.d/homogeneity_audit.yaml` — `id: homogeneity_audit`,
  `surface: set_level_diversity`, `status: heuristic`, `family: set-level-diversity`,
  `compute.tier: core`, `length_floor_words` per-text + a **set floor** (`min_set`, default 10),
  `dependencies.python: []`, `python_optional: [numpy]` with a `cost_note` (`effective_modes` needs
  numpy; the distribution ships stdlib-only). Mirrors `originality_audit.yaml`'s shape.
- **Per-id golden fragment (NO count bump):** one file
  `scripts/tests/_golden_capabilities/homogeneity_audit.json` (`json.dumps(entry, indent=2)` + trailing
  newline). The count is derived from the fragment set (`test_capabilities_dropin.py`,
  `assert len(m["entries"]) == len(golden)`) — no `==N` literal.
- **Dependencies / footprint:** M1 numpy (transitive, guarded, `python_optional`). M2 `luar` reuses the
  `voice_fingerprint` embedding tier; M2 `semantic` needs an API key + `openai`.
- **Paper trail:** the `capabilities.d/` fragment + the `_golden_capabilities/homogeneity_audit.json`
  fragment + a `changelog.d/<slug>.md` fragment (citing **arXiv:2510.22954**) + the dated `ROADMAP.md`
  status-line flip + `tools/gen_calibration_readiness.py` refresh. (No `signals-glossary.md` entry —
  the glossary indexes per-document stylometric *signals*; set-level surfaces are not in it, matching
  the `originality_audit` precedent.) Run `check_capabilities_drift.py` / `gen_calibration_readiness.py`
  / `check_docs_freshness.py` + the capabilities/test suite before push.

## Test contract (names + invariants the build must satisfy)

`plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py` — M1 is **fully CI-testable**.

1. **deterministic-output** — same pool + lens → byte-identical `results`.
2. **envelope-shape** — `build_output()` keys present; `results` carries the enumerated keys per mode
   (`pairwise_cosine_distribution` is the 7-key block; pool mode has `effective_modes`, single-doc has
   `hivemind_proximity`).
3. **claim-license-present** + **refuses-verdict** — the `ClaimLicense` block is present;
   `does_not_license` contains the AI/human refusal + the "high homogeneity ≠ AI" caveat + the
   "local-stylometric ≠ paper's semantic number" caveat.
4. **no-verdict / no-`is_ai` field guard** — none of `is_ai`, `is_human`, `verdict`, `label`,
   `same_author`, `score` appears anywhere in `results` (recursive walk).
5. **numeric pins (M1, model-free):**
   - a pool of **near-identical** texts → `mean_pairwise_cosine` near 1.0 and `effective_modes` ≈ 1.
   - a pool of **unrelated/diverse** texts → lower `mean_pairwise_cosine` and `effective_modes` > 1.
   - `effective_modes` is bounded `1 ≤ effective_modes ≤ n_texts`, and ≈ `n` for an orthonormal pool.
   - single-doc: `hivemind_proximity` monotone in target↔centroid cosine; target = centroid source →
     proximity ≈ 1.0.
6. **set-floor abstention** — a pool with fewer than `min_set` (10) texts → `available:false`
   `reason_category: "bad_input"`.
7. **graceful-degradation / bad input** — a malformed manifest / empty pool / empty centroid →
   `available:false` `bad_input` (no division by zero, no NaN); numpy-absent → `effective_modes: null`
   with a warning (distribution still emitted).
8. **privacy-gate (M2 `--lens luar` only — DEFERRED).** The LUAR lens wires
   `acquisition_core.check_output_privacy` (`acquisition_core.py:202`; canonical caller
   `general_imposters.py:928`), which writes a stderr refusal and `sys.exit(2)` — **exit code 2 + stderr,
   not a JSON envelope** (the real contract). The **M1 local-stylometric** lens does **not** trip the
   gate — a test pins that M1 to a public `--out` path is allowed (no refusal, no exit 2). (The full
   LUAR refusal test lands with M2.)
9. **lens-label honesty** — the `lens` field in `results` always names the lens that produced the
   vectors.
10. **reference-threshold honesty** — `assumptions.reference_threshold_source` names the upstream
    semantic-lens origin of the ~0.8 line and marks it not-calibrated-for-this-lens; M1 emits **no
    band** and no verdict.

## Anti-Goodhart guardrails (structural)

- **G1 — no verdict field in `results`** (Acceptance 4, recursive walk).
- **G2 — measurement, not provenance.** Raw distribution + effective-modes; the ~0.8 line is the
  upstream semantic figure, not transferred (Acceptance 3, 10).
- **G3 — never a selection/training target; held-out stays disjoint.** The descriptive number is not a
  generation/ranking objective; the producer surface never optimizes against its own number.
- **G4 — confound surfaced.** Every output carries the prompt-tightness / shared-genre / single-source
  confound in `assumptions` and the claim license (Acceptance 3).
- **G5 — lens honesty** (Acceptance 9); M2 fails loud on a missing semantic dep, never silent-falls-back.

## Acceptance criteria

The numbered §Test-contract list **is** the acceptance set. M1 is done when 1–7, 9, 10 green in CI
(stdlib, no model); 8's M1 half (M1 local lens does not trip the gate) greens torch-free; the LUAR
refusal half of 8 and the semantic `missing_dependency` path land with M2.

## Milestones

- **M1 (this build, stdlib, CI-green):** `homogeneity_audit.py` with `--lens local-stylometric`, pool
  mode + single-doc proximity mode, the participation-ratio effective-modes estimator, **no band**, the
  capabilities + golden fragments (no count bump), changelog/ROADMAP/readiness paper trail, tests 1–7,
  9, 10 + the M1 half of 8. Ships `status: heuristic`, uncalibrated, no verdict.
- **M2 (POC-gated, deferred):** the `_embed_pool` `luar` + `semantic` lenses behind the seam, the
  `general_imposters` privacy gate on the LUAR path, the semantic `missing_dependency` path, the LUAR
  refusal test, and the seam smoke. Promoted past `heuristic` only after the Code-PC POC.
- **M3 (later):** QUDsim discourse-unit homogeneity ([arXiv:2504.09373](https://arxiv.org/abs/2504.09373)).

## Calibration posture

Ships **PROVISIONAL / `heuristic` / uncalibrated** — no verdict, no band, raw measurements. The path
to `empirically_oriented` is the Code-PC POC + Infinity-Chat (arXiv:2510.22954) replication plus human
response pools, recorded as a PROVENANCE entry; the held-out evaluation pool stays disjoint from any
band-setting pool (G3).

## Open questions (resolved for M1)

1. **Effective-modes estimator** — participation ratio (Gram eigenvalues), hyperparameter-free, pins
   cleanly. POC confirms stability at the real set floor.
2. **Set floor** — `min_set = 10` placeholder; the POC reports the minimum N for a stable distribution.
3. **Local-stylometric feature set** — RESOLVED: `function_word_features` + char-3/4/5-gram families
   over a **pool-fixed shared vocabulary** (the `voice_distance` family).
4. **Default pool source** — no implied default; the operator supplies a prompt-matched response pool
   (mixing prompts inflates apparent diversity). Documented in `assumptions`.
