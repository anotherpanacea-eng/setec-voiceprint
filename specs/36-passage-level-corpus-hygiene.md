# 36-passage-level-corpus-hygiene

> **Passage-level repetition hygiene for training corpora** — a `--passages` mode for
> `near_dup_dedup.py` with a **two-stage detection design**: **Stage A** clusters near-duplicate
> *passage units* (uncoalesced paragraphs; MinHash-LSH candidates confirmed by **exact** Jaccard)
> and can export a schema-valid filtered passage manifest; **Stage B** is an **exact shared-span
> scan** (inverted shingle index, stdlib) that finds a contiguous repeated span of the motivating
> size *inside* otherwise-distinct passages — the case passage-level Jaccard provably cannot see.
> Report-first with a mechanical claim-license carrier; spans are reported for consumer-side
> masking, never excised. Hard constraint carried mechanically: duplicate-dependent
> set-level-diversity surfaces must NOT consume a passage-deduped corpus (marker + `pool_guard`
> scan + a coverage drift test over the complete, grep-derived loader table).

- **Status:** Draft (rework 2 — folds the adversarial spec-review round: M2 cut, detection unit
  redesigned, export made schema-valid, drift test made day-one-green, private-run references
  sanitized, spec made self-contained)
- **Tier:** near-term (CI-runnable; Stage A needs only the existing optional `datasketch`
  acquisition dep, Stage B is pure stdlib)
- **GPU required:** no
- **Upstream / prior art:** the shipped `near_dup_dedup.py` (MinHash-LSH via datasketch, MIT);
  training-data dedup → memorization prior art: Lee et al., *Deduplicating Training Data Makes
  Language Models Better* (ACL 2022, [arXiv:2107.06499](https://arxiv.org/abs/2107.06499) — the
  exact-substring dedup pass is the direct ancestor of Stage B) and Carlini et al., *Quantifying
  Memorization Across Neural Language Models*
  ([arXiv:2202.07646](https://arxiv.org/abs/2202.07646)) — repeated training sequences are
  memorized disproportionately faster, the mechanism the motivating finding exhibits.
- **License decision:** N/A — no new deps, no weights. `datasketch` (MIT) is already the declared
  optional acquisition dep of `near_dup_dedup.py`.
- **Requirements (self-contained):** close the sub-document repetition gap in corpus hygiene —
  both hash-exact dedup (`acquisition_core.content_hash_already_present()`, byte-exact,
  single-directory) and `near_dup_dedup.py`'s document mode (MinHash-LSH, 5-word shingles,
  Jaccard 0.8, deterministic representative = longest text then lowest id) operate on **whole
  documents**; a passage repeated *inside* or *across* otherwise-distinct documents is invisible
  to both. This spec adds the passage/span-level lens, with the guard, symmetry, and honesty
  constraints stated in full below (no external planning doc is required to build this).

## STEP 0 — verified NOT already built

`grep -n "passage" plugins/setec-voiceprint/scripts/near_dup_dedup.py` → no hits. The module is
document-level only: `dedup_records((id, text), ...)` over whole-document texts, one manifest row
per record. No chunking driver, no span scan, no sub-document provenance. No other module in
`scripts/` implements a repeated-span detector. `already_built = false`.

## Motivation

### The gap

Existing corpus hygiene is *document*-grained on both axes it covers:

- **Identity axis:** exact SHA-256 dedup (acquisition-inline) and document-level near-dup
  (`near_dup_dedup.py`) drop whole-document duplicates. Neither sees a repeated *passage*.
- **Content-type axis (the boundary this spec does NOT cross):** `check_corpus.py` — and
  `validation_harness --check-corpus` as its preflight wiring — detects **non-prose
  contamination**: CSS/HTML/code/tables that spaCy would POS-tag as prose. That is a different
  failure class entirely. `check_corpus` asks *"is this prose?"*; this spec asks *"is this prose
  repeated?"*. A corpus can be 100% clean prose and still carry a passage five times. Nothing in
  this spec touches, replaces, or overlaps `check_corpus.py` — the two are orthogonal gates an
  operator may run on the same staged manifest, and the boundary sentence goes into the
  capability docs so neither is mistaken for the other.

### The motivating finding (calibration evidence)

A continued-pretraining run on a **private single-author corpus** (operator-run, not
redistributable) reproduced a **contiguous 41-token training-corpus span** in a generated
output, with maximum policy overlap rising **~0.42 → ~0.59** base→adapter, after byte-exact
document dedup had already run — so the span is **not attributable to a whole-document
duplicate**. The full calibration record (policy parameters, matched base/adapter values,
adapter hash, and the statistical caveats) is held in the operator's private fleet hub as
`handoffs/CPT-RUNG2-MEMORIZATION-CALIBRATION-2026-07-23.md`.

**Carried caveats (the finding's own limits, which this spec must not overstate):** the
aggregate reproduction count moved 5→2 across matched arms — exact McNemar `p = 0.453`,
**unresolved in both directions** — and the *base* arm produced 5/63 reproductions before any
adaptation, so **no absolute memorization rate is claimable** from this run. The finding
licenses exactly one claim: *a contiguous 41-token span was reproduced, and its source is
sub-document repetition that document-level dedup cannot see.* It does **not** show that
memorization worsened, and this spec must not imply it does. Repeated spans being the
fastest-memorized material under multi-epoch training is the independently cited mechanism that
makes the gap worth closing (arXiv:2107.06499, arXiv:2202.07646).

This finding plays the role for this spec that a dated empirical observation plays for the
shipped document-level safeguards: the concrete case that calibrates why the guard exists.

**Orthogonality:** M1 measures a corpus-hygiene axis no shipped surface covers — *sub-document*
repetition across a staged manifest. Exact-hash = whole-document byte identity; `near_dup_dedup`
doc mode = whole-document near-identity; `check_corpus` = non-prose content; `originality_audit`
/ `corpus_novelty_audit` = target-vs-pool / leave-one-out reconstructibility *measurements*, not
an actionable duplicate-span inventory with provenance.

## M1 — passage/span mode for `near_dup_dedup.py`

### Unit of analysis

A staged JSONL acquisition manifest (the module's existing input contract: `id` + inline `text`
or resolvable `text_path`/`path` per row). Both *within-document* repetition (the same passage
twice in one document) and *cross-document* repetition (shared boilerplate, a quoted epigraph, a
syndicated block, a repeated bio/sign-off) are in scope — the cases the document lens is
structurally blind to.

### Why one passage-level Jaccard pass cannot work (the arithmetic)

The first draft chunked documents into coalesced paragraphs and ran the existing Jaccard-0.8 LSH
over them. Spec review showed this **fails at the spec's own motivating case**, and the
arithmetic is unambiguous. With `k = 5` word shingles, a 41-token verbatim span shared between
two otherwise-distinct ~120-word passages contributes `41 − 5 + 1 = 37` shared shingles; each
passage carries `≈ 120 − 4 = 116` shingles; Jaccard `= 37 / (2·116 − 37) ≈ 0.19` — nowhere near
0.8, and still under any radically loosened threshold that doesn't also merge unrelated prose.
Greedy coalescing of short paragraphs makes it *worse*: a sub-floor boilerplate paragraph (bio,
disclaimer, sign-off — the flagship target class) glues to a **different neighbor in each
document**, driving the shared-content Jaccard toward ~0.05. No single passage-unit similarity
threshold detects an embedded span whose length is a small fraction of the unit. The first
draft's claims that "paragraph chunks put the detection unit at the scale the finding lives at"
and that the design's failure direction was "never a false duplicate" are both **retracted**
(the false-positive retraction is expanded under Stage A). The detection design is therefore
two-stage, with the embedded-span case handled by an exact scan whose detection guarantee is
arithmetic, not thresholded.

### Stage A — near-duplicate passage units (the whole-passage class)

Detects passages that are near-duplicates **as units**: reused boilerplate paragraphs, lightly
edited reprints of a passage, repeated section templates.

- **Chunking: raw paragraphs, no coalescing.** Split each document on blank lines
  (`re.split(r"\n\s*\n+", ...)`, the shape of `stylometry_core.paragraphs`, clean-room-copied
  locally — `near_dup_dedup.py` deliberately imports nothing from the audit stack). Paragraphs
  are never merged and never split: coalescing was shown to *destroy* the short-boilerplate
  signal (above), and paragraph boundaries are the authorial unit the whole-passage class recurs
  in.
- **Short passages never enter the LSH.** Paragraphs with fewer than `--min-passage-words`
  tokens (default **10**, i.e. `2k`) are grouped by **exact equality of their normalized token
  sequence** (lowercased `_WORD_RE` tokens, the module's existing normalization) instead of
  MinHash. This both catches repeated short sign-offs *exactly* and closes a false-positive
  class the review flagged: `shingles()`'s documented sub-`k` fallback collapses any <5-word
  text to a single whole-text shingle, so two such passages can score Jaccard exactly 1.0
  spuriously under MinHash. Passage mode never exposes sub-`k` texts to the estimator. (The
  library fallback itself is unchanged — document mode keeps its shipped behavior.)
- **Comparison: LSH candidates, EXACT-Jaccard confirmation.** Passages ≥ the floor are shingled
  (`shingles(text, k=5)`, the existing helper) and indexed in MinHash-LSH exactly as document
  mode does — but candidate pairs are confirmed against the **true shingle sets** (already in
  memory from signature construction), not against `MinHash.jaccard()`'s estimate. Rationale
  (the second half of the retraction): at `num_perm = 128` the MinHash Jaccard estimate has
  SE ≈ 0.04 near `J = 0.8`, sitting exactly in the borderline band — and passage-scale
  comparison produces far more borderline pairs than document scale. Estimated-Jaccard
  confirmation therefore admits false merges, and false merges are **destructive here**: Stage
  A's export drops non-representative cluster members from a *training* corpus. Exact
  confirmation makes every confirmed pair a true `≥ threshold` near-duplicate,
  deterministically, at the cost of one set intersection per LSH candidate pair (candidates are
  already the pruned set; cheap). LSH remains the *candidate generator only*.
- **Clustering + representative:** unchanged from the shipped path — union-find over confirmed
  pairs, deterministic representative (longest text, then lowest id; zero-padded passage
  ordinals keep the id tiebreak sane).
- **Residual false-positive sense, named:** after exact confirmation, a "false positive" can
  only mean *legitimately repeated content the author intends* (an epigraph, a refrain, a
  recurring quotation). That is an editorial judgment, not a similarity error — the report
  carries every occurrence with provenance precisely so the operator reviews before any export,
  and the export never runs without the report.

### Stage B — exact shared-span scan (the embedded-span class; the motivating case)

Detects a contiguous verbatim span repeated at ≥2 locations, **regardless of the passages it is
embedded in**. Pure stdlib, no datasketch, exact by construction — the word-granularity analogue
of Lee et al.'s exact-substring dedup pass (arXiv:2107.06499).

- **Index:** tokenize each document with the module's existing `_WORD_RE` (lowercased for
  matching; raw char offsets retained per token via `finditer`). Build an inverted index from
  each word `k`-shingle (`--span-shingle-k`, default **8**) to its list of
  `(doc_id, token_position)` occurrences.
- **Mark + merge:** every token position covered by a shingle occurring at ≥2 distinct locations
  is marked; consecutive marked positions within a document merge into **maximal duplicated
  regions**. Verbatim-identical spans across locations are additionally grouped into
  **repeated-span clusters** keyed on the sha256 of the normalized token sequence.
- **Report floor:** clusters/regions shorter than `--min-span-words` (default **20**) are
  counted but not itemized.
- **The detection guarantee (the arithmetic the review demanded):** a verbatim repeated span of
  `L` tokens produces `L − k + 1` identical `k`-shingles at consecutive positions in *every*
  occurrence. Stage B therefore reports **every verbatim repeated span with
  `L ≥ max(k, min_span_words)`** — with defaults, every span of ≥ 20 tokens. The motivating
  41-token span yields `41 − 8 + 1 = 34` consecutive shared 8-shingles at each of its two
  locations and is reported **exactly and deterministically, with no probabilistic estimate and
  no dependence on what surrounds it**. (Contrast the single-pass design it replaces: the same
  span scored J ≈ 0.19 and was missed.)
- **Parameter rationale:** `k = 8` rather than reusing `k = 5` because the span index is
  *exact*, so incidental shared n-grams are pure noise — common English 5-grams ("at the end of
  the") collide constantly, 8-grams rarely do. `min_span_words = 20` is a reporting floor chosen
  under the 41-token evidence to keep idiom/quotation collisions out of the headline list; both
  are operator knobs echoed into `assumptions`, uncalibrated; lowering `--min-span-words` toward
  `k` widens the net to ≥8-token spans.
- **Named limits:** (a) Stage B is **verbatim-exact** — an edit inside a repeated span splits it
  into verbatim sub-spans, each reported only if it still clears the floor; lightly-*edited*
  whole-passage reuse is Stage A's class, and edited sub-passage reuse below the floor is
  detected by neither stage (the honest residual, stated in `assumptions` and
  `does_not_license`). (b) Memory is O(corpus tokens) for the index — fine at personal-corpus
  scale; a run at large-corpus scale falls under the repo's standing long-running-surface rules
  (recoverable / visible / continuable) and should shard via the existing `shard_runner`
  conventions before this mode is pointed at anything bigger than a staged personal manifest.
- **Stage B never drops anything.** Its output is the span inventory (report + per-document
  duplicated-region map) for consumer-side action — loss-masking or chunk-stream filtering at
  training time is the training pipeline's decision. Excising spans from documents is refused on
  the same grounds as ever: it mutilates prose and shifts the stylometric properties the corpus
  exists to carry.

Stage selection: `--stages a,b` (default `a,b`). Stage A requires datasketch (absent → the
module's existing clear RuntimeError naming `requirements-acquisition.txt`); `--stages b` runs
stdlib-only. No silent degradation: requesting a stage whose dep is missing fails loud.

### Passage identity + provenance record

Each Stage-A passage gets a deterministic id `"<doc_id>#p<NNNN>"` (`NNNN` = zero-padded ordinal,
input order). `dedup_records` already raises `ValueError` on duplicate ids; the driver
additionally rejects a doc id that itself ends in the `#p<digits>` pattern rather than emit
ambiguous provenance. Every passage and every span occurrence carries a provenance record:

```json
{
  "passage_id": "blog-2019-04#p0007",
  "source_doc_id": "blog-2019-04",
  "source_manifest": "corpus_manifest.jsonl",
  "ordinal": 7,
  "char_start": 4312,
  "char_end": 4790,
  "n_words": 84,
  "sha256": "<hash of the EXACT raw substring text[char_start:char_end]>"
}
```

`char_start`/`char_end` index the document text **as loaded** (post `errors="replace"` decode,
no normalization), so `text[char_start:char_end]` reproduces the passage/span byte-for-byte —
the slice-back invariant is a pinned test. The per-occurrence hash is of the **exact raw slice**
— no case folding, no punctuation folding, no NFC (folded fingerprints over-match distinct text;
provenance hashes must be exact). Span-cluster keys are the sha256 of the *normalized token
sequence* (that is the matched object); the two hashes serve different roles and both are
recorded.

### Determinism

Deterministic chunking (byte-driven paragraph split); exact-Jaccard confirmation and the exact
span index (no estimation participates in any accept/reject decision — MinHash-LSH is
candidate-generation only, and datasketch's fixed default seed keeps even the candidate set
stable for a given datasketch version); stable representative rule; zero-padded positional ids.
Same input manifest + same params ⇒ byte-identical report and export. Pinned by a run-twice
byte-identity test, not asserted.

### The report — with a mechanical honesty carrier

The report is the primary artifact (stdout with `--json`, and `--report-out FILE`). It extends
the module's existing plain-JSON style (acquisition family; the full `build_output` envelope is
not adopted — Design calls #4) but — closing the review's P1 on carrier-less refusals — the
passage-mode report **must carry its own structured honesty block**, not prose in
`capabilities.d/`:

- `assumptions` — params echo (stages, `k`, `span_shingle_k`, thresholds, floors), the named
  limits (verbatim-only Stage B; sub-floor fragments; edited sub-passage reuse detected by
  neither stage; the guard's dir-input limit), and — wherever the report references the
  motivating calibration — its carried caveats (no absolute memorization rate; span
  reproduction ≠ worsened memorization).
- `claim_license` — a real `ClaimLicense` block via
  `claim_license.from_legacy(..., task_surface="voice_coherence_acquisition")` (the surface is
  registered — `claim_license_surfaces/voice_coherence_acquisition.txt` exists, grep-confirmed;
  `claim_license.py` is stdlib, so the module's import purity holds). **Licenses:** "the
  inventory of near-duplicate passage clusters and verbatim repeated spans in the supplied
  manifest, with per-occurrence provenance, under the echoed parameters." **Refuses:** any
  "memorization-safe" / "clean corpus" determination (an empty report at these floors is not
  absence of repetition below them); any AI/human or provenance verdict; any claim that a
  reported repetition is *illegitimate* (epigraphs and refrains are authorial choices); any
  absolute memorization-rate claim from the motivating calibration.
- Document mode's output is **unchanged** (backward compatibility); the carrier is a
  passage-mode requirement, pinned by tests (claim-license-present, refusal strings, and a
  recursive key-walk over the report disjoint from
  `{is_ai, is_human, verdict, label, same_author, score}` with no `band` key).

Report body keys: `mode: "passages"`, `stages`, `n_documents`, `n_passages`,
`stage_a: {clusters, kept, dropped, short_exact_groups}`,
`stage_b: {repeated_spans, duplicated_regions, n_below_floor}`, `documents_affected` (doc id →
passages dropped + spans present), `provenance` (for every itemized passage/occurrence),
`assumptions`, `claim_license`.

### The export (`--out` + `--passage-dir`) — schema-valid, provenance-inheriting

The review found the first draft's export shape produced **three hard errors** under the repo's
own `manifest_validator` (`REQUIRED_FIELDS = ("id", "path", "ai_status", "use")`,
`manifest_validator.py:135`; inline `text` is not in `KNOWN_FIELDS`; no resolvable `path`) —
and, worse, silently dropped `ai_status`, `privacy`, and `consent_status` from a
*training-corpus* artifact, creating a redaction-status laundering path around checks that only
ever ran at document level. Resolution: **the export stays** (it is the artifact the mechanical
guard exists for, and a passage-unit corpus is directly consumable by chunk-stream training
pipelines), redesigned to be **valid under `manifest_validator` by construction**:

- `--out MANIFEST.jsonl --passage-dir DIR` (both required together): each **kept** Stage-A
  passage is written as a text file `DIR/<passage_id>.txt`, and its manifest row carries `id`
  (passage id), `path` (relative path to that file — resolvable, satisfying the validator's
  path check), and **every inheritable field of its source row copied verbatim** — explicitly
  including `ai_status`, `use`, `privacy`, `consent_status`, `register`, `language_status`,
  `corpus_role`, `author`, `persona`, `source`, `date_written`, `era`, `topic`, `notes` (rule:
  all source-row fields are inherited except `id` / `path` / `word_count` / `content_hash`,
  which are recomputed per passage) — plus the `passage_dedup` provenance object (the guard
  marker).
- **Refusal, not fabrication:** if any source row feeding the export lacks a field that is
  REQUIRED on the output row (`ai_status`, `use`), the export **refuses entirely** (clear error
  naming the rows; no partial write; the report is still produced). A hygiene tool must not
  invent provenance for a training artifact. There is no bypass flag.
- `passage_dedup` is added to `manifest_validator.KNOWN_FIELDS` (a one-line registration of the
  marker as a known field — NOT new validation logic; the validator's unknown-field behavior is
  a warning, but the marker is load-bearing for the guard and must not read as an unknown
  stray).
- **Acceptance is mechanical:** a test runs `manifest_validator` over an emitted export and
  asserts **zero errors** (warnings allowed only where the source manifest itself warned).
- Stage B contributes nothing to the export (spans are never excised); the export is the
  Stage-A-deduplicated passage corpus only, and the report always accompanies it.
- Passage mode **never rewrites the input document manifest** — the document-mode
  in-place-rewrite path is structurally unreachable under `--passages`.

### The mechanical guard — set-level-diversity surfaces must not consume this

**Constraint (hard):** the duplicate-dependent set-level-diversity surfaces measure signals that
*live in* retained duplicates (collapse/homogeneity/reuse/novelty) — deduping their pools
destroys the measured object. This is the repo's known recurring bug class (the 2026-06/07
self-exclusion sweeps, #306/#307: dedup applied to pools whose purpose is diversity
measurement). A prose warning is not the fix; neither is a kwarg on one shared loader, because
the pool-loader class contains clean-room copies a shared signature cannot reach.

#### The complete pool-loader surface table (grep-derived, 2026-07-23 — normative, not illustrative)

Enumerated by sweeping `plugins/setec-voiceprint/scripts/*.py` (non-recursive; `tests/`,
`runners/`, `calibration/` excluded — fixtures and operator tooling, not envelope-emitting
surfaces) for (i) every module-top-level definition matching the **anchored** patterns
`^def _load_reference_manifest\(`, `^def _load_reference_dir\(`, `^def _load_manifest\(` — the
anchor + exact-name-plus-paren deliberately **excludes** the prefix family
(`near_dup_dedup._load_manifest_records` and any `_load_manifest_entries`-style names), which
are single-consumer private helpers, not the shared pool-loader shape; (ii) every import binding
the names `_load_reference_manifest` / `_load_reference_dir`, **matched on the imported names
regardless of source module**; and (iii) every `TASK_SURFACE = "set_level_diversity"`
declaration. Classification is by **duplicate-dependence of the measurement** (the #306/#307
comparison-vs-diversity purpose rule), not by surface tag — tag and classification demonstrably
diverge in both directions:

| Module | Surface (verified) | Pool loader (verified) | Duplicate-dependence | Guard |
|---|---|---|---|---|
| `corpus_novelty_audit.py` | `set_level_diversity` | imports shared loaders from `originality_audit` (`:36-37`) | leave-one-out novelty — a repeated passage IS the low-novelty signal | **FIRES** |
| `homogeneity_audit.py` | `set_level_diversity` | **own** `_load_manifest` (`:71`; its `:73` docstring only says it "mirrors" the shared loader's shape — it imports nothing, parses rows itself) | pool collapse is the read | **FIRES** (pool `--manifest`; dir-input limit below) |
| `distinct_diversity_audit.py` | `set_level_diversity` | **own** `_load_manifest` (`:92`) | cluster sizes are the read | **FIRES** |
| `skeleton_overlap_audit.py` | `set_level_diversity` | imports shared loaders from `originality_audit` (`:36`) | cross-document skeleton REUSE is the read | **FIRES** |
| `cross_doc_novelty_profile.py` | `set_level_diversity` | **own clean-room copies** of both shared loaders (`:79`, `:95`) | pool mean/SD as-it-is (including a collapsed mode) is the measured object; silent dedup widens SD, inflates apparent novelty | **FIRES** |
| `originality_audit.py` | `set_level_diversity` | canonical definer (`:52`, `:62`) | duplicate pool members are *idempotent* for longest-match coverage — copy count provably cannot change the result | **EXEMPT** (pinned negative control) |
| `cross_doc_argument_consistency.py` | `argument_consistency` | imports the shared loaders **from `cross_doc_novelty_profile`** (`:67-70` — NOT from `originality_audit`; an import predicate keyed on the source module misses it, which is why the sweep keys on the imported *names*) | claim-consistency comparison across an author's docs; not duplicate-dependent | **EXEMPT** |
| `general_imposters.py` | `voice_coherence` | **own** `_load_manifest` (`:164`) | impostor-pool **comparison** consumer — near-dup dedup of an impostor pool is legitimate and sometimes required (#306/#307 purpose rule) | **EXEMPT** |
| `binoculars_calibrate.py` | `calibration` | **own** `_load_manifest` (`:296`) | labeled-corpus calibration scoring loader — a labeled eval set, not a diversity pool | **EXEMPT** |

The prior draft's coverage test would have **red-failed on day one** on the last two rows (both
match the loader pattern, neither was classified), and its rationale-free exempt list made the
cheapest green either name-list widening or bolting the guard onto a comparison pool — the exact
inversion the guard forbids. Hence: the table above is the **complete** classification map,
every row (FIRES *and* EXEMPT) carries a required rationale, and the drift test embeds the map
with rationale strings mandatory.

#### Mechanism — marker scan at a layer every path must cross, plus a coverage drift test

1. **Producer stamp.** Every row the export writes carries the `passage_dedup` key. Not
   optional, not strippable by flag — it is the artifact's identity.
2. **One shared checker, called per surface — NOT a loader kwarg.** New tiny stdlib
   `scripts/pool_guard.py`: `PASSAGE_DEDUP_MARKER = "passage_dedup"` and
   `scan_manifest_for_passage_dedup(path) -> list[str]` (ids/line numbers of marked rows; raw
   one-pass JSONL scan). Each **FIRES** surface calls it on its **manifest path** around its
   load and, on a hit, exits via its own
   `build_error_output(..., reason_category="bad_input")` (rc 3, the shipped
   set-floor-abstention shape) with a message naming the invariant ("set-level-diversity pools
   depend on retained duplicates; this manifest is passage-deduped — feed the pre-dedup source
   manifest"). File-level because (a) it reaches clean-room copies and own-loader surfaces
   identically — a kwarg on `originality_audit._load_reference_manifest` structurally cannot
   reach `cross_doc_novelty_profile`'s copy (that earlier mechanism is withdrawn) — and (b)
   every loader in the table returns `(id, text[, path])` tuples, discarding the row dict where
   the marker lives, so no post-load check can work without changing every loader's return
   shape.
3. **The coverage drift test** (`tests/test_pool_guard_coverage.py`) — the class-level
   enforcement, specified to be **green on day one** against the table above:
   - **(a) Axis enumeration is closed.** Modules in scope (the pinned glob + exclusions above)
     declaring `TASK_SURFACE = "set_level_diversity"` == the table's six. A new axis surface
     fails until classified.
   - **(b) Loader-definer sweep is closed.** Modules with an anchored
     `^def _load_reference_manifest\(` / `^def _load_reference_dir\(` / `^def _load_manifest\(`
     == the table's definers (including `general_imposters`, `binoculars_calibrate`). A new
     loader or clean-room copy fails until classified.
   - **(c) Loader-importer sweep is closed.** Modules whose import statements bind the names
     `_load_reference_manifest` / `_load_reference_dir` — matched on the **names**, not the
     source module (this is what catches `cross_doc_argument_consistency`'s import from
     `cross_doc_novelty_profile`) — == the table's importers.
   - **(d) Every classified module is guarded or exempt-with-rationale.** FIRES ⇒ source
     imports+calls `pool_guard`. EXEMPT ⇒ present in the test's classification map with a
     **mandatory non-empty rationale string** (the map mirrors the table verbatim; an entry
     without a rationale is itself a test failure — so "add a name to the list" is never the
     cheapest green).
   These are source-scan assertions (the drift-linter style of mechanical gate) because one
   behavioral harness cannot generically drive nine CLIs; the behavioral refusals are pinned
   per-surface (test contract).
4. **Self-guard.** Passage mode refuses an input manifest whose rows already carry
   `passage_dedup` (re-chunking nests provenance and silently re-dedups; reruns start from the
   source document manifest).

#### Named limits of the guard

- **Directory inputs cannot carry the marker.** `--dir` / `--reference-dir` / `--corpus-dir`
  loads consume bare text files with no row metadata; `homogeneity_audit`'s proximity mode takes
  `--centroid`/`--centroid-dir` (file/dir — no manifest exists in that mode). The guard is a
  **manifest-path** check: a default-path guard against the recurring accident, not an
  adversarial-proof seal — named in each refusal message and in the capability docs.
- An operator who hand-strips the `passage_dedup` key has asserted responsibility, as with any
  manifest edit.

### Symmetry requirement (restated in full — no external doc needed)

The framework's standing preprocessing-symmetry rule — already shipped behavior in the
document-level pipeline, where the non-prose stripping applied to a target is applied to
baseline files under the same rules — is: **any preprocessing applied to a target corpus must be
applied identically to the baselines it will be compared against, or the comparison is
asymmetric and its readings drift unpredictably.** Passage-level dedup is corpus preprocessing.
If a passage-deduped export later serves as either side of a target-vs-baseline comparison, the
identical pass — same stages, floors, `k`s, threshold — must be applied to the other side. The
`passage_dedup` marker's params echo makes asymmetry *auditable* (both sides carry matching
params or the comparison is asymmetric); enforcing it inside `voice_distance` is out of scope
and named as operator/consumer responsibility, exactly as the document-level rule is.

## Contract (the testable interface)

- **task_surface:** `voice_coherence_acquisition` (existing — no new surface, no
  `claim_license_surfaces/` fragment).
- **CLI:** `python3 plugins/setec-voiceprint/scripts/near_dup_dedup.py <manifest> --passages
  [--stages a,b] [--min-passage-words 10] [--threshold 0.8] [--num-perm 128] [--shingle-size 5]
  [--span-shingle-k 8] [--min-span-words 20] [--report-out FILE]
  [--out MANIFEST --passage-dir DIR] [--json]`. Passage mode without `--out` is report-only;
  `--out` requires `--passage-dir`; passage mode never rewrites the input manifest. Document
  mode's CLI and output are unchanged.
- **JSON:** the passage-mode report as specified above (plain acquisition-family JSON + the
  mandatory `assumptions` + `claim_license` carrier). The export as specified above
  (validator-clean rows + sidecar passage files).
- **Dependencies / footprint:** Stage A — `datasketch` at call time only (existing lazy-import +
  RuntimeError path preserved; base `import near_dup_dedup` stays stdlib-clean — the new
  `claim_license` import is stdlib). Stage B — stdlib only.

## Registration plan (drop-in, NO `==N`)

1. **`capabilities.d/near_dup_dedup.yaml`** updated in place (same id — a mode, not a new
   capability): purpose/use_when/do_not_use_when gain the passage/span mode, the
   `check_corpus`-boundary sentence, and the set-level-diversity refusal note; `examples` gains
   a `--passages` invocation; `references` gains this spec + the two arXiv ids (cite-arXiv rule:
   also in the PR body + changelog fragment). Regenerate
   `_golden_capabilities/near_dup_dedup.json` to match.
2. **Guard build:** new `scripts/pool_guard.py` + one-line call sites in the five FIRES surfaces
   + `tests/test_pool_guard_coverage.py`. `originality_audit.py` and its loaders are untouched.
   `pool_guard.py` is an internal helper, not a capability — no `capabilities.d/` fragment (no
   CLI, no surface; confirm against `check_capabilities_drift.py`'s script-coverage rule at
   build).
3. **`manifest_validator.py`:** `passage_dedup` added to `KNOWN_FIELDS` (marker registration
   only; no new validation logic).
4. **`changelog.d/` fragments** (never edit `CHANGELOG.md`): the guard is a behavior change to
   five shipped surfaces — the fragment must reference those capability ids so the
   docs-freshness gate counts them; the mode fragment cites the arXiv ids.
5. **`references/signals-glossary.md`:** no new *signal* (this is hygiene, not a diagnostic) —
   entry only if the glossary's own convention covers hygiene passes (check its preamble at
   build; don't invent).
6. **`references/contract_fixtures/`:** `near_dup_dedup` has no fixture there and is not
   consumer-dispatched (`consumers: []`, not in the `setec_run` consumer manifest) — no fixture
   sync expected; confirm against the fixture README at build.
7. **Pre-push gates:** `tools/check_capabilities_drift.py`, `tools/gen_calibration_readiness.py`,
   `tools/check_docs_freshness.py`.
8. **PR shape:** one PR (mode + guard travel together — the guard protects the mode's artifact).

## Test contract (names + invariants the build must satisfy)

Extend `tests/test_near_dup_dedup.py`; guard behavioral tests land in the guarded surfaces'
existing test files; new `tests/test_pool_guard_coverage.py`.

1. **chunker pins:** a fixture document with known paragraph structure → the exact expected
   passages (no coalescing; paragraphs never split); for every passage,
   `doc_text[char_start:char_end]` equals the passage text byte-for-byte and `sha256` matches
   the exact raw slice (no folding, no NFC).
2. **short-passage exact grouping:** two identical 3-word sign-off paragraphs across documents →
   one exact group in `short_exact_groups`; two *different* sub-`k` paragraphs → NOT grouped
   (the sub-`k`-fallback false-positive class is closed: no sub-floor text reaches the LSH,
   asserted structurally).
3. **Stage A exact-confirmation pin:** a candidate pair whose exact Jaccard is just below
   threshold is NOT merged even when presented as an LSH candidate; a pair with exact
   `J ≥ threshold` is merged. (Pins confirm-on-exact, not confirm-on-estimate.)
4. **the motivating case pinned (Stage B):** two documents that are NOT document-level
   near-duplicates, sharing only a single embedded ~41-token verbatim span inside
   otherwise-distinct ~120-word paragraphs → Stage A reports **no** cluster for them (honest:
   this is the class Stage A cannot see) and Stage B reports **exactly one** repeated-span
   cluster of 41 tokens with two occurrences, each provenance-traced with char offsets that
   slice back to the span.
5. **Stage B guarantee sweep:** verbatim spans of length 19 (below floor: counted, not
   itemized), 20 (reported), and 41 (reported) at assorted offsets, including a within-document
   repeat → all reported per the `L ≥ max(k, min_span_words)` rule; an *edited* copy (one token
   changed mid-span) splits into the expected verbatim sub-spans.
6. **report-only default:** passage mode without `--out` leaves the input manifest
   byte-identical and writes no manifest.
7. **claim-license carrier:** the passage-mode report carries `assumptions` and a `ClaimLicense`
   whose `does_not_license` (lowercased) names "memorization-safe", the no-absolute-rate caveat,
   and no-AI/human-verdict; a recursive key-walk over the report is disjoint from
   `{is_ai, is_human, verdict, label, same_author, score}` and contains no `band`; document
   mode's output shape is byte-unchanged on the existing fixtures.
8. **export validator-clean:** run `manifest_validator` over an emitted export → **zero
   errors**; every row resolves its `path` to a written passage file; every row carries
   `ai_status`, `use`, and the inherited fields verbatim from its source row, plus
   `passage_dedup`.
9. **export refusal, not fabrication:** a source row missing `ai_status` or `use` → the export
   refuses entirely (clear error naming the row; no partial write; the report is still
   produced).
10. **deterministic rerun:** same input + params → byte-identical report and export (run twice).
11. **representative rule inherited:** longest passage kept; tie → lowest passage id.
12. **no in-place rewrite in passage mode**; doc-mode behavior unchanged (existing tests green).
13. **self-guard:** passage mode on a marker-carrying manifest → clear error naming the
    rerun-from-source rule; nonzero exit.
14. **id-collision guard:** a doc id ending in `#p<digits>` → clear error, not ambiguous
    provenance.
15. **missing-dep paths:** without datasketch, `--stages a,b` and `--stages a` raise the
    existing RuntimeError; `--stages b` runs stdlib-only and the report states Stage A was not
    run (no silent degradation into "no Stage-A findings").
16. **guard — refusals (all five FIRES surfaces):** a marker-carrying manifest via the manifest
    path of `corpus_novelty_audit`, `homogeneity_audit` (pool mode), `distinct_diversity_audit`,
    `skeleton_overlap_audit`, `cross_doc_novelty_profile` → `available:false`,
    `reason_category: bad_input`, rc 3, message naming the retained-duplicates invariant. One
    behavioral test per surface, in that surface's test file.
17. **guard — negative controls:** the same manifest is accepted by `originality_audit` (as
    comparison reference), `cross_doc_argument_consistency`, `general_imposters`, and
    `binoculars_calibrate` — the guard must not creep onto comparison/calibration pools.
18. **guard — clean input unaffected:** all nine table surfaces behave identically to today on
    an unmarked manifest (no false refusal).
19. **guard — coverage drift test green on day one and closed:** assertions (a)–(d) as
    specified, seeded with the table verbatim (nine modules, rationale strings mandatory on
    every entry); a synthetic fixture module placed under the scope with a matching loader def
    and no classification makes the test fail (self-test of the sweep).
20. **pool_guard unit pins:** marked row → reported with id/line; unmarked manifest → empty;
    malformed JSONL lines skipped without crashing; pure stdlib import.

## Calibration posture

Ships **heuristic / uncalibrated**, no bands, no thresholds promoted. Stage A's `0.8` / `k=5` /
10-word floor and Stage B's `k=8` / 20-word floor are documented starting points echoed in
`assumptions`, not calibrated cuts. The natural calibration loop is the motivating private
shakedown itself: rerun its matched-arm evaluation on a hygiene-treated corpus and observe
whether contiguous-span reproduction recurs — noting, per the carried caveats, that the existing
run's aggregate movement is statistically unresolved (exact McNemar `p = 0.453`) and its base
arm reproduced spans before adaptation, so the loop can at best accumulate evidence across runs,
never mint an absolute rate from one. Recorded operator-side (the private calibration record
named in Motivation), never as a producer-side band. The standing refusal: an empty report at
the default floors is **not** "memorization-safe" — the claim-license carries it.

## Deferred — M2 register-composition sweep (cut from this spec)

Earlier drafts carried an M2: a `register_sweep.py` CLI over `register_classifier.py` reporting
a corpus's heuristic register-label distribution + `register_match` strength against a declared
target — motivated by the fact that a training reference spanning registers is a *mixture, not a
mode*, which depresses achievable voice similarity in ways no additional training fixes.

**Cut, because the shipped classifier cannot support it yet** (verified against the code — this
is not M2's design failing): `_SCORERS` maps 14 entries for the 18 `KNOWN_REGISTERS`
(`policy_advocacy`, `report_prose`, `email` have no scorer); aliases share scorer functions and
the stable sort returns the first-inserted alias on ties, so only ~8 slugs are reachable as
`primary`; and the `manifest_validator.ALLOWED_REGISTER` taxonomy (15 slugs) overlaps
`KNOWN_REGISTERS` on only **6** — with `personal`, a plurality declared value in real manifests,
absent from `KNOWN_REGISTERS` entirely, so `--target-register personal` would be rejected as
`bad_input` and every such row would land in `disagree`. A sweep CLI over that is a wrapper
around a defect: it would report taxonomy misalignment as corpus mixture.

The register sweep needs its **own spec, after** (1) the two register taxonomies are reconciled
and (2) scorer coverage / alias-tie behavior is fixed in `register_classifier.py`. Neither fix
is specified here.

## Out of scope / non-goals

- **No excision/masking of spans from documents** — Stage B's inventory feeds the training
  pipeline's own decision (loss masking, chunk-stream filtering, down-weighting).
- **No sub-passage *near*-verbatim detection** (edited spans below the floor) — Stage B is
  verbatim-exact by design; a fuzzier span pass (suffix-automaton / seed-and-extend) is a
  possible M3 needing its own spec if the exact lens proves insufficient on the shakedown rerun.
- **No memorization probe harness** — ranking probe targets by reconstructibility is
  `corpus_novelty_audit`'s existing output; the probe-set builder is a training-side project.
- **No register work** (deferred above), **no new task_surface, no new signal, no verdict**.
  Neither the report nor the export says "memorization-safe", "clean", "AI", or "human".
- **No `voice_distance`-side symmetry enforcement** — the marker makes asymmetry auditable;
  enforcement is operator/consumer responsibility.
- **No changes to document mode** — CLI, output shape, and behavior are frozen by the existing
  tests.

## Design calls resolved

1. **Detection unit?** → **Two-stage: uncoalesced-paragraph near-dup (Stage A) + exact
   shared-span scan (Stage B).** The first draft's single passage-Jaccard pass provably missed
   the motivating case (J ≈ 0.19 vs threshold 0.8 for a 41-token span in ~120-word passages) and
   its coalescing actively destroyed the short-boilerplate signal — both retracted. Stage B's
   detection guarantee is arithmetic (`L − k + 1` consecutive shared shingles ⇒ every verbatim
   span `≥ max(k, min_span_words)` reported), so the spec's purpose is met by construction, not
   by threshold tuning.
2. **Jaccard, containment, or exact?** → Jaccard (exact-confirmed) for Stage A's whole-unit
   near-dup class, where it is the right relation; **exact span matching** for the embedded-span
   class, where *any* unit-level set similarity dilutes with unit size. Containment
   (`|A∩B| / min(|A|,|B|)`) was considered for Stage A and rejected: it fires on legitimate
   subset relationships (a short passage quoted inside a longer one) that are Stage B's job to
   localize precisely.
3. **False-positive posture?** → The first draft's "never a false duplicate" is **retracted**
   (MinHash estimation noise at `num_perm=128` sits in the borderline band, and the sub-`k`
   shingle fallback manufactures exact-1.0 clusters). Replaced mechanically: exact-Jaccard
   confirmation of LSH candidates, sub-floor passages routed to exact-equality grouping, Stage B
   exact by construction. The residual "false positive" is *legitimate authorial repetition* —
   an editorial judgment the report enables and the tool never makes.
4. **`build_output` envelope for M1?** → Still no (acquisition-family output-shape consistency;
   document mode frozen) — **but the honesty carrier is now mandatory and structured**: the
   passage-mode report embeds `assumptions` + a real `ClaimLicense` block on the registered
   `voice_coherence_acquisition` surface, pinned by tests. Prose-only refusals in
   `capabilities.d/` are not a carrier.
5. **Export: schema-valid or cut?** → **Kept, schema-valid by construction** (sidecar passage
   files + `path`-resolvable rows + verbatim inheritance of all source fields including
   `ai_status`/`privacy`/`consent_status`, refusal-not-fabrication on gaps, validator-clean
   pinned by test). Cutting `--out` was considered (it would shrink the guard's subject) and
   rejected: the passage-unit corpus is the artifact training pipelines actually consume, and
   producing it *here*, marked, is what makes the mechanical guard enforceable at all.
6. **Where does the diversity-pool guard live?** → Producer stamp + shared file-level
   `pool_guard` scan called by each duplicate-dependent surface + a coverage drift test that
   embeds the complete nine-module classification map with mandatory rationales. Two rejected
   alternatives, both structural: an unconditional check in the shared loader breaks legitimate
   comparison-pool dedup; an opt-in kwarg on the shared loader cannot reach clean-room copies or
   own-loader surfaces. The drift test's predicates are pinned (anchored def patterns; import
   matching on the loader *names*, not the source module — `cross_doc_argument_consistency`
   imports them from `cross_doc_novelty_profile`; a scope glob with named exclusions) so the
   day-one state is green against the real tree (`general_imposters` and `binoculars_calibrate`
   classified as rationale-carrying exemptions) and the cheapest future green is a real
   classification, not a name on a list.
7. **Why keep Stage A at all, given Stage B?** → Different classes: Stage B is verbatim-only;
   Stage A catches *near*-verbatim whole-passage reuse (edited reprints, templated paragraphs)
   that exact matching splits into sub-floor fragments. Stage A is also the only stage that can
   feed an export (a span cannot be "kept or dropped" without excision).

## Open questions

1. **Stage-A passage threshold.** 0.8 is inherited from document mode for consistency; passages
   have smaller shingle sets, so the borderline band is wider even with exact confirmation.
   Whether 0.8 or looser is the right *passage* default should be settled on the private
   shakedown rerun. Operator knob either way.
2. **Stage-B floors.** `k = 8` / `min_span_words = 20` are evidence-scaled starting points; the
   right reporting floor against idiom/quotation noise needs a few real-corpus runs. Knobs,
   echoed in `assumptions`.
3. **Marker key spelling.** `passage_dedup` as the row key is this spec's proposal; if a general
   `hygiene:` provenance block on manifest rows is later standardized, the guard should key on
   that instead. Decide before build if a sibling spec lands first.
4. **Glossary entry.** Whether `references/signals-glossary.md`'s convention covers hygiene
   passes (it is signal-oriented) — check its preamble at build.
5. **Export inheritance edge:** rows whose source manifest carries fields *outside*
   `manifest_validator.KNOWN_FIELDS` — inherit verbatim (and accept the validator's
   unknown-field warning, mirroring the source) or drop? Leaning inherit-verbatim (the export
   must never know less than its source); confirm against the validator's warning semantics at
   build.
