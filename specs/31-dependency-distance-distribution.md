# 31 — dependency-distance-distribution (DDD shape descriptors)

> The **distribution-shape descriptors** of the per-link dependency-distance distribution (the DDD
> curve: variance, skew, excess kurtosis, and the tail quantiles of `d = |i − head.i|` pooled over
> all links) — the *geometry of the curve itself*, distinct from the per-sentence MDD mean/SD that
> `variance_audit.mdd_stats` already ships and that `dependency_distance_audit` (spec 24) already
> re-exports alongside a histogram + adjacent/long-range shares.

- **Status:** Built — M1 (stdlib over the existing parse output), no new surface, no
  posture/fairness gate required to land (see §11). The load-bearing design call (**extend spec 24,
  do NOT add a new surface**) is resolved below with source evidence (§2, §3).
- **Tier:** near-term, additive. Parser-tier *only by inheritance* — the new descriptors are pure
  stdlib over a list of integers that `dependency_distance_audit` already builds; they add **zero**
  new model/GPU dependency (§7).
- **arXiv root:** *The distribution of syntactic dependency distances*
  ([arXiv:2211.14620](https://arxiv.org/abs/2211.14620), Niu & Liu). The paper's claim is precisely
  that the **shape** of the dependency-distance distribution — a right-skewed, heavy-tailed curve with
  a characteristic two-regime (adjacent vs. long-range) profile — is a stable, parametrically
  describable object, *not* reducible to its mean. The build cites it in the PR body + changelog
  fragment per the fleet rule (cite-arXiv-in-PR-and-changelog).
- **License decision:** clean-room the descriptors (variance / skewness / excess kurtosis / quantiles
  are textbook `statistics`-module math over the existing `distances` list). No weights, no new vendor.

---

## 1. Framing (one paragraph)

`dependency_distance_audit.py` (spec 24) already emits the dependency-distance **distribution** —
`distance_histogram`, `adjacent_share` (d=1), `long_range_share` (d≥K), plus `mdd_mean` / `mdd_sd`
reused verbatim from `variance_audit.mdd_stats`. So the distribution is *already a feature*. What it
does **not** emit is the **parametric shape** of that distribution as scalars: the variance, skewness,
and excess kurtosis of the *pooled per-link* distance distribution, and its tail quantiles
(median / p90 / p99 / max). arXiv:2211.14620 is exactly about that curve geometry — the dependency-
distance distribution is consistently right-skewed and heavy-tailed, and its shape (not just its mean)
is the stable cross-text object. This spec adds those descriptors **inside the existing
`dependency_distance_audit` surface** (a new `shape` block in `results`), as a descriptive, no-verdict
syntactic-complexity profile. It is M1 stdlib (`statistics` + arithmetic over the `distances` list the
audit already constructs); the spaCy parse is the pre-existing gated seam whose *output* M1 consumes.

---

## 2. The load-bearing design call: extend spec 24, do NOT add a new surface

**Settled: EXTEND `dependency_distance_audit` (spec 24). Reject a new surface.** The evidence is in the
source, and it overturns the brief's premise that "spec 24 emits a scalar mean."

**Evidence A — spec 24 already emits the distribution, not a scalar.**
`scripts/dependency_distance_audit.py::audit_dependency_distance` returns (verbatim from source):
`distance_histogram`, `adjacent_share`, `long_range_share`, `mdd_mean`, `mdd_sd`,
`mean_sentence_length`, `long_threshold`, `n_links`, `n_sentences`, `n_tokens`, `assumptions`. It
already builds the full pooled `distances: list[int]` over all links and already histograms it. A new
"distribution" surface would re-parse the same text, rebuild the same `distances` list, and re-emit a
near-identical envelope — a textbook **fork/duplication** anti-pattern. SETEC's own precedent forbids
this: spec 24 itself was reworked *because* the scalar MDD already lived in `mdd_stats`, and the fix
was "reuse, don't re-implement." The same logic applies one level up.

**Evidence B — the genuine residual gap is real and small.** What spec 24 is missing is the *shape of
the pooled per-link distribution*. Critically, spec 24's `mdd_sd` is **NOT** that:
`variance_audit.mdd_stats` computes a `per_sentence` list (each entry = the *mean* of one sentence's
link distances) and returns `statistics.stdev(per_sentence)`. So `mdd_sd` is the **across-sentence SD
of sentence-level means** — a between-sentence dispersion, by construction blind to the within-pool
spread, the skew, and the tail of the per-link distance distribution that arXiv:2211.14620 studies.
Two texts can share `mdd_mean`, `mdd_sd`, `adjacent_share`, and `long_range_share` to several decimals
and still have visibly different curve geometry (a fat shoulder at d≈2–4 vs. a few extreme d≥20
center-embeddings). The descriptors below separate those; nothing currently shipped does.

**Evidence C — incremental value over the histogram is concrete, not cosmetic.**
The `distance_histogram` *contains* the shape information but does not *summarize* it: a downstream
consumer (APODICTIC, voice_distance, an operator band) cannot diff two right-skewed histograms without
reducing them to scalars first, and the natural reduction is exactly variance/skew/kurtosis/quantiles.
Emitting them is what makes the curve a *feature vector* rather than a chart. `long_range_share` is one
hand-picked tail cut (d≥7); `p90`/`p99`/`max` are the calibration-free tail summary that does not
hard-code a threshold (and so dovetails with the no-verdict posture better than a single share does).

**Why not a new `voice_distance` / AV signal surface.** A *baseline-relative* DDD distance (target
curve vs. writer/register baseline curve) is a legitimate future surface — but it is **M2-shaped**
(needs a baseline corpus + calibration) and orthogonal to this change. This spec deliberately stays
descriptive single-document so it lands as pure M1. The roadmap note (§10) flags the baseline variant.

---

## 3. Unit of analysis

- **Computed over:** the **pooled set of per-link dependency distances** `D = { |t.i − t.head.i| }`
  over every non-`ROOT`, non-self, non-space-token link in the document — i.e. the **same `distances`
  list** `audit_dependency_distance` already constructs. The new descriptors are functions of that one
  list; no re-parse, no second tokenization.
- **Link set:** unchanged and pinned to `mdd_stats` — punctuation kept (`not t.is_space`),
  `ROOT`/self-links excluded. This is the same invariant spec 24 already asserts; the shape block
  inherits it for free, so the descriptors are guaranteed consistent with the shipped histogram and
  scalars.
- **Granularity:** one descriptor block per document (descriptive, single-document, no baseline),
  matching the rest of the `dependency_distance_audit` envelope.

---

## 4. The exact result data shape

A single additive key, `results["shape"]`, is inserted by `audit_dependency_distance` (no other
results key changes; existing consumers/goldens of spec 24 see a superset). Every value is a number or
a list of numbers computed by stdlib over `distances`:

```jsonc
"shape": {
  "variance":        7.412104,   // population pvariance(distances) (pooled per-link)
  "sd":              2.722517,   // population pstdev(distances)     (NOT mdd_sd; see note below)
  "skewness":        2.184430,   // Fisher-Pearson moment skew, g1, over the pooled distances
  "excess_kurtosis": 6.057219,   // g2 (excess; normal == 0), over the pooled distances
  "quantiles": {                 // nearest-rank percentiles of the pooled per-link distances
    "p50": 2.0,
    "p90": 6.0,
    "p99": 14.0,
    "max": 31.0
  },
  "n_links": 1843,               // == results["n_links"]; echoed so shape is self-describing
  "assumptions": { ... }
}
```

**Posture proof — this block carries NO verdict, label, or selection scalar.** Every leaf is a
descriptive moment or quantile of an observed distribution. There is **no** `is_ai`/`is_human`/
`verdict`/`label`/`class`/`flag` key; **no** threshold comparison baked into a value (`p90`/`p99` are
*reported*, not compared to a cut); **no** selection scalar (no `score`/`confidence`/`rank`/`top_*`);
**no** baseline → no z-score, no "drift," no distance-to-anything. This is **descriptive, not
detector-flavored**: it emits values with **no band at all** (the parent envelope's `claim_license`
refuses verdicts), exactly like the shipped `originality_audit`. There is no calibrated band because
there is no decision; the M2 baseline variant (§10) is the surface that would carry
`calibration_status` + a band per the detector contract.

**R4 bounds gate (output_schema):** all leaves are finite by construction over a non-empty integer
list; `variance`/`sd`/`quantiles` are ≥ 0; `skewness`/`excess_kurtosis` are signed and legitimately
negative-capable, and carry **no** surprisal/perplexity/entropy/probability token in their key names,
so `validate_results_bounds` leaves them unchecked (correct — they are moments, not bounded
quantities). The `p50`/`p90`/`p99` keys are **not** matched by the probability regex (which fires only
on `probability`/`p_value`/`pvalue`, not on a bare `pNN`), so they too are left unchecked, and they
are ≥ 0 anyway. The only gate interaction is the unconditional NaN/inf check, which the degenerate-case
`null` handling avoids by emitting `null`, not `nan`.

**Degenerate inputs (pinned):** `audit_dependency_distance` already raises `ValueError` (→ `bad_input`)
when `distances` is empty, so the shape block always sees ≥ 1 link. When `n_links < 3` or all distances
are equal (`sd == 0`), `skewness` and `excess_kurtosis` are **`null`** (the third/fourth standardized
moments are undefined — never emit `0.0`, which would be a fabricated value and a quiet no-verdict
violation by implying "symmetric/mesokurtic"). `variance`/`sd`/`quantiles` remain defined
(0.0 / the single value) for any non-empty list. `_distance_shape([])` raises a defensive `ValueError`
(never reached in the normal path because the caller guarantees ≥ 1 link).

---

## 5. Why these four descriptor families (and not more)

- **variance / sd** — the within-pool spread arXiv:2211.14620 treats as a primary shape parameter;
  the one descriptor most easily *mistaken* for the already-shipped `mdd_sd` (hence the explicit
  `distinct_from_mdd_sd` assumption and a regression-distinctness test in §9).
- **skewness (g1)** — the paper's headline qualitative fact is right-skew; a scalar that *is* the skew
  makes "how right-skewed" comparable across texts instead of eyeballed off a histogram.
- **excess kurtosis (g2)** — tail-heaviness / peakedness; separates a curve with a fat adjacent peak
  from one with a long thin tail at matched mean+variance.
- **tail quantiles (p50/p90/p99/max)** — a **calibration-free** tail summary that does not hard-code a
  threshold the way `long_range_share` (d≥7) does; the natural no-verdict tail description, and the one
  most robust to the length confound (a quantile of the pooled distances does not mechanically inflate
  with sentence count the way a *share* can).

Deliberately **excluded** from M1: parametric fits (the paper fits a right-truncated / two-parameter
family). A fit introduces an estimator, convergence handling, and a goodness-of-fit number that edges
toward a "how-well-does-it-fit" score — out of scope for a descriptive M1; a roadmap item (§10).

---

## 6. M1 scope (model-free, stdlib, CI-runnable)

- **Compute:** a private helper `_distance_shape(distances: list[int]) -> dict` in
  `dependency_distance_audit.py`, using hand-rolled population moments (mean, then central moments
  m2/m3/m4) for variance/sd and Fisher-Pearson g1/g2, + a `_nearest_rank_quantile` helper over
  `sorted(distances)`. **No numpy, no scipy** — `math` (`ceil`) + arithmetic only (matches the
  spec-24 / originality_audit stdlib posture; CI runs it with no extras).
- **Wiring:** `audit_dependency_distance` sets `results["shape"] = _distance_shape(distances)` after it
  builds `distances`. The histogram, shares, and reused `mdd_mean`/`mdd_sd` are untouched.
- **Gating inheritance:** the spaCy parse is the **pre-existing** gated seam. M1 consumes its *output*
  (the integer list). The whole-surface `HAS_SPACY` guard in `_run` already abstains
  (`available:false` / `missing_dependency`) when the parser is absent; the shape block never runs in
  that path. The shape *math* is unit-testable on a hand-built integer list with **no parser at all**.
- **No claim-license change required:** the existing `_claim_license()` already licenses "the
  distribution of dependency distances … a descriptive syntactic-complexity profile." The build adds
  one clause to `licenses` naming the shape moments explicitly and one to `does_not_license`
  ("shape moments are descriptive; skew/kurtosis are not a complexity *score* or an AI signal"), but
  the surface label `voice_coherence` is unchanged → **no new `claim_license_surfaces/<surface>.txt`**,
  no `VALID_TASK_SURFACES` change.

---

## 7. M2 seam — what is explicitly NOT in M1

There is **no M2 model seam for this capability.** The descriptors are pure arithmetic; there is no
larger/model-gated version of "the variance of this list." The only thing on the far side of a gate is
the **already-existing** spaCy parse, which M1 does not touch (it consumes the output). The genuinely
M2-shaped *follow-ons* (each its own future spec, NOT this build):

1. **Baseline-relative DDD distance** (`voice_distance`-style): target curve vs. a writer/register
   baseline curve → needs a baseline corpus + a calibrated band + `calibration_status`. *That* surface
   is detector-flavored and would carry the VALUES + band + `calibration_status` contract; M1 here does
   not, because M1 is descriptive single-document.
2. **Parametric fit** of the DDD curve (the paper's right-truncated family) — introduces an estimator;
   roadmap, not M1.

CI-skip: there is **nothing** in this capability that needs `@pytest.mark.skipif(not HAS_SPACY)` for
the *math* tests (they run on a synthetic integer list); only the end-to-end envelope tests inherit
spec-24's existing `skipif(not HAS_SPACY)`.

---

## 8. Acceptance criteria

1. **Additive-only envelope.** `results["shape"]` is present on a successful run with the keys in §4;
   **every pre-existing spec-24 results key is byte-for-byte unchanged** (this is the load-bearing
   additive guarantee, asserted by `set(results) == pre_existing | {"shape"}`). NOTE (finding P3):
   `schema_version` is the shared envelope-FORMAT constant `output_schema.SCHEMA_VERSION` (`"1.0"`),
   not a per-surface result contract; it is structurally untouched by adding a results key, so no
   action is needed to "keep" it — the byte-for-byte-unchanged-keys assertion carries the weight.
2. **No-verdict recursive-walk (posture guard).** A test walks the full `results` payload (incl.
   `shape`) and asserts **no** key in an **exact forbidden-key set** exists anywhere. FOLDED (finding
   P2): the denylist matches **exact key names**, NOT substrings — a substring regex containing
   `threshold`/`score` would false-positive on the existing, legitimate, untouched `long_threshold`
   results key (and the proposed test would fail on the current payload before `shape` is even added).
   The test asserts `long_threshold` IS present (benign) while the exact-key forbidden set is disjoint
   from the payload keys. It also asserts `claim_license.does_not_license` refuses AI/authorship/
   quality/length-controlled inference and states the shape moments are "not a complexity score / not
   an AI signal."
3. **Never-selects (posture guard).** The shape block contains no ranking/selection scalar: no
   `top_*`, no `rank`, no `best`, no ordering; quantiles are reported values, not a chosen cut.
   Asserted structurally (the same exact-key walk covers `rank`/`score`/`best`/`top`/`selected`).
4. **shape.sd ≠ mdd_sd (distinctness regression — the load-bearing pin).** On a crafted text whose
   per-sentence MDD means are similar but whose per-link distances are dispersed (a center-embedding
   packs a long link against many d=1s), assert `abs(results["shape"]["sd"] - results["mdd_sd"]) > eps`.
   This pins that the new `sd` is the pooled per-link SD, **not** a duplicate of the across-sentence
   `mdd_sd`. (parser-gated: `skipif(not HAS_SPACY)`.)
5. **Moment math correctness (parser-free).** `_distance_shape([known list])` returns variance, sd,
   skewness (g1), excess kurtosis (g2), and quantiles matching hand-computed population values within
   1e-6 on a pinned right-skewed list; and returns `skewness is None` / `excess_kurtosis is None` for
   `[5,5,5,5]` (sd==0), a 2-element list, and a 1-element list (`n_links<3`), while keeping
   `variance`/`quantiles` defined; `_distance_shape([])` raises `ValueError`. Runs with **no spaCy**.
6. **Consistency with the histogram (parser-gated).** `shape["n_links"] == results["n_links"] ==
   sum(histogram.values())`; quantiles are monotone (`max ≥ p99 ≥ p90 ≥ p50`); the run is deterministic.
7. **Right-skew sanity on real syntax (parser-gated).** On a normal English paragraph,
   `shape["skewness"] > 0` and `shape["excess_kurtosis"] > 0` — a directional sanity pin, not a
   calibrated threshold.
8. **Degenerate / R4 safety.** No `shape` leaf is ever `NaN`/`inf` (degenerate cases emit `null` or a
   defined number); `build_output`'s `validate_results_bounds` passes on the augmented `results`
   (skew/kurtosis/`pNN` keys carry no surprisal/probability token, so they are left unchecked).
9. **Graceful degradation unchanged.** With `HAS_SPACY` monkeypatched False → `available:false` /
   `missing_dependency` (no `shape` computed); missing target file → `bad_input`. (Inherits spec-24's
   tests; re-asserted to confirm the addition didn't break the abstain path.)
10. **Anti-Goodhart held-out disjoint (posture guard).** Assert that `dependency_distance_audit` is
    **not imported/referenced by** any `voice_distance` / `crosslingual_voice_distance` /
    `surface_disagreement_resolver` (/ `discrimination_evidence`, if present) scoring path (grep-style
    import test over the real script files), keeping this descriptive surface disjoint from the
    held-out detector seam (the voicewright anti-Goodhart boundary).
11. **Stdlib-import guard.** `dependency_distance_audit.py` imports **no** numpy/scipy/torch for the
    shape math; the module is importable and `_distance_shape` runs without those installed. The only
    heavy import remains the *optional* spaCy already gated by `HAS_SPACY`.
12. **Drop-in registration is green.** `pytest tests/test_capabilities_dropin.py` passes with the
    regenerated golden (the count is derived from `_golden_capabilities/*.json` file count + `_meta.json`,
    **no `== N` literal** is touched); `check_capabilities_drift` and `check_docs_freshness` pass.
    NOTE (finding P3): the capability `id` `dependency_distance_audit` is **unchanged** and already
    covered by released spec-24 content in `CHANGELOG.md`, so docs-freshness is already green WITHOUT a
    new fragment; the changelog.d fragment is still added because the fleet **ship-behavior → drop a
    fragment** workflow rule requires it — NOT because the gate would otherwise go red.

---

## 9. Test contract (file + names)

Extended `plugins/setec-voiceprint/scripts/tests/test_dependency_distance_audit.py` (the spec-24 test
file already exists). New tests:

- `test_distance_shape_math_parser_free` — AC 5 (runs without spaCy; literal list).
- `test_distance_shape_degenerate_returns_null` — AC 5 (sd==0, n<3 → `None`; empty → `ValueError`).
- `test_no_nan_inf_in_shape` — AC 8 (no NaN/inf on any input, incl. degenerate).
- `test_no_numpy_scipy_import` — AC 11.
- `test_dependency_distance_not_imported_by_detectors` — AC 10 (anti-Goodhart import disjointness).
- `test_shape_block_additive_and_present` — AC 1 (`skipif`).
- `test_results_carries_no_verdict_incl_shape` — AC 2/3 (recursive exact-key walk; `skipif`).
- `test_shape_sd_distinct_from_mdd_sd` — AC 4 (`skipif`).
- `test_shape_quantiles_match_histogram` — AC 6 (`skipif`).
- `test_shape_right_skew_on_english` — AC 7 (`skipif`).
- `test_shape_passes_bounds_gate` — AC 8 (`skipif`).

---

## 10. Calibration posture & roadmap

Ships **PROVISIONAL / heuristic** — a measurement, no verdict, no band (the strictest no-verdict
shape: values only, the envelope's claim-license refuses inference). DDD shape is language- and
register-specific; a labeled register corpus would later yield register baselines → a *separate*
baseline-relative surface (§7.1) carrying `calibration_status` + a band per the detector contract.
Roadmap (each its own future spec): (a) baseline-relative DDD distance; (b) parametric fit of the curve
(paper's right-truncated family) with a goodness-of-fit descriptor; (c) per-register shape norms once a
labeled corpus exists.

---

## 11. Build gating

**No posture/fairness/operator gate is required before this lands.** It is additive, descriptive,
single-document, model-free, emits no verdict and no band, adds no new surface or claim-license label,
and reuses the already-blessed spaCy seam. It is in-bounds for the anti-Goodhart boundary precisely
because it is a descriptive corpus-grounded feature with an explicit disjointness guard from the
held-out detector seam (AC 10). Standard CI gates (drop-in golden, docs-freshness, the test contract)
are the only gates. arXiv:2211.14620 cited in the PR body + changelog fragment (fleet rule).

---

## 12. Capability registration (drop-in) — as built

Because this **extends the existing `dependency_distance_audit` surface** (no new task surface, no new
script), the registration footprint is minimal:

- **`capabilities.d/dependency_distance_audit.yaml`** — UPDATED in place: `purpose`/`use_when` extended
  to mention the `shape` descriptor block (variance/skew/kurtosis/quantiles). FOLDED (finding P3): the
  stale `references` path `plugins/setec-voiceprint/specs/24-...` (the plugin-scoped dir does not
  exist) was corrected to the repo-root `specs/24-dependency-distance.md`, and `specs/31-…` added. No
  new fragment file, no surface change.
- **`scripts/tests/_golden_capabilities/dependency_distance_audit.json`** — REGENERATED to match the
  updated fragment (per-id golden; the drop-in test asserts `len(m["entries"]) == len(golden)` over the
  `*.json` file count, so **no `== N` count literal** is edited — post-#239 drop-in golden).
- **`claim_license_surfaces/`** — **NO change.** Surface stays `voice_coherence`; the `.txt` golden
  already exists and `VALID_TASK_SURFACES` are untouched (no new surface).
- **`changelog.d/feat-31-ddd-shape.md`** — NEW fragment, `### Added`, referencing the capability `id`
  `dependency_distance_audit` verbatim and citing arXiv:2211.14620. (Required by the ship-behavior
  rule; the unchanged id is already covered for the gate — see AC 12.)
- **`references/signals-glossary.md`** — glossary entry for the `shape` descriptors.
- **`ROADMAP.md`** — DDD entry annotated with the shape-descriptor follow-on.

---

## 13. Assumptions / limits

- **Inherits spec-24's link-set and parser assumptions verbatim** (punctuation kept; ROOT/self
  excluded; shared `variance_audit._NLP` / `en_core_web_sm`; English; ~150-word floor for a stable
  shape). The shape descriptors are exactly as faithful as the underlying parse.
- **Length / sentence-count confound:** moments of the *pooled* distances still rise with longer
  sentences, so `mean_sentence_length` remains the confound visibility; the no-length-controlled-reading
  caveat carries over. Quantiles are *more* robust to sentence **count** than shares, but not to
  sentence **length** — stated in `shape.assumptions`.
- **Population moments** (N, not N−1) are used so descriptors are the actual moments of the observed
  distribution, not a sample estimate of a hypothesized super-population (there is none — this is a
  descriptive summary of *this* document). Documented in `shape.assumptions`.
- **Not cross-language comparable** (DDD norms are language-specific) and **not an AI/human or quality
  signal** — refused in the claim-license. Skew/kurtosis are descriptive moments, **not** a
  "complexity score."

---

## 14. Open questions (non-blocking)

1. Quantile cut set `p50/p90/p99/max` — confirm `p99` is stable at the ~150-word floor or prefer `p95`
   for short targets (descriptive either way; no posture impact).
2. Whether to additionally emit the **median absolute deviation** of the pooled distances as a
   robust-spread companion to `sd` — leaning fast-follow, not M1, to keep the first block minimal.
