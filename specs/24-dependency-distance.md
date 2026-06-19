# 24-dependency-distance

> A descriptive **syntactic-shape** profile: the distribution of dependency distances (the linear
> span of each syntactic link) — mean dependency distance (MDD) + its shape — a working-memory /
> complexity axis no current SETEC surface measures.

- **Status:** Ready — adversarially reviewed 2026-06-19 (verdict NEEDS-REWORK → reworked). Fixes:
  the scalar MDD already ships as `variance_audit.mdd_stats` → **reframed to the distribution as the
  new axis + reuse mdd_stats**; **punctuation kept** (align with mdd_stats) so the numeric pins +
  `n_links` invariant are defined; whole-surface degradation via `build_error_output` (general_imposters
  precedent, not construction_signature); length-confound caveat + `mean_sentence_length`; exact
  center-embedding test pair; glossary at build. M1 cleared to build.
- **Tier:** near-term (additive; parser-tier — reuses the shared spaCy pipeline)
- **GPU required:** no (CPU spaCy parse; the shared `variance_audit._NLP` / `en_core_web_sm`)
- **Upstream / prior art:**
  - *The distribution of syntactic dependency distances* ([arXiv:2211.14620](https://arxiv.org/abs/2211.14620)).
  - Dependency Distance Minimization (Liu; Futrell et al.) — MDD as a cross-linguistic complexity/
    working-memory measure.
- **License decision:** **clean-room the method** (MDD = mean of `|i − head.i|` over dependency links;
  the distribution is a histogram). No weights. Reuses SETEC's already-vendored spaCy parse.

## Motivation

SETEC reads syntax two ways today: `voice_profile` counts **dependency n-grams** (which dependency
*relations* co-occur) and `construction_signature_audit` counts **named constructions** (passives,
stacked PPs). Neither measures the **geometry** of syntax — how far apart, in linear order, syntactically
linked words sit. **Dependency distance** is exactly that: `d(token) = |position(token) − position(head)|`.
Its mean (MDD) is a well-studied complexity / working-memory-load measure (longer dependencies are
harder to process; languages minimize them), and its **distribution shape** (the adjacent-link share,
the long-range tail) is a stable, interpretable stylometric fingerprint of syntactic complexity.

**Orthogonality — and what is already shipped (load-bearing).** `variance_audit.mdd_stats()` ALREADY
computes the per-sentence **MDD mean + SD** (the `mdd_sd` smoothing signal): it parses, excludes
ROOT/space, and `abs(t.i − t.head.i)`. So the *scalar* MDD is **not** new — this surface **reuses
`mdd_stats` for the mean/SD** and never re-implements it. The genuinely-new, additive axis is the
**distribution itself** (the subject of arXiv:2211.14620): the dependency-distance **histogram**, the
**adjacent-link share** (d = 1), the **long-range tail** (d ≥ K), and the distribution **shape** — a
descriptive profile no surface emits. (Also distinct from `voice_profile` dependency n-grams = *which
relations*; `construction_signature` = *which constructions*; `function_word_grammar_audit` =
function-word *sequences* — none measure the linear-span *distribution*.)

## Method

**Parse once** with `variance_audit._NLP(text)` and iterate `doc.sents` (exactly `mdd_stats`'s shape —
not `split_sentences`, which uses a different segmenter). **Link set, pinned to match `mdd_stats`:**
per sentence, tokens = `[t for t in sent if not t.is_space]` (**punctuation is kept**); a link exists
for every such token except where `t.dep_ == "ROOT"` or `t.head is t`; its distance `d = |t.i −
t.head.i|`. This identical link definition keeps the new distribution **consistent** with the shipped
MDD scalar. Report, at the value level:

- **The distribution (the new, additive contribution):**
  - `distance_histogram` — counts at d = 1, 2, …, capped with a `>= --max-bucket` (default 15) tail bucket.
  - `adjacent_share` (d = 1 proportion) and `long_range_share` (d ≥ `--long-threshold`, default 7) —
    the histogram normalized; the shape summary.
- **Scalars (REUSED, not re-implemented):** `mdd_mean` / `mdd_sd` ← `variance_audit.mdd_stats(text)`
  verbatim (the per-sentence MDD mean/SD it already ships); the surface asserts its own per-sentence
  computation matches `mdd_stats` (a regression tie).
- **Confound visibility:** `mean_sentence_length` (in tokens) co-reported, because MDD/long-range share
  rise mechanically with sentence length — surfaced so the operator sees the confound, not hidden.
- `n_links`, `n_sentences`, `n_tokens` (non-space).
- `assumptions` — parser/model id; punctuation **kept**; ROOT/self-links excluded; the
  length-vs-complexity confound; the descriptive-no-baseline posture.

Descriptive, single-document (no baseline). Deterministic given the same spaCy model.
**Graceful degradation (whole-surface):** if the shared parser is unavailable (`HAS_SPACY` False /
`_NLP` None / model missing), emit `available:false` via
`build_error_output(reason_category="missing_dependency")` (the **`general_imposters`** whole-surface
pattern — NOT construction_signature, which keeps a partial stdlib fallback). There is no faithful
parse-free dependency distance, so the surface abstains rather than approximating.

## Contract (the testable interface)

- **task_surface:** **reuse `voice_coherence`** (the descriptive-syntactic surface
  `construction_signature_audit` already uses; precedented, no new `claim_license_surfaces/` label).
- **CLI:** `python3 plugins/setec-voiceprint/scripts/dependency_distance_audit.py TARGET
  [--long-threshold 7] [--max-bucket 15] [--json] [--out F]`.
- **JSON envelope:** via `output_schema.build_output()` + a `ClaimLicense` block; `results` keys as
  above. Carries `target.spacy_available` (the construction-signature convention).
- **Claim license — licenses:** "the **distribution** of dependency distances of the target (the
  histogram, adjacent / long-range shares) plus the per-sentence MDD mean/SD reused from
  `mdd_stats` — a descriptive syntactic-complexity profile." **Refuses:** any AI/human or authorship
  verdict; any quality/readability judgment; cross-language comparison (MDD norms are
  language-specific); **a length-controlled reading** — MDD and the long-range share covary
  mechanically with sentence length and genre (hence `mean_sentence_length` is co-reported). Thresholds
  operator-side / PROVISIONAL.
- **capabilities.d fragment:** `dependency_distance_audit.yaml` — `surface: voice_coherence`; `status:
  heuristic`; `compute.tier: core` (parser, CPU); `length_floor_words` ~150 (a stable distribution
  needs several sentences); `dependencies.python: [spacy]` + the `en_core_web_sm` model note;
  `do_not_use_when` (no parser/model; < the length floor; non-English).
- **Surface-addition paper trail (AGENTS.md):** the `capabilities.d/` fragment + a `changelog.d/`
  fragment (citing arXiv:2211.14620) + **`_golden_capabilities.json` regen (insert entry, `json.dumps`
  indent=2 **without** `sort_keys`; alphabetical) + the `== N` count bump in
  `tests/test_capabilities_dropin.py`** + `gen_calibration_readiness.py` refresh + a
  **`references/signals-glossary.md` entry at BUILD** (a new signal; the calibration-status
  *distribution counts* + ROADMAP reconcile at release). Run check_capabilities_drift /
  gen_calibration_readiness / check_docs_freshness AND `pytest tests/test_capabilities_dropin.py` before push.

## Test contract (names + invariants the build must satisfy)

`plugins/setec-voiceprint/scripts/tests/test_dependency_distance_audit.py` (skip the parser-dependent
cases on `not HAS_SPACY`):

- **deterministic-output**; **envelope-shape**; **claim-license-present** + **refuses-verdict** (no
  `verdict`/`is_ai` key; caveat in `does_not_license`).
- **graceful-degradation** — `HAS_SPACY` monkeypatched False → `available:false`
  `missing_dependency` (runnable without the model); target file missing → `bad_input`.
- **mdd_stats reuse tie** — the surface's per-sentence `mdd_mean`/`mdd_sd` equal
  `variance_audit.mdd_stats(text)` exactly (pins that the scalars are reused, not re-derived).
- **numeric pins (parser-gated, `skipif(not HAS_SPACY)`):** pin the EXACT sentence pair (not "any
  flat sentence") — a center-embedded "The rat the cat chased ran." has a **higher** `mdd_mean` than
  a specific flat control of the same length; `adjacent_share + Σ(other shares) == 1` over the
  histogram; **punctuation kept**, ROOT/self excluded, so `n_links == n_nonspace_tokens − n_sentences`
  (one root per sentence) — pinned on a known short text.
- **length-confound visibility** — `mean_sentence_length` is present in `results` (the confound is
  surfaced, per the claim license).
- **length-floor** — a < length-floor target warns (still computes; the floor is advisory).

## Calibration posture

Ships **PROVISIONAL / heuristic** — a measurement, no verdict, operator bands. MDD norms are
language- and register-specific; a labeled register corpus would yield register baselines later →
`empirically_oriented` with a PROVENANCE entry. The default emits no decision.

## Out of scope / non-goals

- No authorship/AI verdict; no readability/quality score; no cross-language MDD comparison. Not a
  parse-free approximation (none is faithful — the surface abstains without the parser). Not a
  baseline-comparison (descriptive single-doc; a future voice_distance-style MDD-vs-baseline is separate).

## Open questions

1. ~~**Distance metric**~~ **Resolved:** reuse `mdd_stats`'s linear MDD for the scalar; co-report
   `mean_sentence_length` for the length confound. A normalized (per-length) variant is a possible later add.
2. ~~**Surface**~~ **Resolved: reuse `voice_coherence`** (as `construction_signature` does; precedented).
3. ~~**Punctuation / multi-root**~~ **Resolved: keep punctuation** (exclude only `is_space`), align with
   `mdd_stats`; assume one ROOT per `doc.sents` sentence; pin `n_links == n_nonspace_tokens − n_sentences`
   on a known short text (a multi-root edge case just yields a slightly different count — descriptive, not
   a failure).
