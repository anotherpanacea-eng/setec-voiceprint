# Spec 30 — `gram2vec`: an interpretable (glass-box) document vectorizer for voiceprint

**Status:** BUILT (M1). Findings folded (see the "Findings folded" note at the end of each affected section).
**Surface:** `voice_coherence` (existing — no new surface).
**Tool name:** `style_vectorizer` (`scripts/style_vectorizer.py`).
**arXiv root:** *gram2vec: An Interpretable Document Vectorizer*, arXiv:2406.12131 — <https://arxiv.org/abs/2406.12131>. (Per the fleet rule, the build cites this in the PR body AND the `changelog.d/` fragment, not only here.)

---

## 1. Framing (one paragraph)

`gram2vec` (the paper) is a document vectorizer in which **every dimension is a human-named stylometric feature** — POS n-grams, function words, punctuation, morphology tags, emojis, dependency labels — so the vector is *readable*: you can point at a coordinate and say "this is the rate of the function word *the*" or "this is the frequency of the POS trigram `DET-ADJ-NOUN`". That is the antithesis of an opaque sentence embedding. SETEC already computes exactly these named features in `stylometry_core.extract_features()` and already has an opaque-manifold surface (`voice_fingerprint.py`, `authorship_embedding`, LUAR/Wegmann cosines). What is *missing* is a single surface that emits the **named-feature vector itself** — the ordered, labeled, glass-box coordinate list — as a first-class, descriptive artifact, with a stable feature-name ordering so two documents are vectorized into the *same* coordinate space and a human can read the difference dimension by dimension. `style_vectorizer` is that surface: a transparent **companion** to `voice_profile.py` / `voice_fingerprint.py`, not a replacement. It is **a vectorizer, not a classifier** — it never emits a label, a verdict, a threshold crossing, or a selection scalar. It hands back named coordinates and (optionally, against a supplied baseline) a per-dimension calibrated band; the human reads it.

---

## 2. Unit of analysis

- **Primary unit:** one document (a single UTF-8 prose/markdown file → one named-feature vector).
- **Optional comparison unit:** a baseline corpus (directory or manifest) → a **per-dimension reference distribution** (mean / sd / n) over the same feature names, so a target vector can be reported *relative* to a writer/register baseline (calibrated band per dimension, never a scalar verdict).
- **Feature-name space:**
  - **single mode (no baseline):** the axis list is the **FULL family inventory** of the *target itself* — every named feature each stdlib family produces, with **no frequency cap**. In particular all 135 sorted function words appear (not the top-100 that `select_feature_names` would keep), and the FIXED families (`punctuation` / `paragraph_dialogue` / `pronoun_modal_negation`) appear whole. This preserves the glass-box promise: every named feature is readable, none silently dropped. The build therefore does NOT route single mode through `select_feature_names` (which caps non-FIXED families at `DEFAULT_LIMITS`). [Findings folded: P2 — single-mode full inventory.]
  - **baseline_relative mode:** the axis list is fixed **by the baseline** via `stylometry_core.select_feature_names()`, so the vector's axes are explicit, shared across the target and the baseline, and the per-dimension reference distribution is well-defined. Here the top-N caps (`DEFAULT_LIMITS`: function_words 100, char_ngrams_{3,4,5} 200/200/200) are intentional and acceptable — the baseline defines a shared coordinate space, not the document's full inventory. `single_mode total_dimensions` therefore generally **differs from** baseline-mode total_dimensions; this is expected and documented in `assumptions`.
- **Length floor:** soft warning under 500 words, hard-context warning under 1,000 (reuses the `comparison_warnings` thresholds verbatim); the surface still runs but flags instability. No silent abstain in M1 (stdlib families have no model dependency).

---

## 3. Exact result data shape (and the proof it carries NO verdict)

The script returns the canonical `schema_version: 1.0` envelope via `output_schema.build_output(task_surface="voice_coherence", tool="style_vectorizer", …)`. The script-specific `results` payload is (counts illustrative — computed at runtime):

```jsonc
{
  "mode": "single",                 // "single" (no baseline) or "baseline_relative"
  "feature_space": {                // the glass-box axis list — proof of interpretability
    "families": {
      "function_words":   { "n": 135, "names": ["a","an","and", "…", "the","with"] },
      "char_ngrams_3":    { "n": "<computed>", "names": ["ch3:th","ch3:he", "…"] },
      "char_ngrams_4":    { "n": "<computed>", "names": ["ch4:tion", "…"] },
      "char_ngrams_5":    { "n": "<computed>", "names": ["…"] },
      "punctuation":      { "n": 11,  "names": ["comma_per_100_words", "…"] },
      "paragraph_dialogue": { "n": 7, "names": ["dialogue_paragraph_ratio", "…"] },
      "pronoun_modal_negation": { "n": 9, "names": ["hedge_per_1000", "…"] }
      // pos_trigrams / dependency_ngrams families appear ONLY when M2's
      // spaCy tier is installed (§5). In M1 they are absent, not zero-filled.
    },
    "total_dimensions": "<computed>",   // sum of per-family n; the vector length (computed at runtime)
    "ordering": "family then feature name, both sorted — deterministic"
  },
  "vector": {                       // the document's coordinate, BY NAME, per family
    "function_words":   { "the": 0.061204, "and": 0.028918, "…": 0.0 },
    "punctuation":      { "comma_per_100_words": 7.42, "…": 0.0 },
    "…": {}
  },
  "vector_flat": [                  // same coordinate, flattened in `ordering`, for ML consumers
    { "dim": "function_words::the", "value": 0.061204 },
    { "dim": "function_words::and", "value": 0.028918 }
    // … total_dimensions entries, deterministic order
  ],
  "baseline_reference": {           // PRESENT ONLY in mode="baseline_relative"
    "per_dimension": [
      {
        "dim": "function_words::the",
        "value": 0.061204,
        "baseline_mean": 0.058,
        "baseline_sd": 0.006,
        "z": 0.534,               // signed standardized position; NOT a flag (null when sd==0)
        "band": "within",         // calibrated DESCRIPTIVE band, NOT a verdict (see below)
        "band_note": "provisional thresholds; calibration pending"
      }
      // … one row per dimension; sorted by |z| desc (None last) for readability
    ],
    "calibration_status": "provisional",   // mirrors the real ladder; never "verdict-ready"
    "k_sd": 2.0,                            // the provisional band half-width (mean ± k·sd)
    "n_baseline_files": 12,
    "n_baseline_words": 41032
  },
  "assumptions": {
    "method": "interpretable named-feature vectorization (gram2vec; arXiv:2406.12131)",
    "feature_builders": "reused verbatim from stylometry_core.extract_features (stdlib families in M1)",
    "no_verdict": "descriptive coordinate; no authorship/AI label, no threshold crossing, no selection scalar",
    "feature_space_source": "single mode = full target inventory (no cap); baseline_relative = select_feature_names over the baseline (DEFAULT_LIMITS caps apply)",
    "length_sensitivity": "char-ngram and function-word rates are length-sensitive below ~1000 words",
    "ordering": "family-then-name sorted; identical across documents compared in one run"
  }
}
```

**Why this carries no verdict / label / selection scalar — the load-bearing proof:**

1. **No boolean / categorical authorship key anywhere.** There is no `is_ai`, `is_human`, `same_author`, `verdict`, `label`, `flagged`, `selected`, or `class`. The only categorical fields are `mode` (`single` / `baseline_relative`) and the per-dimension `band` — and `band` describes *where a coordinate sits in a provisional reference distribution*, not whether the document is anything. This mirrors the precedent in `dependency_distance_audit` (a distribution, no verdict) and the `function_word_grammar_audit` posture (values + caveats, thresholds operator-side).
2. **No global scalar that ranks or selects the document.** Unlike `voice_distance` (which emits an `overall.weighted_delta` + `band`), `style_vectorizer` deliberately emits **no aggregate distance and no single number** that could be thresholded to "this one is the AI" or "pick this candidate." The vector is per-dimension and *that is the whole product*. (A consumer who wants an aggregate distance already has `voice_distance`; this surface refuses to be that, by construction — see §8 design call D4.)
3. **The band is a calibration artifact, not a decision.** `band ∈ {below, within, above}` is computed from the baseline's per-dimension mean ± k·sd (k provisional; reported as `k_sd`). It always carries `band_note` + a top-level `calibration_status: "provisional"`. The claim-license names the band as illustrative only. There is no `band == "above" ⇒ <any consequence>` anywhere in the surface.
4. **The R4 recursive-bounds walk passes cleanly.** `build_output(validate_bounds=True)` runs `validate_results_bounds()` over the whole `results` payload. Numeric leaves are bounded **only by name-pattern** (a surplus/perplexity/entropy/probability name) **plus the unconditional NaN/inf reject**. The vectorizer emits **no** surprisal/probability-named field, so its frequencies / rates / `baseline_mean` / `baseline_sd` / `z` are checked for finiteness only — which the `sd==0 ⇒ z=None` rule (no division by zero, no `nan`) guarantees. (The `z` token's match against `_STANDARDIZED_RE` is true-but-irrelevant here: nothing routes `z` into the surprisal `≥0` branch in the first place, because there is no surprisal-named ancestor key.) Empty families yield `0.0`, not `nan`. So the surface ships through the existing posture gate untouched. [Findings folded: P3 — bounds-walk mechanism reworded; the `z` token is not load-bearing for passing the gate.]
5. **`claim_license.does_not_license`** is a **single prose string** (matching `ClaimLicense.does_not_license`, a `str`, packed like `dependency_distance_audit._claim_license`). That one string enumerates the four refusals: authorship/AI verdict; same-author claim; writing-quality/readability judgment; and use as a classifier/selection *target* (the vector may be *consumed* by downstream ML, but this surface emits it as description, not as a label or training target it endorses). [Findings folded: P3 — `does_not_license` is one prose string, not a list; the acceptance test asserts substring presence.]

---

## 4. M1 scope (model-free, stdlib, CI-runnable)

**M1 emits the stdlib feature families only — the ones `stylometry_core` computes with no model:**

- `function_words` — `function_word_features(words)` (all 135 sorted function words)
- `char_ngrams_3`, `char_ngrams_4`, `char_ngrams_5` — `char_ngram_features(text)` (returns one sub-dict per n; flatten as `extract_features` already does)
- `punctuation` — `punctuation_features(text, words, sentences)`
- `paragraph_dialogue` — `paragraph_dialogue_features(text, words, paras)`
- `pronoun_modal_negation` — `pronoun_modal_negation_features(text, words)`

**Reuse, do not reimplement.** The whole M1 vector is produced by calling `stylometry_core.extract_features(text, include_spacy=False)` and reshaping its `["features"]` dict into the `vector` / `vector_flat` / `feature_space` envelope. The optional `baseline_reference` reuses `select_feature_names()`, `feature_vector()`, and `vector_stats()` from the same module. **No new feature math is written in M1** — only the vectorizer envelope, the deterministic flattening, and the per-dimension band wiring.

- **Determinism:** identical input → byte-identical `vector_flat` (sorted ordering; frequencies are pure functions of the tokenization). A `test_deterministic` pins this.
- **CI-runnable:** every M1 path runs on the stdlib (no spaCy, no network, no model download). M1 forces `include_spacy=False` so the POS/dependency families are absent regardless of whether spaCy is installed on the box. They are never zero-filled, never an error.
- **`include_spacy=False` is the M1 hard default** (a `--with-spacy` flag is reserved for M2; see §5). This guarantees the default invocation is the model-free one CI exercises, even when spaCy IS importable.

---

## 5. M2 seam (lazy-import + skipif model/GPU tier)

M2 adds the two **model-gated** families that `stylometry_core` already knows how to compute but which require `spacy` + `en_core_web_sm`:

- `pos_trigrams` — `pos_trigram_features(text)` (returns `{}` unless `HAS_SPACY and _NLP`)
- `dependency_ngrams` — `dependency_ngram_features(text)` (same gate; emits `dep2:`/`dep3:` relation-label n-grams)

The M1/M2 line is drawn **at the family level, and the source already enforces it**: both helpers early-return `{}` when `not HAS_SPACY or _NLP is None`, and `extract_features(include_spacy=True)` only inserts those families "if pos" / "if dep". So M2 is *wiring + tests*, not new model code:

- **M1 (this PR):** schema, envelope, deterministic flattening, per-dimension band, stdlib families, full CI coverage with the model absent (forced via `include_spacy=False`). The `feature_space` and `vector` are correct and complete for the stdlib axes.
- **M2 (follow-up PR):** add a `--with-spacy` flag that flips to `include_spacy=True`, surface the two extra families in the envelope when the model is present, and add `@pytest.mark.skipif(not HAS_SPACY or _NLP is None)` numeric pins for the POS/dependency dimensions (mirroring `_needs_parser` in `test_dependency_distance_audit.py`). No GPU is required for `en_core_web_sm` (CPU parse), so the M2 gate is `skipif(model absent)`, not `skipif(no GPU)`. The dependency-LABEL family key is `dependency_ngrams` exactly (confirmed against `extract_features`).

There is **no behavioral M2 detector**. M2 only *adds named dimensions*. It never adds a verdict; the posture is invariant across milestones.

---

## 6. Acceptance criteria (numbered)

**Core / shape**

1. `style_vectorizer.py --json target.txt` emits a valid `schema_version: 1.0` envelope with `task_surface == "voice_coherence"`, `tool == "style_vectorizer"`, `available: true`.
2. `results.feature_space.total_dimensions == len(results.vector_flat)`, and `vector_flat` is sorted by `dim` in the documented `ordering`. A second run on the same input yields a byte-identical `vector_flat` (**determinism**).
3. Every key in `results.vector[family]` appears in `results.feature_space.families[family].names`, and vice versa (the named axes and the coordinate agree exactly — **glass-box invariant**).
4. With `--baseline-dir` / `--manifest`, `mode == "baseline_relative"` and `baseline_reference.per_dimension` has exactly `total_dimensions` rows; each row's `dim` is a real axis. Without a baseline, `mode == "single"` and `baseline_reference` is absent.
5. M1 default invocation produces **only** the six stdlib families; `pos_trigrams` / `dependency_ngrams` are absent (not zero-filled). (Runs green in CI with spaCy present OR absent, because M1 forces `include_spacy=False`.)
6. **Single-mode full inventory.** In `mode == "single"`, `feature_space.families.function_words.n == 135` (the full sorted FUNCTION_WORDS inventory) — NOT capped to 100. A test asserts the count is the full inventory, not the `select_feature_names` cap.

**Posture guards (the load-bearing ones)**

7. **No-verdict recursive walk.** A test walks the entire `results` payload and asserts no key matches `{is_ai, is_human, same_author, verdict, label, prediction, class, flagged, selected, score_overall, decision}` at any depth, and that no string value is a verdict token (`"ai"`, `"human"`, `"same author"`, `"different author"`).
8. **Never-selects.** Given N≥2 input documents vectorized in one run (or a target + baseline), the surface emits **no ranking, no argmax, no "most/least likely", no selection index** — there is no aggregate scalar to rank on. A test confirms there is no top-level distance/score key and no field that orders documents.
9. **R4 bounds pass.** `output_schema.validate_results_bounds(results)` raises nothing on representative inputs (finite leaves; no surprisal/probability-named field). A test feeds an empty-family / `sd==0` edge case and asserts `z is None` (not `nan`) and a clean envelope.
10. **Anti-Goodhart / held-out disjoint.** When a baseline is supplied, the target document MUST NOT be a member of the baseline corpus; the surface warns (and the test asserts a warning) if the target path resolves to a file inside the baseline dir/manifest — the band must be read against a *held-out* reference, not a self-comparison.
11. **Stdlib-import guard.** A test imports `style_vectorizer` and runs the default path with `spacy` made unavailable (`monkeypatch HAS_SPACY=False`), asserting the surface still produces a complete stdlib vector and `available: true`.

**Registration / contract**

12. **Surface registered.** `style_vectorizer.TASK_SURFACE == "voice_coherence"` and `"voice_coherence" in output_schema.VALID_TASK_SURFACES` (the surface already exists; no new `claim_license_surfaces/*.txt` is added — see §7).
13. **Claim-license matches the envelope.** The `ClaimLicense.task_surface` equals the envelope `task_surface` (enforced by `build_output`), and `does_not_license` (a single prose string) names: authorship/AI verdict, same-author claim, writing-quality judgment, and "not a classifier/selection target."
14. **Drop-in golden present and consistent.** `capabilities.d/style_vectorizer.yaml` and `scripts/tests/_golden_capabilities/style_vectorizer.json` exist, agree field-for-field, and contain **no `==N` count literal**; `tools/check_capabilities_drift.py` and the docs-freshness gate pass. A `changelog.d/<slug>.md` fragment is present and cites arXiv:2406.12131.

---

## 7. Capability registration plan (DROP-IN, per #170 / #239)

Because gram2vec maps onto the **already-registered** `voice_coherence` surface (same surface as `voice_profile`, `voice_distance`, `function_word_grammar_audit`, `dependency_distance_audit`), **no new task surface is created**. Therefore:

- **NO** `claim_license_surfaces/voice_coherence.txt` change — that fragment already exists and is the golden source for the label; `VALID_TASK_SURFACES` already contains `voice_coherence`. Adding a `.txt` would be a duplicate-surface error.
- **ADD** `capabilities.d/style_vectorizer.yaml` — a single drop-in fragment with the REAL fragment shape: a top-level **`entries:`** list wrapping a single entry that carries a **`script_path:`** field, field-for-field like `capabilities.d/dependency_distance_audit.yaml`. The loader (`capabilities.load_manifest`) RAISES `fragment missing top-level entries key` and enforces one-entry-keyed-by-filename, so the wrapper + `script_path` are mandatory. Required fields (mirroring a real sibling): `entries:` → `[ {id, script_path, surface, status, handoff, consumers, family, purpose, use_when, do_not_use_when, inputs, outputs, compute{tier,cost_note,length_floor_words}, registers, dependencies{python, python_optional}, examples, references} ]`. For this entry: `id: style_vectorizer`, `script_path: plugins/setec-voiceprint/scripts/style_vectorizer.py`, `surface: voice_coherence`, `status: heuristic`, `handoff: none`, `consumers: []`, `family: stylometric-vector`, `dependencies.python: []`, `dependencies.python_optional: [spacy]` (M2 families only), `compute.tier: core`, `compute.length_floor_words: 500`, `references:` the spec path + the arXiv link. [Findings folded: P2 — `entries:` wrapper + `script_path:`; the old sketch would fail the drop-in loader.]
- **ADD** `scripts/tests/_golden_capabilities/style_vectorizer.json` — the per-id golden fragment, field-for-field identical to the single entry in the YAML (mirrors `_golden_capabilities/dependency_distance_audit.json`). **No `==N` count literal** anywhere; the per-id fragment + the round-trip test *is* the golden (per the #239 drop-in golden model). Git-add it explicitly.
- **ADD** `changelog.d/feat-30-gram2vec.md` — ships the behavior; cites arXiv:2406.12131 in the fragment (fleet rule); references the capability `id` (`style_vectorizer`) so the docs-freshness coverage gate is satisfied.
- **No shared-dict edit, no count bump.** `VALID_TASK_SURFACES` derives from the `.txt` fragments (unchanged); the capability matrix derives from the `capabilities.d/` fragments (one new file). Parallel PRs cannot collide on this surface.

---

## 8. Load-bearing design calls (resolved)

**D1 — `style_vectorizer` (gram2vec) vs `voice_profile`: why a separate surface and not an extension.**
*Resolved: separate companion script, same `voice_coherence` surface.* `voice_profile.build_profile()` emits a **corpus-level inventory** and is hard-gated **default-private** (it refuses stdout / public paths because a profile is voice-cloning input). `style_vectorizer` emits a **single-document coordinate by name** (the vector), optionally positioned against a baseline. They share `stylometry_core` builders but answer different questions. Folding the vector into `voice_profile` would inherit its corpus-only shape (wrong unit) or its blanket privacy refusal (wrong default for a single descriptive document vector). Keep them as sibling scripts on one surface — exactly the existing pattern. **Privacy note:** `style_vectorizer` is NOT a corpus profile, so it does not inherit voice_profile's hard refusal; but the claim-license still flags that a high-dimensional named vector of a personal corpus is re-identifying, and recommends `--baseline-dir` baselines stay private.

**D2 — dependency-distance-distribution: new surface vs extend spec 24.**
*Resolved: neither — `style_vectorizer` does not touch dependency *distance*.* Spec 24 (`dependency_distance_audit`, arXiv:2211.14620) owns the dependency-distance **distribution** (linear span). gram2vec's dependency contribution is **dependency-*label* n-grams** (`dep2:`/`dep3:` relation sequences from `dependency_ngram_features`), an orthogonal feature, added by `style_vectorizer` in **M2** (model-gated). Both live on `voice_coherence`; neither re-implements the other.

**D3 — FWAN overlap (`function_word_grammar_audit`): evidence it is not a duplicate.**
*Resolved: complementary layers.* FWAN is the **sequence/grammar + compression-band** layer (bigram/trigram entropy, `flagged_signals`, a compression-fraction band). gram2vec's `function_words` family is the **frequency/inventory** layer: the per-word rate of each canonical function word, emitted as transparent named coordinates with **no entropy reduction, no flagged_signals, no band over rhythm signals**. The vector is the raw material FWAN aggregates; shipping the vector does not duplicate FWAN's entropy/flag machinery.

**D4 — detector-flavored variants (TOCSIN, LambdaG): the posture spine.**
*Resolved: gram2vec ships as a vectorizer with the detector posture baked out.* For **any** detector-flavored framing reachable from this feature set, the posture is **values + a calibrated band + `calibration_status`, never a label/threshold/`is_ai`**. `style_vectorizer` enforces it structurally by emitting **no aggregate scalar at all** (§3 point 2): there is nothing to threshold. The logprob-based cousins (LambdaG/TOCSIN) need a per-token log-prob source and are a **separate** surface on the surprisal seam — explicitly out of scope here (this surface is stdlib and reads no log-probs).

**D5 — Neurobiber: drawing the M1(schema/wiring)/M2(model) line.**
*Resolved: Neurobiber is NOT this surface; the line is recorded for the sibling.* Neurobiber (a learned multi-label *Biber-feature predictor*) is a **model** that *predicts* register/Biber dimensions — a classifier requiring its own weights + GPU-class tier. The clean line: `style_vectorizer` **counts** named features with the stdlib (M1) and with a CPU spaCy parse (M2); it never loads a *predictor*. Neurobiber, if built, is a separate surface that does. This keeps the glass-box guarantee intact.

---

## 9. Assumptions and limits

- **Length sensitivity.** Char-n-gram and function-word *rates* are length-sensitive below ~1,000 words; the surface warns (reusing the `comparison_warnings` thresholds) but does not abstain. Bands on short text are noisy and the `band_note` says so.
- **Register/persona confound.** A baseline that mixes registers or personas makes per-dimension bands meaningless; the surface surfaces the existing `comparison_warnings` (register-mix / persona-mix / privacy-mix) when a baseline is supplied.
- **Provisional calibration only.** `calibration_status: "provisional"` and `band_note: "provisional thresholds; calibration pending"` ship on every baseline-relative run. The bands are illustrative; the claim-license forbids reading them as verdicts. A real per-register calibration is future work, not gated into this surface.
- **English-centric.** The function-word list and char-n-gram normalization are English-tuned (inherited from `stylometry_core`). Non-English text vectorizes but the named axes are not meaningful; the claim-license flags this.
- **Re-identification.** A high-dimensional named vector of a personal corpus is re-identifying input even though it is not a full `voice_profile`. The claim-license carries the caveat and recommends private baselines.
- **Not a training-target endorsement.** Downstream ML may *consume* `vector_flat`; the surface emits it as description. The claim-license does not endorse its use as a label/selection target, consistent with the anti-Goodhart posture.

---

## 10. Build-gating

**Not operator/fairness/posture gated to land.** This is an additive, model-free, descriptive surface on an existing surface with no new verdict, no new selection scalar, and no new fairness-sensitive decision. The posture guards (§6.7–§6.11) are CI tests, not a human gate. It follows the standard code-PR path: Codex review on the PR, drop-in golden + changelog fragment, merge commit, version cut at release. No held-out fairness study or operator sign-off is a precondition for landing M1.

---

## 11. Findings folded (audit trail)

All five review findings from `gram2vec-findings.md` were verified against real source and folded:

- **[P2] §7 registration YAML shape** — restated with the `entries:` wrapper + `script_path:` field, field-for-field against `capabilities.d/dependency_distance_audit.yaml`. (§7.)
- **[P2] single-mode function-word cap** — resolved: single mode emits the FULL family inventory (all 135 function words, no `select_feature_names` cap); caps apply only in baseline_relative mode. (§2, §6.6.)
- **[P3] §3 dimension-count arithmetic** — replaced the wrong `527` literal with `<computed>` so no specific wrong integer ships. (§3.)
- **[P3] `does_not_license` is one string, not a list** — clarified; the acceptance test asserts substring presence. (§3.5, §6.13.)
- **[P3] bounds-walk mechanism over-claim** — reworded: numeric leaves are bounded by name-pattern (surprisal/probability) plus the unconditional NaN/inf reject; the vectorizer emits no such named field, so its leaves are finiteness-checked only; the `z` token is not load-bearing. (§3.4.)

Open questions deferred to a future per-register calibration study (per the findings): the band half-width `k` (shipped provisional as `k_sd: 2.0`); the `::` flat-dim delimiter is confirmed collision-free against the family-internal `:` in char-ngram names (e.g. `function_words::the`, `char_ngrams_3::ch3:th` — the `::` join is unambiguous because the family prefix never contains `::`).
