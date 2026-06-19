# 24-dependency-distance

> A descriptive **syntactic-shape** profile: the distribution of dependency distances (the linear
> span of each syntactic link) — mean dependency distance (MDD) + its shape — a working-memory /
> complexity axis no current SETEC surface measures.

- **Status:** Draft
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

**Orthogonality.** `voice_profile` dependency n-grams = *which relations*; `construction_signature` =
*which constructions*; `function_word_grammar_audit` = function-word *sequences*. None measure the
**linear span** of dependencies. MDD is a new axis (syntactic-shape *geometry*, not inventory).

## Method

Parse the target into sentences (`variance_audit.split_sentences`) and dependency-parse each
(`variance_audit._NLP`). For every non-root token, `d = |token.i − token.head.i|`. Report, at the
value level:

- `mdd` — mean dependency distance over all links (the headline scalar; higher = more complex/
  longer-range syntax).
- `mdd_per_sentence_mean` / `mdd_per_sentence_sd` — the writer's sentence-level complexity *and its
  consistency* (the SD is a distinct signal — steady vs. variable syntactic load).
- `distance_histogram` — counts at d = 1, 2, 3, … (capped, with a `>=K` bucket); `adjacent_share`
  (d = 1 proportion) and `long_range_share` (d ≥ `--long-threshold`, default 7).
- `n_links`, `n_sentences`, `n_tokens_parsed`.
- `assumptions` — parser/model id, that root links are excluded, the length floor, the
  descriptive-no-baseline posture.

Descriptive, single-document (no baseline) — like `construction_signature_audit`. Deterministic given
the same spaCy model. **Graceful degradation:** if the shared parser is unavailable (`HAS_SPACY` False
/ `_NLP` None / model missing), the surface emits `available:false` with `reason_category:
missing_dependency` (the construction-signature pattern) — never a silent stdlib fallback (there is no
faithful parse-free MDD).

## Contract (the testable interface)

- **task_surface:** **reuse `voice_coherence`** (the descriptive-syntactic surface
  `construction_signature_audit` already uses; precedented, no new `claim_license_surfaces/` label).
- **CLI:** `python3 plugins/setec-voiceprint/scripts/dependency_distance_audit.py TARGET
  [--long-threshold 7] [--max-bucket 15] [--json] [--out F]`.
- **JSON envelope:** via `output_schema.build_output()` + a `ClaimLicense` block; `results` keys as
  above. Carries `target.spacy_available` (the construction-signature convention).
- **Claim license — licenses:** "the distribution of dependency distances of the target (MDD, per-
  sentence MDD + SD, the distance histogram) — a descriptive syntactic-complexity profile." **Refuses:**
  any AI/human or authorship verdict; any quality/readability judgment; cross-language comparison
  (MDD norms are language-specific). Thresholds operator-side / PROVISIONAL.
- **capabilities.d fragment:** `dependency_distance_audit.yaml` — `surface: voice_coherence`; `status:
  heuristic`; `compute.tier: core` (parser, CPU); `length_floor_words` ~150 (a stable distribution
  needs several sentences); `dependencies.python: [spacy]` + the `en_core_web_sm` model note;
  `do_not_use_when` (no parser/model; < the length floor; non-English).
- **Surface-addition paper trail (AGENTS.md):** the `capabilities.d/` fragment + a `changelog.d/`
  fragment (citing arXiv:2211.14620) + **`_golden_capabilities.json` regen (insert entry, `json.dumps`
  indent=2 **without** `sort_keys`; alphabetical) + the `== N` count bump in
  `tests/test_capabilities_dropin.py`** + `gen_calibration_readiness.py` refresh. The glossary
  distribution + ROADMAP reconcile at release. Run check_capabilities_drift / gen_calibration_readiness
  / check_docs_freshness AND `pytest tests/test_capabilities_dropin.py` before push.

## Test contract (names + invariants the build must satisfy)

`plugins/setec-voiceprint/scripts/tests/test_dependency_distance_audit.py` (skip the parser-dependent
cases on `not HAS_SPACY`):

- **deterministic-output**; **envelope-shape**; **claim-license-present** + **refuses-verdict** (no
  `verdict`/`is_ai` key; caveat in `does_not_license`).
- **graceful-degradation** — `HAS_SPACY` monkeypatched False → `available:false`
  `missing_dependency` (the core path, runnable without the model); target file missing → `bad_input`.
- **numeric pins (parser-gated):** a known sentence with a hand-checkable parse pins `mdd` and the
  `distance_histogram` (e.g. "The cat sat." → specific d's); a center-embedded sentence ("The rat the
  cat chased ran") has a **higher** `mdd` than a flat one of the same length (the complexity invariant);
  `adjacent_share + … = 1` over the histogram; root links excluded (n_links == n_tokens − n_sentences,
  modulo multi-root).
- **length-floor abstention** — a < length-floor target warns (still computes; the floor is advisory).

## Calibration posture

Ships **PROVISIONAL / heuristic** — a measurement, no verdict, operator bands. MDD norms are
language- and register-specific; a labeled register corpus would yield register baselines later →
`empirically_oriented` with a PROVENANCE entry. The default emits no decision.

## Out of scope / non-goals

- No authorship/AI verdict; no readability/quality score; no cross-language MDD comparison. Not a
  parse-free approximation (none is faithful — the surface abstains without the parser). Not a
  baseline-comparison (descriptive single-doc; a future voice_distance-style MDD-vs-baseline is separate).

## Open questions

1. **Distance metric** — linear `|i − head.i|` (standard MDD) only, or also a normalized
   (per-sentence-length) variant? Ship linear MDD; normalized is a possible later add.
2. **Surface** — reuse `voice_coherence` (as `construction_signature`) vs. a new descriptive-syntax
   surface. Default: reuse (precedented).
3. **Punctuation / multi-root** — whether to count punctuation tokens' links and how to handle
   sentences spaCy splits into multiple roots; pin the choice in a test.
