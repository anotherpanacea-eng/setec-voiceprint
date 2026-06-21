# 32-function-word-adjacency-networks (FWAN)

> A descriptive stylometric feature family: model the target's **function-word
> transition structure as a directed graph** (nodes = the 135 canonical
> function words, edges = adjacent-in-text transition frequencies), and emit
> **graph-structural descriptors** — node centralities, per-node transition
> entropy, network-level summaries, and small directed motif counts. A
> *structure* view of the same tokens the shipped `function_word_grammar_audit`
> reads as flat sequences. **No verdict.** M1 = stdlib + numpy (already in CI).

- **Status:** DRAFT — DECISION-COMPLETE, **review findings folded** (see §13). The
  load-bearing design call (overlap with `function_word_grammar_audit`) is
  resolved **with evidence** below: ship as a **sibling capability that reuses the
  same token stream**, because the graph-structural features are mechanically
  disjoint from the flat bigram/entropy features the grammar audit already emits
  (proof in §2). M1 is stdlib + numpy, CI-runnable, no model, no GPU, **no
  networkx** (see §4 — the graph descriptors are computed directly on a numpy
  adjacency matrix).
- **Tier:** near-term (additive; Tier-1 / stdlib — same tier as
  `function_word_grammar_audit` and the variance-audit Tier-1 signals).
- **GPU required:** no. **Model required:** no (M1). An M2 lazy-import seam is
  drawn cleanly in §5 but is **out of M1 scope**.
- **Upstream / prior art:**
  - *Authorship Attribution Using Word Network Features* / function-word
    adjacency networks (Segarra, Eisen, Ribeiro), **[arXiv:1406.4469](https://arxiv.org/abs/1406.4469)** —
    the root method: build a Markov-style word adjacency network over function
    words and use its structure for attribution. SETEC clean-rooms the
    *feature construction* (adjacency graph + descriptors), **not** the paper's
    attribution classifier (which would be a verdict — out of bounds).
  - Classical function-word stylometry (Mosteller & Wallace; Burrows Delta) —
    already in SETEC via `function_word_features` (frequency) and
    `function_word_grammar_audit` (sequence). FWAN is the **graph** layer.
- **License decision:** **clean-room the method.** The feature is a graph over a
  word transition table — no weights, no model, no vendored artifact. Reuses
  SETEC's existing `FUNCTION_WORDS` set and the grammar audit's run segmentation.

---

## 1. Framing + unit of analysis

SETEC reads function words three ways today, all over the same canonical
135-word `FUNCTION_WORDS` set (defined in `variance_audit.py:117`, re-exported
through `stylometry_core.py:24`):

1. **Frequency** — `stylometry_core.function_word_features()` (a per-word
   relative-frequency vector; consumed by Burrows Delta in `voice_distance.py`).
2. **Sequence** — `function_word_grammar_audit.py` (flat function-word
   **bigrams/trigrams** + their entropy, plus per-category profiles:
   prepositions, demonstratives, relative pronouns, complementizers,
   subordinators, auxiliary chains, pronoun transitions).
3. **Syntactic geometry** — `dependency_distance_audit.py` (specs 24/31) and
   `construction_signature_audit.py` (parser-tier; a different axis entirely).

What no surface reads is the **network structure** of function-word
transitions: treat each adjacency `wᵢ → wᵢ₊₁` (between two function words, with
content words acting as boundary breaks, exactly as the grammar audit already
segments runs at `function_word_grammar_audit.py:153-163`) as a directed edge,
accumulate the full transition table into a weighted directed graph, and read
**graph-structural** properties of that graph — which function words are hubs
(centrality), how predictable each node's outgoing transitions are (per-node
transition entropy), how asymmetric the flow is (reciprocity / directionality),
and which small directed motifs recur. This is the arXiv:1406.4469 construction,
used here as a **descriptive feature family**, never as an attributor.

**Unit of analysis:** a single document's function-word token stream (the
content-word-delimited runs of `FUNCTION_WORDS` members), aggregated into one
directed adjacency graph over ≤135 nodes. Single-document, descriptive, no
baseline required (an optional baseline-dir z-comparison rides the existing
`function_word_grammar_audit` baseline pattern — see §6). Deterministic given the
same input.

---

## 2. The overlap with `function_word_grammar_audit` — resolved, with evidence

This is the caveat the task demands be met head-on. **Verdict: ship FWAN as a
distinct capability that REUSES the same token stream; do NOT fold into the
grammar audit, and do NOT re-derive its features.** The justification is
mechanical, not hand-waved.

### 2a. What the grammar audit already emits (read from source)

From `function_word_grammar_audit.audit_function_word_grammar()` (lines 137-345),
the shipped `results` are: `function_bigrams` (**top-20 counts only**, via
`.most_common(20)` at line 320), `function_bigram_entropy_bits`,
`n_function_trigrams`, and six **per-category lexical** profiles
(`preposition_counts`/entropy, `demonstrative_counts`, `relative_pronoun_counts`,
`complementizer_counts`, `subordinator_counts`/entropy, `auxiliary_chain_count`,
`pronoun_transition`), plus a 6-signal compression band.

The bigram view is a **flat, top-20-TRUNCATED edge-weight list with one global
entropy scalar**. It answers "which function-word *pairs* are most frequent, and
how concentrated is the pair distribution." It does **not** construct a graph
object, and therefore cannot answer any of the questions FWAN's descriptors
answer. The full bigram `Counter` is a never-returned local
(`bigram_counts`); only its top-20 view leaves the audit.

### 2b. What FWAN adds that is mechanically absent (grep-proven)

A `grep -niE "graph|network|centrality|node|edge|motif|adjacency|transition_matrix|markov|pagerank"`
over `function_word_grammar_audit.py` returns **zero hits**. None of the
following exist in the shipped audit, and none are computable from its emitted
`function_bigrams` dict (a top-20 truncation already discards the tail the
graph descriptors integrate over):

- **Node centralities** — in/out-degree, weighted degree, and a
  **stationary-distribution / PageRank-style** centrality of the transition
  matrix (which function words are *structural hubs* of the transition network,
  not merely frequent). The grammar audit's per-word frequency is the *node
  weight*; centrality is a *graph* property of the whole transition structure
  and is not recoverable from the frequency vector alone.
- **Per-node transition entropy** — for each function word, the Shannon entropy
  of its *outgoing* transition distribution (how predictably `the` is followed
  vs. how varied `that`'s successors are). The grammar audit emits ONE global
  bigram entropy; FWAN emits a **per-node** entropy vector and its summary
  (mean / sd / min-entropy node) over the **source** nodes that actually have a
  successor distribution (out-degree > 0; pure sinks are excluded and counted
  separately — §13 P4), a strictly finer object.
- **Directionality / asymmetry** — edge reciprocity, the
  forward-vs-reverse weight asymmetry over **reciprocated** pairs, AND the share
  of **one-directional** edges (the maximally asymmetric structure the
  reciprocated-only mean cannot see — §13 P4) of the transition matrix (the
  arXiv:1406.4469 networks are explicitly *directed*; flat bigram counts collapse
  `of the` vs `the of` into two unrelated rows with no asymmetry summary).
- **Network-level descriptors** — graph density (realized / possible directed
  edges over the active node set), number of active nodes, and global transition
  entropy computed from the **full** matrix (not the top-20 truncation).
- **Small directed motifs** — counts of length-3 directed paths and 2-cycles
  (`A↔B` reciprocated pairs) and self-loops over the function-word graph; a
  recurrence-of-local-structure signal the flat n-gram list cannot express.

### 2c. Why a separate capability (not an extension of the grammar audit)

Three reasons, in order of weight:

1. **Disjoint feature semantics, shared input.** The grammar audit's posture is
   *lexical-category profiles + a compression band*; FWAN's is *graph topology*.
   Stapling a `networks` block onto the grammar audit's `results` would bloat its
   already-large 15-key payload and entangle two calibration stories (the grammar
   audit's 6-signal band vs. FWAN's centrality/entropy descriptors) under one
   tool version and one golden. They calibrate independently.
2. **The spec-24/31 precedent is exactly this shape.** `dependency_distance_audit`
   is a *new capability on the same `voice_coherence` surface* that **reuses**
   `variance_audit.mdd_stats` for the shared scalar and adds only the
   genuinely-new distribution/shape. FWAN mirrors that discipline: **reuse the
   grammar audit's run-segmentation logic** (the shared
   `function_word_grammar_audit.function_word_runs` primitive — `_tokens_lower` +
   sentence-boundary split + the `len(run) >= 2` rule) for the token stream, and
   add only the graph descriptors. Same precedent, same reuse rule.
3. **No double-counting of edge weights.** FWAN's adjacency matrix is the *same*
   bigram table the grammar audit counts. To avoid two sources of truth, FWAN
   builds the matrix from the same content-word-delimited runs and, in a test,
   **ties** its total edge weight to the bigram total computed **from the same
   run-segmentation primitive** (NOT to the truncated `function_bigrams` public
   field — see §13 P1). The grammar audit stays the owner of the *bigram counts*;
   FWAN is the owner of the *graph read* of those counts.

**Rejected alternative (folding in):** considered and declined. Folding would
have saved one capability fragment but (a) violated the spec-24 reuse-not-merge
precedent, (b) coupled two calibration ladders, and (c) forced the grammar
audit's claim-license to cover graph claims it was not written for. The
incremental value is real and disjoint; a sibling capability is the honest shape.

---

## 3. The exact result data shape — and the proof it carries NO verdict

`results` payload (passed to `output_schema.build_output()`; every numeric leaf
is finite so it clears the R4 `validate_results_bounds` walk in
`output_schema.py:194`):

```jsonc
{
  "graph": {
    "n_active_nodes": 41,          // function words that appear in >=1 transition
    "n_possible_nodes": 135,       // |FUNCTION_WORDS|
    "n_directed_edges": 312,       // distinct (wi -> wj) pairs observed
    "total_transitions": 1487,     // sum of edge weights (== run-segmentation bigram total; §13 P1)
    "density": 0.1854,             // n_directed_edges / (n_active_nodes*(n_active_nodes-1)); 0 if <2 nodes
    "reciprocity": 0.41,           // share of off-diagonal directed edges whose reverse edge also exists
    "reciprocated_weight_asymmetry_mean": 0.27, // mean |w(a->b)-w(b->a)| / (w(a->b)+w(b->a)) over RECIPROCATED pairs only (§13 P4)
    "one_directional_edge_share": 0.59  // share of off-diagonal edges with NO reverse edge (= 1 - reciprocity); the asymmetry the reciprocated-only mean cannot see (§13 P4)
  },
  "centrality": {
    // descriptive node rankings; top-K only (privacy-safe: function words, no content)
    "top_by_pagerank": [["the", 0.0631], ["of", 0.0498], ["to", 0.0444]],
    "top_by_out_degree": [["the", 23], ["a", 19]],
    "top_by_in_degree": [["the", 31], ["of", 18]],
    "pagerank_gini": 0.58,         // concentration of centrality mass (descriptive)
    "pagerank_top1_share": 0.063,
    "pagerank_damping": 0.85       // the fixed PageRank parameter used (provenance)
  },
  "transition_entropy": {
    "global_bits": 5.84,           // entropy of the full transition matrix (not top-20)
    "per_node_mean_bits": 1.97,    // mean over SOURCE nodes (out-degree > 0) of outgoing-transition entropy; sinks excluded (§13 P4)
    "per_node_sd_bits": 0.88,
    "n_source_nodes": 39,          // active nodes with a successor distribution (out-degree > 0)
    "n_sink_nodes": 2,             // active nodes that only appear as transition TARGETS (out-degree 0) — no successor distribution
    "min_entropy_node": ["the", 0.42],  // most predictable successor distribution, over SOURCE nodes only (null iff every node is a sink) (§13 P4)
    "max_entropy_node": ["and", 3.11]   // most varied successor distribution, over SOURCE nodes only
  },
  "motifs": {
    "two_cycles": 37,              // count of A<->B reciprocated pairs
    "directed_path3": 904,         // count of length-3 directed walks A->B->C (binarized)
    "self_loops": 4                // A->A (e.g. "that that"); reported, not flagged
  },
  "band": {
    "label": "typical structure",        // see §3a — descriptive band, NEVER a class
    "flagged_signals": [],               // NAMED provisional signals that fired (the only band drivers)
    "n_flagged": 0,
    "n_signals": 4,
    "band_offered": true,                // false below the structure floor (§13 P3)
    "calibration_status": {              // SAME shape as variance_audit (§3b)
      "n_calibrated": 0,
      "n_provisional": 4,
      "n_total": 4,
      "calibrated_signals": [],
      "provisional_signals": ["low_global_transition_entropy",
        "high_pagerank_concentration", "low_per_node_entropy_mean",
        "low_graph_density"]
    }
  },
  "assumptions": { ... }
}
```

**NO `band.score` (§13 P2).** The earlier draft carried a bare
`band.score` ∈ [0,1] "structure-concentration scalar." The review (P2)
correctly flagged it as the single most thresholdable artifact in the payload —
a formula-less, unnamed derived index a downstream consumer could read as
`score >= X => "concentrated => AI-like"` with one hand-edit. It is **removed.**
The band is carried by `label` + the NAMED `flagged_signals` (each tied to a
provisional `calibration_status` signal) + `n_flagged`/`n_signals`. The only
scalars in `results` are now genuine **measurements** (centralities, entropies,
counts, ratios) — there is no derived selection scalar anywhere. This mirrors
the spec-31 sibling's "no bare score" discipline and the variance-audit
precedent (which never ships a bare `score`; its `weighted_score` is paired with
`available_weight` + named `flagged_signals` + `calibration_status`).

### 3a. The no-verdict proof (the load-bearing constraint)

This is a detector-*flavored* family (graph features are exactly what
arXiv:1406.4469 fed to a classifier), so the no-verdict discipline must be
explicit and testable:

- **No `is_ai` / `is_human` / `verdict` / `label` (as a top-level result key) /
  `selected` / `selection` / `threshold` / `decision` / `class` / `prediction`
  key exists anywhere in `results`.** A test asserts the recursive walk of the
  envelope contains none of these tokens **by exact key name** (the posture
  guard, AC-3). `band.label` is the sole `label`-suffixed field and is a fixed
  *structure-concentration* phrase, asserted to be drawn from the closed
  vocabulary, never a provenance/authorship class.
- **No selection scalar.** The only scalars are **measurements** (centralities,
  entropies, counts, ratios). There is no derived band score (the P2 removal).
- **VALUES + a descriptive band + `calibration_status`.** FWAN emits the raw
  graph values, a band, and the SAME `calibration_status` block `variance_audit`
  emits, shipping **`n_calibrated: 0` / all-provisional** at M1. There is no
  `is_ai`/threshold output; the band is provisional and operator-side until a
  labeled register corpus lands.
- **Top-K, function-words-only centrality lists.** The centrality rankings emit
  only `FUNCTION_WORDS` tokens (closed-class, content-free) — no content words,
  no document text — so the payload is privacy-safe and carries no
  reconstructable content.

### 3b. Why `calibration_status` and not a bare band

Carrying the `calibration_status` block (all-provisional at M1) makes the
epistemic status machine-readable: every band-driving signal is flagged
`provisional`, so any downstream consumer (APODICTIC) sees the band is **not** a
calibrated decision. This is the anti-Goodhart move — the surface refuses to let
an uncalibrated structural concentration masquerade as a detection score.

---

## 4. M1 scope — stdlib + numpy, CI-runnable (no model, no GPU, NO networkx)

**M1 builds the entire surface with the standard library plus numpy.** numpy is
already a CI transitive dependency (pulled by `scipy>=1.11` in `requirements.txt`
and importable in the env — confirmed `numpy 2.4.4`). networkx is **not** in any
`requirements*.txt`; using it would be a new hard dependency for a Tier-1 surface
and break the "M1 = stdlib" contract, so all graph descriptors are computed
directly on a numpy adjacency matrix:

- **Token stream / runs:** reuse the shared
  `function_word_grammar_audit.function_word_runs` primitive (its `_tokens_lower`
  + the `len(run) >= 2` rule) over the token set filtered to `FUNCTION_WORDS`.
  Content words break runs AND the text is split on sentence / paragraph
  boundaries (`.`/`!`/`?` / blank line) first, so cross-sentence spurious
  adjacencies are not invented — the tokenizer discards the terminal period, so
  without the sentence split `... waited for. The ...` would fabricate a false
  `for`→`the` edge (Codex P1). Runs of length < 2 contribute zero edges. Both
  audits consume the SAME `function_word_runs` (single source of truth), which
  keeps the edge-total tie (§2c.3, §13 P1) exact.
- **Adjacency matrix:** a `numpy` `(n_active, n_active)` integer weight matrix
  indexed by the sorted active-node list. Pure numpy.
- **Centrality:** in/out-degree from row/column sums (numpy); **PageRank** via
  power iteration on the row-normalized matrix with damping 0.85 (a numpy loop
  with a fixed iteration cap + L1 convergence tol — deterministic, no scipy
  eigensolver). Dangling nodes (no out-edges) redistribute uniformly. The damping
  value is emitted for provenance.
- **Per-node + global entropy:** `-Σ p log2 p` over row-normalized rows (numpy);
  `function_word_grammar_audit._entropy` (line 110) is the stdlib reference for
  the global case.
- **Motifs:** 2-cycles = count of `(i,j)`, `i<j`, with both `M[i,j]>0` and
  `M[j,i]>0` (numpy boolean mask); length-3 directed walks = `(B @ B @ B).trace`
  is wrong for *paths*; FWAN counts directed **walks** of length 3 as
  `int((B @ B).sum())` over the binarized off-diagonal adjacency `B` (each entry
  `(B@B)[i,k]` is the number of length-2 walks `i->j->k`; summing counts every
  ordered `(i,j,k)` with both edges present — documented as a length-3-walk
  count, not a chordless-path count); self-loops = `diag(M)>0` count.

All deterministic, all CPU, all sub-second on document-sized inputs. The whole
surface runs in CI with no model download and no `skipif`.

---

## 5. The M2 lazy-import + skipif seam (OUT of M1 scope — drawn cleanly)

FWAN has no GPU/model dependency at all in its core, so unlike TOCSIN/Neurobiber
there is no logprob/model seam in the *measurement*. The only honest M2 is an
**optional spaCy-gated node refinement**, drawn here so M1 does not pretend to
own it:

- **M2 (optional, gated):** restrict the network to function-word *tokens that
  are actually function-tagged by POS* (disambiguating e.g. `that` as
  complementizer vs. relativizer vs. demonstrative — the exact ambiguity the
  grammar audit flags in its caveats). This needs spaCy (`HAS_SPACY` / `_NLP`,
  lazy-imported from `variance_audit`, the `dependency_distance_audit.py:39`
  pattern). When the parser is absent the M2 refinement is simply **not
  offered** — M1's lexical-set network is the default and is never abstained. M2
  tests carry `@pytest.mark.skipif(not HAS_SPACY)`.
- **There is no M2 model/GPU logprob seam for FWAN.** Do not invent one. (The
  surprisal/`fast_detect`/log-prob seam exists for LambdaG/TOCSIN-class
  surfaces; FWAN is purely structural and must not borrow that scaffolding.)

M2 is explicitly **deferred** and is not a precondition for M1 landing.

---

## 6. Contract (the testable interface)

- **task_surface:** **reuse `voice_coherence`** — the descriptive-stylometry
  surface `function_word_grammar_audit`, `construction_signature_audit`, and
  `dependency_distance_audit` already use. **No new `claim_license_surfaces/`
  `.txt` fragment** (the `voice_coherence.txt` label already exists; FWAN is a new
  *capability* on an existing *surface*, so `VALID_TASK_SURFACES` is untouched).
- **CLI:** `python3 plugins/setec-voiceprint/scripts/function_word_adjacency_audit.py
  TARGET [--top-k 15] [--pagerank-damping 0.85] [--json] [--out F]`.
- **JSON envelope:** via `output_schema.build_output()` + a `ClaimLicense` block
  (via `from_legacy`); `results` keys per §3.
  `build_error_output(reason_category="bad_input")` for an unreadable/empty
  target or a target with **zero function-word transitions** (too short / no
  adjacent function words) — the whole-surface abstention pattern (no model to be
  missing, so `missing_dependency` does not apply at M1).
- **Claim license — licenses:** "the graph-structural profile of the target's
  function-word transition network — node centralities, per-node and global
  transition entropy, directionality, density, and small directed motifs — a
  descriptive structural view of the same function-word transitions
  `function_word_grammar_audit` reads as flat sequences." **Refuses
  (`does_not_license`):** any AI/human or authorship verdict (arXiv:1406.4469's
  classifier is **not** reproduced — only its feature construction); any
  quality/readability judgment; a length-controlled reading (density and
  centrality concentration covary with length and function-word-set coverage;
  `n_active_nodes` / `total_transitions` co-reported); cross-language use (the
  `FUNCTION_WORDS` set is English). No verdict; bands operator-side / PROVISIONAL.
- **Additional caveats (in the claim license):** (a) without spaCy, `that` /
  `which` etc. are counted by surface form, not function (the M2 refinement is
  optional); (b) the band is calibration-pending — treat it as a cue, not a
  verdict; (c) graph descriptors are genre-bound (telegraphic vs. periodic prose
  give different densities) — read alongside register match.

---

## 7. Capability registration plan (DROP-IN — golden, no count literal)

Per the post-#170 / post-#239 drop-in convention (`VALID_TASK_SURFACES` derives
from `claim_license_surfaces/*.txt`; goldens are per-id fragments compared by
length, not by an `==N` literal):

1. **`capabilities.d/function_word_adjacency_audit.yaml`** — `id:
   function_word_adjacency_audit`; `surface: voice_coherence`; `status:
   heuristic`; `family: function-word-network`; `compute.tier: core`;
   `length_floor_words: 250` (a stable transition graph needs more text than the
   distance distribution — a concrete integer, §13 P3); `dependencies.python: []`
   (stdlib + numpy, and numpy is a transitive CI dep — treated as ambient,
   matching how the Tier-1 surfaces treat numpy); `use_when` / `do_not_use_when`
   / `registers` / `examples` / `references` (citing arXiv:1406.4469).
2. **`scripts/tests/_golden_capabilities/function_word_adjacency_audit.json`** —
   the matching per-id golden fragment (one entry; the dropin test compares
   `len(m["entries"]) == len(golden)` — `test_capabilities_dropin.py:78` — so
   **no `==N` count literal to bump**; the fragment IS the golden).
3. **`claim_license_surfaces/`** — **NO new `.txt`** (reusing `voice_coherence`).
4. **`changelog.d/feat-32-function-word-adjacency.md`** — a `changelog.d/`
   fragment citing arXiv:1406.4469 (title + id + URL) per the fleet
   cite-arXiv-in-PR-and-changelog rule; also cited in the PR body.
5. **`references/signals-glossary.md`** — a glossary entry at BUILD for each new
   band-driving signal.
6. **Pre-push gates:** `check_capabilities_drift` / `check_docs_freshness` and
   `pytest test_capabilities_dropin.py` before push.

---

## 8. Acceptance criteria (numbered)

1. **deterministic-output** — same input → equal `results` (numpy
   power-iteration uses a fixed iteration cap + tol; node order is `sorted`).
2. **envelope-shape** — output validates as a `schema_version: 1.0`
   `build_output` envelope; every numeric leaf is finite (clears
   `validate_results_bounds`).
3. **no-verdict recursive-walk (POSTURE GUARD)** — a recursive walk of the
   envelope `results` finds **no** key in `{is_ai, is_human, verdict, selected,
   selection, threshold, decision, class, prediction}` and no top-level `score`
   key, and `band.label` is drawn from the fixed structure-concentration
   vocabulary. (`label` is matched only as a top-level result key, not the
   legitimate `band.label`.)
4. **never-selects (POSTURE GUARD)** — the surface emits VALUES + a band +
   `calibration_status`; it never returns a single chosen author/class/decision.
   Asserted: `results` contains no `band.score` (the P2 removal) and
   `band.calibration_status.n_calibrated == 0` at M1 (all-provisional).
5. **claim-license-present + refuses-verdict** — the `claim_license` block is
   present; `does_not_license` explicitly refuses authorship/AI verdicts and
   names arXiv:1406.4469's classifier as NOT reproduced.
6. **edge-total tie (REUSE GUARD; §13 P1)** — `results.graph.total_transitions`
   equals `sum(len(run) - 1 for run in the content-word-delimited FUNCTION_WORDS
   runs of len >= 2)` computed from the SAME `_tokens_lower` + run rule the
   grammar audit uses (lines 153-163), and equals the sum of the FULL recomputed
   bigram `Counter` over those runs — NOT the truncated `function_bigrams` public
   field, which is explicitly a top-20 VIEW and is documented as not the tie
   target. A test pins both equalities on a multi-bigram (>20 distinct bigrams)
   text so the truncation would diverge if the tie were mis-anchored.
7. **graph-descriptor pins** — on a pinned short text: `n_active_nodes` equals
   the count of distinct `FUNCTION_WORDS` participating in ≥1 transition;
   PageRank vector sums to ~1.0 (±1e-6); a deliberately hub-dominated text
   (`the X the Y the Z ...` after segmentation) yields a higher
   `pagerank_top1_share` and `pagerank_gini` than a flat control; `two_cycles`
   counts a known `A↔B` exactly; `self_loops` counts a known `A->A` exactly.
8. **anti-Goodhart held-out disjoint (POSTURE GUARD)** — the FWAN module imports
   none of the held-out detector paths (`voice_distance`,
   `surface_disagreement_resolver`); it computes a *descriptive* graph only (no
   training against, or optimization toward, any detector). Asserted by an
   import-disjointness snapshot.
9. **stdlib-import (M1 GUARD; §13 P3)** — importing FWAN adds no `networkx*` key
   to `sys.modules` (snapshot before/after the import, assert the delta has no
   `networkx` entry — robust to a co-imported module having already pulled
   networkx), the module source contains no `import networkx`, and
   `"networkx"` appears in no `requirements*.txt`. No `skipif` is needed for the
   core surface (it runs unconditionally in CI).
10. **graceful abstention** — empty/unreadable target → `bad_input`; a target
    with zero function-word transitions → `bad_input` with a clear reason.
11. **length-confound visibility** — `n_active_nodes` and `total_transitions` are
    present in `results.graph` (the confound the claim license names is surfaced).
12. **band floor (§13 P3)** — below a concrete `total_transitions` floor the band
    is suppressed (`band.label == "insufficient structure"`,
    `band.band_offered == false`), mirroring variance_audit's "Insufficient
    signal" band; the raw graph values are still emitted. A test pins that a
    below-floor input suppresses the band and an above-floor input offers it.
13. **(M2, skipif) parser-refinement** — deferred; not built in M1.

---

## 9. Calibration posture

Ships **PROVISIONAL / heuristic** — a measurement, no verdict, operator bands,
`calibration_status.n_calibrated == 0`. The band thresholds
(`low_global_transition_entropy` etc.) are provisional and operator-side. A
labeled register corpus would later yield register baselines and earn an
`empirically_oriented` retier with PROVENANCE slugs (the spec-24 / variance-audit
calibration ladder). The default emits no decision.

---

## 10. Assumptions / limits

- **English-only:** the `FUNCTION_WORDS` set is English; cross-language use is
  refused by the claim license.
- **Length / coverage confound:** graph density and centrality concentration
  covary with text length and with how much of the 135-word set the document
  exercises. Co-reported (`n_active_nodes`, `total_transitions`), **not**
  length-controlled — surfaced so the operator sees it.
- **Surface-form function words at M1:** `that` / `which` / `as` etc. are
  counted by form, not syntactic function (the M2 spaCy refinement is optional
  and deferred).
- **Not the paper's classifier:** arXiv:1406.4469 builds these networks for
  *attribution*. SETEC reproduces only the **feature construction** and refuses
  the attribution step — that would be a verdict, out of bounds.
- **numpy, not networkx:** the graph descriptors are computed on a numpy
  adjacency matrix; networkx is deliberately avoided to preserve the Tier-1
  stdlib contract.

---

## 11. Out of scope / non-goals

- No authorship/AI verdict; no readability/quality score; no attribution
  classifier; no cross-language networks.
- No networkx / no new hard dependency.
- No model/GPU/logprob seam (FWAN is purely structural).
- Not a fold-in of `function_word_grammar_audit` (the overlap is resolved as a
  reuse-the-tokens sibling capability, §2).
- No `band.score` / no bare derived selection scalar (§13 P2).

---

## 12. Open questions

1. **Band signal set + thresholds.** The four provisional band signals and their
   cut points are a first proposal; the exact thresholds are calibration-pending.
2. **PageRank vs. stationary distribution.** M1 ships damped PageRank (robust to
   dangling nodes). Emitting the raw un-damped stationary distribution is a
   possible later add — deferred.
3. **Length normalization variant.** A per-length / per-active-node normalized
   density is a candidate M1.x add; M1 co-reports the raw confounders instead.

---

## 13. Review findings — folded

The review (`fwan-function-word-adjacency-findings.md`, GO-WITH-CHANGES) raised
two load-bearing defects and two tightenings; all are folded here. A later
second-pass review (P4) caught two descriptor-semantics defects in the
directionality and per-node-entropy summaries; those are folded too.

- **[P1] AC-6 edge-total tie was untestable against the real API.** The grammar
  audit's public `function_bigrams` is `.most_common(20)`-truncated
  (`function_word_grammar_audit.py:319-320`); the full `Counter` is a
  never-returned local. The tie is **re-anchored to the run-segmentation
  primitive**, not the truncated return: `total_transitions == sum(len(run)-1)`
  over the same `_tokens_lower` + `len(run) >= 2` content-word-delimited runs,
  and `== sum` of the FULL recomputed bigram Counter over those runs. AC-6 now
  pins both on a >20-distinct-bigram text so a mis-anchor to the truncated field
  would fail. Runs of length < 2 contribute zero edges — the identical rule.
- **[Round-8 P1 follow-up] cross-sentence false edges + the tie made structural.**
  The original segmentation broke runs only on content words; the tokenizer discards
  punctuation, so `... waited for. The ...` fabricated a false `for`→`the` edge across
  the sentence boundary. The run primitive now splits on sentence / paragraph
  boundaries FIRST, and it was MOVED into `function_word_grammar_audit` as the single
  `function_word_runs` function that BOTH audits import — so the grammar audit's own
  cross-sentence false edges are removed in the same fix and the edge-total tie is now
  structural (one function object), not two parallel implementations that could drift.
  AC-6 is re-anchored a second time: it cross-checks `total_transitions` against the
  GRAMMAR AUDIT's segmentation (`ga.function_word_runs`) and asserts
  `fwan.function_word_runs is ga.function_word_runs`.
- **[P2] band.score removed.** The bare, formula-less `[0,1]`
  "structure-concentration scalar" was the single most thresholdable artifact and
  a one-edit trust back door. It is deleted. The band is carried by `label` +
  NAMED `flagged_signals` (each tied to a provisional `calibration_status`
  signal) + `n_flagged`/`n_signals`. The only scalars in `results` are now raw
  measurements. AC-4 asserts `band.score` is absent.
- **[P3] networkx guard hardened + length floor pinned.** AC-9's guard is now a
  before/after `sys.modules` *delta* snapshot (robust to networkx being
  importable in the env, 3.6.1, and to a co-import pulling it) plus a source-grep
  + requirements-grep, not a brittle global `"networkx" not in sys.modules`. The
  `length_floor_words` is pinned to the concrete integer **250** (not "~250"),
  and the band is gated on a concrete `total_transitions` floor (below it,
  `band.label == "insufficient structure"`, `band_offered == false`) — AC-12.
- **[P4] per-node entropy summaries excluded sinks; asymmetry field zeroed on
  max asymmetry.** Two descriptor-semantics defects:
  - `min_entropy_node` / `max_entropy_node` / `per_node_mean_bits` were computed
    over ALL active nodes, including pure **SINKS** (function words that appear
    only as transition TARGETS, out-degree 0). A sink's all-zero outgoing row
    makes `_entropy_bits` return `0.0`, so the sink always won `argmin` and
    surfaced as `min_entropy_node` — `["all", -0.0]` on realistic prose — framed
    as "the most predictable successor distribution" when it has **no** successor
    distribution at all, while also diluting `per_node_mean_bits` (and thus the
    `low_per_node_entropy_mean` band signal) downward. **Fix:** the per-node
    summaries are computed over SOURCE nodes only (`out_degree > 0`); the sink
    count is reported as `n_sink_nodes` (with `n_source_nodes`), `min/max_entropy_node`
    are `null` iff every node is a sink, and `-0.0` is normalized to `0.0` on
    emit. AC: a constructed sink does NOT become `min_entropy_node` and does not
    dilute the mean.
  - `weight_asymmetry_mean` was averaged ONLY over reciprocated pairs, so a fully
    one-directional graph — the MAXIMALLY asymmetric structure — reported `0.0`
    (the minimum), the inverse of the truth, under a name that reads as a global
    asymmetry. **Fix:** the field is renamed `reciprocated_weight_asymmetry_mean`
    (its true scope) and the directionality story is completed by co-reporting
    `one_directional_edge_share` (= `1 - reciprocity`). AC: a one-directional
    graph reports `reciprocity == 0.0`, `reciprocated_weight_asymmetry_mean == 0.0`,
    `one_directional_edge_share == 1.0`.
