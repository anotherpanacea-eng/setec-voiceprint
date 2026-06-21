# 32 — lambdag — LambdaG grammar likelihood-ratio AV signal

> A **white-box, model-free** authorship-verification signal: build an n-gram language model
> over a document's **POS-tag sequences**, score a query document's grammar log-likelihood
> under a **reference-author** POS-LM versus a **background** POS-LM, and emit the calibrated
> **log-likelihood-ratio (λ_G)** plus a leaning band. A likelihood-ratio sibling to Burrows's Delta in
> `voice_distance.py` — one advisory AV signal among many, **no verdict**.

- **Status:** Ready — M1 stdlib, CI-runnable, build-gated on the posture/fairness sign-off (below).
- **Tier:** near-term (additive). M1 = stdlib (no model). M2 = an optional learned-POS backend (deferred, skipif-gated).
- **GPU required:** **no.** M1 is a pure-Python n-gram LM over POS sequences. M2's parser tier is CPU spaCy (already vendored); no GPU anywhere in this surface.
- **Upstream / prior art:**
  - **LambdaG — *Grammar as a Behavioral Biometric: Using Cognitively Motivated Grammar Models for Authorship Verification*** ([arXiv:2403.08462](https://arxiv.org/abs/2403.08462)). The method: train an n-gram grammar model on a reference author's documents and a background corpus; the verification score is the sum over the query's grammar n-grams of `log( P_author(gram) / P_background(gram) )` — a likelihood ratio that asks "is this query's *grammar* more probable under the reference author than under the population at large?"
  - Burrows's Delta (the frequency-space AV backbone already in `voice_distance.py`); LambdaG is its **sequence-probability** counterpart (a generative LR rather than a standardized-distance).
  - Capability-review placement: `setec-scratch/arxiv-capability-review/03-authorship-attribution-verification.md` (row: "Grammar as a Behavioral Biometric (LambdaG)", **NEW/IMPROVES `voice_distance`**, stdlib-friendly).
- **License decision:** **clean-room the method.** LambdaG is a model-free n-gram LM over POS tags with a count-based smoothed estimator (no weights, no vendored artifact). M1 reuses SETEC's existing POS seam (`stylometry_core.pos_trigram_features` → the per-sentence `t.pos_` stream from the shared `variance_audit._NLP`). Nothing to wrap.

---

## Review-findings fold (folded before build — see `setec-scratch/spec-wave-3/lambdag-findings.md`)

This spec is the **folded** version. The original review found one P1 and four P2/P3 anchor inaccuracies;
all are corrected here against real source:

- **[P1 — loader API] FOLDED.** There is **no generic `--reference-filter K=V` / `--background-filter
  K=V` loader.** `stylometry_core.load_entries_from_manifest` (line 465) accepts **only** the fixed
  keyword filters `use` / `split` / `register` / `persona` / `ai_status`; there is no `author` field
  anywhere in the loader or the manifest. The Delta sibling (`voice_distance.py`) selects via these same
  fixed kwargs (`--persona`, etc., line 615/705). **M1 selection uses the real loader contract:** the
  reference corpus is selected by `--reference-dir` or `--manifest` + a `--reference-persona` /
  `--reference-split` / `--reference-register` filter; the background by `--background-dir` or
  `--manifest` + `--background-persona` / `--background-split` / `--background-register`. No K=V filter
  and no loader extension is invented.
- **[P2 — band precedent] FOLDED.** `surprisal_audit._provisional_band` (line 255) returns a **FLAT**
  dict with keys `band` / `flags` / `provisional` / `calibration_anchor` / `thresholds_used` — no
  `calibration_status` key, no `band:` nesting. LambdaG's band **matches that flat-key shape** (it adds
  a `calibration_status` STRING inside the same flat dict — clearly an *adaptation*, not a claim of
  byte-identity; the `flags` key is retained, defaulting to `[]`).
- **[P2 — build_output args] FOLDED.** `output_schema.build_output` (line 220) **requires**
  `version=` / `target_path=` / `target_words=` (all non-defaulted). The envelope is built with
  `version=SCRIPT_VERSION`, `target_path=<query>`, `target_words=<query word count via
  stylometry_core.word_tokens>`, `baseline=<background metadata>`, `results=…`, `claim_license=…`.
- **[P2 — capabilities fragment keys] FOLDED.** The fragment carries the **full** sibling key set
  (`dependency_distance_audit.yaml`): `id`, `script_path`, `surface`, `status`, `handoff`, `consumers`,
  `family`, `purpose`, `use_when`, `do_not_use_when`, `inputs`, `outputs`, `compute`, `registers`,
  `dependencies`, `examples`, `references`. The per-id golden mirrors it byte-for-byte.
- **[P3 — bounds-gate rationale] FOLDED.** `_TRANSFORM_RE` (output_schema line 133) matches
  `log|ln|logit|ratio|sum|delta|diff` only as whole snake_case tokens. `lambda_g`,
  `lambda_g_per_token`, and `logl_ref_nats` (`logl` is one token, not `log`) match **none** of them.
  They pass the bounds gate **because they are not surprisal/probability-classified keys** (no `>=0` or
  `[0,1]` check applies), with NaN/inf still rejected on every leaf. The false `_TRANSFORM_RE` claim is
  dropped; Acceptance 11 tests the real path.

---

## Method (M1 — stdlib, this build)

1. **POS streams (reuse the existing seam).** For each document, parse with the shared
   `variance_audit._NLP` and take, per sentence, `tags = [t.pos_ for t in sent if not t.is_space]` —
   the *exact* stream `stylometry_core.pos_trigram_features` builds. M1 factors that per-sentence-POS
   extraction into a small reusable helper (`stylometry_core.pos_tag_sentences(text) -> list[list[str]]`)
   so `pos_trigram_features` and LambdaG share one definition (no second POS path to drift). The POS
   *parse* is the only model-gated step; the LM itself is pure arithmetic.

2. **Grammar n-gram LM (model-free).** Fix `n` (default `n=3`; `--n` 2–4). For each corpus build a
   count-based n-gram model over the POS streams with **add-k / Lidstone smoothing** (default `k=0.5`,
   `--smoothing-k`) and `<s>`/`</s>` sentence-boundary padding. This is a `Counter` over
   `(context, tag)` plus context totals — stdlib only (`collections`, `math`), deterministic. The POS
   vocabulary is the closed UPOS set (≈17 tags), so counts are dense even on short corpora.

3. **Score the query (the log-likelihood-ratio).**
   ```
   lambda_g            = logL_ref − logL_bg                # total grammar log-LR (nats)
   lambda_g_per_token  = lambda_g / n_scored_ngrams        # length-normalized (the reported scalar)
   ```
   `> 0` = grammar more probable under the reference author than the background; `< 0` the reverse.
   Both halves are reported. A per-sentence λ_G list and the top-k author-/background-favoring n-grams
   form the explanation block.

4. **Calibrated band (no verdict).** Map `lambda_g_per_token` to a 3-level **leaning** band
   (`background_leaning` / `indeterminate` / `author_leaning`) with PROVISIONAL thresholds, the flat
   `_provisional_band` shape plus a `calibration_status` string. A reading aid over a continuous score,
   **never** a same-author/different-author boolean.

**M2 (deferred, NOT this build).** A `--backend` swapping the POS alphabet (fine-grained `t.tag_`,
POS+dep tokens, or a learned tagger) and/or KN smoothing, lazy-imported behind `HAS_<backend>`, default
the M1 stdlib path. The schema, band, license, and wiring are frozen in M1; M2 adds only the model.

---

## Result data shape (carries no verdict)

`results` payload via `build_output(task_surface="voice_coherence", tool="lambdag_audit", …)`:

```jsonc
"results": {
  "lambda_g": -12.84,                  // total grammar log-LR, nats (logL_ref − logL_bg)
  "lambda_g_per_token": -0.0193,       // length-normalized scalar (headline number)
  "logL_ref_nats": -2841.07,           // both halves of the ratio, surfaced for audit
  "logL_bg_nats": -2828.23,
  "n_scored_ngrams": 664,
  "n": 3,
  "smoothing": { "method": "lidstone", "k": 0.5 },
  "pos_tagset": "spacy_upos",
  "band": {                            // flat _provisional_band shape + a calibration_status string
    "band": "indeterminate",           // background_leaning | indeterminate | author_leaning
    "flags": [],
    "provisional": true,
    "calibration_anchor": "reference-author + background pair required",
    "calibration_status": "PROVISIONAL — uncalibrated; thresholds operator-side",
    "thresholds_used": { "author_leaning_above": 0.05, "background_leaning_below": -0.05 }
  },
  "per_sentence": [ { "i": 0, "lambda_g": -0.91, "n_ngrams": 22 } ],
  "top_author_favoring_ngrams":     [ { "gram": "PRON-AUX-VERB", "log_ratio": 0.42 } ],
  "top_background_favoring_ngrams": [ { "gram": "ADP-DET-NOUN",  "log_ratio": -0.55 } ],
  "reference_summary":  { "n_docs": 7,  "n_sentences": 410, "n_pos_tokens": 9120 },
  "background_summary": { "n_docs": 84, "n_sentences": 5102, "n_pos_tokens": 121400 },
  "assumptions": {
    "method": "POS n-gram grammar LM log-likelihood-ratio (LambdaG, arXiv:2403.08462)",
    "tagset": "Universal-Dependencies POS via the shared spaCy parse; n-grams do not cross sentences",
    "orientation": "lambda_g > 0 = query grammar more probable under the reference author than the background; this is NOT a same-author determination",
    "corpus_dependence": "the LR is relative to THIS reference/background pair; a thin or register-mismatched background inflates |lambda_g|; topic and register confound grammar",
    "smoothing": "add-k (Lidstone) over the closed UPOS vocabulary; KN smoothing is an M2 option"
  }
}
```

**No-verdict proof:** no `is_ai` / `is_human` / `same_author` / `different_author` / `match` /
`verdict` / `prob_same_author` key anywhere; the headline is a continuous signed real; both halves of
the ratio are exposed; the only categorical is a 3-level *leaning* band stamped `provisional: true`.
The surface scores one query against one named reference/background pair — verification, not attribution
(no author ranking / argmax). A recursive walk (Acceptance 3) pins the absence of every forbidden key.

---

## Contract

- **task_surface:** **reuse `voice_coherence`** (the AV surface that hosts `voice_distance`,
  `voice_profile`, `dependency_distance_audit`, `construction_signature_audit`). No new
  `claim_license_surfaces/` label; `voice_coherence.txt` already exists.
- **CLI:**
  ```
  python3 plugins/setec-voiceprint/scripts/lambdag_audit.py QUERY \
      (--reference-dir DIR | --manifest M [--reference-persona P|--reference-split S|--reference-register R]) \
      (--background-dir DIR | --manifest M [--background-persona P|--background-split S|--background-register R]) \
      [--n 3] [--smoothing-k 0.5] [--top-k 10] [--json] [--out F]
  ```
  Reference and background may both draw from one `--manifest` via the real fixed filters; the loader
  asserts the two document sets are **disjoint by id** (Acceptance 7). Uses
  `stylometry_core.load_entries_from_dir` / `load_entries_from_manifest` — no new corpus path, no
  invented K=V filter.
- **JSON envelope:** `build_output(... version=SCRIPT_VERSION, target_path=<query>,
  target_words=<query word count>, baseline=<background metadata>, results=…, claim_license=…)`.
  `baseline` carries the **background** metadata via `build_baseline_metadata`; the **reference** summary
  rides in `results.reference_summary`. Error paths use `build_error_output`.
- **Claim license — refuses (`does_not_license`):** a same-author/different-author determination; an
  AI/human provenance call; ignores the corpus-relativity confound (thin/mismatched corpora inflate or
  flip the sign; topic/register/genre confound grammar); thresholds and bands operator-side /
  PROVISIONAL; no decision. Calibration is the validation harness's job.
- **Registration:** `capabilities.d/lambdag_audit.yaml` (full sibling key set) +
  `scripts/tests/_golden_capabilities/lambdag_audit.json` (byte-mirror) + `changelog.d/` fragment +
  `references/signals-glossary.md` entry. No `==N` count literal; no new surface txt.

---

## Acceptance criteria (the build greens these)

Tests in `scripts/tests/test_lambdag_audit.py` (parser cases `skipif(not HAS_SPACY)`; LM-math cases run
on fixture POS streams with **no** parser):

1. **Deterministic output.** Same query + reference + background → identical `results`.
2. **Envelope shape.** `schema_version == "1.0"`, `task_surface == "voice_coherence"`,
   `tool == "lambdag_audit"`, the `results` keys above; `claim_license` present on success.
3. **Refuses-verdict (recursive walk).** `does_not_license` contains the same-author AND AI/human
   refusals; a recursive walk of the whole envelope finds no `is_ai` / `is_human` / `same_author` /
   `different_author` / `verdict` / `match` / `prob_same_author` key at any depth.
4. **Never-selects.** One reference author; given a multi-author manifest it does not rank or emit a
   "most likely author"; no argmax/ranking field anywhere.
5. **LR math pins (no parser).** identical LMs → `lambda_g == 0`; `lambda_g == logL_ref − logL_bg`;
   `lambda_g_per_token == lambda_g / n_scored_ngrams`; reference-matching query → `> 0`, sign-flipped → `< 0`.
6. **Smoothing well-defined (no parser).** An n-gram absent from both corpora still gets a finite
   log-prob under add-k; `lambda_g` is finite (keeps the NaN/inf bounds gate green).
7. **Held-out disjoint (anti-Goodhart).** Reference/background drawn from one manifest must be disjoint
   by id; overlap → `bad_input` `build_error_output` naming the offending id(s); self-scoring refused.
8. **Background-relativity caveat surfaced.** `results.assumptions.corpus_dependence` present.
9. **Graceful degradation.** (a) module imports + LM math runs with `HAS_SPACY` False; (b) end-to-end
   with no parser → `available:false` / `missing_dependency`; (c) missing query / empty reference /
   empty background → `bad_input`.
10. **Length floor.** Query below ~150 words still computes but warns the LR is unstable.
11. **Bounds-gate compatibility.** Envelope passes `validate_results_bounds`; negative `lambda_g` /
    `logL_*_nats` allowed (not surprisal/probability keys); NaN-injected payload raises `OutputValidityError`.
12. **Registration drift-clean.** Fragment + golden byte-mirror; `test_capabilities_dropin.py`,
    `check_capabilities_drift`, `check_docs_freshness` all green; no `==N` literal.

---

## Calibration posture

Ships **PROVISIONAL / heuristic** — a measurement and a leaning band, no verdict, operator-side
thresholds. Calibration later = an FPR-targeted same-vs-different-author study over a labeled corpus
(the validation harness machinery), producing a PROVENANCE entry. **Anti-Goodhart:** calibration uses a
held-out reference/background split disjoint from any audit-independence corpus; the disjointness guard
(criterion 7) is the in-surface enforcement.

## Out of scope / non-goals

- No same-author/different-author verdict; no AI/human provenance call; no author ranking / attribution.
- No parse-free approximation (the surface abstains without the parser — criterion 9b).
- No learned model in M1 (the LM is count-based add-k; the learned/richer-tagset backend is M2).
- No cross-language claim (the UPOS LM and any thresholds are language- and register-specific).
- No standalone calibration study in this build (the validation harness's job, post-merge).

## Build gate

Build-gated on a posture/fairness sign-off: confirm the no-verdict recursive-walk and never-selects
guards (3–4), the held-out-disjoint anti-Goodhart guard (7), the stdlib-import guard (9a), and the
claim-license refusals are present and green. Not docs-only — does not skip the Codex gate.
