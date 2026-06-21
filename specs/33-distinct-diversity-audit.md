# 33-distinct-diversity-audit

> A **distinct-output diversity / mode-collapse harness over a SET of generations**: equivalence-cluster
> near-identical outputs (stdlib shingle/Jaccard near-dup), then report the **deduplicated-distinct-CLUSTER
> distribution + per-cluster representatives + a utility-weighted distinctness** — the *cluster-count* axis of
> set diversity, **ORTHOGONAL** to the shipped `homogeneity_audit` (average-pairwise-cosine) and
> `variance_audit` (within-text spread). M1 = pure-stdlib clustering, CI-runnable. **NEVER a single
> "diversity score"** — the distribution + representatives are the read. Posture: descriptive set-level
> shape, no-verdict.

- **Status:** **M1 BUILT** — backs the `distinct_diversity_audit` id on the existing `set_level_diversity`
  surface (`plugins/setec-voiceprint/scripts/distinct_diversity_audit.py`,
  `capabilities.d/distinct_diversity_audit.yaml`). Ships `status: heuristic` (uncalibrated). M2 model-dedup
  lens remains a POC-gated seam (not in this build).
- **arXiv root:** **NoveltyBench: Evaluating Language Models for Humanlike Diversity**
  ([arXiv:2504.05228](https://arxiv.org/abs/2504.05228)). The paper proposes scoring a *set* of generations by
  **partitioning them into equivalence classes** (semantically/near-duplicate-equivalent outputs collapse into
  one class) and reporting **(a) the number of distinct classes** and **(b) a utility-weighted distinctness**
  (`Distinct@k` / cumulative-utility), rather than averaging a pairwise similarity. M1 clean-rooms the
  *partitioning + distinct-count + utility-weighted-distinctness* arithmetic with a **stdlib lexical
  near-dup** equivalence relation (the paper's learned deduper is the M2 seam).
- **Tier:** near-term (M1, stdlib, CI-runnable). Model/embedding equivalence relation = a POC-gated M2 seam.
- **GPU required:** no (M1). M2 model-deduper lens = lazy-import + skipif, off by default.
- **Surface:** **existing** — `set_level_diversity` (already registered; already backs `homogeneity_audit` +
  `originality_audit`). This is a **THIRD id** on that surface, not a new surface.

## STEP 0 — verified NOT already built

`grep -ril "noveltybench|2504.05228|distinct.diversity|distinct_diversity|mode.collapse"` over
`setec-voiceprint` returns **no implementing surface** — the only hits are the orthogonal siblings
(`variance_audit`, `homogeneity_audit`, spec 30/22). The `set_level_diversity` surface exists, but its two
shipped ids answer **different questions**:

- `homogeneity_audit` → the **average-pairwise-cosine distribution + effective_modes** (a *continuous spread*
  read over a stylometric Gram matrix). It never **partitions** the set into discrete equivalence classes and
  never emits **per-class representatives**.
- `originality_audit` → **target-vs-pool reconstructibility** (longest-n-gram coverage of ONE target by a
  reference corpus). A doc-vs-pool question, not a within-set partition.

NoveltyBench's read is **discrete cluster structure**: *how many genuinely distinct things did the model say,
and what does each cluster look like* — a partition + representatives + utility-weighted count. That is a
**new id**, additive on the same surface. `already_built = false`.

## Unit of analysis

A **SET of generations to ONE prompt** (a response pool): `--manifest` (JSONL `id` + `text`|`text_path`) or
`--dir` of `.txt/.md`. Identical input contract to `homogeneity_audit` (same `_load_manifest` / `_load_dir`
conventions — reuse them verbatim). The signal lives **between** the texts (in the *partition* they induce),
not inside any one — no per-document surface can see it. NOT a doc-vs-baseline comparison.

## Method (M1 — stdlib, model-free, CI-runnable)

Clean-room reimplementation of NoveltyBench's set-partition read, with a **lexical near-dup** equivalence
relation in place of the paper's learned deduper.

### 1. Per-text shingle sets (NEW stdlib helper — no existing API does this)

`stylometry_core` provides `word_tokens(text) -> list[str]` (regex, lowercased) but has **no shingle /
Jaccard / n-gram-set helper** (grep-confirmed: only `function_word_features`, `char_ngram_features`,
`word_tokens`, `char_ngram_family_name`, `paragraphs` exist). So this surface defines two small local
stdlib functions in its own module (it does NOT touch `stylometry_core`):

- `word_shingles(text, k) -> frozenset[tuple[str, ...]]` — the set of length-`k` word k-grams over
  `word_tokens(text)` (default `k = --shingle-k = 5`). Empty/under-`k` texts → singleton-token fallback so a
  short text still has a comparable set.
- `jaccard(a, b) -> float` — `|a ∩ b| / |a ∪ b|`, returns `0.0` on two empty sets (degenerate-but-not-NaN;
  the R4 bounds gate would reject a NaN). Bounded `[0, 1]`.

### 2. Equivalence-cluster the set (single-link near-dup join, deterministic)

Two texts are **near-duplicate-equivalent** when `jaccard(shingles_i, shingles_j) >= --near-dup-threshold`
(default `0.5`). Take the transitive closure (single-link union-find over the all-pairs i<j Jaccard matrix) →
a **partition into equivalence clusters**. Deterministic: stable input order, fixed threshold, integer
union-find. This is the paper's "partition into equivalence classes" with a lexical relation; the **`--lens`
seam** (M2) swaps the relation for a model deduper without changing the partition arithmetic.

### 3. The read — distribution + representatives + utility-weighted distinctness (NO single score)

`results` carries (every key a measurement; **no verdict / band / selection scalar** — proven below):

- `n_texts` — usable pool size (after the per-text length floor).
- `n_clusters` — number of distinct equivalence clusters (the headline **count**, not a normalized score).
- `cluster_size_distribution` — the 7-key block (`n`/`mean`/`sd`/`min`/`p10`/`p50`/`p90` of cluster sizes),
  reusing the `cosine_distribution`-shaped summary pattern from `homogeneity_audit` (clean-room, stdlib).
- `cluster_sizes` — the explicit sorted list of cluster sizes (the full discrete shape, descending).
- `distinct_ratio` — `n_clusters / n_texts` ∈ (0, 1]. A *ratio of two reported counts*, oriented
  `gt = MORE distinct` — **NOT** a quality/diversity verdict (a tight topical prompt forces a low ratio with no
  model defect; see confounds). Emitted as a value with NO band.
- `utility_weighted_distinctness` — the paper's cumulative-utility read, **clean-roomed without a utility
  model**: each cluster contributes `1` for its first (representative) member and a **discounted** weight
  `--utility-discount ** rank` (default `0.0` → pure distinct-count; operator may set e.g. `0.5` to credit
  redundant restatements partially). Reported as a **value with the discount echoed in `assumptions`**, never
  a thresholded pass/fail. (Default `0.0` keeps M1 a pure partition count; the knob exists so the operator,
  not the surface, owns any utility weighting.)
- `representatives` — for each cluster, **one representative** = the **earliest member by input order**
  (deterministic; NOT a "best" / selected output — see never-selects guard), plus that cluster's `size`,
  `member_ids`, and a `repr_excerpt` (first ~30 tokens). This is the qualitative read: *what each distinct
  thing the model said looks like*.
- `assumptions` — `method` (NoveltyBench partition, arXiv:2504.05228, lexical-near-dup lens), `lens`
  (`lexical-near-dup`, model-free, NOT the paper's learned deduper — not paper-comparable), `shingle_k`,
  `near_dup_threshold`, `utility_discount`, `orientation` (`distinct_ratio` gt = more distinct), `confounds`
  (a tight topical prompt / shared genre / single source collapses clusters with NO model defect; mixing
  prompts inflates apparent distinctness — operator must supply a prompt-matched pool), `no_band` (no absolute
  band, like `originality_audit` / `homogeneity_audit`; thresholding is operator-side), and
  `paper_lens_incomparable` (the paper's numbers use a learned deduper; this lexical lens is not comparable).

## Proof the result carries NO verdict / label / selection scalar

Per the no-verdict posture (this is a clustering read, not a detector — but the proof still holds for the
detector-flavored-read rule: any judge-flavored read would be VALUES + a band + `calibration_status`, never a
thresholded decision — here there is **no judge at all**, so the bar is the stricter "no verdict, no band"):

1. **No provenance leaf.** A recursive key walk over `results` (the `_walk_keys` pattern from
   `test_homogeneity_audit.py`) must be **disjoint** from `{is_ai, is_human, verdict, label, same_author,
   score}`. `n_clusters` / `distinct_ratio` / `utility_weighted_distinctness` are **counts and ratios of
   reported counts**, not provenance calls. Low distinctness is NOT "AI / mode-collapsed model"; high is NOT
   "human".
2. **No band leaf.** No `band` / `provisional_band` key anywhere in `results` (matches the
   `homogeneity_audit` / `originality_audit` precedent — both ship band-less). The NoveltyBench paper's
   reference figures are named in `assumptions.paper_lens_incomparable` as an **upstream learned-deduper**
   figure, explicitly NOT transferred to this lexical lens (mirrors `homogeneity_audit`'s
   `reference_threshold_source` honesty).
3. **No selection scalar.** `representatives` is the **earliest-by-input-order** member of each cluster
   (deterministic tie-break), explicitly documented as *a positional representative, NOT a "best" / ranked /
   selected output*. The surface **never picks a winner** and emits no per-text quality/rank/`score` field.
4. **No single "diversity score."** There is intentionally **no** top-level scalar named `diversity` /
   `diversity_score`. The read is the **distribution (`cluster_sizes` + `cluster_size_distribution`) + the
   representatives**. `distinct_ratio` and `utility_weighted_distinctness` are reported *alongside* the
   distribution, never as a standalone verdict, and carry no band.

## M1 (build now) vs M2 (lazy-import + skipif seam)

- **M1 (this build):** the **lexical-near-dup** lens — `word_shingles` + `jaccard` + single-link union-find +
  the distribution/representatives/utility read. **Pure stdlib, no numpy, no spaCy, no model, no API,
  deterministic.** Fully CI-testable. `--lens lexical-near-dup` is the only choice that runs.
- **M2 (seam, NOT in this build):** a **model/embedding equivalence relation** (`--lens model-dedup`) — the
  paper's learned deduper or an embedding-cosine cutoff in place of Jaccard. **Lazy-import inside the lens
  branch**, behind `--lens model-dedup`; absent the dep → `available:false` `reason_category:
  missing_dependency` (fail loud, never a silent fallback to the lexical lens, which would change the meaning).
  Its tests are `@pytest.mark.skipif(no model dep)`. POC-gated before any promotion past `heuristic`: a
  Code-PC POC must show the model lens's partition (a) reproduces the paper's distinct-count regime and (b)
  separates a diverse human pool from a collapsed model pool. Until then M2 stays `heuristic` / null-logged.
- **Fail-loud-on-import-success too (no planted false-invariant).** In **this** build M1 wires **no** real
  deduper, so the `--lens model-dedup` path must fail loud **whether or not** a module named
  `noveltybench_deduper` happens to be importable. The seam returns a `not_implemented` /
  `missing_dependency` error block on **both** the ImportError branch (the dep is absent) and the
  import-SUCCESS branch (a module by that name exists but M1 has not wired a real deduper to it) — so a future
  package, a stub, or a name collision can **never** make `model-dedup` silently emit lexical-lens numbers
  mislabeled. Both branches use `reason_category: missing_dependency` (the established enum member; what is
  missing is a *wired* deduper, not merely the import). The no-silent-fallback invariant the docstring
  promises is pinned by a test that monkeypatches a stub `noveltybench_deduper` onto `sys.modules` and asserts
  `--lens model-dedup` still yields `available:false`.

## Contract (the testable interface)

- **task_surface:** `set_level_diversity` (**existing** — do NOT add a `claim_license_surfaces/` fragment;
  the surface label already covers "set-level diversity"). Surface label already reads *"set-level diversity &
  originality (reconstructibility vs a reference pool; within-set homogeneity)"* — the distinct-cluster read
  fits "set-level diversity" without a label edit.
- **CLI:**
  `python3 plugins/setec-voiceprint/scripts/distinct_diversity_audit.py [--manifest M | --dir D]
  [--shingle-k 5] [--near-dup-threshold 0.5] [--utility-discount 0.0] [--min-set 10]
  [--lens lexical-near-dup] [--json] [--out F]`
- **JSON envelope:** via `output_schema.build_output()` (grep-confirmed signature: keyword-only
  `task_surface, tool, version, target_path, target_words, baseline, results, claim_license, ...`); one
  `ClaimLicense` block via `claim_license.from_legacy(_claim_license(), task_surface="set_level_diversity")`.
  Error path via `build_error_output(... reason_category="bad_input")`. `validate_bounds=True` (default) runs
  the R4 walk over `results` — every leaf finite, ratios in `[0,1]`.
- **Claim license — licenses:** "the equivalence-cluster partition of the supplied pool under the named lens:
  the number of distinct clusters, the cluster-size distribution, one positional representative per cluster,
  and a utility-weighted distinctness at the operator-supplied discount." **Refuses:** any AI/human verdict;
  any "this model is mode-collapsed / lacks diversity" determination; any band; any claim that a representative
  is the "best" output; comparability to the paper's learned-deduper numbers. Low distinctness ≠ AI (a tight
  topical prompt forces it); the representative is positional, not selected.
- **`build_output` `baseline`:** `{"pool": <ref>, "n_texts": <usable>}` (mirrors `homogeneity_audit`).

## Registration plan (drop-in, NO `==N`)

Per the voiceprint-capability-golden-bump standard (drop-in as of #239; no `==N` count literal, no shared-file
edit):

1. **`plugins/setec-voiceprint/capabilities.d/distinct_diversity_audit.yaml`** — `id:
   distinct_diversity_audit`, `script_path: .../distinct_diversity_audit.py`, `surface:
   set_level_diversity`, `status: heuristic`, `family: set-level-diversity`, `handoff: none`, `consumers:
   []`; `purpose` (the partition/distinct-count/representatives read; clean-room of arXiv:2504.05228; NO
   verdict, NO band); `use_when` (a prompt-matched pool, ≥ `min_set` texts, you want discrete distinct-cluster
   structure + representatives that pairwise-cosine can't give); `do_not_use_when` (AI/human call; mixed-prompt
   or single-tight-topic pool; < set floor; want the paper's learned-deduper numbers); `compute.tier: core`
   (stdlib, no numpy — strictly lighter than `homogeneity_audit`), `length_floor_words: 15`, `min_set: 10`;
   `dependencies.python: []`, `python_optional: []` (M2 model dep is added when M2 lands); `references` (this
   spec + the arXiv link, per the cite-arXiv-in-PR-and-changelog fleet rule).
2. **`plugins/setec-voiceprint/scripts/tests/_golden_capabilities/distinct_diversity_audit.json`** — the
   per-id golden **fragment** (same content as the yaml, JSON-shaped, matching the
   `homogeneity_audit.json` fragment exactly). **Drop-in — no `_meta.json` edit, no count literal.**
3. **`changelog.d/feat-<n>-distinct-diversity-audit.md`** — a fragment (NOT a `CHANGELOG.md` edit), citing the
   arXiv id + URL in the body per the standing fleet rule.
4. **`references/signals-glossary.md`** entry + the dated **`ROADMAP.md`** status line.
5. **Pre-push gates (CI runs them):** `tools/check_capabilities_drift.py`,
   `tools/gen_calibration_readiness.py`, `tools/check_docs_freshness.py`. `references/contract_fixtures/` —
   add `distinct_diversity_audit.json` if the surface's fixture set keys per-id (match the existing
   convention; do not invent one).

## Acceptance criteria (numbered)

1. **deterministic-output** — same pool → byte-identical `results` (stable order, fixed threshold/k, integer
   union-find).
2. **envelope-shape** — `build_output()` 12-key success envelope; `results` carries
   `n_texts, n_clusters, cluster_size_distribution, cluster_sizes, distinct_ratio,
   utility_weighted_distinctness, representatives, assumptions`.
3. **collapsed-pool pin** — a pool of N identical (or `>= threshold` near-dup) texts → `n_clusters == 1`,
   `distinct_ratio == 1/N`, `cluster_sizes == [N]`, one representative.
4. **diverse-pool pin** — a pool of N genuinely distinct texts (reuse the `_DIVERSE` fixture shape from
   `test_homogeneity_audit.py`) → `n_clusters == N`, `distinct_ratio == 1.0`, N singleton clusters.
5. **partial-collapse pin** — a pool with a known near-dup block → exactly the expected cluster count and
   sizes (e.g. 12 texts, one 3-member near-dup block + 9 singletons → `n_clusters == 10`, `cluster_sizes`
   contains a 3).
6. **threshold-monotone** — raising `--near-dup-threshold` (stricter) never *decreases* `n_clusters`
   (fewer merges); lowering never *increases* it. (Structural monotonicity, no magic number.)
7. **jaccard / shingle unit pins** — `jaccard(s, s) == 1.0`; `jaccard(s, ∅) == 0.0`; `jaccard(∅, ∅) == 0.0`
   (no NaN); `word_shingles(text, k)` length = `max(0, len(tokens) - k + 1)` for `len(tokens) >= k`.
8. **claim-license-present + refuses-verdict** — `ClaimLicense` block present; `does_not_license` (lowercased)
   contains `ai/human`, the confound caveat (low distinctness is NOT a model defect), `representative` (the
   positional-not-best caveat), and `no verdict`; **no `--verdict` flag exists**.
9. **no-verdict field guard (recursive)** — `set(_walk_keys(env["results"]))` is **disjoint** from
   `{is_ai, is_human, verdict, label, same_author, score}`; and `band` / `provisional_band` /
   `diversity_score` are **absent** anywhere in `results` (the never-a-single-score guard).
10. **never-selects guard** — every `representatives[*]` carries `member_ids` and is documented/asserted as
    the **earliest-by-input-order** member; no `representatives[*]` carries a `best` / `rank` / `score` /
    `selected` key.
11. **set-floor abstention** — fewer than `--min-set` (default 10) usable texts → `available:false`
    `reason_category:bad_input` (no partition shipped on too small a set), `rc == 3`. Mirrors
    `homogeneity_audit`'s `test_set_floor_abstention`.
12. **length-floor drop** — texts below the 15-word per-text floor are dropped before the set-floor check; a
    pool of only stubs → `bad_input`.
13. **graceful-degradation** — empty pool / empty manifest → `bad_input` (no div-by-zero on `distinct_ratio`);
    malformed manifest rows are skipped with a stderr note (reuse `homogeneity_audit._load_manifest`'s
    skip-and-warn).
14. **lens-label honesty** — `results["lens"] == "lexical-near-dup"`; `assumptions.paper_lens_incomparable`
    names arXiv:2504.05228's learned deduper as NOT comparable to this lens (the
    `reference_threshold_source` honesty pattern).
15. **anti-Goodhart held-out disjoint** — any fixture used to *pin* cluster counts is **disjoint** from any
    corpus later used to calibrate the M2 lens (no fixture leaks into a calibration set). Documented in the
    test header (matches the fleet anti-Goodhart boundary; M1 ships uncalibrated so this is a forward
    guard for M2).
16. **model-dedup fails loud on import-SUCCESS too** — `--lens model-dedup` yields `available:false`
    `reason_category:missing_dependency` **even when a module named `noveltybench_deduper` is importable**
    (M1 wires no real deduper, so the seam never falls through to the lexical lens). Pinned by a test that
    monkeypatches a stub `noveltybench_deduper` onto `sys.modules`. Closes the planted-false-invariant
    silent-fallback gap.

## Structural posture guards (carry into the build)

- **No-verdict recursive walk** (AC-9) — scoped to `results`, enforced by `_walk_keys`, not prose.
- **Never-selects** (AC-10) — representatives are positional; the surface picks no winner and ranks no output.
- **No single score** (AC-9) — the distribution + representatives ARE the read; `distinct_ratio` /
  `utility_weighted_distinctness` are band-less values reported alongside, never a standalone verdict.
- **Anti-Goodhart held-out disjoint** (AC-15) — pin fixtures stay out of any M2 calibration corpus.
- **No-band** — like `originality_audit` / `homogeneity_audit`, M1 emits no absolute band; the paper's
  reference numbers are named as upstream-incomparable, never a cut.
- **No-silent-fallback** (AC-16) — `--lens model-dedup` fails loud in this build regardless of whether a
  `noveltybench_deduper` module is importable; the lexical lens is never substituted under a model-dedup label.

## Assumptions / limits

- **Lexical lens ≠ semantic equivalence.** Jaccard-over-word-shingles catches *near-verbatim* restatement
  (the dominant mode-collapse signature) but will **split** two semantically equivalent outputs that share few
  shingles (paraphrase). M1 is honest about this (`lens: lexical-near-dup`, `paper_lens_incomparable`); the
  semantic relation is the M2 model-dedup seam. This is the *conservative* failure direction: M1 may **over-
  count** distinct clusters (under-report collapse), never falsely claim collapse.
- **Prompt-matched pool required.** Mixing prompts in the pool inflates apparent distinctness; a single tight
  topical prompt deflates it. The operator must supply a prompt-matched pool — no implied default pool.
- **Threshold / k are operator knobs, not tuned constants.** Defaults (`k=5`, `threshold=0.5`,
  `discount=0.0`) are documented starting points echoed into `assumptions`, not calibrated cuts.
- **Uncalibrated / PROVISIONAL.** Ships `status: heuristic`; promotion needs a labeled diverse-vs-collapsed
  pool POC (the M2 path), recorded as a PROVENANCE entry. The default is never a verdict.
- **No plagiarism / no model-quality determination** — distinct-count is a measurement, not a claim that a
  model "lacks diversity" or that any output is derivative.

## Design calls resolved

1. **New surface or new id on `set_level_diversity`?** → **New id on the existing surface.** Precedented
   (`set_level_diversity` already backs `homogeneity_audit` + `originality_audit`; the drift linter keys on
   `script_path`, not surface-uniqueness). No `claim_license_surfaces/` fragment; the existing surface label
   covers it.
2. **Equivalence relation for M1?** → **Lexical near-dup: word-shingle Jaccard ≥ threshold, single-link
   transitive closure.** Pure stdlib, deterministic, CI-runnable; the model deduper is the M2 `--lens` seam.
   This keeps M1 strictly model-free and orthogonal to `homogeneity_audit` (which is *continuous cosine*, no
   partition).
3. **Headline read — count, ratio, or score?** → **The distribution + representatives are the read**;
   `n_clusters` (count) and `distinct_ratio` (ratio of reported counts) and `utility_weighted_distinctness`
   are reported alongside, **band-less, never a single "diversity score"** (the explicit anti-pattern). No
   `diversity_score` key exists.
4. **Representative selection (selection-scalar risk)?** → **Earliest-by-input-order member**, documented and
   asserted as *positional, not best/ranked/selected*. The surface never picks a winner.
5. **Utility weighting without a utility model?** → A **`--utility-discount` knob defaulting to `0.0`**
   (pure distinct-count); the operator owns any redundancy credit; the discount is echoed into `assumptions`,
   never a threshold.
6. **numpy?** → **Not needed for M1** (`compute.tier: core`, `python_optional: []`); union-find +
   Jaccard are integer/stdlib. (Strictly lighter than `homogeneity_audit`, which needs numpy only for
   `effective_modes`.)
7. **Reuse `homogeneity_audit`'s loaders?** → **Yes** — `_load_manifest` / `_load_dir` / the 7-key
   distribution-summary shape are the established conventions; clean-room-copy the shape (don't cross-import a
   sibling surface's privates), keeping the two scripts independent.

## Open questions

1. **Default `--near-dup-threshold` (0.5) and `--shingle-k` (5).** Starting points, not calibrated; the M2
   POC should report the threshold/k that best separates a diverse human pool from a collapsed model pool.
2. **Utility-weighted-distinctness shape.** M1 ships the discounted-rank form with `discount=0.0` (pure
   count); whether to also expose the paper's full cumulative-utility curve is an M2/M3 refinement once a
   utility lens exists.
3. **M2 lens choice** — the paper's learned deduper (paper-comparable, heavier) vs an embedding-cosine cutoff
   reusing the existing LUAR/`authorship_embedding` path (on-box, glass-box, privacy-gated like spec 22's M2).
   Resolve at the M2 POC.
4. **`references/contract_fixtures/` keying** — confirm whether the surface's fixture set is per-id (add
   `distinct_diversity_audit.json`) or per-surface (already covered) before push.
