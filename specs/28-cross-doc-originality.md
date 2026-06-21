# 28 — cross-doc-originality — cross-document homogenization & originality (skeleton-reuse + novelty-vs-pool)

> Two **set-level** diagnostics no per-document and no single-target surface can see: (a) how much a
> *corpus* of texts reuses the **same discourse skeleton** (QUDsim-style cross-document structural
> homogenization — the "everything has the same bones" axis) and (b) the **distribution** of
> novelty-vs-pool across that corpus (DJ-Search reconstructibility run set-wide, not on one target).
> Both extend the existing `set_level_diversity` surface to **originality as a property of a set**.

- **Status:** Built — M1 (this PR). Anchored against the shipped `originality_audit.py` (spec 22 M1)
  and the real `general_imposters` / `acquisition_core` / `stylometry_core` / `output_schema` /
  `claim_license` seams. **M1 cleared to spec → build; M2 model-gated** (QUD parsing needs an LLM).
- **Tier:** near-term (M1 cross-doc skeleton-overlap + set-wide novelty distribution, **stdlib**) ·
  research-grade (M2 LLM-parsed QUD units, gated)
- **GPU required:** **no** for M1 (pure stdlib: sentence segmentation has a regex fallback, see seam
  §S5; substring index reused from `originality_audit`). M2's true QUD extraction is an LLM lazy-import
  behind a skipif gate — never on the M1 path.
- **Upstream / prior art:**
  - **QUDsim** — *Quantifying Discourse Template Reuse in LLM-generated text*, question-under-discussion
    (QUD) discourse-skeleton similarity across documents ([arXiv:2504.09373](https://arxiv.org/abs/2504.09373)).
  - **Creativity Index / DJ Search** — *AI as Humanity's Salieri: Quantifying Linguistic Creativity*
    ([arXiv:2410.04265](https://arxiv.org/abs/2410.04265)); the verbatim-span reconstructibility metric
    `originality_audit` already clean-rooms — reused set-wide here.
  - Sibling to **spec 22 (`set-level-diversity`)** — same surface, same Hivemind/originality framing;
    this is its **cross-document structural** extension.
- **License decision:** **clean-room the methods.** QUDsim's *idea* is the unit of analysis (compare
  documents by their ordered discourse skeleton, not their words); the **M1 skeleton proxy is an
  original stdlib construction** (function-word + discourse-marker sentence signatures — no QUDsim
  code, no weights). DJ-Search longest-span matching is already clean-roomed in `originality_audit`
  and **reused, not reimplemented** (M1 calls its `audit_originality`). M2's QUD parser is the only
  model touch and it is gated; no third-party weights are vendored by this spec.

## Motivation

`originality_audit` (spec 22 M1, shipped — `plugins/setec-voiceprint/scripts/originality_audit.py`)
answers originality for **one target** vs a pool. Two questions remain structurally unreachable:

1. **Cross-document structural homogenization (the skeleton axis).** A corpus can be lexically diverse
   yet **structurally identical**: every essay opens with a hook, states a thesis, gives three
   evenly-weighted supports, concedes once, and closes with a call-to-action — the QUDsim finding that
   LLM corpora reuse a small set of discourse templates. No SETEC surface compares the *ordered
   discourse shape* of document A to document B.

2. **The novelty *distribution* across a set.** `originality_audit` gives one target's
   `originality = 1 − coverage`. Run on a *corpus*, the interesting object is the **distribution** of
   that scalar (leave-one-out, intra-set variant). A mean alone is a verdict waiting to happen; the
   **distribution + per-document table** is the descriptive object that keeps the human in the loop.

**Orthogonality.** New unit of analysis = an *ordered pair of documents* (skeleton overlap) and a
*set* (novelty distribution), not a doc-vs-baseline. This spec **does not modify** `originality_audit`
— it *imports* `audit_originality` from it (seam §S1) and adds new scripts on the same surface.

## The load-bearing design question (and its answer)

> **Does a cross-document *originality* extension justify a new task surface, a new claim-license
> label, and a golden-count bump — or does it ride the existing `set_level_diversity` surface?**

**Answer: it rides `set_level_diversity`. No new surface, no `claim_license_surfaces/` fragment, no
count bump.** Verified against the live tree:

- The surface already exists: `plugins/setec-voiceprint/scripts/claim_license_surfaces/set_level_diversity.txt`
  reads *"set-level diversity & originality (reconstructibility vs a reference pool; within-set
  homogeneity)"* — which **already names both arms of this item**. The shipped `originality_audit.py`
  sets `TASK_SURFACE = "set_level_diversity"` (line 34) and spec 22 established the
  **one-surface-many-scripts** pattern. This item is the **second and third ids** on that surface.
- `VALID_TASK_SURFACES` derives from the fragment dir (`output_schema.py:69`,
  `VALID_TASK_SURFACES = frozenset(TASK_SURFACE_LABELS)`, itself loaded from the drop-in files at
  `claim_license.py:53–65`), so **no `VALID_TASK_SURFACES` / `TASK_SURFACE_LABELS` edit** is allowed
  or needed.
- The capabilities-golden test derives its count from the fragment set
  (`tests/test_capabilities_dropin.py:78`, `assert len(m["entries"]) == len(golden)` — comment at
  line 41 notes there is *"no `==N` count literal"*), so adding the new ids is a matter of dropping a
  `capabilities.d/<id>.yaml` fragment **and** a per-id `tests/_golden_capabilities/<id>.json`
  fragment — **no count bump, no shared-dict edit.**

## Design (M1 model-free core + M2 gated seam)

Two scripts, one surface.

### M1a — `corpus_novelty_audit.py` (the novelty *distribution*; wraps the shipped DJ-Search core)

Given a `--corpus-dir` / `--manifest` of N documents, compute for **each document** its DJ-Search
reconstructibility **against the rest of the corpus** (leave-one-out), by calling the shipped
`audit_originality(target_text, reference, min_ngram=…)` from `originality_audit` (seam §S1) with the
target document excluded from its own reference.

**Tuple-arity adapter (FOLDED FROM FINDINGS, P2).** The §S2 loader returns
`list[tuple[source, text, resolved_path]]` (3-tuples), but `audit_originality`'s `reference` arg is
`list[tuple[str, str]]` (2-tuples). Per leave-one-out iteration, drop the self doc by resolved_path
**then strip to 2-tuples** before calling `audit_originality` — mirroring
`originality_audit.py:249–251` verbatim:

    this_abs = loaded[i][2]
    reference = [(src, text) for src, text, pth in loaded if pth != this_abs]

Report, at the value level:

- `per_document` — a list of `{id, originality, coverage, longest_match_tokens, top_source}`.
  **`top_source` definition (FOLDED FROM FINDINGS, P3):** `audit_originality` does NOT return a single
  "most-reconstructing" source — its `attribution` field is the 5 longest spans `[{length, text,
  source}]` where `source` is the FIRST reference doc containing that span. So `top_source` is defined
  **precisely** as *the source of the single longest matched span* (`attribution[0]["source"]` when
  attribution is non-empty, else `null`). It is the longest-span source, **not** "the document that
  most reconstructs this one" (that gloss is dropped).
- `novelty_distribution` — a **descriptive** summary: `{min, p25, median, p75, max, mean, sd}` over
  the per-doc `originality` values, plus an integer `histogram` (fixed deciles `0.0–1.0`). **No single
  "corpus originality score"** is emitted as a headline (design call D1 / guard G4). `sd` is guarded:
  with a single value it is `0.0` (no `statistics.StatisticsError`, no NaN — protects the §S4 R4 gate).
- `mutual_reconstructibility` — count and fraction of *ordered pairs* (A,B) where B covers ≥ a
  **reported, operator-visible** share of A. **(FOLDED FROM FINDINGS, P3):** the share threshold is
  surfaced in `assumptions.mutual_share` and is **NOT** a verdict band; no per-pair `is_reconstructed`
  boolean is emitted — only the count/fraction census.
- `assumptions` — `{method, orientation: "low novelty = more reconstructible FROM THIS corpus, NOT
  'AI'", corpus_dependence, self_exclusion, mutual_share}`.

Pure stdlib, deterministic.

### M1b — `skeleton_overlap_audit.py` (cross-document discourse-skeleton reuse; the QUDsim axis, stdlib proxy)

The QUDsim *unit of analysis* (compare documents by their ordered discourse skeleton) with a
**model-free skeleton proxy** so M1 stays CI-runnable. Per document:

1. **Segment into discourse units.** Use `stylometry_core.split_sentences` (re-exported from
   `variance_audit.split_sentences`, seam §S5) — which has a **pure-regex fallback** (`_SENT_RE`,
   `variance_audit.py:151`) when NLTK is absent, so M1 never requires a model.
2. **Compute a per-unit discourse signature** — a small, content-word-free vector designed to capture
   *rhetorical move*, not *topic*: leading **discourse-marker bucket** (a fixed stdlib list: contrast
   / addition / cause / concession / exemplification / sequence / none); structural scalars — relative
   position, normalized length, terminal-punctuation class (`.`/`?`/`!`).
3. **Discretize each unit to a small symbol** (marker-bucket × length-tercile × terminal-class → one
   of a bounded alphabet), giving each document an **ordered skeleton string** = its sequence of
   discourse-move symbols. Topic is washed out; what remains is the *shape*.
4. **Cross-document skeleton overlap** = a normalized **ordered-alignment** score between every pair
   of skeleton strings (longest-common-subsequence ratio over the symbol sequences via stdlib
   `difflib.SequenceMatcher`; deterministic), yielding a **skeleton-overlap matrix**.

Report, at the value level:
- `skeleton_overlap` — `{mean_pairwise, median_pairwise, max_pairwise}` over the off-diagonal,
  **descriptive only**.
- `pair_table` — the top-`--top-k` most-overlapping document pairs `{a, b, overlap, shared_skeleton}`.
- `template_clusters` — connected components of pairs at or above a **descriptive** `--report-threshold`
  (surfaced as "documents grouped by skeleton similarity for the reader", explicitly **not** a verdict
  band — G4). The threshold value is surfaced in `assumptions.report_threshold`.
- `per_document` — `{id, skeleton: "<symbol string>", n_units}` (auditable).
- `assumptions` — `{method, proxy_note, topic_robust, orientation: "high overlap = shared discourse
  template, NOT 'AI'", qud_lens, report_threshold}`.

Pure stdlib (`re`, `difflib`, `collections`, `statistics`), deterministic, no model, no GPU.

### M2 — `--qud-lens model` (true QUDsim QUD units; lazy-import, skipif-gated)

`--qud-lens proxy` (default) → the M1b stdlib path. `--qud-lens model` → lazy-imports a model client
**inside the branch**, and if absent emits `available:false` `missing_dependency` via
`build_error_output` (seam §S4) — **fail loud, never silently fall back to the proxy** (G7). Tests
`skipif` the model path. POC-gated before any confident band ships.

## Anchored seams (real source — verified line-by-line against the live tree)

All script paths under `plugins/setec-voiceprint/scripts/`. **(FOLDED FROM FINDINGS, P3 path
precision):** `specs/` is at the **repo root** (this file: `specs/28-cross-doc-originality.md`);
`capabilities.d/` is at `plugins/setec-voiceprint/capabilities.d/` (**not** under `scripts/`); only the
script seams and the `_golden_capabilities/` goldens are under `scripts/`.

- **§S1 — DJ-Search core (reused, not reimplemented).** `originality_audit.audit_originality(target_text,
  reference, *, min_ngram=DEFAULT_MIN_NGRAM, max_span=_MAX_SPAN) -> dict` —
  `originality_audit.py:115`. `reference` is `list[tuple[str, str]]` (2-tuples). Returns `coverage`,
  `originality`, `longest_match_tokens`, `attribution`, `assumptions`, `target_tokens`, etc.
  `TASK_SURFACE = "set_level_diversity"` (line 34), `DEFAULT_MIN_NGRAM = 8` (line 38). Raises
  `ValueError` on empty target/reference (lines 129, 133) — caller maps to `bad_input`.
- **§S2 — reference/corpus loading.** `originality_audit._load_reference_dir(root, suffixes=(".txt",".md"))`
  (`:51`) and `_load_reference_manifest(path)` (`:61`) return `list[tuple[source, text, resolved_path]]`
  (3-tuples). M1a/M1b reuse this loader shape; `resolved_path` powers self-exclusion.
- **§S3 — self-exclusion (mandatory).** Drop any pool doc whose `Path.resolve()` equals the target's,
  mirroring `originality_audit._run` (`:249–251`). M1a applies this **per leave-one-out iteration**;
  M1b excludes the self-pair from the matrix diagonal. A doc that appears twice (same resolved path) is
  dropped from its own reference; emit a `dropped_self` count in `assumptions` + a stderr note.
- **§S4 — envelope + structured error.** `output_schema.build_output(*, task_surface, tool, version,
  target_path, target_words, baseline, results, claim_license, …)` (`output_schema.py:220`) and
  `build_error_output(*, task_surface, tool, version, reason, reason_category, target_path=…)`
  (`:348`). `reason_category` ∈ `REASON_CATEGORIES` (`bad_input`, `missing_dependency`, …).
  `build_output` raises `OutputValidityError` (`:103`) on NaN/inf leaves — the R4 gate, so the
  distribution stats must be finite (guard empty/singleton inputs first).
  **(FOLDED FROM FINDINGS, P3 set-level envelope metadata):** set-level scripts have no single target.
  Pass `target_path = <corpus dir | manifest path>`, `target_words = total corpus word count`,
  `baseline = {corpus: <dir|manifest>, n_docs: N}`.
- **§S5 — model-free segmentation + topic-robust features.** `stylometry_core.split_sentences` and
  `FUNCTION_WORDS` are re-exported from `variance_audit` (`stylometry_core.py:23–29`).
  `variance_audit.split_sentences` (`variance_audit.py:155`) tries NLTK then **falls back to the pure
  regex `_SENT_RE`** (`variance_audit.py:151`) — stdlib-clean, no model.
- **§S6 — claim license.** `claim_license.from_legacy(legacy_dict, *, task_surface)`
  (`claim_license.py:224`); the surface label is loaded from the drop-in fragment. Reuse the shipped
  `originality_audit._claim_license()` pattern for the `licenses` / `does_not_license` text.
- **§S7 — privacy gate (DECISION D5, M1 does NOT inherit).** M1 outputs are aggregate
  structural/coverage statistics + a topic-stripped symbol skeleton, **not** per-text embeddings, so M1
  does **not** inherit the embedding privacy gate. M1 ships **without** `--allow-public-output`.

## Contract (the testable interface)

- **task_surface:** **reuse `set_level_diversity`** (registered). No new surface, no fragment, no
  `VALID_TASK_SURFACES` edit, no count bump.
- **Two new ids:** `corpus_novelty_audit` (M1a) and `skeleton_overlap_audit` (M1b/M2).
- **CLI:**
  - `corpus_novelty_audit.py [--corpus-dir D | --manifest M] [--min-ngram 8] [--max-span 256]
    [--min-docs 3] [--mutual-share 0.5] [--json] [--out F]`
  - `skeleton_overlap_audit.py [--corpus-dir D | --manifest M] [--qud-lens proxy|model] [--top-k 20]
    [--report-threshold 0.8] [--min-docs 3] [--json] [--out F]`
- **Set floor (abstention, D3).** Both require ≥ `--min-docs` (default 3) documents → below the floor,
  `available:false` `bad_input` (rc 3).
- **Claim license — refuses (both):** any AI/human verdict (low novelty / high skeleton-overlap is
  **NOT 'AI'**); any plagiarism/derivative determination; any band that is not operator-supplied /
  PROVISIONAL; any selection signal (G3). **(FOLDED FROM FINDINGS, P2):** the `does_not_license` text
  contains the lowercased tokens `ai/human`, `not 'ai'`, and `plagiarism` (aligned with the shipped
  `test_originality_audit.py:114` assertion).
- **No-verdict guard (FOLDED FROM FINDINGS, P2).** The structural guard ADDS `is_human` to the shipped
  set: `assert not any(k in env["results"] for k in ("verdict", "label", "is_ai", "is_human",
  "decision"))`. (The shipped `test_originality_audit.py:116` does not contain `is_human`; adding it is
  stronger.)
- **capabilities.d fragments:** `capabilities.d/corpus_novelty_audit.yaml`,
  `capabilities.d/skeleton_overlap_audit.yaml` — `surface: set_level_diversity`; `status: heuristic`;
  `compute.tier: core`; `dependencies.python: []` (M1), `python_optional` for M2's model lens;
  `use_when` / `do_not_use_when`. Each fragment is wrapped in a top-level `entries:` list (one entry,
  `id` = filename stem) — mirror `originality_audit.yaml`. Plus a per-id
  `tests/_golden_capabilities/<id>.json` fragment each — **no count bump** (count is derived).
- **Paper trail:** the two `capabilities.d/` fragments + the two `_golden_capabilities/<id>.json`
  fragments + a `changelog.d/<slug>.md` fragment (citing **both** ids and
  **arXiv:2504.09373 + arXiv:2410.04265** in the body) + the dated `ROADMAP.md` status line. **No**
  `claim_license_surfaces/` fragment (surface exists). Run `tools/check_capabilities_drift.py`,
  `tools/check_docs_freshness.py` before push (CI gates them).

## Test contract (names + invariants the build must satisfy)

`scripts/tests/test_corpus_novelty_audit.py` and `test_skeleton_overlap_audit.py`:

1. **deterministic-output** — same corpus → byte-identical `results`.
2. **envelope-shape** — `build_output()` keys present; `task_surface == "set_level_diversity"`;
   `tool` correct; enumerated `results` keys.
3. **claim-license-present + refuses-verdict** — `does_not_license` lowercased contains `ai/human`,
   `not 'ai'`, `plagiarism`; no-verdict guard (incl. `is_human`).
4. **no-aggregate-verdict-scalar (D1/G4)** — no top-level `originality_score` / `corpus_originality` /
   `homogeneity_score` key; the headline is a `*_distribution` / matrix.
5. **set-floor abstention** — `< --min-docs` → `available:false` `bad_input`, rc 3.
6. **graceful-degradation** — empty corpus / all-empty texts → `available:false` `bad_input` (no
   NaN/division-by-zero). M2 `--qud-lens model` with no client → `available:false`
   `missing_dependency` (fail loud); `skipif` the model path.
7. **self-exclusion (M1a)** — a corpus where doc D appears twice → D's leave-one-out reference drops
   the duplicate (`assumptions.dropped_self >= 1`), computed against the *rest*.
8. **numeric pins (M1a)** — N identical docs → every `originality` ≈ 0.0, `median` ≈ 0.0,
   `mutual_reconstructibility.fraction` ≈ 1.0; N mutually-disjoint docs → every `originality` ≈ 1.0,
   `fraction` ≈ 0.0; mixed → spread (`min < max`). **mutual_share back-door pin (FOLDED, P3):**
   `assumptions.mutual_share` is surfaced and no per-pair `is_reconstructed` boolean exists.
9. **numeric pins (M1b)** — N docs from the same ordered template (different content words) → high
   `mean_pairwise`, templated pair tops `pair_table`, one `template_clusters` group; structurally
   different docs → low overlap. **Topic-invariance pin:** same skeleton + different vocabulary → high
   overlap (structural, not lexical).
10. **glass-box** — `per_document[*].skeleton` is a readable symbol string; `pair_table[*].shared_skeleton`
    is the aligned run; M1a `per_document[*].top_source` names the longest-span source.
11. **never-selects (G3)** — no "most/least original" winner, no flag, no `--select`/`--flag` flag; no
    boolean/winner field. **report-threshold-is-descriptive pin (FOLDED, P3):** `--report-threshold`
    only groups for display and is surfaced in `assumptions.report_threshold`; it is not a gate.
12. **corpus-dependence caveat surfaced** — `assumptions` notes reconstructibility/overlap is
    corpus- and register-dependent.

## Anti-Goodhart guardrails

- **G1 — no-verdict field, scoped to `results`.** Neither script emits `is_ai`/`is_human`/`verdict`/
  `decision`/`label`. (Test 3.)
- **G2 — descriptive evidence, never a selection signal.** Output is for the human to read; never wired
  to choose/rank-as-decision/gate/exclude. (Test 11 + claim-license refusal.)
- **G3 — never-selects.** No "most/least original" winner, no flag, no `--select`/`--threshold-as-gate`;
  `--report-threshold` and `--mutual-share` only group/census for display. (Test 11, Test 8.)
- **G4 — no aggregate score that reads as a verdict (D1).** Headline is a distribution / matrix. (Test 4.)
- **G5 — absence-of-novelty is NOT "AI".** Hard-wired into `does_not_license` and `assumptions`.
  (Test 3, Test 12.)
- **G6 — topic-robust by construction.** Skeleton signature excludes content words. (Test 9.)
- **G7 — fail-loud model gate.** M2's `--qud-lens model` never silently degrades to the proxy. (Test 6.)

## Calibration posture

Both ship **PROVISIONAL / `status: heuristic`** — measurements (a distribution; a structural-overlap
matrix), no verdict, operator-side bands. Ride **SETEC's real `calibration_status` ladder**.

## Considered and rejected

- A new `cross_doc_originality` surface + claim-license label (split one posture, needless count bump).
- Modifying `originality_audit` to take a corpus (keep it single-target; M1a imports it).
- A single "corpus originality score" headline scalar (G4/D1: a lone scalar is a verdict in disguise).
- Embedding the skeleton (semantic clustering) for M1 (that is spec 22 M2's `homogeneity_audit`).
- True LLM-parsed QUDs in M1 (un-CI-able; the proxy is M1, the LLM QUD lens is gated M2).
- Inheriting the embedding privacy gate (§S7/D5): M1 outputs are aggregate stats + topic-stripped
  skeletons, not per-text voiceprint embeddings.

## Open questions

1. Skeleton-symbol alphabet granularity (marker-bucket × length-tercile × terminal-class — coarser/finer
   for stability at the `min_docs` floor; calibration, not a blocker).
2. Alignment metric (LCS-ratio via `difflib` vs edit-distance vs positional n-gram overlap).
3. `min_docs` set floor (default 3 is the bare minimum; calibration corpus reports the stable N).
4. M2 QUD-text privacy (re-evaluate §S7 gate if `--qud-lens model` surfaces near-verbatim QUD text).
5. Default corpus/register (impostor pool as-is vs a register-matched subset).
